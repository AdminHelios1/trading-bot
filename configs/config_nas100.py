"""
configs/config_nas100.py — Configuration spécifique NAS100 (NASDAQ 100).

Setup : SMC Order Block + Killzones ICT.
Sessions : NY uniquement (indice américain) avec fenêtres Killzone précises.
"""

from dataclasses import dataclass, field
from typing import List

from configs.config_base import ConfigBase, _MT5_H4, _MT5_H1, _MT5_M15, _MT5_M5


@dataclass
class ConfigNAS100(ConfigBase):
    """Configuration complète pour le trading du NASDAQ 100 (NAS100)."""

    # ── Identification ────────────────────────────────────────────────────
    SYMBOLE: str = "NAS100"
    NOM_AFFICHAGE: str = "NASDAQ 100 (NAS100)"
    TYPE_STRATEGIE: str = "SMC_KILLZONES_ICT"

    # ── Timeframes ────────────────────────────────────────────────────────
    TIMEFRAME_HTF: int = _MT5_H4
    TIMEFRAME_MTF: int = _MT5_H1
    TIMEFRAME_LTF: int = _MT5_M15
    TIMEFRAME_ENTREE: int = _MT5_M5

    # ── Sessions — NY uniquement (indice américain) ────────────────────────
    SESSIONS: List[dict] = field(default_factory=lambda: [
        {"name": "Pre_Market", "open": 13, "close": 14},
        {"name": "New_York",   "open": 14, "close": 21},
    ])

    # ── Killzones ICT — fenêtres haute probabilité ────────────────────────
    KILLZONES_ENABLED: bool = True
    KILLZONES: List[dict] = field(default_factory=lambda: [
        {
            "name": "NY_Open_KZ",
            "open": 13, "minute_open": 30,
            "close": 16, "minute_close": 0,
        },
        {
            "name": "NY_Lunch_KZ",
            "open": 16, "minute_open": 0,
            "close": 17, "minute_close": 0,
        },
    ])

    # ── Spread — NAS100 naturellement plus large que l'Or ─────────────────
    SPREAD_HARD_CAP_POINTS: float = 15.0
    SPREAD_DYNAMIC_MULTIPLIER: float = 2.0

    # ── Displacement — légèrement moins strict sur les indices ─────────────
    DISPLACEMENT_MIN_BODY_ATR_RATIO: float = 1.3
    DISPLACEMENT_MIN_BODY_RANGE_PCT: float = 55.0

    # ── Setup flags ────────────────────────────────────────────────────────
    FVG_PRIORITY: bool = False
    LIQUIDITY_SWEEP_ENABLED: bool = False

    # ── News critiques NAS100 ─────────────────────────────────────────────
    NEWS_CRITIQUES: List[str] = field(default_factory=lambda: [
        "Fed Interest Rate Decision",
        "Non-Farm Payrolls",
        "CPI m/m",
        "GDP q/q",
    ])
    NEWS_BLOCK_AVANT_MIN: int = 30
    NEWS_BLOCK_APRES_MIN: int = 60

    # ── Paramètres spécifiques NAS100 ─────────────────────────────────────
    # Saisons de résultats trimestriels (mois) — risque réduit
    EARNINGS_SEASON_MONTHS: List[int] = field(
        default_factory=lambda: [1, 4, 7, 10]
    )
    REDUCE_RISK_EARNINGS: bool = True    # Risque → 0.5% pendant earnings

    # Corrélation avec SP500 — NAS100 et SP500 ne peuvent pas être ouverts ensemble
    SYMBOLES_CORRELES: List[str] = field(default_factory=lambda: ["US500"])
    COEFFICIENT_CORRELATION: float = 0.95

    # ── Magic number unique par actif ──────────────────────────────────────
    MAGIC_NUMBER: int = 20250201
    ADDON_MAGIC_NUMBER: int = 20250202
