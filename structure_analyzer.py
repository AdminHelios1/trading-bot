"""
structure_analyzer.py — Détection de la structure de marché SMC.
Identifie les swings H/L, BOS (Break of Structure) et CHoCH (Change of Character).
"""

from dataclasses import dataclass
from enum import Enum
from typing import List, Optional, Tuple
import pandas as pd
import numpy as np
from loguru import logger

from config import CONFIG


class Tendance(Enum):
    """Tendance de marché identifiée sur la structure."""
    HAUSSIERE = "BULLISH"
    BAISSIERE = "BEARISH"
    NEUTRE = "NEUTRAL"


class TypeBOS(Enum):
    """Type de cassure de structure."""
    BOS_HAUSSIER = "BOS_BULL"   # Casse le dernier swing High → continuation haussière
    BOS_BAISSIER = "BOS_BEAR"   # Casse le dernier swing Low → continuation baissière
    CHOCH_HAUSSIER = "CHOCH_BULL"  # Premier BOS haussier dans une tendance baissière
    CHOCH_BAISSIER = "CHOCH_BEAR"  # Premier BOS baissier dans une tendance haussière


@dataclass
class SwingPoint:
    """Un point de swing identifié sur le graphique."""
    index: int
    prix: float
    timestamp: pd.Timestamp
    type: str  # "HIGH" ou "LOW"
    est_significatif: bool = True


@dataclass
class CassureStructure:
    """Une cassure de structure (BOS ou CHoCH)."""
    type: TypeBOS
    prix_cassure: float
    timestamp: pd.Timestamp
    index_bougie: int
    swing_reference: SwingPoint


@dataclass
class AnalyseStructure:
    """Résultat complet de l'analyse de structure."""
    tendance: Tendance
    swings_hauts: List[SwingPoint]
    swings_bas: List[SwingPoint]
    derniere_cassure: Optional[CassureStructure]
    choch_recent: Optional[CassureStructure]
    dernier_swing_haut: Optional[SwingPoint]
    dernier_swing_bas: Optional[SwingPoint]
    # Pour identifier la zone discount/premium
    dernier_leg_haut: float = 0.0
    dernier_leg_bas: float = 0.0


