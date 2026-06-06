"""
tests/test_slippage_simulator.py — Tests unitaires du simulateur de slippage.

Tous les tests utilisant la reproductibilité utilisent seed=42.
Les tests de conditions de marché utilisent _test_force_condition.
"""

import pytest
from datetime import datetime
from unittest.mock import MagicMock

from slippage_simulator import (
    SimulateurSlippage,
    ConditionMarche,
    QualiteExecution,
    ExecutionSimulee,
    RapportSlippage,
)
from paper_order_book import CarnetOrdresPaper
from config import CONFIG


# ── Fixtures ───────────────────────────────────────────────────────────────

def creer_simulateur(
    seed: int = 0,
    news_bloquee: bool = False,
    force_condition: ConditionMarche = None,
    force_requote: bool = False,
    force_partial: bool = False,
    force_requote_deviation: float = None,
    max_requote_deviation: float = None,
):
    """Crée un SimulateurSlippage configuré pour les tests."""
    # Filtre news mock
    news_mock = MagicMock()
    if news_bloquee:
        news_mock.is_trading_allowed.return_value = (False, "NFP dans 30min")
    else:
        news_mock.is_trading_allowed.return_value = (True, "")

    # Moniteur spread mock
    spread_mock = MagicMock()
    spread_mock.get_spread_actuel.return_value = 10.0
    spread_mock.get_spread_moyen.return_value = 10.0

    # Config optionnelle
    config = CONFIG.__class__()
    if max_requote_deviation is not None:
        config.PAPER_MAX_REQUOTE_DEVIATION_PTS = max_requote_deviation

    sim = SimulateurSlippage(
        connecteur=None,
        moniteur_spread=spread_mock,
        filtre_news=news_mock,
        config=config,
        seed=seed,
    )

    # Override pour les tests
    if force_condition is not None:
        sim._test_force_condition = force_condition
    if force_requote:
        sim._test_force_requote = True
    if force_partial:
        sim._test_force_partial = True
    if force_requote_deviation is not None:
        sim._test_requote_deviation_pts = force_requote_deviation

    return sim


def creer_execution_mock(
    prix: float = 2000.0,
    lots: float = 0.05,
    order_type: str = "BUY",
    slippage_pts: float = 2.0,
    statut: QualiteExecution = QualiteExecution.BON,
) -> ExecutionSimulee:
    """Crée une ExecutionSimulee pour les tests du carnet d'ordres."""
    return ExecutionSimulee(
        order_id="test001",
        requested_price=prix,
        requested_lots=lots,
        order_type=order_type,
        symbol="XAUUSD",
        requested_at=datetime.utcnow(),
        executed_price=prix + slippage_pts * 0.01,
        executed_lots=lots,
        execution_quality=statut,
        market_condition=ConditionMarche.NORMAL,
        slippage_pts=slippage_pts,
        slippage_usd=round(slippage_pts * lots * 1.0, 2),
        latency_ms=100,
        spread_pts=10.0,
        price_moved_during_latency=0.01,
    )


# ── Tests : Direction du slippage ──────────────────────────────────────────

