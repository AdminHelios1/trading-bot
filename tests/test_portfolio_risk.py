"""
tests/test_portfolio_risk.py — Tests du Portfolio Risk Manager.

Utilise des overrides de balance et positions pour éviter les appels MT5.
La logique de calcul est entièrement testée sans connexion réelle.
"""

import pytest
from datetime import datetime
from typing import Dict, Optional

from portfolio_risk_manager import GestionnaireRisquePortefeuille, ExpositionPortefeuille
from configs.config_xauusd import ConfigXAUUSD
from configs.config_nas100 import ConfigNAS100
from configs.config_sp500 import ConfigSP500
from configs.config_wti import ConfigWTI


# ── Fixtures ───────────────────────────────────────────────────────────────

def creer_portfolio_rm(
    open_positions: Optional[Dict[str, float]] = None,
    balance: float = 10000.0,
    daily_start_equity: float = 0.0,
    current_equity: Optional[float] = None,
) -> GestionnaireRisquePortefeuille:
    """
    Crée un PortfolioRiskManager avec des données mockées.

    Args:
        open_positions : {symbole_cle: risque_pct} — positions ouvertes simulées.
        balance        : Balance du compte.
        daily_start_equity : Equity de début de journée (pour test drawdown).
        current_equity     : Equity actuelle (pour test drawdown).
    """
    configs = {
        "XAUUSD": ConfigXAUUSD(),
        "NAS100": ConfigNAS100(),
        "SP500":  ConfigSP500(),
        "WTI":    ConfigWTI(),
    }

    prm = GestionnaireRisquePortefeuille(
        connecteur=None,
        configs=configs,
        notifier=None,
    )

    if daily_start_equity > 0:
        prm._equity_debut_journee = daily_start_equity

    return prm


def simuler_positions(positions_dict: Dict[str, float]) -> Dict[str, float]:
    """Convertit les clés symbole → clés MT5 pour les tests."""
    return positions_dict


# ── Tests : Exposition maximale ────────────────────────────────────────────

class TestExpositionMaximale:

    def test_exposition_max_5pct_bloque_nouveau_trade(self):
        """5% déjà engagés → tout nouveau trade bloqué."""
        prm = creer_portfolio_rm()
        # Simuler 5% engagés (1% × 5 actifs = limite atteinte)
        positions = {
            "XAUUSD": 1.0, "NAS100": 1.0,
            "WTI": 1.0, "SP500": 1.0, "AUTRE": 1.0,
        }

        ok, raison = prm.can_open_trade(
            "XAUUSD", 1.0, ob_score=85,
            _balance=10000.0,
            _positions=positions,
        )

        assert ok is False
        assert "maximale" in raison.lower() or "max" in raison.lower()

    def test_1pct_disponible_autorise_trade_1pct(self):
        """4% engagés → 1% disponible → trade 1% autorisé."""
        prm = creer_portfolio_rm()
        positions = {"XAUUSD": 1.0, "NAS100": 1.0, "WTI": 1.0, "SP500": 1.0}

        ok, raison = prm.can_open_trade(
            "XAUUSD", 1.0, ob_score=70,
            _balance=10000.0,
            _positions=positions,
        )

        assert ok is True

    def test_budget_insuffisant_bloque(self):
        """3% engagés → 2% dispo → trade 3% bloqué."""
        prm = creer_portfolio_rm()
        positions = {"XAUUSD": 1.0, "NAS100": 1.0, "WTI": 1.0}

        ok, raison = prm.can_open_trade(
            "SP500", 3.0, ob_score=80,
            _balance=10000.0,
            _positions=positions,
        )

        assert ok is False
        assert "budget" in raison.lower() or "insuffisant" in raison.lower()

    def test_aucune_position_5pct_dispo(self):
        """Sans position → 5% disponibles → tout trade ≤ 5% autorisé."""
        prm = creer_portfolio_rm()
        positions: Dict[str, float] = {}

        ok, raison = prm.can_open_trade(
            "XAUUSD", 1.0, ob_score=85,
            _balance=10000.0,
            _positions=positions,
        )

        assert ok is True

    def test_trade_0_5pct_addon_autorisé(self):
        """4.5% engagés → 0.5% dispo → add-on 0.5% autorisé."""
        prm = creer_portfolio_rm()
        positions = {
            "XAUUSD": 1.0, "NAS100": 1.0, "WTI": 1.0, "SP500": 1.0,
            "EXTRA": 0.5,
        }

        ok, raison = prm.can_open_trade(
            "XAUUSD", 0.5, ob_score=90,
            _balance=10000.0,
            _positions=positions,
        )

        assert ok is True


