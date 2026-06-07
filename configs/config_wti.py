"""
configs/config_wti.py — Configuration spécifique WTI (Pétrole XTIUSD).

Setup : SMC + Liquidity Sweep.
Sessions : Asiatique partielle (02h–06h) + London + New York.
En session asiatique : paramètres plus stricts (risque réduit, OB min 75).
"""

from dataclasses import dataclass, field
from typing import List

from configs.config_base import ConfigBase, _MT5_H4, _MT5_H1, _MT5_M15, _MT5_M5


@dataclass
class ConfigWTI(ConfigBase):
    """Configuration complète pour le trading du Pétrole WTI (XTIUSD)."""

    # ── Identification ────────────────────────────────────────────────────
    SYMBOLE: str = "XTIUSD"
    NOM_AFFICHAGE: str = "Pétrole WTI (XTIUSD)"
    TYPE_STRATEGIE: str = "SMC_LIQUIDITY_SWEEP"

    # ── Timeframes ────────────────────────────────────────────────────────
    TIMEFRAME_HTF: int = _MT5_H4
    TIMEFRAME_MTF: int = _MT5_H1
    TIMEFRAME_LTF: int = _MT5_M15
    TIMEFRAME_ENTREE: int = _MT5_M5

    # ── Sessions — incluant session asiatique partielle ────────────────────
    SESSIONS: List[dict] = field(default_factory=lambda: [
        {"name": "Asian_Partial", "open": 2,  "close": 6},
        {"name": "London",        "open": 7,  "close": 13},
        {"name": "New_York",      "open": 13, "close": 20},
    ])

    # ── Session asiatique WTI ──────────────────────────────────────────────
    ASIAN_SESSION_ENABLED: bool = True
    ASIAN_SESSION_DEBUT: int = 2    # 02h00 UTC — ouverture marchés chinois
    ASIAN_SESSION_FIN: int = 6      # 06h00 UTC

    # Paramètres plus stricts en session asiatique
    ASIAN_RISK_MULTIPLIER: float = 0.5      # Risque → 0.5% (au lieu de 1%)
    ASIAN_OB_SCORE_MIN: int = 75            # OB score minimum plus élevé la nuit
    ASIAN_SPREAD_HARD_CAP_POINTS: float = 30.0  # Spread plus tolérant la nuit

    # ── Spread ────────────────────────────────────────────────────────────
    SPREAD_HARD_CAP_POINTS: float = 20.0
    SPREAD_DYNAMIC_MULTIPLIER: float = 2.0

    # ── Displacement ──────────────────────────────────────────────────────
    DISPLACEMENT_MIN_BODY_ATR_RATIO: float = 1.4
    DISPLACEMENT_MIN_BODY_RANGE_PCT: float = 58.0

    # ── Setup flags ────────────────────────────────────────────────────────
    KILLZONES_ENABLED: bool = False
    FVG_PRIORITY: bool = False
    LIQUIDITY_SWEEP_ENABLED: bool = True

    # ── Paramètres Liquidity Sweep ────────────────────────────────────────
    SWEEP_MIN_WICK_ATR_RATIO: float = 0.8   # Mèche min = 0.8× ATR
    SWEEP_MAX_BODY_RATIO: float = 0.40      # Corps max 40% du range
    SWEEP_LOOKBACK_CANDLES: int = 30        # Chercher niveaux sur 30 bougies H1

    # ── News critiques WTI ────────────────────────────────────────────────
    NEWS_CRITIQUES: List[str] = field(default_factory=lambda: [
        "EIA Crude Oil Inventories",
        "API Weekly Crude Oil Stock",
        "OPEC Meeting",
        "Non-Farm Payrolls",
        "Fed Interest Rate Decision",
    ])
    NEWS_BLOCK_AVANT_MIN: int = 30
    NEWS_BLOCK_APRES_MIN: int = 45    # WTI se stabilise plus vite que l'Or

    # ── Données économiques chinoises (impact sur la demande de pétrole) ──
    EVENTS_CHINE: List[str] = field(default_factory=lambda: [
        "China Manufacturing PMI",
        "China Industrial Production",
        "China GDP",
    ])
    BLOQUER_EVENTS_CHINE: bool = True

    # ── Magic number unique par actif ──────────────────────────────────────
    MAGIC_NUMBER: int = 20250401
    ADDON_MAGIC_NUMBER: int = 20250402
