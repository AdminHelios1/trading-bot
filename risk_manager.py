"""
risk_manager.py — Gestion du risque : position sizing, drawdown, circuit breaker.
Toutes les règles de risque sont centralisées ici et doivent être respectées sans exception.
"""

import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional, List, Tuple
from loguru import logger

import MetaTrader5 as mt5

from config import CONFIG
from level_confluence import CalculateurConfluence, SourceNiveau, NiveauConfluent
from obstacle_analyzer import AnalyseurObstacles
from indicators import Indicateurs


@dataclass
class EtatRisque:
    """État du système de risque à un instant donné."""
    balance_depart_journee: float = 0.0
    balance_depart_total: float = 0.0
    drawdown_journalier_pct: float = 0.0
    drawdown_total_pct: float = 0.0
    pertes_consecutives: int = 0
    bot_arrete: bool = False
    circuit_breaker_actif: bool = False
    circuit_breaker_fin_timestamp: float = 0.0
    historique_resultats: List[float] = field(default_factory=list)  # +profit ou -perte


class GestionnaireRisque:
    """
    Calcule le position sizing, surveille les drawdowns et gère le circuit breaker.
    Doit être instancié une seule fois et partagé entre tous les modules.
    """

    def __init__(self) -> None:
        self.etat = EtatRisque()

    # ── Initialisation de la session ───────────────────────────────────────

    def initialiser_session(self, balance: float) -> None:
        """
        Initialise les valeurs de référence pour la nouvelle session de trading.
        À appeler une fois par jour au démarrage du bot.

        Args:
            balance: Balance du compte au début de la session.
        """
        self.etat.balance_depart_journee = balance
        if self.etat.balance_depart_total == 0.0:
            self.etat.balance_depart_total = balance
        logger.info(
            f"Session initialisée | Balance: {balance:.2f} | "
            f"Drawdown max journalier: {CONFIG.MAX_DRAWDOWN_JOURNALIER_PCT}% | "
            f"Drawdown max total: {CONFIG.MAX_DRAWDOWN_TOTAL_PCT}%"
        )

    # ── Calcul du lot size ─────────────────────────────────────────────────

    def calculer_lot_size(
        self,
        capital: float,
        risque_pct: float,
        sl_points: float,
        symbole: str,
        multiplicateur_risk: float = 1.0,
    ) -> float:
        """
        Calcule le lot size basé sur le capital, le risque % et la distance du SL.

        Formule : lot = montant_risqué / (sl_points × valeur_pip_par_lot)

        Args:
            capital: Solde du compte.
            risque_pct: Pourcentage du capital à risquer (ex: 1.0).
            sl_points: Distance du stop loss en points (unités MT5).
            symbole: Symbole MT5 (ex: "XAUUSD").
            multiplicateur_risk: Multiplier CB (1.0 normal, 0.5 en WARNING CB).

        Returns:
            Lot size arrondi aux contraintes du broker.
        """
        # Appliquer le multiplicateur du circuit breaker
        risque_pct_effectif = risque_pct * multiplicateur_risk
        if multiplicateur_risk < 1.0:
            logger.info(
                f"Risk réduit par CB : {risque_pct}% × {multiplicateur_risk} "
                f"= {risque_pct_effectif:.2f}%"
            )
        risque_pct = risque_pct_effectif
        if sl_points <= 0:
            logger.error("Calcul lot size impossible : sl_points <= 0")
            return 0.0

        info = mt5.symbol_info(symbole)
        if info is None:
            logger.error(f"Impossible de récupérer les infos du symbole {symbole}")
            return 0.0

        montant_risque = capital * (risque_pct / 100.0)

        # Valeur d'1 point pour 1 lot standard
        valeur_point = info.trade_tick_value  # Valeur d'1 tick (= 1 point)

        if valeur_point <= 0:
            logger.error(f"Valeur du tick invalide pour {symbole}: {valeur_point}")
            return 0.0

        lot_calcule = montant_risque / (sl_points * valeur_point)

        # Arrondir au volume_step du broker
        pas_volume = info.volume_step
        lot_arrondi = round(lot_calcule / pas_volume) * pas_volume
        lot_arrondi = round(lot_arrondi, 2)

        # Respecter les limites du broker
        lot_final = max(info.volume_min, min(lot_arrondi, info.volume_max))

        logger.debug(
            f"Position sizing | Capital: {capital:.2f} | Risque: {risque_pct}% = "
            f"{montant_risque:.2f} | SL: {sl_points:.1f}pts | "
            f"Valeur tick: {valeur_point:.4f} | Lot calculé: {lot_calcule:.3f} | "
            f"Lot final: {lot_final}"
        )
        return lot_final

    # ── Vérification drawdown ──────────────────────────────────────────────

    def mettre_a_jour_drawdown(self, equity_actuelle: float) -> None:
        """
        Met à jour les métriques de drawdown.

        Args:
            equity_actuelle: Equity actuelle du compte.
        """
        if self.etat.balance_depart_journee > 0:
            self.etat.drawdown_journalier_pct = max(
                0.0,
                (self.etat.balance_depart_journee - equity_actuelle)
                / self.etat.balance_depart_journee * 100,
            )
        if self.etat.balance_depart_total > 0:
            self.etat.drawdown_total_pct = max(
                0.0,
                (self.etat.balance_depart_total - equity_actuelle)
                / self.etat.balance_depart_total * 100,
            )

    def verifier_drawdown_journalier(self) -> bool:
        """
        Vérifie si le drawdown journalier est dans les limites.

        Returns:
            True si le drawdown est acceptable (trading autorisé).
        """
        if self.etat.drawdown_journalier_pct >= CONFIG.MAX_DRAWDOWN_JOURNALIER_PCT:
            logger.warning(
                f"⛔ DRAWDOWN JOURNALIER ATTEINT: {self.etat.drawdown_journalier_pct:.2f}% "
                f"≥ {CONFIG.MAX_DRAWDOWN_JOURNALIER_PCT}% — Pas de nouveau trade aujourd'hui"
            )
            return False
        return True

    def verifier_drawdown_total(self) -> bool:
        """
        Vérifie si le drawdown total est dans les limites.
        Si dépassé → arrêt total du bot.

        Returns:
            True si le drawdown total est acceptable.
        """
        if self.etat.drawdown_total_pct >= CONFIG.MAX_DRAWDOWN_TOTAL_PCT:
            logger.critical(
                f"🚨 DRAWDOWN TOTAL CRITIQUE: {self.etat.drawdown_total_pct:.2f}% "
                f"≥ {CONFIG.MAX_DRAWDOWN_TOTAL_PCT}% — ARRÊT D'URGENCE DU BOT"
            )
            self.etat.bot_arrete = True
            return False
        return True

    # ── Circuit breaker ────────────────────────────────────────────────────

    def enregistrer_resultat_trade(self, profit: float) -> None:
        """
        Enregistre le résultat d'un trade fermé et met à jour le circuit breaker.

        Args:
            profit: Profit ou perte du trade (positif = gain, négatif = perte).
        """
        self.etat.historique_resultats.append(profit)

        if profit < 0:
            self.etat.pertes_consecutives += 1
            logger.debug(f"Perte enregistrée | Pertes consécutives: {self.etat.pertes_consecutives}")
        else:
            self.etat.pertes_consecutives = 0

        # Activer le circuit breaker si N pertes consécutives
        if self.etat.pertes_consecutives >= CONFIG.CIRCUIT_BREAKER_PERTES_CONSECUTIVES:
            duree_pause_sec = CONFIG.CIRCUIT_BREAKER_PAUSE_HEURES * 3600
            self.etat.circuit_breaker_actif = True
            self.etat.circuit_breaker_fin_timestamp = time.time() + duree_pause_sec
            logger.warning(
                f"⚠️ CIRCUIT BREAKER ACTIVÉ: {self.etat.pertes_consecutives} pertes "
                f"consécutives — Pause de {CONFIG.CIRCUIT_BREAKER_PAUSE_HEURES}h"
            )

    def verifier_circuit_breaker(self) -> bool:
        """
        Vérifie si le circuit breaker est actif.

        Returns:
            True si le trading est autorisé (circuit breaker inactif).
        """
        if not self.etat.circuit_breaker_actif:
            return True

        temps_restant = self.etat.circuit_breaker_fin_timestamp - time.time()
        if temps_restant <= 0:
            self.etat.circuit_breaker_actif = False
            self.etat.pertes_consecutives = 0
            logger.info("Circuit breaker désactivé — reprise du trading autorisée")
            return True

        heures = int(temps_restant / 3600)
        minutes = int((temps_restant % 3600) / 60)
        logger.debug(
            f"Circuit breaker actif — reprise dans {heures}h{minutes:02d}m"
        )
        return False

    # ── Validation globale avant trade ────────────────────────────────────

    def trading_autorise(self, equity: float) -> bool:
        """
        Validation complète avant d'ouvrir un trade.
        Agrège toutes les vérifications de risque.

        Args:
            equity: Equity actuelle du compte.

        Returns:
            True si toutes les conditions de risque sont satisfaites.
        """
        self.mettre_a_jour_drawdown(equity)

        if self.etat.bot_arrete:
            logger.error("Bot arrêté définitivement (drawdown total dépassé)")
            return False

        if not self.verifier_drawdown_total():
            return False

        if not self.verifier_drawdown_journalier():
            return False

        if not self.verifier_circuit_breaker():
            return False

        return True

    # ── Calcul SL/TP ──────────────────────────────────────────────────────

    def calculer_sl_tp(
        self,
        prix_entree: float,
        prix_ob_bas: float,
        prix_ob_haut: float,
        atr_m15: float,
        direction: str,
    ) -> tuple:
        """
        Calcule le Stop Loss et les Take Profit selon la méthodologie SMC.

        SL placé sous/au-dessus de l'Order Block + marge ATR pour éviter les stop hunts.
        TP calculés selon les ratios R:R configurés.

        Args:
            prix_entree: Prix d'entrée du trade.
            prix_ob_bas: Bas de l'Order Block de référence.
            prix_ob_haut: Haut de l'Order Block de référence.
            atr_m15: ATR(14) sur M15 au moment du trade.
            direction: "LONG" ou "SHORT".

        Returns:
            Tuple (sl, tp1, tp2, tp3, sl_points).
        """
        marge_sl = atr_m15 * CONFIG.SL_MARGE_ATR

        if direction == "LONG":
            sl = prix_ob_bas - marge_sl
            if sl >= prix_entree:
                logger.warning(f"SL invalide pour LONG: SL {sl:.5f} >= entrée {prix_entree:.5f}")
                return None, None, None, None, None
            sl_distance = prix_entree - sl
        else:  # SHORT
            sl = prix_ob_haut + marge_sl
            if sl <= prix_entree:
                logger.warning(f"SL invalide pour SHORT: SL {sl:.5f} <= entrée {prix_entree:.5f}")
                return None, None, None, None, None
            sl_distance = sl - prix_entree

        # Take profits basés sur les multiples de R
        if direction == "LONG":
            tp1 = prix_entree + sl_distance * CONFIG.TP1_RR
            tp2 = prix_entree + sl_distance * CONFIG.TP2_RR
            tp3 = prix_entree + sl_distance * CONFIG.TP3_RR
        else:
            tp1 = prix_entree - sl_distance * CONFIG.TP1_RR
            tp2 = prix_entree - sl_distance * CONFIG.TP2_RR
            tp3 = prix_entree - sl_distance * CONFIG.TP3_RR

        # Vérifier le R:R minimum
        rr_effectif = sl_distance * CONFIG.RR_MINIMUM
        if direction == "LONG" and tp2 < prix_entree + rr_effectif:
            logger.debug(
                f"R:R insuffisant: {(tp2 - prix_entree) / sl_distance:.2f} "
                f"< {CONFIG.RR_MINIMUM}"
            )
            return None, None, None, None, None
        if direction == "SHORT" and tp2 > prix_entree - rr_effectif:
            logger.debug(f"R:R insuffisant pour SHORT")
            return None, None, None, None, None

        logger.debug(
            f"SL/TP calculés | {direction} @ {prix_entree:.5f} | "
            f"SL: {sl:.5f} ({sl_distance:.2f}pts) | "
            f"TP1: {tp1:.5f} | TP2: {tp2:.5f} | TP3: {tp3:.5f} | "
            f"R:R effectif: {CONFIG.TP2_RR:.1f}"
        )
        return sl, tp1, tp2, tp3, sl_distance

    # ── Rapport ────────────────────────────────────────────────────────────

    def get_resume(self) -> dict:
        """Retourne un résumé de l'état du risque pour le dashboard."""
        nb_trades = len(self.etat.historique_resultats)
        nb_gagnants = sum(1 for r in self.etat.historique_resultats if r > 0)
        win_rate = (nb_gagnants / nb_trades * 100) if nb_trades > 0 else 0.0

        return {
            "drawdown_journalier_pct": round(self.etat.drawdown_journalier_pct, 2),
            "drawdown_total_pct": round(self.etat.drawdown_total_pct, 2),
            "pertes_consecutives": self.etat.pertes_consecutives,
            "circuit_breaker_actif": self.etat.circuit_breaker_actif,
            "bot_arrete": self.etat.bot_arrete,
            "nb_trades": nb_trades,
            "win_rate": round(win_rate, 1),
        }


