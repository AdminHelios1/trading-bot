"""
circuit_breaker.py — Système de protection multi-niveaux à double condition.
Niveau 1 WARNING : 2 pertes OU DD > 1.5% → risk réduit à 50%
Niveau 2 PAUSE   : 3 pertes ET DD > 1.5% → pause 4h
Niveau 3 STOPPED : DD > 3% OU 4 pertes OU (3 pertes + DD > 2%) → arrêt journalier
"""

import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import Optional, List
from loguru import logger

from config import CONFIG
from daily_stats_tracker import SuiveurStatsJournalieres, ResultatTrade

FICHIER_ETAT = Path("data/circuit_breaker_state.json")


# ── Énumérations ───────────────────────────────────────────────────────────

class NiveauCB(Enum):
    AUCUN    = 0   # Aucun circuit breaker actif
    WARNING  = 1   # Avertissement — risk réduit
    PAUSE    = 2   # Pause 4h
    ARRETE   = 3   # Arrêt journalier


class RaisonCB(Enum):
    # Niveau 1
    DEUX_PERTES_CONSECUTIVES  = "2 pertes consécutives"
    DD_SUPERIEUR_1_5_PCT      = "Drawdown > 1.5%"
    # Niveau 2
    TROIS_PERTES_ET_DD_1_5    = "3 pertes consécutives + DD > 1.5%"
    # Niveau 3
    DD_SUPERIEUR_3_PCT        = "Drawdown journalier > 3%"
    QUATRE_PERTES_CONSECUTIVES = "4 pertes consécutives"
    TROIS_PERTES_ET_DD_2      = "3 pertes consécutives + DD > 2%"


# ── Dataclasses ────────────────────────────────────────────────────────────

@dataclass
class EtatCB:
    """État complet du circuit breaker — persisté entre redémarrages."""
    niveau: NiveauCB = NiveauCB.AUCUN
    raison: Optional[RaisonCB] = None
    declenche_a: Optional[datetime] = None
    pause_jusqu_a: Optional[datetime] = None   # Pour PAUSE 4h
    reset_a_minuit: bool = False                # Pour ARRETE
    risk_pct_actuel: float = CONFIG.RISQUE_PAR_TRADE_PCT
    trades_bloques: int = 0
    dernier_reset: Optional[datetime] = None
    historique: List[dict] = field(default_factory=list)


# ── Circuit Breaker principal ──────────────────────────────────────────────

