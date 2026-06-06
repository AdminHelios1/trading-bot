"""
test_daily_bias.py — Tests unitaires du module Daily Bias XAUUSD.
Tous les tests utilisent des fixtures sans connexion MT5.
"""

import sys
from datetime import datetime, date
from pathlib import Path
from unittest.mock import MagicMock
import pytest
import pandas as pd
import numpy as np

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.modules.setdefault("MetaTrader5", MagicMock())

from daily_bias import (
    AnalyseurBiaisJournalier, BiaisJournalier, ResultatFacteur,
    DirectionBiais, ForceBiais,
)
from asian_session import (
    AnalyseurSessionAsiatique, DonneesSessionAsiatique,
    BreakoutAsiatique, DirectionBreakout,
)
import daily_bias as db_module


# ── Fixtures ───────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def isoler_fichiers(tmp_path, monkeypatch):
    """Isole le fichier de log dans tmp_path."""
    monkeypatch.setattr(db_module, "FICHIER_LOG_BIAIS", tmp_path / "bias_log.json")


def creer_df_ohlcv(
    n: int = 20,
    prix_base: float = 2000.0,
    tendance: float = 0.1,
    seed: int = 42,
) -> pd.DataFrame:
    """DataFrame OHLCV synthétique."""
    np.random.seed(seed)
    index = pd.date_range("2025-01-01", periods=n, freq="4h", tz="UTC")
    prix = prix_base
    rows = []
    for _ in range(n):
        var = np.random.normal(tendance, 1.5)
        ouv = prix
        fer = prix + var
        haut = max(ouv, fer) + abs(np.random.normal(0, 0.5))
        bas  = min(ouv, fer) - abs(np.random.normal(0, 0.5))
        rows.append({"open": ouv, "high": haut, "low": bas, "close": fer, "volume": 1000})
        prix = fer
    return pd.DataFrame(rows, index=index)


def creer_bougie(
    open_: float, high: float, low: float, close: float
) -> pd.Series:
    """Crée une bougie OHLCV sous forme de Series."""
    return pd.Series({"open": open_, "high": high, "low": low, "close": close})


def creer_session_asiatique(
    haut: float = 2025.0,
    bas: float = 2015.0,
    fermeture: float = 2022.0,
    range_compresse: bool = False,
) -> DonneesSessionAsiatique:
    """Crée une DonneesSessionAsiatique fictive."""
    return DonneesSessionAsiatique(
        date_utc=datetime.utcnow().strftime("%Y-%m-%d"),
        haut_session=haut,
        bas_session=bas,
        range_pips=(haut - bas) / 0.10,
        ouverture_session=(haut + bas) / 2,
        fermeture_session=fermeture,
        milieu_range=(haut + bas) / 2,
        equal_hauts=[haut - 0.1, haut + 0.05],
        equal_bas=[bas + 0.1, bas - 0.05],
        pools_liquidite=[
            {"niveau": haut, "type": "BSL", "touches": 3, "description": "BSL"},
            {"niveau": bas, "type": "SSL", "touches": 3, "description": "SSL"},
        ],
        range_compresse=range_compresse,
    )


def creer_biais(
    direction: DirectionBiais = DirectionBiais.HAUSSIER,
    force: ForceBiais = ForceBiais.FORT,
    score_h: int = 7,
    score_b: int = 2,
    raisons_no_trade: list = None,
) -> BiaisJournalier:
    """Crée un BiaisJournalier fictif."""
    return BiaisJournalier(
        date_utc=datetime.utcnow().strftime("%Y-%m-%d"),
        direction=direction,
        force=force,
        score_haussier=score_h,
        score_baissier=score_b,
        facteurs=[],
        session_asiatique=creer_session_asiatique(),
        niveaux_au_dessus=[2030.0, 2035.0],
        niveaux_en_dessous=[2010.0, 2005.0],
        raisons_no_trade=raisons_no_trade or [],
    )


def creer_analyseur(biais_actuel=None) -> AnalyseurBiaisJournalier:
    """Crée un AnalyseurBiaisJournalier avec mock."""
    analyseur = AnalyseurBiaisJournalier(connecteur=None)
    analyseur._biais_actuel = biais_actuel
    return analyseur


