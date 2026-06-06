"""
config.py — Paramètres centralisés du bot de trading SMC XAUUSD.
Aucun "magic number" ne doit apparaître ailleurs dans le code.
"""

from dataclasses import dataclass, field
from typing import List

# Constantes MT5 définies en dur pour éviter l'import au niveau module
# (MT5 n'est disponible que sur Windows — ces valeurs sont fixes dans l'API)
_MT5_TIMEFRAME_M5  = 5
_MT5_TIMEFRAME_M15 = 15
_MT5_TIMEFRAME_H4  = 16388  # Valeur interne MT5 pour H4


@dataclass
class Config:
    """Configuration complète du bot. Modifier ici uniquement."""

    # ── Actif et timeframes ────────────────────────────────────────────────
    SYMBOLE: str = "XAUUSD"
    TIMEFRAME_HTF: int = _MT5_TIMEFRAME_H4    # Structure de marché
    TIMEFRAME_LTF: int = _MT5_TIMEFRAME_M15   # Confirmation d'entrée
    TIMEFRAME_ENTREE: int = _MT5_TIMEFRAME_M5  # Déclencheur final

    # ── Nombre de bougies à charger ────────────────────────────────────────
    BOUGIES_HTF: int = 200   # H4 : ~33 jours
    BOUGIES_LTF: int = 150   # M15 : ~37h
    BOUGIES_ENTREE: int = 100  # M5 : ~8h

    # ── Gestion du risque ──────────────────────────────────────────────────
    RISQUE_PAR_TRADE_PCT: float = 1.0          # % du capital par trade (max 2%)
    RR_MINIMUM: float = 2.0                     # R:R minimum pour valider
    RR_CIBLE: float = 2.5                       # R:R objectif principal
    MAX_DRAWDOWN_JOURNALIER_PCT: float = 3.0    # Arrêt journalier si dépassé
    MAX_DRAWDOWN_TOTAL_PCT: float = 10.0        # Arrêt total si dépassé
    MAX_POSITIONS_OUVERTES: int = 1             # Une seule position à la fois
    MAGIC_NUMBER: int = 20250101                # Identifiant unique des ordres du bot

    # ── Sessions de trading (heures UTC) ──────────────────────────────────
    SESSION_LONDON_OUVERTURE: int = 7     # 08h00 Paris (heure d'hiver)
    SESSION_NY_OUVERTURE: int = 13        # 14h00 Paris
    SESSION_FERMETURE: int = 20           # 21h00 Paris
    # Fenêtre interdite autour de la clôture journalière XAUUSD
    CLOTURE_JOURNALIERE_DEBUT_UTC: int = 22   # 22h00 UTC
    CLOTURE_JOURNALIERE_FIN_UTC: int = 23     # 23h59 UTC (jusqu'à minuit UTC+1)

    # ── Détection Order Block ──────────────────────────────────────────────
    OB_LOOKBACK_BOUGIES: int = 50         # Fenêtre de recherche
    OB_MIN_IMBALANCE_PCT: float = 0.3     # FVG minimum en % pour valider un OB
    OB_MAX_TOUCHES: int = 2               # Nombre max de touches avant invalidation
    SWING_MIN_BOUGIES_AUTOUR: int = 3     # Bougies de chaque côté pour valider un swing

    # ── Breaker Block ──────────────────────────────────────────────────────
    BB_LOOKBACK_BOUGIES: int = 100        # Fenêtre de recherche breaker blocks

    # ── Trailing Stop ──────────────────────────────────────────────────────
    TRAILING_ACTIVATION_RR: float = 1.0    # Activer après 1R de gain
    TRAILING_DISTANCE_ATR: float = 1.5     # Distance en ATR(14) M15

    # ── Stop Loss ──────────────────────────────────────────────────────────
    SL_MARGE_ATR: float = 0.5              # Marge sous/au-dessus de l'OB en ATR(14) M15

    # ── Filtres de qualité ─────────────────────────────────────────────────
    RSI_PERIODE: int = 14
    RSI_SEUIL_LONG: float = 45.0          # RSI M15 < seuil pour signal long
    RSI_SEUIL_SHORT: float = 55.0         # RSI M15 > seuil pour signal short
    ATR_PERIODE: int = 14
    VOLUME_MOYENNE_PERIODES: int = 20     # Périodes pour la moyenne de volume
    SPREAD_MAX_MULTIPLICATEUR: float = 3.0  # Spread max = 3× le spread moyen
    ATR_VOLATILITE_MIN_PCT: float = 50.0  # ATR H4 doit être > 50% de sa moyenne 50b

    # ── CHoCH / BOS ────────────────────────────────────────────────────────
    CHOCH_LOOKBACK_BOUGIES: int = 20      # CHoCH récent si < 20 bougies H4

    # ── Circuit breaker ────────────────────────────────────────────────────
    CIRCUIT_BREAKER_PERTES_CONSECUTIVES: int = 3  # Pause 24h après N pertes
    CIRCUIT_BREAKER_PAUSE_HEURES: int = 24

    # ── Take Profit partiels ───────────────────────────────────────────────
    TP1_FRACTION: float = 0.5             # 50% de la position fermée à TP1
    TP1_RR: float = 1.0                   # TP1 à 1R
    TP2_RR: float = 2.5                   # TP2 à 2.5R (objectif principal)
    TP3_RR: float = 3.0                   # TP3 optionnel si structure favorable

    # ── Boucle principale ──────────────────────────────────────────────────
    INTERVALLE_BOUCLE_SECONDES: int = 60   # Vérification toutes les 60s
    MAX_TENTATIVES_CONNEXION: int = 3       # Retry connexion MT5

    # ── Backtest ───────────────────────────────────────────────────────────
    BACKTEST_DATE_DEBUT: str = "2023-01-01"
    BACKTEST_DATE_FIN: str = "2024-12-31"
    BACKTEST_CAPITAL_INITIAL: float = 10_000.0
    BACKTEST_CSV_CACHE: str = "reports/cache_ohlcv_{symbole}_{tf}.csv"

    # ── Rapport ────────────────────────────────────────────────────────────
    RAPPORT_CSV: str = "reports/backtest_{symbole}_{date}.csv"
    RAPPORT_PNG: str = "reports/equity_curve_{symbole}.png"
    RAPPORT_TXT: str = "reports/summary_{symbole}.txt"

    # ── Logging ────────────────────────────────────────────────────────────
    LOG_ROTATION: str = "00:00"           # Rotation à minuit
    LOG_RETENTION: str = "30 days"
    LOG_FORMAT: str = (
        "<green>{time:YYYY-MM-DD HH:mm:ss}</green> | "
        "<level>{level: <8}</level> | "
        "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> — "
        "<level>{message}</level>"
    )


# Instance globale importée par tous les modules
CONFIG = Config()
