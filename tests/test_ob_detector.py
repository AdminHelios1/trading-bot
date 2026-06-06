"""
test_ob_detector.py — Tests unitaires du détecteur OB multi-timeframes.
Tous les tests utilisent des DataFrames synthétiques — pas de connexion MT5.
"""

import sys
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock
import pytest
import pandas as pd
import numpy as np

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.modules.setdefault("MetaTrader5", MagicMock())

from ob_detector import (
    DetecteurOB, ZoneOB, OBMultiTimeframe,
    TypeOB, ForceOB, StatutOB
)
from ob_scorer import ScorerOB


# ── Helpers / Fixtures ─────────────────────────────────────────────────────

def creer_df_ohlcv(
    n: int = 70,
    prix_base: float = 2000.0,
    tendance: float = 0.1,
    seed: int = 42,
) -> pd.DataFrame:
    """Crée un DataFrame OHLCV synthétique avec ATR."""
    np.random.seed(seed)
    index = pd.date_range("2025-01-01", periods=n, freq="4h", tz="UTC")
    prix = prix_base
    opens, highs, lows, closes, volumes = [], [], [], [], []

    for _ in range(n):
        variation = np.random.normal(tendance, 2.0)
        ouv = prix
        fer = prix + variation
        haut = max(ouv, fer) + abs(np.random.normal(0, 1.0))
        bas = min(ouv, fer) - abs(np.random.normal(0, 1.0))
        opens.append(round(ouv, 3))
        highs.append(round(haut, 3))
        lows.append(round(bas, 3))
        closes.append(round(fer, 3))
        volumes.append(np.random.randint(500, 2000))
        prix = fer

    df = pd.DataFrame(
        {"open": opens, "high": highs, "low": lows, "close": closes, "volume": volumes},
        index=index
    )
    # Ajouter ATR synthétique
    df["atr_14"] = 5.0 + np.random.uniform(-1, 1, n)
    return df


def creer_df_avec_ob_haussier(prix_base: float = 2000.0) -> pd.DataFrame:
    """
    Crée un DataFrame avec un Bullish OB clairement identifiable.
    Structure : bougie baissière → grande bougie haussière (impulsion ≥ 1.2× ATR).
    """
    n = 60
    index = pd.date_range("2025-01-01", periods=n, freq="4h", tz="UTC")
    opens = [prix_base] * n
    highs = [prix_base + 5] * n
    lows = [prix_base - 5] * n
    closes = [prix_base + 1] * n

    pivot = 40
    # Bougie baissière (OB)
    opens[pivot] = prix_base + 3
    closes[pivot] = prix_base - 2    # Baissière
    highs[pivot] = prix_base + 4
    lows[pivot] = prix_base - 3

    # Grande bougie haussière suivante (impulsion)
    opens[pivot + 1] = prix_base - 2
    closes[pivot + 1] = prix_base + 15   # Grande impulsion haussière
    highs[pivot + 1] = prix_base + 16
    lows[pivot + 1] = prix_base - 2

    df = pd.DataFrame(
        {"open": opens, "high": highs, "low": lows, "close": closes,
         "volume": [1000] * n},
        index=index
    )
    df["atr_14"] = 8.0  # ATR fixe — impulsion = 17 pts > 1.2 × 8 = 9.6 ✓
    return df


def creer_df_avec_ob_baissier(prix_base: float = 2050.0) -> pd.DataFrame:
    """Crée un DataFrame avec un Bearish OB clairement identifiable."""
    n = 60
    index = pd.date_range("2025-01-01", periods=n, freq="4h", tz="UTC")
    opens = [prix_base] * n
    highs = [prix_base + 5] * n
    lows = [prix_base - 5] * n
    closes = [prix_base - 1] * n

    pivot = 40
    # Bougie haussière (OB baissier)
    opens[pivot] = prix_base - 3
    closes[pivot] = prix_base + 4     # Haussière
    highs[pivot] = prix_base + 5
    lows[pivot] = prix_base - 3

    # Grande bougie baissière suivante
    opens[pivot + 1] = prix_base + 4
    closes[pivot + 1] = prix_base - 12  # Grande impulsion baissière
    highs[pivot + 1] = prix_base + 4
    lows[pivot + 1] = prix_base - 13

    df = pd.DataFrame(
        {"open": opens, "high": highs, "low": lows, "close": closes,
         "volume": [1000] * n},
        index=index
    )
    df["atr_14"] = 8.0
    return df


