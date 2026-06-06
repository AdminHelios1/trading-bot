"""
test_level_confluence.py — Tests unitaires du calcul de confluence multi-sources
et de l'analyseur d'obstacles.
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.modules.setdefault("MetaTrader5", MagicMock())

from level_confluence import CalculateurConfluence, SourceNiveau, NiveauConfluent
from obstacle_analyzer import AnalyseurObstacles


# ── Helpers ────────────────────────────────────────────────────────────────

def creer_calculateur() -> CalculateurConfluence:
    return CalculateurConfluence()


def creer_analyseur() -> AnalyseurObstacles:
    return AnalyseurObstacles()


# ── Tests CalculateurConfluence ────────────────────────────────────────────

class TestCalculateurConfluence:
    """Tests du calcul de confluence multi-sources."""

    def test_niveaux_proches_regroupes(self):
        """Deux niveaux à moins de 0.08% → regroupés en un seul."""
        calc = creer_calculateur()
        niveaux = [
            (2010.0, SourceNiveau.ORDER_BLOCK_H4),
            (2010.1, SourceNiveau.ASIE_EQUAL_BAS),  # 0.005% de différence < 0.08%
        ]
        carte = calc.construire_carte_confluence(niveaux, tolerance_pct=0.08)
        assert len(carte) == 1
        assert carte[0].score_confluence >= 2

    def test_niveaux_eloignes_separes(self):
        """Deux niveaux à plus de 0.08% → deux entrées distinctes."""
        calc = creer_calculateur()
        niveaux = [
            (2010.0, SourceNiveau.ORDER_BLOCK_H4),
            (2020.0, SourceNiveau.ASIE_HAUT),  # Très éloigné
        ]
        carte = calc.construire_carte_confluence(niveaux)
        assert len(carte) == 2

    def test_bonus_ob_et_asiatique(self):
        """OB + niveau asiatique → bonus +2 sur le score."""
        calc = creer_calculateur()
        niveaux = [
            (2010.0, SourceNiveau.ORDER_BLOCK_H4),
            (2010.05, SourceNiveau.ASIE_EQUAL_BAS),
        ]
        carte = calc.construire_carte_confluence(niveaux)
        assert len(carte) >= 1
        # Score = 2 (nb sources) + 2 (bonus OB+asiatique) = 4
        assert carte[0].score_confluence >= 4

    def test_triple_confluence_bonus(self):
        """3 sources → bonus supplémentaire."""
        calc = creer_calculateur()
        niveaux = [
            (2010.0, SourceNiveau.ORDER_BLOCK_H4),
            (2010.05, SourceNiveau.ASIE_EQUAL_BAS),
            (2010.02, SourceNiveau.SWING_BAS_H4),
        ]
        carte = calc.construire_carte_confluence(niveaux)
        assert carte[0].score_confluence >= 4  # 3 sources + bonus ob+asie + bonus triple

    def test_carte_vide_si_aucun_niveau(self):
        """Liste vide → retourner liste vide."""
        calc = creer_calculateur()
        carte = calc.construire_carte_confluence([])
        assert carte == []

    def test_carte_triee_par_score_decroissant(self):
        """Carte triée par score décroissant."""
        calc = creer_calculateur()
        niveaux = [
            (2010.0, SourceNiveau.ORDER_BLOCK_H4),
            (2010.05, SourceNiveau.ASIE_EQUAL_BAS),   # Confluent (score élevé)
            (2020.0, SourceNiveau.ATR_BASE),           # Isolé (score bas)
        ]
        carte = calc.construire_carte_confluence(niveaux)
        for i in range(len(carte) - 1):
            assert carte[i].score_confluence >= carte[i + 1].score_confluence

    def test_trouver_niveau_proche(self):
        """find_best_level_near() trouve le niveau le plus pertinent."""
        calc = creer_calculateur()
        niveaux = [
            (2010.0, SourceNiveau.ORDER_BLOCK_H4),
            (2010.05, SourceNiveau.ASIE_EQUAL_BAS),
            (2025.0, SourceNiveau.ASIE_HAUT),
        ]
        carte = calc.construire_carte_confluence(niveaux)
        # Chercher près de 2010.03
        trouve = calc.trouver_meilleur_niveau_proche(2010.03, carte, fenetre_pct=0.5)
        assert trouve is not None
        assert abs(trouve.prix - 2010.0) < 1.0

    def test_aucun_niveau_proche_retourne_none(self):
        """Aucun niveau dans la fenêtre → None."""
        calc = creer_calculateur()
        niveaux = [(2010.0, SourceNiveau.ORDER_BLOCK_H4)]
        carte = calc.construire_carte_confluence(niveaux)
        trouve = calc.trouver_meilleur_niveau_proche(2100.0, carte, fenetre_pct=0.1)
        assert trouve is None

    def test_scorer_niveau_base_asiatique_bonus(self):
        """BSL/SSL asiatique → bonus +20 dans le score."""
        calc = creer_calculateur()
        niveaux = [
            (2025.0, SourceNiveau.ASIE_BSL),
            (2025.0, SourceNiveau.ORDER_BLOCK_H4),
        ]
        carte = calc.construire_carte_confluence(niveaux)
        score = calc.scorer_niveau(2025.0, SourceNiveau.ASIE_BSL, carte)
        assert score >= 40  # Niveau confluent avec bonus liquidité

    def test_scorer_niveau_isole_score_faible(self):
        """Niveau isolé → score de base 20."""
        calc = creer_calculateur()
        carte = calc.construire_carte_confluence([(2010.0, SourceNiveau.ORDER_BLOCK_H4)])
        score = calc.scorer_niveau(2050.0, SourceNiveau.ATR_BASE, carte)
        assert score == 20

    def test_alias_anglais_compatibles(self):
        """Les alias anglais (build_confluence_map, find_best_level_near, score_level) fonctionnent."""
        calc = creer_calculateur()
        niveaux = [(2010.0, SourceNiveau.ORDER_BLOCK_H4)]
        carte = calc.build_confluence_map(niveaux)
        assert len(carte) >= 1
        trouve = calc.find_best_level_near(2010.0, carte)
        assert trouve is not None
        score = calc.score_level(2010.0, SourceNiveau.ORDER_BLOCK_H4, carte)
        assert isinstance(score, int)


# ── Tests AnalyseurObstacles ───────────────────────────────────────────────

class TestAnalyseurObstacles:
    """Tests de la détection d'obstacles entre entrée et TP."""

    def test_obstacle_detecte_au_milieu(self):
        """Résistance à 50% entre entrée et TP → détectée."""
        analyseur = creer_analyseur()
        niveaux = [(2028.0, SourceNiveau.ASIE_HAUT)]
        obstacles = analyseur.trouver_obstacles(
            entree=2018.0, tp=2038.0, direction="bullish",
            niveaux=niveaux
        )
        assert len(obstacles) == 1
        assert abs(obstacles[0]["position_pct"] - 50.0) < 1.0

    def test_obstacle_ignore_trop_pres_entree(self):
        """Résistance à 5% de l'entrée → ignorée (< 10%)."""
        analyseur = creer_analyseur()
        niveaux = [(2019.0, SourceNiveau.ASIE_HAUT)]
        # Position = (2019-2018)/(2038-2018) = 5% < 10% → ignoré
        obstacles = analyseur.trouver_obstacles(2018.0, 2038.0, "bullish", niveaux)
        assert len(obstacles) == 0

    def test_obstacle_ignore_trop_pres_tp(self):
        """Résistance à 90% vers le TP → ignorée (> 85%)."""
        analyseur = creer_analyseur()
        niveaux = [(2036.0, SourceNiveau.ASIE_HAUT)]
        # Position = (2036-2018)/(2038-2018) = 90% > 85% → ignoré
        obstacles = analyseur.trouver_obstacles(2018.0, 2038.0, "bullish", niveaux)
        assert len(obstacles) == 0

    def test_obstacle_hors_trajectoire_ignore(self):
        """Niveau en dehors de la trajectoire → ignoré."""
        analyseur = creer_analyseur()
        niveaux = [(2000.0, SourceNiveau.ASIE_HAUT)]  # En dessous de l'entrée
        obstacles = analyseur.trouver_obstacles(2018.0, 2038.0, "bullish", niveaux)
        assert len(obstacles) == 0

    def test_obstacle_severite_major_bsl(self):
        """BSL asiatique → sévérité MAJOR."""
        analyseur = creer_analyseur()
        niveaux = [(2028.0, SourceNiveau.ASIE_BSL)]
        obstacles = analyseur.trouver_obstacles(2018.0, 2038.0, "bullish", niveaux)
        assert len(obstacles) >= 1
        assert obstacles[0]["severity"] == "major"

    def test_obstacle_severite_minor_high_asiatique(self):
        """High asiatique simple → sévérité MINOR."""
        analyseur = creer_analyseur()
        niveaux = [(2028.0, SourceNiveau.ASIE_HAUT)]
        obstacles = analyseur.trouver_obstacles(2018.0, 2038.0, "bullish", niveaux)
        if obstacles:
            assert obstacles[0]["severity"] == "minor"

    def test_a_obstacle_majeur_vrai(self):
        """has_major_obstacle() retourne True si obstacle MAJOR."""
        analyseur = creer_analyseur()
        obstacles = [{"severity": "major", "price": 2028.0, "position_pct": 50.0, "description": "test"}]
        assert analyseur.a_obstacle_majeur(obstacles) is True

    def test_a_obstacle_majeur_faux(self):
        """has_major_obstacle() retourne False si seulement MINOR."""
        analyseur = creer_analyseur()
        obstacles = [{"severity": "minor", "price": 2028.0, "position_pct": 50.0, "description": "test"}]
        assert analyseur.a_obstacle_majeur(obstacles) is False

    def test_suggerer_tp_ajuste_valide(self):
        """TP ajusté respectant le R:R minimum → retourné."""
        analyseur = creer_analyseur()
        # Obstacle à 2030, marge = 10 × 0.3 = 3 → TP ajusté = 2030 - 3 = 2027
        # Avec entrée=2018, distance_sl=5 → R:R = (2027-2018)/5 = 1.8 >= 1.5 ✓
        obstacles = [{"severity": "major", "price": 2030.0, "position_pct": 60.0,
                      "description": "BSL @ 2030"}]
        tp_ajuste = analyseur.suggerer_tp_ajuste(
            entree=2018.0, tp_original=2038.0, obstacles=obstacles,
            direction="bullish", rr_min=1.5, distance_sl=5.0
        )
        assert tp_ajuste is not None
        assert tp_ajuste < 2030.0  # Avant l'obstacle

    def test_suggerer_tp_ajuste_rr_insuffisant(self):
        """TP ajusté trop proche → None (R:R insuffisant)."""
        analyseur = creer_analyseur()
        # Obstacle très proche de l'entrée → TP ajusté < RR minimum
        obstacles = [{"severity": "major", "price": 2020.5, "position_pct": 15.0,
                      "description": "BSL @ 2020.5"}]
        tp_ajuste = analyseur.suggerer_tp_ajuste(
            entree=2018.0, tp_original=2038.0, obstacles=obstacles,
            direction="bullish", rr_min=2.0, distance_sl=10.0
        )
        assert tp_ajuste is None

    def test_obstacle_short_cherche_support(self):
        """Pour SHORT : cherche les supports (ASIE_BAS, SSL, etc.), pas les résistances."""
        analyseur = creer_analyseur()
        # Pour SHORT : entrée=2038, tp=2018, obstacle=2028 (support)
        niveaux = [
            (2028.0, SourceNiveau.ASIE_BAS),    # Support → obstacle pour SHORT
            (2045.0, SourceNiveau.ASIE_HAUT),   # Résistance → PAS un obstacle pour SHORT
        ]
        obstacles = analyseur.trouver_obstacles(2038.0, 2018.0, "bearish", niveaux)
        # Seul ASIE_BAS devrait être un obstacle
        prix_obstacles = [o["price"] for o in obstacles]
        assert 2028.0 in prix_obstacles
        assert 2045.0 not in prix_obstacles

    def test_alias_anglais_compatibles(self):
        """Les alias anglais fonctionnent."""
        analyseur = creer_analyseur()
        obstacles = analyseur.find_obstacles(2018.0, 2038.0, "bullish", [])
        assert isinstance(obstacles, list)
        assert analyseur.has_major_obstacle([]) is False


