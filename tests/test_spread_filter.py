"""
test_spread_filter.py — Tests unitaires du filtre spread/volatilité XAUUSD.
Tous les tests s'exécutent SANS connexion MT5 (mocks injectés).
"""

import sys
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest
import pandas as pd
import numpy as np

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.modules.setdefault("MetaTrader5", MagicMock())

from spread_monitor import MoniteurSpread, SnapshotSpread
from spread_filter import FiltreSpread
from config import CONFIG


# ── Helpers / Mocks ───────────────────────────────────────────────────────

def creer_moniteur_mock(
    spread_actuel: float = 15.0,
    spread_moyen: float = 14.0,
    nb_samples: int = 20,
) -> MoniteurSpread:
    """Crée un MoniteurSpread mocké avec des valeurs contrôlées."""
    moniteur = MagicMock(spec=MoniteurSpread)
    moniteur.get_spread_actuel.return_value = spread_actuel
    moniteur.get_spread_moyen.return_value = spread_moyen
    moniteur.get_nb_samples_recents.return_value = nb_samples
    moniteur.get_stats_24h.return_value = {
        "spread_actuel": spread_actuel,
        "spread_moyen_50min": spread_moyen,
        "spread_min_24h": 11.0,
        "spread_max_24h": 28.0,
        "heure_min": "07h UTC",
        "heure_max": "22h UTC",
        "nb_samples_24h": nb_samples,
    }
    return moniteur


def creer_connecteur_mock(
    atr_actuel: float = 8.0,
    atr_moyen: float = 8.0,
    lever_exception: bool = False,
) -> MagicMock:
    """Crée un ConnecteurMT5 mocké qui retourne des données OHLCV avec ATR contrôlé."""
    connecteur = MagicMock()

    if lever_exception:
        connecteur.get_ohlcv.side_effect = Exception("MT5 indisponible")
        return connecteur

    # Créer un DataFrame OHLCV factice avec 64 bougies
    n = 64
    prix = 2000.0
    donnees = {
        "open": [prix] * n,
        "high": [prix + 10] * n,
        "low": [prix - 10] * n,
        "close": [prix + np.random.uniform(-5, 5) for _ in range(n)],
        "volume": [1000] * n,
        "spread": [15] * n,
    }
    index = pd.date_range("2025-01-01", periods=n, freq="4h", tz="UTC")
    df = pd.DataFrame(donnees, index=index)

    connecteur.get_ohlcv.return_value = df
    return connecteur


def creer_filtre(
    spread_actuel: float = 15.0,
    spread_moyen: float = 14.0,
    nb_samples: int = 20,
    atr_actuel: float = 8.0,
    atr_moyen: float = 8.0,
    lever_exception_atr: bool = False,
) -> FiltreSpread:
    """Crée un FiltreSpread complet avec mocks injectés."""
    moniteur = creer_moniteur_mock(spread_actuel, spread_moyen, nb_samples)
    connecteur = creer_connecteur_mock(atr_actuel, atr_moyen, lever_exception_atr)
    return FiltreSpread(moniteur=moniteur, connecteur=connecteur)


# ── Tests Hard Cap ─────────────────────────────────────────────────────────

class TestHardCap:
    """Tests du hard cap absolu (35 pts)."""

    def test_hard_cap_bloque_spread_36pts(self):
        """Spread de 36 pts → bloqué par hard cap."""
        filtre = creer_filtre(spread_actuel=36.0)
        ok, raison = filtre._verifier_hard_cap()
        assert ok is False
        assert "HARD CAP" in raison

    def test_hard_cap_bloque_spread_100pts(self):
        """Spread extrême → bloqué."""
        filtre = creer_filtre(spread_actuel=100.0)
        ok, raison = filtre._verifier_hard_cap()
        assert ok is False
        assert "HARD CAP" in raison

    def test_hard_cap_autorise_spread_34pts(self):
        """Spread de 34 pts → sous le hard cap → autorisé."""
        filtre = creer_filtre(spread_actuel=34.0)
        ok, _ = filtre._verifier_hard_cap()
        assert ok is True

    def test_hard_cap_autorise_spread_exactement_35pts(self):
        """Spread exactement égal au hard cap → autorisé (seuil non inclusif)."""
        filtre = creer_filtre(spread_actuel=35.0)
        ok, _ = filtre._verifier_hard_cap()
        assert ok is True

    def test_hard_cap_raison_contient_valeurs(self):
        """La raison de blocage doit mentionner le spread actuel et la limite."""
        filtre = creer_filtre(spread_actuel=42.0)
        _, raison = filtre._verifier_hard_cap()
        assert "42" in raison
        assert "35" in raison

    def test_hard_cap_spread_zero_non_bloquant(self):
        """Spread à 0 (non disponible) → ne pas bloquer."""
        filtre = creer_filtre(spread_actuel=0.0)
        ok, _ = filtre._verifier_hard_cap()
        assert ok is True