def creer_df_sans_impulsion(prix_base: float = 2000.0) -> pd.DataFrame:
    """DataFrame avec bougie baissière mais sans impulsion significative."""
    n = 60
    index = pd.date_range("2025-01-01", periods=n, freq="4h", tz="UTC")
    opens = [prix_base] * n
    highs = [prix_base + 3] * n
    lows = [prix_base - 3] * n
    closes = [prix_base] * n

    pivot = 40
    opens[pivot] = prix_base + 1
    closes[pivot] = prix_base - 1    # Légèrement baissière
    highs[pivot] = prix_base + 2
    lows[pivot] = prix_base - 2

    # Impulsion faible (< 1.2 × ATR)
    opens[pivot + 1] = prix_base - 1
    closes[pivot + 1] = prix_base + 2   # Seulement +3 pts, ATR = 8 → 3 < 9.6
    highs[pivot + 1] = prix_base + 3
    lows[pivot + 1] = prix_base - 1

    df = pd.DataFrame(
        {"open": opens, "high": highs, "low": lows, "close": closes,
         "volume": [1000] * n},
        index=index
    )
    df["atr_14"] = 8.0
    return df


def creer_zone_ob(
    timeframe: str,
    zone_bas: float,
    zone_haut: float,
    type_ob: TypeOB = TypeOB.HAUSSIER,
    fvg: bool = True,
    impulsion: float = 10.0,
) -> ZoneOB:
    """Crée une ZoneOB pour les tests."""
    milieu = (zone_haut + zone_bas) / 2
    return ZoneOB(
        timeframe=timeframe,
        type_ob=type_ob,
        zone_haut=zone_haut,
        zone_bas=zone_bas,
        zone_milieu=milieu,
        forme_a=datetime(2025, 2, 7, 10, 0),
        bougie_open=milieu + 1,
        bougie_haut=zone_haut + 1,
        bougie_bas=zone_bas - 1,
        bougie_close=zone_bas,
        taille_impulsion=impulsion,
        fvg_present=fvg,
        fvg_taille_pct=0.3 if fvg else 0.0,
    )


def creer_mtf_ob(
    h4: bool = True,
    h1: bool = True,
    m15: bool = False,
    fvg_h4: bool = True,
    fvg_h1: bool = False,
    fvg_m15: bool = False,
    impulsion_forte: bool = True,
    nb_touches: int = 0,
    type_ob: TypeOB = TypeOB.HAUSSIER,
) -> OBMultiTimeframe:
    """Crée un OBMultiTimeframe pour les tests de scoring."""
    mtf = OBMultiTimeframe(
        type_ob=type_ob,
        symbole="XAUUSD",
        zone_entree_bas=2010.0,
        zone_entree_haut=2015.0,
        zone_entree_milieu=2012.5,
        nb_touches=nb_touches,
        niveau_invalidation=2008.0 if type_ob == TypeOB.HAUSSIER else 2017.0,
    )

    impulsion = 20.0 if impulsion_forte else 5.0

    if h4:
        mtf.zone_h4 = creer_zone_ob("H4", 2010.0, 2015.0, type_ob, fvg_h4, impulsion)
    if h1:
        mtf.zone_h1 = creer_zone_ob("H1", 2011.0, 2014.5, type_ob, fvg_h1, impulsion)
    if m15:
        mtf.zone_m15 = creer_zone_ob("M15", 2011.5, 2014.0, type_ob, fvg_m15, impulsion)

    return mtf


def creer_df_avec_close(close: float, n: int = 10) -> pd.DataFrame:
    """DataFrame minimal avec close fixé."""
    index = pd.date_range("2025-01-01", periods=n, freq="15min", tz="UTC")
    return pd.DataFrame({
        "open": [close - 1] * n,
        "high": [close + 2] * n,
        "low": [close - 2] * n,
        "close": [close] * n,
        "volume": [1000] * n,
    }, index=index)


# ── Tests détection OB haussier ────────────────────────────────────────────

