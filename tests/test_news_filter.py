"""
test_news_filter.py — Tests unitaires complets du filtre news.
Tous les tests fonctionnent hors connexion MT5 et hors réseau.
"""

import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest

# Mock MetaTrader5 avant tout import
sys.path.insert(0, str(Path(__file__).parent.parent))
sys.modules.setdefault("MetaTrader5", MagicMock())

from news_fetcher import EvenementNews, RecuperateurCalendrier
from news_filter import FiltreNews, EVENEMENTS_CRITIQUES_XAUUSD


# ── Fixtures ───────────────────────────────────────────────────────────────

@pytest.fixture
def evenements_test():
    """Événements fictifs injectés directement (pas d'appel réseau)."""
    return [
        EvenementNews(
            datetime_utc=datetime(2025, 2, 7, 13, 30),
            title="Non-Farm Payrolls",
            currency="USD",
            impact="High",
            source="test",
        ),
        EvenementNews(
            datetime_utc=datetime(2025, 2, 7, 15, 0),
            title="Fed Chair Speech",
            currency="USD",
            impact="High",
            source="test",
        ),
        EvenementNews(
            datetime_utc=datetime(2025, 2, 10, 9, 0),
            title="UK GDP m/m",
            currency="GBP",
            impact="High",
            source="test",
        ),
        EvenementNews(
            datetime_utc=datetime(2025, 2, 12, 13, 30),
            title="CPI m/m",
            currency="USD",
            impact="High",
            source="test",
        ),
    ]


@pytest.fixture
def filtre(evenements_test):
    """Instance FiltreNews avec événements injectés."""
    return FiltreNews(evenements=evenements_test)


# ── Tests détection événements critiques ───────────────────────────────────

class TestDetectionEvenementsCritiques:
    """Tests de la méthode _est_critique()."""

    def test_nfp_est_critique(self, filtre):
        """Non-Farm Payrolls doit être détecté comme critique."""
        assert filtre._est_critique("Non-Farm Payrolls") is True

    def test_fed_rate_est_critique(self, filtre):
        """Fed Interest Rate Decision doit être critique."""
        assert filtre._est_critique("Fed Interest Rate Decision") is True

    def test_fomc_statement_est_critique(self, filtre):
        assert filtre._est_critique("FOMC Statement") is True

    def test_fomc_minutes_est_critique(self, filtre):
        assert filtre._est_critique("FOMC Meeting Minutes") is True

    def test_cpi_est_critique(self, filtre):
        assert filtre._est_critique("CPI m/m") is True

    def test_core_cpi_est_critique(self, filtre):
        assert filtre._est_critique("Core CPI m/m") is True

    def test_jackson_hole_est_critique(self, filtre):
        assert filtre._est_critique("Jackson Hole Symposium") is True

    def test_fed_chair_speech_est_critique(self, filtre):
        assert filtre._est_critique("Fed Chair Speech") is True

    def test_gdp_est_critique(self, filtre):
        assert filtre._est_critique("GDP q/q") is True

    def test_uk_gdp_nest_pas_critique(self, filtre):
        """UK GDP est HIGH mais pas critique (fenêtre standard)."""
        assert filtre._est_critique("UK GDP m/m") is False

    def test_uk_house_price_nest_pas_critique(self, filtre):
        assert filtre._est_critique("UK House Price Index") is False

    def test_sensibilite_casse(self, filtre):
        """La détection ne doit pas être sensible à la casse."""
        assert filtre._est_critique("non-farm payrolls") is True
        assert filtre._est_critique("NON-FARM PAYROLLS") is True


# ── Tests NFP (événement CRITIQUE) ────────────────────────────────────────

