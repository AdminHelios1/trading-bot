"""
tests/test_correlation_monitor.py — Tests du moniteur de corrélations.

Pure logique — aucune dépendance MT5.
Vérifie la matrice de corrélation et les règles de blocage.
"""

import pytest
from typing import List

from correlation_monitor import MoniteurCorrelation, ResultatCorrelation


# ── Fixture ────────────────────────────────────────────────────────────────

def creer_moniteur(seuil: float = 0.70) -> MoniteurCorrelation:
    """Crée un MoniteurCorrelation avec le seuil par défaut."""
    return MoniteurCorrelation(seuil_blocage=seuil)


# ── Tests : check_pair() ───────────────────────────────────────────────────

class TestCheckPair:

    def test_nas100_sp500_bloqué(self):
        """NAS100 ↔ SP500 : corrélation 0.95 > seuil 0.70 → BLOQUÉ."""
        cm = creer_moniteur()
        result = cm.check_pair("NAS100", "US500")

        assert isinstance(result, ResultatCorrelation)
        assert result.bloque is True
        assert result.blocked is True
        assert abs(result.coefficient - 0.95) < 0.001
        assert abs(result.correlation - 0.95) < 0.001

    def test_sp500_nas100_bloqué_sens_inverse(self):
        """US500 ↔ NAS100 : même résultat dans l'autre sens."""
        cm = creer_moniteur()
        result = cm.check_pair("US500", "NAS100")

        assert result.bloque is True
        assert abs(result.coefficient - 0.95) < 0.001

    def test_xauusd_wti_autorisé(self):
        """XAUUSD ↔ XTIUSD : corrélation 0.40 < seuil 0.70 → AUTORISÉ."""
        cm = creer_moniteur()
        result = cm.check_pair("XAUUSD", "XTIUSD")

        assert result.bloque is False
        assert result.blocked is False
        assert abs(result.coefficient - 0.40) < 0.001

    def test_nas100_xauusd_autorisé(self):
        """NAS100 ↔ XAUUSD : corrélation -0.30 → AUTORISÉ."""
        cm = creer_moniteur()
        result = cm.check_pair("NAS100", "XAUUSD")

        assert result.bloque is False
        assert result.coefficient == pytest.approx(-0.30, abs=0.001)

    def test_nas100_wti_autorisé(self):
        """NAS100 ↔ XTIUSD : corrélation -0.20 → AUTORISÉ."""
        cm = creer_moniteur()
        result = cm.check_pair("NAS100", "XTIUSD")

        assert result.bloque is False

    def test_sp500_xauusd_autorisé(self):
        """US500 ↔ XAUUSD : corrélation -0.30 → AUTORISÉ."""
        cm = creer_moniteur()
        result = cm.check_pair("US500", "XAUUSD")

        assert result.bloque is False

    def test_sp500_wti_autorisé(self):
        """US500 ↔ XTIUSD : corrélation -0.20 → AUTORISÉ."""
        cm = creer_moniteur()
        result = cm.check_pair("US500", "XTIUSD")

        assert result.bloque is False

    def test_paire_inconnue_autorisée(self):
        """Paire inconnue → corrélation 0.0 → autorisée par défaut."""
        cm = creer_moniteur()
        result = cm.check_pair("BTCUSD", "ETHUSD")

        assert result.bloque is False
        assert result.coefficient == 0.0

    def test_seuil_personnalisé(self):
        """Avec seuil 0.30, la corrélation 0.40 (XAUUSD/WTI) est bloquée."""
        cm = creer_moniteur(seuil=0.30)
        result = cm.check_pair("XAUUSD", "XTIUSD")

        assert result.bloque is True
        assert abs(result.coefficient - 0.40) < 0.001


# ── Tests : verifier_nouveau_trade() ──────────────────────────────────────

class TestVerifierNouveauTrade:

    def test_nas100_bloqué_si_sp500_ouvert(self):
        """NAS100 bloqué si SP500 (US500) déjà ouvert."""
        cm = creer_moniteur()
        bloque, raison = cm.verifier_nouveau_trade("NAS100", ["US500"])

        assert bloque is True
        assert "0.95" in raison or "NAS100" in raison or "US500" in raison

    def test_sp500_bloqué_si_nas100_ouvert(self):
        """US500 bloqué si NAS100 déjà ouvert."""
        cm = creer_moniteur()
        bloque, raison = cm.verifier_nouveau_trade("US500", ["NAS100"])

        assert bloque is True

    def test_xauusd_autorisé_si_wti_ouvert(self):
        """XAUUSD autorisé si WTI déjà ouvert."""
        cm = creer_moniteur()
        bloque, raison = cm.verifier_nouveau_trade("XAUUSD", ["XTIUSD"])

        assert bloque is False

    def test_nas100_autorisé_si_xauusd_ouvert(self):
        """NAS100 autorisé si XAUUSD déjà ouvert."""
        cm = creer_moniteur()
        bloque, raison = cm.verifier_nouveau_trade("NAS100", ["XAUUSD"])

        assert bloque is False

    def test_aucune_position_toujours_autorisé(self):
        """Sans position ouverte, tout actif est autorisé."""
        cm = creer_moniteur()

        for symbole in ["XAUUSD", "NAS100", "US500", "XTIUSD"]:
            bloque, raison = cm.verifier_nouveau_trade(symbole, [])
            assert bloque is False, f"{symbole} devrait être autorisé sans position"

    def test_même_symbole_ignoré(self):
        """Un symbole n'est pas comparé avec lui-même."""
        cm = creer_moniteur()
        bloque, raison = cm.verifier_nouveau_trade("XAUUSD", ["XAUUSD"])

        assert bloque is False

    def test_nas100_bloqué_parmi_plusieurs_positions(self):
        """NAS100 bloqué si SP500 est parmi plusieurs positions."""
        cm = creer_moniteur()
        bloque, raison = cm.verifier_nouveau_trade(
            "NAS100", ["XAUUSD", "US500", "XTIUSD"]
        )

        assert bloque is True