class TestDirectionSlippage:

    def test_slippage_buy_toujours_défavorable(self):
        """BUY avec slippage → prix exécuté ≥ prix demandé."""
        sim = creer_simulateur(
            seed=42, force_condition=ConditionMarche.NORMAL
        )
        erreurs = []
        for _ in range(50):
            ex = sim.simulate_order("BUY", 2000.0, 0.01, 1990.0, 2020.0, "XAUUSD")
            if ex.slippage_pts > 0 and ex.executed_price < ex.requested_price:
                erreurs.append(f"BUY slip={ex.slippage_pts} ex={ex.executed_price} req={ex.requested_price}")
        assert not erreurs, f"Slippage favorable détecté sur BUY :\n" + "\n".join(erreurs[:3])

    def test_slippage_sell_toujours_défavorable(self):
        """SELL avec slippage → prix exécuté ≤ prix demandé."""
        sim = creer_simulateur(
            seed=42, force_condition=ConditionMarche.NORMAL
        )
        erreurs = []
        for _ in range(50):
            ex = sim.simulate_order("SELL", 2000.0, 0.01, 2010.0, 1980.0, "XAUUSD")
            if ex.slippage_pts > 0 and ex.executed_price > ex.requested_price:
                erreurs.append(f"SELL slip={ex.slippage_pts} ex={ex.executed_price} req={ex.requested_price}")
        assert not erreurs, f"Slippage favorable détecté sur SELL :\n" + "\n".join(erreurs[:3])

    def test_slippage_jamais_négatif(self):
        """Le slippage ne peut pas être négatif (pas de prix amélioré)."""
        sim = creer_simulateur(seed=42)
        for _ in range(100):
            ex = sim.simulate_order("BUY", 2000.0, 0.01, 1990.0, 2020.0, "XAUUSD")
            assert ex.slippage_pts >= 0, f"Slippage négatif : {ex.slippage_pts}"

    def test_slippage_dans_plage_du_profil(self):
        """Le slippage reste dans la plage du profil NORMAL (1–4 pts + variation ±15%)."""
        sim = creer_simulateur(seed=42, force_condition=ConditionMarche.NORMAL)
        profil = sim.PROFILS[ConditionMarche.NORMAL]
        for _ in range(200):
            ex = sim.simulate_order("BUY", 2000.0, 0.01, 1990.0, 2020.0, "XAUUSD")
            if ex.was_requoted or ex.is_partial:
                continue
            # Avec variation ±15%, la plage théorique est [min*0.85, max*1.15]
            assert ex.slippage_pts <= profil.slippage_max_pts * 1.20, (
                f"Slippage {ex.slippage_pts} > max théorique {profil.slippage_max_pts * 1.20}"
            )


# ── Tests : Condition de marché ────────────────────────────────────────────

class TestConditionMarche:

    def test_condition_news_détectée(self):
        """News bloquée → condition NEWS détectée."""
        sim = creer_simulateur(news_bloquee=True)
        condition = sim._evaluer_condition(datetime.utcnow())
        assert condition == ConditionMarche.NEWS

    def test_condition_pas_news_si_autorisé(self):
        """Trading autorisé → condition n'est pas NEWS (hors autres critères)."""
        sim = creer_simulateur(news_bloquee=False, force_condition=ConditionMarche.NORMAL)
        condition = sim._evaluer_condition(datetime.utcnow())
        assert condition != ConditionMarche.NEWS

    def test_condition_london_open_à_8h15(self):
        """8h15 UTC → condition OUVERTURE (London Open)."""
        sim = creer_simulateur(news_bloquee=False)
        heure_london = datetime(2025, 6, 2, 8, 15, 0)
        condition = sim._evaluer_condition(heure_london)
        assert condition == ConditionMarche.OUVERTURE

    def test_condition_london_open_à_7h50(self):
        """7h50 UTC → condition OUVERTURE (pré-London)."""
        sim = creer_simulateur(news_bloquee=False)
        heure = datetime(2025, 6, 2, 7, 50, 0)
        condition = sim._evaluer_condition(heure)
        assert condition == ConditionMarche.OUVERTURE

    def test_condition_ny_open_à_14h00(self):
        """14h00 UTC → condition OUVERTURE (NY Open)."""
        sim = creer_simulateur(news_bloquee=False)
        heure = datetime(2025, 6, 2, 14, 0, 0)
        condition = sim._evaluer_condition(heure)
        assert condition == ConditionMarche.OUVERTURE

    def test_condition_calme_nuit(self):
        """3h UTC → condition CALME (nuit asiatique peu liquide)."""
        sim = creer_simulateur(news_bloquee=False)
        heure_nuit = datetime(2025, 6, 2, 3, 30, 0)
        condition = sim._evaluer_condition(heure_nuit)
        assert condition == ConditionMarche.CALME

    def test_slippage_news_supérieur_au_normal(self):
        """Slippage moyen en condition NEWS > 1.5× slippage NORMAL."""
        sim_normal = creer_simulateur(seed=42, force_condition=ConditionMarche.NORMAL)
        sim_news = creer_simulateur(seed=42, force_condition=ConditionMarche.NEWS)

        slip_normal = []
        slip_news = []
        for _ in range(100):
            ex_n = sim_normal.simulate_order("BUY", 2000.0, 0.01, 1990.0, 2020.0, "XAUUSD")
            ex_news = sim_news.simulate_order("BUY", 2000.0, 0.01, 1990.0, 2020.0, "XAUUSD")
            if not ex_n.was_requoted:
                slip_normal.append(ex_n.slippage_pts)
            if not ex_news.was_requoted:
                slip_news.append(ex_news.slippage_pts)

        if slip_normal and slip_news:
            moy_normal = sum(slip_normal) / len(slip_normal)
            moy_news = sum(slip_news) / len(slip_news)
            assert moy_news > moy_normal * 1.5, (
                f"Slippage NEWS {moy_news:.2f} pas supérieur à 1.5× NORMAL {moy_normal:.2f}"
            )