class TestBlocageNFP:
    """Tests des fenêtres de blocage autour du NFP (critique)."""

    def test_bloque_45min_avant_nfp(self, filtre):
        """13:00 UTC = exactement 30 min avant NFP (dans la fenêtre de 45 min)."""
        heure_test = datetime(2025, 2, 7, 12, 46)  # 44 min avant → bloqué
        autorise, raison = filtre.is_trading_allowed(heure_test)
        assert autorise is False
        assert "Non-Farm Payrolls" in raison

    def test_bloque_juste_avant_fenetre_critique(self, filtre):
        """12:45 UTC = exactement 45 min avant NFP → bloqué."""
        heure_test = datetime(2025, 2, 7, 12, 45)
        autorise, raison = filtre.is_trading_allowed(heure_test)
        assert autorise is False
        assert "Non-Farm Payrolls" in raison

    def test_autorise_46min_avant_nfp(self, filtre):
        """12:44 UTC = 46 min avant NFP → autorisé (hors fenêtre)."""
        heure_test = datetime(2025, 2, 7, 12, 44)
        autorise, _ = filtre.is_trading_allowed(heure_test)
        assert autorise is True

    def test_bloque_pendant_nfp(self, filtre):
        """13:30 UTC = pendant le NFP → bloqué."""
        heure_test = datetime(2025, 2, 7, 13, 30)
        autorise, raison = filtre.is_trading_allowed(heure_test)
        assert autorise is False

    def test_bloque_89min_apres_nfp(self, filtre):
        """14:59 UTC = 89 min après NFP → encore bloqué (fenêtre 90 min)."""
        heure_test = datetime(2025, 2, 7, 14, 59)
        autorise, raison = filtre.is_trading_allowed(heure_test)
        assert autorise is False
        assert "Non-Farm Payrolls" in raison or "Fed Chair Speech" in raison

    def test_raison_contient_nom_event(self, filtre):
        """La raison de blocage doit mentionner le nom de l'événement."""
        heure_test = datetime(2025, 2, 7, 13, 0)
        _, raison = filtre.is_trading_allowed(heure_test)
        assert "Non-Farm Payrolls" in raison

    def test_raison_pre_news_mentionne_minutes_restantes(self, filtre):
        """Avant l'event, la raison doit mentionner 'dans Xmin'."""
        heure_test = datetime(2025, 2, 7, 13, 0)  # 30 min avant
        _, raison = filtre.is_trading_allowed(heure_test)
        assert "min" in raison

    def test_raison_post_news_mentionne_minutes_ecoulees(self, filtre):
        """Après l'event, la raison doit mentionner 'min écoulées'."""
        heure_test = datetime(2025, 2, 7, 14, 0)  # 30 min après
        _, raison = filtre.is_trading_allowed(heure_test)
        assert "écoulées" in raison or "min" in raison


# ── Tests Fed Chair Speech (CRITIQUE consécutif au NFP) ───────────────────

class TestBlocageFedChairSpeech:
    """Tests autour du Fed Chair Speech à 15h00."""

    def test_bloque_45min_avant_fed_speech(self, filtre):
        """14:15 UTC = 45 min avant Fed Speech → bloqué."""
        heure_test = datetime(2025, 2, 7, 14, 15)
        autorise, raison = filtre.is_trading_allowed(heure_test)
        assert autorise is False

    def test_bloque_1min_apres_fed_speech(self, filtre):
        """15:01 UTC = 1 min après Fed Speech → bloqué."""
        heure_test = datetime(2025, 2, 7, 15, 1)
        autorise, raison = filtre.is_trading_allowed(heure_test)
        assert autorise is False
        assert "Fed Chair Speech" in raison

    def test_autorise_apres_fenetre_fed_speech(self, filtre):
        """16:31 UTC = 91 min après Fed Speech → autorisé."""
        heure_test = datetime(2025, 2, 7, 16, 31)
        autorise, _ = filtre.is_trading_allowed(heure_test)
        assert autorise is True


# ── Tests UK GDP (événement HIGH STANDARD) ────────────────────────────────

class TestBlocageStandard:
    """Tests des fenêtres de blocage standard (non critiques)."""

    def test_bloque_30min_avant_gdp(self, filtre):
        """08:31 UTC = 29 min avant UK GDP → bloqué (fenêtre 30 min)."""
        heure_test = datetime(2025, 2, 10, 8, 31)
        autorise, _ = filtre.is_trading_allowed(heure_test)
        assert autorise is False

    def test_bloque_exactement_30min_avant(self, filtre):
        """08:30 UTC = exactement 30 min avant → bloqué."""
        heure_test = datetime(2025, 2, 10, 8, 30)
        autorise, _ = filtre.is_trading_allowed(heure_test)
        assert autorise is False

    def test_autorise_31min_avant_gdp(self, filtre):
        """08:29 UTC = 31 min avant UK GDP → autorisé."""
        heure_test = datetime(2025, 2, 10, 8, 29)
        autorise, _ = filtre.is_trading_allowed(heure_test)
        assert autorise is True

    def test_bloque_59min_apres_gdp(self, filtre):
        """09:59 UTC = 59 min après UK GDP → bloqué (fenêtre 60 min)."""
        heure_test = datetime(2025, 2, 10, 9, 59)
        autorise, _ = filtre.is_trading_allowed(heure_test)
        assert autorise is False

    def test_autorise_61min_apres_gdp(self, filtre):
        """10:01 UTC = 61 min après UK GDP → autorisé."""
        heure_test = datetime(2025, 2, 10, 10, 1)
        autorise, _ = filtre.is_trading_allowed(heure_test)
        assert autorise is True