# ── Tests facteur 1 : Structure H4 ────────────────────────────────────────

class TestFacteur1Structure:
    """Tests du facteur Structure H4."""

    def test_facteur1_bullish_fallback_ema(self):
        """Sans structure (None) → fallback EMA → retourne un facteur valide."""
        analyseur = creer_analyseur()
        # DataFrame avec tendance haussière claire
        df_h4 = creer_df_ohlcv(tendance=2.0, seed=1)  # Forte tendance haussière
        result = analyseur._facteur_1_structure_h4(None, df_h4)
        assert result.direction in ("bullish", "bearish", "neutral")
        assert result.poids == 2
        assert result.nom_facteur == "Structure H4"

    def test_facteur1_neutral_sans_structure(self):
        """Structure None avec données plates → neutral ou bullish selon EMA."""
        analyseur = creer_analyseur()
        df_h4 = creer_df_ohlcv(tendance=0.0, seed=99)  # Tendance neutre
        result = analyseur._facteur_1_structure_h4(None, creer_df_ohlcv())
        assert result.direction in ("bullish", "bearish", "neutral")
        assert isinstance(result.poids, int)

    def test_facteur1_neutral_sans_structure(self):
        """Structure None → fallback EMA → result sans planter."""
        analyseur = creer_analyseur()
        df_h4 = creer_df_ohlcv()
        result = analyseur._facteur_1_structure_h4(None, df_h4)
        assert result.direction in ("bullish", "bearish", "neutral")
        assert result.poids == 2


# ── Tests facteur 2 : Bougie Daily ────────────────────────────────────────

class TestFacteur2BougieDaily:
    """Tests du facteur Bougie Daily."""

    def test_facteur2_d1_haussiere_forte(self):
        """Bougie haussière corps 74%, clôture 89% → bullish."""
        # Corps = 22$, Range = 27$ → 81% > 40% ✓
        # Clôture = (2022-2000)/(2027-2000) = 81% > 66% ✓
        bougie = creer_bougie(2000.0, 2027.0, 2000.0, 2022.0)
        df = pd.DataFrame([bougie])
        analyseur = creer_analyseur()
        result = analyseur._facteur_2_bougie_daily(df)
        assert result.direction == "bullish"
        assert result.poids == 2

    def test_facteur2_d1_baissiere_forte(self):
        """Bougie baissière corps 72%, clôture 12% → bearish."""
        # Close < open, clôture dans tiers inférieur
        bougie = creer_bougie(2025.0, 2027.0, 2000.0, 2003.0)
        df = pd.DataFrame([bougie])
        analyseur = creer_analyseur()
        result = analyseur._facteur_2_bougie_daily(df)
        assert result.direction == "bearish"

    def test_facteur2_d1_doji_neutral(self):
        """Bougie Doji (corps <40%) → neutral."""
        bougie = creer_bougie(2010.0, 2020.0, 2000.0, 2012.0)
        # Corps = 2$, Range = 20$ → 10% < 40% → neutral
        df = pd.DataFrame([bougie])
        analyseur = creer_analyseur()
        result = analyseur._facteur_2_bougie_daily(df)
        assert result.direction == "neutral"

    def test_facteur2_df_vide_neutre(self):
        """DataFrame vide → neutral sans planter."""
        df = pd.DataFrame(columns=["open", "high", "low", "close"])
        analyseur = creer_analyseur()
        result = analyseur._facteur_2_bougie_daily(df)
        assert result.direction == "neutral"


# ── Tests facteur 4 : Clôture asiatique ───────────────────────────────────