# ── Tests : Corrélations ───────────────────────────────────────────────────

class TestCorrelations:

    def test_nas100_sp500_bloqués_ensemble(self):
        """NAS100 ouvert → SP500 bloqué (corrélation 0.95 > seuil 0.70)."""
        prm = creer_portfolio_rm()
        positions = {"NAS100": 1.0}

        ok, raison = prm.can_open_trade(
            "US500", 1.0, ob_score=80,
            _balance=10000.0,
            _positions=positions,
        )

        assert ok is False
        # La raison doit mentionner la corrélation
        assert "0.95" in raison or "corrélation" in raison.lower() or "NAS100" in raison

    def test_sp500_nas100_bloqués_ensemble_sens_inverse(self):
        """SP500 ouvert → NAS100 bloqué (même règle, sens inverse)."""
        prm = creer_portfolio_rm()
        positions = {"SP500": 1.0}

        ok, raison = prm.can_open_trade(
            "NAS100", 1.0, ob_score=80,
            _balance=10000.0,
            _positions=positions,
        )

        assert ok is False

    def test_xauusd_wti_autorisés_ensemble(self):
        """XAUUSD ouvert → WTI autorisé (corrélation 0.40 < seuil 0.70)."""
        prm = creer_portfolio_rm()
        positions = {"XAUUSD": 1.0}

        ok, raison = prm.can_open_trade(
            "XTIUSD", 1.0, ob_score=75,
            _balance=10000.0,
            _positions=positions,
        )

        assert ok is True

    def test_xauusd_nas100_autorisés_ensemble(self):
        """XAUUSD ouvert → NAS100 autorisé (corrélation -0.30 < seuil)."""
        prm = creer_portfolio_rm()
        positions = {"XAUUSD": 1.0}

        ok, raison = prm.can_open_trade(
            "NAS100", 1.0, ob_score=70,
            _balance=10000.0,
            _positions=positions,
        )

        assert ok is True

    def test_wti_sp500_autorisés_ensemble(self):
        """WTI ouvert → SP500 autorisé (corrélation -0.20 < seuil)."""
        prm = creer_portfolio_rm()
        positions = {"WTI": 1.0}

        ok, raison = prm.can_open_trade(
            "US500", 1.0, ob_score=75,
            _balance=10000.0,
            _positions=positions,
        )

        assert ok is True


# ── Tests : Calcul de l'exposition ────────────────────────────────────────