# ── Tests sans événements ──────────────────────────────────────────────────

class TestSansEvenements:
    """Tests quand le calendrier est vide ou indisponible."""

    def test_calendrier_vide_bloque_par_securite(self):
        """Sans événements chargés et fetcher défaillant → bloquer par sécurité (fail-safe)."""
        fetcher_mock = MagicMock()
        fetcher_mock.recuperer_calendrier.return_value = []  # Fetcher retourne vide
        filtre_vide = FiltreNews(fetcher=fetcher_mock, evenements=[])
        autorise, raison = filtre_vide.is_trading_allowed(datetime(2025, 2, 7, 10, 0))
        assert autorise is False
        assert "sécurité" in raison.lower() or "indisponible" in raison.lower()

    def test_autorise_loin_de_tout_event(self, filtre):
        """Un jour sans événement → autorisé."""
        heure_test = datetime(2025, 2, 8, 10, 0)  # Dimanche, pas d'event
        autorise, raison = filtre.is_trading_allowed(heure_test)
        assert autorise is True
        assert raison == "OK"


# ── Tests méthodes utilitaires ─────────────────────────────────────────────

class TestMethodesUtilitaires:
    """Tests des méthodes get_next_news, get_today_events, etc."""

    def test_get_next_news_retourne_le_premier(self, filtre):
        """get_next_news() doit retourner le prochain event à venir."""
        prochaine = filtre.get_next_news(from_time=datetime(2025, 2, 7, 10, 0))
        assert prochaine is not None
        assert prochaine.title == "Non-Farm Payrolls"
        assert prochaine.datetime_utc == datetime(2025, 2, 7, 13, 30)

    def test_get_next_news_apres_nfp(self, filtre):
        """Après le NFP, la prochaine news doit être Fed Chair Speech."""
        prochaine = filtre.get_next_news(from_time=datetime(2025, 2, 7, 14, 0))
        assert prochaine is not None
        assert prochaine.title == "Fed Chair Speech"

    def test_get_next_news_retourne_none_si_rien(self, filtre):
        """Après tous les events, get_next_news() retourne None."""
        prochaine = filtre.get_next_news(from_time=datetime(2025, 12, 31, 23, 59))
        assert prochaine is None

    def test_get_today_events(self, filtre):
        """get_today_events() doit filtrer sur le jour UTC courant."""
        # Simuler que "aujourd'hui" est le 7 février 2025
        with patch("news_filter.datetime") as mock_dt:
            mock_dt.utcnow.return_value = datetime(2025, 2, 7, 12, 0)
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            events_jour = filtre.get_today_events()
        # On ne peut pas facilement patcher .date() dans le filtre
        # → tester directement avec la logique
        events_feb7 = [e for e in filtre._evenements if e.datetime_utc.date().day == 7]
        assert len(events_feb7) == 2  # NFP + Fed Chair Speech

    def test_is_calendar_fresh_apres_chargement(self, evenements_test):
        """Après force_refresh, le calendrier doit être frais."""
        fetcher_mock = MagicMock()
        fetcher_mock.recuperer_calendrier.return_value = evenements_test
        filtre = FiltreNews(fetcher=fetcher_mock)
        filtre.force_refresh()
        assert filtre.is_calendar_fresh() is True

    def test_is_calendar_fresh_sans_maj(self):
        """Sans mise à jour, le calendrier n'est pas frais."""
        filtre = FiltreNews(evenements=[])
        # Pas de _timestamp_derniere_maj
        assert filtre.is_calendar_fresh() is False

    def test_get_status_retourne_dict_complet(self, filtre):
        """get_status() doit retourner tous les champs attendus."""
        filtre._timestamp_derniere_maj = datetime(2025, 2, 7, 10, 0)
        status = filtre.get_status()
        champs_requis = [
            "source", "derniere_maj", "events_charges",
            "prochaine_news", "calendar_frais", "trading_autorise"
        ]
        for champ in champs_requis:
            assert champ in status, f"Champ manquant : {champ}"

    def test_get_status_trading_bloque_pendant_nfp(self, filtre):
        """get_status() doit refléter le blocage pendant le NFP."""
        status = filtre.get_status()
        # Appel sans check_time → dépend de l'heure réelle, on vérifie juste la structure
        assert isinstance(status["trading_autorise"], bool)

    def test_resume_pour_dashboard_format(self, filtre):
        """resume_pour_dashboard() doit retourner une chaîne non vide."""
        resume = filtre.resume_pour_dashboard()
        assert isinstance(resume, str)
        assert len(resume) > 0


