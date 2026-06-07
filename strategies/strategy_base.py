"""
strategies/strategy_base.py — Classe abstraite commune à toutes les stratégies.

Définit l'interface que chaque stratégie doit implémenter et centralise
les filtres communs (session, news, spread, circuit breaker, portfolio).

Les filtres communs sont exécutés dans l'ordre du moins coûteux au plus coûteux.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional, Tuple

from loguru import logger


# ── Résultat de signal ─────────────────────────────────────────────────────

@dataclass
class SignalResult:
    """
    Résultat de l'évaluation d'une stratégie.
    Compatible avec l'existant SignalTrading de strategy.py.
    """
    signal: Optional[str]           # "bullish", "bearish", ou None
    rejected_by: Optional[str]      # Filtre qui a rejeté (ex: "news_filter")
    reason: str                     # Raison lisible

    # Métadonnées du signal
    ob_score: int = 0
    ob_strength: str = ""
    confluences: List[str] = field(default_factory=list)
    entry_price: float = 0.0
    risk_multiplier: float = 1.0    # Multiplicateur de risque (CB, earnings...)
    ob: Optional[object] = None     # OBMultiTimeframe ou None
    atr_m15: float = 0.0

    @property
    def valide(self) -> bool:
        """Alias pour compatibilité avec SignalTrading existant."""
        return self.signal is not None

    @property
    def raison_rejet(self) -> str:
        return self.reason


# ── Stratégie de base abstraite ────────────────────────────────────────────

class StrategieBase(ABC):
    """
    Classe abstraite commune à toutes les stratégies du bot multi-actif.

    Chaque stratégie concrete doit implémenter :
    - evaluate_signal() : logique principale du setup
    - get_strategy_name() : identifiant du setup

    Les filtres communs (session, news, spread, CB, portfolio) sont
    centralisés ici via run_common_filters().
    """

    def __init__(
        self,
        connecteur=None,
        config=None,
        news_filter=None,
        spread_filter=None,
        circuit_breaker=None,
        ob_detector=None,
        structure_analyzer=None,
        daily_bias_analyzer=None,
        portfolio_risk_manager=None,
        session_manager=None,
    ) -> None:
        self.connecteur = connecteur
        self.config = config
        self.news_filter = news_filter
        self.spread_filter = spread_filter
        self.circuit_breaker = circuit_breaker
        self.ob_detector = ob_detector
        self.structure_analyzer = structure_analyzer
        self.daily_bias_analyzer = daily_bias_analyzer
        self.portfolio_rm = portfolio_risk_manager
        self.session_manager = session_manager

        self._dernier_multiplicateur_risk: float = 1.0

    # ── Filtres communs ────────────────────────────────────────────────────

    def run_common_filters(
        self,
        current_time: Optional[datetime] = None,
    ) -> Tuple[bool, str]:
        """
        Exécute les filtres communs à tous les actifs.
        Ordre : session → news → spread → CB → portfolio.

        Retourne (passed: bool, reason: str).
        """
        now = current_time or datetime.utcnow()
        symbole = getattr(self.config, "SYMBOLE", "?")

        # ── Filtre 1 : Session active ──────────────────────────────────────
        if self.session_manager is not None:
            try:
                session_ok, session_raison = self.session_manager.is_session_active(
                    symbole, now
                )
                if not session_ok:
                    return False, session_raison
            except Exception as e:
                logger.debug(f"SessionManager erreur : {e}")

        # ── Filtre 2 : News ────────────────────────────────────────────────
        if self.news_filter is not None:
            try:
                news_ok, news_raison = self.news_filter.is_trading_allowed(now)
                if not news_ok:
                    return False, f"⛔ NEWS: {news_raison}"
            except Exception as e:
                logger.debug(f"NewsFilter erreur : {e}")

        # ── Filtre 3 : Spread ──────────────────────────────────────────────
        if self.spread_filter is not None:
            try:
                spread_ok, spread_raison = self.spread_filter.is_market_tradeable(now)
                if not spread_ok:
                    return False, f"⛔ SPREAD: {spread_raison}"
            except Exception as e:
                logger.debug(f"SpreadFilter erreur : {e}")

        # ── Filtre 4 : Circuit Breaker individuel ──────────────────────────
        if self.circuit_breaker is not None:
            try:
                cb_ok, cb_raison, cb_mult = self.circuit_breaker.trading_autorise()
                self._dernier_multiplicateur_risk = cb_mult
                if not cb_ok:
                    return False, f"⛔ CB: {cb_raison}"
            except Exception as e:
                logger.debug(f"CircuitBreaker erreur : {e}")

        # ── Filtre 5 : Portfolio Risk Manager ──────────────────────────────
        if self.portfolio_rm is not None:
            try:
                risk_pct = getattr(
                    self.config, "RISQUE_PAR_TRADE_PCT",
                    getattr(self.config, "RISK_PER_TRADE_PCT", 1.0)
                )
                portfolio_ok, portfolio_raison = self.portfolio_rm.can_open_trade(
                    symbole,
                    risk_pct,
                    ob_score=0,  # Score préliminaire — affiné après détection OB
                )
                if not portfolio_ok:
                    return False, f"⛔ PORTFOLIO: {portfolio_raison}"
            except Exception as e:
                logger.debug(f"PortfolioRiskManager erreur : {e}")

        return True, "OK"

    # ── Interface abstraite ────────────────────────────────────────────────

    @abstractmethod
    def evaluate_signal(
        self,
        current_time: Optional[datetime] = None,
    ) -> SignalResult:
        """
        Évalue le setup de trading et retourne un SignalResult.
        Doit appeler run_common_filters() en premier.
        """
        pass

    @abstractmethod
    def get_strategy_name(self) -> str:
        """Retourne l'identifiant du setup (ex: 'SMC_KILLZONES_ICT_NAS100')."""
        pass

    # ── Utilitaires communs ────────────────────────────────────────────────

    def _get_prix_actuel(self) -> float:
        """Prix courant bid (LONG) ou ask (SHORT) depuis MT5."""
        if self.connecteur is None:
            return 0.0
        try:
            symbole = getattr(self.config, "SYMBOLE", "XAUUSD")
            tick = self.connecteur.get_tick(symbole)
            return float(tick["bid"]) if tick else 0.0
        except Exception:
            return 0.0

    def _get_ohlcv(self, timeframe: int, n_bougies: int = 100):
        """Récupère les données OHLCV depuis le connecteur."""
        if self.connecteur is None:
            return None
        try:
            symbole = getattr(self.config, "SYMBOLE", "XAUUSD")
            return self.connecteur.get_ohlcv(symbole, timeframe, n_bougies)
        except Exception as e:
            logger.debug(f"get_ohlcv erreur : {e}")
            return None

    def _get_ob_type_bullish(self):
        """Retourne TypeOB.HAUSSIER si disponible."""
        try:
            from ob_detector import TypeOB
            return TypeOB.HAUSSIER
        except Exception:
            return None

    def _get_ob_type_bearish(self):
        """Retourne TypeOB.BAISSIER si disponible."""
        try:
            from ob_detector import TypeOB
            return TypeOB.BAISSIER
        except Exception:
            return None


# Alias anglais
BaseStrategy = StrategieBase