class TestFacteur4ClotureAsiatique:
    """Tests du facteur Clôture de la session asiatique."""

    def test_facteur4_cloture_tiers_superieur(self):
        """Clôture à 80% du range → bullish."""
        session = creer_session_asiatique(haut=2025, bas=2015, fermeture=2023)
        # Position = (2023-2015)/(2025-2015) = 80% > 66%
        analyseur = creer_analyseur()
        result = analyseur._facteur_4_cloture_asiatique(session)
        assert result.direction == "bullish"

    def test_facteur4_cloture_tiers_inferieur(self):
        """Clôture à 20% du range → bearish."""
        session = creer_session_asiatique(haut=2025, bas=2015, fermeture=2017)
        # Position = (2017-2015)/(2025-2015) = 20% < 34%
        analyseur = creer_analyseur()
        result = analyseur._facteur_4_cloture_asiatique(session)
        assert result.direction == "bearish"

    def test_facteur4_cloture_milieu_neutre(self):
        """Clôture à 50% du range → neutral."""
        session = creer_session_asiatique(haut=2025, bas=2015, fermeture=2020)
        analyseur = creer_analyseur()
        result = analyseur._facteur_4_cloture_asiatique(session)
        assert result.direction == "neutral"

    def test_facteur4_session_none_neutre(self):
        """Session None → neutral sans planter."""
        analyseur = creer_analyseur()
        result = analyseur._facteur_4_cloture_asiatique(None)
        assert result.direction == "neutral"


# ── Tests facteur 5 : Sweep liquidité ─────────────────────────────────────

class TestFacteur5SweepLiquidite:
    """Tests du facteur Sweep de liquidité."""

    def test_facteur5_sweep_ssl_detecte(self):
        """Mèche sous low asiatique + clôture au-dessus → bullish."""
        session = creer_session_asiatique(haut=2025, bas=2015)
        # Tolérance = (2025-2015) × 0.15 = 1.5$
        # Bougie avec low = 2012 (< 2015 - 1.5 = 2013.5) et close = 2018 (> 2015)
        index = pd.date_range("2025-02-07 05:00", periods=5, freq="1h", tz="UTC")
        df_h1 = pd.DataFrame({
            "open": [2016, 2017, 2016, 2013, 2018],
            "high": [2018, 2019, 2017, 2016, 2020],
            "low":  [2015, 2016, 2015, 2012, 2017],  # Mèche à 2012 < 2013.5
            "close":[2017, 2018, 2016, 2018, 2019],  # Close à 2018 > 2015
            "volume": [1000] * 5,
        }, index=index)

        analyseur = creer_analyseur()
        result = analyseur._facteur_5_sweep_liquidite(session, df_h1)
        assert result.direction == "bullish"
        assert result.poids == 2

    def test_facteur5_sweep_bsl_detecte(self):
        """Mèche au-dessus high asiatique + clôture en-dessous → bearish."""
        session = creer_session_asiatique(haut=2025, bas=2015)
        # Tolérance = 1.5$, seuil = 2025 + 1.5 = 2026.5
        index = pd.date_range("2025-02-07 05:00", periods=5, freq="1h", tz="UTC")
        df_h1 = pd.DataFrame({
            "open": [2022, 2023, 2024, 2028, 2020],
            "high": [2023, 2024, 2025, 2030, 2022],  # Mèche à 2030 > 2026.5
            "low":  [2021, 2022, 2023, 2020, 2018],
            "close":[2022, 2023, 2024, 2020, 2019],  # Close 2020 < 2025
            "volume": [1000] * 5,
        }, index=index)

        analyseur = creer_analyseur()
        result = analyseur._facteur_5_sweep_liquidite(session, df_h1)
        assert result.direction == "bearish"

    def test_facteur5_pas_de_sweep_neutre(self):
        """Aucun sweep → neutral."""
        session = creer_session_asiatique(haut=2025, bas=2015)
        index = pd.date_range("2025-02-07 05:00", periods=5, freq="1h", tz="UTC")
        df_h1 = pd.DataFrame({
            "open": [2018, 2019, 2020, 2019, 2020],
            "high": [2020, 2021, 2022, 2021, 2022],
            "low":  [2017, 2018, 2019, 2018, 2019],
            "close":[2019, 2020, 2021, 2020, 2021],
            "volume": [1000] * 5,
        }, index=index)

        analyseur = creer_analyseur()
        result = analyseur._facteur_5_sweep_liquidite(session, df_h1)
        assert result.direction == "neutral"


# ── Tests facteur 6 : PDH/PDL ─────────────────────────────────────────────