# ── Tests intégration CalculateurNiveaux ──────────────────────────────────

class ObSimple:
    """Zone OB simplifiée pour les tests (sans MagicMock)."""
    def __init__(self, bas: float, haut: float):
        self.zone_entree_bas = bas
        self.zone_entree_haut = haut
        self.zone_h4 = None
        self.zone_h1 = None


class TestCalculateurNiveaux:
    """Tests du calculateur de niveaux enrichi."""

    def test_calculer_niveaux_sans_session_asie(self):
        """Sans session asiatique → fonctionne avec OB + structure."""
        from risk_manager import CalculateurNiveaux

        calc = CalculateurNiveaux(connecteur=None)
        ob = ObSimple(bas=2010.0, haut=2015.0)

        res = calc.calculer_niveaux(
            prix_entree=2018.0, direction="bullish",
            zone_ob=ob, session_asie=None, structure=None, atr_m15=5.0,
        )
        assert res.stop_loss < 2018.0
        assert res.tp1 > 2018.0
        assert res.tp2 > res.tp1

    def test_calculer_niveaux_sl_sous_equal_low(self):
        """Avec equal low asiatique → SL candidate depuis equal low."""
        from risk_manager import CalculateurNiveaux, SourceNiveau
        from asian_session import DonneesSessionAsiatique

        calc = CalculateurNiveaux(connecteur=None)
        ob = ObSimple(bas=2013.0, haut=2016.0)

        session = DonneesSessionAsiatique(
            date_utc="2025-02-07",
            haut_session=2025.0, bas_session=2015.0,
            range_pips=100.0, ouverture_session=2018.0,
            fermeture_session=2022.0, milieu_range=2020.0,
            equal_hauts=[2024.5], equal_bas=[2010.0],
            pools_liquidite=[], range_compresse=False,
        )

        res = calc.calculer_niveaux(
            prix_entree=2018.0, direction="bullish",
            zone_ob=ob, session_asie=session, structure=None, atr_m15=5.0,
        )
        assert res.stop_loss < 2015.0

    def test_calculer_niveaux_tp2_bsl_asiatique(self):
        """BSL asiatique dans la fenêtre R:R → utilisé comme TP2."""
        from risk_manager import CalculateurNiveaux, SourceNiveau
        from asian_session import DonneesSessionAsiatique

        calc = CalculateurNiveaux(connecteur=None)
        ob = ObSimple(bas=2013.0, haut=2016.0)

        session = DonneesSessionAsiatique(
            date_utc="2025-02-07",
            haut_session=2025.0, bas_session=2015.0,
            range_pips=100.0, ouverture_session=2018.0,
            fermeture_session=2022.0, milieu_range=2020.0,
            equal_hauts=[2024.5], equal_bas=[2010.0],
            pools_liquidite=[{"niveau": 2030.0, "type": "BSL", "touches": 3, "description": "BSL"}],
            range_compresse=False,
        )

        res = calc.calculer_niveaux(
            prix_entree=2018.0, direction="bullish",
            zone_ob=ob, session_asie=session, structure=None, atr_m15=5.0,
        )
        # Le TP2 doit cibler le BSL ou être à 2R selon le calcul
        assert res.tp2 > res.tp1

    def test_validation_reussie_rr_superieur_minimum(self):
        """R:R TP2 >= RR_MINIMUM → validation_reussie = True."""
        from risk_manager import CalculateurNiveaux
        calc = CalculateurNiveaux(connecteur=None)
        ob = ObSimple(bas=2005.0, haut=2008.0)

        res = calc.calculer_niveaux(
            prix_entree=2018.0, direction="bullish",
            zone_ob=ob, session_asie=None, structure=None, atr_m15=5.0,
        )
        if res.rr_tp2 >= 2.0:
            assert res.validation_reussie is True
