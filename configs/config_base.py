"""
configs/config_base.py — Paramètres communs à tous les actifs.

ScalpingBaseConfig est la base pour le bot scalping multi-actif.
ConfigBase est maintenu pour compatibilité avec les modules existants.
"""

from dataclasses import dataclass, field
from typing import List

# ── Constantes MT5 timeframes (valeurs internes fixes de l'API) ───────────
_MT5_M1  = 1
_MT5_M5  = 5
_MT5_M15 = 15
_MT5_M30 = 30
_MT5_H1  = 16385
_MT5_H4  = 16388
_MT5_D1  = 16408


@dataclass
class ScalpingBaseConfig:
    """
    Configuration de base pour le scalping multi-actif.
    Stratégie hybride EMA 9/21/50 + RSI + ADX + Price Action.
    Chaque actif hérite de cette base et surcharge ce qui est nécessaire.
    """

    # ── Identification ────────────────────────────────────────────────────
    SYMBOLE: str = "XAUUSD"
    NOM_AFFICHAGE: str = "Actif Scalping"
    TYPE_STRATEGIE: str = "SCALPING_HYBRID"

    # ── Timeframes scalping ────────────────────────────────────────────────
    TIMEFRAME_SIGNAL: int = _MT5_M5    # Bougie de signal
    TIMEFRAME_CONFIRM: int = _MT5_M15  # Confirmation intermédiaire
    TIMEFRAME_HTF: int = _MT5_H1       # Contexte tendance
    LOOP_INTERVAL_SEC: int = 10        # Fréquence de boucle

    # ── Nombre de bougies à charger ────────────────────────────────────────
    BOUGIES_SIGNAL: int = 80
    BOUGIES_HTF: int = 50

    # ── EMAs ──────────────────────────────────────────────────────────────
    EMA_FAST: int = 9
    EMA_SLOW: int = 21
    EMA_TREND: int = 50
    HTF_EMA_LEN: int = 21
    EMA_MIN_SPREAD_PCT: float = 0.10   # Écart min EMA9/EMA21 (%)

    # ── RSI ───────────────────────────────────────────────────────────────
    RSI_LEN: int = 14
    RSI_BULL_MIN: int = 45
    RSI_BULL_MAX: int = 68
    RSI_BEAR_MIN: int = 32
    RSI_BEAR_MAX: int = 55
    RSI_OB: int = 72                   # Surachat
    RSI_OS: int = 28                   # Survente

    # ── ADX ───────────────────────────────────────────────────────────────
    ADX_LEN: int = 14
    ADX_MIN: float = 20.0              # Marché en tendance si ADX > 20

    # ── Volume ────────────────────────────────────────────────────────────
    VOL_MULT: float = 1.3              # Volume signal > 1.3× moyenne
    VOL_MA_LEN: int = 20

    # ── Price Action ──────────────────────────────────────────────────────
    PIN_BAR_RATIO: float = 0.55        # Mèche ≥ 55% du range pour pin bar

    # ── ATR / SL / TP ─────────────────────────────────────────────────────
    ATR_LEN: int = 14
    SL_ATR_MULT: float = 1.5           # SL = ATR × 1.5 (serré en scalping)
    RR_TP: float = 2.0                 # R:R 1:2
    TRAILING_ATR: float = 1.0          # Trailing après activation

    # ── Cooldown ──────────────────────────────────────────────────────────
    COOLDOWN_BARS: int = 3             # Minimum 3 bougies entre trades

    # ── Durée max d'un trade scalping ─────────────────────────────────────
    MAX_TRADE_DURATION_BARS: int = 20  # Fermer après 20 bougies sans résultat

    # ── Risk management scalping ──────────────────────────────────────────
    RISQUE_PAR_TRADE_PCT: float = 0.5  # 0.5% par trade (conservateur)
    MAX_POSITIONS_OUVERTES: int = 1
    MAX_DRAWDOWN_JOURNALIER_PCT: float = 2.0
    MAX_DRAWDOWN_TOTAL_PCT: float = 8.0
    PORTFOLIO_MAX_EXPOSITION_PCT: float = 5.0
    PORTFOLIO_MAX_DD_JOURNALIER_PCT: float = 5.0

    # ── Circuit breaker scalping — plus réactif ────────────────────────────
    CB_WARNING_DD_PCT: float = 1.0
    CB_STOP_DD_PCT: float = 1.5
    CB_PAUSE_DURATION_HEURES: int = 2
    CB_WARNING_RISK_MULTIPLIER: float = 0.5

    # ── Trailing stop — actif dès +0.5R en scalping ───────────────────────
    TRAILING_ACTIVATION_RR: float = 0.5
    TRAILING_DISTANCE_ATR: float = 1.0

    # ── Pyramiding — désactivé en scalping ────────────────────────────────
    PYRAMIDING_ENABLED: bool = False
    ADDON_RISQUE_PCT: float = 0.25

    # ── Spread scalping ───────────────────────────────────────────────────
    SPREAD_HARD_CAP_POINTS: float = 20.0
    SPREAD_DYNAMIC_MULTIPLIER: float = 2.0
    SPREAD_AVERAGE_WINDOW_MIN: int = 30

    # ── Volatilité ATR ────────────────────────────────────────────────────
    ATR_PERIODE: int = 14
    ATR_MIN_RATIO: float = 0.30
    ATR_MAX_RATIO: float = 4.0

    # ── Sessions ───────────────────────────────────────────────────────────
    SESSIONS: List[dict] = field(default_factory=list)

    # ── News ───────────────────────────────────────────────────────────────
    NEWS_BLOCK_AVANT_MIN: int = 30
    NEWS_BLOCK_APRES_MIN: int = 45

    # ── Précision prix ─────────────────────────────────────────────────────
    PRICE_DIGITS: int = 2

    # ── Identification MT5 ─────────────────────────────────────────────────
    MAGIC_NUMBER: int = 20250101
    ADDON_MAGIC_NUMBER: int = 20250102
    DEVIATION_POINTS: int = 10

    # ── Boucle principale ──────────────────────────────────────────────────
    INTERVALLE_BOUCLE_SECONDES: int = 10
    MAX_TENTATIVES_CONNEXION: int = 3

    # ── Aliases anglais ────────────────────────────────────────────────────
    @property
    def SYMBOL(self) -> str: return self.SYMBOLE
    @property
    def RR_MIN(self) -> float: return 1.5
    @property
    def RR_MINIMUM(self) -> float: return 1.5
    @property
    def RISK_PER_TRADE_PCT(self) -> float: return self.RISQUE_PAR_TRADE_PCT
    @property
    def MAX_DAILY_DRAWDOWN_PCT(self) -> float: return self.MAX_DRAWDOWN_JOURNALIER_PCT
    @property
    def PORTFOLIO_MAX_EXPOSURE_PCT(self) -> float: return self.PORTFOLIO_MAX_EXPOSITION_PCT
    @property
    def OB_MIN_SCORE(self) -> int: return 0
    @property
    def OB_SCORE_MIN(self) -> int: return 0
    @property
    def SESSION_LONDON_OUVERTURE(self) -> int: return 7
    @property
    def SESSION_FERMETURE(self) -> int: return 20


