"""
addon_validator.py — Validation des conditions d'ouverture d'un add-on pyramiding.

Chaque check est indépendant et documenté. Un seul échec = add-on refusé.
Le validator est pur : il reçoit les données pré-calculées (structure, OBs)
et ne fait que de la logique — pas d'appels MT5.
"""

from datetime import datetime
from enum import Enum
from typing import List, Optional, Tuple

from loguru import logger

from config import CONFIG
from trade_manager import TradeGere, DirectionTrade


# ── Raisons d'annulation ───────────────────────────────────────────────────

class RaisonAnnulationAddon(Enum):
    """Raison pour laquelle un add-on a été refusé ou annulé."""
    STRUCTURE_INVERSEE     = "Structure H4 inversée"
    BIAIS_JOURNALIER       = "Daily bias incompatible"
    HORS_SESSION           = "Hors session active"
    DRAWDOWN_DEPASSE       = "Circuit breaker actif"
    PAS_D_OB_VALIDE        = "Aucun OB multi-TF valide"
    SPREAD_TROP_ELEVE      = "Spread trop élevé"
    NEWS_IMMINENTE         = "News imminente"
    TRADE_PARENT_FERME     = "Trade principal fermé"
    MAX_ADDONS_ATTEINT     = "Maximum 1 add-on par trade atteint"
    RR_INSUFFISANT         = "R:R add-on insuffisant"


# Alias anglais pour compatibilité
AddonCancelReason = RaisonAnnulationAddon


# ── Validateur principal ───────────────────────────────────────────────────