# ── Tests Périodes Dangereuses ─────────────────────────────────────────────

class TestPeriodesDangereuses:
    """Tests de la détection des périodes à spread explosif."""

    def test_cloture_journaliere_21h50_bloquee(self):
        """21h50 UTC = pendant la clôture journalière → bloqué."""
        filtre = creer_filtre()
        ok, raison = filtre._verifier_periode_dangereuse(datetime(2025, 2, 7, 21, 50))
        assert ok is False
        assert "Clôture" in raison

    def test_cloture_journaliere_21h45_bloquee(self):
        """21h45 UTC = début de la fenêtre → bloqué."""
        filtre = creer_filtre()
        ok, _ = filtre._verifier_periode_dangereuse(datetime(2025, 2, 7, 21, 45))
        assert ok is False

    def test_cloture_journaliere_22h10_bloquee(self):
        """22h10 UTC = encore dans la fenêtre → bloqué."""
        filtre = creer_filtre()
        ok, _ = filtre._verifier_periode_dangereuse(datetime(2025, 2, 7, 22, 10))
        assert ok is False

    def test_cloture_journaliere_22h20_autorisee(self):
        """22h20 UTC = après la fenêtre de clôture → autorisé (lundi pour éviter règle vendredi)."""
        filtre = creer_filtre()
        # 2025-02-10 est un lundi
        ok, _ = filtre._verifier_periode_dangereuse(datetime(2025, 2, 10, 22, 20))
        assert ok is True

    def test_vendredi_19h30_bloque(self):
        """Vendredi 19h30 UTC → bloqué (liquidité en baisse)."""
        filtre = creer_filtre()
        # 2025-02-07 est un vendredi (weekday=4)
        ok, raison = filtre._verifier_periode_dangereuse(datetime(2025, 2, 7, 19, 30))
        assert ok is False
        assert "Vendredi" in raison

    def test_vendredi_18h59_autorise(self):
        """Vendredi 18h59 UTC → encore autorisé (avant 19h)."""
        filtre = creer_filtre()
        ok, _ = filtre._verifier_periode_dangereuse(datetime(2025, 2, 7, 18, 59))
        assert ok is True

    def test_samedi_bloque(self):
        """Samedi toute la journée → bloqué (marché fermé)."""
        filtre = creer_filtre()
        # 2025-02-08 est un samedi (weekday=5)
        for heure in [0, 6, 12, 18, 23]:
            ok, raison = filtre._verifier_periode_dangereuse(datetime(2025, 2, 8, heure, 0))
            assert ok is False, f"Heure {heure}h devrait être bloquée"
            assert "Samedi" in raison

    def test_dimanche_22h_bloque(self):
        """Dimanche 22h UTC → bloqué (dans la fenêtre dimanche ET clôture journalière)."""
        filtre = creer_filtre()
        # 2025-02-09 est un dimanche (weekday=6)
        ok, raison = filtre._verifier_periode_dangereuse(datetime(2025, 2, 9, 22, 0))
        assert ok is False
        # Peut être bloqué par la règle dimanche OU la clôture journalière
        assert "Dimanche" in raison or "Clôture" in raison

    def test_dimanche_23h_autorise(self):
        """Dimanche 23h UTC → autorisé (marché rouvert)."""
        filtre = creer_filtre()
        ok, _ = filtre._verifier_periode_dangereuse(datetime(2025, 2, 9, 23, 0))
        assert ok is True

    def test_lundi_10h_autorise(self):
        """Lundi 10h UTC → session active → autorisé."""
        filtre = creer_filtre()
        # 2025-02-10 est un lundi (weekday=0)
        ok, _ = filtre._verifier_periode_dangereuse(datetime(2025, 2, 10, 10, 0))
        assert ok is True

    def test_mercredi_14h_autorise(self):
        """Mercredi 14h UTC → session NY → autorisé."""
        filtre = creer_filtre()
        ok, _ = filtre._verifier_periode_dangereuse(datetime(2025, 2, 12, 14, 0))
        assert ok is True


# ── Tests Spread Dynamique ─────────────────────────────────────────────────

