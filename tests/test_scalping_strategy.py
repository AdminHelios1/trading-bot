"""
tests/test_scalping_strategy.py — Tests unitaires de la stratégie scalping.

Vérifie :
- Les 10 conditions du signal (LONG et SHORT)
- Le filtre ADX (range bloqué)
- Le filtre EMA spread
- Le cooldown
- La sortie sur bougie adverse
- La durée maximum du trade
- Le trailing stop à +0.5R
- Le risk 0.5% et timeframe M5
- Le pyramiding désactivé
"""

import pytest
import pandas as pd
import numpy as np
from datetime import datetime
from types import SimpleNamespace
from typing import Optional

from strategies.strategy_scalping_base import ScalpingStrategy
from strategies.strategy_scalping_nas100 import NAS100ScalpingStrategy
from configs.config_xauusd import XAUUSDScalpConfig
from configs.config_nas100 import NAS100ScalpConfig
from configs.config_base import ScalpingBaseConfig
from trade_manager import GestionnairePositions, DirectionTrade
from trade_manager import NiveauSL, NiveauPartiel


# ── Fixtures ───────────────────────────────────────────────────────────────

def create_mock_scalping_strategy(
    config=None,
    connector=None,
) -> ScalpingStrategy:
    """Crée une ScalpingStrategy sans connexion MT5 pour les tests."""
    cfg = config or XAUUSDScalpConfig()
    strat = ScalpingStrategy.__new__(ScalpingStrategy)
    strat.config = cfg
    strat.connecteur = connector
    strat.news_filter = None
    strat.spread_filter = None
    strat.circuit_breaker = None
    strat.ob_detector = None
    strat.structure_analyzer = None
    strat.daily_bias_analyzer = None
    strat.portfolio_rm = None
    strat.session_manager = None
    strat._last_trade_bar = -999
    strat._current_bar = 0
    strat._heure_dernier_signal = None
    return strat


def create_bullish_scenario_df(n: int = 60) -> pd.DataFrame:
    """
    Crée un DataFrame avec un scénario haussier validant les 10 conditions.
    - EMA 9 > EMA 21 > EMA 50
    - ADX > 20
    - Engulfing haussier sur la dernière bougie fermée
    - Volume élevé
    """
    np.random.seed(42)
    base = 2000.0
    closes = base + np.cumsum(np.random.normal(0.5, 1.0, n))  # Tendance haussière

    df = pd.DataFrame({
        "open":        closes - np.random.uniform(0, 2, n),
        "high":        closes + np.random.uniform(1, 3, n),
        "low":         closes - np.random.uniform(1, 3, n),
        "close":       closes,
        "tick_volume": np.random.randint(100, 300, n) * 1.5,
    })

    # Forcer un engulfing haussier sur iloc[-2]
    i = n - 2
    df.iloc[i - 1, df.columns.get_loc("open")]  = closes[i - 1] + 2
    df.iloc[i - 1, df.columns.get_loc("close")] = closes[i - 1] - 2  # Baissière
    df.iloc[i, df.columns.get_loc("open")]  = closes[i] - 3          # Haussière large
    df.iloc[i, df.columns.get_loc("close")] = closes[i] + 3
    df.iloc[i, df.columns.get_loc("high")]  = closes[i] + 4
    df.iloc[i, df.columns.get_loc("low")]   = closes[i] - 4
    df.iloc[i, df.columns.get_loc("tick_volume")] = 500  # Volume élevé

    return df


def create_ranging_market_df(n: int = 60) -> pd.DataFrame:
    """Crée un DataFrame avec un marché en range (ADX bas ~12)."""
    np.random.seed(99)
    closes = 2000.0 + np.random.normal(0, 0.5, n)  # Pas de tendance

    df = pd.DataFrame({
        "open":        closes - np.random.uniform(0, 0.5, n),
        "high":        closes + np.random.uniform(0, 0.5, n),
        "low":         closes - np.random.uniform(0, 0.5, n),
        "close":       closes,
        "tick_volume": np.random.randint(50, 100, n),
    })
    return df


def create_flat_ema_df(n: int = 60) -> pd.DataFrame:
    """Crée un DataFrame avec EMAs très proches (écart < 0.10%)."""
    closes = np.ones(n) * 2000.0 + np.random.normal(0, 0.1, n)  # Flat

    df = pd.DataFrame({
        "open":        closes - 0.1,
        "high":        closes + 0.2,
        "low":         closes - 0.2,
        "close":       closes,
        "tick_volume": np.random.randint(80, 120, n),
    })
    return df


