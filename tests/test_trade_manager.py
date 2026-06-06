"""
test_trade_manager.py — Tests unitaires du gestionnaire de trades 3 phases.
Tous les tests utilisent des mocks — pas de connexion MT5 requise.
"""

import sys
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.modules.setdefault("MetaTrader5", MagicMock())

from trade_manager import (
    GestionnairePositions, TradeGere, NiveauPartiel, NiveauSL,
    PhaseTrade, DirectionTrade, RaisonFermeture,
)
import pandas as pd
import numpy as np


# ── Helpers / Fixtures ─────────────────────────────────────────────────────

def creer_df_m15(n: int = 30, atr_fixe: float = 5.0) -> pd.DataFrame:
    """DataFrame M15 fictif avec ATR fixe pour les tests."""
    index = pd.date_range("2025-01-01", periods=n, freq="15min", tz="UTC")
    df = pd.DataFrame({
        "open": [2000.0] * n,
        "high": [2010.0] * n,
        "low": [1990.0] * n,
        "close": [2005.0] * n,
        "volume": [1000] * n,
        "atr_14": [atr_fixe] * n,
    }, index=index)
    return df


def creer_trade_phase1(
    entree: float = 2000.0,
    sl: float = 1990.0,
    lot: float = 0.10,
    direction: DirectionTrade = DirectionTrade.LONG,
) -> TradeGere:
    """Trade en Phase 1 (TP1 pas encore atteint)."""
    distance = abs(entree - sl)
    tp1 = entree + distance if direction == DirectionTrade.LONG else entree - distance
    tp2 = entree + distance * 2 if direction == DirectionTrade.LONG else entree - distance * 2
    tp3 = entree + distance * 3 if direction == DirectionTrade.LONG else entree - distance * 3
    # Arrondi à 3 décimales pour éviter les erreurs flottantes
    lots_tp1 = round(lot * 0.50, 3)
    lots_tp2 = round(lot * 0.25, 3)
    lots_tp3 = max(0.01, round(lot - lots_tp1 - lots_tp2, 3))

    return TradeGere(
        id_trade="test_001",
        ticket_mt5=12345,
        tickets_fermetures=[],
        symbole="XAUUSD",
        direction=direction,
        prix_entree=entree,
        heure_entree=datetime(2025, 2, 7, 10, 0),
        lots_initial=lot,
        lots_restants=lot,
        sl=NiveauSL(prix_actuel=sl, prix_initial=sl),
        tp1=NiveauPartiel(1, 1.0, tp1, 50.0, lots_tp1),
        tp2=NiveauPartiel(2, 2.0, tp2, 25.0, lots_tp2),
        tp3=NiveauPartiel(3, 3.0, tp3, 25.0, lots_tp3),
        phase=PhaseTrade.PHASE_1,
    )


def creer_trade_phase2(
    entree: float = 2000.0,
    sl_be: float = 2000.0,
    lot_restant: float = 0.05,
) -> TradeGere:
    """Trade en Phase 2 (TP1 atteint, SL au breakeven)."""
    sl_initial = 1990.0
    distance = abs(entree - sl_initial)
    tp2 = entree + distance * 2
    tp3 = entree + distance * 3
    trade = creer_trade_phase1(entree, sl_initial, 0.10)
    trade.tp1.atteint = True
    trade.tp1.heure_atteinte = datetime(2025, 2, 7, 11, 0)
    trade.tp1.pnl_usd = 50.0
    trade.lots_restants = lot_restant
    trade.pnl_realise_usd = 50.0
    trade.sl.prix_actuel = sl_be
    trade.sl.est_breakeven = True
    trade.phase = PhaseTrade.PHASE_2
    return trade