# ── Dataclasses pour le calcul de niveaux enrichi ─────────────────────────

@dataclass
class CandidatSL:
    """Candidat Stop Loss avec évaluation."""
    prix: float
    source: SourceNiveau
    confluence: Optional[NiveauConfluent]
    distance_entree: float
    base_asiatique: bool
    score_qualite: int
    raison: str

    # Alias anglais
    @property
    def price(self) -> float: return self.prix
    @property
    def quality_score(self) -> int: return self.score_qualite


@dataclass
class CandidatTP:
    """Candidat Take Profit avec évaluation."""
    prix: float
    id_niveau: int
    source: SourceNiveau
    confluence: Optional[NiveauConfluent]
    multiple_r: float
    a_obstacle: bool
    niveau_obstacle: Optional[float]
    est_liquidite: bool
    score_qualite: int
    raison: str

    # Alias anglais
    @property
    def price(self) -> float: return self.prix
    @property
    def r_multiple(self) -> float: return self.multiple_r
    @property
    def has_obstacle(self) -> bool: return self.a_obstacle
    @property
    def is_liquidity_target(self) -> bool: return self.est_liquidite
    @property
    def quality_score(self) -> int: return self.score_qualite


@dataclass
class NiveauxRisque:
    """Résultat complet du calcul SL/TP enrichi avec niveaux asiatiques."""
    prix_entree: float
    direction: str
    stop_loss: float
    sl_candidat: CandidatSL
    tp1: float
    tp2: float
    tp3: float
    tp1_candidat: CandidatTP
    tp2_candidat: CandidatTP
    tp3_candidat: CandidatTP
    distance_risque: float
    rr_tp1: float
    rr_tp2: float
    rr_tp3: float
    qualite_globale: int
    validation_reussie: bool
    raison_validation: str
    niveaux_asiatiques_utilises: List[str]
    obstacles_detectes: List[dict]

    # Alias anglais
    @property
    def entry_price(self) -> float: return self.prix_entree
    @property
    def overall_quality(self) -> int: return self.qualite_globale
    @property
    def validation_passed(self) -> bool: return self.validation_reussie
    @property
    def sl_candidate(self) -> CandidatSL: return self.sl_candidat
    @property
    def tp1_candidate(self) -> CandidatTP: return self.tp1_candidat
    @property
    def tp2_candidate(self) -> CandidatTP: return self.tp2_candidat
    @property
    def tp3_candidate(self) -> CandidatTP: return self.tp3_candidat
    @property
    def asian_levels_used(self) -> List[str]: return self.niveaux_asiatiques_utilises