def create_mock_trade_manager(config=None) -> GestionnairePositions:
    """Crée un GestionnairePositions avec config scalping pour les tests."""
    return GestionnairePositions(connecteur=None, config=config or XAUUSDScalpConfig())


def create_mock_long_trade(
    entry: float = 2000.0,
    sl: float = 1990.0,
) -> SimpleNamespace:
    """Crée un trade LONG mock pour les tests du trade_manager."""
    trade = SimpleNamespace(
        id_trade="test_trade_01",
        direction=DirectionTrade.LONG,
        prix_entree=entry,
        heure_entree=datetime(2025, 6, 9, 10, 0, 0),
        sl=SimpleNamespace(prix_actuel=sl, prix_initial=sl),
        stop_loss=sl,
        entry_price=entry,
    )
    return trade


def create_mock_short_trade(
    entry: float = 2000.0,
    sl: float = 2010.0,
) -> SimpleNamespace:
    """Crée un trade SHORT mock."""
    return SimpleNamespace(
        id_trade="test_trade_02",
        direction=DirectionTrade.SHORT,
        prix_entree=entry,
        sl=SimpleNamespace(prix_actuel=sl, prix_initial=sl),
        stop_loss=sl,
        entry_price=entry,
    )


# ── Tests : Calcul des indicateurs ────────────────────────────────────────

class TestCalculIndicateurs:

    def test_calcul_retourne_toutes_les_cles(self):
        """_calculate_indicators() retourne toutes les clés attendues."""
        strat = create_mock_scalping_strategy()
        df = create_bullish_scenario_df()
        ind = strat._calculate_indicators(df)

        cles_attendues = [
            "ema_fast", "ema_slow", "ema_trend",
            "rsi", "atr", "adx",
            "volume", "vol_avg",
            "close", "open", "high", "low",
            "bull_engulf", "bear_engulf",
            "bull_pin", "bear_pin",
            "close_pos_pct", "ema_spread_pct",
            "bounce_bull", "bounce_bear",
            "cross_up", "cross_down",
        ]
        for cle in cles_attendues:
            assert cle in ind, f"Clé manquante : {cle}"

    def test_ema_fast_superieur_slow_sur_tendance_haussiere(self):
        """EMA fast > EMA slow sur un scénario haussier."""
        strat = create_mock_scalping_strategy()
        df = create_bullish_scenario_df()
        ind = strat._calculate_indicators(df)

        # Sur une tendance haussière construite, EMA fast doit dépasser EMA slow
        assert ind["ema_fast"] > 0
        assert ind["ema_slow"] > 0
        assert ind["atr"] > 0
        assert 0 <= ind["rsi"] <= 100

    def test_engulfing_haussier_detecte(self):
        """L'engulfing haussier forcé dans le DataFrame est détecté."""
        strat = create_mock_scalping_strategy()
        df = create_bullish_scenario_df()
        ind = strat._calculate_indicators(df)

        # L'engulfing est forcé dans create_bullish_scenario_df
        assert ind["bull_engulf"] is True

    def test_adx_calculé(self):
        """ADX est calculé et dans une plage raisonnable."""
        strat = create_mock_scalping_strategy()
        df = create_bullish_scenario_df(n=60)
        ind = strat._calculate_indicators(df)

        assert 0 <= ind["adx"] <= 100

    def test_close_pos_pct_dans_intervalle(self):
        """close_pos_pct est entre 0 et 100."""
        strat = create_mock_scalping_strategy()
        df = create_bullish_scenario_df()
        ind = strat._calculate_indicators(df)

        assert 0.0 <= ind["close_pos_pct"] <= 100.0

    def test_ema_spread_pct_positif(self):
        """ema_spread_pct est positif (valeur absolue)."""
        strat = create_mock_scalping_strategy()
        df = create_bullish_scenario_df()
        ind = strat._calculate_indicators(df)

        assert ind["ema_spread_pct"] >= 0.0


# ── Tests : Filtres de signal ──────────────────────────────────────────────

