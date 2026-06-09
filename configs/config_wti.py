"""
configs/config_wti.py — Configuration scalping WTI (Pétrole XTIUSD).

Timeframes : M5 signal, M15 confirm, H1 contexte.
Sessions : Asie partielle 02h–05h30 + London 07h15–12h30 + NY 13h45–19h30 UTC.
Confirmation Liquidity Sweep optionnelle.
En session asiatique : risque réduit + ADX minimum plus élevé.
"""

from dataclasses import dataclass, field
from typing import List

from configs.config_base import ScalpingBaseConfig, _MT5_M5, _MT5_M15, _MT5_H1


@dataclass
class WTIScalpConfig(ScalpingBaseConfig):
    """Configuration scalping complète pour le Pétrole WTI (XTIUSD)."""

    # ── Identification ────────────────────────────────────────────────────
    SYMBOLE: str = "XTIUSD"
    NOM_AFFICHAGE: str = "Pétrole WTI Scalping (XTIUSD)"
    TYPE_STRATEGIE: str = "SCALPING_HYBRID_SWEEP"

    # ── Timeframes WTI ────────────────────────────────────────────────────
    TIMEFRAME_SIGNAL: int = _MT5_M5
    TIMEFRAME_CONFIRM: int = _MT5_M15
    TIMEFRAME_HTF: int = _MT5_H1
    LOOP_INTERVAL_SEC: int = 10

    # ── Sessions — 3 sessions dont asiatique partielle ─────────────────────
    SESSIONS: List[dict] = field(default_factory=lambda: [
        {
            "name": "Asia_WTI",
            "open": 2, "minute_open": 0,
            "close": 5, "minute_close": 30,
        },
        {
            "name": "London_WTI",
            "open": 7, "minute_open": 15,
            "close": 12, "minute_close": 30,
        },
        {
            "name": "NY_WTI",
            "open": 13, "minute_open": 45,
            "close": 19, "minute_close": 30,
        },
    ])

    # ── Session asiatique — paramètres renforcés ───────────────────────────
    ASIAN_SESSION_ENABLED: bool = True
    ASIAN_SESSION_DEBUT: int = 2
    ASIAN_SESSION_FIN: int = 6           # 06h UTC (englobant jusqu'après la fin)
    ASIAN_RISK_MULTIPLIER: float = 0.5   # 0.25% la nuit (0.5 × 0.5%)
    ASIAN_OB_SCORE_MIN: int = 0
    ASIAN_ADX_MIN: float = 25.0          # Plus strict la nuit
    ASIAN_SPREAD_CAP: float = 25.0       # Spread plus tolérant la nuit

    # ── Confirmation Liquidity Sweep (optionnel) ───────────────────────────
    USE_SWEEP_CONFIRMATION: bool = True
    SWEEP_MIN_WICK_ATR: float = 0.6      # Mèche min = 0.6× ATR
    SWEEP_MAX_BODY_RATIO: float = 0.45   # Corps max 45% du range
    SWEEP_LOOKBACK_CANDLES: int = 20

    # ── Spread WTI ────────────────────────────────────────────────────────
    SPREAD_HARD_CAP_POINTS: float = 15.0
    SPREAD_DYNAMIC_MULTIPLIER: float = 2.0

    # ── SL/TP WTI ─────────────────────────────────────────────────────────
    SL_ATR_MULT: float = 1.5
    RR_TP: float = 2.0

    # ── News WTI — EIA pétrole critique ───────────────────────────────────
    NEWS_CRITIQUES: List[str] = field(default_factory=lambda: [
        "EIA Crude Oil Inventories",
        "API Weekly Crude Oil Stock",
        "OPEC Meeting",
        "Non-Farm Payrolls",
        "Fed Interest Rate Decision",
    ])
    NEWS_BLOCK_AVANT_MIN: int = 30
    NEWS_BLOCK_APRES_MIN: int = 45

    # ── Précision prix WTI ────────────────────────────────────────────────
    PRICE_DIGITS: int = 2

    # ── Magic number ──────────────────────────────────────────────────────
    MAGIC_NUMBER: int = 20250401
    ADDON_MAGIC_NUMBER: int = 20250402


# Alias — maintenu pour compatibilité
ConfigWTI = WTIScalpConfig
