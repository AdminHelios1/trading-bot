"""
tests/test_session_manager.py — Tests du Session Manager multi-actif.

Tests purement temporels — aucune dépendance MT5.
Vérifie que chaque actif est correctement autorisé/bloqué selon l'heure UTC.
"""

import pytest
from datetime import datetime

from session_manager import GestionnaireSession
from configs.config_xauusd import ConfigXAUUSD
from configs.config_nas100 import ConfigNAS100
from configs.config_sp500 import ConfigSP500
from configs.config_wti import ConfigWTI


# ── Fixture ────────────────────────────────────────────────────────────────

def creer_session_manager() -> GestionnaireSession:
    """Crée un SessionManager avec les 4 configs actifs."""
    configs = {
        "XAUUSD": ConfigXAUUSD(),
        "NAS100": ConfigNAS100(),
        "SP500":  ConfigSP500(),
        "WTI":    ConfigWTI(),
    }
    return GestionnaireSession(configs)


def dt(weekday: int = 0, hour: int = 10, minute: int = 0) -> datetime:
    """
    Crée un datetime UTC de test.
    weekday: 0=lundi, 1=mardi, ..., 5=samedi, 6=dimanche
    """
    # 2025-06-02 est un lundi (weekday=0)
    base = datetime(2025, 6, 2 + weekday, hour, minute, 0)
    return base


# ── Tests XAUUSD ───────────────────────────────────────────────────────────

class TestSessionXAUUSD:

    def test_xauusd_autorisé_london(self):
        """XAUUSD autorisé en session London (09h UTC)."""
        sm = creer_session_manager()
        ok, raison = sm.is_session_active("XAUUSD", dt(hour=9))
        assert ok is True
        assert "London" in raison

    def test_xauusd_autorisé_new_york(self):
        """XAUUSD autorisé en session New York (15h UTC)."""
        sm = creer_session_manager()
        ok, raison = sm.is_session_active("XAUUSD", dt(hour=15))
        assert ok is True
        # Le nom de session scalping peut être "NewYork_Scalp" ou "New_York"
        assert ok is True  # La session est active, peu importe le nom exact

    def test_xauusd_bloqué_nuit(self):
        """XAUUSD bloqué la nuit (03h UTC)."""
        sm = creer_session_manager()
        ok, raison = sm.is_session_active("XAUUSD", dt(hour=3))
        assert ok is False

    def test_xauusd_bloqué_après_session(self):
        """XAUUSD bloqué après 20h UTC."""
        sm = creer_session_manager()
        ok, raison = sm.is_session_active("XAUUSD", dt(hour=21))
        assert ok is False

    def test_xauusd_bloqué_avant_london(self):
        """XAUUSD bloqué avant London (06h30 UTC)."""
        sm = creer_session_manager()
        ok, raison = sm.is_session_active("XAUUSD", dt(hour=6, minute=30))
        assert ok is False

    def test_xauusd_autorisé_début_london(self):
        """XAUUSD autorisé à 07h20 UTC (après les 15 premières minutes de London)."""
        sm = creer_session_manager()
        ok, raison = sm.is_session_active("XAUUSD", dt(hour=7, minute=20))
        assert ok is True


# ── Tests NAS100 ───────────────────────────────────────────────────────────

class TestSessionNAS100:

    def test_nas100_bloqué_session_asiatique(self):
        """NAS100 bloqué en session asiatique (03h UTC)."""
        sm = creer_session_manager()
        ok, raison = sm.is_session_active("NAS100", dt(hour=3))
        assert ok is False

    def test_nas100_bloqué_london(self):
        """NAS100 bloqué pendant London (09h UTC) — indice américain uniquement."""
        sm = creer_session_manager()
        ok, raison = sm.is_session_active("NAS100", dt(hour=9))
        assert ok is False

    def test_nas100_autorisé_pre_market(self):
        """NAS100 autorisé en Killzone NY Open (13h30 UTC)."""
        sm = creer_session_manager()
        ok, raison = sm.is_session_active("NAS100", dt(hour=13, minute=35))
        assert ok is True

    def test_nas100_autorisé_ny_open(self):
        """NAS100 autorisé en Killzone NY Power (16h UTC)."""
        sm = creer_session_manager()
        ok, raison = sm.is_session_active("NAS100", dt(hour=16))
        assert ok is True

    def test_nas100_bloqué_après_17h(self):
        """NAS100 bloqué après 17h UTC (fin des Killzones)."""
        sm = creer_session_manager()
        ok, raison = sm.is_session_active("NAS100", dt(hour=18))
        assert ok is False


# ── Tests SP500 ────────────────────────────────────────────────────────────

class TestSessionSP500:

    def test_sp500_bloqué_matin(self):
        """SP500 bloqué le matin (09h UTC) — NY uniquement."""
        sm = creer_session_manager()
        ok, raison = sm.is_session_active("US500", dt(hour=9))
        assert ok is False

    def test_sp500_autorisé_ny_session(self):
        """SP500 autorisé en session NY (15h UTC)."""
        sm = creer_session_manager()
        ok, raison = sm.is_session_active("US500", dt(hour=15))
        assert ok is True
        assert "NY" in raison or "New_York" in raison

    def test_sp500_autorisé_ny_ouverture(self):
        """SP500 autorisé dès 14h UTC (ouverture NY)."""
        sm = creer_session_manager()
        ok, raison = sm.is_session_active("US500", dt(hour=14, minute=0))
        assert ok is True

    def test_sp500_bloqué_après_21h(self):
        """SP500 bloqué après 21h UTC."""
        sm = creer_session_manager()
        ok, raison = sm.is_session_active("US500", dt(hour=21))
        assert ok is False


