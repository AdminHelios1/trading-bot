"""
configs/config_base.py — Paramètres communs à tous les actifs multi-actif.

Définit la BaseConfig qui est héritée par chaque config d'actif.
Ne jamais importer MetaTrader5 ici — les constantes timeframe sont codées en dur.
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
class ConfigBase:
    """
    Paramètres communs à tous les actifs du bot multi-actif.

    Chaque config d'actif hérite de cette base et peut surcharger
    n'importe quel paramètre selon les besoins spécifiques de l'actif.
    """

    # ── Identification ────────────────────────────────────────────────────
    SYMBOLE: str = "XAUUSD"
    NOM_AFFICHAGE: str = "Actif"
    TYPE_STRATEGIE: str = "SMC_BASE"

    # ── Risk management par trade ─────────────────────────────────────────
    RISQUE_PAR_TRADE_PCT: float = 1.0         # % du capital par trade
    RR_MINIMUM: float = 2.0                   # R:R minimum pour valider
    RR_CIBLE: float = 2.5                     # R:R objectif principal
    MAX_POSITIONS_OUVERTES: int = 1           # Par actif (jamais > 1)

    # ── Exposition globale portefeuille ───────────────────────────────────
    PORTFOLIO_MAX_EXPOSITION_PCT: float = 5.0  # Max tous actifs confondus
    PORTFOLIO_MAX_DD_JOURNALIER_PCT: float = 5.0  # DD global → fermeture tout

    # ── Drawdown individuel ────────────────────────────────────────────────
    MAX_DRAWDOWN_JOURNALIER_PCT: float = 3.0
    MAX_DRAWDOWN_TOTAL_PCT: float = 10.0

    # ── Timeframes par défaut (surchargeables) ─────────────────────────────
    TIMEFRAME_HTF: int = _MT5_H4
    TIMEFRAME_MTF: int = _MT5_H1
    TIMEFRAME_LTF: int = _MT5_M15
    TIMEFRAME_ENTREE: int = _MT5_M5

    # ── Nombre de bougies à charger ────────────────────────────────────────
    BOUGIES_HTF: int = 200
    BOUGIES_MTF: int = 150
    BOUGIES_LTF: int = 150
    BOUGIES_ENTREE: int = 100

    # ── Détection Order Block ──────────────────────────────────────────────
    OB_LOOKBACK_BOUGIES: int = 50
    OB_SCORE_MIN: int = 60
    OB_MAX_TOUCHES: int = 2

    # ── Trailing Stop ──────────────────────────────────────────────────────
    TRAILING_DISTANCE_ATR: float = 1.5
    TRAILING_ACTIVATION_RR: float = 1.0

    # ── Pyramiding ────────────────────────────────────────────────────────
    PYRAMIDING_ENABLED: bool = True
    ADDON_RISQUE_PCT: float = 0.5
    ADDON_MAGIC_NUMBER: int = 20250102

    # ── Circuit Breaker ────────────────────────────────────────────────────
    CB_WARNING_DD_PCT: float = 1.5
    CB_STOP_DD_PCT: float = 2.0
    CB_PAUSE_DURATION_HEURES: int = 4
    CB_WARNING_RISK_MULTIPLIER: float = 0.5

    # ── Spread ─────────────────────────────────────────────────────────────
    SPREAD_HARD_CAP_POINTS: float = 35.0
    SPREAD_DYNAMIC_MULTIPLIER: float = 2.5
    SPREAD_AVERAGE_WINDOW_MIN: int = 50

    # ── ATR & Volatilité ───────────────────────────────────────────────────
    ATR_PERIODE: int = 14
    ATR_MIN_RATIO: float = 0.50
    ATR_MAX_RATIO: float = 3.0

    # ── Displacement ──────────────────────────────────────────────────────
    DISPLACEMENT_MIN_BODY_ATR_RATIO: float = 1.5
    DISPLACEMENT_MIN_BODY_RANGE_PCT: float = 60.0
    DISPLACEMENT_CLOSE_THRESHOLD_BULLISH: float = 66.0
    DISPLACEMENT_CLOSE_THRESHOLD_BEARISH: float = 34.0
    DISPLACEMENT_MAX_REJECTION_WICK_PCT: float = 20.0

    # ── Structure de marché ────────────────────────────────────────────────
    SWING_DETECTION_LOOKBACK: int = 3
    BOS_LOOKBACK_CANDLES: int = 50
    CHOCH_MAX_AGE_CANDLES: int = 20
    TREND_MAX_AGE_CANDLES: int = 30

    # ── RSI ────────────────────────────────────────────────────────────────
    RSI_PERIODE: int = 14
    RSI_SEUIL_LONG: float = 45.0
    RSI_SEUIL_SHORT: float = 55.0

    # ── Sessions ───────────────────────────────────────────────────────────
    SESSIONS: List[dict] = field(default_factory=list)

    # ── News ───────────────────────────────────────────────────────────────
    NEWS_BLOCK_AVANT_MIN: int = 30
    NEWS_BLOCK_APRES_MIN: int = 60

    # ── Identification MT5 ─────────────────────────────────────────────────
    MAGIC_NUMBER: int = 20250101       # Magic principal du trade
    ADDON_MAGIC_NUMBER: int = 20250102
    DEVIATION_POINTS: int = 10

    # ── Boucle principale ──────────────────────────────────────────────────
    INTERVALLE_BOUCLE_SECONDES: int = 60
    MAX_TENTATIVES_CONNEXION: int = 3

    # Aliases anglais pour compatibilité avec le code existant
    @property
    def SYMBOL(self) -> str: return self.SYMBOLE
    @property
    def RR_MIN(self) -> float: return self.RR_MINIMUM
    @property
    def RISK_PER_TRADE_PCT(self) -> float: return self.RISQUE_PAR_TRADE_PCT
    @property
    def MAX_DAILY_DRAWDOWN_PCT(self) -> float: return self.MAX_DRAWDOWN_JOURNALIER_PCT
    @property
    def PORTFOLIO_MAX_EXPOSURE_PCT(self) -> float: return self.PORTFOLIO_MAX_EXPOSITION_PCT
    @property
    def OB_MIN_SCORE(self) -> int: return self.OB_SCORE_MIN
    @property
    def SESSION_LONDON_OUVERTURE(self) -> int: return 7
    @property
    def SESSION_FERMETURE(self) -> int: return 20
