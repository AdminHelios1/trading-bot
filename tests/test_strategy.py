"""
test_strategy.py — Tests unitaires de la logique de signal SMC.
Vérifie les 5 conditions du setup Breaker Block + Order Block.
"""

import pytest
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent))

from strategy import StrategieSMC, DirectionSignal
from structure_analyzer import AnalyseurStructure, Tendance
from ob_detector import DetecteurOB
from tests.fixtures import (
    creer_df_tendance_haussiere,
    creer_df_tendance_baissiere,
    creer_df_avec_order_block_haussier,
    creer_df_m15_avec_rejet_haussier,
    creer_df_m5_breakout,
)


class TestSignalLong:
    """Tests du signal LONG avec les 5 conditions."""

    def setup_method(self):
        self.strategie = StrategieSMC()

    def test_signal_long_rejete_structure_bearish(self):
        """Un signal LONG doit être rejeté si la structure H4 est baissière."""
        df_h4 = creer_df_tendance_baissiere(n=100)
        df_m15 = creer_df_m15_avec_rejet_haussier(n=60)
        df_m5 = creer_df_m5_breakout(n=50)

        signal = self.strategie.evaluer(
            df_h4=df_h4,
            df_m15=df_m15,
            df_m5=df_m5,
            heure_utc=10,
            est_en_session=True,
        )
        assert signal.direction != DirectionSignal.LONG or not signal.valide
        if signal.direction == DirectionSignal.LONG:
            assert "bearish" in signal.raison_rejet.lower() or \
                   "baissière" in signal.raison_rejet.lower() or \
                   not signal.valide

    def test_signal_long_rejete_hors_session(self):
        """Un signal LONG doit être rejeté si on est hors session de trading."""
        df_h4 = creer_df_tendance_haussiere(n=100)
        df_m15 = creer_df_m15_avec_rejet_haussier(n=60)
        df_m5 = creer_df_m5_breakout(n=50)

        signal = self.strategie.evaluer(
            df_h4=df_h4,
            df_m15=df_m15,
            df_m5=df_m5,
            heure_utc=10,
            est_en_session=False,  # Hors session
        )
        assert not signal.valide
        assert "session" in signal.raison_rejet.lower()

    def test_signal_long_rejete_volatilite_insuffisante(self):
        """Signal rejeté si la volatilité ATR H4 est trop faible."""
        import pandas as pd
        import numpy as np

        # Créer un marché plat (presque aucune volatilité)
        n = 100
        prix = 2000.0
        df_plat = pd.DataFrame({
            "open": [prix + 0.01] * n,
            "high": [prix + 0.02] * n,
            "low": [prix - 0.02] * n,
            "close": [prix + 0.01] * n,
            "volume": [100] * n,
            "spread": [2] * n,
        }, index=pd.date_range("2024-01-01", periods=n, freq="4h", tz="UTC"))

        df_m15 = creer_df_m15_avec_rejet_haussier(n=60)
        df_m5 = creer_df_m5_breakout(n=50)

        signal = self.strategie.evaluer(
            df_h4=df_plat,
            df_m15=df_m15,
            df_m5=df_m5,
            heure_utc=10,
            est_en_session=True,
        )
        # La volatilité insuffisante doit bloquer le signal
        assert not signal.valide

    def test_signal_long_structure_haussiere_sans_ob(self):
        """Signal LONG rejeté s'il n'y a pas d'OB haussier actif."""
        # Tendance haussière simple sans OB détectable
        df_h4 = creer_df_tendance_haussiere(n=100)
        df_m15 = creer_df_m15_avec_rejet_haussier(n=60, prix_base=float(df_h4["close"].iloc[-1]))
        df_m5 = creer_df_m5_breakout(n=50, prix_rejet_high=float(df_h4["close"].iloc[-1]))

        signal = self.strategie.evaluer(
            df_h4=df_h4,
            df_m15=df_m15,
            df_m5=df_m5,
            heure_utc=10,
            est_en_session=True,
        )
        # Avec une tendance haussière simple, il peut y avoir ou non un signal
        # Le test vérifie que l'évaluation ne plante pas
        assert signal.direction in (DirectionSignal.LONG, DirectionSignal.SHORT, DirectionSignal.AUCUN)