class TestSpreadDynamique:
    """Tests du filtre de spread dynamique (ratio vs moyenne)."""

    def test_spread_dynamique_bloque_ratio_2_6x(self):
        """Spread 37 pts, moyenne 14 pts → ratio 2.64× > seuil 2.5× → bloqué."""
        filtre = creer_filtre(spread_actuel=37.0, spread_moyen=14.0, nb_samples=20)
        ok, raison = filtre._verifier_spread_dynamique()
        assert ok is False
        assert "anormal" in raison.lower()

    def test_spread_dynamique_autorise_ratio_2_4x(self):
        """Spread 33 pts, moyenne 14 pts → ratio 2.36× < seuil 2.5× → autorisé."""
        filtre = creer_filtre(spread_actuel=33.0, spread_moyen=14.0, nb_samples=20)
        ok, _ = filtre._verifier_spread_dynamique()
        assert ok is True

    def test_spread_dynamique_autorise_exactement_seuil(self):
        """Ratio exactement 2.5× → autorisé (seuil non inclusif)."""
        filtre = creer_filtre(spread_actuel=35.0, spread_moyen=14.0, nb_samples=20)
        ok, _ = filtre._verifier_spread_dynamique()
        assert ok is True

    def test_historique_insuffisant_ignore_filtre(self):
        """Moins de 10 samples → filtre dynamique ignoré."""
        filtre = creer_filtre(
            spread_actuel=50.0, spread_moyen=14.0,
            nb_samples=5  # < SPREAD_MIN_HISTORY_SAMPLES (10)
        )
        ok, _ = filtre._verifier_spread_dynamique()
        assert ok is True  # Ignoré, pas bloqué

    def test_spread_moyen_zero_non_bloquant(self):
        """Spread moyen à 0 → pas de division par zéro, non bloquant."""
        filtre = creer_filtre(spread_actuel=20.0, spread_moyen=0.0, nb_samples=20)
        ok, _ = filtre._verifier_spread_dynamique()
        assert ok is True

    def test_raison_contient_ratio(self):
        """La raison doit mentionner le ratio."""
        filtre = creer_filtre(spread_actuel=37.0, spread_moyen=14.0, nb_samples=20)
        _, raison = filtre._verifier_spread_dynamique()
        assert "×" in raison or "x" in raison.lower()


# ── Tests Volatilité ATR ───────────────────────────────────────────────────

class TestVolatiliteATR:
    """Tests du filtre de volatilité ATR."""

    def test_atr_trop_faible_bloque(self):
        """ATR actuel très faible → marché en range → bloqué."""
        filtre = creer_filtre()
        # Patcher l'indicateur ATR directement
        with patch("spread_filter.Indicateurs.atr") as mock_atr:
            # ATR actuel = 3.0, série de 52 valeurs avec moyenne ~8.0
            serie = pd.Series([8.0] * 51 + [3.0])
            mock_atr.return_value = serie
            ok, raison = filtre._verifier_volatilite_atr()
        assert ok is False
        assert "trop faible" in raison.lower()

    def test_atr_excessif_bloque(self):
        """ATR actuel extrêmement élevé → marché en panique → bloqué."""
        filtre = creer_filtre()
        with patch("spread_filter.Indicateurs.atr") as mock_atr:
            # ATR actuel = 28.0, moyenne = 8.0 → ratio 3.5 > 3.0
            serie = pd.Series([8.0] * 51 + [28.0])
            mock_atr.return_value = serie
            ok, raison = filtre._verifier_volatilite_atr()
        assert ok is False
        assert "excessive" in raison.lower()

    def test_atr_normal_autorise(self):
        """ATR normal (ratio 1.2×) → autorisé."""
        filtre = creer_filtre()
        with patch("spread_filter.Indicateurs.atr") as mock_atr:
            # ATR actuel = 9.6, moyenne = 8.0 → ratio 1.2
            serie = pd.Series([8.0] * 51 + [9.6])
            mock_atr.return_value = serie
            ok, _ = filtre._verifier_volatilite_atr()
        assert ok is True

    def test_atr_limite_basse_autorise(self):
        """ATR exactement à 50% de la moyenne → autorisé (seuil non inclusif)."""
        filtre = creer_filtre()
        with patch("spread_filter.Indicateurs.atr") as mock_atr:
            serie = pd.Series([8.0] * 51 + [4.0])  # ratio = 0.5
            mock_atr.return_value = serie
            ok, _ = filtre._verifier_volatilite_atr()
        assert ok is True

    def test_erreur_atr_bloque_par_securite(self):
        """Si MT5 plante → bloquer par sécurité."""
        filtre = creer_filtre(lever_exception_atr=True)
        ok, raison = filtre._verifier_volatilite_atr()
        assert ok is False
        assert "indisponible" in raison.lower()

    def test_sans_connecteur_atr_ignore(self):
        """Sans connecteur MT5 → filtre ATR ignoré."""
        moniteur = creer_moniteur_mock()
        filtre = FiltreSpread(moniteur=moniteur, connecteur=None)
        ok, _ = filtre._verifier_volatilite_atr()
        assert ok is True