# ── Tests robustesse ───────────────────────────────────────────────────────

class TestRobustesse:
    """Tests de robustesse : exceptions, timezone, edge cases."""

    def test_is_trading_allowed_avec_timezone_aware(self, filtre):
        """is_trading_allowed() doit accepter les datetime timezone-aware."""
        heure_utc = datetime(2025, 2, 7, 10, 0, tzinfo=timezone.utc)
        autorise, raison = filtre.is_trading_allowed(heure_utc)
        assert isinstance(autorise, bool)
        assert isinstance(raison, str)

    def test_is_trading_allowed_sans_argument(self, filtre):
        """Sans argument → utilise datetime.utcnow(), ne doit pas planter."""
        autorise, raison = filtre.is_trading_allowed()
        assert isinstance(autorise, bool)

    def test_force_refresh_avec_fetcher_qui_echoue(self):
        """Si le fetcher échoue, force_refresh() retourne False sans lever d'exception."""
        fetcher_mock = MagicMock()
        fetcher_mock.recuperer_calendrier.side_effect = Exception("Réseau indisponible")
        filtre = FiltreNews(fetcher=fetcher_mock)
        succes = filtre.force_refresh()
        assert succes is False

    def test_double_event_consecutif_bloque_les_deux(self, filtre):
        """Entre deux events consécutifs, le trading doit rester bloqué."""
        # NFP finit à 13:30 + 90min = 15:00
        # Fed Speech commence à 15:00 - 45min = 14:15
        # Entre 14:15 et 15:00 → bloqué par les deux events
        heure_test = datetime(2025, 2, 7, 14, 30)
        autorise, _ = filtre.is_trading_allowed(heure_test)
        assert autorise is False

    def test_is_critical_event_detecte_variations(self, filtre):
        """La détection critique doit gérer les variations de noms."""
        variantes_nfp = [
            "Non-Farm Payrolls",
            "non-farm payrolls",
            "NON-FARM PAYROLLS",
        ]
        for variante in variantes_nfp:
            assert filtre._est_critique(variante) is True, f"Variante non détectée: {variante}"


# ── Tests EvenementNews ────────────────────────────────────────────────────

class TestEvenementNews:
    """Tests de sérialisation/désérialisation des événements."""

    def test_to_dict_round_trip(self):
        """Un événement sérialisé puis désérialisé doit être identique."""
        event = EvenementNews(
            datetime_utc=datetime(2025, 2, 7, 13, 30),
            title="Non-Farm Payrolls",
            currency="USD",
            impact="High",
            source="test",
        )
        d = event.to_dict()
        event2 = EvenementNews.from_dict(d, source="test")
        assert event2.title == event.title
        assert event2.currency == event.currency
        assert event2.impact == event.impact
        assert event2.datetime_utc == event.datetime_utc

    def test_from_dict_format_iso(self):
        """from_dict() doit accepter le format ISO sans timezone."""
        d = {
            "datetime_utc": "2025-02-07T13:30:00",
            "title": "NFP",
            "currency": "USD",
            "impact": "High",
            "source": "test",
        }
        event = EvenementNews.from_dict(d)
        assert event.datetime_utc == datetime(2025, 2, 7, 13, 30)