class CircuitBreaker:
    """
    Système de protection à 3 niveaux avec double condition pour le niveau 2.

    Règle clé : Niveau 2 exige LES DEUX critères simultanément.
    Règle clé : STOPPED ne se reset JAMAIS sur trade gagnant — uniquement minuit UTC.
    """

    def __init__(
        self,
        suiveur_stats: Optional[SuiveurStatsJournalieres] = None,
        connecteur=None,
        notifier=None,
    ) -> None:
        """
        Args:
            suiveur_stats: Instance SuiveurStatsJournalieres.
            connecteur: Instance ConnecteurMT5.
            notifier: Instance NotificateurTelegram (optionnel).
        """
        self.suiveur_stats = suiveur_stats or SuiveurStatsJournalieres(connecteur)
        self.connecteur = connecteur
        self.notifier = notifier
        FICHIER_ETAT.parent.mkdir(exist_ok=True)
        self._etat = self._charger_etat()

    # ── Interface principale ───────────────────────────────────────────────

    def trading_autorise(self) -> tuple:
        """
        Vérifie si un nouveau trade peut être ouvert.

        Returns:
            Tuple (autorise: bool, raison: str, multiplicateur_risk: float)
            multiplicateur_risk = 1.0 (normal), 0.5 (WARNING), 0.0 (bloqué)
        """
        maintenant = datetime.utcnow()
        self._verifier_reset_automatique(maintenant)

        niveau = self._etat.niveau

        if niveau == NiveauCB.AUCUN:
            return True, "OK", 1.0

        elif niveau == NiveauCB.WARNING:
            # Trading autorisé mais risk réduit
            return (
                True,
                f"⚠️ CB WARNING — {self._etat.raison.value} | risk réduit à 0.5%",
                CONFIG.CB_WARNING_RISK_MULTIPLIER,
            )

        elif niveau == NiveauCB.PAUSE:
            if self._etat.pause_jusqu_a and maintenant < self._etat.pause_jusqu_a:
                restant = self._etat.pause_jusqu_a - maintenant
                minutes = int(restant.total_seconds() / 60)
                self._etat.trades_bloques += 1
                self._sauvegarder_etat()
                return (
                    False,
                    f"🔴 CB PAUSE — {minutes}min restantes | {self._etat.raison.value}",
                    0.0,
                )
            else:
                # Pause expirée → reset automatique
                self._reset("Pause 4h expirée — reset automatique")
                return True, "OK (pause expirée)", 1.0

        elif niveau == NiveauCB.ARRETE:
            minuit = (
                maintenant.replace(hour=0, minute=0, second=0, microsecond=0)
                + timedelta(days=1)
            )
            minutes = int((minuit - maintenant).total_seconds() / 60)
            self._etat.trades_bloques += 1
            self._sauvegarder_etat()
            return (
                False,
                f"⛔ CB ARRÊT JOURNALIER — reset à minuit UTC (dans {minutes}min) | "
                f"{self._etat.raison.value}",
                0.0,
            )

        return True, "OK", 1.0

    def evaluer_apres_trade(self, resultat: ResultatTrade) -> None:
        """
        Évalue si le circuit breaker doit changer de niveau après un trade.
        Appelé par trade_manager après chaque fermeture de position.

        Args:
            resultat: ResultatTrade avec les détails du trade clôturé.
        """
        # Enregistrer dans les stats journalières
        self.suiveur_stats.enregistrer_trade(resultat)

        pertes = self.suiveur_stats.get_pertes_consecutives()
        dd_pct = self.suiveur_stats.get_drawdown_pct()
        niveau_actuel = self._etat.niveau

        logger.debug(
            f"Évaluation CB | Pertes: {pertes} | DD: {dd_pct:.2f}% | "
            f"Niveau: {niveau_actuel.name}"
        )

        # Trade gagnant → potentiel reset (selon le niveau)
        if resultat.est_gagnant:
            self._gerer_trade_gagnant(niveau_actuel)
            return

        # ── Niveau 3 (priorité maximale) ──────────────────────────────────
        raison_n3 = self._verifier_niveau3(pertes, dd_pct)
        if raison_n3:
            if niveau_actuel != NiveauCB.ARRETE:
                self._activer(NiveauCB.ARRETE, raison_n3)
            return

        # ── Niveau 2 ──────────────────────────────────────────────────────
        raison_n2 = self._verifier_niveau2(pertes, dd_pct)
        if raison_n2:
            if niveau_actuel not in (NiveauCB.PAUSE, NiveauCB.ARRETE):
                self._activer(NiveauCB.PAUSE, raison_n2)
            return

        # ── Niveau 1 ──────────────────────────────────────────────────────
        raison_n1 = self._verifier_niveau1(pertes, dd_pct)
        if raison_n1:
            if niveau_actuel == NiveauCB.AUCUN:
                self._activer(NiveauCB.WARNING, raison_n1)

    # ── Vérification des conditions par niveau ────────────────────────────

    def _verifier_niveau1(
        self, pertes: int, dd_pct: float
    ) -> Optional[RaisonCB]:
        """
        NIVEAU 1 — WARNING (UNE des deux conditions suffit).
        A: 2 pertes consécutives
        B: DD journalier > 1.5%
        """
        if pertes >= 2:
            return RaisonCB.DEUX_PERTES_CONSECUTIVES
        if dd_pct > CONFIG.CB_WARNING_DD_PCT:
            return RaisonCB.DD_SUPERIEUR_1_5_PCT
        return None

    def _verifier_niveau2(
        self, pertes: int, dd_pct: float
    ) -> Optional[RaisonCB]:
        """
        NIVEAU 2 — PAUSE 4H (LES DEUX conditions obligatoires).
        Condition : 3 pertes consécutives ET DD > 1.5%
        JAMAIS l'un OU l'autre — c'est le changement clé vs l'ancien système.
        """
        if pertes >= 3 and dd_pct > CONFIG.CB_WARNING_DD_PCT:
            return RaisonCB.TROIS_PERTES_ET_DD_1_5
        return None

    def _verifier_niveau3(
        self, pertes: int, dd_pct: float
    ) -> Optional[RaisonCB]:
        """
        NIVEAU 3 — ARRÊT JOURNALIER (UNE des trois conditions suffit).
        A: DD > 3% (hard limit)
        B: 4 pertes consécutives
        C: 3 pertes + DD > 2%
        """
        if dd_pct > CONFIG.MAX_DRAWDOWN_JOURNALIER_PCT:
            return RaisonCB.DD_SUPERIEUR_3_PCT
        if pertes >= 4:
            return RaisonCB.QUATRE_PERTES_CONSECUTIVES
        if pertes >= 3 and dd_pct > CONFIG.CB_STOP_DD_PCT:
            return RaisonCB.TROIS_PERTES_ET_DD_2
        return None

    # ── Activation et reset ────────────────────────────────────────────────

    def _activer(self, niveau: NiveauCB, raison: RaisonCB) -> None:
        """Active un niveau de circuit breaker."""
        maintenant = datetime.utcnow()
        ancien_niveau = self._etat.niveau

        self._etat.niveau = niveau
        self._etat.raison = raison
        self._etat.declenche_a = maintenant
        self._etat.trades_bloques = 0

        if niveau == NiveauCB.WARNING:
            self._etat.risk_pct_actuel = (
                CONFIG.RISQUE_PAR_TRADE_PCT * CONFIG.CB_WARNING_RISK_MULTIPLIER
            )
            self._etat.pause_jusqu_a = None
            self._etat.reset_a_minuit = False

        elif niveau == NiveauCB.PAUSE:
            self._etat.pause_jusqu_a = maintenant + timedelta(
                hours=CONFIG.CB_PAUSE_DURATION_HEURES
            )
            self._etat.risk_pct_actuel = 0.0
            self._etat.reset_a_minuit = False

        elif niveau == NiveauCB.ARRETE:
            self._etat.pause_jusqu_a = None
            self._etat.risk_pct_actuel = 0.0
            self._etat.reset_a_minuit = True
            # Fermeture d'urgence UNIQUEMENT si DD > 3%
            if raison == RaisonCB.DD_SUPERIEUR_3_PCT:
                self._fermeture_urgence()

        # Enregistrer dans l'historique
        self._etat.historique.append({
            "niveau": niveau.name,
            "raison": raison.value,
            "declenche_a": maintenant.isoformat(),
            "depuis_niveau": ancien_niveau.name,
        })
        self._sauvegarder_etat()

        # Notification
        emoji = "⚠️" if niveau == NiveauCB.WARNING else "🔴"
        message = (
            f"{emoji} CIRCUIT BREAKER {niveau.name}\n"
            f"Raison: {raison.value}\n"
            f"Pertes: {self.suiveur_stats.get_pertes_consecutives()}\n"
            f"DD: {self.suiveur_stats.get_drawdown_pct():.2f}%\n"
            f"UTC: {maintenant.strftime('%H:%M:%S')}"
        )
        logger.warning(f"CB {niveau.name} activé | {raison.value}")
        if self.notifier:
            try:
                self.notifier.envoyer(message)
            except Exception:
                pass

    def _reset(self, raison_reset: str = "Reset") -> None:
        """Remet le circuit breaker à AUCUN."""
        if self._etat.niveau == NiveauCB.AUCUN:
            return

        ancien_niveau = self._etat.niveau

        # Compléter la dernière entrée historique
        for entree in reversed(self._etat.historique):
            if "reset_a" not in entree:
                entree["reset_a"] = datetime.utcnow().isoformat()
                entree["raison_reset"] = raison_reset
                entree["trades_bloques"] = self._etat.trades_bloques
                break

        self._etat.niveau = NiveauCB.AUCUN
        self._etat.raison = None
        self._etat.declenche_a = None
        self._etat.pause_jusqu_a = None
        self._etat.reset_a_minuit = False
        self._etat.risk_pct_actuel = CONFIG.RISQUE_PAR_TRADE_PCT
        self._etat.dernier_reset = datetime.utcnow()
        self._sauvegarder_etat()

        logger.info(f"CB reset | Ancien: {ancien_niveau.name} | {raison_reset}")
        if self.notifier:
            try:
                self.notifier.envoyer(
                    f"✅ Circuit Breaker reset\n"
                    f"Ancien niveau: {ancien_niveau.name}\n"
                    f"Raison: {raison_reset}\n"
                    f"Risk restauré: {CONFIG.RISQUE_PAR_TRADE_PCT}%"
                )
            except Exception:
                pass

    def _gerer_trade_gagnant(self, niveau_actuel: NiveauCB) -> None:
        """
        Un trade gagnant peut reset WARNING ou PAUSE, JAMAIS ARRETE.

        WARNING → reset immédiat
        PAUSE   → reset immédiat (anticipé)
        ARRETE  → aucun reset (uniquement minuit UTC)
        """
        if niveau_actuel == NiveauCB.WARNING:
            self._reset("Trade gagnant après WARNING")
        elif niveau_actuel == NiveauCB.PAUSE:
            self._reset("Trade gagnant pendant PAUSE — reset anticipé")
        # ARRETE : aucun reset sur trade gagnant — règle absolue

    def _verifier_reset_automatique(self, maintenant: datetime) -> None:
        """Vérifie les resets automatiques à chaque appel de trading_autorise()."""
        # Reset minuit UTC pour ARRETE
        if self._etat.niveau == NiveauCB.ARRETE and self._etat.reset_a_minuit:
            if self._etat.declenche_a:
                jour_declenchement = self._etat.declenche_a.strftime("%Y-%m-%d")
                jour_actuel = maintenant.strftime("%Y-%m-%d")
                if jour_actuel != jour_declenchement:
                    self._reset("Reset automatique minuit UTC")
                    return

        # Reset expiration pause pour PAUSE
        if (
            self._etat.niveau == NiveauCB.PAUSE
            and self._etat.pause_jusqu_a
            and maintenant >= self._etat.pause_jusqu_a
        ):
            self._reset("Pause 4h expirée — reset automatique")

    # ── Fermeture d'urgence ────────────────────────────────────────────────

    def _fermeture_urgence(self) -> None:
        """
        Ferme toutes les positions ouvertes en urgence.
        Appelée UNIQUEMENT si raison = DD_SUPERIEUR_3_PCT.
        """
        if self.connecteur is None:
            logger.critical("Fermeture d'urgence demandée mais connecteur absent")
            return

        try:
            positions = self.connecteur.get_positions_ouvertes(CONFIG.SYMBOLE)
            if not positions:
                return

            logger.critical(
                f"⛔ FERMETURE D'URGENCE — {len(positions)} position(s) | "
                f"DD: {self.suiveur_stats.get_drawdown_pct():.2f}%"
            )

            import MetaTrader5 as mt5
            for pos in positions:
                type_fermeture = (
                    mt5.ORDER_TYPE_SELL
                    if pos.get("type") == "LONG"
                    else mt5.ORDER_TYPE_BUY
                )
                self.connecteur.fermer_position(
                    pos["ticket"], pos["symbole"],
                    pos["volume"], type_fermeture
                )
                logger.critical(
                    f"⛔ Position fermée d'urgence | Ticket: {pos['ticket']}"
                )
        except Exception as e:
            logger.critical(f"Erreur fermeture d'urgence : {e}")

    # ── Persistance ────────────────────────────────────────────────────────

    def _sauvegarder_etat(self) -> None:
        """Sauvegarde l'état dans data/circuit_breaker_state.json."""
        try:
            def dt_str(dt):
                return dt.isoformat() if isinstance(dt, datetime) else None

            data = {
                "niveau": self._etat.niveau.name,
                "raison": self._etat.raison.name if self._etat.raison else None,
                "declenche_a": dt_str(self._etat.declenche_a),
                "pause_jusqu_a": dt_str(self._etat.pause_jusqu_a),
                "reset_a_minuit": self._etat.reset_a_minuit,
                "risk_pct_actuel": self._etat.risk_pct_actuel,
                "trades_bloques": self._etat.trades_bloques,
                "dernier_reset": dt_str(self._etat.dernier_reset),
                "historique": self._etat.historique,
            }
            FICHIER_ETAT.write_text(
                json.dumps(data, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
        except Exception as e:
            logger.error(f"Impossible de sauvegarder l'état CB : {e}")

    def _charger_etat(self) -> EtatCB:
        """Charge l'état depuis le fichier JSON au démarrage."""
        if not FICHIER_ETAT.exists():
            logger.info("Aucun état CB sauvegardé — initialisation AUCUN")
            return EtatCB()

        try:
            data = json.loads(FICHIER_ETAT.read_text(encoding="utf-8"))

            def parse_dt(s):
                if not s:
                    return None
                try:
                    return datetime.fromisoformat(s)
                except Exception:
                    return None

            niveau = NiveauCB[data.get("niveau", "AUCUN")]
            raison_str = data.get("raison")
            raison = RaisonCB[raison_str] if raison_str else None

            etat = EtatCB(
                niveau=niveau,
                raison=raison,
                declenche_a=parse_dt(data.get("declenche_a")),
                pause_jusqu_a=parse_dt(data.get("pause_jusqu_a")),
                reset_a_minuit=data.get("reset_a_minuit", False),
                risk_pct_actuel=data.get("risk_pct_actuel", CONFIG.RISQUE_PAR_TRADE_PCT),
                trades_bloques=data.get("trades_bloques", 0),
                dernier_reset=parse_dt(data.get("dernier_reset")),
                historique=data.get("historique", []),
            )

            # Vérifier si une pause a expiré pendant l'absence du bot
            maintenant = datetime.utcnow()
            if (
                etat.niveau == NiveauCB.PAUSE
                and etat.pause_jusqu_a
                and maintenant >= etat.pause_jusqu_a
            ):
                logger.info("CB PAUSE expiré depuis le dernier redémarrage — reset")
                etat.niveau = NiveauCB.AUCUN
                etat.pause_jusqu_a = None
                etat.risk_pct_actuel = CONFIG.RISQUE_PAR_TRADE_PCT

            logger.info(
                f"État CB chargé | Niveau: {etat.niveau.name} | "
                f"Raison: {etat.raison}"
            )
            return etat

        except Exception as e:
            logger.error(f"Erreur chargement état CB : {e} — réinitialisation")
            return EtatCB()

    # ── Utilitaires ────────────────────────────────────────────────────────

    def get_status(self) -> dict:
        """Retourne le statut complet pour le dashboard."""
        stats_resume = self.suiveur_stats.get_resume()
        return {
            "niveau": self._etat.niveau.name,
            "niveau_int": self._etat.niveau.value,
            "raison": self._etat.raison.value if self._etat.raison else None,
            "declenche_a": self._etat.declenche_a,
            "pause_jusqu_a": self._etat.pause_jusqu_a,
            "trades_bloques": self._etat.trades_bloques,
            "risk_pct_actuel": self._etat.risk_pct_actuel,
            **stats_resume,
        }

    def force_reset(self, raison: str = "Reset forcé manuellement") -> None:
        """Reset forcé — pour intervention manuelle."""
        self._reset(raison)
        logger.warning(f"CB reset FORCÉ — {raison}")

    # ── Compatibilité avec l'ancien GestionnaireRisque ────────────────────

    def verifier_circuit_breaker(self) -> bool:
        """Compatibilité : retourne True si trading autorisé."""
        autorise, _, _ = self.trading_autorise()
        return autorise

    def enregistrer_resultat_trade(self, profit: float) -> None:
        """
        Compatibilité avec l'ancien risk_manager.
        Crée un ResultatTrade minimal depuis un profit USD.
        """
        resultat = ResultatTrade(
            id_trade="compat",
            heure_fermeture=datetime.utcnow().isoformat(),
            direction="LONG",
            r_realise=1.0 if profit > 0 else -1.0,
            pnl_usd=profit,
            pnl_pct=profit / 10000.0 * 100,
            est_gagnant=profit > 0,
            raison_fermeture="inconnu",
            ob_score=0,
            condition_marche="inconnu",
        )
        self.evaluer_apres_trade(resultat)
