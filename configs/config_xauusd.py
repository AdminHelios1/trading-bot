"""
configs/config_xauusd.py — Configuration spécifique XAUUSD (Or).

Setup : SMC Breaker Block + Order Block Confluence (setup existant).
Sessions : London (07h–13h UTC) + New York (13h–20h UTC).
"""

from dataclasses import dataclass, field
from typing import List

from configs.config_base import ConfigBase, _MT5_H4, _MT5_H1, _MT5_M15, _MT5_M5


@dataclass
class ConfigXAUUSD(ConfigBase):
    """Configuration complète pour le trading de l'Or (XAUUSD)."""

    # ── Identification ────────────────────────────────────────────────────
    SYMBOLE: str = "XAUUSD"
    NOM_AFFICHAGE: str = "Or (XAUUSD)"
    TYPE_STRATEGIE: str = "SMC_BREAKER_BLOCK"

    # ── Timeframes ────────────────────────────────────────────────────────
    TIMEFRAME_HTF: int = _MT5_H4
    TIMEFRAME_MTF: int = _MT5_H1
    TIMEFRAME_LTF: int = _MT5_M15
    TIMEFRAME_ENTREE: int = _MT5_M5

    # ── Sessions (heures UTC) ─────────────────────────────────────────────
    SESSIONS: List[dict] = field(default_factory=lambda: [
        {"name": "London",   "open": 7,  "close": 13},
        {"name": "New_York", "open": 13, "close": 20},
    ])

    # Session asiatique utilisée pour le Daily Bias (pas de trade)
    SESSION_ASIATIQUE_DEBUT: int = 0
    SESSION_ASIATIQUE_FIN: int = 7

    # ── Spread ────────────────────────────────────────────────────────────
    SPREAD_HARD_CAP_POINTS: float = 35.0
    SPREAD_DYNAMIC_MULTIPLIER: float = 2.5

    # ── Displacement — strict (Or très institutionnel) ─────────────────────
    DISPLACEMENT_MIN_BODY_ATR_RATIO: float = 1.5
    DISPLACEMENT_MIN_BODY_RANGE_PCT: float = 60.0

    # ── Setup flags ────────────────────────────────────────────────────────
    KILLZONES_ENABLED: bool = False
    FVG_PRIORITY: bool = False
    LIQUIDITY_SWEEP_ENABLED: bool = False

    # ── News critiques XAUUSD ─────────────────────────────────────────────
    NEWS_CRITIQUES: List[str] = field(default_factory=lambda: [
        "Non-Farm Payrolls",
        "Fed Interest Rate Decision",
        "FOMC Statement",
        "CPI m/m",
        "Fed Chair Speech",
        "Jackson Hole",
    ])
    NEWS_BLOCK_AVANT_MIN: int = 45    # 45min avant pour l'Or
    NEWS_BLOCK_APRES_MIN: int = 90    # 90min après pour l'Or

    # ── Paramètres Asian Session (pour le Daily Bias uniquement) ──────────
    ASIAN_SESSION_ENABLED: bool = True
    EQUAL_HIGH_LOW_TOLERANCE_PCT: float = 0.05
    BSL_SSL_POOL_MIN_TOUCHES: int = 3

    # ── Magic number unique par actif ──────────────────────────────────────
    MAGIC_NUMBER: int = 20250101
    ADDON_MAGIC_NUMBER: int = 20250102
