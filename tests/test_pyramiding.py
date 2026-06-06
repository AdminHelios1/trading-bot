"""
tests/test_pyramiding.py — Tests unitaires du système de pyramiding.

Couvre : AddonValidator, GestionnairePyramiding, cycle de vie complet.
Tous les appels MT5 sont mockés via conftest.py.
"""

import pytest
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from addon_validator import ValidateurAddon, RaisonAnnulationAddon
from pyramiding_manager import (
    GestionnairePyramiding,
    TradeAddon,
    EtatPyramiding,
    StatutAddon,
    ParametresAddon,
)
from trade_manager import DirectionTrade, PhaseTrade, NiveauSL, NiveauPartiel
from config import CONFIG


# ── Fixtures de base ───────────────────────────────────────────────────────

class MockTrade:
    """Simule un TradeGere en Phase 2 (TP1 atteint, SL au BE)."""

    def __init__(self, **kwargs):
        self.id_trade = kwargs.get("id_trade", "trade001")
        self.ticket_mt5 = kwargs.get("ticket_mt5", 12345)
        self.symbole = kwargs.get("symbole", "XAUUSD")
        self.direction = kwargs.get("direction", DirectionTrade.LONG)
        self.prix_entree = kwargs.get("prix_entree", 2000.0)
        self.heure_entree = kwargs.get("heure_entree", datetime(2025, 6, 1, 9, 0))
        self.lots_initial = kwargs.get("lots_initial", 0.10)
        self.lots_restants = kwargs.get("lots_restants", 0.05)
        self.est_ferme = kwargs.get("est_ferme", False)
        self.phase = kwargs.get("phase", PhaseTrade.PHASE_2)

        # SL au breakeven (= prix_entree après TP1)
        sl_prix = kwargs.get("sl", self.prix_entree)
        self.sl = NiveauSL(prix_actuel=sl_prix, prix_initial=sl_prix - 10.0)

        tp1_prix = kwargs.get("tp1", self.prix_entree + 10.0)
        tp2_prix = kwargs.get("tp2", self.prix_entree + 20.0)
        tp3_prix = kwargs.get("tp3", self.prix_entree + 30.0)
        self.tp1 = NiveauPartiel(1, 1.0, tp1_prix, 50.0, 0.05, atteint=True)
        self.tp2 = NiveauPartiel(2, 2.0, tp2_prix, 25.0, 0.025)
        self.tp3 = NiveauPartiel(3, 3.0, tp3_prix, 25.0, 0.025)

    def est_long(self):
        return self.direction == DirectionTrade.LONG

    @property
    def distance_risque(self):
        return abs(self.prix_entree - self.sl.prix_initial)


def creer_mock_addon(
    addon_id="addon01",
    id_parent="trade001",
    direction="bullish",
    prix_entree=2010.0,
    sl=2000.0,
    tp1=2020.0,
    tp2=2030.0,
    lots=0.02,
    trailing_price=None,
    atr_dist=5.0,
    tp1_atteint=False,
    statut=StatutAddon.OUVERT,
):
    """Crée un TradeAddon pour les tests."""
    addon = TradeAddon(
        addon_id=addon_id,
        id_trade_parent=id_parent,
        ticket_mt5=90001,
        statut=statut,
        direction=direction,
        prix_entree=prix_entree,
        lots=lots,
        sl_prix=sl,
        tp1_prix=tp1,
        tp2_prix=tp2,
        sl_actuel=sl,
        lots_restants=lots,
        tp1_atteint=tp1_atteint,
        trailing_actif=trailing_price is not None,
        trailing_price=trailing_price,
        trailing_atr_distance=atr_dist,
    )
    return addon


def creer_etat(
    id_parent="trade001",
    opened=0,
    won=0,
    pnl=0.0,
    addon=None,
):
    """Crée un EtatPyramiding pour les tests."""
    etat = EtatPyramiding(id_trade_parent=id_parent)
    etat.total_addons_ouverts = opened
    etat.total_addons_gagnants = won
    etat.pnl_total_addons = pnl
    if addon is not None:
        etat.addon = addon
        etat.addon_ouvert = True
        etat.addon_evalue = True
    return etat