# Alias anglais
RiskLevels = NiveauxRisque
SLCandidate = CandidatSL
TPCandidate = CandidatTP


# ── Calculateur de niveaux enrichi ────────────────────────────────────────

class CalculateurNiveaux:
    """
    Calcule les niveaux SL/TP en intégrant les niveaux asiatiques.
    Enrichit le calcul de base (OB + ATR) avec la confluence multi-sources.
    """

    def __init__(self, connecteur=None) -> None:
        self.connecteur = connecteur
        self.calc_confluence = CalculateurConfluence()
        self.analyseur_obstacles = AnalyseurObstacles()

    def calculer_niveaux(
        self,
        prix_entree: float,
        direction: str,
        zone_ob,
        session_asie=None,
        structure=None,
        atr_m15: float = 5.0,
    ) -> NiveauxRisque:
        """
        Calcule les niveaux SL/TP optimaux en intégrant les niveaux asiatiques.

        Args:
            prix_entree: Prix d'entrée.
            direction: "bullish" ou "bearish".
            zone_ob: Zone OB (OBMultiTimeframe ou tuple avec zone_entree_bas/haut).
            session_asie: DonneesSessionAsiatique (optionnel).
            structure: StructureMarche (optionnel).
            atr_m15: ATR M15 courant.

        Returns:
            NiveauxRisque avec tous les niveaux calculés.
        """
        # Collecter tous les niveaux disponibles
        tous_niveaux = self._collecter_niveaux(zone_ob, session_asie, structure, prix_entree)
        carte = self.calc_confluence.construire_carte_confluence(tous_niveaux)

        # Calculer le SL optimal
        sl_candidat = self._calculer_sl_optimal(
            prix_entree, direction, zone_ob, session_asie, carte, atr_m15
        )
        distance_sl = abs(prix_entree - sl_candidat.prix)

        # Calculer les 3 TP
        tp1_candidat = self._calculer_tp1(
            prix_entree, direction, distance_sl, session_asie, carte
        )
        tp2_candidat = self._calculer_tp2(
            prix_entree, direction, distance_sl, session_asie, carte, tp1_candidat.prix
        )
        tp3_candidat = self._calculer_tp3(
            prix_entree, direction, distance_sl, session_asie,
            structure, carte, tous_niveaux, tp2_candidat.prix
        )

        # Analyser les obstacles
        obstacles_tp2 = self.analyseur_obstacles.trouver_obstacles(
            prix_entree, tp2_candidat.prix, direction, tous_niveaux
        )
        obstacles_tp3 = self.analyseur_obstacles.trouver_obstacles(
            prix_entree, tp3_candidat.prix, direction, tous_niveaux
        )

        # Ajuster TP2 si obstacle majeur
        if self.analyseur_obstacles.a_obstacle_majeur(obstacles_tp2):
            tp2_ajuste = self.analyseur_obstacles.suggerer_tp_ajuste(
                prix_entree, tp2_candidat.prix, obstacles_tp2,
                direction, CONFIG.RR_MINIMUM, distance_sl,
            )
            if tp2_ajuste:
                tp2_candidat.prix = tp2_ajuste
                tp2_candidat.multiple_r = abs(tp2_ajuste - prix_entree) / distance_sl if distance_sl > 0 else 2.0
                tp2_candidat.a_obstacle = True
                tp2_candidat.niveau_obstacle = obstacles_tp2[0]["price"]

        # R:R finaux
        rr_tp1 = abs(tp1_candidat.prix - prix_entree) / distance_sl if distance_sl > 0 else 1.0
        rr_tp2 = abs(tp2_candidat.prix - prix_entree) / distance_sl if distance_sl > 0 else 2.0
        rr_tp3 = abs(tp3_candidat.prix - prix_entree) / distance_sl if distance_sl > 0 else 3.0

        # Validation R:R
        valide = rr_tp2 >= CONFIG.RR_MINIMUM
        raison_validation = (
            f"R:R TP2 = {rr_tp2:.2f} ≥ minimum {CONFIG.RR_MINIMUM}"
            if valide
            else f"R:R TP2 = {rr_tp2:.2f} < minimum {CONFIG.RR_MINIMUM} — trade refusé"
        )

        # Score global
        qualite = self._calculer_qualite_globale(
            sl_candidat, tp1_candidat, tp2_candidat, tp3_candidat,
            rr_tp2, obstacles_tp2
        )

        niveaux_asie = self._niveaux_asiatiques_utilises(
            sl_candidat, tp1_candidat, tp2_candidat, tp3_candidat
        )

        resultat = NiveauxRisque(
            prix_entree=prix_entree,
            direction=direction,
            stop_loss=sl_candidat.prix,
            sl_candidat=sl_candidat,
            tp1=tp1_candidat.prix,
            tp2=tp2_candidat.prix,
            tp3=tp3_candidat.prix,
            tp1_candidat=tp1_candidat,
            tp2_candidat=tp2_candidat,
            tp3_candidat=tp3_candidat,
            distance_risque=distance_sl,
            rr_tp1=rr_tp1,
            rr_tp2=rr_tp2,
            rr_tp3=rr_tp3,
            qualite_globale=qualite,
            validation_reussie=valide,
            raison_validation=raison_validation,
            niveaux_asiatiques_utilises=niveaux_asie,
            obstacles_detectes=obstacles_tp2 + obstacles_tp3,
        )

        self._logger_resume(resultat)
        return resultat

    # ── Collecte des niveaux ──────────────────────────────────────────────

    def _collecter_niveaux(
        self,
        zone_ob,
        session_asie,
        structure,
        prix_entree: float,
    ) -> List[Tuple[float, SourceNiveau]]:
        """Collecte tous les niveaux de prix de toutes les sources."""
        niveaux = []

        # Niveaux OB
        if zone_ob is not None:
            if hasattr(zone_ob, "zone_h4") and zone_ob.zone_h4:
                niveaux.append((zone_ob.zone_h4.zone_haut, SourceNiveau.ORDER_BLOCK_H4))
                niveaux.append((zone_ob.zone_h4.zone_bas, SourceNiveau.ORDER_BLOCK_H4))
            if hasattr(zone_ob, "zone_h1") and zone_ob.zone_h1:
                niveaux.append((zone_ob.zone_h1.zone_haut, SourceNiveau.ORDER_BLOCK_H1))
                niveaux.append((zone_ob.zone_h1.zone_bas, SourceNiveau.ORDER_BLOCK_H1))
            if hasattr(zone_ob, "zone_entree_bas"):
                niveaux.append((zone_ob.zone_entree_bas, SourceNiveau.ORDER_BLOCK_H4))
                niveaux.append((zone_ob.zone_entree_haut, SourceNiveau.ORDER_BLOCK_H4))
            if hasattr(zone_ob, "zone_bas"):
                niveaux.append((zone_ob.zone_bas, SourceNiveau.ORDER_BLOCK_H4))
                niveaux.append((zone_ob.zone_haut, SourceNiveau.ORDER_BLOCK_H4))

        # Niveaux asiatiques
        if session_asie is not None:
            niveaux.append((session_asie.haut_session, SourceNiveau.ASIE_HAUT))
            niveaux.append((session_asie.bas_session, SourceNiveau.ASIE_BAS))
            niveaux.append((session_asie.milieu_range, SourceNiveau.ASIE_MILIEU))
            for eq_h in session_asie.equal_hauts:
                niveaux.append((eq_h, SourceNiveau.ASIE_EQUAL_HAUT))
            for eq_b in session_asie.equal_bas:
                niveaux.append((eq_b, SourceNiveau.ASIE_EQUAL_BAS))
            for pool in session_asie.pools_liquidite:
                source = SourceNiveau.ASIE_BSL if pool["type"] == "BSL" else SourceNiveau.ASIE_SSL
                niveaux.append((pool["niveau"], source))

        # Niveaux structure H4
        if structure is not None:
            for _, sh in structure.swings_hauts:
                niveaux.append((sh, SourceNiveau.SWING_HAUT_H4))
            for _, sb in structure.swings_bas:
                niveaux.append((sb, SourceNiveau.SWING_BAS_H4))

        # Filtrer : ±5% du prix d'entrée
        if prix_entree > 0:
            niveaux = [
                (p, s) for p, s in niveaux
                if p > 0 and abs(p - prix_entree) / prix_entree <= 0.05
            ]

        return niveaux

    # ── Calcul SL ─────────────────────────────────────────────────────────

    def _calculer_sl_optimal(
        self,
        entree: float,
        direction: str,
        zone_ob,
        session_asie,
        carte: List[NiveauConfluent],
        atr_m15: float,
    ) -> CandidatSL:
        """Calcule le SL optimal — OB + ajustement asiatique."""
        candidats = []

        # Candidat 1 : basé sur l'OB
        sl_ob = self._sl_depuis_ob(entree, direction, zone_ob, atr_m15)
        if sl_ob is not None:
            score = self.calc_confluence.scorer_niveau(sl_ob, SourceNiveau.ORDER_BLOCK_H4, carte)
            candidats.append(CandidatSL(
                prix=sl_ob, source=SourceNiveau.ORDER_BLOCK_H4,
                confluence=self.calc_confluence.trouver_meilleur_niveau_proche(sl_ob, carte),
                distance_entree=abs(entree - sl_ob),
                base_asiatique=False, score_qualite=score,
                raison="OB zone ± 0.5× ATR",
            ))

        # Candidat 2 : aligné avec equal lows/highs asiatiques
        if session_asie:
            niveaux_asie_sl = (
                session_asie.equal_bas if direction == "bullish"
                else session_asie.equal_hauts
            )
            for niveau_asie in niveaux_asie_sl:
                if direction == "bullish" and niveau_asie < entree:
                    sl_asie = niveau_asie - atr_m15 * 0.3
                    source = SourceNiveau.ASIE_EQUAL_BAS
                elif direction == "bearish" and niveau_asie > entree:
                    sl_asie = niveau_asie + atr_m15 * 0.3
                    source = SourceNiveau.ASIE_EQUAL_HAUT
                else:
                    continue

                score = self.calc_confluence.scorer_niveau(sl_asie, source, carte) + 15
                candidats.append(CandidatSL(
                    prix=sl_asie, source=source,
                    confluence=self.calc_confluence.trouver_meilleur_niveau_proche(sl_asie, carte),
                    distance_entree=abs(entree - sl_asie),
                    base_asiatique=True, score_qualite=score,
                    raison=f"Equal {'Low' if direction == 'bullish' else 'High'} asiatique {niveau_asie:.2f} ± ATR",
                ))

        if not candidats:
            # Fallback ATR
            sl_fallback = entree - atr_m15 * 1.5 if direction == "bullish" else entree + atr_m15 * 1.5
            return CandidatSL(
                prix=round(sl_fallback, 2), source=SourceNiveau.ATR_BASE,
                confluence=None, distance_entree=atr_m15 * 1.5,
                base_asiatique=False, score_qualite=20,
                raison="Fallback ATR × 1.5",
            )

        # Filtrer : SL max 2.5× ATR
        max_dist = atr_m15 * 2.5
        valides = [c for c in candidats if c.distance_entree <= max_dist]
        if not valides:
            valides = candidats

        meilleur = max(valides, key=lambda x: x.score_qualite)
        meilleur.prix = round(meilleur.prix, 2)
        logger.info(
            f"SL sélectionné : {meilleur.prix:.2f} | "
            f"Source: {meilleur.source.value} | Score: {meilleur.score_qualite}/100"
        )
        return meilleur

    def _sl_depuis_ob(
        self,
        entree: float,
        direction: str,
        zone_ob,
        atr_m15: float,
    ) -> Optional[float]:
        """Extrait le niveau SL depuis la zone OB."""
        marge = atr_m15 * CONFIG.SL_MARGE_ATR

        if zone_ob is None:
            return None

        # Extraire le bas/haut de la zone
        if hasattr(zone_ob, "zone_h4") and zone_ob.zone_h4:
            zone_bas = zone_ob.zone_h4.zone_bas
            zone_haut = zone_ob.zone_h4.zone_haut
        elif hasattr(zone_ob, "zone_entree_bas"):
            zone_bas = zone_ob.zone_entree_bas
            zone_haut = zone_ob.zone_entree_haut
        elif hasattr(zone_ob, "zone_bas"):
            zone_bas = zone_ob.zone_bas
            zone_haut = zone_ob.zone_haut
        else:
            return None

        if direction == "bullish":
            sl = zone_bas - marge
            return sl if sl < entree else None
        else:
            sl = zone_haut + marge
            return sl if sl > entree else None

    # ── Calcul TP1 ────────────────────────────────────────────────────────

    def _calculer_tp1(
        self,
        entree: float,
        direction: str,
        distance_sl: float,
        session_asie,
        carte: List[NiveauConfluent],
    ) -> CandidatTP:
        """TP1 = 1R ou niveau confluent dans la fenêtre 0.8R–1.2R."""
        tp1_base = entree + distance_sl if direction == "bullish" else entree - distance_sl

        plus_proche = self.calc_confluence.trouver_meilleur_niveau_proche(
            tp1_base, carte, fenetre_pct=0.2
        )
        if plus_proche and plus_proche.score_confluence >= 3:
            valide = (
                (direction == "bullish" and plus_proche.prix > entree)
                or (direction == "bearish" and plus_proche.prix < entree)
            )
            if valide:
                est_liquidite = any(
                    s in (SourceNiveau.ASIE_BSL, SourceNiveau.ASIE_SSL)
                    for s in plus_proche.sources
                )
                return CandidatTP(
                    prix=round(plus_proche.prix, 2), id_niveau=1,
                    source=plus_proche.sources[0], confluence=plus_proche,
                    multiple_r=abs(plus_proche.prix - entree) / distance_sl if distance_sl > 0 else 1.0,
                    a_obstacle=False, niveau_obstacle=None,
                    est_liquidite=est_liquidite,
                    score_qualite=self.calc_confluence.scorer_niveau(plus_proche.prix, plus_proche.sources[0], carte),
                    raison=f"Niveau confluent à 1R | {plus_proche.description}",
                )

        return CandidatTP(
            prix=round(tp1_base, 2), id_niveau=1, source=SourceNiveau.ATR_BASE,
            confluence=None, multiple_r=1.0, a_obstacle=False, niveau_obstacle=None,
            est_liquidite=False, score_qualite=40, raison="TP1 = 1R exact",
        )

    # ── Calcul TP2 ────────────────────────────────────────────────────────

    def _calculer_tp2(
        self,
        entree: float,
        direction: str,
        distance_sl: float,
        session_asie,
        carte: List[NiveauConfluent],
        tp1_prix: float,
    ) -> CandidatTP:
        """TP2 = 2R ou pool de liquidité asiatique dans la fenêtre 1.5R–3R."""
        tp2_base = entree + distance_sl * 2.0 if direction == "bullish" else entree - distance_sl * 2.0

        # Priorité aux pools de liquidité asiatiques
        if session_asie:
            for pool in session_asie.pools_liquidite:
                niveau_pool = pool["niveau"]
                bon_type = (
                    (direction == "bullish" and pool["type"] == "BSL" and niveau_pool > tp1_prix)
                    or (direction == "bearish" and pool["type"] == "SSL" and niveau_pool < tp1_prix)
                )
                if not bon_type or distance_sl <= 0:
                    continue
                multiple_r = abs(niveau_pool - entree) / distance_sl
                if 1.5 <= multiple_r <= 3.0:
                    source = SourceNiveau.ASIE_BSL if pool["type"] == "BSL" else SourceNiveau.ASIE_SSL
                    score = self.calc_confluence.scorer_niveau(niveau_pool, source, carte)
                    return CandidatTP(
                        prix=round(niveau_pool, 2), id_niveau=2, source=source,
                        confluence=self.calc_confluence.trouver_meilleur_niveau_proche(niveau_pool, carte),
                        multiple_r=multiple_r, a_obstacle=False, niveau_obstacle=None,
                        est_liquidite=True, score_qualite=score,
                        raison=f"Pool {pool['type']} asiatique @ {niveau_pool:.2f} | R:R {multiple_r:.2f}",
                    )

        # Fallback niveau confluent
        plus_proche = self.calc_confluence.trouver_meilleur_niveau_proche(tp2_base, carte, 0.25)
        if plus_proche and plus_proche.score_confluence >= 2:
            valide = (
                (direction == "bullish" and plus_proche.prix > tp1_prix)
                or (direction == "bearish" and plus_proche.prix < tp1_prix)
            )
            if valide:
                return CandidatTP(
                    prix=round(plus_proche.prix, 2), id_niveau=2, source=plus_proche.sources[0],
                    confluence=plus_proche,
                    multiple_r=abs(plus_proche.prix - entree) / distance_sl if distance_sl > 0 else 2.0,
                    a_obstacle=False, niveau_obstacle=None, est_liquidite=False,
                    score_qualite=self.calc_confluence.scorer_niveau(plus_proche.prix, plus_proche.sources[0], carte),
                    raison=f"Niveau confluent à 2R | {plus_proche.description}",
                )

        return CandidatTP(
            prix=round(tp2_base, 2), id_niveau=2, source=SourceNiveau.ATR_BASE,
            confluence=None, multiple_r=2.0, a_obstacle=False, niveau_obstacle=None,
            est_liquidite=False, score_qualite=40, raison="TP2 = 2R exact",
        )

    # ── Calcul TP3 ────────────────────────────────────────────────────────

    def _calculer_tp3(
        self,
        entree: float,
        direction: str,
        distance_sl: float,
        session_asie,
        structure,
        carte: List[NiveauConfluent],
        tous_niveaux: List[Tuple[float, SourceNiveau]],
        tp2_prix: float,
    ) -> CandidatTP:
        """TP3 = equal highs/lows asiatiques ou swing H4 dans la fenêtre 2.5R–5R."""
        min_tp3 = entree + distance_sl * 2.5 if direction == "bullish" else entree - distance_sl * 2.5
        candidats = []

        # Equal highs/lows asiatiques
        if session_asie:
            niveaux_eq = (
                session_asie.equal_hauts if direction == "bullish"
                else session_asie.equal_bas
            )
            source_eq = SourceNiveau.ASIE_EQUAL_HAUT if direction == "bullish" else SourceNiveau.ASIE_EQUAL_BAS
            for eq in niveaux_eq:
                au_dela_tp2 = (direction == "bullish" and eq > tp2_prix) or (direction == "bearish" and eq < tp2_prix)
                au_dela_min = (direction == "bullish" and eq >= min_tp3) or (direction == "bearish" and eq <= min_tp3)
                if au_dela_tp2 and au_dela_min and distance_sl > 0:
                    r = abs(eq - entree) / distance_sl
                    score = self.calc_confluence.scorer_niveau(eq, source_eq, carte) + 20
                    candidats.append((eq, r, score, source_eq, True, "Equal High/Low asiatique"))

        # Swing H4
        if structure:
            swings = structure.swings_hauts if direction == "bullish" else structure.swings_bas
            source_sw = SourceNiveau.SWING_HAUT_H4 if direction == "bullish" else SourceNiveau.SWING_BAS_H4
            for _, sw in swings:
                au_dela_tp2 = (direction == "bullish" and sw > tp2_prix) or (direction == "bearish" and sw < tp2_prix)
                au_dela_min = (direction == "bullish" and sw >= min_tp3) or (direction == "bearish" and sw <= min_tp3)
                if au_dela_tp2 and au_dela_min and distance_sl > 0:
                    r = abs(sw - entree) / distance_sl
                    score = self.calc_confluence.scorer_niveau(sw, source_sw, carte)
                    candidats.append((sw, r, score, source_sw, False, "Swing H4"))

        if candidats:
            valides = [(p, r, q, s, liq, desc) for p, r, q, s, liq, desc in candidats if 2.5 <= r <= 5.0]
            if valides:
                meilleur = max(valides, key=lambda x: x[2])
                p, r, q, s, liq, desc = meilleur
                a_obs = len(self.analyseur_obstacles.trouver_obstacles(entree, p, direction, tous_niveaux)) > 0
                return CandidatTP(
                    prix=round(p, 2), id_niveau=3, source=s,
                    confluence=self.calc_confluence.trouver_meilleur_niveau_proche(p, carte),
                    multiple_r=r, a_obstacle=a_obs, niveau_obstacle=None,
                    est_liquidite=liq, score_qualite=q,
                    raison=f"TP3 : {desc} @ {p:.2f} | R:R {r:.2f}",
                )

        # Fallback 3R
        tp3_fallback = entree + distance_sl * 3.0 if direction == "bullish" else entree - distance_sl * 3.0
        return CandidatTP(
            prix=round(tp3_fallback, 2), id_niveau=3, source=SourceNiveau.ATR_BASE,
            confluence=None, multiple_r=3.0, a_obstacle=False, niveau_obstacle=None,
            est_liquidite=False, score_qualite=30, raison="TP3 = 3R exact (fallback)",
        )

    # ── Qualité et log ────────────────────────────────────────────────────

    def _calculer_qualite_globale(
        self,
        sl: CandidatSL, tp1: CandidatTP, tp2: CandidatTP, tp3: CandidatTP,
        rr_tp2: float, obstacles: List[dict],
    ) -> int:
        score = (
            sl.score_qualite * 0.30
            + tp2.score_qualite * 0.40
            + tp3.score_qualite * 0.20
            + min(20, rr_tp2 * 8)
        )
        score -= sum(10 for o in obstacles if o["severity"] == "major")
        if tp2.est_liquidite:
            score += 10
        if tp3.est_liquidite:
            score += 5
        return max(0, min(100, int(score)))

    def _niveaux_asiatiques_utilises(
        self, sl: CandidatSL, tp1: CandidatTP, tp2: CandidatTP, tp3: CandidatTP
    ) -> List[str]:
        sources_asie = {
            SourceNiveau.ASIE_HAUT, SourceNiveau.ASIE_BAS, SourceNiveau.ASIE_MILIEU,
            SourceNiveau.ASIE_EQUAL_HAUT, SourceNiveau.ASIE_EQUAL_BAS,
            SourceNiveau.ASIE_BSL, SourceNiveau.ASIE_SSL,
        }
        utilises = []
        for candidat, label in [(sl, "SL"), (tp1, "TP1"), (tp2, "TP2"), (tp3, "TP3")]:
            if candidat.source in sources_asie:
                utilises.append(f"{label}: {candidat.source.value}")
        return utilises

    def _logger_resume(self, res: NiveauxRisque) -> None:
        logger.info(
            f"📐 NIVEAUX | Entry: {res.prix_entree:.2f} | "
            f"SL: {res.stop_loss:.2f} [{res.sl_candidat.source.value}] | "
            f"TP1: {res.tp1:.2f} ({res.rr_tp1:.2f}R) | "
            f"TP2: {res.tp2:.2f} ({res.rr_tp2:.2f}R) | "
            f"TP3: {res.tp3:.2f} ({res.rr_tp3:.2f}R) | "
            f"Qualité: {res.qualite_globale}/100 | "
            f"{'✅' if res.validation_reussie else '❌'}"
        )
        if res.niveaux_asiatiques_utilises:
            logger.info(f"📐 Niveaux asiatiques : {' | '.join(res.niveaux_asiatiques_utilises)}")


# Instance globale du calculateur de niveaux
_calculateur_niveaux = CalculateurNiveaux()
