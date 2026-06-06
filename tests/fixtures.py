"""
fixtures.py — Données OHLCV fictives réutilisables dans tous les tests.
Génère des scénarios de marché contrôlés pour tester la logique du bot.
"""

import pandas as pd
import numpy as np
from datetime import datetime, timedelta, timezone


def _timestamps(n: int, freq_minutes: int = 240) -> pd.DatetimeIndex:
    """Génère N timestamps UTC espacés de freq_minutes minutes."""
    debut = datetime(2024, 1, 1, 8, 0, tzinfo=timezone.utc)
    return pd.DatetimeIndex([debut + timedelta(minutes=i * freq_minutes) for i in range(n)])


def creer_df_tendance_haussiere(n: int = 100, prix_depart: float = 2000.0) -> pd.DataFrame:
    """
    Crée un DataFrame H4 avec une tendance clairement haussière.
    Série de Higher Highs et Higher Lows.
    """
    np.random.seed(42)
    prix = prix_depart
    opens, highs, lows, closes, volumes, spreads = [], [], [], [], [], []

    for i in range(n):
        # Tendance haussière : dérive positive + noise
        variation = np.random.normal(0.3, 2.0)
        ouverture = prix
        fermeture = prix + variation
        high = max(ouverture, fermeture) + abs(np.random.normal(0, 1.0))
        low = min(ouverture, fermeture) - abs(np.random.normal(0, 1.0))

        opens.append(round(ouverture, 3))
        highs.append(round(high, 3))
        lows.append(round(low, 3))
        closes.append(round(fermeture, 3))
        volumes.append(np.random.randint(500, 2000))
        spreads.append(2)
        prix = fermeture

    return pd.DataFrame(
        {"open": opens, "high": highs, "low": lows, "close": closes,
         "volume": volumes, "spread": spreads},
        index=_timestamps(n, freq_minutes=240)
    )


def creer_df_tendance_baissiere(n: int = 100, prix_depart: float = 2100.0) -> pd.DataFrame:
    """Crée un DataFrame H4 avec une tendance clairement baissière."""
    np.random.seed(123)
    prix = prix_depart
    opens, highs, lows, closes, volumes, spreads = [], [], [], [], [], []

    for i in range(n):
        variation = np.random.normal(-0.3, 2.0)
        ouverture = prix
        fermeture = prix + variation
        high = max(ouverture, fermeture) + abs(np.random.normal(0, 1.0))
        low = min(ouverture, fermeture) - abs(np.random.normal(0, 1.0))

        opens.append(round(ouverture, 3))
        highs.append(round(high, 3))
        lows.append(round(low, 3))
        closes.append(round(fermeture, 3))
        volumes.append(np.random.randint(500, 2000))
        spreads.append(2)
        prix = fermeture

    return pd.DataFrame(
        {"open": opens, "high": highs, "low": lows, "close": closes,
         "volume": volumes, "spread": spreads},
        index=_timestamps(n, freq_minutes=240)
    )


def creer_df_avec_order_block_haussier(n: int = 80) -> pd.DataFrame:
    """
    Crée un DataFrame avec un Order Block haussier clairement identifiable.
    Structure : bougies haussières → bougie baissière → mouvement impulsif haussier avec FVG.
    """
    df = creer_df_tendance_haussiere(n, prix_depart=2000.0)
    valeurs = df.values.copy()

    # Insérer un OB haussier au milieu : bougie baissière avant un mouvement impulsif
    pivot = n // 2

    # Bougie baissière (futur OB)
    ref = valeurs[pivot - 1, 3]  # close précédent
    valeurs[pivot, 0] = ref + 3       # open
    valeurs[pivot, 3] = ref - 2       # close < open → baissière
    valeurs[pivot, 1] = ref + 4       # high
    valeurs[pivot, 2] = ref - 3       # low

    # Bougie impulsive haussière après
    valeurs[pivot + 1, 0] = ref - 2
    valeurs[pivot + 1, 3] = ref + 15   # Grande bougie haussière
    valeurs[pivot + 1, 1] = ref + 16
    valeurs[pivot + 1, 2] = ref - 1

    # FVG : low[pivot+2] > high[pivot] pour créer l'imbalance
    valeurs[pivot + 2, 2] = valeurs[pivot, 1] + 5  # low > high précédent
    valeurs[pivot + 2, 3] = valeurs[pivot, 1] + 8
    valeurs[pivot + 2, 0] = valeurs[pivot, 1] + 5
    valeurs[pivot + 2, 1] = valeurs[pivot, 1] + 10

    # S'assurer que le prix ne touche plus la zone OB après
    # (éviter que les bougies suivantes fassent monter nb_touches > max)
    for k in range(pivot + 3, min(pivot + 20, n)):
        valeurs[k, 0] = valeurs[k, 0] + 20
        valeurs[k, 1] = valeurs[k, 1] + 20
        valeurs[k, 2] = valeurs[k, 2] + 20
        valeurs[k, 3] = valeurs[k, 3] + 20

    df_result = pd.DataFrame(
        valeurs,
        columns=["open", "high", "low", "close", "volume", "spread"],
        index=df.index
    )
    return df_result.round(3)