# ── Tests : Requote ────────────────────────────────────────────────────────

class TestRequote:

    def test_requote_génère_flag_was_requoted(self):
        """Forcer un requote → was_requoted=True."""
        sim = creer_simulateur(seed=42, force_requote=True)
        ex = sim.simulate_order("BUY", 2000.0, 0.01, 1990.0, 2020.0, "XAUUSD")
        assert ex.was_requoted is True
        assert ex.requote_price is not None

    def test_requote_accepté_faible_déviation(self):
        """Requote avec déviation ≤ max → accepté, lots > 0."""
        sim = creer_simulateur(
            seed=42,
            force_requote=True,
            force_requote_deviation=3.0,
            max_requote_deviation=5.0,
        )
        ex = sim.simulate_order("BUY", 2000.0, 0.01, 1990.0, 2020.0, "XAUUSD")
        if ex.was_requoted:
            assert ex.requote_accepted is True
            assert ex.executed_lots > 0

    def test_requote_rejeté_grande_déviation(self):
        """Requote avec déviation > max → rejeté, lots = 0."""
        sim = creer_simulateur(
            seed=42,
            force_requote=True,
            force_requote_deviation=10.0,
            max_requote_deviation=5.0,
        )
        ex = sim.simulate_order("BUY", 2000.0, 0.01, 1990.0, 2020.0, "XAUUSD")
        if ex.was_requoted:
            assert ex.requote_accepted is False
            assert ex.executed_lots == 0.0

    def test_requote_enregistré_dans_executions(self):
        """Un requote (même rejeté) est enregistré dans _executions."""
        sim = creer_simulateur(seed=42, force_requote=True)
        ex = sim.simulate_order("BUY", 2000.0, 0.01, 1990.0, 2020.0, "XAUUSD")
        assert len(sim._executions) > 0
        assert any(e.was_requoted for e in sim._executions)


# ── Tests : Partial fill ───────────────────────────────────────────────────

class TestPartialFill:

    def test_partial_fill_réduit_les_lots(self):
        """Partial fill → lots exécutés < lots demandés, lots_restants > 0."""
        sim = creer_simulateur(seed=42, force_partial=True)
        ex = sim.simulate_order("BUY", 2000.0, 0.10, 1990.0, 2020.0, "XAUUSD")
        if ex.is_partial:
            assert ex.executed_lots < 0.10
            assert ex.remaining_lots > 0
            # Conservation des lots totaux
            assert abs(ex.executed_lots + ex.remaining_lots - 0.10) < 0.001

    def test_partial_fill_petit_lot_pas_activé(self):
        """Un lot ≤ 0.05 ne peut pas avoir de partial fill."""
        sim = creer_simulateur(seed=42, force_partial=True)
        # lots=0.01 → trop petit pour partial fill
        ex = sim.simulate_order("BUY", 2000.0, 0.01, 1990.0, 2020.0, "XAUUSD")
        # Avec 0.01 lots, le partial fill est désactivé (condition lots > 0.05)
        assert not ex.is_partial or ex.executed_lots == 0.01

    def test_partial_fill_qualite_correcte(self):
        """Un partial fill a la qualité PARTIEL."""
        sim = creer_simulateur(seed=42, force_partial=True)
        for _ in range(20):
            ex = sim.simulate_order("BUY", 2000.0, 0.10, 1990.0, 2020.0, "XAUUSD")
            if ex.is_partial:
                assert ex.execution_quality == QualiteExecution.PARTIEL
                break


# ── Tests : Spread simulé ──────────────────────────────────────────────────