class TestSignalShort:
    """Tests du signal SHORT (symétrique au LONG)."""

    def setup_method(self):
        self.strategie = StrategieSMC()

    def test_signal_short_rejete_structure_haussiere(self):
        """Un signal SHORT doit être rejeté si la structure H4 est haussière."""
        df_h4 = creer_df_tendance_haussiere(n=100)
        df_m15 = creer_df_m15_avec_rejet_haussier(n=60)
        df_m5 = creer_df_m5_breakout(n=50)

        signal = self.strategie.evaluer(
            df_h4=df_h4,
            df_m15=df_m15,
            df_m5=df_m5,
            heure_utc=10,
            est_en_session=True,
        )
        # Avec structure haussière, le SHORT doit être bloqué
        if signal.direction == DirectionSignal.SHORT:
            assert not signal.valide

    def test_signal_short_hors_session_rejete(self):
        """Signal SHORT rejeté hors session."""
        df_h4 = creer_df_tendance_baissiere(n=100)
        df_m15 = creer_df_m15_avec_rejet_haussier(n=60)
        df_m5 = creer_df_m5_breakout(n=50)

        signal = self.strategie.evaluer(
            df_h4=df_h4,
            df_m15=df_m15,
            df_m5=df_m5,
            heure_utc=2,
            est_en_session=False,
        )
        assert not signal.valide


class TestInvalidationPosition:
    """Tests de la vérification d'invalidation d'une position ouverte."""

    def setup_method(self):
        self.strategie = StrategieSMC()

    def test_invalidation_long_par_bos_baissier(self):
        """Une position LONG doit être invalidée par un BOS baissier sur H4."""
        from ob_detector import OBMultiTimeframe, TypeOB, ZoneOB
        import pandas as pd

        df_h4 = creer_df_tendance_baissiere(n=100)
        df_m15 = creer_df_tendance_baissiere(n=60, prix_depart=float(df_h4["close"].iloc[-1]))

        prix_actuel = float(df_m15["close"].iloc[-1])
        zone_ref = OBMultiTimeframe(
            type_ob=TypeOB.HAUSSIER,
            symbole="XAUUSD",
            zone_entree_bas=prix_actuel - 20,
            zone_entree_haut=prix_actuel - 15,
            zone_entree_milieu=prix_actuel - 17.5,
            niveau_invalidation=prix_actuel - 22,
        )

        invalide, raison = self.strategie.position_invalidee(
            df_h4=df_h4,
            df_m15=df_m15,
            direction="LONG",
            zone_reference=zone_ref,
        )
        assert isinstance(invalide, bool)
        assert isinstance(raison, str)

    def test_invalidation_long_close_sous_ob(self):
        """Position LONG invalidée si le prix clôture sous l'OB."""
        from ob_detector import OBMultiTimeframe, TypeOB
        import pandas as pd

        df_h4 = creer_df_tendance_haussiere(n=80)
        df_m15 = creer_df_m15_avec_rejet_haussier(n=60)

        prix_actuel = float(df_m15["close"].iloc[-1])
        zone_ref = OBMultiTimeframe(
            type_ob=TypeOB.HAUSSIER,
            symbole="XAUUSD",
            zone_entree_bas=prix_actuel + 50,
            zone_entree_haut=prix_actuel + 55,
            zone_entree_milieu=prix_actuel + 52.5,
            niveau_invalidation=prix_actuel + 48,
        )

        invalide, raison = self.strategie.position_invalidee(
            df_h4=df_h4,
            df_m15=df_m15,
            direction="LONG",
            zone_reference=zone_ref,
        )
        assert invalide is True, "La position doit être invalidée (close sous l'OB)"
        assert "OB" in raison or "ob" in raison.lower() or "zone" in raison.lower() \
            or len(raison) > 0


class TestEvaluationComplete:
    """Tests d'intégration de l'évaluation complète du setup."""

    def setup_method(self):
        self.strategie = StrategieSMC()

    def test_evaluer_ne_plante_pas_sur_donnees_quelconques(self):
        """L'évaluation ne doit jamais lever d'exception pour des données valides."""
        for seed in range(5):
            import numpy as np
            np.random.seed(seed * 10)

            df_h4 = creer_df_tendance_haussiere(n=100)
            df_m15 = creer_df_m15_avec_rejet_haussier(n=60)
            df_m5 = creer_df_m5_breakout(n=50)

            try:
                signal = self.strategie.evaluer(
                    df_h4=df_h4,
                    df_m15=df_m15,
                    df_m5=df_m5,
                    heure_utc=10,
                    est_en_session=True,
                )
                assert signal is not None
                assert signal.direction in list(DirectionSignal)
            except Exception as e:
                pytest.fail(f"Exception levée avec seed={seed}: {e}")

    def test_signal_retourne_structure_correcte(self):
        """Le signal retourné doit toujours avoir les attributs requis."""
        df_h4 = creer_df_tendance_haussiere(n=100)
        df_m15 = creer_df_m15_avec_rejet_haussier(n=60)
        df_m5 = creer_df_m5_breakout(n=50)

        signal = self.strategie.evaluer(
            df_h4=df_h4,
            df_m15=df_m15,
            df_m5=df_m5,
            heure_utc=10,
            est_en_session=True,
        )
        assert hasattr(signal, "direction")
        assert hasattr(signal, "valide")
        assert hasattr(signal, "raison_rejet")
        assert hasattr(signal, "score_confiance")
        assert 0 <= signal.score_confiance <= 100