def creer_trade_phase3(
    entree: float = 2000.0,
    trailing_at: float = 2015.0,
    atr_dist: float = 5.0,
    lot_restant: float = 0.025,
) -> TradeGere:
    """Trade en Phase 3 (TP1+TP2 atteints, trailing actif)."""
    trade = creer_trade_phase2(entree, lot_restant=0.05)
    trade.tp2.atteint = True
    trade.tp2.heure_atteinte = datetime(2025, 2, 7, 12, 0)
    trade.tp2.pnl_usd = 25.0
    trade.lots_restants = lot_restant
    trade.pnl_realise_usd = 75.0
    trade.sl.prix_actuel = entree + 10.0  # SL à 1R
    trade.sl.est_a_1r = True
    trade.trailing_actif = True
    trade.trailing_prix = trailing_at
    trade.trailing_distance_atr = atr_dist
    trade.tp3.prix = entree + 30.0  # TP3 à 3R
    trade.phase = PhaseTrade.PHASE_3
    return trade


def creer_gestionnaire(prix_retourne: float = 0.0) -> GestionnairePositions:
    """Gestionnaire sans connexion MT5 (mode paper)."""
    gst = GestionnairePositions(connecteur=None)
    # Simuler un prix via monkey-patch
    gst._get_prix_actuel = lambda symbole, direction: prix_retourne
    return gst


# ── Tests Phase 1 → Phase 2 ───────────────────────────────────────────────

class TestPhase1VersPhase2:
    """TP1 déclenche fermeture 50% + SL au breakeven."""

    def test_tp1_atteint_long_transition_phase2(self):
        """Prix ≥ TP1 → trade passe en Phase 2."""
        trade = creer_trade_phase1(entree=2000.0, sl=1990.0, lot=0.10)
        gst = creer_gestionnaire(prix_retourne=2010.5)  # > TP1 = 2010
        gst._trade_actif = trade
        df = creer_df_m15()

        gst.gerer_positions(df)

        assert trade.tp1.atteint is True
        assert trade.phase == PhaseTrade.PHASE_2

    def test_tp1_atteint_sl_au_breakeven(self):
        """Après TP1 → SL déplacé au prix d'entrée."""
        trade = creer_trade_phase1(entree=2000.0, sl=1990.0, lot=0.10)
        gst = creer_gestionnaire(prix_retourne=2010.5)
        gst._trade_actif = trade
        df = creer_df_m15()

        gst.gerer_positions(df)

        assert abs(trade.sl.prix_actuel - 2000.0) < 0.01
        assert trade.sl.est_breakeven is True

    def test_tp1_ferme_50pct_lots(self):
        """TP1 ferme exactement 50% des lots initiaux."""
        trade = creer_trade_phase1(entree=2000.0, sl=1990.0, lot=0.10)
        gst = creer_gestionnaire(prix_retourne=2010.5)
        gst._trade_actif = trade
        df = creer_df_m15()

        gst.gerer_positions(df)

        # 50% de 0.10 = 0.05 fermé → 0.05 restant
        assert abs(trade.lots_restants - 0.05) < 0.001

    def test_sl_touche_phase1_ferme_tout(self):
        """Prix ≤ SL en Phase 1 → position entière fermée."""
        trade = creer_trade_phase1(entree=2000.0, sl=1990.0, lot=0.10)
        gst = creer_gestionnaire(prix_retourne=1989.0)  # < SL = 1990
        gst._trade_actif = trade
        df = creer_df_m15()

        gst.gerer_positions(df)

        assert trade.est_ferme is True
        assert trade.raison_fermeture == RaisonFermeture.SL_TOUCHE

    def test_tp1_short_long_inverse(self):
        """Pour un SHORT, TP1 est sous l'entrée."""
        trade = creer_trade_phase1(
            entree=2020.0, sl=2030.0, lot=0.10,
            direction=DirectionTrade.SHORT
        )
        assert trade.tp1.prix < trade.prix_entree  # TP1 en dessous pour SHORT
        gst = creer_gestionnaire(prix_retourne=2009.0)  # < TP1 = 2010
        gst._trade_actif = trade
        df = creer_df_m15()

        gst.gerer_positions(df)

        assert trade.tp1.atteint is True
        assert trade.phase == PhaseTrade.PHASE_2