# ── Compatibilité avec l'ancien code (ConfigBase) ────────────────────────
@dataclass
class ConfigBase(ScalpingBaseConfig):
    """Alias de ScalpingBaseConfig — maintenu pour compatibilité."""

    # Paramètres hérités des anciennes configs (swing/intraday)
    BOUGIES_HTF: int = 200
    BOUGIES_MTF: int = 150
    BOUGIES_LTF: int = 150
    BOUGIES_ENTREE: int = 100

    TIMEFRAME_MTF: int = _MT5_H1
    TIMEFRAME_LTF: int = _MT5_M15
    TIMEFRAME_ENTREE: int = _MT5_M5

    OB_LOOKBACK_BOUGIES: int = 50
    OB_SCORE_MIN: int = 60
    OB_MAX_TOUCHES: int = 2

    SWING_DETECTION_LOOKBACK: int = 3
    BOS_LOOKBACK_CANDLES: int = 50
    CHOCH_MAX_AGE_CANDLES: int = 20
    TREND_MAX_AGE_CANDLES: int = 30

    RSI_PERIODE: int = 14
    RSI_SEUIL_LONG: float = 45.0
    RSI_SEUIL_SHORT: float = 55.0

    SPREAD_HISTORY_MAX_MB: float = 5.0
    SPREAD_HISTORY_RETENTION_DAYS: int = 7
    SPREAD_MIN_HISTORY_SAMPLES: int = 10

    LOG_ROTATION: str = "00:00"
    LOG_RETENTION: str = "30 days"