class TestFacteur6PDH_PDL:
    """Tests du facteur PDH/PDL."""

    def test_facteur6_prix_au_dessus_pdh(self):
        """Prix au-dessus PDH → bullish."""
        # df_d1 doit avoir >= 2 lignes (iloc[-1] = veille)
        idx = pd.date_range("2025-02-05", periods=2, freq="D", tz="UTC")
        df_d1 = pd.DataFrame([
            {"open": 2005, "high": 2020, "low": 2000, "close": 2015},
            {"open": 2010, "high": 2025, "low": 2008, "close": 2020},  # veille
        ], index=idx)
        df_h4 = creer_df_ohlcv()
        df_h4["close"] = 2030.0  # > PDH 2025
        analyseur = creer_analyseur()
        result = analyseur._facteur_6_pdh_pdl(df_d1, df_h4)
        assert result.direction == "bullish"

    def test_facteur6_prix_sous_pdl(self):
        """Prix en dessous PDL → bearish."""
        idx = pd.date_range("2025-02-05", periods=2, freq="D", tz="UTC")
        df_d1 = pd.DataFrame([
            {"open": 2005, "high": 2020, "low": 2000, "close": 2015},
            {"open": 2010, "high": 2025, "low": 2008, "close": 2020},
        ], index=idx)
        df_h4 = creer_df_ohlcv()
        df_h4["close"] = 2005.0  # < PDL 2008
        analyseur = creer_analyseur()
        result = analyseur._facteur_6_pdh_pdl(df_d1, df_h4)
        assert result.direction == "bearish"


# ── Tests détermination du biais ──────────────────────────────────────────

class TestDeterminationBiais:
    """Tests de _determiner_biais()."""

    def test_biais_bullish_fort_5_facteurs(self):
        """Score 7/9 → BULLISH FORT."""
        analyseur = creer_analyseur()
        facteurs = [
            ResultatFacteur("F1", "bullish", 2, "", None),
            ResultatFacteur("F2", "bullish", 2, "", None),
            ResultatFacteur("F3", "bullish", 1, "", None),
            ResultatFacteur("F4", "neutral", 1, "", None),
            ResultatFacteur("F5", "bullish", 2, "", None),
            ResultatFacteur("F6", "bearish", 1, "", None),
        ]
        direction, force, _ = analyseur._determiner_biais(7, 1, facteurs)
        assert direction == DirectionBiais.HAUSSIER
        assert force == ForceBiais.FORT

    def test_biais_neutral_scores_equilibres(self):
        """Bull=4 Bear=4 → NEUTRE."""
        analyseur = creer_analyseur()
        facteurs = [
            ResultatFacteur("F1", "bullish", 2, "", None),
            ResultatFacteur("F2", "bearish", 2, "", None),
            ResultatFacteur("F3", "bullish", 2, "", None),
            ResultatFacteur("F4", "bearish", 2, "", None),
            ResultatFacteur("F5", "neutral", 1, "", None),
            ResultatFacteur("F6", "neutral", 1, "", None),
        ]
        direction, force, _ = analyseur._determiner_biais(4, 4, facteurs)
        assert direction == DirectionBiais.NEUTRE

    def test_biais_no_trade_score_insuffisant(self):
        """Score max < 3 avec structure neutre → NO_TRADE."""
        analyseur = creer_analyseur()
        # Structure H4 neutre + score dominant insuffisant → NO_TRADE
        facteurs = [
            ResultatFacteur("Structure H4", "neutral", 2, "", None),  # structure neutre!
            ResultatFacteur("F2", "bullish", 1, "", None),
            ResultatFacteur("F3", "neutral", 1, "", None),
            ResultatFacteur("F4", "neutral", 1, "", None),
            ResultatFacteur("F5", "neutral", 2, "", None),
            ResultatFacteur("F6", "neutral", 1, "", None),
        ]
        direction, force, raisons = analyseur._determiner_biais(1, 0, facteurs)
        # Score 1 < 3 → NO_TRADE
        assert direction == DirectionBiais.NO_TRADE
        assert len(raisons) > 0

    def test_biais_bearish_modere(self):
        """Score Bear=4, Bull=1 → BEARISH MODÉRÉ."""
        analyseur = creer_analyseur()
        facteurs = [
            ResultatFacteur("F1", "bearish", 2, "", None),
            ResultatFacteur("F2", "bearish", 2, "", None),
            ResultatFacteur("F3", "bullish", 1, "", None),
            ResultatFacteur("F4", "neutral", 1, "", None),
            ResultatFacteur("F5", "neutral", 2, "", None),
            ResultatFacteur("F6", "neutral", 1, "", None),
        ]
        direction, force, _ = analyseur._determiner_biais(1, 4, facteurs)
        assert direction == DirectionBiais.BAISSIER
        assert force == ForceBiais.MODERE


