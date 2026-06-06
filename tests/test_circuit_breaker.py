"""
test_circuit_breaker.py — Tests unitaires du circuit breaker multi-niveaux.
Tous les tests utilisent des mocks — pas de connexion MT5 requise.
"""

import sys
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.modules.setdefault("MetaTrader5", MagicMock())

from circuit_breaker import CircuitBreaker, NiveauCB, RaisonCB, EtatCB
from daily_stats_tracker import SuiveurStatsJournalieres, ResultatTrade, StatsJournalieres
import circuit_breaker as cb_module
import daily_stats_tracker as dst_module


@pytest.fixture(autouse=True)
def isoler_fichiers(tmp_path, monkeypatch):
    """Redirige les fichiers JSON vers tmp_path pour tous les tests."""
    monkeypatch.setattr(cb_module, "FICHIER_ETAT", tmp_path / "cb_state.json")
    monkeypatch.setattr(dst_module, "FICHIER_STATS", tmp_path / "daily_stats.json")
    monkeypatch.setattr(dst_module, "DOSSIER_ARCHIVES", tmp_path / "archives")
    (tmp_path / "archives").mkdir(exist_ok=True)


# ── Helpers / Fixtures ─────────────────────────────────────────────────────

def creer_resultat_trade(gagnant: bool = False, r: float = None) -> ResultatTrade:
    """Crée un ResultatTrade fictif."""
    r_val = r if r is not None else (1.0 if gagnant else -1.0)
    return ResultatTrade(
        id_trade="test_001",
        heure_fermeture=datetime.utcnow().isoformat(),
        direction="LONG",
        r_realise=r_val,
        pnl_usd=100.0 if gagnant else -100.0,
        pnl_pct=1.0 if gagnant else -1.0,
        est_gagnant=gagnant,
        raison_fermeture="SL_TOUCHE" if not gagnant else "TP2_ATTEINT",
        ob_score=75,
        condition_marche="BULLISH",
    )


def creer_suiveur_mock(pertes: int = 0, dd_pct: float = 0.0) -> MagicMock:
    """Crée un SuiveurStatsJournalieres mocké avec des valeurs contrôlées."""
    mock = MagicMock(spec=SuiveurStatsJournalieres)
    mock.get_pertes_consecutives.return_value = pertes
    mock.get_drawdown_pct.return_value = dd_pct
    mock.get_pnl_pct.return_value = -dd_pct

    stats = StatsJournalieres(
        date_utc=datetime.utcnow().strftime("%Y-%m-%d"),
        balance_ouverture=10000.0,
        balance_actuelle=10000.0 * (1 - dd_pct / 100),
        balance_pic=10000.0,
        pertes_consecutives=pertes,
        drawdown_pct=dd_pct,
        drawdown_max_pct=dd_pct,
    )
    mock.stats = stats
    mock.get_resume.return_value = {
        "pertes_consecutives": pertes,
        "drawdown_pct": dd_pct,
        "nb_trades": 0,
        "win_rate": 0.0,
        "pnl_usd": 0.0,
        "pnl_pct": 0.0,
    }

    # Le mock ne modifie PAS les pertes — elles sont déjà à la valeur `pertes`
    # (simuler que le trade a déjà été enregistré et les stats reflètent l'état final)
    def enregistrer(resultat):
        if resultat.est_gagnant:
            mock.get_pertes_consecutives.return_value = 0
        # Pas d'incrément — la valeur initiale représente l'état APRÈS le trade
    mock.enregistrer_trade.side_effect = enregistrer

    return mock


def creer_cb(pertes: int = 0, dd_pct: float = 0.0) -> CircuitBreaker:
    """Crée un CircuitBreaker avec mock des stats."""
    suiveur = creer_suiveur_mock(pertes, dd_pct)
    cb = CircuitBreaker(suiveur_stats=suiveur, connecteur=None, notifier=None)
    return cb


def creer_cb_au_niveau(niveau: NiveauCB, pause_dans: int = 4) -> CircuitBreaker:
    """Crée un CircuitBreaker déjà activé à un certain niveau."""
    cb = creer_cb()
    if niveau == NiveauCB.WARNING:
        cb._etat.niveau = NiveauCB.WARNING
        cb._etat.raison = RaisonCB.DEUX_PERTES_CONSECUTIVES
        cb._etat.declenche_a = datetime.utcnow()
        cb._etat.risk_pct_actuel = 0.5
    elif niveau == NiveauCB.PAUSE:
        cb._etat.niveau = NiveauCB.PAUSE
        cb._etat.raison = RaisonCB.TROIS_PERTES_ET_DD_1_5
        cb._etat.declenche_a = datetime.utcnow()
        cb._etat.pause_jusqu_a = datetime.utcnow() + timedelta(hours=pause_dans)
        cb._etat.risk_pct_actuel = 0.0
    elif niveau == NiveauCB.ARRETE:
        cb._etat.niveau = NiveauCB.ARRETE
        cb._etat.raison = RaisonCB.DD_SUPERIEUR_3_PCT
        cb._etat.declenche_a = datetime.utcnow()
        cb._etat.reset_a_minuit = True
        cb._etat.risk_pct_actuel = 0.0
    return cb