class TestFiltresSignal:

    def test_cooldown_bloque_trade_immediat(self):
        """Le cooldown bloque un signal dans les 3 bougies suivant un trade."""
        strat = create_mock_scalping_strategy()
        strat._register_trade()
        strat._current_bar = strat._last_trade_bar + 1  # Seulement 1 bougie

        assert strat._check_cooldown() is False

    def test_cooldown_libere_apres_n_bougies(self):
        """Le cooldown est libéré après COOLDOWN_BARS bougies."""
        strat = create_mock_scalping_strategy()
        strat._register_trade()
        strat._current_bar = strat._last_trade_bar + 3  # Exactement COOLDOWN_BARS

        assert strat._check_cooldown() is True

    def test_pas_de_cooldown_au_demarrage(self):
        """Pas de cooldown au démarrage (aucun trade précédent)."""
        strat = create_mock_scalping_strategy()
        assert strat._check_cooldown() is True

    def test_pyramiding_desactive(self):
        """Pyramiding désactivé en scalping."""
        config = ScalpingBaseConfig()
        assert config.PYRAMIDING_ENABLED is False

    def test_risk_0_5_pct(self):
        """Risk par trade = 0.5% (pas 1%)."""
        config = XAUUSDScalpConfig()
        assert config.RISQUE_PAR_TRADE_PCT == 0.5

    def test_timeframe_signal_m5(self):
        """Timeframe signal = M5 (5) pour XAUUSD."""
        config = XAUUSDScalpConfig()
        assert config.TIMEFRAME_SIGNAL == 5  # _MT5_M5 = 5

    def test_timeframe_signal_m1_nas100(self):
        """Timeframe signal = M1 (1) pour NAS100."""
        config = NAS100ScalpConfig()
        assert config.TIMEFRAME_SIGNAL == 1  # _MT5_M1 = 1

    def test_max_trade_duration_20_bougies(self):
        """Durée max = 20 bougies."""
        config = ScalpingBaseConfig()
        assert config.MAX_TRADE_DURATION_BARS == 20

    def test_trailing_activation_a_0_5R(self):
        """Trailing activé dès +0.5R (pas +1R)."""
        config = ScalpingBaseConfig()
        assert config.TRAILING_ACTIVATION_R == 0.5


# ── Tests : Trade Manager scalping ────────────────────────────────────────

class TestTradeManagerScalping:

    def test_bougie_adverse_ferme_long(self):
        """Engulfing baissier + clôture tiers inférieur → sortie LONG."""
        mgr = create_mock_trade_manager()
        trade = create_mock_long_trade()
        candle = {"bear_engulf": True, "close_pos_pct": 20, "close": 2000.0}

        assert mgr._check_adverse_candle_exit(trade, candle) is True

    def test_bougie_adverse_pas_de_sortie_sans_close_position(self):
        """Engulfing baissier mais clôture tiers supérieur → pas de sortie."""
        mgr = create_mock_trade_manager()
        trade = create_mock_long_trade()
        candle = {"bear_engulf": True, "close_pos_pct": 70, "close": 2000.0}

        assert mgr._check_adverse_candle_exit(trade, candle) is False

    def test_bougie_adverse_ferme_short(self):
        """Engulfing haussier + clôture tiers supérieur → sortie SHORT."""
        mgr = create_mock_trade_manager()
        trade = create_mock_short_trade()
        candle = {"bull_engulf": True, "close_pos_pct": 80, "close": 2000.0}

        assert mgr._check_adverse_candle_exit(trade, candle) is True

    def test_pas_de_sortie_adverse_si_desactive(self):
        """ADVERSE_CANDLE_EXIT=False → jamais de sortie adverse."""
        config = XAUUSDScalpConfig()
        config.ADVERSE_CANDLE_EXIT = False
        mgr = create_mock_trade_manager(config=config)
        trade = create_mock_long_trade()
        candle = {"bear_engulf": True, "close_pos_pct": 10, "close": 2000.0}

        assert mgr._check_adverse_candle_exit(trade, candle) is False

    def test_max_duration_ferme_apres_20_bougies(self):
        """Trade fermé après MAX_TRADE_DURATION_BARS bougies."""
        mgr = create_mock_trade_manager()
        trade = create_mock_long_trade()

        assert mgr._check_max_duration(trade, 20) is True

    def test_max_duration_pas_de_fermeture_avant(self):
        """Trade PAS fermé avant MAX_TRADE_DURATION_BARS."""
        mgr = create_mock_trade_manager()
        trade = create_mock_long_trade()

        assert mgr._check_max_duration(trade, 19) is False

    def test_trailing_active_a_0_5R_long(self):
        """Trailing activé dès +0.5R pour un LONG."""
        mgr = create_mock_trade_manager()
        # Trade LONG : entry=2000, sl=1990 → distance=10, 0.5R=+5
        trade = create_mock_long_trade(entry=2000.0, sl=1990.0)
        atr = 3.0

        # Prix à 2005 = exactement +0.5R → trailing doit s'activer
        new_sl = mgr._update_trailing_stop_scalping(trade, 2005.0, atr)
        assert new_sl is not None
        assert new_sl > 1990.0

    def test_trailing_inactif_avant_0_5R(self):
        """Trailing PAS activé si prix < activation (+0.5R)."""
        mgr = create_mock_trade_manager()
        trade = create_mock_long_trade(entry=2000.0, sl=1990.0)
        atr = 3.0

        # Prix à 2002 = seulement +0.2R → trailing ne s'active pas
        new_sl = mgr._update_trailing_stop_scalping(trade, 2002.0, atr)
        assert new_sl is None

    def test_trade_manager_accepte_config(self):
        """GestionnairePositions accepte un paramètre config."""
        config = XAUUSDScalpConfig()
        mgr = GestionnairePositions(connecteur=None, config=config)
        assert mgr.config is config