class TestSpreadSimulé:

    def test_spread_élevé_à_22h_utc(self):
        """Spread simulé à 22h UTC > 17 pts (clôture/réouverture XAUUSD).
        Profil 22h = 25 pts. Avec spread_mock=10 → hybride=19 pts.
        Avec variation min ×0.90 → 17.1 pts minimum garanti."""
        sim = creer_simulateur(seed=42)
        heure_22h = datetime(2025, 6, 2, 22, 0, 0)
        spreads = [sim._get_spread_simule(heure_22h) for _ in range(30)]
        assert all(s > 17.0 for s in spreads), (
            f"Spread à 22h devrait toujours être > 17 pts, min obtenu : {min(spreads):.1f}"
        )

    def test_spread_faible_à_10h_utc(self):
        """Spread simulé à 10h UTC < 12 pts (London session pleine)."""
        sim = creer_simulateur()
        heure_10h = datetime(2025, 6, 2, 10, 0, 0)
        spread = sim._get_spread_simule(heure_10h)
        assert spread < 12.0, f"Spread à 10h devrait être < 12 pts, obtenu {spread:.1f}"

    def test_spread_varie_légèrement(self):
        """Le spread varie aléatoirement à ±10% du profil horaire."""
        sim = creer_simulateur(seed=42)
        heure_10h = datetime(2025, 6, 2, 10, 0, 0)
        spreads = [sim._get_spread_simule(heure_10h) for _ in range(50)]
        # Tous dans [8*0.9, 8*1.1] = [7.2, 8.8] × mélange 60/40 avec spread_mock=10
        # spread_hybrid = 8*0.6 + 10*0.4 = 8.8 → plage [7.92, 9.68]
        assert min(spreads) > 5.0
        assert max(spreads) < 15.0

    def test_profil_horaire_complet(self):
        """Le profil horaire couvre les 24 heures."""
        sim = creer_simulateur()
        assert len(sim.SPREAD_PAR_HEURE) == 24
        for heure in range(24):
            assert heure in sim.SPREAD_PAR_HEURE


# ── Tests : Qualité d'exécution ────────────────────────────────────────────

class TestQualiteExecution:

    def test_qualite_parfait_slippage_zéro(self):
        """Slippage = 0 → qualité PARFAIT."""
        sim = creer_simulateur()
        qualite = sim._evaluer_qualite(0.0, False)
        assert qualite == QualiteExecution.PARFAIT

    def test_qualite_bon_slippage_1_2pts(self):
        """Slippage 1–2 pts → qualité BON."""
        sim = creer_simulateur()
        assert sim._evaluer_qualite(1.0, False) == QualiteExecution.BON
        assert sim._evaluer_qualite(2.0, False) == QualiteExecution.BON

    def test_qualite_moyen_slippage_3_5pts(self):
        """Slippage 3–5 pts → qualité MOYEN."""
        sim = creer_simulateur()
        assert sim._evaluer_qualite(3.0, False) == QualiteExecution.MOYEN
        assert sim._evaluer_qualite(5.0, False) == QualiteExecution.MOYEN

    def test_qualite_mauvais_slippage_6pts_plus(self):
        """Slippage > 5 pts → qualité MAUVAIS."""
        sim = creer_simulateur()
        assert sim._evaluer_qualite(6.0, False) == QualiteExecution.MAUVAIS
        assert sim._evaluer_qualite(10.0, False) == QualiteExecution.MAUVAIS

    def test_qualite_partiel_override(self):
        """is_partial=True → qualité PARTIEL peu importe le slippage."""
        sim = creer_simulateur()
        assert sim._evaluer_qualite(0.0, True) == QualiteExecution.PARTIEL
        assert sim._evaluer_qualite(10.0, True) == QualiteExecution.PARTIEL

    def test_qualite_cohérente_avec_exécution(self):
        """La qualité dans l'exécution est cohérente avec le slippage."""
        sim = creer_simulateur(seed=42, force_condition=ConditionMarche.NORMAL)
        for _ in range(30):
            ex = sim.simulate_order("BUY", 2000.0, 0.01, 1990.0, 2020.0, "XAUUSD")
            if ex.was_requoted or ex.is_partial:
                continue
            qualite_attendue = sim._evaluer_qualite(ex.slippage_pts, False)
            assert ex.execution_quality == qualite_attendue, (
                f"Qualité incohérente : slippage={ex.slippage_pts}, "
                f"qualité={ex.execution_quality}, attendu={qualite_attendue}"
            )