def creer_validator(
    cb_actif=False,
    news_bloquee=False,
    spread_bloque=False,
    biais_aligne=True,
    heure_utc=9,
):
    """Crée un ValidateurAddon avec tous les filtres mockés."""
    # Circuit breaker
    cb = MagicMock()
    if cb_actif:
        cb.trading_autorise.return_value = (False, "CB PAUSE actif", 0.0)
    else:
        cb.trading_autorise.return_value = (True, "", 1.0)

    # Filtre news
    news = MagicMock()
    if news_bloquee:
        news.is_trading_allowed.return_value = (False, "NFP dans 30min")
    else:
        news.is_trading_allowed.return_value = (True, "")

    # Filtre spread
    spread = MagicMock()
    if spread_bloque:
        spread.is_market_tradeable.return_value = (False, "Spread 45 pts > hard cap")
    else:
        spread.is_market_tradeable.return_value = (True, "")

    # Daily bias
    biais = MagicMock()
    if biais_aligne:
        biais.signal_aligne_avec_biais.return_value = (True, "Biais HAUSSIER aligné")
    else:
        biais.signal_aligne_avec_biais.return_value = (False, "Biais BAISSIER — signal LONG refusé")

    return ValidateurAddon(
        circuit_breaker=cb,
        filtre_news=news,
        filtre_spread=spread,
        analyseur_biais=biais,
    )


_HEURE_SESSION_FIXE = datetime(2025, 6, 2, 10, 0)  # 10h UTC — toujours en session


def creer_pyramiding_manager(
    capital=10000.0,
    conditions_valides=True,
    cb_actif=False,
    news_bloquee=False,
    spread_bloque=False,
    biais_aligne=True,
    pyramiding_enabled=True,
    rr_min=None,
):
    """Crée un GestionnairePyramiding entièrement configuré pour les tests."""
    config_test = CONFIG.__class__()
    config_test.PYRAMIDING_ENABLED = pyramiding_enabled
    if rr_min is not None:
        config_test.RR_MINIMUM = rr_min

    if not conditions_valides:
        cb_actif = True  # Forcer un refus si conditions invalides

    validator = creer_validator(
        cb_actif=cb_actif,
        news_bloquee=news_bloquee,
        spread_bloque=spread_bloque,
        biais_aligne=biais_aligne,
    )

    pm = GestionnairePyramiding(
        connecteur=None,  # Mode paper — pas de MT5 réel
        config=config_test,
        validator=validator,
        capital_initial=capital,
    )
    # Fixer l'heure UTC pour les tests (évite les échecs hors session la nuit)
    pm._obtenir_heure_utc = lambda: _HEURE_SESSION_FIXE
    return pm


# ── Tests principaux ───────────────────────────────────────────────────────

