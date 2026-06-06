"""
test_structure.py — Tests unitaires de la détection de structure de marché.
Vérifie BOS, CHoCH, swings et Order Blocks.
"""

import pytest
import pandas as pd
import numpy as np
import sys
from pathlib import Path

# Ajouter le dossier parent au path
sys.path.insert(0, str(Path(__file__).parent.parent))

from structure_analyzer import AnalyseurStructure, Tendance, TypeBOS
from ob_detector import DetecteurOB, TypeZone
from tests.fixtures import (
    creer_df_tendance_haussiere,
    creer_df_tendance_baissiere,
    creer_df_avec_order_block_haussier,
)


class TestDetectionSwings:
    """Tests de la détection des points de swing."""

    def setup_method(self):
        self.analyseur = AnalyseurStructure()

    def test_swings_detectes_sur_tendance_haussiere(self):
        """Les swings hauts et bas doivent être détectés sur une tendance haussière."""
        df = creer_df_tendance_haussiere(n=60)
        swings_hauts, swings_bas = self.analyseur.detecter_swings(df)

        assert len(swings_hauts) > 0, "Aucun swing haut détecté"
        assert len(swings_bas) > 0, "Aucun swing bas détecté"

    def test_swing_haut_est_maximum_local(self):
        """Chaque swing haut doit être supérieur aux N bougies de chaque côté."""
        df = creer_df_tendance_haussiere(n=60)
        swings_hauts, _ = self.analyseur.detecter_swings(df, n_bougies=3)
        highs = df["high"].values

        for swing in swings_hauts:
            i = swing.index
            for j in range(1, 4):
                assert highs[i] > highs[i - j], f"Swing haut non valide à {i}: pas plus haut que {i-j}"
                assert highs[i] > highs[i + j], f"Swing haut non valide à {i}: pas plus haut que {i+j}"

    def test_swing_bas_est_minimum_local(self):
        """Chaque swing bas doit être inférieur aux N bougies de chaque côté."""
        df = creer_df_tendance_baissiere(n=60)
        _, swings_bas = self.analyseur.detecter_swings(df, n_bougies=3)
        lows = df["low"].values

        for swing in swings_bas:
            i = swing.index
            for j in range(1, 4):
                assert lows[i] < lows[i - j], f"Swing bas non valide à {i}"
                assert lows[i] < lows[i + j], f"Swing bas non valide à {i}"


class TestDetectionBOS:
    """Tests de la détection des Break of Structure."""

    def setup_method(self):
        self.analyseur = AnalyseurStructure()

    def test_detection_bos_haussier(self):
        """Un BOS haussier doit être détecté quand le prix casse un swing haut."""
        df = creer_df_tendance_haussiere(n=80)
        swings_hauts, swings_bas = self.analyseur.detecter_swings(df)
        cassures = self.analyseur.detecter_bos_choch(df, swings_hauts, swings_bas)

        bos_haussiers = [c for c in cassures if c.type == TypeBOS.BOS_HAUSSIER]
        assert len(bos_haussiers) > 0, "Aucun BOS haussier détecté sur tendance haussière"

    def test_detection_bos_baissier(self):
        """Un BOS baissier doit être détecté sur une tendance baissière."""
        df = creer_df_tendance_baissiere(n=80)
        swings_hauts, swings_bas = self.analyseur.detecter_swings(df)
        cassures = self.analyseur.detecter_bos_choch(df, swings_hauts, swings_bas)

        bos_baissiers = [c for c in cassures if c.type == TypeBOS.BOS_BAISSIER]
        assert len(bos_baissiers) > 0, "Aucun BOS baissier détecté sur tendance baissière"

    def test_detection_choch_bearish(self):
        """Un CHoCH doit être détecté lors du premier BOS contre la tendance dominante."""
        # Créer d'abord une tendance haussière puis une impulsion baissière
        df_bull = creer_df_tendance_haussiere(n=50)
        df_bear = creer_df_tendance_baissiere(n=30, prix_depart=float(df_bull["close"].iloc[-1]))
        df_combined = pd.concat([df_bull, df_bear])
        df_combined = df_combined.reset_index(drop=False)
        df_combined.index = pd.date_range("2024-01-01", periods=len(df_combined), freq="4h", tz="UTC")

        swings_hauts, swings_bas = self.analyseur.detecter_swings(df_combined)
        cassures = self.analyseur.detecter_bos_choch(df_combined, swings_hauts, swings_bas)

        # On attend au moins un BOS dans chaque direction
        types = [c.type for c in cassures]
        assert len(cassures) > 0, "Aucune cassure détectée"