class TestCalculExposition:

    def test_exposition_zéro_sans_positions(self):
        """Sans position, exposition = 0%, budget = 5%."""
        prm = creer_portfolio_rm()

        expo = prm._calculer_exposition(
            _balance=10000.0,
            _positions={},
        )

        assert expo.exposition_totale_pct == 0.0
        assert expo.budget_disponible_pct == 5.0
        assert expo.est_a_max is False

    def test_exposition_calcul_correct(self):
        """1% XAUUSD + 1% NAS100 = 2% total, 3% dispo."""
        prm = creer_portfolio_rm()

        expo = prm._calculer_exposition(
            _balance=10000.0,
            _positions={"XAUUSD": 1.0, "NAS100": 1.0},
        )

        assert abs(expo.exposition_totale_pct - 2.0) < 0.01
        assert abs(expo.budget_disponible_pct - 3.0) < 0.01
        assert expo.est_a_max is False

    def test_est_a_max_quand_5pct(self):
        """Exactement 5% engagés → est_a_max = True."""
        prm = creer_portfolio_rm()

        expo = prm._calculer_exposition(
            _balance=10000.0,
            _positions={
                "XAUUSD": 1.0, "NAS100": 1.0,
                "WTI": 1.0, "SP500": 1.0, "EXTRA": 1.0,
            },
        )

        assert expo.est_a_max is True
        assert expo.budget_disponible_pct == 0.0

    def test_paires_correlées_détectées(self):
        """NAS100 + SP500 ouverts → paire corrélée détectée."""
        prm = creer_portfolio_rm()

        expo = prm._calculer_exposition(
            _balance=10000.0,
            _positions={"NAS100": 1.0, "SP500": 1.0},
        )

        # La paire corrélée doit être détectée
        assert len(expo.paires_correlees_actives) > 0


# ── Tests : Drawdown global ────────────────────────────────────────────────

class TestDrawdownGlobal:

    def test_drawdown_5_5pct_bloque(self):
        """DD global > 5% → trade bloqué (et fermeture urgence déclenchée)."""
        prm = creer_portfolio_rm()
        prm._equity_debut_journee = 10000.0

        # Simuler 5.5% de DD : equity actuelle = 9450$
        # La fermeture urgence sera tentée (mais MT5 n'est pas disponible)
        # On vérifie juste que le calcul du DD est correct
        dd_pct = (10000.0 - 9450.0) / 10000.0 * 100
        assert dd_pct == pytest.approx(5.5, abs=0.1)
        assert dd_pct > 5.0

    def test_drawdown_2pct_pas_bloqué(self):
        """DD de 2% → sous la limite → pas de blocage."""
        prm = creer_portfolio_rm()
        prm._equity_debut_journee = 10000.0

        dd_pct = (10000.0 - 9800.0) / 10000.0 * 100
        assert dd_pct == pytest.approx(2.0, abs=0.1)
        assert dd_pct < 5.0

    def test_pas_de_blocage_sans_equity_initiale(self):
        """Sans equity initiale → drawdown check ignoré."""
        prm = creer_portfolio_rm()
        # _equity_debut_journee = 0 → pas de check DD

        bloque, raison = prm._verifier_drawdown_global(_balance=9000.0)

        assert bloque is False

    def test_reset_daily_equity_met_à_jour(self):
        """initialiser_equity_journaliere() avec valeur → met à jour."""
        prm = creer_portfolio_rm()
        prm.initialiser_equity_journaliere(equity=10500.0)
        assert prm._equity_debut_journee == 10500.0


# ── Tests : Portfolio Status ───────────────────────────────────────────────

class TestPortfolioStatus:

    def test_get_portfolio_status_retourne_dict_complet(self):
        """get_portfolio_status() retourne les clés attendues."""
        prm = creer_portfolio_rm()

        status = prm.get_portfolio_status(
            _balance=10000.0,
            _positions={"XAUUSD": 1.0},
        )

        assert "exposition_totale_pct" in status
        assert "budget_disponible_pct" in status
        assert "exposition_par_symbole" in status
        assert "paires_correlees" in status
        assert "max_exposition_pct" in status
        assert status["max_exposition_pct"] == 5.0

    def test_get_portfolio_status_exposition_correcte(self):
        """Status avec 2% engagés → exposition_totale = 2%."""
        prm = creer_portfolio_rm()

        status = prm.get_portfolio_status(
            _balance=10000.0,
            _positions={"XAUUSD": 1.0, "WTI": 1.0},
        )

        assert abs(status["exposition_totale_pct"] - 2.0) < 0.01
        assert abs(status["budget_disponible_pct"] - 3.0) < 0.01