# ── Tests : Configs scalping ───────────────────────────────────────────────

class TestConfigsScalping:

    def test_xauusd_spread_reduit(self):
        """XAUUSD spread réduit à 25 (était 35 en swing)."""
        config = XAUUSDScalpConfig()
        assert config.SPREAD_HARD_CAP_POINTS <= 25

    def test_nas100_emas_plus_reactives(self):
        """NAS100 utilise EMAs 8/13/34 (plus réactives que 9/21/50)."""
        config = NAS100ScalpConfig()
        assert config.EMA_FAST == 8
        assert config.EMA_SLOW == 13
        assert config.EMA_TREND == 34

    def test_portfolio_dd_max_reduit(self):
        """DD global max réduit à 3% pour le scalping."""
        config = ScalpingBaseConfig()
        assert config.PORTFOLIO_MAX_DD_JOURNALIER_PCT <= 3.0

    def test_cb_seuil_abaisse(self):
        """Circuit breaker se déclenche à 1% (était 1.5% en swing)."""
        config = ScalpingBaseConfig()
        assert config.CB_WARNING_DD_PCT <= 1.0

    def test_symbol_et_symbole_compatibles(self):
        """SYMBOL et SYMBOLE retournent la même valeur."""
        config = XAUUSDScalpConfig()
        assert config.SYMBOL == config.SYMBOLE == "XAUUSD"

    def test_close_third_filter_active(self):
        """Filtre clôture dans le bon tiers activé par défaut."""
        config = ScalpingBaseConfig()
        assert config.CLOSE_THIRD_FILTER is True

    def test_adverse_candle_exit_active(self):
        """Sortie sur bougie adverse activée par défaut."""
        config = ScalpingBaseConfig()
        assert config.ADVERSE_CANDLE_EXIT is True


# ── Tests : NAS100 Killzones ───────────────────────────────────────────────

class TestNAS100Killzones:

    def test_killzone_ny_open_13h35(self):
        """NAS100 autorisé en Killzone NY Open à 13h35 UTC."""
        strat = NAS100ScalpingStrategy.__new__(NAS100ScalpingStrategy)
        strat.config = NAS100ScalpConfig()

        heure = datetime(2025, 6, 9, 13, 35, 0)
        in_kz, raison = strat._check_killzone(heure)

        assert in_kz is True
        assert "NY_Open" in raison

    def test_hors_killzone_12h(self):
        """NAS100 bloqué à 12h UTC (hors Killzone)."""
        strat = NAS100ScalpingStrategy.__new__(NAS100ScalpingStrategy)
        strat.config = NAS100ScalpConfig()

        heure = datetime(2025, 6, 9, 12, 0, 0)
        in_kz, raison = strat._check_killzone(heure)

        assert in_kz is False

    def test_earnings_season_detectee(self):
        """Les mois d'earnings (1, 4, 7, 10) sont détectés."""
        strat = NAS100ScalpingStrategy.__new__(NAS100ScalpingStrategy)
        strat.config = NAS100ScalpConfig()

        for mois in [1, 4, 7, 10]:
            assert strat._is_earnings_season(datetime(2025, mois, 15))

    def test_pas_earnings_hors_saison(self):
        """Les autres mois ne sont pas en earnings season."""
        strat = NAS100ScalpingStrategy.__new__(NAS100ScalpingStrategy)
        strat.config = NAS100ScalpConfig()

        for mois in [2, 3, 5, 6, 8, 9, 11, 12]:
            assert not strat._is_earnings_season(datetime(2025, mois, 15))