class TestDetectionOBHaussier:
    """Tests de détection d'un Bullish OB sur un seul timeframe."""

    def setup_method(self):
        self.detecteur = DetecteurOB()

    def test_ob_haussier_detecte_conditions_parfaites(self):
        """OB haussier détecté avec toutes les conditions réunies."""
        df = creer_df_avec_ob_haussier()
        obs = self.detecteur._detecter_sur_timeframe(df, "H4")
        haussiers = [ob for ob in obs if ob.type_ob == TypeOB.HAUSSIER]
        assert len(haussiers) > 0, "Aucun OB haussier détecté"

    def test_ob_haussier_zone_correcte(self):
        """La zone de l'OB haussier doit aller du low au max(open, close)."""
        df = creer_df_avec_ob_haussier(prix_base=2000.0)
        obs = self.detecteur._detecter_sur_timeframe(df, "H4")
        haussiers = [ob for ob in obs if ob.type_ob == TypeOB.HAUSSIER]
        assert len(haussiers) > 0
        for ob in haussiers:
            assert ob.zone_bas < ob.zone_haut
            assert ob.zone_milieu == (ob.zone_haut + ob.zone_bas) / 2

    def test_ob_rejeté_sans_impulsion(self):
        """Bougie baissière sans impulsion significative → pas d'OB."""
        df = creer_df_sans_impulsion()
        obs = self.detecteur._detecter_sur_timeframe(df, "H4")
        # L'impulsion (3 pts) < 1.2 × ATR (9.6) → pas d'OB haussier
        # Note : des OB baissiers peuvent exister mais pas haussiers sur ce scénario
        haussiers_fort = [
            ob for ob in obs
            if ob.type_ob == TypeOB.HAUSSIER and ob.taille_impulsion >= 9.6
        ]
        assert len(haussiers_fort) == 0

    def test_ob_timeframe_correct(self):
        """L'OB doit porter le bon label de timeframe."""
        df = creer_df_avec_ob_haussier()
        obs = self.detecteur._detecter_sur_timeframe(df, "H1")
        for ob in obs:
            assert ob.timeframe == "H1"


# ── Tests détection OB baissier ────────────────────────────────────────────

class TestDetectionOBBaissier:
    """Tests de détection d'un Bearish OB."""

    def setup_method(self):
        self.detecteur = DetecteurOB()

    def test_ob_baissier_detecte_conditions_parfaites(self):
        """OB baissier détecté avec toutes les conditions."""
        df = creer_df_avec_ob_baissier()
        obs = self.detecteur._detecter_sur_timeframe(df, "H4")
        baissiers = [ob for ob in obs if ob.type_ob == TypeOB.BAISSIER]
        assert len(baissiers) > 0, "Aucun OB baissier détecté"

    def test_ob_baissier_zone_correcte(self):
        """Zone du Bearish OB = du min(open, close) au high."""
        df = creer_df_avec_ob_baissier()
        obs = self.detecteur._detecter_sur_timeframe(df, "H4")
        baissiers = [ob for ob in obs if ob.type_ob == TypeOB.BAISSIER]
        for ob in baissiers:
            assert ob.zone_bas < ob.zone_haut


# ── Tests alignement multi-timeframes ──────────────────────────────────────

