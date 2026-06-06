"""
test_risk_manager.py — Tests unitaires du gestionnaire de risque.
Vérifie le position sizing, les règles de drawdown et le circuit breaker.
"""

import time
import pytest
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent))

from risk_manager import GestionnaireRisque, EtatRisque
from config import CONFIG
from tests.fixtures import creer_symbole_info_mock


class TestCalculLotSize:
    """Tests du calcul du lot size basé sur le risque %."""

    def setup_method(self):
        self.gestionnaire = GestionnaireRisque()

    @patch("MetaTrader5.symbol_info")
    def test_lot_size_calcul_1pct_risque(self, mock_symbol_info):
        """Le lot size doit correspondre exactement à 1% du capital risqué."""
        mock_symbol_info.return_value = creer_symbole_info_mock()

        capital = 10_000.0
        risque_pct = 1.0
        sl_points = 200.0  # 200 pips

        # Avec tick_value=1.0, 200 pips × 1$ × lot = 200 × lot
        # 1% de 10000 = 100$
        # lot = 100 / (200 × 1) = 0.50
        lot = self.gestionnaire.calculer_lot_size(capital, risque_pct, sl_points, "XAUUSD")
        assert abs(lot - 0.50) < 0.01, f"Lot attendu ~0.50, obtenu {lot}"

    @patch("MetaTrader5.symbol_info")
    def test_lot_size_min_volume_respecte(self, mock_symbol_info):
        """Le lot size ne doit jamais être inférieur au volume minimum du broker."""
        info = creer_symbole_info_mock()
        info.volume_min = 0.01
        mock_symbol_info.return_value = info

        # SL très large → lot calculé pourrait être < 0.01
        lot = self.gestionnaire.calculer_lot_size(1000.0, 0.1, 50000.0, "XAUUSD")
        assert lot >= 0.01, f"Lot {lot} inférieur au volume minimum"

    @patch("MetaTrader5.symbol_info")
    def test_lot_size_max_volume_respecte(self, mock_symbol_info):
        """Le lot size ne doit jamais dépasser le volume maximum."""
        info = creer_symbole_info_mock()
        info.volume_max = 100.0
        mock_symbol_info.return_value = info

        # Gros capital, SL très petit → lot calculé pourrait dépasser 100
        lot = self.gestionnaire.calculer_lot_size(10_000_000.0, 2.0, 1.0, "XAUUSD")
        assert lot <= 100.0, f"Lot {lot} dépasse le volume maximum"

    @patch("MetaTrader5.symbol_info")
    def test_lot_size_sl_zero_retourne_zero(self, mock_symbol_info):
        """Un SL de 0 ne doit pas provoquer de division par zéro."""
        mock_symbol_info.return_value = creer_symbole_info_mock()
        lot = self.gestionnaire.calculer_lot_size(10_000.0, 1.0, 0.0, "XAUUSD")
        assert lot == 0.0, "Lot doit être 0 si SL est 0"


class TestDrawdown:
    """Tests des vérifications de drawdown."""

    def setup_method(self):
        self.gestionnaire = GestionnaireRisque()
        self.gestionnaire.initialiser_session(10_000.0)

    def test_drawdown_journalier_ok(self):
        """Le trading doit être autorisé si le drawdown journalier est dans les limites."""
        equity_ok = 9_800.0  # -2% < 3% limite
        self.gestionnaire.mettre_a_jour_drawdown(equity_ok)
        assert self.gestionnaire.verifier_drawdown_journalier() is True

    def test_drawdown_journalier_bloquant(self):
        """Le trading doit être refusé si le drawdown journalier dépasse la limite."""
        equity_depasse = 9_600.0  # -4% > 3% limite
        self.gestionnaire.mettre_a_jour_drawdown(equity_depasse)
        assert self.gestionnaire.verifier_drawdown_journalier() is False

    def test_drawdown_total_arret_urgence(self):
        """Le bot doit s'arrêter si le drawdown total dépasse 10%."""
        self.gestionnaire.etat.balance_depart_total = 10_000.0
        equity_critique = 8_900.0  # -11% > 10% limite
        self.gestionnaire.mettre_a_jour_drawdown(equity_critique)

        # Le vérificateur doit retourner False ET setter bot_arrete
        result = self.gestionnaire.verifier_drawdown_total()
        assert result is False
        assert self.gestionnaire.etat.bot_arrete is True

    def test_trading_autorise_conditions_ok(self):
        """Le trading doit être autorisé quand toutes les conditions sont respectées."""
        equity_ok = 9_850.0  # -1.5% → dans les limites
        result = self.gestionnaire.trading_autorise(equity_ok)
        assert result is True

    def test_trading_interdit_bot_arrete(self):
        """Le trading doit être refusé si le bot est en état d'arrêt."""
        self.gestionnaire.etat.bot_arrete = True
        result = self.gestionnaire.trading_autorise(9_000.0)
        assert result is False