# ── Tests vérification des conditions ──────────────────────────────────────

class TestVerificationNiveaux:
    """Tests des méthodes _verifier_niveau1/2/3."""

    def test_niveau1_2_pertes_consecutives(self):
        """2 pertes → raison WARNING."""
        cb = creer_cb()
        raison = cb._verifier_niveau1(pertes=2, dd_pct=0.5)
        assert raison == RaisonCB.DEUX_PERTES_CONSECUTIVES

    def test_niveau1_dd_superieur_1_5(self):
        """DD > 1.5% → raison WARNING."""
        cb = creer_cb()
        raison = cb._verifier_niveau1(pertes=0, dd_pct=1.6)
        assert raison == RaisonCB.DD_SUPERIEUR_1_5_PCT

    def test_niveau1_ni_pertes_ni_dd(self):
        """1 perte et DD 0.5% → pas de WARNING."""
        cb = creer_cb()
        raison = cb._verifier_niveau1(pertes=1, dd_pct=0.5)
        assert raison is None

    def test_niveau2_requiert_les_deux_conditions(self):
        """3 pertes MAIS DD 1.2% → PAS de PAUSE (les deux obligatoires)."""
        cb = creer_cb()
        raison = cb._verifier_niveau2(pertes=3, dd_pct=1.2)
        assert raison is None

    def test_niveau2_seulement_dd_pas_assez(self):
        """DD > 1.5% MAIS 2 pertes → PAS de PAUSE."""
        cb = creer_cb()
        raison = cb._verifier_niveau2(pertes=2, dd_pct=1.8)
        assert raison is None

    def test_niveau2_3_pertes_et_dd_1_5(self):
        """3 pertes ET DD 1.6% → PAUSE."""
        cb = creer_cb()
        raison = cb._verifier_niveau2(pertes=3, dd_pct=1.6)
        assert raison == RaisonCB.TROIS_PERTES_ET_DD_1_5

    def test_niveau3_dd_superieur_3(self):
        """DD > 3% → ARRETE (raison DD)."""
        cb = creer_cb()
        raison = cb._verifier_niveau3(pertes=0, dd_pct=3.1)
        assert raison == RaisonCB.DD_SUPERIEUR_3_PCT

    def test_niveau3_4_pertes(self):
        """4 pertes consécutives → ARRETE."""
        cb = creer_cb()
        raison = cb._verifier_niveau3(pertes=4, dd_pct=0.5)
        assert raison == RaisonCB.QUATRE_PERTES_CONSECUTIVES

    def test_niveau3_3_pertes_et_dd_2(self):
        """3 pertes + DD > 2% → ARRETE."""
        cb = creer_cb()
        raison = cb._verifier_niveau3(pertes=3, dd_pct=2.1)
        assert raison == RaisonCB.TROIS_PERTES_ET_DD_2

    def test_niveau3_pas_declenche_avec_dd_insuffisant(self):
        """3 pertes + DD 1.8% (< 2%) → PAS de ARRETE."""
        cb = creer_cb()
        raison = cb._verifier_niveau3(pertes=3, dd_pct=1.8)
        assert raison is None


# ── Tests activation des niveaux ──────────────────────────────────────────