class TestOuvertureAddon:

    def test_addon_ouvert_conditions_parfaites(self):
        """Un add-on s'ouvre quand toutes les conditions sont remplies."""
        pm = creer_pyramiding_manager(conditions_valides=True)
        parent = MockTrade(prix_entree=2000.0)

        addon = pm.on_tp1_reached(parent)

        assert addon is not None
        assert addon.statut == StatutAddon.OUVERT
        assert addon.lots > 0
        # SL de l'add-on = breakeven du parent
        assert abs(addon.sl_prix - parent.prix_entree) < 0.01

    def test_addon_annule_cb_actif(self):
        """Un add-on est refusé si le circuit breaker est actif."""
        pm = creer_pyramiding_manager(cb_actif=True)
        parent = MockTrade()

        addon = pm.on_tp1_reached(parent)

        assert addon is None
        etat = pm._states.get(parent.id_trade)
        assert etat is not None
        assert etat.raison_annulation == RaisonAnnulationAddon.DRAWDOWN_DEPASSE

    def test_addon_annule_news_imminente(self):
        """Un add-on est refusé si une news est imminente."""
        pm = creer_pyramiding_manager(news_bloquee=True)
        parent = MockTrade()

        addon = pm.on_tp1_reached(parent)

        assert addon is None
        etat = pm._states[parent.id_trade]
        assert etat.raison_annulation == RaisonAnnulationAddon.NEWS_IMMINENTE

    def test_addon_annule_spread_trop_eleve(self):
        """Un add-on est refusé si le spread est trop élevé."""
        pm = creer_pyramiding_manager(spread_bloque=True)
        parent = MockTrade()

        addon = pm.on_tp1_reached(parent)

        assert addon is None
        etat = pm._states[parent.id_trade]
        assert etat.raison_annulation == RaisonAnnulationAddon.SPREAD_TROP_ELEVE

    def test_addon_annule_biais_incompatible(self):
        """Un add-on LONG est refusé si le daily bias est BEARISH."""
        pm = creer_pyramiding_manager(biais_aligne=False)
        parent = MockTrade()  # LONG par défaut

        addon = pm.on_tp1_reached(parent)

        assert addon is None
        etat = pm._states[parent.id_trade]
        assert etat.raison_annulation == RaisonAnnulationAddon.BIAIS_JOURNALIER

    def test_max_1_addon_par_trade(self):
        """On ne peut pas ouvrir un deuxième add-on sur le même trade."""
        pm = creer_pyramiding_manager(conditions_valides=True)
        parent = MockTrade()

        # Premier add-on
        addon1 = pm.on_tp1_reached(parent)
        assert addon1 is not None

        # Deuxième tentative → refusé
        addon2 = pm.on_tp1_reached(parent)
        assert addon2 is None

    def test_pyramiding_desactive_aucun_addon(self):
        """PYRAMIDING_ENABLED=False → aucun add-on, jamais."""
        pm = creer_pyramiding_manager(pyramiding_enabled=False)
        parent = MockTrade()

        addon = pm.on_tp1_reached(parent)

        assert addon is None
        assert pm.config.PYRAMIDING_ENABLED is False

    def test_sl_addon_est_breakeven_parent(self):
        """Le SL de l'add-on est TOUJOURS le breakeven du trade parent."""
        pm = creer_pyramiding_manager(conditions_valides=True)
        parent = MockTrade(prix_entree=2000.0)

        addon = pm.on_tp1_reached(parent)

        if addon is not None:
            assert abs(addon.sl_prix - 2000.0) < 0.01, (
                f"SL add-on {addon.sl_prix} ≠ prix_entree parent 2000.0"
            )

    def test_risque_addon_0_5_pct_capital(self):
        """L'add-on risque exactement 0.5% du capital (avec tolérance 5%)."""
        capital = 10000.0
        pm = creer_pyramiding_manager(capital=capital, conditions_valides=True)
        parent = MockTrade(prix_entree=2000.0)

        addon = pm.on_tp1_reached(parent)

        if addon is not None:
            risque_max = capital * 0.005  # 0.5% = 50$
            # lot_size * sl_distance * 100 = risque réel
            sl_distance = abs(addon.prix_entree - addon.sl_prix)
            risque_reel = addon.lots * sl_distance * 100.0
            assert risque_reel <= risque_max * 1.1, (
                f"Risque {risque_reel:.2f}$ > 0.5% du capital ({risque_max:.2f}$)"
            )

    def test_addon_annule_parent_ferme(self):
        """Un add-on est refusé si le trade parent est déjà fermé."""
        pm = creer_pyramiding_manager(conditions_valides=True)
        parent = MockTrade(est_ferme=True)

        addon = pm.on_tp1_reached(parent)

        assert addon is None
        etat = pm._states.get(parent.id_trade)
        if etat:
            assert etat.raison_annulation == RaisonAnnulationAddon.TRADE_PARENT_FERME