# ── Tests Interface Principale ─────────────────────────────────────────────

class TestIsMarketTradeable:
    """Tests de la méthode principale is_market_tradeable()."""

    def test_toutes_conditions_ok_retourne_vrai(self):
        """Toutes les conditions satisfaites → tradeable."""
        filtre = creer_filtre(spread_actuel=15.0, spread_moyen=14.0, nb_samples=20)
        with patch("spread_filter.Indicateurs.atr") as mock_atr:
            serie = pd.Series([8.0] * 52)
            mock_atr.return_value = serie
            # Lundi 10h UTC
            ok, raison = filtre.is_market_tradeable(datetime(2025, 2, 10, 10, 0))
        assert ok is True
        assert raison == "OK"

    def test_hard_cap_depasse_bloque_en_premier(self):
        """Hard cap dépassé → bloqué avant toute autre vérification."""
        filtre = creer_filtre(spread_actuel=50.0)
        ok, raison = filtre.is_market_tradeable(datetime(2025, 2, 10, 10, 0))
        assert ok is False
        assert "HARD CAP" in raison

    def test_periode_dangereuse_bloque_avant_spread_dynamique(self):
        """Période dangereuse → bloqué avant le filtre dynamique."""
        filtre = creer_filtre(spread_actuel=15.0, spread_moyen=14.0, nb_samples=20)
        # Samedi = bloqué peu importe le spread
        ok, raison = filtre.is_market_tradeable(datetime(2025, 2, 8, 10, 0))
        assert ok is False
        assert "Samedi" in raison

    def test_erreur_generique_bloque_par_securite(self):
        """Exception non prévue → bloqué par sécurité."""
        moniteur = MagicMock()
        moniteur.get_spread_actuel.side_effect = Exception("Erreur inattendue")
        filtre = FiltreSpread(moniteur=moniteur, connecteur=None)
        ok, raison = filtre.is_market_tradeable()
        assert ok is False
        assert "sécurité" in raison.lower()

    def test_retourne_toujours_tuple(self):
        """is_market_tradeable() doit toujours retourner un tuple (bool, str)."""
        filtre = creer_filtre()
        resultat = filtre.is_market_tradeable()
        assert isinstance(resultat, tuple)
        assert len(resultat) == 2
        assert isinstance(resultat[0], bool)
        assert isinstance(resultat[1], str)


# ── Tests MoniteurSpread ───────────────────────────────────────────────────

class TestMoniteurSpread:
    """Tests du moniteur de spread."""

    def test_get_spread_moyen_calcul_correct(self):
        """Le spread moyen doit être la moyenne des snapshots récents."""
        moniteur = MoniteurSpread(connecteur=None)
        # Ajouter des snapshots manuellement
        for i, spread in enumerate([10.0, 15.0, 20.0, 25.0]):
            snap = SnapshotSpread(
                timestamp_utc=datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S"),
                spread_points=spread,
                spread_pips=spread / 10,
                spread_usd=spread * 0.1,
                heure_utc=10,
                jour_semaine=0,
            )
            moniteur.ajouter_snapshot_manuel(snap)

        moyen = moniteur.get_spread_moyen(minutes=60)
        assert abs(moyen - 17.5) < 0.1  # (10+15+20+25)/4 = 17.5

    def test_get_spread_actuel_dernier_snapshot(self):
        """get_spread_actuel() doit retourner le dernier snapshot."""
        moniteur = MoniteurSpread(connecteur=None)
        for spread in [10.0, 15.0, 22.0]:
            snap = SnapshotSpread(
                timestamp_utc=datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S"),
                spread_points=spread,
                spread_pips=spread / 10,
                spread_usd=spread * 0.1,
                heure_utc=10,
                jour_semaine=0,
            )
            moniteur.ajouter_snapshot_manuel(snap)
        assert moniteur.get_spread_actuel() == 22.0

    def test_snapshot_serialisation_round_trip(self):
        """Un snapshot sérialisé puis désérialisé doit être identique."""
        snap = SnapshotSpread(
            timestamp_utc="2025-02-07T13:30:00",
            spread_points=18.0,
            spread_pips=1.8,
            spread_usd=1.8,
            heure_utc=13,
            jour_semaine=4,
        )
        d = snap.to_dict()
        snap2 = SnapshotSpread.from_dict(d)
        assert snap2.spread_points == snap.spread_points
        assert snap2.heure_utc == snap.heure_utc
        assert snap2.timestamp_utc == snap.timestamp_utc