class TestActivationNiveaux:
    """Tests de _activer() et evaluer_apres_trade()."""

    def test_activation_warning_apres_2_pertes(self):
        """evaluer_apres_trade après 2 pertes → niveau WARNING."""
        cb = creer_cb(pertes=2, dd_pct=0.5)
        cb.evaluer_apres_trade(creer_resultat_trade(gagnant=False))
        assert cb._etat.niveau == NiveauCB.WARNING
        assert cb._etat.raison == RaisonCB.DEUX_PERTES_CONSECUTIVES

    def test_activation_warning_apres_dd_1_5(self):
        """evaluer_apres_trade avec DD > 1.5% → niveau WARNING."""
        cb = creer_cb(pertes=0, dd_pct=1.6)
        cb.evaluer_apres_trade(creer_resultat_trade(gagnant=False))
        assert cb._etat.niveau == NiveauCB.WARNING
        assert cb._etat.raison == RaisonCB.DD_SUPERIEUR_1_5_PCT

    def test_activation_pause_3_pertes_et_dd(self):
        """3 pertes + DD 1.6% → niveau PAUSE."""
        cb = creer_cb(pertes=3, dd_pct=1.6)
        cb.evaluer_apres_trade(creer_resultat_trade(gagnant=False))
        assert cb._etat.niveau == NiveauCB.PAUSE
        assert cb._etat.pause_jusqu_a is not None

    def test_pause_dure_4h(self):
        """La pause PAUSE doit durer exactement 4 heures."""
        cb = creer_cb(pertes=3, dd_pct=1.6)
        cb.evaluer_apres_trade(creer_resultat_trade(gagnant=False))
        delta = cb._etat.pause_jusqu_a - cb._etat.declenche_a
        assert abs(delta.total_seconds() - 4 * 3600) < 10

    def test_activation_arrete_dd_3(self):
        """DD > 3% → niveau ARRETE + reset_a_minuit."""
        cb = creer_cb(pertes=1, dd_pct=3.1)
        cb.evaluer_apres_trade(creer_resultat_trade(gagnant=False))
        assert cb._etat.niveau == NiveauCB.ARRETE
        assert cb._etat.raison == RaisonCB.DD_SUPERIEUR_3_PCT
        assert cb._etat.reset_a_minuit is True

    def test_activation_arrete_4_pertes(self):
        """4 pertes (DD faible) → niveau ARRETE."""
        cb = creer_cb(pertes=4, dd_pct=0.8)
        cb.evaluer_apres_trade(creer_resultat_trade(gagnant=False))
        assert cb._etat.niveau == NiveauCB.ARRETE
        assert cb._etat.raison == RaisonCB.QUATRE_PERTES_CONSECUTIVES

    def test_activation_arrete_3_pertes_et_dd_2(self):
        """3 pertes + DD > 2% → ARRETE."""
        cb = creer_cb(pertes=3, dd_pct=2.1)
        cb.evaluer_apres_trade(creer_resultat_trade(gagnant=False))
        assert cb._etat.niveau == NiveauCB.ARRETE
        assert cb._etat.raison == RaisonCB.TROIS_PERTES_ET_DD_2

    def test_niveau3_prioritaire_sur_niveau2(self):
        """Si conditions N2 ET N3 → N3 est activé (priorité)."""
        cb = creer_cb(pertes=4, dd_pct=3.5)
        cb.evaluer_apres_trade(creer_resultat_trade(gagnant=False))
        assert cb._etat.niveau == NiveauCB.ARRETE


# ── Tests reset ────────────────────────────────────────────────────────────

class TestReset:
    """Tests des resets automatiques et sur trade gagnant."""

    def test_warning_reset_sur_trade_gagnant(self):
        """WARNING se reset si prochain trade gagnant."""
        cb = creer_cb_au_niveau(NiveauCB.WARNING)
        cb.evaluer_apres_trade(creer_resultat_trade(gagnant=True))
        assert cb._etat.niveau == NiveauCB.AUCUN

    def test_pause_reset_sur_trade_gagnant(self):
        """PAUSE se reset si trade gagnant (reset anticipé)."""
        cb = creer_cb_au_niveau(NiveauCB.PAUSE)
        cb.evaluer_apres_trade(creer_resultat_trade(gagnant=True))
        assert cb._etat.niveau == NiveauCB.AUCUN

    def test_arrete_pas_reset_sur_trade_gagnant(self):
        """ARRETE NE se reset PAS sur trade gagnant — règle absolue."""
        cb = creer_cb_au_niveau(NiveauCB.ARRETE)
        cb.evaluer_apres_trade(creer_resultat_trade(gagnant=True))
        assert cb._etat.niveau == NiveauCB.ARRETE

    def test_pause_expiree_reset_automatique(self):
        """Pause expirée → trading_autorise() reset automatiquement."""
        cb = creer_cb_au_niveau(NiveauCB.PAUSE)
        cb._etat.pause_jusqu_a = datetime.utcnow() - timedelta(minutes=1)
        autorise, _, multiplicateur = cb.trading_autorise()
        assert autorise is True
        assert cb._etat.niveau == NiveauCB.AUCUN

    def test_arrete_reset_minuit_utc(self):
        """ARRETE reset automatiquement le lendemain."""
        cb = creer_cb_au_niveau(NiveauCB.ARRETE)
        cb._etat.declenche_a = datetime.utcnow() - timedelta(days=1)
        cb._etat.reset_a_minuit = True
        cb._verifier_reset_automatique(datetime.utcnow())
        assert cb._etat.niveau == NiveauCB.AUCUN

    def test_force_reset(self):
        """force_reset() remet n'importe quel niveau à AUCUN."""
        cb = creer_cb_au_niveau(NiveauCB.ARRETE)
        cb.force_reset("Test force reset")
        assert cb._etat.niveau == NiveauCB.AUCUN