class TestAlignementMultiTF:
    """Tests de l'alignement des OB entre timeframes."""

    def setup_method(self):
        self.detecteur = DetecteurOB()

    def test_alignement_h4_h1_overlap_50pct(self):
        """Overlap 50% entre H4 et H1 → alignés (seuil 30%)."""
        h4_ob = creer_zone_ob("H4", 2010.0, 2015.0, TypeOB.HAUSSIER)
        h1_ob = creer_zone_ob("H1", 2012.0, 2018.0, TypeOB.HAUSSIER)
        # Overlap = 2012–2015 = 3 pts, zone H1 = 6 pts → 50% > 30% ✓
        resultat = self.detecteur._trouver_meilleur_aligne(h4_ob, [h1_ob], 30.0)
        assert resultat is not None

    def test_alignement_rejete_overlap_16pct(self):
        """Overlap 16% → rejeté (< seuil 30%)."""
        h4_ob = creer_zone_ob("H4", 2010.0, 2015.0, TypeOB.HAUSSIER)
        h1_ob = creer_zone_ob("H1", 2014.0, 2020.0, TypeOB.HAUSSIER)
        # Overlap = 2014–2015 = 1 pt, zone H1 = 6 pts → 16.7% < 30% ✗
        resultat = self.detecteur._trouver_meilleur_aligne(h4_ob, [h1_ob], 30.0)
        assert resultat is None

    def test_alignement_rejete_direction_opposee(self):
        """OB de directions opposées ne s'alignent pas."""
        h4_ob = creer_zone_ob("H4", 2010.0, 2015.0, TypeOB.HAUSSIER)
        h1_ob = creer_zone_ob("H1", 2010.0, 2015.0, TypeOB.BAISSIER)  # Direction opposée
        resultat = self.detecteur._trouver_meilleur_aligne(h4_ob, [h1_ob], 30.0)
        assert resultat is None

    def test_alignement_sans_chevauchement(self):
        """Zones non chevauchantes → pas d'alignement."""
        h4_ob = creer_zone_ob("H4", 2010.0, 2015.0, TypeOB.HAUSSIER)
        h1_ob = creer_zone_ob("H1", 2020.0, 2025.0, TypeOB.HAUSSIER)  # Loin
        resultat = self.detecteur._trouver_meilleur_aligne(h4_ob, [h1_ob], 30.0)
        assert resultat is None

    def test_calcul_zone_entree_intersection(self):
        """La zone d'entrée est l'intersection (pas l'union) des zones."""
        mtf = creer_mtf_ob(h4=True, h1=True, m15=False)
        # H4 : 2010–2015, H1 : 2011–2014.5
        # Intersection attendue : 2011–2014.5
        haut, bas = self.detecteur._calculer_zone_entree(mtf)
        assert bas >= 2010.0  # Au moins le bas de H4
        assert haut <= 2015.0  # Au plus le haut de H4
        assert bas <= haut

    def test_calcul_zone_entree_fallback_h4(self):
        """Intersection invalide → fallback sur zone H4."""
        mtf = OBMultiTimeframe(type_ob=TypeOB.HAUSSIER, symbole="XAUUSD")
        mtf.zone_h4 = creer_zone_ob("H4", 2010.0, 2015.0, TypeOB.HAUSSIER)
        # Zone H1 qui ne chevauche pas H4 → intersection invalide
        mtf.zone_h1 = creer_zone_ob("H1", 2020.0, 2025.0, TypeOB.HAUSSIER)
        haut, bas = self.detecteur._calculer_zone_entree(mtf)
        # Fallback sur H4
        assert haut == 2015.0
        assert bas == 2010.0


# ── Tests scoring ──────────────────────────────────────────────────────────

class TestScoring:
    """Tests du système de scoring OBScorer."""

    def setup_method(self):
        self.scorer = ScorerOB()
        self.df_h4 = creer_df_ohlcv(n=60)
        self.df_h1 = creer_df_ohlcv(n=80)
        self.df_m15 = creer_df_ohlcv(n=100)

    def test_score_institutionnel_3tf_fvg_forte_impulsion(self):
        """H4+H1+M15 + FVG sur tous les TF + impulsion forte → score ≥ 80."""
        mtf = creer_mtf_ob(h4=True, h1=True, m15=True,
                           fvg_h4=True, fvg_h1=True, fvg_m15=True,
                           impulsion_forte=True)
        score = self.scorer.calculer_score(mtf, self.df_h4, self.df_h1, self.df_m15)
        assert score >= 80, f"Score attendu ≥ 80 (INSTITUTIONNEL), obtenu {score}"

    def test_score_fort_2tf_sans_m15(self):
        """H4+H1 + FVG H4+H1 + impulsion forte → score entre 60 et 79."""
        mtf = creer_mtf_ob(h4=True, h1=True, m15=False,
                           fvg_h4=True, fvg_h1=True, impulsion_forte=True)
        score = self.scorer.calculer_score(mtf, self.df_h4, self.df_h1, self.df_m15)
        assert 60 <= score < 80, f"Score attendu 60–79 (FORT), obtenu {score}"

    def test_score_faible_h4_seul_sans_fvg(self):
        """H4 seul + sans FVG + impulsion faible → score < 60 (rejeté)."""
        mtf = creer_mtf_ob(h4=True, h1=False, m15=False,
                           fvg_h4=False, impulsion_forte=False)
        score = self.scorer.calculer_score(mtf, self.df_h4, self.df_h1, self.df_m15)
        assert score < 60, f"Score attendu < 60 (rejeté), obtenu {score}"

    def test_penalite_ob_deja_teste(self):
        """Un OB déjà touché 1 fois doit avoir un score réduit."""
        mtf_vierge = creer_mtf_ob(h4=True, h1=True, fvg_h4=True, nb_touches=0)
        mtf_teste = creer_mtf_ob(h4=True, h1=True, fvg_h4=True, nb_touches=1)
        score_vierge = self.scorer.calculer_score(mtf_vierge, self.df_h4, self.df_h1, None)
        score_teste = self.scorer.calculer_score(mtf_teste, self.df_h4, self.df_h1, None)
        assert score_teste < score_vierge, "L'OB testé doit avoir un score inférieur"

    def test_score_clampe_entre_0_et_100(self):
        """Le score doit toujours être entre 0 et 100."""
        for _ in range(10):
            mtf = creer_mtf_ob(
                h4=bool(np.random.randint(0, 2)),
                h1=bool(np.random.randint(0, 2)),
                m15=bool(np.random.randint(0, 2)),
                fvg_h4=bool(np.random.randint(0, 2)),
                nb_touches=np.random.randint(0, 3),
            )
            score = self.scorer.calculer_score(mtf, self.df_h4, self.df_h1, None)
            assert 0 <= score <= 100, f"Score hors limites : {score}"

    def test_force_institutionnel_score_80(self):
        """Score 80+ → INSTITUTIONNEL."""
        assert DetecteurOB._score_vers_force(80) == ForceOB.INSTITUTIONNEL
        assert DetecteurOB._score_vers_force(100) == ForceOB.INSTITUTIONNEL

    def test_force_fort_score_60_79(self):
        """Score 60–79 → FORT."""
        assert DetecteurOB._score_vers_force(60) == ForceOB.FORT
        assert DetecteurOB._score_vers_force(79) == ForceOB.FORT

    def test_force_modere_score_40_59(self):
        assert DetecteurOB._score_vers_force(40) == ForceOB.MODERE
        assert DetecteurOB._score_vers_force(59) == ForceOB.MODERE

    def test_force_faible_score_sous_40(self):
        assert DetecteurOB._score_vers_force(0) == ForceOB.FAIBLE
        assert DetecteurOB._score_vers_force(39) == ForceOB.FAIBLE