class ValidateurAddon:
    """
    Valide toutes les conditions d'ouverture d'un add-on pyramiding.

    Design : le validateur reçoit les données pré-calculées (structure H4,
    liste des OBs actifs) via validate(). Il gère lui-même les checks stateless
    (session, news, spread, circuit breaker).

    Un seul check raté → (False, raison, description).
    """

    def __init__(
        self,
        circuit_breaker=None,
        filtre_news=None,
        filtre_spread=None,
        analyseur_biais=None,
        config=None,
    ) -> None:
        self.circuit_breaker = circuit_breaker
        self.filtre_news = filtre_news
        self.filtre_spread = filtre_spread
        self.analyseur_biais = analyseur_biais
        self.config = config or CONFIG

    def validate(
        self,
        trade_parent: TradeGere,
        prix_actuel: float,
        heure_actuelle: datetime,
        structure=None,
        obs_actifs: Optional[List] = None,
    ) -> Tuple[bool, Optional[RaisonAnnulationAddon], str]:
        """
        Valide toutes les conditions pour un add-on.

        Args:
            trade_parent    : Le trade principal (TradeGere) en Phase 2.
            prix_actuel     : Prix bid ou ask courant (selon direction).
            heure_actuelle  : datetime UTC du moment de l'évaluation.
            structure       : StructureMarche pré-calculée (ou None → skip check).
            obs_actifs      : Liste d'OBMultiTimeframe actifs (ou None → skip check).

        Returns:
            (valide, raison_annulation, message_detail)
        """

        # ── CHECK 1 : Trade parent encore ouvert ──────────────────────────
        if trade_parent.est_ferme:
            return (False,
                    RaisonAnnulationAddon.TRADE_PARENT_FERME,
                    "Trade principal déjà fermé")

        # ── CHECK 2 : Circuit breaker ──────────────────────────────────────
        if self.circuit_breaker is not None:
            try:
                cb_ok, cb_raison, _ = self.circuit_breaker.trading_autorise()
                if not cb_ok:
                    return (False,
                            RaisonAnnulationAddon.DRAWDOWN_DEPASSE,
                            f"Circuit breaker actif : {cb_raison}")
            except Exception as e:
                logger.debug(f"Circuit breaker non disponible : {e}")

        # ── CHECK 3 : Session active ───────────────────────────────────────
        heure = heure_actuelle.hour
        debut_session = self.config.SESSION_LONDON_OUVERTURE
        fin_session = self.config.SESSION_FERMETURE
        if not (debut_session <= heure < fin_session):
            return (False,
                    RaisonAnnulationAddon.HORS_SESSION,
                    f"Hors session active (heure UTC : {heure}h)")

        # ── CHECK 4 : Filtre news ──────────────────────────────────────────
        if self.filtre_news is not None:
            try:
                news_ok, news_raison = self.filtre_news.is_trading_allowed(heure_actuelle)
                if not news_ok:
                    return (False,
                            RaisonAnnulationAddon.NEWS_IMMINENTE,
                            f"News imminente : {news_raison}")
            except Exception as e:
                logger.debug(f"Filtre news non disponible : {e}")

        # ── CHECK 5 : Filtre spread ────────────────────────────────────────
        if self.filtre_spread is not None:
            try:
                spread_ok, spread_raison = self.filtre_spread.is_market_tradeable(heure_actuelle)
                if not spread_ok:
                    return (False,
                            RaisonAnnulationAddon.SPREAD_TROP_ELEVE,
                            f"Spread défavorable : {spread_raison}")
            except Exception as e:
                logger.debug(f"Filtre spread non disponible : {e}")

        # ── CHECK 6 : Structure H4 toujours alignée ────────────────────────
        if structure is not None:
            resultat_struct = self._verifier_structure(trade_parent, structure)
            if resultat_struct is not None:
                raison, msg = resultat_struct
                return (False, raison, msg)

        # ── CHECK 7 : Daily bias aligné ────────────────────────────────────
        if self.analyseur_biais is not None:
            try:
                direction_signal = (
                    "bullish" if trade_parent.est_long() else "bearish"
                )
                biais_ok, biais_raison = self.analyseur_biais.signal_aligne_avec_biais(
                    direction_signal
                )
                if not biais_ok:
                    return (False,
                            RaisonAnnulationAddon.BIAIS_JOURNALIER,
                            f"Daily bias incompatible : {biais_raison}")
            except Exception as e:
                logger.debug(f"Analyseur biais non disponible : {e}")

        # ── CHECK 8 : OB multi-TF valide au prix actuel ────────────────────
        if obs_actifs is not None:
            resultat_ob = self._verifier_ob(trade_parent, prix_actuel, obs_actifs)
            if resultat_ob is not None:
                raison, msg = resultat_ob
                return (False, raison, msg)
            # Trouver le meilleur OB pour le log
            meilleur_score = self._score_meilleur_ob(trade_parent, prix_actuel, obs_actifs)
            return (True, None,
                    f"Toutes conditions validées | OB score : {meilleur_score}/100")

        return (True, None, "Toutes conditions validées")

    # ── Vérifications internes ─────────────────────────────────────────────

    def _verifier_structure(
        self,
        trade_parent: TradeGere,
        structure,
    ) -> Optional[Tuple[RaisonAnnulationAddon, str]]:
        """
        Vérifie que la structure H4 reste alignée avec la direction du trade.
        Retourne (raison, msg) si invalide, None si ok.
        """
        try:
            # Import ici pour éviter la dépendance circulaire
            from structure_analyzer import Tendance

            tendance = getattr(structure, "tendance", None)
            if tendance is None:
                return None  # Pas de donnée → skip

            est_long = trade_parent.est_long()
            structure_ok = (
                (est_long and tendance == Tendance.HAUSSIERE)
                or (not est_long and tendance == Tendance.BAISSIERE)
            )

            if not structure_ok:
                direction_str = "LONG" if est_long else "SHORT"
                return (
                    RaisonAnnulationAddon.STRUCTURE_INVERSEE,
                    f"Structure H4 {tendance.value} — incompatible avec trade {direction_str}",
                )

            # Vérifier qu'aucun BOS fort contraire n'est apparu depuis l'entrée
            derniere_cassure = getattr(structure, "derniere_cassure", None)
            if derniere_cassure is not None:
                est_valide = getattr(derniere_cassure, "est_valide", False)
                type_bos = getattr(derniere_cassure, "type_bos", None)
                ts_cassure = getattr(derniere_cassure, "timestamp_cassure", None)

                if est_valide and type_bos is not None and ts_cassure is not None:
                    from structure_analyzer import TypeBOS
                    bos_adverse = (
                        (est_long and type_bos in (TypeBOS.BEARISH_STRONG,))
                        or (not est_long and type_bos in (TypeBOS.BULLISH_STRONG,))
                    )
                    if bos_adverse and ts_cassure > trade_parent.heure_entree:
                        return (
                            RaisonAnnulationAddon.STRUCTURE_INVERSEE,
                            f"BOS fort adverse détecté après l'entrée "
                            f"@ {ts_cassure.strftime('%H:%M UTC')}",
                        )

        except Exception as e:
            logger.debug(f"Vérification structure add-on : {e}")

        return None  # Structure ok

    def _verifier_ob(
        self,
        trade_parent: TradeGere,
        prix_actuel: float,
        obs_actifs: List,
    ) -> Optional[Tuple[RaisonAnnulationAddon, str]]:
        """
        Vérifie qu'un OB multi-TF valide (score ≥ ADDON_SCORE_OB_MIN)
        est présent à ±0.3% du prix actuel.
        Retourne (raison, msg) si invalide, None si ok.
        """
        if not obs_actifs:
            return (
                RaisonAnnulationAddon.PAS_D_OB_VALIDE,
                f"Aucun OB multi-TF disponible pour l'add-on",
            )

        try:
            from ob_detector import TypeOB

            est_long = trade_parent.est_long()
            tolerance = 0.003  # ±0.3% du prix

            obs_direction = [
                ob for ob in obs_actifs
                if (est_long and getattr(ob, "ob_type", None) == TypeOB.HAUSSIER)
                or (not est_long and getattr(ob, "ob_type", None) == TypeOB.BAISSIER)
            ]

            obs_proches = [
                ob for ob in obs_direction
                if self._ob_proche(ob, prix_actuel, tolerance)
            ]

            if not obs_proches:
                direction_str = "haussier" if est_long else "baissier"
                return (
                    RaisonAnnulationAddon.PAS_D_OB_VALIDE,
                    f"Aucun OB {direction_str} valide à ±0.3% du prix {prix_actuel:.2f}",
                )

            meilleur = max(obs_proches, key=lambda ob: getattr(ob, "score", 0))
            score = getattr(meilleur, "score", 0)

            if score < self.config.ADDON_SCORE_OB_MIN:
                return (
                    RaisonAnnulationAddon.PAS_D_OB_VALIDE,
                    f"OB trouvé mais score insuffisant : {score}/100 "
                    f"< {self.config.ADDON_SCORE_OB_MIN} minimum",
                )

        except Exception as e:
            logger.debug(f"Vérification OB add-on : {e}")

        return None  # OB ok

    def _ob_proche(self, ob, prix: float, tolerance: float) -> bool:
        """True si l'OB contient le prix ou est à <tolerance% de lui."""
        try:
            bas = getattr(ob, "entry_zone_low", None)
            haut = getattr(ob, "entry_zone_high", None)
            milieu = getattr(ob, "entry_zone_mid", None)

            if bas is not None and haut is not None:
                if bas <= prix <= haut:
                    return True
            if milieu is not None:
                return abs(prix - milieu) / prix < tolerance
        except Exception:
            pass
        return False

    def _score_meilleur_ob(
        self, trade_parent: TradeGere, prix: float, obs_actifs: List
    ) -> int:
        """Retourne le score du meilleur OB proche (pour le log)."""
        try:
            from ob_detector import TypeOB
            est_long = trade_parent.est_long()
            tolerance = 0.005  # 0.5% pour le log

            obs_filtres = [
                ob for ob in obs_actifs
                if (est_long and getattr(ob, "ob_type", None) == TypeOB.HAUSSIER)
                or (not est_long and getattr(ob, "ob_type", None) == TypeOB.BAISSIER)
                if self._ob_proche(ob, prix, tolerance)
            ]
            if not obs_filtres:
                return 0
            return max(getattr(ob, "score", 0) for ob in obs_filtres)
        except Exception:
            return 0


# Alias anglais
AddonValidator = ValidateurAddon