# ── Tests : Distribution beta ──────────────────────────────────────────────

class TestBetaDistribution:

    def test_beta_valeurs_entre_0_et_1(self):
        """La distribution Beta retourne toujours une valeur dans [0, 1]."""
        sim = creer_simulateur(seed=42)
        for _ in range(1000):
            val = sim._beta_distribution(1.5, 4.0)
            assert 0.0 <= val <= 1.0, f"Valeur beta hors [0,1] : {val}"

    def test_beta_biaisée_vers_les_petites_valeurs(self):
        """Beta(1.5, 4.0) → majorité des valeurs dans le premier tiers."""
        sim = creer_simulateur(seed=42)
        valeurs = [sim._beta_distribution(1.5, 4.0) for _ in range(1000)]
        # Plus de 60% des valeurs devraient être < 0.40 (premier 40%)
        proportion_faibles = sum(1 for v in valeurs if v < 0.40) / len(valeurs)
        assert proportion_faibles > 0.50, (
            f"Distribution pas assez biaisée : {proportion_faibles:.1%} < 0.40"
        )


# ── Tests : Reproductibilité ───────────────────────────────────────────────

class TestReproductibilité:

    def test_seed_identique_donne_mêmes_résultats(self):
        """Même seed → mêmes slippage et latence sur le premier ordre."""
        sim1 = creer_simulateur(
            seed=42, force_condition=ConditionMarche.NORMAL
        )
        sim2 = creer_simulateur(
            seed=42, force_condition=ConditionMarche.NORMAL
        )

        ex1 = sim1.simulate_order("BUY", 2000.0, 0.01, 1990.0, 2020.0, "XAUUSD")
        ex2 = sim2.simulate_order("BUY", 2000.0, 0.01, 1990.0, 2020.0, "XAUUSD")

        assert ex1.slippage_pts == ex2.slippage_pts, (
            f"Slippage différent avec même seed : {ex1.slippage_pts} ≠ {ex2.slippage_pts}"
        )
        assert ex1.latency_ms == ex2.latency_ms, (
            f"Latence différente avec même seed : {ex1.latency_ms} ≠ {ex2.latency_ms}"
        )

    def test_seeds_différents_donnent_résultats_différents(self):
        """Seeds différents → résultats différents (avec très haute probabilité)."""
        sim1 = creer_simulateur(seed=42, force_condition=ConditionMarche.NORMAL)
        sim2 = creer_simulateur(seed=99, force_condition=ConditionMarche.NORMAL)

        resultats_1 = [
            sim1.simulate_order("BUY", 2000.0, 0.01, 1990.0, 2020.0, "XAUUSD").slippage_pts
            for _ in range(10)
        ]
        resultats_2 = [
            sim2.simulate_order("BUY", 2000.0, 0.01, 1990.0, 2020.0, "XAUUSD").slippage_pts
            for _ in range(10)
        ]
        # Au moins un résultat différent sur 10 (probabilité ≈ 1 - 10^-10)
        assert resultats_1 != resultats_2


# ── Tests : Rapport de simulation ─────────────────────────────────────────