def creer_df_m15_avec_rejet_haussier(n: int = 60, prix_base: float = 2002.0) -> pd.DataFrame:
    """
    Crée un DataFrame M15 avec une bougie de rejet haussière (hammer) en fin de série.
    """
    np.random.seed(7)
    opens, highs, lows, closes, volumes = [], [], [], [], []

    for i in range(n):
        if i == n - 2:
            # Hammer haussier : mèche basse longue, petit corps haussier
            ouv = prix_base
            fer = prix_base + 1.5     # Corps haussier
            haut = prix_base + 2.0
            bas = prix_base - 5.0    # Grande mèche basse
            vol = 1500                # Volume élevé
        else:
            ouv = prix_base + np.random.normal(0, 1)
            fer = ouv + np.random.normal(0, 2)
            haut = max(ouv, fer) + abs(np.random.normal(0, 1))
            bas = min(ouv, fer) - abs(np.random.normal(0, 1))
            vol = np.random.randint(300, 1000)

        opens.append(round(ouv, 3))
        highs.append(round(haut, 3))
        lows.append(round(bas, 3))
        closes.append(round(fer, 3))
        volumes.append(vol)

    return pd.DataFrame(
        {"open": opens, "high": highs, "low": lows, "close": closes, "volume": volumes,
         "spread": [2] * n},
        index=_timestamps(n, freq_minutes=15)
    )


def creer_df_m5_breakout(n: int = 50, prix_rejet_high: float = 2005.0) -> pd.DataFrame:
    """
    Crée un DataFrame M5 avec une bougie de breakout (close > high du rejet M15).
    Volume élevé sur la dernière bougie fermée.
    """
    np.random.seed(11)
    opens, highs, lows, closes, volumes = [], [], [], [], []

    for i in range(n):
        if i == n - 2:
            # Bougie M5 de breakout : close > prix_rejet_high
            ouv = prix_rejet_high - 0.5
            fer = prix_rejet_high + 2.0  # Clôture au-dessus du high du rejet
            haut = fer + 0.5
            bas = ouv - 0.3
            vol = 2000  # Volume supérieur à la moyenne
        else:
            ouv = prix_rejet_high - 3 + np.random.normal(0, 1)
            fer = ouv + np.random.normal(0, 1)
            haut = max(ouv, fer) + 0.5
            bas = min(ouv, fer) - 0.5
            vol = np.random.randint(200, 600)

        opens.append(round(ouv, 3))
        highs.append(round(haut, 3))
        lows.append(round(bas, 3))
        closes.append(round(fer, 3))
        volumes.append(vol)

    return pd.DataFrame(
        {"open": opens, "high": highs, "low": lows, "close": closes, "volume": volumes,
         "spread": [2] * n},
        index=_timestamps(n, freq_minutes=5)
    )


def creer_symbole_info_mock():
    """Crée un mock de mt5.symbol_info pour les tests de position sizing."""
    class SymboleInfoMock:
        trade_tick_value = 1.0   # 1$ par pip pour 1 lot (simplifié)
        volume_min = 0.01
        volume_max = 100.0
        volume_step = 0.01
        point = 0.01

    return SymboleInfoMock()
