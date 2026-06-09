"""
configs/config_xauusd.py — Configuration scalping XAUUSD (Or).

Timeframes : M5 signal, M15 confirm, H1 contexte.
Sessions : London 07h15–12h30 UTC + NY 13h45–19h30 UTC.
Éviter les 15 premières minutes de chaque session (spread élevé).
"""

from dataclasses import dataclass, field
from typing import List

from configs.config_base import ScalpingBaseConfig, _MT5_M5, _MT5_M15, _MT5_H1


@dataclass
class XAUUSDScalpConfig(ScalpingBaseConfig):
    """Configuration scalping complète pour l'Or (XAUUSD)."""

    # ── Identification ────────────────────────────────────────────────────
    SYMBOLE: str = "XAUUSD"
    NOM_AFFICHAGE: str = "Or Scalping (XAUUSD)"
    TYPE_STRATEGIE: str = "SCALPING_HYBRID"

    # ── Timeframes Or — M5 signal ─────────────────────────────────────────
    TIMEFRAME_SIGNAL: int = _MT5_M5
    TIMEFRAME_CONFIRM: int = _MT5_M15
    TIMEFRAME_HTF: int = _MT5_H1
    LOOP_INTERVAL_SEC: int = 10

    # ── Sessions — éviter 15 premières minutes (spread élevé) ─────────────
    SESSIONS: List[dict] = field(default_factory=lambda: [
        {
            "name": "London_Scalp",
            "open": 7, "minute_open": 15,
            "close": 12, "minute_close": 30,
        },
        {
            "name": "NewYork_Scalp",
            "open": 13, "minute_open": 45,
            "close": 19, "minute_close": 30,
        },
    ])

    # Session asiatique — uniquement pour le Daily Bias
    ASIAN_SESSION_ENABLED: bool = True
    SESSION_ASIATIQUE_DEBUT: int = 0
    SESSION_ASIATIQUE_FIN: int = 7

    # ── Spread plus strict en scalping ────────────────────────────────────
    SPREAD_HARD_CAP_POINTS: float = 25.0
    SPREAD_DYNAMIC_MULTIPLIER: float = 2.0

    # ── SL/TP Or ──────────────────────────────────────────────────────────
    SL_ATR_MULT: float = 1.5
    RR_TP: float = 2.0

    # ── RSI — Or suit bien la tendance ────────────────────────────────────
    RSI_BULL_MIN: int = 45
    RSI_BULL_MAX: int = 68
    RSI_BEAR_MIN: int = 32
    RSI_BEAR_MAX: int = 55

    # ── ADX Or — tendances claires ────────────────────────────────────────
    ADX_MIN: float = 20.0

    # ── News Or — très réactif (45min/90min) ──────────────────────────────
    NEWS_CRITIQUES: List[str] = field(default_factory=lambda: [
        "Non-Farm Payrolls",
        "Fed Interest Rate Decision",
        "FOMC Statement",
        "CPI m/m",
        "Fed Chair Speech",
        "Jackson Hole",
    ])
    NEWS_BLOCK_AVANT_MIN: int = 45
    NEWS_BLOCK_APRES_MIN: int = 90

    # ── Précision prix Or ─────────────────────────────────────────────────
    PRICE_DIGITS: int = 2

    # ── Magic number unique ────────────────────────────────────────────────
    MAGIC_NUMBER: int = 20250101
    ADDON_MAGIC_NUMBER: int = 20250102


# Alias — maintenu pour compatibilité avec les modules existants
ConfigXAUUSD = XAUUSDScalpConfig