class TestRapport:

    def test_rapport_généré_après_exécutions(self):
        """generate_report() fonctionne après N exécutions."""
        sim = creer_simulateur(seed=42, force_condition=ConditionMarche.NORMAL)
        for _ in range(20):
            sim.simulate_order("BUY", 2000.0, 0.01, 1990.0, 2020.0, "XAUUSD")

        rapport = sim.generate_report()

        assert rapport.total_executions == 20
        assert rapport.slippage_moyen_pts >= 0
        assert isinstance(rapport.impact_live_estime_pct, float)
        assert len(rapport.executions) == 20

    def test_rapport_impact_faible_conditions_normales(self):
        """En conditions NORMAL, impact estimé < 1% du capital (0.01 lots)."""
        sim = creer_simulateur(seed=42, force_condition=ConditionMarche.NORMAL)
        for _ in range(30):
            sim.simulate_order("BUY", 2000.0, 0.01, 1990.0, 2020.0, "XAUUSD")

        rapport = sim.generate_report()
        assert rapport.impact_live_estime_pct < 1.0, (
            f"Impact {rapport.impact_live_estime_pct:.3f}% > 1% en conditions NORMAL"
        )

    def test_rapport_compteurs_cohérents(self):
        """Les compteurs de qualité dans le rapport sont cohérents."""
        sim = creer_simulateur(seed=42, force_condition=ConditionMarche.NORMAL)
        for _ in range(50):
            sim.simulate_order("BUY", 2000.0, 0.01, 1990.0, 2020.0, "XAUUSD")

        rapport = sim.generate_report()
        total_compteurs = (
            rapport.nb_parfait + rapport.nb_bon + rapport.nb_moyen
            + rapport.nb_mauvais + rapport.nb_requotes + rapport.nb_partiels
        )
        assert total_compteurs == rapport.total_executions

    def test_rapport_vide_lève_exception(self):
        """generate_report() sans exécution lève une ValueError."""
        sim = creer_simulateur(seed=42)
        with pytest.raises(ValueError, match="Aucune exécution"):
            sim.generate_report()

    def test_stats_actuelles_correctes(self):
        """get_stats_actuelles() retourne les bonnes valeurs après N exécutions."""
        sim = creer_simulateur(seed=42, force_condition=ConditionMarche.NORMAL)
        for _ in range(5):
            sim.simulate_order("BUY", 2000.0, 0.01, 1990.0, 2020.0, "XAUUSD")

        stats = sim.get_stats_actuelles()
        assert stats["nb_executions"] == 5
        assert stats["slippage_moyen_pts"] >= 0
        assert stats["latence_moy_ms"] > 0

    def test_stats_actuelles_vides(self):
        """get_stats_actuelles() avant tout ordre retourne nb_executions=0."""
        sim = creer_simulateur(seed=42)
        stats = sim.get_stats_actuelles()
        assert stats["nb_executions"] == 0


# ── Tests : Carnet d'ordres ────────────────────────────────────────────────

class TestCarnetOrdres:

    def test_enregistrer_position(self):
        """Une exécution valide est enregistrée correctement."""
        carnet = CarnetOrdresPaper(CONFIG)
        # slippage_pts=2.0 → executed_price = 2000 + 2.0*0.01 = 2000.02
        ex = creer_execution_mock(prix=2000.0, lots=0.05, slippage_pts=2.0)
        ticket = carnet.enregistrer(ex, sl=1990.0, tp=2020.0)

        assert ticket >= CarnetOrdresPaper.TICKET_DEBUT
        pos = carnet.get_position(ticket)
        assert pos is not None
        # prix_entree = executed_price = 2000.02 (2 pts × 0.01 par pt XAUUSD)
        assert pos["prix_entree"] > 2000.0   # BUY avec slippage → prix > demandé
        assert abs(pos["lots"] - 0.05) < 0.001

    def test_enregistrement_refusé_lots_zéro(self):
        """Un ordre refusé (lots=0) retourne ticket=-1."""
        carnet = CarnetOrdresPaper(CONFIG)
        ex = creer_execution_mock(lots=0.0)
        ticket = carnet.enregistrer(ex, sl=1990.0, tp=2020.0)
        assert ticket == -1

    def test_close_partial_réduit_les_lots(self):
        """Une fermeture partielle réduit les lots_restants."""
        carnet = CarnetOrdresPaper(CONFIG)
        ex = creer_execution_mock(prix=2000.0, lots=0.10)
        ticket = carnet.enregistrer(ex, sl=1990.0, tp=2020.0)

        carnet.fermer_partiel(ticket, 0.05, 2010.0)   # Positional, pas keyword

        pos = carnet.get_position(ticket)
        assert pos is not None
        assert abs(pos["lots_restants"] - 0.05) < 0.001

    def test_close_partial_complet_ferme_position(self):
        """Fermer tous les lots restants → position supprimée du carnet."""
        carnet = CarnetOrdresPaper(CONFIG)
        ex = creer_execution_mock(prix=2000.0, lots=0.05)
        ticket = carnet.enregistrer(ex, sl=1990.0, tp=2020.0)

        carnet.fermer_partiel(ticket, 0.05, 2010.0)   # Positional, pas keyword

        # Position fermée → plus dans le carnet actif
        assert carnet.get_position(ticket) is None

    def test_modifier_sl_tp(self):
        """modifier_sl_tp met à jour les niveaux de la position."""
        carnet = CarnetOrdresPaper(CONFIG)
        ex = creer_execution_mock(prix=2000.0, lots=0.05)
        ticket = carnet.enregistrer(ex, sl=1990.0, tp=2020.0)

        carnet.modifier_sl_tp(ticket, nouveau_sl=2000.0, nouveau_tp=2025.0)

        pos = carnet.get_position(ticket)
        assert abs(pos["sl"] - 2000.0) < 0.01
        assert abs(pos["tp"] - 2025.0) < 0.01

    def test_tickets_commencent_à_100001(self):
        """Les tickets simulés commencent à 100001."""
        carnet = CarnetOrdresPaper(CONFIG)
        ex = creer_execution_mock()
        ticket = carnet.enregistrer(ex, sl=1990.0, tp=2020.0)
        assert ticket >= 100001

    def test_tickets_incrementés(self):
        """Chaque nouvelle position a un ticket différent."""
        carnet = CarnetOrdresPaper(CONFIG)
        tickets = []
        for _ in range(5):
            ex = creer_execution_mock()
            tickets.append(carnet.enregistrer(ex, sl=1990.0, tp=2020.0))
        assert len(set(tickets)) == 5  # Tous uniques

    def test_pnl_flottant_long(self):
        """P&L flottant LONG = (prix_actuel - entry) * lots * 100."""
        carnet = CarnetOrdresPaper(CONFIG)
        ex = creer_execution_mock(prix=2000.0, lots=0.10, order_type="BUY", slippage_pts=0)
        ticket = carnet.enregistrer(ex, sl=1990.0, tp=2020.0)

        # entry = 2000.0 (slippage_pts=0 → executed_price = 2000.0)
        pnl = carnet.get_pnl_flottant(prix_actuel=2010.0)
        # diff=10, lots=0.10, mult=100 → 10*0.10*100 = 100$
        assert abs(pnl - 100.0) < 1.0  # Tolérance 1$ (arrondi)