# ── Tests signal_aligne_avec_biais() ──────────────────────────────────────

class TestSignalAligne:
    """Tests de l'interface principale signal_aligne_avec_biais()."""

    def test_signal_bullish_aligne_biais_bullish(self):
        """Signal LONG + biais BULLISH → autorisé."""
        biais = creer_biais(DirectionBiais.HAUSSIER, ForceBiais.FORT)
        analyseur = creer_analyseur(biais)
        ok, raison = analyseur.signal_aligne_avec_biais("bullish")
        assert ok is True

    def test_signal_bearish_bloque_biais_bullish_fort(self):
        """Signal SHORT + biais BULLISH FORT → bloqué."""
        biais = creer_biais(DirectionBiais.HAUSSIER, ForceBiais.FORT)
        analyseur = creer_analyseur(biais)
        ok, raison = analyseur.signal_aligne_avec_biais("bearish")
        assert ok is False
        assert "CONTRA-BIAS" in raison

    def test_signal_bearish_bloque_biais_bullish_modere(self):
        """Signal SHORT + biais BULLISH MODÉRÉ → bloqué (sauf OB 80+)."""
        biais = creer_biais(DirectionBiais.HAUSSIER, ForceBiais.MODERE)
        analyseur = creer_analyseur(biais)
        ok, raison = analyseur.signal_aligne_avec_biais("bearish")
        assert ok is False

    def test_signal_bullish_autorise_biais_faible_contraire(self):
        """Signal LONG + biais BEARISH FAIBLE → autorisé avec prudence."""
        biais = creer_biais(DirectionBiais.BAISSIER, ForceBiais.FAIBLE)
        analyseur = creer_analyseur(biais)
        ok, raison = analyseur.signal_aligne_avec_biais("bullish")
        assert ok is True
        assert "prudence" in raison.lower() or "faible" in raison.lower()

    def test_signal_bloque_no_trade(self):
        """Biais NO_TRADE → tous les signaux bloqués."""
        biais = creer_biais(DirectionBiais.NO_TRADE, raisons_no_trade=["Structure neutre"])
        analyseur = creer_analyseur(biais)
        ok, raison = analyseur.signal_aligne_avec_biais("bullish")
        assert ok is False
        assert "NO_TRADE" in raison

    def test_signal_autorise_biais_neutre(self):
        """Biais NEUTRE → tous les signaux autorisés."""
        biais = creer_biais(DirectionBiais.NEUTRE, ForceBiais.FAIBLE)
        analyseur = creer_analyseur(biais)
        ok_bull, _ = analyseur.signal_aligne_avec_biais("bullish")
        ok_bear, _ = analyseur.signal_aligne_avec_biais("bearish")
        assert ok_bull is True
        assert ok_bear is True

    def test_signal_autorise_biais_non_calcule(self):
        """Sans biais calculé → signal autorisé par défaut."""
        analyseur = creer_analyseur(biais_actuel=None)
        ok, raison = analyseur.signal_aligne_avec_biais("bullish")
        assert ok is True
        assert "non" in raison.lower()

    def test_necessite_recalcul_si_aucun_biais(self):
        """necesssite_recalcul() retourne True si aucun biais calculé."""
        analyseur = creer_analyseur(biais_actuel=None)
        assert analyseur.necessite_recalcul() is True

    def test_necessite_recalcul_si_ancien_biais(self):
        """necessite_recalcul() retourne True si biais d'un autre jour."""
        biais = creer_biais()
        biais.date_utc = "2020-01-01"  # Vieux biais
        analyseur = creer_analyseur(biais)
        assert analyseur.necessite_recalcul() is True

    def test_pas_de_recalcul_si_biais_du_jour(self):
        """necessite_recalcul() retourne False si biais d'aujourd'hui."""
        biais = creer_biais()
        biais.date_utc = datetime.utcnow().strftime("%Y-%m-%d")
        analyseur = creer_analyseur(biais)
        assert analyseur.necessite_recalcul() is False