# ── Tests trading_autorise() ──────────────────────────────────────────────

class TestTradingAutorise:
    """Tests de la méthode principale trading_autorise()."""

    def test_aucun_niveau_autorise(self):
        """Aucun CB → trading autorisé avec risk×1.0."""
        cb = creer_cb()
        autorise, raison, mult = cb.trading_autorise()
        assert autorise is True
        assert mult == 1.0
        assert raison == "OK"

    def test_warning_autorise_risk_reduit(self):
        """WARNING → trading autorisé mais multiplicateur 0.5."""
        cb = creer_cb_au_niveau(NiveauCB.WARNING)
        autorise, raison, mult = cb.trading_autorise()
        assert autorise is True
        assert mult == 0.5
        assert "0.5%" in raison

    def test_pause_bloque_nouvelles_entrees(self):
        """PAUSE → trading bloqué, multiplicateur 0.0."""
        cb = creer_cb_au_niveau(NiveauCB.PAUSE)
        autorise, raison, mult = cb.trading_autorise()
        assert autorise is False
        assert mult == 0.0
        assert "PAUSE" in raison

    def test_arrete_bloque_jusqu_a_minuit(self):
        """ARRETE → trading bloqué, multiplicateur 0.0."""
        cb = creer_cb_au_niveau(NiveauCB.ARRETE)
        autorise, raison, mult = cb.trading_autorise()
        assert autorise is False
        assert mult == 0.0
        assert "ARRÊT" in raison or "minuit" in raison

    def test_trades_bloques_incrementes(self):
        """Chaque appel bloqué incrémente le compteur."""
        cb = creer_cb_au_niveau(NiveauCB.PAUSE)
        cb.trading_autorise()
        cb.trading_autorise()
        cb.trading_autorise()
        assert cb._etat.trades_bloques == 3

    def test_trading_autorise_retourne_tuple(self):
        """trading_autorise() retourne toujours un tuple de 3 éléments."""
        cb = creer_cb()
        resultat = cb.trading_autorise()
        assert isinstance(resultat, tuple)
        assert len(resultat) == 3
        assert isinstance(resultat[0], bool)
        assert isinstance(resultat[1], str)
        assert isinstance(resultat[2], float)


# ── Tests historique ──────────────────────────────────────────────────────

class TestHistorique:
    """Tests de l'enregistrement dans l'historique."""

    def test_historique_enregistre_chaque_activation(self):
        """Chaque activation est enregistrée dans l'historique."""
        cb = creer_cb()
        cb._activer(NiveauCB.WARNING, RaisonCB.DEUX_PERTES_CONSECUTIVES)
        cb._reset("Test")
        cb._activer(NiveauCB.PAUSE, RaisonCB.TROIS_PERTES_ET_DD_1_5)
        assert len(cb._etat.historique) == 2

    def test_historique_contient_raison_reset(self):
        """Après reset, la raison est dans l'historique."""
        cb = creer_cb()
        cb._activer(NiveauCB.WARNING, RaisonCB.DEUX_PERTES_CONSECUTIVES)
        cb._reset("Raison test")
        assert cb._etat.historique[0].get("reset_a") is not None
        assert cb._etat.historique[0].get("raison_reset") == "Raison test"

    def test_historique_trades_bloques(self):
        """Le nombre de trades bloqués est enregistré à la fermeture."""
        cb = creer_cb()
        # Activer via _activer pour avoir une entrée dans l'historique
        cb._activer(NiveauCB.PAUSE, RaisonCB.TROIS_PERTES_ET_DD_1_5)
        cb._etat.pause_jusqu_a = datetime.utcnow() + timedelta(hours=2)
        cb.trading_autorise()  # +1 bloqué
        cb.trading_autorise()  # +2 bloqués
        cb._reset("Test trades bloqués")
        assert cb._etat.historique[-1].get("trades_bloques") == 2


# ── Tests persistance ─────────────────────────────────────────────────────

