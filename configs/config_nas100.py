"""
configs/config_nas100.py — Configuration scalping NAS100 (NASDAQ 100).

Timeframes : M1 signal, M5 confirm, M15 contexte.
Sessions : Killzones ICT NY uniquement (NY Open 13h30–15h30, Power 15h30–17h).
EMAs : 8/13/34 (plus réactives sur M1).
"""

from dataclasses import dataclass, field
from typing import List

from configs.config_base import ScalpingBaseConfig, _MT5_M1, _MT5_M5, _MT5_M15


@dataclass
class NAS100ScalpConfig(ScalpingBaseConfig):
    """Configuration scalping complète pour le NASDAQ 100 (NAS100)."""

    # ── Identification ────────────────────────────────────────────────────
    SYMBOLE: str = "ND100m"
    NOM_AFFICHAGE: str = "NASDAQ Scalping (ND100m)"
    TYPE_STRATEGIE: str = "SCALPING_HYBRID"

    # ── Timeframes M1 — NASDAQ très liquide ───────────────────────────────
    TIMEFRAME_SIGNAL: int = _MT5_M1
    TIMEFRAME_CONFIRM: int = _MT5_M5
    TIMEFRAME_HTF: int = _MT5_M15
    LOOP_INTERVAL_SEC: int = 5           # Boucle toutes les 5s sur M1

    # ── Killzones ICT — fenêtres haute probabilité uniquement ─────────────
    KILLZONES_ENABLED: bool = True
    SESSIONS: List[dict] = field(default_factory=lambda: [
        {
            "name": "NY_Open_KZ",
            "open": 13, "minute_open": 30,
            "close": 15, "minute_close": 30,
        },
        {
            "name": "NY_Power_KZ",
            "open": 15, "minute_open": 30,
            "close": 17, "minute_close": 0,
        },
    ])

    # ── Spread NASDAQ ─────────────────────────────────────────────────────
    SPREAD_HARD_CAP_POINTS: float = 12.0
    SPREAD_DYNAMIC_MULTIPLIER: float = 2.0

    # ── EMAs plus réactives sur M1 (8/13/34 au lieu de 9/21/50) ──────────
    EMA_FAST: int = 8
    EMA_SLOW: int = 13
    EMA_TREND: int = 34
    HTF_EMA_LEN: int = 21
    EMA_MIN_SPREAD_PCT: float = 0.08    # Légèrement plus souple sur M1

    # ── RSI M1 — plus de bruit, plages plus larges ────────────────────────
    RSI_BULL_MIN: int = 40
    RSI_BULL_MAX: int = 70
    RSI_BEAR_MIN: int = 30
    RSI_BEAR_MAX: int = 60

    # ── ADX — tendances plus courtes sur M1 ───────────────────────────────
    ADX_MIN: float = 15.0

    # ── SL plus serré sur M1 ──────────────────────────────────────────────
    SL_ATR_MULT: float = 1.2
    RR_TP: float = 1.8

    # ── Risk réduit pendant earnings ──────────────────────────────────────
    RISQUE_PAR_TRADE_PCT: float = 0.5
    REDUCE_RISK_EARNINGS: bool = True
    EARNINGS_RISK_PCT: float = 0.25
    EARNINGS_SEASON_MONTHS: List[int] = field(
        default_factory=lambda: [1, 4, 7, 10]
    )

    # ── News NASDAQ ───────────────────────────────────────────────────────
    NEWS_CRITIQUES: List[str] = field(default_factory=lambda: [
        "Fed Interest Rate Decision",
        "Non-Farm Payrolls",
        "CPI m/m",
        "GDP q/q",
    ])
    NEWS_BLOCK_AVANT_MIN: int = 30
    NEWS_BLOCK_APRES_MIN: int = 45

    # ── Corrélation SP500 ─────────────────────────────────────────────────
    SYMBOLES_CORRELES: List[str] = field(default_factory=lambda: ["US500", "SP500"])
    COEFFICIENT_CORRELATION: float = 0.95

    # ── Précision prix NASDAQ ─────────────────────────────────────────────
    PRICE_DIGITS: int = 1

    # ── Magic number ──────────────────────────────────────────────────────
    MAGIC_NUMBER: int = 20250201
    ADDON_MAGIC_NUMBER: int = 20250202

    # ── Durée max plus courte sur M1 ──────────────────────────────────────
    MAX_TRADE_DURATION_BARS: int = 30   # M1 → 30 bougies = 30 minutes max


# Alias — maintenu pour compatibilité
ConfigNAS100 = NAS100ScalpConfig