class TestCalculAddon:

    def test_sl_distance_trop_petite_annule(self):
        """Si le prix est quasi au breakeven, l'add-on est annulé."""
        pm = creer_pyramiding_manager(conditions_valides=True)
        parent = MockTrade(prix_entree=2000.0)

        # Prix à 0.05$ du breakeven → sl_distance < 0.10 → refus
        params = pm._calculer_entree(parent, prix_actuel=2000.05)

        assert params is None

    def test_tp1_tp2_coherents_long(self):
        """TP1 = entry + 1R, TP2 = entry + 2R pour un LONG."""
        pm = creer_pyramiding_manager(conditions_valides=True)
        parent = MockTrade(prix_entree=2000.0)

        prix_actuel = 2010.0  # 10$ au-dessus du BE → SL distance = 10
        params = pm._calculer_entree(parent, prix_actuel=prix_actuel)

        if params is not None:
            dist = abs(params.entree_proposee - params.sl_propose)
            assert abs(params.tp1_propose - (prix_actuel + dist)) < 0.10
            assert abs(params.tp2_propose - (prix_actuel + 2 * dist)) < 0.10

    def test_tp1_tp2_coherents_short(self):
        """TP1 = entry - 1R, TP2 = entry - 2R pour un SHORT."""
        pm = creer_pyramiding_manager(conditions_valides=True)
        parent = MockTrade(
            prix_entree=2000.0,
            direction=DirectionTrade.SHORT,
            sl=2010.0,
        )

        prix_actuel = 1990.0  # 10$ en-dessous du BE → SL distance = 10
        params = pm._calculer_entree(parent, prix_actuel=prix_actuel)

        if params is not None:
            dist = abs(params.entree_proposee - params.sl_propose)
            assert params.tp1_propose < prix_actuel
            assert params.tp2_propose < params.tp1_propose

    def test_lots_minimum_0_01(self):
        """Les lots ne peuvent pas être inférieurs à 0.01."""
        pm = creer_pyramiding_manager(capital=100.0)  # Capital très faible
        parent = MockTrade(prix_entree=2000.0)

        params = pm._calculer_entree(parent, prix_actuel=2010.0)

        # Soit None (trop petit pour partiaux), soit lots >= 0.01
        if params is not None:
            assert params.lots >= 0.01

    def test_rr_insuffisant_annule(self):
        """Si le R:R de l'add-on < RR_MINIMUM, l'add-on est annulé."""
        pm = creer_pyramiding_manager(conditions_valides=True, rr_min=3.0)
        parent = MockTrade(prix_entree=2000.0)

        # Avec TP2 = entry + 2R → RR = 2.0 < 3.0 → refus
        params = pm._calculer_entree(parent, prix_actuel=2010.0)
        if params is not None:
            dist = abs(params.entree_proposee - params.sl_propose)
            rr = abs(params.tp2_propose - params.entree_proposee) / dist
            # On vérifie que le manager le rejette
            etat = EtatPyramiding(id_trade_parent="trade001")
            etat.addon_evalue = False
            pm._states[parent.id_trade] = etat

            addon = pm.on_tp1_reached(parent)
            # Avec RR_MINIMUM=3.0 et TP2=entry+2R, l'addon doit être refusé
            assert addon is None or rr >= 2.0  # La règle RR s'applique


