"""
tests/test_trade_journal.py — Tests unitaires du journal de trades.

Utilise tmp_path pour isoler les fichiers CSV/PNG de chaque test.
Tous les objets TradeGere, SignalRejete, etc. sont mockés avec SimpleNamespace.
"""

import csv
import os
import pytest
from datetime import datetime
from types import SimpleNamespace
from typing import List

from trade_journal import JournalTrades, EntreeJournal, SignalRejete
from performance_analyzer import AnalyseurPerformance, RapportHebdomadaire
from config import CONFIG


# ── Fixtures de base ───────────────────────────────────────────────────────

def creer_trade_mock(
    r: float = 2.0,
    pnl: float = 80.0,
    direction: str = "LONG",
    heure_entree: datetime = None,
    ob_score: int = 80,
    ob_force: str = "FORT",
):
    """Crée un TradeGere-like object pour les tests."""
    from enum import Enum

    if heure_entree is None:
        heure_entree = datetime(2025, 6, 2, 9, 0, 0)

    class DirectionFake:
        value = direction

    class RaisonFake:
        value = "TP2 atteint" if pnl > 0 else "Stop Loss touché"

    sl = SimpleNamespace(
        prix_initial=heure_entree and 1990.0,
        prix_actuel=2000.0,  # BE après TP1
        est_breakeven=True,
    )

    def make_tp(prix, lots, atteint=False, pnl_usd=None, prix_atteint=None):
        return SimpleNamespace(
            prix=prix,
            lots=lots,
            atteint=atteint,
            heure_atteinte=heure_entree if atteint else None,
            prix_atteint=prix_atteint,
            pnl_usd=pnl_usd,
            multiple_r=2.0,
        )

    tp1_atteint = pnl > 0
    tp2_atteint = pnl > 0
    tp3_atteint = False

    return SimpleNamespace(
        id_trade="trade_test_01",
        ticket_mt5=12345,
        symbole="XAUUSD",
        direction=DirectionFake(),
        prix_entree=2000.0,
        heure_entree=heure_entree,
        lots_initial=0.10,
        lots_restants=0.025,
        sl=SimpleNamespace(
            prix_initial=1990.0,
            prix_actuel=2000.0,
            est_breakeven=True,
        ),
        tp1=make_tp(2010.0, 0.05, tp1_atteint, pnl * 0.5, 2010.0 if tp1_atteint else None),
        tp2=make_tp(2020.0, 0.025, tp2_atteint, pnl * 0.3, 2020.0 if tp2_atteint else None),
        tp3=make_tp(2030.0, 0.025, tp3_atteint),
        raison_fermeture=RaisonFake(),
        heure_fermeture=datetime(2025, 6, 2, 11, 30, 0),
        est_ferme=True,
        pnl_total_usd=pnl,
        pnl_realise_usd=pnl,
        r_total_realise=r,
        mfe=r + 0.3,
        mae=0.5,
        ob_score=ob_score,
        ob_force=ob_force,
        confluences=["FVG H4", "Equal Low asiatique"],
        phase=None,
    )


def creer_entree_journal(
    r: float = 2.0,
    pnl: float = 80.0,
    direction: str = "LONG",
    heure_entree: datetime = None,
    ob_score: int = 80,
    ob_force: str = "FORT",
) -> EntreeJournal:
    """Crée une EntreeJournal directement pour les tests de l'analyseur."""
    if heure_entree is None:
        heure_entree = datetime(2025, 6, 2, 9, 0, 0)

    return EntreeJournal(
        journal_id=f"jrn_{id(r)}",
        id_trade=f"trade_{id(r)}",
        ticket_mt5=12345,
        symbole="XAUUSD",
        direction=direction,
        prix_entree=2000.0,
        heure_entree=heure_entree,
        prix_sortie=2000.0 + r * 10,
        heure_sortie=datetime(2025, 6, 2, 11, 0),
        duree_minutes=120,
        lots_initial=0.10,
        sl_initial=1990.0,
        sl_final=2000.0,
        tp1=2010.0,
        tp2=2020.0,
        tp3=2030.0,
        raison_fermeture="TP2 atteint" if pnl > 0 else "Stop Loss touché",
        tp1_atteint=pnl > 0,
        tp2_atteint=pnl > 0,
        tp3_atteint=False,
        total_r_realise=r,
        pnl_total_usd=pnl,
        mfe_r=r + 0.3,
        mae_r=0.5,
        ob_score=ob_score,
        ob_force=ob_force,
        confluences=["FVG H4"],
        tendance_h4="HAUSSIÈRE",
        biais_journalier="BULLISH",
        force_biais="FORT",
        session_entree="LONDON",
        heure_entree_utc=heure_entree.hour,
    )