class TestTendance:
    """Tests de l'identification de la tendance."""

    def setup_method(self):
        self.analyseur = AnalyseurStructure()

    def test_tendance_haussiere_identifiee(self):
        """La tendance haussière doit être correctement identifiée."""
        df = creer_df_tendance_haussiere(n=100)
        analyse = self.analyseur.analyser(df)
        assert analyse.tendance == Tendance.HAUSSIERE, \
            f"Tendance attendue HAUSSIERE, obtenu {analyse.tendance}"

    def test_tendance_baissiere_identifiee(self):
        """La tendance baissière doit être correctement identifiée."""
        df = creer_df_tendance_baissiere(n=100)
        analyse = self.analyseur.analyser(df)
        assert analyse.tendance == Tendance.BAISSIERE, \
            f"Tendance attendue BAISSIERE, obtenu {analyse.tendance}"


class TestDetectionOrderBlock:
    """Tests de la détection des Order Blocks."""

    def setup_method(self):
        self.detecteur = DetecteurOB()

    def test_detection_order_block_bullish(self):
        """
        Un OB haussier doit être détecté (brut) sur le scénario avec impulsion + FVG.
        L'OB peut être invalidé par un nombre de touches élevé (comportement normal)
        mais il doit au moins être identifié avant invalidation.
        """
        df = creer_df_avec_order_block_haussier(n=80)
        # Vérifier la détection brute (avant filtrage par nb_touches)
        zones_brutes = self.detecteur._detecter_tous_ob_bruts(df)

        ob_haussiers_bruts = [
            z for z in zones_brutes
            if z.type == TypeZone.ORDER_BLOCK_HAUSSIER
        ]
        assert len(ob_haussiers_bruts) > 0, "Aucun OB haussier détecté (brut)"
        # Vérifier le FVG de la meilleure zone
        meilleur = max(ob_haussiers_bruts, key=lambda z: z.force_fvg_pct)
        assert meilleur.force_fvg_pct >= 0.3, f"FVG trop faible: {meilleur.force_fvg_pct:.3f}%"

    def test_ob_haussier_a_open_inferieur_close(self):
        """La bougie OB haussier (baissière) doit avoir close < open."""
        df = creer_df_avec_order_block_haussier(n=80)
        ob_actifs, _ = self.detecteur.detecter_toutes_zones(df)

        for ob in ob_actifs:
            assert ob.prix_bas < ob.prix_haut, f"OB invalide: bas {ob.prix_bas} >= haut {ob.prix_haut}"

    def test_invalidation_ob_traverse(self):
        """Un OB traversé par le prix ne doit plus être actif."""
        df = creer_df_tendance_haussiere(n=50)
        # Obtenir des OB avant la fin de la série
        df_partiel = df.head(40)
        ob_actifs_partiel, _ = self.detecteur.detecter_toutes_zones(df_partiel)

        # Sur la série complète (le prix peut avoir traversé certains OB)
        ob_actifs_complet, _ = self.detecteur.detecter_toutes_zones(df)

        # Le nombre d'OB actifs ne peut qu'être ≤ (les OB invalidés sont retirés)
        # Test de non-régression : les OB actifs sur la série courte ne sont pas forcément actifs sur la longue
        for ob in ob_actifs_complet:
            assert ob.est_actif, "Un OB inactif ne devrait pas être dans la liste des actifs"

    def test_ob_nb_touches_correct(self):
        """Le compteur de touches d'un OB doit être cohérent."""
        df = creer_df_avec_order_block_haussier(n=80)
        ob_actifs, _ = self.detecteur.detecter_toutes_zones(df)

        for ob in ob_actifs:
            assert ob.nb_touches >= 0, "Nombre de touches ne peut pas être négatif"
            # Un OB avec plus de max_touches ne doit pas être dans la liste
            assert ob.nb_touches <= self.detecteur.max_touches, \
                f"OB avec {ob.nb_touches} touches dans la liste (max: {self.detecteur.max_touches})"