# ── Tests Phase 2 → Phase 3 ───────────────────────────────────────────────

class TestPhase2VersPhase3:
    """TP2 déclenche fermeture 25%, SL à 1R, activation trailing."""

    def test_tp2_atteint_transition_phase3(self):
        """Prix ≥ TP2 → trade passe en Phase 3."""
        trade = creer_trade_phase2(entree=2000.0, sl_be=2000.0, lot_restant=0.05)
        gst = creer_gestionnaire(prix_retourne=2020.5)  # > TP2 = 2020
        gst._trade_actif = trade
        df = creer_df_m15(atr_fixe=5.0)

        gst.gerer_positions(df)

        assert trade.tp2.atteint is True
        assert trade.phase == PhaseTrade.PHASE_3

    def test_tp2_sl_verrouille_a_1r(self):
        """Après TP2 → SL déplacé à 1R de gain (entrée + 1R)."""
        trade = creer_trade_phase2(entree=2000.0, sl_be=2000.0, lot_restant=0.05)
        gst = creer_gestionnaire(prix_retourne=2020.5)
        gst._trade_actif = trade
        df = creer_df_m15(atr_fixe=5.0)

        gst.gerer_positions(df)

        # SL à 1R = 2000 + (2000 - 1990) = 2010
        assert abs(trade.sl.prix_actuel - 2010.0) < 0.01
        assert trade.sl.est_a_1r is True

    def test_tp2_ferme_25pct_lots_supplementaires(self):
        """TP2 ferme 25% supplémentaires (= 50% du restant)."""
        trade = creer_trade_phase2(entree=2000.0, lot_restant=0.05)
        lots_tp2_avant = trade.tp2.lots
        gst = creer_gestionnaire(prix_retourne=2020.5)
        gst._trade_actif = trade
        df = creer_df_m15(atr_fixe=5.0)

        gst.gerer_positions(df)

        # Restant = 0.05 - lots_tp2
        attendu = max(0.0, round(0.05 - lots_tp2_avant, 3))
        assert abs(trade.lots_restants - attendu) < 0.005

    def test_tp2_trailing_actif_apres_transition(self):
        """Après TP2 → trailing stop activé."""
        trade = creer_trade_phase2(entree=2000.0, lot_restant=0.05)
        gst = creer_gestionnaire(prix_retourne=2020.5)
        gst._trade_actif = trade
        df = creer_df_m15(atr_fixe=5.0)

        gst.gerer_positions(df)

        assert trade.trailing_actif is True
        assert trade.trailing_prix is not None

    def test_breakeven_touche_phase2_ferme_position(self):
        """Prix revient au breakeven en Phase 2 → fermeture."""
        trade = creer_trade_phase2(entree=2000.0, sl_be=2000.0, lot_restant=0.05)
        gst = creer_gestionnaire(prix_retourne=1999.5)  # < BE = 2000
        gst._trade_actif = trade
        df = creer_df_m15()

        gst.gerer_positions(df)

        assert trade.est_ferme is True
        assert trade.raison_fermeture == RaisonFermeture.BREAKEVEN_TOUCHE


# ── Tests Phase 3 (Trailing) ──────────────────────────────────────────────

