"""
indicators.py — Calcul des indicateurs techniques via pandas-ta.
Centralise tous les indicateurs utilisés par la stratégie SMC.
"""

from typing import Optional, Tuple
import pandas as pd
import numpy as np
from loguru import logger

# Calculs ATR et RSI implémentés nativement (compatible Python 3.9+)
# Évite la dépendance à pandas-ta qui requiert Python >=3.12

from config import CONFIG


class Indicateurs:
    """Calcule et expose les indicateurs techniques nécessaires à la stratégie."""

    @staticmethod
    def atr(df: pd.DataFrame, periode: int = CONFIG.ATR_PERIODE) -> pd.Series:
        """
        Calcule l'Average True Range (Wilder smoothing).

        Args:
            df: DataFrame OHLCV.
            periode: Période de l'ATR.

        Returns:
            Série ATR.
        """
        high = df["high"]
        low = df["low"]
        close_prev = df["close"].shift(1)
        tr = pd.concat([
            high - low,
            (high - close_prev).abs(),
            (low - close_prev).abs(),
        ], axis=1).max(axis=1)
        atr = tr.ewm(alpha=1 / periode, min_periods=periode, adjust=False).mean()
        return atr.fillna(0)

    @staticmethod
    def rsi(df: pd.DataFrame, periode: int = CONFIG.RSI_PERIODE) -> pd.Series:
        """
        Calcule le RSI (Relative Strength Index) via Wilder smoothing.

        Args:
            df: DataFrame OHLCV.
            periode: Période du RSI.

        Returns:
            Série RSI (0–100).
        """
        delta = df["close"].diff()
        gain = delta.clip(lower=0)
        loss = (-delta).clip(lower=0)
        avg_gain = gain.ewm(alpha=1 / periode, min_periods=periode, adjust=False).mean()
        avg_loss = loss.ewm(alpha=1 / periode, min_periods=periode, adjust=False).mean()
        rs = avg_gain / avg_loss.replace(0, 1e-10)
        rsi = 100 - (100 / (1 + rs))
        return rsi.fillna(50)

    @staticmethod
    def ema(df: pd.DataFrame, periode: int) -> pd.Series:
        """
        Calcule une Moyenne Mobile Exponentielle.

        Args:
            df: DataFrame OHLCV.
            periode: Période de l'EMA.

        Returns:
            Série EMA.
        """
        return df["close"].ewm(span=periode, adjust=False).mean().fillna(df["close"])

    @staticmethod
    def volume_moyen(df: pd.DataFrame, periodes: int = CONFIG.VOLUME_MOYENNE_PERIODES) -> pd.Series:
        """
        Calcule la moyenne mobile simple du volume.

        Args:
            df: DataFrame OHLCV.
            periodes: Nombre de bougies pour la moyenne.

        Returns:
            Série de volume moyen.
        """
        return df["volume"].rolling(window=periodes).mean().fillna(df["volume"])

    @staticmethod
    def spread_moyen(df: pd.DataFrame, periodes: int = 20) -> float:
        """
        Calcule le spread moyen des N dernières bougies.

        Args:
            df: DataFrame avec colonne 'spread'.
            periodes: Nombre de bougies.

        Returns:
            Spread moyen en points.
        """
        if "spread" not in df.columns or df["spread"].sum() == 0:
            return 0.0
        return df["spread"].tail(periodes).mean()

    @staticmethod
    def bougie_rejet_haussiere(df: pd.DataFrame, index: int, seuil_mèche: float = 2.0) -> bool:
        """
        Détecte une bougie de rejet haussière (hammer, pin bar, engulfing bull).

        Une bougie de rejet haussière a :
        - Une mèche basse ≥ 2× le corps
        - OU c'est un engulfing haussier (close > open_precedent AND open < close_precedent)

        Args:
            df: DataFrame OHLCV.
            index: Index de la bougie à analyser.
            seuil_mèche: Ratio mèche/corps minimum.

        Returns:
            True si bougie de rejet haussière détectée.
        """
        if index <= 0 or index >= len(df):
            return False

        bougie = df.iloc[index]
        corps = abs(bougie["close"] - bougie["open"])
        range_totale = bougie["high"] - bougie["low"]

        if range_totale <= 0:
            return False

        # Hammer / Pin Bar haussier
        meche_basse = min(bougie["open"], bougie["close"]) - bougie["low"]
        if corps > 0 and meche_basse / corps >= seuil_mèche and bougie["close"] >= bougie["open"]:
            return True

        # Engulfing haussier
        if index > 0:
            precedent = df.iloc[index - 1]
            if (
                bougie["close"] > bougie["open"]  # Bougie actuelle haussière
                and precedent["close"] < precedent["open"]  # Précédente baissière
                and bougie["close"] > precedent["open"]
                and bougie["open"] < precedent["close"]
            ):
                return True

        return False

    @staticmethod
    def bougie_rejet_baissiere(df: pd.DataFrame, index: int, seuil_mèche: float = 2.0) -> bool:
        """
        Détecte une bougie de rejet baissière (shooting star, pin bar, engulfing bear).

        Args:
            df: DataFrame OHLCV.
            index: Index de la bougie.
            seuil_mèche: Ratio mèche/corps minimum.

        Returns:
            True si bougie de rejet baissière détectée.
        """
        if index <= 0 or index >= len(df):
            return False

        bougie = df.iloc[index]
        corps = abs(bougie["close"] - bougie["open"])
        range_totale = bougie["high"] - bougie["low"]

        if range_totale <= 0:
            return False

        # Shooting Star / Pin Bar baissier
        meche_haute = bougie["high"] - max(bougie["open"], bougie["close"])
        if corps > 0 and meche_haute / corps >= seuil_mèche and bougie["close"] <= bougie["open"]:
            return True

        # Engulfing baissier
        if index > 0:
            precedent = df.iloc[index - 1]
            if (
                bougie["close"] < bougie["open"]  # Bougie actuelle baissière
                and precedent["close"] > precedent["open"]  # Précédente haussière
                and bougie["open"] > precedent["close"]
                and bougie["close"] < precedent["open"]
            ):
                return True

        return False

    @staticmethod
    def atr_volatilite_suffisante(df_h4: pd.DataFrame) -> bool:
        """
        Vérifie que l'ATR H4 est supérieur à CONFIG.ATR_VOLATILITE_MIN_PCT% de sa moyenne 50b.
        Évite de trader dans un marché plat/compressé.

        Args:
            df_h4: DataFrame H4.

        Returns:
            True si volatilité suffisante.
        """
        atr_serie = Indicateurs.atr(df_h4, CONFIG.ATR_PERIODE)
        if len(atr_serie) < 50:
            return True  # Pas assez de données → ne pas bloquer

        atr_actuel = atr_serie.iloc[-1]
        atr_moyen_50 = atr_serie.tail(50).mean()

        if atr_moyen_50 <= 0:
            return True

        ratio = (atr_actuel / atr_moyen_50) * 100
        suffisant = ratio >= CONFIG.ATR_VOLATILITE_MIN_PCT

        if not suffisant:
            logger.debug(
                f"Volatilité insuffisante: ATR actuel {atr_actuel:.2f} = "
                f"{ratio:.1f}% de la moyenne 50b ({atr_moyen_50:.2f})"
            )
        return suffisant

    @staticmethod
    def volume_superieur_moyenne(df: pd.DataFrame, index: int = -1) -> bool:
        """
        Vérifie si le volume de la bougie est supérieur à la moyenne des 20 dernières.

        Args:
            df: DataFrame OHLCV.
            index: Index de la bougie (défaut: dernière).

        Returns:
            True si volume > moyenne.
        """
        if len(df) < CONFIG.VOLUME_MOYENNE_PERIODES + 1:
            return True  # Pas assez de données

        vol_actuel = df["volume"].iloc[index]
        vol_moyen = df["volume"].tail(CONFIG.VOLUME_MOYENNE_PERIODES + 1).iloc[:-1].mean()

        return vol_actuel > vol_moyen

    @staticmethod
    def spread_acceptable(df: pd.DataFrame, symbole_info) -> bool:
        """
        Vérifie que le spread actuel est ≤ 3× le spread moyen des 20 dernières bougies.

        Args:
            df: DataFrame OHLCV avec colonne 'spread'.
            symbole_info: Info symbole MT5 (pour les points).

        Returns:
            True si le spread est acceptable.
        """
        if "spread" not in df.columns:
            return True

        spread_actuel = df["spread"].iloc[-1]
        spread_moyen = Indicateurs.spread_moyen(df, periodes=20)

        if spread_moyen <= 0:
            return True

        ratio = spread_actuel / spread_moyen
        acceptable = ratio <= CONFIG.SPREAD_MAX_MULTIPLICATEUR

        if not acceptable:
            logger.warning(
                f"Spread trop large: {spread_actuel:.1f} points = "
                f"{ratio:.1f}× la moyenne ({spread_moyen:.1f})"
            )
        return acceptable