# ── Tests mise à jour statuts ──────────────────────────────────────────────

class TestMiseAJourStatuts:
    """Tests de la mise à jour des statuts OB."""

    def setup_method(self):
        self.detecteur = DetecteurOB()

    def test_touch_count_incremente_prix_dans_zone(self):
        """Prix dans la zone → touch_count +1 et statut = TESTE."""
        mtf = creer_mtf_ob()
        mtf.zone_entree_bas = 2010.0
        mtf.zone_entree_haut = 2015.0
        df_m15 = creer_df_avec_close(2012.5)  # Dans la zone
        self.detecteur._mettre_a_jour_statuts([mtf], df_m15)
        assert mtf.nb_touches == 1
        assert mtf.statut == StatutOB.TESTE

    def test_ob_epuise_apres_2_touches(self):
        """2 touches → statut EPUISE."""
        mtf = creer_mtf_ob(nb_touches=2)
        mtf.zone_entree_bas = 2010.0
        mtf.zone_entree_haut = 2015.0
        mtf.statut = StatutOB.TESTE
        df_m15 = creer_df_avec_close(2012.5)
        self.detecteur._mettre_a_jour_statuts([mtf], df_m15)
        assert mtf.statut == StatutOB.EPUISE

    def test_invalidation_ob_haussier_prix_sous_niveau(self):
        """Prix sous le niveau d'invalidation → statut INVALIDE."""
        mtf = creer_mtf_ob()
        mtf.zone_entree_bas = 2010.0
        mtf.zone_entree_haut = 2015.0
        mtf.niveau_invalidation = 2009.0
        df_m15 = creer_df_avec_close(2008.5)  # Sous l'invalidation
        self.detecteur._mettre_a_jour_statuts([mtf], df_m15)
        assert mtf.statut == StatutOB.INVALIDE

    def test_invalidation_ob_baissier_prix_au_dessus(self):
        """OB baissier : prix au-dessus de l'invalidation → INVALIDE."""
        mtf = creer_mtf_ob(type_ob=TypeOB.BAISSIER)
        mtf.zone_entree_bas = 2030.0
        mtf.zone_entree_haut = 2035.0
        mtf.niveau_invalidation = 2037.0
        df_m15 = creer_df_avec_close(2038.0)
        self.detecteur._mettre_a_jour_statuts([mtf], df_m15)
        assert mtf.statut == StatutOB.INVALIDE

    def test_pas_de_touch_prix_hors_zone(self):
        """Prix loin de la zone → statut inchangé."""
        mtf = creer_mtf_ob()
        mtf.zone_entree_bas = 2010.0
        mtf.zone_entree_haut = 2015.0
        mtf.niveau_invalidation = 2008.0
        df_m15 = creer_df_avec_close(2050.0)  # Loin de la zone
        self.detecteur._mettre_a_jour_statuts([mtf], df_m15)
        assert mtf.nb_touches == 0
        assert mtf.statut == StatutOB.ACTIF


