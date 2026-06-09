"""
asset_engine.py — Moteur de trading complet pour un actif.

Encapsule tous les modules nécessaires pour trader un actif de façon
entièrement indépendante. Un AssetEngine = un actif = une stratégie dédiée.

La connexion MT5 est partagée (une seule instance pour tous les actifs).
Chaque actif a son propre circuit breaker, spread monitor, journal, etc.
"""

from datetime import datetime
from typing import Optional

from loguru import logger


class MoteurActif:
    """
    Moteur de trading complet pour un actif du portefeuille.

    Instancie et coordonne tous les modules nécessaires :
    - News filter, spread filter, circuit breaker (individuel)
    - OB detector, structure analyzer, daily bias
    - Risk manager, trade manager, pyramiding manager
    - Journal de trades dédié

    La stratégie est sélectionnée automatiquement selon config.TYPE_STRATEGIE.
    """

    def __init__(
        self,
        config,
        connecteur=None,
        portfolio_rm=None,
        notifier=None,
        session_manager=None,
    ) -> None:
        self.config = config
        self.symbole = getattr(config, "SYMBOLE", "?")
        self.connecteur = connecteur
        self.portfolio_rm = portfolio_rm
        self.notifier = notifier
        self.session_manager = session_manager

        # ── Initialisation lazy des sous-modules ───────────────────────────
        self._news_filter = None
        self._spread_filter = None
        self._circuit_breaker = None
        self._ob_detector = None
        self._structure_analyzer = None
        self._daily_bias_analyzer = None
        self._trade_manager = None
        self._journal = None
        self._strategy = None

        self._init_modules()

        logger.info(
            f"MoteurActif initialisé : {getattr(config, 'NOM_AFFICHAGE', self.symbole)} | "
            f"Setup : {getattr(config, 'TYPE_STRATEGIE', '?')}"
        )

    # ── Initialisation ─────────────────────────────────────────────────────

    def _init_modules(self) -> None:
        """Initialise tous les sous-modules de façon défensive."""
        # News filter
        try:
            from news_filter import FiltreNews
            from news_fetcher import RecuperateurCalendrier
            self._news_filter = FiltreNews(fetcher=RecuperateurCalendrier())
        except Exception as e:
            logger.debug(f"[{self.symbole}] NewsFilter non disponible : {e}")

        # Spread filter
        try:
            from spread_monitor import MoniteurSpread
            from spread_filter import FiltreSpread
            moniteur = MoniteurSpread(connecteur=self.connecteur)
            self._spread_filter = FiltreSpread(
                moniteur=moniteur, connecteur=self.connecteur
            )
        except Exception as e:
            logger.debug(f"[{self.symbole}] SpreadFilter non disponible : {e}")

        # Circuit breaker individuel par actif
        try:
            from daily_stats_tracker import SuiveurStatsJournalieres
            from circuit_breaker import CircuitBreaker
            stats = SuiveurStatsJournalieres(connecteur=self.connecteur)
            self._circuit_breaker = CircuitBreaker(
                suiveur_stats=stats,
                connecteur=self.connecteur,
                notifier=self.notifier,
            )
        except Exception as e:
            logger.debug(f"[{self.symbole}] CircuitBreaker non disponible : {e}")

        # OB Detector
        try:
            from ob_detector import DetecteurOB
            self._ob_detector = DetecteurOB()
        except Exception as e:
            logger.debug(f"[{self.symbole}] OBDetector non disponible : {e}")

        # Structure Analyzer
        try:
            from structure_analyzer import AnalyseurStructure
            self._structure_analyzer = AnalyseurStructure()
        except Exception as e:
            logger.debug(f"[{self.symbole}] StructureAnalyzer non disponible : {e}")

        # Trade Manager individuel par actif
        try:
            from trade_manager import GestionnairePositions
            self._trade_manager = GestionnairePositions(connecteur=self.connecteur)
        except Exception as e:
            logger.debug(f"[{self.symbole}] TradeManager non disponible : {e}")

        # Journal dédié par actif
        try:
            from trade_journal import JournalTrades
            from chart_generator import GenerateurGraphiques
            from performance_analyzer import AnalyseurPerformance
            self._journal = JournalTrades(
                connecteur=self.connecteur,
                config=self.config,
                generateur_graphiques=GenerateurGraphiques(self.connecteur, self.config),
                analyseur_performance=AnalyseurPerformance(self.config),
            )
        except Exception as e:
            logger.debug(f"[{self.symbole}] Journal non disponible : {e}")

        # Stratégie dédiée
        self._strategy = self._creer_strategie()

    def _creer_strategie(self):
        """Instancie la stratégie adaptée selon config.TYPE_STRATEGIE."""
        type_strategie = getattr(self.config, "TYPE_STRATEGIE", "SMC_BREAKER_BLOCK")

        kwargs_communs = {
            "connecteur": self.connecteur,
            "config": self.config,
            "news_filter": self._news_filter,
            "spread_filter": self._spread_filter,
            "circuit_breaker": self._circuit_breaker,
            "ob_detector": self._ob_detector,
            "structure_analyzer": self._structure_analyzer,
            "daily_bias_analyzer": self._daily_bias_analyzer,
            "portfolio_risk_manager": self.portfolio_rm,
            "session_manager": self.session_manager,
        }

        try:
            # ── Stratégies scalping (nouvelles) ───────────────────────────
            # NAS100 — détecter par symbole en priorité
            symbole = getattr(self.config, "SYMBOLE",
                               getattr(self.config, "SYMBOL", "XAUUSD"))
            if symbole == "NAS100":
                from strategies.strategy_scalping_nas100 import NAS100ScalpingStrategy
                logger.info(f"AssetEngine: NAS100 → NAS100ScalpingStrategy M1")
                return NAS100ScalpingStrategy(**kwargs_communs)

            if type_strategie in ("SCALPING_HYBRID", "SCALPING_HYBRID_FVG",
                                   "SCALPING_HYBRID_SWEEP"):
                if symbole == "US500":
                    from strategies.strategy_scalping_sp500 import SP500ScalpingStrategy
                    logger.info(f"AssetEngine: SP500 → SP500ScalpingStrategy M5 + FVG")
                    return SP500ScalpingStrategy(**kwargs_communs)
                elif symbole == "XTIUSD":
                    from strategies.strategy_scalping_wti import WTIScalpingStrategy
                    logger.info(f"AssetEngine: WTI → WTIScalpingStrategy M5 + Sweep")
                    return WTIScalpingStrategy(**kwargs_communs)
                else:
                    # XAUUSD et autres → stratégie scalping de base
                    from strategies.strategy_scalping_base import ScalpingStrategy
                    logger.info(f"AssetEngine: {symbole} → ScalpingStrategy M5")
                    return ScalpingStrategy(**kwargs_communs)

            # ── Stratégies SMC héritées (compatibilité) ───────────────────
            elif type_strategie == "SMC_BREAKER_BLOCK":
                from strategy import StrategieSMC
                return StrategieSMC(
                    filtre_news=self._news_filter,
                    filtre_spread=self._spread_filter,
                    circuit_breaker=self._circuit_breaker,
                )

            elif type_strategie == "SMC_KILLZONES_ICT":
                from strategies.strategy_nas100 import StrategieNAS100
                return StrategieNAS100(**kwargs_communs)

            elif type_strategie == "SMC_FVG_PRIORITY":
                from strategies.strategy_sp500 import StrategieSP500
                return StrategieSP500(**kwargs_communs)

            elif type_strategie == "SMC_LIQUIDITY_SWEEP":
                from strategies.strategy_wti import StrategieWTI
                return StrategieWTI(**kwargs_communs)

            else:
                logger.error(f"Type de stratégie inconnu : {type_strategie}")
                return None

        except Exception as e:
            logger.error(
                f"[{self.symbole}] Erreur création stratégie {type_strategie} : {e}"
            )
            return None

    # ── Cycle de trading ───────────────────────────────────────────────────

    def run_cycle(self) -> None:
        """
        Un cycle de trading complet pour cet actif.
        Appelé toutes les 60 secondes par le scheduler APScheduler.

        1. Mettre à jour le trade en cours (trailing, TP partiels)
        2. Si pas de position : évaluer un nouveau signal
        3. Si signal valide : ouvrir le trade
        """
        now = datetime.utcnow()

        try:
            # ── Gestion du trade en cours ──────────────────────────────────
            if self._trade_manager is not None:
                if self._trade_manager.a_position_ouverte(self.symbole):
                    df_m15 = self._get_ohlcv_lent()
                    if df_m15 is not None:
                        self._trade_manager.gerer_positions(df_m15)
                    return  # Pas de nouveau signal si position ouverte

            # ── Évaluation d'un nouveau signal ─────────────────────────────
            if self._strategy is None:
                logger.debug(f"[{self.symbole}] Stratégie non initialisée")
                return

            signal = self._evaluer_signal(now)

            if signal is None or not getattr(signal, "valide", False):
                return

            # ── Ouvrir le trade ────────────────────────────────────────────
            self._executer_signal(signal)

        except Exception as e:
            logger.error(f"[{self.symbole}] Erreur run_cycle : {e}")

    def _evaluer_signal(self, now: datetime) -> Optional[object]:
        """Évalue le signal selon le type de stratégie."""
        try:
            # Stratégies multi-actif (héritent de StrategieBase)
            from strategies.strategy_base import StrategieBase, SignalResult
            if isinstance(self._strategy, StrategieBase):
                result = self._strategy.evaluate_signal(now)
                if result.signal is not None:
                    logger.debug(
                        f"[{self.symbole}] Signal {result.signal} | "
                        f"OB: {result.ob_score}/100"
                    )
                return result

            # Stratégie XAUUSD existante (StrategieSMC)
            df_h4 = self._get_ohlcv_lent()
            df_m15 = self._get_ohlcv_lent()
            df_m5 = self._get_ohlcv_lent()
            if any(df is None for df in [df_h4, df_m15, df_m5]):
                return None
            return self._strategy.evaluer(df_h4, df_m15, df_m5, now.hour, True)

        except Exception as e:
            logger.debug(f"[{self.symbole}] Évaluation signal erreur : {e}")
            return None

    def _executer_signal(self, signal) -> None:
        """Exécute un signal validé — ouvre la position sur MT5."""
        try:
            if self.notifier is not None:
                direction = getattr(signal, "signal", None) or getattr(
                    signal, "direction", None
                )
                direction_str = str(direction)
                if hasattr(direction, "value"):
                    direction_str = direction.value

                self.notifier.send_trade_notification("OPEN", {
                    "symbol": self.symbole,
                    "direction": "LONG" if "long" in direction_str.lower()
                                          or "bullish" in direction_str.lower()
                                 else "SHORT",
                    "entry": getattr(signal, "entry_price", 0.0)
                             or getattr(signal, "prix_entree_suggere", 0.0),
                    "sl": 0.0,
                    "tp1": 0.0,
                    "lots": 0.0,
                    "risk_pct": getattr(
                        self.config, "RISQUE_PAR_TRADE_PCT", 1.0
                    ),
                    "ob_score": getattr(signal, "ob_score", 0)
                                or getattr(signal, "score_confiance", 0),
                    "ob_strength": getattr(signal, "ob_strength", ""),
                    "confluences": getattr(signal, "confluences", []),
                })

        except Exception as e:
            logger.error(f"[{self.symbole}] Exécution signal erreur : {e}")

    def _get_ohlcv_lent(self):
        """Récupère les données OHLCV M15 pour la gestion des trades."""
        if self.connecteur is None:
            return None
        try:
            timeframe = getattr(self.config, "TIMEFRAME_LTF", 15)
            n_bougies = getattr(self.config, "BOUGIES_LTF", 150)
            return self.connecteur.get_ohlcv(self.symbole, timeframe, n_bougies)
        except Exception:
            return None

    # ── Accesseurs publics ─────────────────────────────────────────────────

    @property
    def a_position_ouverte(self) -> bool:
        """True si une position est actuellement ouverte sur cet actif."""
        if self._trade_manager is None:
            return False
        return self._trade_manager.a_position_ouverte(self.symbole)

    def get_status(self) -> dict:
        """Retourne le statut de l'actif pour le dashboard."""
        return {
            "symbole": self.symbole,
            "nom": getattr(self.config, "NOM_AFFICHAGE", self.symbole),
            "type_strategie": getattr(self.config, "TYPE_STRATEGIE", "?"),
            "position_ouverte": self.a_position_ouverte,
            "strategy_ready": self._strategy is not None,
        }


# Alias anglais
AssetEngine = MoteurActif
