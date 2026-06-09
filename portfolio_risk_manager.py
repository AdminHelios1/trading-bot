"""
portfolio_risk_manager.py — Supervision globale de l'exposition du portefeuille.

C'est le gardien central : il autorise ou bloque chaque nouveau trade
en fonction de l'exposition totale (max 5%) et des corrélations entre actifs.

RÈGLES ABSOLUES :
1. Exposition totale maximum : 5% du capital simultanément
2. NAS100 + SP500 jamais ensemble (corrélation 0.95)
3. Drawdown global > 5% journalier → fermeture de tout
4. Signal priorité : OB score le plus élevé d'abord
5. En cas d'égalité OB score → XAUUSD > WTI > NAS100 > SP500
"""

import json
import os
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional, Tuple

from loguru import logger

from correlation_monitor import MoniteurCorrelation


# ── Dataclasses ────────────────────────────────────────────────────────────

@dataclass
class ExpositionPortefeuille:
    """État de l'exposition globale du portefeuille à un instant T."""
    timestamp_utc: datetime
    exposition_totale_pct: float        # % du capital total risqué
    exposition_totale_usd: float        # USD total risqué
    balance_compte: float
    exposition_par_symbole: Dict[str, float]  # {symbole: pct}
    budget_disponible_pct: float        # % restant (5% - total)
    est_a_max: bool
    paires_correlees_actives: List[str]

    # Aliases anglais
    @property
    def total_exposure_pct(self) -> float: return self.exposition_totale_pct
    @property
    def available_budget_pct(self) -> float: return self.budget_disponible_pct
    @property
    def is_at_max_exposure(self) -> bool: return self.est_a_max
    @property
    def positions_by_symbol(self) -> Dict[str, float]:
        return self.exposition_par_symbole


# ── Gestionnaire de risque portefeuille ────────────────────────────────────