class AnalyseurStructure:
    """Analyse la structure de marché sur les données H4."""

    def __init__(self) -> None:
        self.min_bougies_autour: int = CONFIG.SWING_MIN_BOUGIES_AUTOUR
        self.choch_lookback: int = CONFIG.CHOCH_LOOKBACK_BOUGIES

    def detecter_swings(
        self,
        df: pd.DataFrame,
        n_bougies: int = None,
    ) -> Tuple[List[SwingPoint], List[SwingPoint]]:
        """
        Détecte les swings High et Low significatifs.
        Un swing High est valide si N bougies de chaque côté ont un High inférieur.
        Un swing Low est valide si N bougies de chaque côté ont un Low supérieur.

        Args:
            df: DataFrame OHLCV.
            n_bougies: Nombre de bougies de chaque côté (défaut: CONFIG.SWING_MIN_BOUGIES_AUTOUR).

        Returns:
            Tuple (swings_hauts, swings_bas).
        """
        n = n_bougies or self.min_bougies_autour
        swings_hauts: List[SwingPoint] = []
        swings_bas: List[SwingPoint] = []

        highs = df["high"].values
        lows = df["low"].values
        timestamps = df.index

        for i in range(n, len(df) - n):
            # Swing High : maximum local
            if all(highs[i] > highs[i - j] for j in range(1, n + 1)) and \
               all(highs[i] > highs[i + j] for j in range(1, n + 1)):
                swings_hauts.append(SwingPoint(
                    index=i,
                    prix=highs[i],
                    timestamp=timestamps[i],
                    type="HIGH",
                ))

            # Swing Low : minimum local
            if all(lows[i] < lows[i - j] for j in range(1, n + 1)) and \
               all(lows[i] < lows[i + j] for j in range(1, n + 1)):
                swings_bas.append(SwingPoint(
                    index=i,
                    prix=lows[i],
                    timestamp=timestamps[i],
                    type="LOW",
                ))

        return swings_hauts, swings_bas

    def detecter_bos_choch(
        self,
        df: pd.DataFrame,
        swings_hauts: List[SwingPoint],
        swings_bas: List[SwingPoint],
    ) -> List[CassureStructure]:
        """
        Détecte les BOS et CHoCH en cherchant les clôtures au-delà des swings.

        Args:
            df: DataFrame OHLCV H4.
            swings_hauts: Liste des swings hauts.
            swings_bas: Liste des swings bas.

        Returns:
            Liste des cassures de structure dans l'ordre chronologique.
        """
        cassures: List[CassureStructure] = []
        closes = df["close"].values
        timestamps = df.index

        # Pour chaque bougie, vérifier si elle casse un swing précédent
        for i in range(1, len(df)):
            # Vérifier cassure haussière (BOS haussier) : close > dernier swing haut
            for swing in reversed(swings_hauts):
                if swing.index >= i:
                    continue
                if closes[i] > swing.prix:
                    # Déterminer si c'est un BOS ou CHoCH
                    # CHoCH si la tendance précédente était BAISSIÈRE
                    tendance_precedente = self._tendance_avant(
                        cassures, TypeBOS.BOS_BAISSIER, TypeBOS.CHOCH_BAISSIER
                    )
                    type_cassure = (
                        TypeBOS.CHOCH_HAUSSIER
                        if tendance_precedente == Tendance.BAISSIERE
                        else TypeBOS.BOS_HAUSSIER
                    )
                    cassures.append(CassureStructure(
                        type=type_cassure,
                        prix_cassure=closes[i],
                        timestamp=timestamps[i],
                        index_bougie=i,
                        swing_reference=swing,
                    ))
                    break  # Une seule cassure par bougie

            # Vérifier cassure baissière : close < dernier swing bas
            for swing in reversed(swings_bas):
                if swing.index >= i:
                    continue
                if closes[i] < swing.prix:
                    tendance_precedente = self._tendance_avant(
                        cassures, TypeBOS.BOS_HAUSSIER, TypeBOS.CHOCH_HAUSSIER
                    )
                    type_cassure = (
                        TypeBOS.CHOCH_BAISSIER
                        if tendance_precedente == Tendance.HAUSSIERE
                        else TypeBOS.BOS_BAISSIER
                    )
                    cassures.append(CassureStructure(
                        type=type_cassure,
                        prix_cassure=closes[i],
                        timestamp=timestamps[i],
                        index_bougie=i,
                        swing_reference=swing,
                    ))
                    break

        return cassures

    def _tendance_avant(
        self,
        cassures: List[CassureStructure],
        type_bull: TypeBOS,
        type_choch_bull: TypeBOS,
    ) -> Tendance:
        """Détermine la tendance dominante avant la cassure en cours."""
        if not cassures:
            return Tendance.NEUTRE
        derniere = cassures[-1]
        if derniere.type in (type_bull, type_choch_bull):
            return Tendance.HAUSSIERE
        return Tendance.BAISSIERE

    def identifier_tendance(
        self,
        swings_hauts: List[SwingPoint],
        swings_bas: List[SwingPoint],
        cassures: List[CassureStructure],
    ) -> Tendance:
        """
        Détermine la tendance dominante actuelle basée sur les cassures récentes.

        Logique :
        - BULLISH si la dernière cassure significative est un BOS haussier ou CHoCH haussier
        - BEARISH si inverse
        - Confirmation via séquence HH/HL ou LH/LL
        """
        if not cassures:
            return self._tendance_par_swings(swings_hauts, swings_bas)

        # Regarder les 3 dernières cassures
        recentes = cassures[-3:]
        votes_bull = sum(
            1 for c in recentes
            if c.type in (TypeBOS.BOS_HAUSSIER, TypeBOS.CHOCH_HAUSSIER)
        )
        votes_bear = len(recentes) - votes_bull

        if votes_bull > votes_bear:
            return Tendance.HAUSSIERE
        elif votes_bear > votes_bull:
            return Tendance.BAISSIERE
        return Tendance.NEUTRE

    def _tendance_par_swings(
        self,
        swings_hauts: List[SwingPoint],
        swings_bas: List[SwingPoint],
    ) -> Tendance:
        """Fallback : tendance via séquence de HH/HL ou LH/LL."""
        if len(swings_hauts) >= 2:
            if swings_hauts[-1].prix > swings_hauts[-2].prix:
                return Tendance.HAUSSIERE
        if len(swings_bas) >= 2:
            if swings_bas[-1].prix < swings_bas[-2].prix:
                return Tendance.BAISSIERE
        return Tendance.NEUTRE

    def analyser(self, df: pd.DataFrame) -> AnalyseStructure:
        """
        Point d'entrée principal : analyse complète de la structure sur df.

        Args:
            df: DataFrame OHLCV H4 (minimum 50 bougies recommandé).

        Returns:
            AnalyseStructure avec tous les éléments de contexte.
        """
        swings_hauts, swings_bas = self.detecter_swings(df)
        cassures = self.detecter_bos_choch(df, swings_hauts, swings_bas)
        tendance = self.identifier_tendance(swings_hauts, swings_bas, cassures)

        # Dernière cassure et CHoCH récent
        derniere_cassure = cassures[-1] if cassures else None

        choch_recent = None
        lookback_idx = len(df) - self.choch_lookback
        for c in reversed(cassures):
            if (
                c.type in (TypeBOS.CHOCH_HAUSSIER, TypeBOS.CHOCH_BAISSIER)
                and c.index_bougie >= lookback_idx
            ):
                choch_recent = c
                break

        # Dernier swing H/L
        dernier_swing_haut = swings_hauts[-1] if swings_hauts else None
        dernier_swing_bas = swings_bas[-1] if swings_bas else None

        # Bornes du dernier leg pour discount/premium
        leg_haut = max((s.prix for s in swings_hauts[-3:]), default=0.0)
        leg_bas = min((s.prix for s in swings_bas[-3:]), default=0.0)

        logger.debug(
            f"Structure H4 | Tendance: {tendance.value} | "
            f"Dernier BOS: {derniere_cassure.type.value if derniere_cassure else 'aucun'} | "
            f"CHoCH récent: {'oui' if choch_recent else 'non'}"
        )

        return AnalyseStructure(
            tendance=tendance,
            swings_hauts=swings_hauts,
            swings_bas=swings_bas,
            derniere_cassure=derniere_cassure,
            choch_recent=choch_recent,
            dernier_swing_haut=dernier_swing_haut,
            dernier_swing_bas=dernier_swing_bas,
            dernier_leg_haut=leg_haut,
            dernier_leg_bas=leg_bas,
        )

    def est_dans_discount(self, prix: float, analyse: AnalyseStructure) -> bool:
        """
        Vérifie si le prix est dans la zone de discount (< 50% du dernier leg).
        Utilisé pour valider les entrées LONG.
        """
        milieu = (analyse.dernier_leg_haut + analyse.dernier_leg_bas) / 2
        return prix < milieu and milieu > 0

    def est_dans_premium(self, prix: float, analyse: AnalyseStructure) -> bool:
        """
        Vérifie si le prix est dans la zone de premium (> 50% du dernier leg).
        Utilisé pour valider les entrées SHORT.
        """
        milieu = (analyse.dernier_leg_haut + analyse.dernier_leg_bas) / 2
        return prix > milieu and milieu > 0