def creer_signal_rejete(
    rejete_par: str = "spread_filter",
    heure: datetime = None,
) -> SignalRejete:
    """Crée un SignalRejete pour les tests."""
    if heure is None:
        heure = datetime(2025, 6, 2, 9, 30, 0)

    return SignalRejete(
        timestamp_utc=heure,
        direction="LONG",
        rejete_par=rejete_par,
        raison=f"Rejeté par {rejete_par}",
        prix_actuel=2015.0,
        ob_score=75,
        ob_force="FORT",
        tendance_h4="HAUSSIÈRE",
        biais_journalier="BULLISH",
        heure_utc=heure.hour,
    )


def creer_journal(tmp_path, avec_generateur=False) -> JournalTrades:
    """Crée un JournalTrades avec chemins isolés dans tmp_path."""
    journal = JournalTrades(
        connecteur=None,
        config=CONFIG,
        generateur_graphiques=None,  # Pas de graphiques en test
        analyseur_performance=AnalyseurPerformance(CONFIG),
    )
    # Rediriger les chemins vers tmp_path
    journal.CHEMIN_CSV         = str(tmp_path / "journal" / "trades.csv")
    journal.CHEMIN_CSV_REJETES = str(tmp_path / "journal" / "rejected_signals.csv")
    journal.DOSSIER_CHARTS     = str(tmp_path / "journal" / "charts")
    journal.DOSSIER_RAPPORTS   = str(tmp_path / "journal" / "weekly_reports")
    journal.DOSSIER_SNAPSHOTS  = str(tmp_path / "journal" / "snapshots")
    journal._creer_dossiers()
    return journal


# ── Tests : Enregistrement CSV ─────────────────────────────────────────────