class GestionnaireRisquePortefeuille:
    """
    Supervise l'exposition globale du portefeuille sur tous les actifs.

    Design : toutes les méthodes MT5 acceptent des overrides optionnels
    pour permettre les tests sans connexion MT5 réelle.
    """

    MAX_EXPOSITION_PORTEFEUILLE_PCT: float = 5.0
    MAX_DD_JOURNALIER_PORTEFEUILLE_PCT: float = 3.0   # Réduit pour scalping (était 5%)

    # Ordre de priorité des actifs (en cas d'égalité OB score)
    PRIORITE_ACTIFS: List[str] = ["XAUUSD", "XTIUSD", "NAS100", "US500"]

    def __init__(
        self,
        connecteur=None,
        configs: Optional[Dict] = None,
        notifier=None,
    ) -> None:
        self.connecteur = connecteur
        self.configs = configs or {}
        self.notifier = notifier
        self.moniteur_correlation = MoniteurCorrelation()

        self._equity_debut_journee: float = 0.0
        self._pic_equity: float = 0.0

        logger.info(
            f"PortfolioRiskManager initialisé | "
            f"Max exposition: {self.MAX_EXPOSITION_PORTEFEUILLE_PCT}% | "
            f"Actifs: {list(self.configs.keys())}"
        )

    # ── Interface principale ───────────────────────────────────────────────

    def can_open_trade(
        self,
        symbol: str,
        risk_pct: float,
        ob_score: int = 0,
        # Overrides pour les tests (évite les appels MT5)
        _balance: Optional[float] = None,
        _positions: Optional[Dict[str, float]] = None,
    ) -> Tuple[bool, str]:
        """
        Vérifie si un nouveau trade peut être ouvert sur cet actif.
        Appelé par chaque AssetEngine/Stratégie avant d'ouvrir une position.

        Args:
            symbol   : Symbole MT5 de l'actif (ex: "XAUUSD").
            risk_pct : % du capital risqué sur ce trade.
            ob_score : Score OB du signal (pour logging).
            _balance : Override balance pour les tests.
            _positions: Override positions {symbol: pct} pour les tests.

        Returns:
            (autorisé: bool, raison: str)
        """
        exposure = self._calculer_exposition(
            _balance=_balance, _positions=_positions
        )

        # Règle 1 — Exposition maximale atteinte
        if exposure.est_a_max:
            return (
                False,
                f"Exposition maximale atteinte : "
                f"{exposure.exposition_totale_pct:.2f}% / "
                f"{self.MAX_EXPOSITION_PORTEFEUILLE_PCT}% | "
                f"Impossible d'ouvrir {symbol}",
            )

        # Règle 2 — Budget suffisant
        if risk_pct > exposure.budget_disponible_pct:
            return (
                False,
                f"Budget insuffisant : {exposure.budget_disponible_pct:.2f}% "
                f"disponible < {risk_pct}% requis pour {symbol}",
            )

        # Règle 3 — Corrélations dangereuses
        symboles_ouverts = [
            s for s, pct in exposure.exposition_par_symbole.items()
            if pct > 0
        ]
        corr_bloque, corr_raison = self.moniteur_correlation.verifier_nouveau_trade(
            symbol, symboles_ouverts
        )
        if corr_bloque:
            return False, corr_raison

        # Règle 4 — Drawdown global journalier
        dd_bloque, dd_raison = self._verifier_drawdown_global(_balance)
        if dd_bloque:
            return False, dd_raison

        exposition_apres = exposure.exposition_totale_pct + risk_pct
        return (
            True,
            f"Trade autorisé | Exposition après: {exposition_apres:.2f}% "
            f"/ {self.MAX_EXPOSITION_PORTEFEUILLE_PCT}% | OB: {ob_score}/100",
        )

    # ── Calcul de l'exposition ─────────────────────────────────────────────

    def _calculer_exposition(
        self,
        _balance: Optional[float] = None,
        _positions: Optional[Dict[str, float]] = None,
    ) -> ExpositionPortefeuille:
        """
        Calcule l'exposition actuelle du portefeuille.

        En mode live : lit depuis MT5.
        En mode test : utilise les overrides _balance et _positions.
        """
        # ── Lecture du compte ──────────────────────────────────────────────
        balance = _balance
        if balance is None:
            try:
                import MetaTrader5 as mt5
                account = mt5.account_info()
                balance = float(account.balance) if account else 10000.0
            except (ImportError, TypeError, AttributeError):
                balance = 10000.0

        # ── Positions ouvertes ─────────────────────────────────────────────
        if _positions is not None:
            # Override de test — positions fournies directement en %
            exposition_par_symbole = dict(_positions)
            total_risque_usd = sum(
                pct / 100.0 * balance
                for pct in exposition_par_symbole.values()
            )
        else:
            exposition_par_symbole, total_risque_usd = (
                self._lire_positions_mt5(balance)
            )

        # ── Calculs dérivés ────────────────────────────────────────────────
        total_pct = (
            total_risque_usd / balance * 100 if balance > 0 else 0.0
        )
        disponible = max(0.0, self.MAX_EXPOSITION_PORTEFEUILLE_PCT - total_pct)

        # Paires corrélées actives
        symboles_ouverts = [
            s for s, pct in exposition_par_symbole.items() if pct > 0
        ]
        paires_correlees = self._detecter_paires_correlees_actives(symboles_ouverts)

        return ExpositionPortefeuille(
            timestamp_utc=datetime.utcnow(),
            exposition_totale_pct=round(total_pct, 3),
            exposition_totale_usd=round(total_risque_usd, 2),
            balance_compte=balance,
            exposition_par_symbole=exposition_par_symbole,
            budget_disponible_pct=round(disponible, 3),
            est_a_max=total_pct >= self.MAX_EXPOSITION_PORTEFEUILLE_PCT,
            paires_correlees_actives=paires_correlees,
        )

    def _lire_positions_mt5(
        self, balance: float
    ) -> Tuple[Dict[str, float], float]:
        """Lit les positions ouvertes depuis MT5 et calcule le risque."""
        exposition: Dict[str, float] = {}
        total_risque_usd = 0.0

        for symbole_cle, config in self.configs.items():
            symbole_mt5 = getattr(config, "SYMBOLE", symbole_cle)
            exposition[symbole_cle] = 0.0

            try:
                import MetaTrader5 as mt5
                positions = mt5.positions_get(symbol=symbole_mt5)
                if not positions:
                    continue

                risque_symbole_usd = 0.0
                for pos in positions:
                    try:
                        sl = float(pos.sl)
                        price_open = float(pos.price_open)
                        volume = float(pos.volume)
                        if sl <= 0:
                            continue
                        sl_dist = abs(price_open - sl)
                        info = mt5.symbol_info(symbole_mt5)
                        if info:
                            tick_val = float(info.trade_tick_value)
                            tick_sz = float(info.trade_tick_size)
                            risque = sl_dist * volume * tick_val / tick_sz
                            risque_symbole_usd += risque
                    except (TypeError, AttributeError):
                        continue

                pct = (risque_symbole_usd / balance * 100) if balance > 0 else 0.0
                exposition[symbole_cle] = round(pct, 3)
                total_risque_usd += risque_symbole_usd

            except (ImportError, Exception) as e:
                logger.debug(
                    f"Lecture positions MT5 {symbole_mt5} non disponible : {e}"
                )

        return exposition, total_risque_usd

    # ── Drawdown global ────────────────────────────────────────────────────

    def _verifier_drawdown_global(
        self, _balance: Optional[float] = None
    ) -> Tuple[bool, str]:
        """Vérifie le drawdown global journalier du portefeuille."""
        if self._equity_debut_journee <= 0:
            return False, ""

        try:
            import MetaTrader5 as mt5
            account = mt5.account_info()
            equity_actuelle = float(account.equity) if account else (
                _balance or self._equity_debut_journee
            )
        except (ImportError, TypeError, AttributeError):
            if _balance is not None:
                equity_actuelle = _balance
            else:
                return False, ""

        dd_pct = (
            (self._equity_debut_journee - equity_actuelle)
            / self._equity_debut_journee * 100
        )

        if dd_pct > self.MAX_DD_JOURNALIER_PORTEFEUILLE_PCT:
            self._fermeture_urgence_portefeuille(dd_pct)
            return (
                True,
                f"DRAWDOWN GLOBAL CRITIQUE : {dd_pct:.2f}% > "
                f"limite {self.MAX_DD_JOURNALIER_PORTEFEUILLE_PCT}% | "
                f"Toutes les positions fermées",
            )

        return False, ""

    def _fermeture_urgence_portefeuille(self, dd_pct: float) -> None:
        """Ferme d'urgence toutes les positions sur tous les actifs."""
        logger.critical(
            f"⛔ FERMETURE D'URGENCE PORTEFEUILLE | DD: {dd_pct:.2f}%"
        )
        if self.notifier is not None:
            try:
                from telegram_notifier import NiveauAlerte
                self.notifier.send(
                    f"⛔ <b>FERMETURE D'URGENCE — PORTEFEUILLE</b>\n"
                    f"Drawdown global journalier : {dd_pct:.2f}%\n"
                    f"Limite : {self.MAX_DD_JOURNALIER_PORTEFEUILLE_PCT}%\n"
                    f"Toutes les positions fermées sur tous les actifs.",
                    level=NiveauAlerte.CRITICAL,
                )
            except Exception:
                pass

        # Tenter la fermeture MT5
        for symbole_cle, config in self.configs.items():
            symbole_mt5 = getattr(config, "SYMBOLE", symbole_cle)
            try:
                import MetaTrader5 as mt5
                positions = mt5.positions_get(symbol=symbole_mt5)
                if not positions:
                    continue
                for pos in positions:
                    try:
                        type_fermeture = (
                            mt5.ORDER_TYPE_SELL
                            if int(pos.type) == mt5.ORDER_TYPE_BUY
                            else mt5.ORDER_TYPE_BUY
                        )
                        tick = mt5.symbol_info_tick(symbole_mt5)
                        prix = (
                            tick.bid
                            if int(pos.type) == mt5.ORDER_TYPE_BUY
                            else tick.ask
                        )
                        mt5.order_send({
                            "action": mt5.TRADE_ACTION_DEAL,
                            "symbol": symbole_mt5,
                            "volume": float(pos.volume),
                            "type": type_fermeture,
                            "position": int(pos.ticket),
                            "price": prix,
                            "deviation": 20,
                            "magic": 20250199,
                            "comment": "PORTFOLIO_EMERGENCY_CLOSE",
                            "type_time": mt5.ORDER_TIME_GTC,
                            "type_filling": mt5.ORDER_FILLING_IOC,
                        })
                        logger.info(
                            f"Position urgence fermée : {symbole_mt5} "
                            f"#{int(pos.ticket)}"
                        )
                    except Exception as e:
                        logger.error(
                            f"Fermeture urgence {symbole_mt5} échouée : {e}"
                        )
            except (ImportError, Exception) as e:
                logger.error(
                    f"Fermeture urgence MT5 non disponible : {e}"
                )

    # ── Corrélations ──────────────────────────────────────────────────────

    def _detecter_paires_correlees_actives(
        self, symboles_ouverts: List[str]
    ) -> List[str]:
        """Détecte les paires corrélées parmi les positions ouvertes."""
        paires = []
        paires_bloquees = self.moniteur_correlation.get_paires_bloquees()
        for s1, s2, coeff in paires_bloquees:
            if s1 in symboles_ouverts and s2 in symboles_ouverts:
                paires.append(f"{s1}+{s2} ({coeff:.2f})")
        return paires

    # ── Interface publique ─────────────────────────────────────────────────

    def get_portfolio_status(
        self,
        _balance: Optional[float] = None,
        _positions: Optional[Dict[str, float]] = None,
    ) -> dict:
        """Retourne le statut complet pour le dashboard multi-actif."""
        exposure = self._calculer_exposition(_balance, _positions)

        dd_pct = 0.0
        if self._equity_debut_journee > 0:
            try:
                import MetaTrader5 as mt5
                account = mt5.account_info()
                eq = float(account.equity) if account else exposure.balance_compte
                dd_pct = (
                    (self._equity_debut_journee - eq)
                    / self._equity_debut_journee * 100
                )
            except (ImportError, TypeError, AttributeError):
                pass

        return {
            "exposition_totale_pct": exposure.exposition_totale_pct,
            "budget_disponible_pct": exposure.budget_disponible_pct,
            "exposition_par_symbole": exposure.exposition_par_symbole,
            "paires_correlees": exposure.paires_correlees_actives,
            "dd_global_journalier_pct": round(max(0.0, dd_pct), 2),
            "est_a_max": exposure.est_a_max,
            "max_exposition_pct": self.MAX_EXPOSITION_PORTEFEUILLE_PCT,
        }

    # Alias anglais
    def get_portfolio_status_en(
        self,
        balance: Optional[float] = None,
        positions: Optional[Dict[str, float]] = None,
    ) -> dict:
        return self.get_portfolio_status(balance, positions)

    def reset_daily_equity(self) -> None:
        """Reset de l'equity de référence journalière à minuit UTC."""
        try:
            import MetaTrader5 as mt5
            account = mt5.account_info()
            if account:
                self._equity_debut_journee = float(account.equity)
                logger.info(
                    f"Portfolio : equity journalière réinitialisée "
                    f"à {self._equity_debut_journee:.2f}$"
                )
        except (ImportError, TypeError, AttributeError):
            pass

    def initialiser_equity_journaliere(
        self, equity: Optional[float] = None
    ) -> None:
        """Initialise l'equity de référence (appelé au démarrage)."""
        if equity is not None:
            self._equity_debut_journee = equity
            return
        self.reset_daily_equity()


# Alias anglais
PortfolioRiskManager = GestionnaireRisquePortefeuille