# ── Tests : Matrice de corrélation ────────────────────────────────────────

class TestMatriceCorrelation:

    def test_matrice_complète_symétrique(self):
        """get_matrice_complete() retourne une matrice symétrique."""
        cm = creer_moniteur()
        matrice = cm.get_matrice_complete()

        # Vérifier la symétrie
        for s1, voisins in matrice.items():
            for s2, coeff in voisins.items():
                assert s2 in matrice, f"{s2} manquant dans la matrice"
                assert matrice[s2].get(s1) == coeff, (
                    f"Asymétrie détectée : {s1}/{s2}={coeff} "
                    f"mais {s2}/{s1}={matrice[s2].get(s1)}"
                )

    def test_paires_bloquées_retourne_nas100_sp500(self):
        """get_paires_bloquees() inclut NAS100/US500 (0.95 > 0.70)."""
        cm = creer_moniteur()
        paires = cm.get_paires_bloquees()

        # Chercher NAS100/US500 dans les paires
        symboles_bloquees = [(s1, s2) for s1, s2, _ in paires]
        assert ("NAS100", "US500") in symboles_bloquees

    def test_paires_bloquées_coefficients_supérieurs_au_seuil(self):
        """Toutes les paires bloquées ont un coefficient > seuil."""
        cm = creer_moniteur()
        paires = cm.get_paires_bloquees()

        for _, _, coeff in paires:
            assert coeff >= cm.SEUIL_BLOCAGE, (
                f"Paire avec coeff {coeff} < seuil {cm.SEUIL_BLOCAGE} "
                f"dans les paires bloquées"
            )

    def test_get_correlation_xauusd_wti(self):
        """get_correlation() retourne 0.40 pour XAUUSD/XTIUSD."""
        cm = creer_moniteur()
        coeff = cm.get_correlation("XAUUSD", "XTIUSD")

        assert coeff is not None
        assert abs(coeff - 0.40) < 0.001

    def test_get_correlation_sens_inverse(self):
        """get_correlation() fonctionne dans les deux sens."""
        cm = creer_moniteur()
        coeff_1 = cm.get_correlation("NAS100", "US500")
        coeff_2 = cm.get_correlation("US500", "NAS100")

        assert coeff_1 == coeff_2

    def test_get_correlation_inconnue_retourne_none(self):
        """get_correlation() retourne None pour une paire inconnue."""
        cm = creer_moniteur()
        coeff = cm.get_correlation("BTCUSD", "ETHUSD")

        assert coeff is None

    def test_toutes_corrélations_dans_intervalle_valide(self):
        """Toutes les corrélations de la matrice sont dans [-1, +1]."""
        cm = creer_moniteur()

        for (s1, s2), coeff in cm.MATRICE_CORRELATION.items():
            assert -1.0 <= coeff <= 1.0, (
                f"Corrélation hors intervalle : {s1}/{s2} = {coeff}"
            )

    def test_corrélation_nas100_sp500_est_la_plus_élevée(self):
        """NAS100/SP500 doit avoir la corrélation absolue la plus élevée."""
        cm = creer_moniteur()
        coeff_max = max(
            abs(coeff) for coeff in cm.MATRICE_CORRELATION.values()
        )

        coeff_indices = abs(
            cm.MATRICE_CORRELATION.get(("NAS100", "US500"), 0)
        )
        assert coeff_indices == coeff_max, (
            f"NAS100/SP500 ({coeff_indices}) n'est pas le plus élevé ({coeff_max})"
        )


# ── Tests : Alias anglais ──────────────────────────────────────────────────

class TestAliasAnglais:

    def test_correlation_monitor_alias(self):
        """CorrelationMonitor est un alias de MoniteurCorrelation."""
        from correlation_monitor import CorrelationMonitor
        cm = CorrelationMonitor()
        assert isinstance(cm, MoniteurCorrelation)

    def test_check_pair_bloque_propriété(self):
        """ResultatCorrelation.blocked est un alias de bloque."""
        cm = creer_moniteur()
        result = cm.check_pair("NAS100", "US500")

        assert result.blocked == result.bloque

    def test_check_pair_correlation_propriété(self):
        """ResultatCorrelation.correlation est un alias de coefficient."""
        cm = creer_moniteur()
        result = cm.check_pair("XAUUSD", "XTIUSD")

        assert result.correlation == result.coefficient

    def test_check_new_trade_alias(self):
        """check_new_trade() est un alias de verifier_nouveau_trade()."""
        cm = creer_moniteur()

        result_fr = cm.verifier_nouveau_trade("NAS100", ["US500"])
        result_en = cm.check_new_trade("NAS100", ["US500"])

        assert result_fr == result_en