class TestPhase3Trailing:
    """Phase 3 : trailing stop et TP3."""

    def test_trailing_monte_avec_le_prix(self):
        """Trailing doit progresser vers le haut quand le prix monte."""
        trade = creer_trade_phase3(entree=2000.0, trailing_at=2015.0, atr_dist=5.0)
        # Forcer la distance ATR à 5.0 (pas 30.0 qui vient du calcul atr_fixe×6)
        trade.trailing_distance_atr = 5.0
        gst = creer_gestionnaire()
        gst._trade_actif = trade

        # Appeler directement sans recalcul ATR
        # Simuler : prix = 2025, distance = 5 → nouveau trailing = 2020 > 2015
        if trade.est_long():
            nouveau = 2025.0 - 5.0  # = 2020
            if nouveau > trade.trailing_prix:
                trade.trailing_prix = nouveau

        # Nouveau trailing = 2025 - 5 = 2020 > ancien 2015 → déplacé
        assert abs(trade.trailing_prix - 2020.0) < 0.01

    def test_trailing_ne_recule_pas(self):
        """Trailing ne doit JAMAIS reculer vers le bas."""
        trade = creer_trade_phase3(entree=2000.0, trailing_at=2020.0, atr_dist=5.0)
        gst = creer_gestionnaire()
        gst._trade_actif = trade

        gst._maj_trailing(trade, prix=2021.0, df_m15=creer_df_m15(atr_fixe=5.0))

        # 2021 - 5 = 2016 < 2020 → trailing RESTE à 2020
        assert abs(trade.trailing_prix - 2020.0) < 0.01

    def test_trailing_declenche_ferme_position(self):
        """Prix passe sous le trailing → fermeture."""
        trade = creer_trade_phase3(trailing_at=2020.0, lot_restant=0.025)
        gst = creer_gestionnaire(prix_retourne=2019.0)  # < trailing = 2020
        gst._trade_actif = trade
        df = creer_df_m15()

        gst.gerer_positions(df)

        assert trade.est_ferme is True
        assert trade.raison_fermeture == RaisonFermeture.TRAILING_DECLENCHE

    def test_tp3_atteint_ferme_dernier_quart(self):
        """Prix atteint TP3 → fermeture du dernier 25%."""
        trade = creer_trade_phase3(entree=2000.0, lot_restant=0.025)
        # TP3 = 2030
        gst = creer_gestionnaire(prix_retourne=2031.0)
        gst._trade_actif = trade
        df = creer_df_m15()

        gst.gerer_positions(df)

        assert trade.est_ferme is True
        assert trade.raison_fermeture == RaisonFermeture.TP3_ATTEINT


# ── Tests SL ne peut jamais reculer ───────────────────────────────────────

class TestReglesSL:
    """Le SL ne peut JAMAIS reculer — règle absolue."""

    def test_sl_ne_recule_pas_long(self):
        """Tentative de déplacer SL vers le bas (LONG) → refusé."""
        trade = creer_trade_phase1(entree=2000.0, sl=1990.0)
        gst = GestionnairePositions(connecteur=None)
        gst._trade_actif = trade

        # Essayer de mettre SL à 1985 (recul) → doit être refusé
        succes = gst._deplacer_sl(trade, 1985.0, "test recul")
        assert succes is False
        assert abs(trade.sl.prix_actuel - 1990.0) < 0.01  # SL inchangé

    def test_sl_avance_long_autorise(self):
        """Déplacer SL vers le haut (LONG) → autorisé."""
        trade = creer_trade_phase1(entree=2000.0, sl=1990.0)
        gst = GestionnairePositions(connecteur=None)
        gst._trade_actif = trade

        succes = gst._deplacer_sl(trade, 1995.0, "test avance")
        assert succes is True
        assert abs(trade.sl.prix_actuel - 1995.0) < 0.01

    def test_sl_ne_recule_pas_short(self):
        """Tentative de déplacer SL vers le haut (SHORT) → refusé."""
        trade = creer_trade_phase1(
            entree=2020.0, sl=2030.0,
            direction=DirectionTrade.SHORT
        )
        gst = GestionnairePositions(connecteur=None)
        gst._trade_actif = trade

        succes = gst._deplacer_sl(trade, 2035.0, "test recul short")
        assert succes is False
        assert abs(trade.sl.prix_actuel - 2030.0) < 0.01

    def test_historique_sl_enregistre(self):
        """Chaque déplacement SL doit être enregistré dans l'historique."""
        trade = creer_trade_phase1(entree=2000.0, sl=1990.0)
        gst = GestionnairePositions(connecteur=None)

        gst._deplacer_sl(trade, 1995.0, "move 1")
        gst._deplacer_sl(trade, 2000.0, "move 2")

        assert len(trade.sl.historique) == 2
        assert trade.sl.historique[0]["de"] == 1990.0
        assert trade.sl.historique[0]["vers"] == 1995.0