class TestCircuitBreaker:
    """Tests du circuit breaker après pertes consécutives."""

    def setup_method(self):
        self.gestionnaire = GestionnaireRisque()
        self.gestionnaire.initialiser_session(10_000.0)

    def test_circuit_breaker_inactif_par_defaut(self):
        """Le circuit breaker doit être inactif au démarrage."""
        assert self.gestionnaire.etat.circuit_breaker_actif is False

    def test_circuit_breaker_active_apres_n_pertes(self):
        """Le circuit breaker doit s'activer après N pertes consécutives."""
        n_pertes = CONFIG.CIRCUIT_BREAKER_PERTES_CONSECUTIVES
        for _ in range(n_pertes):
            self.gestionnaire.enregistrer_resultat_trade(-100.0)

        assert self.gestionnaire.etat.circuit_breaker_actif is True

    def test_circuit_breaker_reset_apres_gain(self):
        """Un gain doit remettre le compteur de pertes consécutives à zéro."""
        for _ in range(CONFIG.CIRCUIT_BREAKER_PERTES_CONSECUTIVES - 1):
            self.gestionnaire.enregistrer_resultat_trade(-100.0)

        self.gestionnaire.enregistrer_resultat_trade(200.0)  # Gain
        assert self.gestionnaire.etat.pertes_consecutives == 0
        assert self.gestionnaire.etat.circuit_breaker_actif is False

    def test_circuit_breaker_expire_apres_delai(self):
        """Le circuit breaker doit se désactiver après la durée de pause."""
        # Activer le circuit breaker avec expiration très courte
        self.gestionnaire.etat.circuit_breaker_actif = True
        self.gestionnaire.etat.circuit_breaker_fin_timestamp = time.time() - 1  # Déjà expiré

        result = self.gestionnaire.verifier_circuit_breaker()
        assert result is True  # Trading autorisé
        assert self.gestionnaire.etat.circuit_breaker_actif is False


class TestCalculSLTP:
    """Tests du calcul des niveaux SL/TP."""

    def setup_method(self):
        self.gestionnaire = GestionnaireRisque()

    def test_sl_tp_long_coherents(self):
        """Pour un LONG, SL < entrée < TP1 < TP2."""
        sl, tp1, tp2, tp3, sl_dist = self.gestionnaire.calculer_sl_tp(
            prix_entree=2020.0,
            prix_ob_bas=2015.0,
            prix_ob_haut=2018.0,
            atr_m15=3.0,
            direction="LONG",
        )
        assert sl is not None, "SL ne doit pas être None"
        assert sl < 2020.0, f"SL {sl} doit être inférieur à l'entrée 2020"
        assert tp1 > 2020.0, f"TP1 {tp1} doit être supérieur à l'entrée"
        assert tp2 > tp1, f"TP2 {tp2} doit être supérieur à TP1 {tp1}"

    def test_sl_tp_short_coherents(self):
        """Pour un SHORT, SL > entrée > TP1 > TP2."""
        sl, tp1, tp2, tp3, sl_dist = self.gestionnaire.calculer_sl_tp(
            prix_entree=2050.0,
            prix_ob_bas=2052.0,
            prix_ob_haut=2055.0,
            atr_m15=3.0,
            direction="SHORT",
        )
        assert sl is not None, "SL ne doit pas être None"
        assert sl > 2050.0, f"SL {sl} doit être supérieur à l'entrée 2050"
        assert tp1 < 2050.0, f"TP1 {tp1} doit être inférieur à l'entrée"
        assert tp2 < tp1, f"TP2 {tp2} doit être inférieur à TP1 {tp1}"

    def test_rr_minimum_respecte(self):
        """Le R:R effectif doit être supérieur ou égal au minimum configuré."""
        sl, tp1, tp2, tp3, sl_dist = self.gestionnaire.calculer_sl_tp(
            prix_entree=2020.0,
            prix_ob_bas=2015.0,
            prix_ob_haut=2018.0,
            atr_m15=3.0,
            direction="LONG",
        )
        if sl is not None and sl_dist > 0:
            rr_effectif = (tp2 - 2020.0) / sl_dist
            assert rr_effectif >= CONFIG.RR_MINIMUM, \
                f"R:R {rr_effectif:.2f} inférieur au minimum {CONFIG.RR_MINIMUM}"

    def test_sl_invalide_retourne_none(self):
        """Un SL mal positionné (au-dessus de l'entrée pour un LONG) doit retourner None."""
        # OB au-dessus de l'entrée → SL sera > entrée → invalide
        sl, tp1, tp2, tp3, sl_dist = self.gestionnaire.calculer_sl_tp(
            prix_entree=2010.0,
            prix_ob_bas=2015.0,   # OB bas > entrée → SL > entrée
            prix_ob_haut=2020.0,
            atr_m15=1.0,
            direction="LONG",
        )
        assert sl is None, "SL invalide (> entrée LONG) doit retourner None"
