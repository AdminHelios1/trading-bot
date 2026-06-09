"""
configs/config_sp500.py — Configuration scalping SP500 (S&P 500).

Timeframes : M5 signal, M15 confirm, H1 contexte.
Sessions : NY 14h00–20h00 UTC.
Confirmation FVG optionnelle (SP500 comble ses FVG systématiquement).
"""

from dataclasses import dataclass, field
from typing import List

from configs.config_base import ScalpingBaseConfig, _MT5_M5, _MT5_M15, _MT5_H1


@dataclass
class SP500ScalpConfig(ScalpingBaseConfig):
    """Configuration scalping complète pour le S&P 500 (SP500)."""

    # ── Identification ────────────────────────────────────────────────────
    SYMBOLE: str = "US500"
    NOM_AFFICHAGE: str = "S&P 500 Scalping (SP500)"
    TYPE_STRATEGIE: str = "SCALPING_HYBRID_FVG"

    # ── Timeframes SP500 ──────────────────────────────────────────────────
    TIMEFRAME_SIGNAL: int = _MT5_M5
    TIMEFRAME_CONFIRM: int = _MT5_M15
    TIMEFRAME_HTF: int = 16388  # H4 — filtre HTF plus souple qu'H1
    LOOP_INTERVAL_SEC: int = 10

    # ── Sessions NY uniquement ────────────────────────────────────────────
    SESSIONS: List[dict] = field(default_factory=lambda: [
        {
            "name": "PreNY_Scalp",
            "open": 13, "minute_open": 0,
            "close": 14, "minute_close": 0,
        },
        {
            "name": "NY_Scalp",
            "open": 14, "minute_open": 0,
            "close": 20, "minute_close": 30,
        },
    ])

    # ── Spread SP500 — le plus serré des 4 actifs ─────────────────────────
    SPREAD_HARD_CAP_POINTS: float = 8.0
    SPREAD_DYNAMIC_MULTIPLIER: float = 2.0

    # ── Confirmation FVG (optionnel) ──────────────────────────────────────
    USE_FVG_CONFIRMATION: bool = True
    FVG_MIN_SIZE_PCT: float = 0.10       # FVG min 0.10% du prix
    FVG_MAX_AGE_CANDLES: int = 10        # FVG valide max 10 bougies (court en scalping)

    # ── SL/TP SP500 ───────────────────────────────────────────────────────
    SL_ATR_MULT: float = 1.3
    RR_TP: float = 2.0

    # ── News SP500 ────────────────────────────────────────────────────────
    NEWS_CRITIQUES: List[str] = field(default_factory=lambda: [
        "Fed Interest Rate Decision",
        "Non-Farm Payrolls",
        "CPI m/m",
        "GDP q/q",
        "ISM Manufacturing PMI",
    ])
    NEWS_BLOCK_AVANT_MIN: int = 30
    NEWS_BLOCK_APRES_MIN: int = 45

    # ── Corrélation NAS100 ────────────────────────────────────────────────
    SYMBOLES_CORRELES: List[str] = field(default_factory=lambda: ["NAS100"])
    COEFFICIENT_CORRELATION: float = 0.95

    # ── Précision prix SP500 ─────────────────────────────────────────────
    PRICE_DIGITS: int = 1

    # ── Magic number ──────────────────────────────────────────────────────
    MAGIC_NUMBER: int = 20250301
    ADDON_MAGIC_NUMBER: int = 20250302


# Alias — maintenu pour compatibilité
ConfigSP500 = SP500ScalpConfig