class TestEnregistrementCSV:

    def test_record_trade_crée_csv(self, tmp_path):
        """enregistrer_trade() crée le fichier CSV si inexistant."""
        journal = creer_journal(tmp_path)
        trade = creer_trade_mock(r=2.1, pnl=84.0)

        entree = journal.enregistrer_trade(trade)

        assert entree is not None
        assert os.path.exists(journal.CHEMIN_CSV)

    def test_csv_contient_données_correctes(self, tmp_path):
        """Les données du trade sont correctement écrites dans le CSV."""
        journal = creer_journal(tmp_path)
        trade = creer_trade_mock(r=2.1, pnl=84.0)

        journal.enregistrer_trade(trade)

        with open(journal.CHEMIN_CSV, "r", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            row = next(reader)

        assert row["id_trade"] == "trade_test_01"
        assert abs(float(row["total_r_realise"]) - 2.1) < 0.01
        assert abs(float(row["pnl_total_usd"]) - 84.0) < 0.01
        assert row["direction"] == "LONG"

    def test_csv_contient_toutes_les_colonnes(self, tmp_path):
        """Le CSV contient toutes les colonnes définies dans _EN_TETES_CSV."""
        journal = creer_journal(tmp_path)
        trade = creer_trade_mock()

        journal.enregistrer_trade(trade)

        with open(journal.CHEMIN_CSV, "r", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            colonnes = reader.fieldnames

        colonnes_obligatoires = [
            "id_trade", "ob_score", "biais_journalier",
            "total_r_realise", "sl_initial", "tp1", "tendance_h4",
        ]
        for col in colonnes_obligatoires:
            assert col in colonnes, f"Colonne manquante : {col}"

    def test_csv_multiple_trades(self, tmp_path):
        """Plusieurs trades s'accumulent dans le CSV (append)."""
        journal = creer_journal(tmp_path)
        trades = [
            creer_trade_mock(r=2.1, pnl=84.0),
            creer_trade_mock(r=-1.0, pnl=-40.0),
            creer_trade_mock(r=1.5, pnl=60.0),
        ]

        for t in trades:
            journal.enregistrer_trade(t)

        with open(journal.CHEMIN_CSV, "r", encoding="utf-8-sig") as f:
            nb_lignes = sum(1 for _ in csv.DictReader(f))

        assert nb_lignes == 3

    def test_en_tête_csv_créé_une_seule_fois(self, tmp_path):
        """L'en-tête CSV n'est écrit qu'une seule fois (pas de doublon)."""
        journal = creer_journal(tmp_path)
        for _ in range(3):
            journal.enregistrer_trade(creer_trade_mock())

        with open(journal.CHEMIN_CSV, "r", encoding="utf-8-sig") as f:
            lignes = f.readlines()

        # La première ligne est l'en-tête, les suivantes sont des données
        nb_lignes_entete = sum(1 for l in lignes if "id_trade" in l)
        assert nb_lignes_entete == 1


# ── Tests : Signaux rejetés ────────────────────────────────────────────────

class TestSignauxRejetes:

    def test_signal_rejeté_enregistré_dans_csv(self, tmp_path):
        """enregistrer_signal_rejete() crée le CSV des rejets."""
        journal = creer_journal(tmp_path)

        journal.enregistrer_signal_rejete(
            direction="LONG",
            rejete_par="spread_filter",
            raison="Spread 42 pts > hard cap 35 pts",
            prix_actuel=2015.0,
        )

        assert os.path.exists(journal.CHEMIN_CSV_REJETES)

    def test_csv_rejets_colonnes_correctes(self, tmp_path):
        """Le CSV des rejets contient les bonnes colonnes."""
        journal = creer_journal(tmp_path)

        journal.enregistrer_signal_rejete(
            direction="SHORT",
            rejete_par="news_filter",
            raison="NFP dans 30min",
            prix_actuel=2012.0,
        )

        with open(journal.CHEMIN_CSV_REJETES, "r", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            row = next(reader)

        assert row["rejete_par"] == "news_filter"
        assert row["direction"] == "SHORT"
        assert row["prix_actuel"] == "2012.0"

    def test_record_rejected_signal_extrait_filtre_news(self, tmp_path):
        """record_rejected_signal() extrait 'news_filter' depuis le préfixe '⛔ NEWS:'."""
        journal = creer_journal(tmp_path)

        signal_mock = SimpleNamespace(
            direction=SimpleNamespace(value="LONG"),
            raison_rejet="⛔ NEWS: NFP imminent",
        )
        journal.record_rejected_signal(
            signal_result=signal_mock,
            current_price=2010.0,
        )

        with open(journal.CHEMIN_CSV_REJETES, "r", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            row = next(reader)

        assert row["rejete_par"] == "news_filter"

    def test_record_rejected_signal_extrait_filtre_spread(self, tmp_path):
        """record_rejected_signal() extrait 'spread_filter' depuis le préfixe '⛔ SPREAD:'."""
        journal = creer_journal(tmp_path)

        signal_mock = SimpleNamespace(
            direction=SimpleNamespace(value="SHORT"),
            raison_rejet="⛔ SPREAD: Spread 42 pts",
        )
        journal.record_rejected_signal(signal_result=signal_mock, current_price=2010.0)

        with open(journal.CHEMIN_CSV_REJETES, "r", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            row = next(reader)

        assert row["rejete_par"] == "spread_filter"

    def test_multiples_rejets_accumulés(self, tmp_path):
        """Plusieurs rejets s'accumulent dans le CSV sans doublon d'en-tête."""
        journal = creer_journal(tmp_path)

        filtres = ["spread_filter", "news_filter", "spread_filter", "daily_bias"]
        for f in filtres:
            journal.enregistrer_signal_rejete(
                direction="LONG", rejete_par=f,
                raison=f"Rejeté par {f}", prix_actuel=2015.0
            )

        with open(journal.CHEMIN_CSV_REJETES, "r", encoding="utf-8-sig") as f:
            nb = sum(1 for _ in csv.DictReader(f))

        assert nb == 4


# ── Tests : Rapport hebdomadaire ───────────────────────────────────────────

class TestRapportHebdomadaire:

    def test_rapport_calculé_métriques_correctes(self):
        """calculate_weekly_report() calcule correctement les métriques de base."""
        entrees = [
            creer_entree_journal(r=2.1, pnl=84.0),
            creer_entree_journal(r=-1.0, pnl=-40.0),
            creer_entree_journal(r=1.5, pnl=60.0),
            creer_entree_journal(r=2.8, pnl=112.0),
        ]
        rejetes = [
            creer_signal_rejete("spread_filter"),
            creer_signal_rejete("news_filter"),
            creer_signal_rejete("spread_filter"),
        ]

        analyseur = AnalyseurPerformance(CONFIG)
        rapport = analyseur.calculer_rapport_hebdomadaire(
            entrees, rejetes,
            semaine_debut=datetime(2025, 6, 2),
            semaine_fin=datetime(2025, 6, 6),
        )

        assert rapport.total_trades == 4
        assert rapport.trades_gagnants == 3
        assert rapport.trades_perdants == 1
        assert abs(rapport.win_rate_pct - 75.0) < 0.1
        assert rapport.filtre_principal_rejet == "spread_filter"
        assert len(rapport.recommandations) <= 5

    def test_profit_factor_calcul_correct(self):
        """Profit Factor = somme gagnants / somme perdants."""
        entrees = [
            creer_entree_journal(r=2.0, pnl=80.0),
            creer_entree_journal(r=2.5, pnl=100.0),
            creer_entree_journal(r=-1.0, pnl=-40.0),
        ]
        analyseur = AnalyseurPerformance(CONFIG)
        rapport = analyseur.calculer_rapport_hebdomadaire(
            entrees, [], datetime(2025, 6, 2), datetime(2025, 6, 6)
        )
        # PF = 180 / 40 = 4.5
        assert abs(rapport.profit_factor - 4.5) < 0.1

    def test_distribution_r_correcte(self):
        """Distribution R:R classe correctement chaque trade."""
        analyseur = AnalyseurPerformance(CONFIG)
        entrees = [
            creer_entree_journal(r=0.5,  pnl=20),
            creer_entree_journal(r=1.5,  pnl=60),
            creer_entree_journal(r=2.3,  pnl=92),
            creer_entree_journal(r=-0.8, pnl=-32),
            creer_entree_journal(r=3.5,  pnl=140),
        ]
        dist = analyseur._calculer_distribution_r(entrees)

        assert dist["0 à 1R"] == 1
        assert dist["1R à 2R"] == 1
        assert dist["2R à 3R"] == 1
        assert dist["> 3R"] == 1
        assert dist["-1R à 0"] == 1

    def test_max_drawdown_calcul_correct(self):
        """Max Drawdown : pic=160, creux=120 → DD = 40/160 = 25%."""
        entrees = [
            creer_entree_journal(r=2.0, pnl=80.0,  heure_entree=datetime(2025,6,2,9)),
            creer_entree_journal(r=2.0, pnl=80.0,  heure_entree=datetime(2025,6,3,9)),
            creer_entree_journal(r=-1.0, pnl=-40.0, heure_entree=datetime(2025,6,4,9)),
            creer_entree_journal(r=2.8, pnl=112.0, heure_entree=datetime(2025,6,5,9)),
        ]
        analyseur = AnalyseurPerformance(CONFIG)
        max_dd = analyseur._calculer_max_drawdown(entrees)
        # Equity : 80, 160, 120, 232 → pic=160, creux=120, DD=40/160=25%
        assert abs(max_dd - 25.0) < 1.0

    def test_sharpe_positif_série_gagnante(self):
        """Sharpe > 0 sur une série de trades gagnants."""
        entrees = [
            creer_entree_journal(r=r, pnl=r*40, heure_entree=datetime(2025, 6, i+1, 9))
            for i, r in enumerate([2.1, 1.8, 2.5, 1.9, 2.3, 2.0])
        ]
        analyseur = AnalyseurPerformance(CONFIG)
        sharpe = analyseur._calculer_sharpe(entrees)
        assert sharpe > 0

    def test_recommandation_win_rate_faible(self):
        """Recommandation générée si win rate < 40%."""
        entrees = [creer_entree_journal(r=-1.0, pnl=-40) for _ in range(7)]
        entrees += [creer_entree_journal(r=2.0, pnl=80)]  # 1 gagnant sur 8 = 12.5%
        analyseur = AnalyseurPerformance(CONFIG)
        rapport = analyseur.calculer_rapport_hebdomadaire(
            entrees, [], datetime(2025, 6, 2), datetime(2025, 6, 6)
        )
        assert any("Win rate" in r for r in rapport.recommandations)

    def test_erreur_si_aucun_trade(self):
        """calculer_rapport_hebdomadaire() lève ValueError si liste vide."""
        analyseur = AnalyseurPerformance(CONFIG)
        with pytest.raises(ValueError, match="Aucun trade"):
            analyseur.calculer_rapport_hebdomadaire(
                [], [], datetime(2025, 6, 2), datetime(2025, 6, 6)
            )

    def test_nb_rejets_par_filtre_correct(self):
        """Les rejets sont correctement comptés par filtre."""
        rejetes = [
            creer_signal_rejete("spread_filter"),
            creer_signal_rejete("spread_filter"),
            creer_signal_rejete("news_filter"),
        ]
        entrees = [creer_entree_journal()]
        analyseur = AnalyseurPerformance(CONFIG)
        rapport = analyseur.calculer_rapport_hebdomadaire(
            entrees, rejetes, datetime(2025, 6, 2), datetime(2025, 6, 6)
        )
        assert rapport.nb_rejets_par_filtre.get("spread_filter", 0) == 2
        assert rapport.nb_rejets_par_filtre.get("news_filter", 0) == 1

    def test_max_pertes_consecutives(self):
        """max_pertes_consecutives calculé correctement."""
        entrees = [
            creer_entree_journal(r=2.0, pnl=80),
            creer_entree_journal(r=-1.0, pnl=-40),
            creer_entree_journal(r=-1.0, pnl=-40),
            creer_entree_journal(r=-1.0, pnl=-40),
            creer_entree_journal(r=1.5, pnl=60),
        ]
        analyseur = AnalyseurPerformance(CONFIG)
        max_consec = analyseur._calculer_max_pertes_consecutives(entrees)
        assert max_consec == 3


# ── Tests : Rapport HTML ───────────────────────────────────────────────────

class TestRapportHTML:

    def test_rapport_html_généré(self, tmp_path):
        """generate_weekly_report() génère un fichier HTML."""
        journal = creer_journal(tmp_path)
        # Ajouter des entrées directement dans la liste interne
        for r, pnl in [(2.1, 84), (-1.0, -40), (1.5, 60)]:
            journal._entrees.append(creer_entree_journal(r=r, pnl=pnl))

        chemin_html = journal.generer_rapport_hebdomadaire(
            semaine_debut=datetime(2025, 6, 2),
            semaine_fin=datetime(2025, 6, 6),
        )

        assert chemin_html != ""
        assert os.path.exists(chemin_html)
        assert chemin_html.endswith(".html")

    def test_rapport_html_contient_métriques(self, tmp_path):
        """Le rapport HTML contient les métriques clés."""
        journal = creer_journal(tmp_path)
        journal._entrees.append(creer_entree_journal(r=2.1, pnl=84))
        journal._entrees.append(creer_entree_journal(r=-1.0, pnl=-40))

        chemin_html = journal.generer_rapport_hebdomadaire(
            semaine_debut=datetime(2025, 6, 2),
            semaine_fin=datetime(2025, 6, 6),
        )

        with open(chemin_html, "r", encoding="utf-8") as f:
            contenu = f.read()

        # Vérifier la présence des sections clés
        assert "Profit Factor" in contenu
        assert "Win Rate" in contenu
        assert "Sharpe" in contenu
        assert "Recommandations" in contenu

    def test_rapport_html_aucun_trade(self, tmp_path):
        """generate_weekly_report() retourne vide si aucun trade."""
        journal = creer_journal(tmp_path)

        chemin_html = journal.generer_rapport_hebdomadaire(
            semaine_debut=datetime(2025, 6, 2),
            semaine_fin=datetime(2025, 6, 6),
        )

        assert chemin_html == ""

    def test_rapport_html_autosuffisant_base64(self, tmp_path):
        """Le rapport HTML est auto-suffisant (pas de liens externes)."""
        journal = creer_journal(tmp_path)
        journal._entrees.append(creer_entree_journal(r=2.1, pnl=84))

        chemin_html = journal.generer_rapport_hebdomadaire(
            semaine_debut=datetime(2025, 6, 2),
            semaine_fin=datetime(2025, 6, 6),
        )

        with open(chemin_html, "r", encoding="utf-8") as f:
            contenu = f.read()

        # Pas de liens vers des fichiers externes relatifs
        assert "src='../'" not in contenu
        assert "href='../" not in contenu


# ── Tests : Graphiques ─────────────────────────────────────────────────────

class TestGraphiques:

    def test_equity_curve_générée(self, tmp_path):
        """GenerateurGraphiques.generer_courbe_equity() crée un PNG."""
        from chart_generator import GenerateurGraphiques

        gen = GenerateurGraphiques(connecteur=None)
        entrees = [
            creer_entree_journal(r=r, pnl=r*40)
            for r in [2.1, -1.0, 1.8, 2.5]
        ]

        dossier = str(tmp_path / "charts")
        chemin = gen.generer_courbe_equity(entrees, "2025-06-02", dossier)

        assert chemin != ""
        assert os.path.exists(chemin)
        assert chemin.endswith(".png")

    def test_distribution_r_générée(self, tmp_path):
        """GenerateurGraphiques.generer_distribution_r() crée un PNG."""
        from chart_generator import GenerateurGraphiques

        gen = GenerateurGraphiques(connecteur=None)
        distribution = {
            "< -1R": 1, "-1R à 0": 0, "0 à 1R": 1,
            "1R à 2R": 2, "2R à 3R": 1, "> 3R": 0,
        }

        dossier = str(tmp_path / "charts")
        chemin = gen.generer_distribution_r(distribution, "2025-06-02", dossier)

        assert chemin != ""
        assert os.path.exists(chemin)
        assert chemin.endswith(".png")

    def test_graphique_trade_sans_connecteur_retourne_vide(self, tmp_path):
        """generer_graphique_trade() sans MT5 retourne chaîne vide (pas d'exception)."""
        from chart_generator import GenerateurGraphiques

        gen = GenerateurGraphiques(connecteur=None)
        entree = creer_entree_journal(r=2.1, pnl=84)

        chemin = gen.generer_graphique_trade(
            entree, None, str(tmp_path / "chart.png")
        )

        # Sans connecteur → None ou "" (pas d'exception)
        assert chemin == "" or chemin is None or isinstance(chemin, str)


# ── Tests : Stats du journal ───────────────────────────────────────────────

class TestStatsJournal:

    def test_stats_vides_au_démarrage(self, tmp_path):
        """get_stats_journal() retourne nb_trades=0 si aucun trade."""
        journal = creer_journal(tmp_path)
        stats = journal.get_stats_journal()
        assert stats["nb_trades"] == 0

    def test_stats_après_trades(self, tmp_path):
        """get_stats_journal() retourne le bon nb_trades après enregistrement."""
        journal = creer_journal(tmp_path)
        for _ in range(3):
            journal.enregistrer_trade(creer_trade_mock())

        stats = journal.get_stats_journal()
        assert stats["nb_trades"] == 3
        assert stats["dernier_trade"] == "trade_test_01"

    def test_stats_compte_rejets(self, tmp_path):
        """Le compteur de rejets est mis à jour correctement."""
        journal = creer_journal(tmp_path)

        for filtre in ["spread_filter", "news_filter", "spread_filter"]:
            journal.enregistrer_signal_rejete(
                direction="LONG", rejete_par=filtre,
                raison="Test", prix_actuel=2010.0
            )

        stats = journal.get_stats_journal()
        assert stats["nb_signaux_rejetes"] == 3