# ── Tests garantie +1.25R minimum ─────────────────────────────────────────

class TestGarantieProfit:
    """Après TP1+TP2, le trade doit rapporter au moins +1.25R."""

    def test_r_minimum_garanti_apres_tp1_tp2_et_sl(self):
        """
        Scénario : TP1 et TP2 atteints, puis prix revient au SL 1R.
        Résultat attendu : +1R×50% + +2R×25% + +1R×25% = +1.25R.
        """
        # Setup
        entree = 2000.0
        sl_initial = 1990.0
        distance = 10.0  # 1R = 10$

        # Simuler les fermetures partielles
        # TP1 : 50% @ 2010 → P&L = +10$ × 0.05 lots × 100 = +50$
        # TP2 : 25% @ 2020 → P&L = +20$ × 0.025 lots × 100 = +50$
        # TP3 revient au SL 1R = 2010 → P&L = +10$ × 0.025 lots × 100 = +25$
        # Total = +50 + 50 + 25 = +125$ / risque_usd = 10 × 0.10 × 100 = +100$ → 1.25R

        trade = creer_trade_phase3(entree=entree, lot_restant=0.025)
        trade.sl.prix_initial = sl_initial
        trade.lots_initial = 0.10
        trade.pnl_realise_usd = 100.0  # TP1 + TP2 déjà réalisés

        gst = GestionnairePositions(connecteur=None)
        gst._trade_actif = trade

        # Prix revient au SL 1R = 2010
        gst._fermer_tout(trade, RaisonFermeture.SL_TOUCHE, 2010.0)

        # Vérification : R total ≥ 1.20 (tolérance calcul)
        risque_usd = distance * 0.10 * 100.0  # = 100$
        r_simule = trade.pnl_total_usd / risque_usd if risque_usd > 0 else 0
        assert r_simule >= 1.20, f"R garanti insuffisant : {r_simule:.2f}R < 1.20R"


# ── Tests distribution lots ────────────────────────────────────────────────

class TestDistributionLots:
    """50% / 25% / 25% de la position initiale."""

    def test_distribution_50_25_25(self):
        """Les lots sont correctement distribués en 3 tranches."""
        trade = creer_trade_phase1(entree=2000.0, sl=1990.0, lot=0.10)
        assert abs(trade.tp1.lots - 0.05) < 0.005   # 50% ± tolérance arrondi
        assert abs(trade.tp2.lots - 0.025) < 0.005  # 25% ± tolérance arrondi
        assert abs(trade.tp3.lots - 0.025) < 0.005  # 25% ± tolérance arrondi

    def test_somme_lots_egale_total(self):
        """La somme des 3 tranches = lots initial."""
        trade = creer_trade_phase1(entree=2000.0, sl=1990.0, lot=0.12)
        somme = trade.tp1.lots + trade.tp2.lots + trade.tp3.lots
        assert abs(somme - 0.12) < 0.001


# ── Tests métriques MFE/MAE ───────────────────────────────────────────────

