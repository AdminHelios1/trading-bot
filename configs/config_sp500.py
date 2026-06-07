"""
configs/config_sp500.py — Configuration spécifique SP500 (S&P 500).

Setup : SMC Order Block + Fair Value Gap prioritaire.
Sessions : New York uniquement.
"""

from dataclasses import dataclass, field
from typing import List

from configs.config_base import ConfigBase, _MT5_H4, _MT5_H1, _MT5_M15, _MT5_M5


@dataclass
class ConfigSP500(ConfigBase):
    """Configuration complète pour le trading du S&P 500 (SP500)."""

    # ── Identification ────────────────────────────────────────────────────
    SYMBOLE: str = "US500"
    NOM_AFFICHAGE: str = "S&P 500 (SP500)"
    TYPE_STRATEGIE: str = "SMC_FVG_PRIORITY"

    # ── Timeframes ────────────────────────────────────────────────────────
    TIMEFRAME_HTF: int = _MT5_H4
    TIMEFRAME_MTF: int = _MT5_H1
    TIMEFRAME_LTF: int = _MT5_M15
    TIMEFRAME_ENTREE: int = _MT5_M5

    # ── Sessions — NY uniquement ───────────────────────────────────────────
    SESSIONS: List[dict] = field(default_factory=lambda: [
        {"name": "New_York", "open": 14, "close": 21},
    ])

    # ── Spread — SP500 spread le plus serré des 4 actifs ──────────────────
    SPREAD_HARD_CAP_POINTS: float = 10.0
    SPREAD_DYNAMIC_MULTIPLIER: float = 2.0

    # ── Displacement ──────────────────────────────────────────────────────
    DISPLACEMENT_MIN_BODY_ATR_RATIO: float = 1.3
    DISPLACEMENT_MIN_BODY_RANGE_PCT: float = 55.0

    # ── Setup flags ────────────────────────────────────────────────────────
    KILLZONES_ENABLED: bool = False
    FVG_PRIORITY: bool = True           # FVG est le trigger principal
    LIQUIDITY_SWEEP_ENABLED: bool = False

    # ── Paramètres FVG spécifiques SP500 ──────────────────────────────────
    FVG_MIN_SIZE_PCT: float = 0.15       # FVG minimum 0.15% du prix
    FVG_MAX_AGE_CANDLES: int = 20        # FVG valide max 20 bougies H1
    FVG_CONFLUENCE_OB_REQUIRED: bool = True  # FVG doit être dans une zone OB

    # ── News critiques SP500 ──────────────────────────────────────────────
    NEWS_CRITIQUES: List[str] = field(default_factory=lambda: [
        "Fed Interest Rate Decision",
        "Non-Farm Payrolls",
        "CPI m/m",
        "GDP q/q",
        "ISM Manufacturing PMI",
    ])
    NEWS_BLOCK_AVANT_MIN: int = 30
    NEWS_BLOCK_APRES_MIN: int = 60

    # ── Corrélation avec NAS100 ────────────────────────────────────────────
    SYMBOLES_CORRELES: List[str] = field(default_factory=lambda: ["NAS100"])
    COEFFICIENT_CORRELATION: float = 0.95

    # ── Magic number unique par actif ──────────────────────────────────────
    MAGIC_NUMBER: int = 20250301
    ADDON_MAGIC_NUMBER: int = 20250302