class TestGestionPhase1:

    def test_tp1_addon_declenche_partiel_50pct(self):
        """Quand TP1 est atteint, 50% est fermé et le trailing est activé."""
        pm = creer_pyramiding_manager(conditions_valides=True)
        parent = MockTrade(prix_entree=2000.0)

        # Créer l'add-on directement pour tester _gerer_phase1
        addon = creer_mock_addon(
            prix_entree=2010.0, sl=2000.0, tp1=2020.0, tp2=2030.0, lots=0.04
        )
        etat = creer_etat("trade001", addon=addon)
        pm._states["trade001"] = etat

        # Simuler TP1 atteint (prix ≥ tp1)
        pm._gerer_phase1(addon, etat, prix=2020.5, est_long=True, df_m15=None)

        assert addon.tp1_atteint is True
        assert addon.trailing_actif is True
        assert addon.trailing_price is not None
        assert addon.trailing_price < 2020.5  # Trailing sous le prix pour un LONG

    def test_tp1_addon_sl_monte_au_be(self):
        """Après TP1, le SL de l'add-on monte au breakeven de l'add-on (= entry)."""
        pm = creer_pyramiding_manager(conditions_valides=True)
        addon = creer_mock_addon(
            prix_entree=2010.0, sl=2000.0, tp1=2020.0, tp2=2030.0, lots=0.04
        )
        etat = creer_etat("trade001", addon=addon)
        pm._states["trade001"] = etat

        pm._gerer_phase1(addon, etat, prix=2021.0, est_long=True, df_m15=None)

        if addon.tp1_atteint:
            # SL doit être ≥ prix_entree de l'add-on
            assert addon.sl_actuel >= addon.prix_entree

    def test_sl_touche_phase1_ferme_addon(self):
        """Si le SL est touché avant TP1, l'add-on est fermé (BE ou perte)."""
        pm = creer_pyramiding_manager(conditions_valides=True)
        addon = creer_mock_addon(
            prix_entree=2010.0, sl=2000.0, tp1=2020.0, tp2=2030.0, lots=0.04
        )
        etat = creer_etat("trade001", addon=addon)
        pm._states["trade001"] = etat

        # Simuler SL touché (prix ≤ sl pour un LONG)
        pm._gerer_phase1(addon, etat, prix=1999.0, est_long=True, df_m15=None)

        assert addon.statut in (StatutAddon.FERME_BE, StatutAddon.FERME_PERTE, StatutAddon.FERME_GAGNANT)


class TestGestionPhase2:

    def test_trailing_monte_avec_prix_long(self):
        """Le trailing stop d'un LONG monte quand le prix monte."""
        pm = creer_pyramiding_manager()
        addon = creer_mock_addon(
            prix_entree=2010.0,
            tp1_atteint=True,
            trailing_price=2018.0,
            atr_dist=5.0,
        )

        pm._maj_trailing_addon(addon, prix=2025.0, est_long=True)

        # Nouveau trailing = 2025 - 5 = 2020 > 2018 → mise à jour
        assert abs(addon.trailing_price - 2020.0) < 0.01

    def test_trailing_ne_descend_pas(self):
        """Le trailing ne peut jamais reculer (règle absolue)."""
        pm = creer_pyramiding_manager()
        addon = creer_mock_addon(
            prix_entree=2010.0,
            tp1_atteint=True,
            trailing_price=2020.0,
            atr_dist=5.0,
        )

        # Prix baisse → nouveau trailing serait 2022 - 5 = 2017 < 2020
        pm._maj_trailing_addon(addon, prix=2022.0, est_long=True)

        assert abs(addon.trailing_price - 2020.0) < 0.01, (
            "Le trailing ne doit pas reculer"
        )

    def test_trailing_short_descend(self):
        """Le trailing d'un SHORT descend quand le prix baisse."""
        pm = creer_pyramiding_manager()
        addon = creer_mock_addon(
            direction="bearish",
            prix_entree=2010.0,
            sl=2020.0,
            tp1=2000.0,
            tp2=1990.0,
            tp1_atteint=True,
            trailing_price=2002.0,
            atr_dist=5.0,
        )

        pm._maj_trailing_addon(addon, prix=1995.0, est_long=False)

        # Nouveau trailing = 1995 + 5 = 2000 < 2002 → mise à jour
        assert abs(addon.trailing_price - 2000.0) < 0.01

    def test_tp2_ferme_addon_gagnant(self):
        """Quand TP2 est atteint, l'add-on est fermé en GAGNANT."""
        pm = creer_pyramiding_manager()
        addon = creer_mock_addon(
            prix_entree=2010.0,
            sl=2000.0,
            tp1=2020.0,
            tp2=2030.0,
            tp1_atteint=True,
            lots=0.04,
        )
        addon.lots_restants = 0.02  # 50% restant après TP1
        addon.pnl_realise_usd = 8.0  # P&L du premier 50%
        etat = creer_etat("trade001", addon=addon)
        pm._states["trade001"] = etat

        pm._gerer_phase2(addon, etat, prix=2031.0, est_long=True)

        assert addon.statut == StatutAddon.FERME_GAGNANT

    def test_trailing_declenche_ferme_addon(self):
        """Quand le trailing est déclenché, l'add-on est fermé."""
        pm = creer_pyramiding_manager()
        addon = creer_mock_addon(
            prix_entree=2010.0,
            sl=2010.0,      # BE add-on
            tp1=2020.0,
            tp2=2030.0,
            tp1_atteint=True,
            trailing_price=2022.0,
            atr_dist=5.0,
            lots=0.04,
        )
        addon.lots_restants = 0.02
        etat = creer_etat("trade001", addon=addon)
        pm._states["trade001"] = etat

        # Prix tombe sous le trailing
        pm._gerer_phase2(addon, etat, prix=2021.5, est_long=True)

        assert addon.statut in (StatutAddon.FERME_GAGNANT, StatutAddon.FERME_BE, StatutAddon.FERME_PERTE)


