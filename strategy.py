"""
strategy.py — Logique centrale du setup SMC Breaker Block + Order Block Confluence.
C'est le SEUL setup que le bot exécute. Aucune déviation autorisée.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Optional, List, Tuple
import pandas as pd
from loguru import logger

from config import CONFIG
from structure_analyzer import AnalyseurStructure, StructureMarche as AnalyseStructure, Tendance, TypeBOS
from ob_detector import DetecteurOB, OBMultiTimeframe, TypeOB, StatutOB
from ob_visualizer import logger_resume_ob
from indicators import Indicateurs
from news_filter import FiltreNews
from news_fetcher import RecuperateurCalendrier
from spread_filter import FiltreSpread


class DirectionSignal(Enum):
    LONG = "LONG"
    SHORT = "SHORT"
    AUCUN = "NONE"


@dataclass
class SignalTrading:
    """Résultat de l'évaluation du setup SMC."""
    direction: DirectionSignal
    valide: bool
    zone_reference: Optional[OBMultiTimeframe]  # Nouveau : OBMultiTimeframe
    prix_entree_suggere: float = 0.0
    raison_rejet: str = ""
    atr_m15: float = 0.0
    score_confiance: int = 0  # Score OB (0–100)


class StrategieSMC:
    """
    Évalue le setup 'Breaker Block + Order Block Confluence' sur 3 timeframes.
    Conditions TOUTES requises simultanément pour valider un signal.
    """

    def __init__(
        self,
        filtre_news: Optional[FiltreNews] = None,
        filtre_spread: Optional[FiltreSpread] = None,
        circuit_breaker=None,
    ) -> None:
        """
        Args:
            filtre_news: Instance FiltreNews injectée (optionnel).
            filtre_spread: Instance FiltreSpread injectée (optionnel).
            circuit_breaker: Instance CircuitBreaker injectée (optionnel).
        """
        self.analyseur_structure = AnalyseurStructure()
        self.detecteur_ob = DetecteurOB()
        self.filtre_news = filtre_news or FiltreNews(fetcher=RecuperateurCalendrier())
        self.filtre_spread = filtre_spread
        self.circuit_breaker = circuit_breaker
        self._dernier_multiplicateur_risk: float = 1.0  # Transmis au risk_manager

    # ── Point d'entrée principal ───────────────────────────────────────────

    def evaluer(
        self,
        df_h4: pd.DataFrame,
        df_m15: pd.DataFrame,
        df_m5: pd.DataFrame,
        heure_utc: int,
        est_en_session: bool,
    ) -> SignalTrading:
        """
        Évalue le setup complet et retourne un signal de trading.

        Args:
            df_h4: DataFrame H4 (structure de marché).
            df_m15: DataFrame M15 (confirmation d'entrée).
            df_m5: DataFrame M5 (déclencheur final).
            heure_utc: Heure UTC actuelle (int).
            est_en_session: True si dans une session de trading active.

        Returns:
            SignalTrading avec direction et métadonnées.
        """
        # ── Filtre 0 : Circuit Breaker (le moins coûteux — premier filtre) ──
        if self.circuit_breaker is not None:
            cb_autorise, cb_raison, cb_multiplicateur = (
                self.circuit_breaker.trading_autorise()
            )
            self._dernier_multiplicateur_risk = cb_multiplicateur
            if not cb_autorise:
                return SignalTrading(
                    direction=DirectionSignal.AUCUN,
                    valide=False,
                    zone_reference=None,
                    raison_rejet=cb_raison,
                )

        # ── Filtres globaux (vérifiés avant tout calcul coûteux) ──────────
        if not est_en_session:
            return SignalTrading(
                direction=DirectionSignal.AUCUN,
                valide=False,
                zone_reference=None,
                raison_rejet="Hors session de trading",
            )

        # ── Ordre d'évaluation des filtres (du moins coûteux au plus coûteux) ──
        # 0. Circuit Breaker (déjà vérifié ci-dessus)
        # 1. Session (déjà vérifié ci-dessus)
        # 2. News (lookup dict)
        # 3. Spread / hard cap (lecture symbol_info)
        # 4. ATR volatilité (lecture OHLCV + calcul) — intégré dans filtre_spread
        # 5. Structure H4 (calcul lourd)
        # 6. Order Blocks (calcul lourd)
        # 7. Confirmation M15/M5 (calcul lourd)

        # ── Filtre news ────────────────────────────────────────────────────
        news_ok, raison_news = self.filtre_news.is_trading_allowed(
            datetime.now(timezone.utc)
        )
        if not news_ok:
            return SignalTrading(
                direction=DirectionSignal.AUCUN,
                valide=False,
                zone_reference=None,
                raison_rejet=f"⛔ NEWS: {raison_news}",
            )

        # ── Filtre spread + volatilité ATR ────────────────────────────────
        if self.filtre_spread is not None:
            spread_ok, raison_spread = self.filtre_spread.is_market_tradeable(
                datetime.now(timezone.utc)
            )
            if not spread_ok:
                return SignalTrading(
                    direction=DirectionSignal.AUCUN,
                    valide=False,
                    zone_reference=None,
                    raison_rejet=f"⛔ SPREAD: {raison_spread}",
                )

        if not Indicateurs.atr_volatilite_suffisante(df_h4):
            return SignalTrading(
                direction=DirectionSignal.AUCUN,
                valide=False,
                zone_reference=None,
                raison_rejet="Volatilité H4 insuffisante (marché plat)",
            )

        # ── Analyse de structure H4 avec validation Displacement ─────────
        analyse = self.analyseur_structure.analyser(df_h4)

        # Règle : tendance RANGE ou NEUTRE → pas de trade
        from structure_analyzer import Tendance as TendanceEnum
        if analyse.tendance in (TendanceEnum.RANGE, TendanceEnum.NEUTRE):
            return SignalTrading(
                direction=DirectionSignal.AUCUN,
                valide=False,
                zone_reference=None,
                raison_rejet=f"Marché en {analyse.tendance.value} — aucun BOS_FORT récent",
            )

        # Règle : tendance trop ancienne
        if analyse.age_tendance_bougies > CONFIG.TREND_MAX_AGE_CANDLES:
            return SignalTrading(
                direction=DirectionSignal.AUCUN,
                valide=False,
                zone_reference=None,
                raison_rejet=(
                    f"Tendance {analyse.tendance.value} trop ancienne "
                    f"({analyse.age_tendance_bougies} bougies H4) — attendre confirmation"
                ),
            )

        # ── Détection des OB multi-TF (H4 + H1 + M15) ────────────────────
        # H4 est obligatoire — sans H4 l'OB est rejeté
        # Seuls les OB score >= 60 (FORT/INSTITUTIONNEL) sont retournés
        obs_mtf = self.detecteur_ob.detecter_multi_tf(df_h4, df_m15=df_m15)
        logger_resume_ob(obs_mtf, float(df_m5["close"].iloc[-1]))

        # ── Calcul des indicateurs M15 ────────────────────────────────────
        rsi_m15 = Indicateurs.rsi(df_m15)
        atr_m15_serie = Indicateurs.atr(df_m15)
        atr_m15 = float(atr_m15_serie.iloc[-1]) if len(atr_m15_serie) > 0 else 0.0
        prix_actuel = float(df_m5["close"].iloc[-1])

        # ── Évaluation LONG ───────────────────────────────────────────────
        signal_long = self._evaluer_long_mtf(
            analyse, obs_mtf, df_m15, df_m5, rsi_m15, atr_m15, prix_actuel
        )
        if signal_long.valide:
            logger.info(
                f"✅ SIGNAL LONG VALIDÉ | "
                f"Zone: [{signal_long.zone_reference.zone_entree_bas:.2f}"
                f"–{signal_long.zone_reference.zone_entree_haut:.2f}] | "
                f"Score OB: {signal_long.score_confiance}/100"
            )
            return signal_long

        # ── Évaluation SHORT ──────────────────────────────────────────────
        signal_short = self._evaluer_short_mtf(
            analyse, obs_mtf, df_m15, df_m5, rsi_m15, atr_m15, prix_actuel
        )
        if signal_short.valide:
            logger.info(
                f"✅ SIGNAL SHORT VALIDÉ | "
                f"Zone: [{signal_short.zone_reference.zone_entree_bas:.2f}"
                f"–{signal_short.zone_reference.zone_entree_haut:.2f}] | "
                f"Score OB: {signal_short.score_confiance}/100"
            )
            return signal_short

        raison = signal_long.raison_rejet or signal_short.raison_rejet or "Conditions non réunies"
        logger.debug(f"Pas de signal | {raison}")

        return SignalTrading(
            direction=DirectionSignal.AUCUN,
            valide=False,
            zone_reference=None,
            raison_rejet=raison,
        )

    # ── Évaluation LONG multi-TF ──────────────────────────────────────────

    def _evaluer_long_mtf(
        self,
        analyse: AnalyseStructure,
        obs_mtf: List[OBMultiTimeframe],
        df_m15: pd.DataFrame,
        df_m5: pd.DataFrame,
        rsi_m15: pd.Series,
        atr_m15: float,
        prix_actuel: float,
    ) -> SignalTrading:
        """
        Évalue le signal LONG avec les nouveaux OB multi-TF.
        Remplace _evaluer_long() en utilisant OBMultiTimeframe.
        """
        # Condition 1 : Structure H4 haussière
        if analyse.tendance != Tendance.HAUSSIERE:
            return SignalTrading(
                direction=DirectionSignal.LONG, valide=False, zone_reference=None,
                raison_rejet=f"Structure H4 non haussière ({analyse.tendance.value})",
            )

        if not analyse.derniere_cassure or analyse.derniere_cassure.type not in (
            TypeBOS.BOS_HAUSSIER, TypeBOS.CHOCH_HAUSSIER
        ):
            return SignalTrading(
                direction=DirectionSignal.LONG, valide=False, zone_reference=None,
                raison_rejet="Pas de BOS haussier récent sur H4",
            )

        # Condition 2 : OB multi-TF haussier actif
        obs_haussiers = [
            ob for ob in obs_mtf
            if ob.type_ob == TypeOB.HAUSSIER
            and ob.statut not in (StatutOB.INVALIDE, StatutOB.EPUISE)
        ]
        if not obs_haussiers:
            return SignalTrading(
                direction=DirectionSignal.LONG, valide=False, zone_reference=None,
                raison_rejet="Aucun OB haussier multi-TF valide (score ≥ 60)",
            )

        # Prendre le meilleur OB (score le plus élevé)
        meilleur_ob = obs_haussiers[0]

        # Condition 3 : Prix dans ou proche de la zone d'entrée
        tolerance = prix_actuel * 0.005  # ±0.5%
        dans_zone = (
            meilleur_ob.zone_entree_bas - tolerance
            <= prix_actuel
            <= meilleur_ob.zone_entree_haut + tolerance
        )
        if not dans_zone:
            distance_pct = abs(prix_actuel - meilleur_ob.zone_entree_milieu) / prix_actuel * 100
            return SignalTrading(
                direction=DirectionSignal.LONG, valide=False, zone_reference=None,
                raison_rejet=f"Prix hors zone OB haussier ({distance_pct:.2f}% de distance)",
            )

        # Condition 4 : RSI M15 favorable
        rsi_actuel = float(rsi_m15.iloc[-1])
        if rsi_actuel >= CONFIG.RSI_SEUIL_LONG:
            return SignalTrading(
                direction=DirectionSignal.LONG, valide=False, zone_reference=None,
                raison_rejet=f"RSI M15 trop élevé: {rsi_actuel:.1f} ≥ {CONFIG.RSI_SEUIL_LONG}",
            )

        # Condition 5 : Bougie de rejet M15
        idx_m15 = len(df_m15) - 2
        if not Indicateurs.bougie_rejet_haussiere(df_m15, idx_m15):
            return SignalTrading(
                direction=DirectionSignal.LONG, valide=False, zone_reference=None,
                raison_rejet="Pas de bougie de rejet haussière sur M15",
            )

        # Condition 6 : Déclencheur M5
        idx_m5 = len(df_m5) - 2
        high_rejet_m15 = df_m15["high"].iloc[idx_m15]
        if df_m5["close"].iloc[idx_m5] <= high_rejet_m15:
            return SignalTrading(
                direction=DirectionSignal.LONG, valide=False, zone_reference=None,
                raison_rejet="M5 ne casse pas le high du rejet M15",
            )

        if not Indicateurs.volume_superieur_moyenne(df_m5, idx_m5):
            return SignalTrading(
                direction=DirectionSignal.LONG, valide=False, zone_reference=None,
                raison_rejet="Volume M5 insuffisant",
            )

        return SignalTrading(
            direction=DirectionSignal.LONG,
            valide=True,
            zone_reference=meilleur_ob,
            prix_entree_suggere=float(df_m5["close"].iloc[-1]),
            atr_m15=atr_m15,
            score_confiance=meilleur_ob.score,
        )

    def _evaluer_short_mtf(
        self,
        analyse: AnalyseStructure,
        obs_mtf: List[OBMultiTimeframe],
        df_m15: pd.DataFrame,
        df_m5: pd.DataFrame,
        rsi_m15: pd.Series,
        atr_m15: float,
        prix_actuel: float,
    ) -> SignalTrading:
        """Évalue le signal SHORT avec les nouveaux OB multi-TF (symétrique au LONG)."""
        if analyse.tendance != Tendance.BAISSIERE:
            return SignalTrading(
                direction=DirectionSignal.SHORT, valide=False, zone_reference=None,
                raison_rejet=f"Structure H4 non baissière ({analyse.tendance.value})",
            )

        if not analyse.derniere_cassure or analyse.derniere_cassure.type not in (
            TypeBOS.BOS_BAISSIER, TypeBOS.CHOCH_BAISSIER
        ):
            return SignalTrading(
                direction=DirectionSignal.SHORT, valide=False, zone_reference=None,
                raison_rejet="Pas de BOS baissier récent sur H4",
            )

        obs_baissiers = [
            ob for ob in obs_mtf
            if ob.type_ob == TypeOB.BAISSIER
            and ob.statut not in (StatutOB.INVALIDE, StatutOB.EPUISE)
        ]
        if not obs_baissiers:
            return SignalTrading(
                direction=DirectionSignal.SHORT, valide=False, zone_reference=None,
                raison_rejet="Aucun OB baissier multi-TF valide (score ≥ 60)",
            )

        meilleur_ob = obs_baissiers[0]
        tolerance = prix_actuel * 0.005
        dans_zone = (
            meilleur_ob.zone_entree_bas - tolerance
            <= prix_actuel
            <= meilleur_ob.zone_entree_haut + tolerance
        )
        if not dans_zone:
            distance_pct = abs(prix_actuel - meilleur_ob.zone_entree_milieu) / prix_actuel * 100
            return SignalTrading(
                direction=DirectionSignal.SHORT, valide=False, zone_reference=None,
                raison_rejet=f"Prix hors zone OB baissier ({distance_pct:.2f}% de distance)",
            )

        rsi_actuel = float(rsi_m15.iloc[-1])
        if rsi_actuel <= CONFIG.RSI_SEUIL_SHORT:
            return SignalTrading(
                direction=DirectionSignal.SHORT, valide=False, zone_reference=None,
                raison_rejet=f"RSI M15 trop bas: {rsi_actuel:.1f} ≤ {CONFIG.RSI_SEUIL_SHORT}",
            )

        idx_m15 = len(df_m15) - 2
        if not Indicateurs.bougie_rejet_baissiere(df_m15, idx_m15):
            return SignalTrading(
                direction=DirectionSignal.SHORT, valide=False, zone_reference=None,
                raison_rejet="Pas de bougie de rejet baissière sur M15",
            )

        idx_m5 = len(df_m5) - 2
        low_rejet_m15 = df_m15["low"].iloc[idx_m15]
        if df_m5["close"].iloc[idx_m5] >= low_rejet_m15:
            return SignalTrading(
                direction=DirectionSignal.SHORT, valide=False, zone_reference=None,
                raison_rejet="M5 ne casse pas le low du rejet M15",
            )

        if not Indicateurs.volume_superieur_moyenne(df_m5, idx_m5):
            return SignalTrading(
                direction=DirectionSignal.SHORT, valide=False, zone_reference=None,
                raison_rejet="Volume M5 insuffisant",
            )

        return SignalTrading(
            direction=DirectionSignal.SHORT,
            valide=True,
            zone_reference=meilleur_ob,
            prix_entree_suggere=float(df_m5["close"].iloc[-1]),
            atr_m15=atr_m15,
            score_confiance=meilleur_ob.score,
        )

    # ── Évaluation LONG legacy (conservé pour compatibilité tests) ─────────

    def _evaluer_long(
        self,
        analyse: AnalyseStructure,
        zones: List[OBMultiTimeframe],
        df_m15: pd.DataFrame,
        df_m5: pd.DataFrame,
        rsi_m15: pd.Series,
        atr_m15: float,
        prix_actuel: float,
    ) -> SignalTrading:
        """
        Vérifie les 5 conditions du setup LONG.
        Toutes les conditions doivent être vraies simultanément.
        """
        score = 0

        # ── Condition 1 : Structure H4 haussière + BOS haussier ───────────
        if analyse.tendance != Tendance.HAUSSIERE:
            return SignalTrading(
                direction=DirectionSignal.LONG,
                valide=False,
                zone_reference=None,
                raison_rejet=f"Structure H4 non haussière ({analyse.tendance.value})",
            )

        if not analyse.derniere_cassure or analyse.derniere_cassure.type not in (
            TypeBOS.BOS_HAUSSIER, TypeBOS.CHOCH_HAUSSIER
        ):
            return SignalTrading(
                direction=DirectionSignal.LONG,
                valide=False,
                zone_reference=None,
                raison_rejet="Pas de BOS haussier récent sur H4",
            )

        if not analyse.choch_recent:
            return SignalTrading(
                direction=DirectionSignal.LONG,
                valide=False,
                zone_reference=None,
                raison_rejet="Pas de CHoCH récent (< 20 bougies H4)",
            )
        score += 30

        # ── Condition 2 : Zone de demande H4 dans le discount ─────────────
        zones_haussières = [z for z in zones if z.est_haussier]
        if not zones_haussières:
            return SignalTrading(
                direction=DirectionSignal.LONG,
                valide=False,
                zone_reference=None,
                raison_rejet="Aucun OB/Breaker haussier actif sur H4",
            )

        zone_cible = self.detecteur_ob.trouver_zone_la_plus_proche(
            zones_haussières, prix_actuel, "BULL"
        )
        if zone_cible is None:
            return SignalTrading(
                direction=DirectionSignal.LONG,
                valide=False,
                zone_reference=None,
                raison_rejet="Aucune zone haussière accessible (prix trop loin)",
            )

        if not analyse.est_dans_discount(prix_actuel, analyse):
            return SignalTrading(
                direction=DirectionSignal.LONG,
                valide=False,
                zone_reference=None,
                raison_rejet="Prix pas dans la zone de discount (> 50% du leg)",
            )
        score += 25

        # ── Condition 3 : Le prix entre dans la zone sur M15 + RSI ────────
        prix_dans_zone = zone_cible.contient(prix_actuel) or \
            zone_cible.contient(df_m15["low"].iloc[-1])

        if not prix_dans_zone:
            return SignalTrading(
                direction=DirectionSignal.LONG,
                valide=False,
                zone_reference=None,
                raison_rejet=f"Prix M15 {prix_actuel:.2f} pas dans la zone "
                             f"[{zone_cible.prix_bas:.2f}–{zone_cible.prix_haut:.2f}]",
            )

        rsi_actuel = float(rsi_m15.iloc[-1])
        if rsi_actuel >= CONFIG.RSI_SEUIL_LONG:
            return SignalTrading(
                direction=DirectionSignal.LONG,
                valide=False,
                zone_reference=None,
                raison_rejet=f"RSI M15 trop élevé: {rsi_actuel:.1f} ≥ {CONFIG.RSI_SEUIL_LONG}",
            )
        score += 20

        # Bougie de rejet haussière sur M15
        idx_m15 = len(df_m15) - 2  # Dernière bougie FERMÉE
        if not Indicateurs.bougie_rejet_haussiere(df_m15, idx_m15):
            return SignalTrading(
                direction=DirectionSignal.LONG,
                valide=False,
                zone_reference=None,
                raison_rejet="Pas de bougie de rejet haussière sur M15",
            )
        score += 15

        # ── Condition 4 : Déclencheur M5 ──────────────────────────────────
        idx_m5 = len(df_m5) - 2  # Dernière bougie M5 fermée
        bougie_m5 = df_m5.iloc[idx_m5]
        bougie_m5_precedente = df_m5.iloc[idx_m5 - 1] if idx_m5 > 0 else None

        # Clôture M5 haussière qui casse le high de la bougie M15 de rejet
        high_rejet_m15 = df_m15["high"].iloc[idx_m15]
        if bougie_m5["close"] <= high_rejet_m15:
            return SignalTrading(
                direction=DirectionSignal.LONG,
                valide=False,
                zone_reference=None,
                raison_rejet="M5 ne casse pas le high de la bougie de rejet M15",
            )

        # Volume M5 supérieur à la moyenne
        if not Indicateurs.volume_superieur_moyenne(df_m5, idx_m5):
            return SignalTrading(
                direction=DirectionSignal.LONG,
                valide=False,
                zone_reference=None,
                raison_rejet="Volume M5 inférieur à la moyenne (signal faible)",
            )
        score += 10

        # ── Toutes les conditions validées → Signal LONG ──────────────────
        return SignalTrading(
            direction=DirectionSignal.LONG,
            valide=True,
            zone_reference=zone_cible,
            prix_entree_suggere=float(df_m5["close"].iloc[-1]),
            atr_m15=atr_m15,
            score_confiance=score,
        )

    # ── Évaluation SHORT (symétrique au LONG) ─────────────────────────────

    def _evaluer_short(
        self,
        analyse: AnalyseStructure,
        zones: List[OBMultiTimeframe],
        df_m15: pd.DataFrame,
        df_m5: pd.DataFrame,
        rsi_m15: pd.Series,
        atr_m15: float,
        prix_actuel: float,
    ) -> SignalTrading:
        """Vérifie les 5 conditions du setup SHORT (symétrique au LONG)."""
        score = 0

        # ── Condition 1 : Structure H4 baissière ──────────────────────────
        if analyse.tendance != Tendance.BAISSIERE:
            return SignalTrading(
                direction=DirectionSignal.SHORT,
                valide=False,
                zone_reference=None,
                raison_rejet=f"Structure H4 non baissière ({analyse.tendance.value})",
            )

        if not analyse.derniere_cassure or analyse.derniere_cassure.type not in (
            TypeBOS.BOS_BAISSIER, TypeBOS.CHOCH_BAISSIER
        ):
            return SignalTrading(
                direction=DirectionSignal.SHORT,
                valide=False,
                zone_reference=None,
                raison_rejet="Pas de BOS baissier récent sur H4",
            )

        if not analyse.choch_recent:
            return SignalTrading(
                direction=DirectionSignal.SHORT,
                valide=False,
                zone_reference=None,
                raison_rejet="Pas de CHoCH récent (< 20 bougies H4)",
            )
        score += 30

        # ── Condition 2 : Zone d'offre H4 dans le premium ─────────────────
        zones_baissières = [z for z in zones if not z.est_haussier]
        if not zones_baissières:
            return SignalTrading(
                direction=DirectionSignal.SHORT,
                valide=False,
                zone_reference=None,
                raison_rejet="Aucun OB/Breaker baissier actif sur H4",
            )

        zone_cible = self.detecteur_ob.trouver_zone_la_plus_proche(
            zones_baissières, prix_actuel, "BEAR"
        )
        if zone_cible is None:
            return SignalTrading(
                direction=DirectionSignal.SHORT,
                valide=False,
                zone_reference=None,
                raison_rejet="Aucune zone baissière accessible",
            )

        if not self.analyseur_structure.est_dans_premium(prix_actuel, analyse):
            return SignalTrading(
                direction=DirectionSignal.SHORT,
                valide=False,
                zone_reference=None,
                raison_rejet="Prix pas dans la zone de premium (< 50% du leg)",
            )
        score += 25

        # ── Condition 3 : Prix dans la zone M15 + RSI ─────────────────────
        prix_dans_zone = zone_cible.contient(prix_actuel) or \
            zone_cible.contient(df_m15["high"].iloc[-1])

        if not prix_dans_zone:
            return SignalTrading(
                direction=DirectionSignal.SHORT,
                valide=False,
                zone_reference=None,
                raison_rejet=f"Prix M15 {prix_actuel:.2f} pas dans la zone "
                             f"[{zone_cible.prix_bas:.2f}–{zone_cible.prix_haut:.2f}]",
            )

        rsi_actuel = float(rsi_m15.iloc[-1])
        if rsi_actuel <= CONFIG.RSI_SEUIL_SHORT:
            return SignalTrading(
                direction=DirectionSignal.SHORT,
                valide=False,
                zone_reference=None,
                raison_rejet=f"RSI M15 trop bas: {rsi_actuel:.1f} ≤ {CONFIG.RSI_SEUIL_SHORT}",
            )
        score += 20

        # Bougie de rejet baissière sur M15
        idx_m15 = len(df_m15) - 2
        if not Indicateurs.bougie_rejet_baissiere(df_m15, idx_m15):
            return SignalTrading(
                direction=DirectionSignal.SHORT,
                valide=False,
                zone_reference=None,
                raison_rejet="Pas de bougie de rejet baissière sur M15",
            )
        score += 15

        # ── Condition 4 : Déclencheur M5 ──────────────────────────────────
        idx_m5 = len(df_m5) - 2
        low_rejet_m15 = df_m15["low"].iloc[idx_m15]
        bougie_m5 = df_m5.iloc[idx_m5]

        if bougie_m5["close"] >= low_rejet_m15:
            return SignalTrading(
                direction=DirectionSignal.SHORT,
                valide=False,
                zone_reference=None,
                raison_rejet="M5 ne casse pas le low de la bougie de rejet M15",
            )

        if not Indicateurs.volume_superieur_moyenne(df_m5, idx_m5):
            return SignalTrading(
                direction=DirectionSignal.SHORT,
                valide=False,
                zone_reference=None,
                raison_rejet="Volume M5 insuffisant",
            )
        score += 10

        return SignalTrading(
            direction=DirectionSignal.SHORT,
            valide=True,
            zone_reference=zone_cible,
            prix_entree_suggere=float(df_m5["close"].iloc[-1]),
            atr_m15=atr_m15,
            score_confiance=score,
        )

    # ── Vérification invalidation position ouverte ────────────────────────

    def position_invalidee(
        self,
        df_h4: pd.DataFrame,
        df_m15: pd.DataFrame,
        direction: str,
        zone_reference,  # OBMultiTimeframe ou ZoneOB
    ) -> Tuple[bool, str]:
        """
        Vérifie si une position ouverte doit être fermée prématurément.
        Compatible avec OBMultiTimeframe (nouveau) et ZoneOB.

        Conditions :
        1. BOS contraire sur H4
        2. Clôture M15 sous/au-dessus de la zone de référence

        Returns:
            Tuple (invalide, raison).
        """
        analyse = self.analyseur_structure.analyser(df_h4)
        close_m15 = float(df_m15["close"].iloc[-1])

        # Extraire les bornes de la zone (compatible OBMultiTimeframe et ZoneOB)
        if hasattr(zone_reference, "zone_entree_bas"):
            # Nouveau format : OBMultiTimeframe
            zone_bas = zone_reference.zone_entree_bas
            zone_haut = zone_reference.zone_entree_haut
        elif hasattr(zone_reference, "prix_bas"):
            # Ancien format : ZoneInstitutionnelle / ZoneOB
            zone_bas = zone_reference.prix_bas
            zone_haut = zone_reference.prix_haut
        elif hasattr(zone_reference, "zone_bas"):
            zone_bas = zone_reference.zone_bas
            zone_haut = zone_reference.zone_haut
        else:
            return False, ""

        if direction == "LONG":
            if analyse.derniere_cassure and analyse.derniere_cassure.type in (
                TypeBOS.BOS_BAISSIER, TypeBOS.CHOCH_BAISSIER
            ):
                return True, "BOS baissier H4 détecté — thèse invalidée"
            if close_m15 < zone_bas:
                return True, f"Clôture M15 ({close_m15:.2f}) sous l'OB ({zone_bas:.2f})"
        else:  # SHORT
            if analyse.derniere_cassure and analyse.derniere_cassure.type in (
                TypeBOS.BOS_HAUSSIER, TypeBOS.CHOCH_HAUSSIER
            ):
                return True, "BOS haussier H4 détecté — thèse invalidée"
            if close_m15 > zone_haut:
                return True, f"Clôture M15 ({close_m15:.2f}) au-dessus de l'OB ({zone_haut:.2f})"

        return False, ""