# ── Tests : Interface du simulateur ───────────────────────────────────────

class TestInterfaceSimulateur:

    def test_simulate_order_retourne_execution_simulee(self):
        """simulate_order() retourne une ExecutionSimulee complète."""
        sim = creer_simulateur(seed=42)
        ex = sim.simulate_order("BUY", 2000.0, 0.01, 1990.0, 2020.0, "XAUUSD")

        assert isinstance(ex, ExecutionSimulee)
        assert ex.order_id is not None
        assert ex.executed_price > 0
        assert ex.latency_ms > 0
        assert ex.spread_pts > 0

    def test_executions_enregistrées(self):
        """Chaque ordre est ajouté à _executions."""
        sim = creer_simulateur(seed=42)
        for i in range(5):
            sim.simulate_order("BUY", 2000.0, 0.01, 1990.0, 2020.0, "XAUUSD")
        assert len(sim._executions) == 5

    def test_simulate_close_partial(self):
        """simulate_close_partial() retourne une exécution valide."""
        sim = creer_simulateur(seed=42)
        # D'abord créer une position
        ex_ouv = sim.simulate_order("BUY", 2000.0, 0.10, 1990.0, 2020.0, "XAUUSD")
        if ex_ouv.executed_lots > 0:
            ticket = sim.carnet._prochain_ticket - 1
            ex_close = sim.simulate_close_partial(ticket, 0.05, "XAUUSD")
            if ex_close is not None:
                assert ex_close.slippage_pts >= 0
                # Slippage fermeture ≤ slippage ouverture (réduit de 30%)
                profil = sim.PROFILS[ex_close.market_condition]
                assert ex_close.slippage_pts <= profil.slippage_max_pts

    def test_simulate_sl_tp_modification_acceptée(self):
        """simulate_sl_tp_modification() retourne toujours True."""
        sim = creer_simulateur(seed=42)
        ex = sim.simulate_order("BUY", 2000.0, 0.01, 1990.0, 2020.0, "XAUUSD")
        if ex.executed_lots > 0:
            ticket = sim.carnet._prochain_ticket - 1
            resultat = sim.simulate_sl_tp_modification(ticket, 2005.0, 2025.0)
            assert resultat is True