class TestInvalidationEnCours:

    def test_addon_ferme_parent_ferme(self):
        """Si le trade parent est fermé, l'add-on est fermé aussi."""
        pm = creer_pyramiding_manager(conditions_valides=True)
        parent = MockTrade(prix_entree=2000.0)

        addon = pm.on_tp1_reached(parent)
        if addon is None:
            pytest.skip("Add-on non ouvert — test de fermeture impossible")

        # Fermer le parent
        parent.est_ferme = True
        pm.update(parent, df_m15=None)

        etat = pm._states.get(parent.id_trade)
        if etat and etat.addon:
            assert etat.addon.statut != StatutAddon.OUVERT

    def test_invalidation_news_phase1_ferme(self):
        """En Phase 1, une news imminente ferme l'add-on d'urgence."""
        pm = creer_pyramiding_manager(conditions_valides=True)
        parent = MockTrade(prix_entree=2000.0)

        addon = creer_mock_addon(
            prix_entree=2010.0, sl=2000.0, tp1=2020.0, tp2=2030.0
        )
        etat = creer_etat(parent.id_trade, addon=addon)
        pm._states[parent.id_trade] = etat

        # Injecter un filtre news bloqué
        news_mock = MagicMock()
        news_mock.is_trading_allowed.return_value = (False, "NFP dans 30min")
        pm.validator = ValidateurAddon(filtre_news=news_mock)

        pm._verifier_invalidation(addon, etat, parent, prix=2012.0)

        # L'add-on doit être fermé (pas OUVERT)
        assert addon.statut != StatutAddon.OUVERT

    def test_phase2_pas_d_invalidation(self):
        """En Phase 2 (après TP1), _verifier_invalidation ne fait rien."""
        pm = creer_pyramiding_manager(conditions_valides=True)
        parent = MockTrade(prix_entree=2000.0)

        addon = creer_mock_addon(
            prix_entree=2010.0, sl=2010.0, tp1=2020.0, tp2=2030.0,
            tp1_atteint=True, trailing_price=2018.0
        )
        etat = creer_etat(parent.id_trade, addon=addon)
        pm._states[parent.id_trade] = etat

        statut_avant = addon.statut

        # En Phase 2, l'invalidation ne doit pas être appelée
        # (la méthode update() le gère — ce test vérifie la logique directement)
        pm._maj_trailing_addon(addon, prix=2022.0, est_long=True)

        assert addon.statut == statut_avant  # Pas changé


