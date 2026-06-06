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
from structure_analyzer import AnalyseurStructure, AnalyseStructure, Tendance, TypeBOS
from ob_detector import DetecteurOB, ZoneInstitutionnelle, TypeZone
from indicators import Indicateurs


class DirectionSignal(Enum):
    LONG = "LONG"
    SHORT = "SHORT"
    AUCUN = "NONE"


@dataclass
class SignalTrading:
    """Résultat de l'évaluation du setup SMC."""
    direction: DirectionSignal
    valide: bool
    zone_reference: Optional[ZoneInstitutionnelle]
    prix_entree_suggere: float = 0.0
    raison_rejet: str = ""
    # Contexte pour le calcul SL/TP
    atr_m15: float = 0.0
    # Score de confiance (0–100)
    score_confiance: int = 0


class StrategieSMC:
    """
    Évalue le setup 'Breaker Block + Order Block Confluence' sur 3 timeframes.
    Conditions TOUTES requises simultanément pour valider un signal.
    """

    def __init__(self) -> None:
        self.analyseur_structure = AnalyseurStructure()
        self.detecteur_ob = DetecteurOB()

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
        # ── Filtres globaux (vérifiés avant tout calcul coûteux) ──────────
        if not est_en_session:
            return SignalTrading(
                direction=DirectionSignal.AUCUN,
                valide=False,
                zone_reference=None,
                raison_rejet="Hors session de trading",
            )

        if not Indicateurs.atr_volatilite_suffisante(df_h4):
            return SignalTrading(
                direction=DirectionSignal.AUCUN,
                valide=False,
                zone_reference=None,
                raison_rejet="Volatilité H4 insuffisante (marché plat)",
            )

        # ── Analyse de structure H4 ───────────────────────────────────────
        analyse = self.analyseur_structure.analyser(df_h4)

        # ── Détection des zones H4 ────────────────────────────────────────
        ob_actifs, breakers = self.detecteur_ob.detecter_toutes_zones(df_h4)
        toutes_zones = ob_actifs + breakers

        # ── Calcul des indicateurs M15 ────────────────────────────────────
        rsi_m15 = Indicateurs.rsi(df_m15)
        atr_m15_serie = Indicateurs.atr(df_m15)
        atr_m15 = float(atr_m15_serie.iloc[-1]) if len(atr_m15_serie) > 0 else 0.0

        prix_actuel = float(df_m5["close"].iloc[-1])

        # ── Évaluation LONG ───────────────────────────────────────────────
        signal_long = self._evaluer_long(
            analyse, toutes_zones, df_m15, df_m5, rsi_m15, atr_m15, prix_actuel
        )
        if signal_long.valide:
            logger.info(
                f"✅ SIGNAL LONG VALIDÉ | Zone: [{signal_long.zone_reference.prix_bas:.2f}"
                f"–{signal_long.zone_reference.prix_haut:.2f}] | "
                f"Confiance: {signal_long.score_confiance}/100"
            )
            return signal_long

        # ── Évaluation SHORT ──────────────────────────────────────────────
        signal_short = self._evaluer_short(
            analyse, toutes_zones, df_m15, df_m5, rsi_m15, atr_m15, prix_actuel
        )
        if signal_short.valide:
            logger.info(
                f"✅ SIGNAL SHORT VALIDÉ | Zone: [{signal_short.zone_reference.prix_bas:.2f}"
                f"–{signal_short.zone_reference.prix_haut:.2f}] | "
                f"Confiance: {signal_short.score_confiance}/100"
            )
            return signal_short

        # Loguer la raison principale du rejet
        raison = signal_long.raison_rejet or signal_short.raison_rejet or "Conditions non réunies"
        logger.debug(f"Pas de signal | {raison}")

        return SignalTrading(
            direction=DirectionSignal.AUCUN,
            valide=False,
            zone_reference=None,
            raison_rejet=raison,
        )

    # ── Évaluation LONG (5 conditions) ────────────────────────────────────

    def _evaluer_long(
        self,
        analyse: AnalyseStructure,
        zones: List[ZoneInstitutionnelle],
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
        zones: List[ZoneInstitutionnelle],
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
        zone_reference: ZoneInstitutionnelle,
    ) -> Tuple[bool, str]:
        """
        Vérifie si une position ouverte doit être fermée prématurément (invalidation).

        Conditions d'invalidation :
        1. BOS contraire sur H4
        2. Clôture M15 sous l'OB de référence (LONG) ou au-dessus (SHORT)

        Args:
            df_h4: DataFrame H4 actuel.
            df_m15: DataFrame M15 actuel.
            direction: Direction de la position ("LONG" ou "SHORT").
            zone_reference: Zone OB/BB de référence de l'entrée.

        Returns:
            Tuple (invalide, raison).
        """
        analyse = self.analyseur_structure.analyser(df_h4)
        close_m15 = float(df_m15["close"].iloc[-1])

        if direction == "LONG":
            # BOS baissier sur H4 = invalidation
            if analyse.derniere_cassure and analyse.derniere_cassure.type in (
                TypeBOS.BOS_BAISSIER, TypeBOS.CHOCH_BAISSIER
            ):
                return True, "BOS baissier H4 détecté — thèse invalidée"

            # Clôture M15 sous l'OB
            if close_m15 < zone_reference.prix_bas:
                return True, f"Clôture M15 ({close_m15:.2f}) sous l'OB ({zone_reference.prix_bas:.2f})"

        else:  # SHORT
            if analyse.derniere_cassure and analyse.derniere_cassure.type in (
                TypeBOS.BOS_HAUSSIER, TypeBOS.CHOCH_HAUSSIER
            ):
                return True, "BOS haussier H4 détecté — thèse invalidée"

            if close_m15 > zone_reference.prix_haut:
                return True, f"Clôture M15 ({close_m15:.2f}) au-dessus de l'OB ({zone_reference.prix_haut:.2f})"

        return False, ""