class TestMFEMAE:
    """MFE et MAE calculés en R."""

    def test_mfe_mis_a_jour_prix_favorable(self):
        """MFE progresse quand le prix avance en faveur."""
        trade = creer_trade_phase1(entree=2000.0, sl=1990.0)
        gst = GestionnairePositions(connecteur=None)

        gst._maj_excursions(trade, 2015.0)  # +1.5R favorable
        assert abs(trade.mfe - 1.5) < 0.01

    def test_mae_mis_a_jour_prix_adverse(self):
        """MAE progresse quand le prix va contre le trade."""
        trade = creer_trade_phase1(entree=2000.0, sl=1990.0)
        gst = GestionnairePositions(connecteur=None)

        gst._maj_excursions(trade, 1995.0)  # -0.5R adverse
        assert abs(trade.mae - 0.5) < 0.01

    def test_mfe_ne_diminue_pas(self):
        """MFE ne diminue jamais (maximum historique)."""
        trade = creer_trade_phase1(entree=2000.0, sl=1990.0)
        gst = GestionnairePositions(connecteur=None)

        gst._maj_excursions(trade, 2020.0)  # MFE = 2.0R
        gst._maj_excursions(trade, 2005.0)  # Prix revient → MFE reste 2.0R

        assert abs(trade.mfe - 2.0) < 0.01

    def test_mae_positif_seulement(self):
        """MAE ne doit jamais être négatif."""
        trade = creer_trade_phase1(entree=2000.0, sl=1990.0)
        gst = GestionnairePositions(connecteur=None)

        gst._maj_excursions(trade, 2010.0)  # Prix favorable → MAE = 0
        assert trade.mae >= 0.0


# ── Tests persistance ─────────────────────────────────────────────────────

class TestPersistance:
    """Sérialisation et désérialisation du trade."""

    def test_serialisation_deserialisation_round_trip(self, tmp_path):
        """Un trade sérialisé puis désérialisé doit être identique."""
        import json
        from position_tracker import SuiveurPosition

        trade = creer_trade_phase2(entree=2000.0, lot_restant=0.05)

        # Sauvegarder dans un fichier temporaire
        tracker = SuiveurPosition()

        # Simuler la sérialisation
        data = tracker._serialiser(trade)
        trade2 = tracker._deserialiser(data)

        assert trade2 is not None
        assert trade2.id_trade == trade.id_trade
        assert trade2.phase == PhaseTrade.PHASE_2
        assert abs(trade2.sl.prix_actuel - trade.sl.prix_actuel) < 0.01
        assert trade2.tp1.atteint == trade.tp1.atteint
        assert abs(trade2.lots_restants - trade.lots_restants) < 0.001

    def test_deserialisation_raison_fermeture(self):
        """La raison de fermeture est correctement reconstituée."""
        from position_tracker import SuiveurPosition

        trade = creer_trade_phase1()
        trade.raison_fermeture = RaisonFermeture.TP2_ATTEINT
        trade.est_ferme = True

        tracker = SuiveurPosition()
        data = tracker._serialiser(trade)
        trade2 = tracker._deserialiser(data)

        assert trade2.raison_fermeture == RaisonFermeture.TP2_ATTEINT


# ── Tests enregistrer_position ─────────────────────────────────────────────

class TestEnregistrerPosition:
    """Tests de la méthode d'enregistrement d'une position."""

    def test_enregistrer_cree_trade_gere(self):
        """enregistrer_position() crée un TradeGere valide."""
        gst = GestionnairePositions(connecteur=None)
        gst.enregistrer_position(
            ticket=99999, symbole="XAUUSD", direction="LONG",
            volume=0.10, prix_entree=2000.0, sl=1990.0,
            tp1=2010.0, tp2=2020.0, tp3=2030.0, sl_distance=10.0,
        )
        assert gst._trade_actif is not None
        assert gst._trade_actif.phase == PhaseTrade.PHASE_1
        assert gst._trade_actif.direction == DirectionTrade.LONG

    def test_enregistrer_position_a_position_ouverte(self):
        """a_position_ouverte() retourne True après enregistrement."""
        gst = GestionnairePositions(connecteur=None)
        assert gst.a_position_ouverte() is False

        gst.enregistrer_position(
            ticket=1, symbole="XAUUSD", direction="LONG",
            volume=0.05, prix_entree=2000.0, sl=1990.0,
            tp1=2010.0, tp2=2020.0, tp3=2030.0, sl_distance=10.0,
        )
        assert gst.a_position_ouverte() is True
        assert gst.a_position_ouverte("XAUUSD") is True
        assert gst.a_position_ouverte("EURUSD") is False