# ── Tests confluences ──────────────────────────────────────────────────────

class TestConfluences:
    """Tests de la détection des confluences."""

    def setup_method(self):
        self.scorer = ScorerOB()

    def test_confluence_fibonacci_ote_detecte(self):
        """Zone OB dans le range OTE (61.8%–78.6%) → confluence détectée."""
        # Leg H4 : swing_high=2030, swing_low=2000 → range=30
        # 61.8% = 2030 - 30×0.618 = 2011.46
        # 78.6% = 2030 - 30×0.786 = 2006.42
        df_h4 = creer_df_ohlcv(n=60)
        # Forcer les prix pour avoir un swing connu
        df_h4["high"] = 2030.0
        df_h4["low"] = 2000.0
        df_h4["close"] = 2015.0

        mtf = creer_mtf_ob()
        mtf.zone_entree_milieu = 2009.0  # Dans la range OTE (2006–2011)
        mtf.zone_entree_bas = 2007.0
        mtf.zone_entree_haut = 2011.0

        confluences = self.scorer.detecter_confluences(mtf, df_h4)
        assert "OTE Fibonacci 61.8%–78.6%" in confluences

    def test_confluence_tf_alignes_toujours_presente(self):
        """La confluence '2/3 TF alignés' est toujours présente."""
        df_h4 = creer_df_ohlcv(n=60)
        mtf = creer_mtf_ob(h4=True, h1=True, m15=False)
        confluences = self.scorer.detecter_confluences(mtf, df_h4)
        assert any("TF alignés" in c for c in confluences)

    def test_confluence_fvg_h4_presente(self):
        """FVG H4 → confluence 'FVG H4' présente."""
        df_h4 = creer_df_ohlcv(n=60)
        mtf = creer_mtf_ob(h4=True, h1=True, fvg_h4=True)
        confluences = self.scorer.detecter_confluences(mtf, df_h4)
        assert "FVG H4" in confluences

    def test_confluences_retourne_liste(self):
        """detect_confluences() retourne toujours une liste."""
        df_h4 = creer_df_ohlcv(n=60)
        mtf = creer_mtf_ob()
        confluences = self.scorer.detecter_confluences(mtf, df_h4)
        assert isinstance(confluences, list)


# ── Tests intégration detecter_multi_tf ───────────────────────────────────

class TestIntegrationMultiTF:
    """Tests de la méthode principale detecter_multi_tf()."""

    def setup_method(self):
        self.detecteur = DetecteurOB()

    def test_detecter_multi_tf_ne_plante_pas(self):
        """detecter_multi_tf() ne doit pas lever d'exception."""
        df_h4 = creer_df_ohlcv(n=70)
        df_m15 = creer_df_ohlcv(n=100)
        try:
            obs = self.detecteur.detecter_multi_tf(df_h4, df_m15=df_m15)
            assert isinstance(obs, list)
        except Exception as e:
            pytest.fail(f"Exception levée : {e}")

    def test_detecter_multi_tf_retourne_seulement_score_60_plus(self):
        """Tous les OB retournés ont un score >= 60."""
        df_h4 = creer_df_ohlcv(n=70)
        obs = self.detecteur.detecter_multi_tf(df_h4)
        for ob in obs:
            assert ob.score >= 60, f"OB avec score {ob.score} < 60 retourné"

    def test_detecter_multi_tf_tri_par_score_decroissant(self):
        """Les OB sont triés par score décroissant."""
        df_h4 = creer_df_ohlcv(n=70)
        obs = self.detecteur.detecter_multi_tf(df_h4)
        scores = [ob.score for ob in obs]
        assert scores == sorted(scores, reverse=True)

    def test_detecter_toutes_zones_compatibilite(self):
        """detecter_toutes_zones() retourne un tuple de deux listes."""
        df_h4 = creer_df_ohlcv(n=70)
        resultat = self.detecteur.detecter_toutes_zones(df_h4)
        assert isinstance(resultat, tuple)
        assert len(resultat) == 2
        assert isinstance(resultat[0], list)
        assert isinstance(resultat[1], list)