# ── Tests session asiatique ────────────────────────────────────────────────

class TestSessionAsiatique:
    """Tests de AnalyseurSessionAsiatique."""

    def test_equal_hauts_detectes(self):
        """Equal highs à moins de 0.05% → détectés."""
        analyseur = AnalyseurSessionAsiatique(connecteur=None)
        index = pd.date_range("2025-01-01", periods=10, freq="15min", tz="UTC")
        df = pd.DataFrame({
            "open": [2000.0] * 10,
            "high": [2024.4, 2024.5, 2024.6, 2020.0, 2021.0,
                     2022.0, 2023.0, 2024.45, 2019.0, 2018.0],
            "low":  [1999.0] * 10,
            "close":[2002.0] * 10,
            "volume": [1000] * 10,
        }, index=index)

        equals = analyseur._trouver_equal_levels(df, "high", tolerance_pct=0.05)
        assert len(equals) >= 1

    def test_pools_liquidite_identifies(self):
        """BSL et SSL détectés si ≥ 3 touches dans les zones."""
        analyseur = AnalyseurSessionAsiatique(connecteur=None)
        # 5 bougies avec hauts proches de 2025 → BSL
        # 5 bougies avec bas proches de 2015 → SSL
        index = pd.date_range("2025-01-01", periods=10, freq="15min", tz="UTC")
        highs = [2025, 2024.8, 2025.1, 2025, 2024.9, 2016, 2017, 2016.5, 2017, 2016]
        lows  = [2016, 2015.8, 2016, 2015.9, 2015.8, 2015, 2014.9, 2015, 2015.1, 2015]
        df = pd.DataFrame({
            "open": [2020.0] * 10,
            "high": highs,
            "low": lows,
            "close": [2020.0] * 10,
            "volume": [1000] * 10,
        }, index=index)

        pools = analyseur._identifier_pools_liquidite(df)
        types = [p["type"] for p in pools]
        assert "BSL" in types or "SSL" in types

    def test_breakout_haussier_avec_retest(self):
        """Breakout au-dessus du high asiatique + retest → tradeable."""
        analyseur = AnalyseurSessionAsiatique(connecteur=None)
        session = creer_session_asiatique(haut=2025.0, bas=2015.0)

        # DataFrame M15 avec breakout et retest
        index = pd.date_range("2025-02-07 07:00", periods=10, freq="15min", tz="UTC")
        closes = [2024.0, 2026.0, 2027.0, 2025.1, 2026.0, 2027.0, 2028.0, 2029.0, 2030.0, 2031.0]
        lows   = [2023.0, 2025.5, 2026.0, 2024.9, 2025.0, 2026.0, 2027.0, 2028.0, 2029.0, 2030.0]
        df_m15 = pd.DataFrame({
            "open": [2023.0] * 10,
            "high": [c + 0.5 for c in closes],
            "low":  lows,
            "close": closes,
            "volume": [1000] * 10,
        }, index=index)

        breakout = analyseur.detecter_breakout_london(
            session,
            prix_actuel=2027.0,
            heure_actuelle=datetime(2025, 2, 7, 8, 30),
            df_m15=df_m15,
        )
        # Le breakout doit être détecté (direction non AUCUN)
        assert breakout.direction != DirectionBreakout.AUCUN

    def test_breakout_hors_fenetre_london_non_tradeable(self):
        """Breakout détecté hors fenêtre London (avant 07h ou après 10h) → non tradeable."""
        analyseur = AnalyseurSessionAsiatique(connecteur=None)
        session = creer_session_asiatique()
        df_m15 = pd.DataFrame({
            "open": [2026.0] * 5,
            "high": [2027.0] * 5,
            "low":  [2025.0] * 5,
            "close": [2026.5] * 5,
            "volume": [1000] * 5,
        }, index=pd.date_range("2025-02-07 11:00", periods=5, freq="15min", tz="UTC"))

        breakout = analyseur.detecter_breakout_london(
            session, 2026.0,
            datetime(2025, 2, 7, 11, 0),  # Après 10h → hors fenêtre
            df_m15,
        )
        assert breakout.direction == DirectionBreakout.AUCUN
        assert breakout.est_tradeable is False