class TestPersistance:
    """Tests de sauvegarde et chargement de l'état CB."""

    def test_sauvegarder_charger_etat_pause(self, tmp_path, monkeypatch):
        """Un état PAUSE sauvegardé puis rechargé doit être identique."""
        import circuit_breaker as cb_module
        # Rediriger le fichier vers tmp_path
        monkeypatch.setattr(cb_module, "FICHIER_ETAT", tmp_path / "cb_state.json")

        cb = creer_cb_au_niveau(NiveauCB.PAUSE, pause_dans=3)
        cb._sauvegarder_etat()

        # Recharger
        cb2 = CircuitBreaker.__new__(CircuitBreaker)
        cb2.suiveur_stats = creer_suiveur_mock()
        cb2.connecteur = None
        cb2.notifier = None
        # Patcher le chemin
        import circuit_breaker as cb_module2
        cb_module2.FICHIER_ETAT = tmp_path / "cb_state.json"
        etat = cb2._charger_etat()

        assert etat.niveau == NiveauCB.PAUSE
        assert etat.reset_a_minuit is False

    def test_pause_expiree_au_chargement_reset_auto(self, tmp_path, monkeypatch):
        """Une pause expirée au démarrage doit être auto-resetée."""
        import circuit_breaker as cb_module
        monkeypatch.setattr(cb_module, "FICHIER_ETAT", tmp_path / "cb_state.json")

        cb = creer_cb_au_niveau(NiveauCB.PAUSE)
        cb._etat.pause_jusqu_a = datetime.utcnow() - timedelta(hours=1)
        cb._sauvegarder_etat()

        # Recharger → pause expirée → reset auto
        cb2 = CircuitBreaker.__new__(CircuitBreaker)
        cb2.suiveur_stats = creer_suiveur_mock()
        cb2.connecteur = None
        cb2.notifier = None
        cb_module.FICHIER_ETAT = tmp_path / "cb_state.json"
        etat = cb2._charger_etat()

        assert etat.niveau == NiveauCB.AUCUN


# ── Tests SuiveurStatsJournalieres ─────────────────────────────────────────

class TestSuiveurStats:
    """Tests du suivi des statistiques journalières."""

    def test_pertes_consecutives_incrementees(self):
        """Chaque perte incrémente les pertes consécutives."""
        suiveur = SuiveurStatsJournalieres(connecteur=None)
        suiveur._stats.balance_ouverture = 10000.0
        suiveur._stats.balance_actuelle = 10000.0
        suiveur._stats.balance_pic = 10000.0

        for _ in range(3):
            suiveur.enregistrer_trade(creer_resultat_trade(gagnant=False))

        assert suiveur.stats.pertes_consecutives == 3

    def test_gain_reset_pertes_consecutives(self):
        """Un gain remet les pertes consécutives à 0."""
        suiveur = SuiveurStatsJournalieres(connecteur=None)
        suiveur._stats.balance_ouverture = 10000.0
        suiveur._stats.balance_actuelle = 10000.0
        suiveur._stats.balance_pic = 10000.0
        suiveur._stats.pertes_consecutives = 2

        suiveur.enregistrer_trade(creer_resultat_trade(gagnant=True))
        assert suiveur.stats.pertes_consecutives == 0

    def test_drawdown_calcule_depuis_pic(self):
        """Le drawdown est calculé depuis le pic de balance du jour."""
        suiveur = SuiveurStatsJournalieres(connecteur=None)
        suiveur._stats.balance_ouverture = 10000.0
        suiveur._stats.balance_actuelle = 10200.0  # Nouveau pic
        suiveur._stats.balance_pic = 10200.0

        # Perte de 200$ → DD = 200/10200 ≈ 1.96%
        res = creer_resultat_trade(gagnant=False)
        res.pnl_usd = -200.0
        suiveur.enregistrer_trade(res)

        assert abs(suiveur.stats.drawdown_pct - 1.96) < 0.1

    def test_win_rate_calcule(self):
        """Le win rate est correctement calculé."""
        suiveur = SuiveurStatsJournalieres(connecteur=None)
        suiveur._stats.balance_ouverture = 10000.0
        suiveur._stats.balance_actuelle = 10000.0
        suiveur._stats.balance_pic = 10000.0

        suiveur.enregistrer_trade(creer_resultat_trade(gagnant=True))
        suiveur.enregistrer_trade(creer_resultat_trade(gagnant=True))
        suiveur.enregistrer_trade(creer_resultat_trade(gagnant=False))

        assert abs(suiveur.stats.win_rate - 66.67) < 0.1