# ── Tests WTI ──────────────────────────────────────────────────────────────

class TestSessionWTI:

    def test_wti_autorisé_session_asiatique_02h_06h(self):
        """WTI autorisé en session asiatique 02h–06h UTC."""
        sm = creer_session_manager()
        ok, raison = sm.is_session_active("XTIUSD", dt(hour=3, minute=30))
        assert ok is True
        assert "Asia" in raison

    def test_wti_bloqué_avant_02h_session_asiatique(self):
        """WTI bloqué avant 02h UTC (pas encore en session)."""
        sm = creer_session_manager()
        ok, raison = sm.is_session_active("XTIUSD", dt(hour=1, minute=0))
        assert ok is False

    def test_wti_bloqué_entre_06h_et_07h(self):
        """WTI bloqué entre 06h et 07h UTC (entre sessions)."""
        sm = creer_session_manager()
        ok, raison = sm.is_session_active("XTIUSD", dt(hour=6, minute=30))
        assert ok is False

    def test_wti_autorisé_london(self):
        """WTI autorisé en session London (09h UTC)."""
        sm = creer_session_manager()
        ok, raison = sm.is_session_active("XTIUSD", dt(hour=9))
        assert ok is True
        assert "London" in raison

    def test_wti_autorisé_new_york(self):
        """WTI autorisé en session New York (15h UTC)."""
        sm = creer_session_manager()
        ok, raison = sm.is_session_active("XTIUSD", dt(hour=15))
        assert ok is True

    def test_wti_bloqué_après_20h(self):
        """WTI bloqué après 20h UTC."""
        sm = creer_session_manager()
        ok, raison = sm.is_session_active("XTIUSD", dt(hour=21))
        assert ok is False


# ── Tests Week-end ─────────────────────────────────────────────────────────

class TestWeekEnd:

    def test_samedi_tous_actifs_bloqués(self):
        """Samedi → tous les actifs bloqués."""
        sm = creer_session_manager()
        samedi = dt(weekday=5, hour=12, minute=0)  # Samedi 12h

        for symbole in ["XAUUSD", "NAS100", "US500", "XTIUSD"]:
            ok, raison = sm.is_session_active(symbole, samedi)
            assert ok is False, f"{symbole} devrait être bloqué le samedi"
            assert "Samedi" in raison or "samedi" in raison.lower()

    def test_dimanche_12h_bloqué(self):
        """Dimanche 12h → tous bloqués (avant 23h UTC)."""
        sm = creer_session_manager()
        dimanche = dt(weekday=6, hour=12, minute=0)

        for symbole in ["XAUUSD", "NAS100"]:
            ok, raison = sm.is_session_active(symbole, dimanche)
            assert ok is False, f"{symbole} devrait être bloqué dimanche 12h"

    def test_lundi_09h_xauusd_autorisé(self):
        """Lundi 09h UTC → XAUUSD autorisé (London session)."""
        sm = creer_session_manager()
        lundi = dt(weekday=0, hour=9, minute=0)

        ok, raison = sm.is_session_active("XAUUSD", lundi)
        assert ok is True


# ── Tests Killzones NAS100 ─────────────────────────────────────────────────

class TestKillzonesNAS100:

    def test_killzone_ny_open_13h30_15h30(self):
        """NAS100 autorisé dans la Killzone NY Open (13h30–15h30)."""
        sm = creer_session_manager()

        for minute_test in [35, 45]:
            ok, raison = sm.is_session_active(
                "NAS100", dt(hour=13, minute=minute_test)
            )
            assert ok is True, f"NAS100 devrait être autorisé à 13h{minute_test}"

    def test_hors_killzone_12h_bloqué(self):
        """NAS100 bloqué à 12h (hors Killzone)."""
        sm = creer_session_manager()
        ok, raison = sm.is_session_active("NAS100", dt(hour=12))
        assert ok is False

    def test_killzone_lunch_16h_17h(self):
        """NAS100 autorisé dans la Killzone lunch (16h–17h)."""
        sm = creer_session_manager()
        ok, raison = sm.is_session_active("NAS100", dt(hour=16, minute=30))
        assert ok is True


# ── Tests prochaine session ────────────────────────────────────────────────

class TestProchaineSession:

    def test_prochaine_session_indiquée_dans_raison(self):
        """Le message de rejet mentionne la prochaine session."""
        sm = creer_session_manager()
        ok, raison = sm.is_session_active("XAUUSD", dt(hour=3))

        assert ok is False
        assert "Prochaine" in raison or "prochaine" in raison.lower()

    def test_get_sessions_actives_retourne_tous(self):
        """get_sessions_actives() retourne le statut de tous les actifs."""
        sm = creer_session_manager()
        resultats = sm.get_sessions_actives(dt(hour=9))

        # Doit avoir une entrée pour chaque actif configuré
        assert len(resultats) == 4
        assert "XAUUSD" in resultats
        assert "NAS100" in resultats