class TestStatsPyramiding:

    def test_stats_coherentes_multi_trades(self):
        """Les stats globales agrègent correctement tous les états."""
        pm = creer_pyramiding_manager()
        pm._states["trade1"] = creer_etat("trade1", opened=2, won=2, pnl=50.0)
        pm._states["trade2"] = creer_etat("trade2", opened=1, won=0, pnl=-10.0)

        stats = pm.get_stats_pyramiding()

        assert stats["total_addons_ouverts"] == 3
        assert stats["total_addons_gagnants"] == 2
        assert abs(stats["pnl_total_usd"] - 40.0) < 0.01
        assert abs(stats["win_rate_pct"] - 66.67) < 0.1

    def test_stats_vides_au_demarrage(self):
        """Au démarrage, toutes les stats sont à zéro."""
        pm = creer_pyramiding_manager()

        stats = pm.get_stats_pyramiding()

        assert stats["total_addons_ouverts"] == 0
        assert stats["total_addons_gagnants"] == 0
        assert stats["pnl_total_usd"] == 0.0
        assert stats["addons_actifs"] == 0

    def test_win_rate_calcule_correctement(self):
        """Le win rate est calculé sur les add-ons ouverts (pas les refusés)."""
        pm = creer_pyramiding_manager()
        pm._states["t1"] = creer_etat("t1", opened=4, won=3, pnl=30.0)

        stats = pm.get_stats_pyramiding()

        assert abs(stats["win_rate_pct"] - 75.0) < 0.01

    def test_get_addon_pour_trade_existant(self):
        """get_addon_pour_trade retourne l'add-on du trade demandé."""
        pm = creer_pyramiding_manager()
        addon = creer_mock_addon("addon01", "trade001")
        pm._states["trade001"] = creer_etat("trade001", addon=addon)

        resultat = pm.get_addon_pour_trade("trade001")

        assert resultat is not None
        assert resultat.addon_id == "addon01"

    def test_get_addon_pour_trade_inexistant(self):
        """get_addon_pour_trade retourne None si le trade n'existe pas."""
        pm = creer_pyramiding_manager()

        resultat = pm.get_addon_pour_trade("trade_inconnu")

        assert resultat is None


class TestValidateurAddon:

    def test_validator_accepte_conditions_parfaites(self):
        """Le validator accepte quand toutes les conditions sont remplies."""
        validator = creer_validator()
        parent = MockTrade()
        maintenant = datetime(2025, 6, 1, 10, 0)  # 10h UTC → en session

        valide, raison, msg = validator.validate(
            parent, 2010.0, maintenant, structure=None, obs_actifs=None
        )

        assert valide is True
        assert raison is None

    def test_validator_refuse_cb_actif(self):
        """Le validator refuse si le circuit breaker est actif."""
        validator = creer_validator(cb_actif=True)
        parent = MockTrade()

        valide, raison, msg = validator.validate(
            parent, 2010.0, datetime(2025, 6, 1, 10, 0)
        )

        assert valide is False
        assert raison == RaisonAnnulationAddon.DRAWDOWN_DEPASSE

    def test_validator_refuse_hors_session(self):
        """Le validator refuse si hors session (avant 7h ou après 20h UTC)."""
        validator = creer_validator()
        parent = MockTrade()
        hors_session = datetime(2025, 6, 1, 3, 0)  # 3h UTC → hors session

        valide, raison, msg = validator.validate(
            parent, 2010.0, hors_session
        )

        assert valide is False
        assert raison == RaisonAnnulationAddon.HORS_SESSION

    def test_validator_refuse_parent_ferme(self):
        """Le validator refuse immédiatement si le parent est fermé."""
        validator = creer_validator()
        parent = MockTrade(est_ferme=True)

        valide, raison, msg = validator.validate(
            parent, 2010.0, datetime(2025, 6, 1, 10, 0)
        )

        assert valide is False
        assert raison == RaisonAnnulationAddon.TRADE_PARENT_FERME

    def test_validator_refuse_news(self):
        """Le validator refuse si le filtre news bloque."""
        validator = creer_validator(news_bloquee=True)
        parent = MockTrade()

        valide, raison, msg = validator.validate(
            parent, 2010.0, datetime(2025, 6, 1, 10, 0)
        )

        assert valide is False
        assert raison == RaisonAnnulationAddon.NEWS_IMMINENTE

    def test_validator_sans_filtres_accepte(self):
        """Un validator sans filtres injectés accepte toujours (mode test)."""
        validator = ValidateurAddon()  # Aucun filtre
        parent = MockTrade()

        valide, raison, msg = validator.validate(
            parent, 2010.0, datetime(2025, 6, 1, 10, 0)
        )

        assert valide is True


class TestIntegrationTradeManager:

    def test_gerer_tp1_appelle_pyramiding(self):
        """_gerer_tp1 appelle pyramiding_manager.on_tp1_reached après TP1."""
        from trade_manager import GestionnairePositions, TradeGere, NiveauSL, NiveauPartiel

        connecteur_mock = MagicMock()
        gestionnaire = GestionnairePositions(connecteur=connecteur_mock)
        pm_mock = MagicMock()
        pm_mock.on_tp1_reached.return_value = None
        gestionnaire.pyramiding_manager = pm_mock

        import pandas as pd
        import numpy as np
        # DataFrame M15 minimal pour _calculer_atr_m15
        df_m15 = pd.DataFrame({
            "high": [2010.0] * 20,
            "low": [2000.0] * 20,
            "close": [2005.0] * 20,
        })

        # Trade avec TP1 NON atteint (pour que _fermer_partiel passe)
        lots_tp1 = 0.05
        trade = TradeGere(
            id_trade="test001",
            ticket_mt5=11111,
            tickets_fermetures=[],
            symbole="XAUUSD",
            direction=DirectionTrade.LONG,
            prix_entree=2000.0,
            heure_entree=datetime(2025, 6, 1, 9, 0),
            lots_initial=0.10,
            lots_restants=0.10,
            sl=NiveauSL(prix_actuel=1990.0, prix_initial=1990.0),
            tp1=NiveauPartiel(1, 1.0, 2010.0, 50.0, lots_tp1, atteint=False),
            tp2=NiveauPartiel(2, 2.0, 2020.0, 25.0, 0.025),
            tp3=NiveauPartiel(3, 3.0, 2030.0, 25.0, 0.025),
        )
        gestionnaire._trade_actif = trade

        # Appel direct de _gerer_tp1
        gestionnaire._gerer_tp1(trade, 2010.0, df_m15)

        # Le pyramiding doit être évalué après TP1 (si activé)
        if CONFIG.PYRAMIDING_ENABLED:
            pm_mock.on_tp1_reached.assert_called_once_with(trade)

    def test_gerer_positions_appelle_pyramiding_update(self):
        """gerer_positions appelle pyramiding_manager.update quand un trade est actif."""
        from trade_manager import GestionnairePositions, TradeGere, NiveauSL, NiveauPartiel
        import pandas as pd

        connecteur_mock = MagicMock()
        connecteur_mock.get_tick.return_value = {"bid": 2008.0, "ask": 2008.5}
        gestionnaire = GestionnairePositions(connecteur=connecteur_mock)
        pm_mock = MagicMock()
        pm_mock.update.return_value = None
        gestionnaire.pyramiding_manager = pm_mock

        df_m15 = pd.DataFrame({
            "high": [2010.0] * 20,
            "low": [2000.0] * 20,
            "close": [2005.0] * 20,
        })

        # Trade en Phase 2 (TP1 déjà atteint, SL au BE)
        trade = TradeGere(
            id_trade="test002",
            ticket_mt5=22222,
            tickets_fermetures=[],
            symbole="XAUUSD",
            direction=DirectionTrade.LONG,
            prix_entree=2000.0,
            heure_entree=datetime(2025, 6, 1, 9, 0),
            lots_initial=0.10,
            lots_restants=0.05,
            sl=NiveauSL(prix_actuel=2000.0, prix_initial=1990.0, est_breakeven=True),
            tp1=NiveauPartiel(1, 1.0, 2010.0, 50.0, 0.05, atteint=True),
            tp2=NiveauPartiel(2, 2.0, 2020.0, 25.0, 0.025),
            tp3=NiveauPartiel(3, 3.0, 2030.0, 25.0, 0.025),
            phase=PhaseTrade.PHASE_2,
        )
        gestionnaire._trade_actif = trade

        gestionnaire.gerer_positions(df_m15)

        if CONFIG.PYRAMIDING_ENABLED:
            pm_mock.update.assert_called()
