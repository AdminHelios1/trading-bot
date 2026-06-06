"""
asian_session.py — Cartographie de la session asiatique XAUUSD (00h00–07h00 UTC).
Identifie le range, equal highs/lows et pools de liquidité pour le London Open.
"""

from dataclasses import dataclass, field
from datetime import datetime, date, timedelta
from enum import Enum
from typing import List, Optional, Dict
import pandas as pd
from loguru import logger

from config import CONFIG
from indicators import Indicateurs


# ── Dataclasses ────────────────────────────────────────────────────────────

class DirectionBreakout(Enum):
    HAUSSIER = "BULLISH"
    BAISSIER = "BEARISH"
    AUCUN    = "AUCUN"


@dataclass
class DonneesSessionAsiatique:
    """Données complètes de la session asiatique 00h00–07h00 UTC."""
    date_utc: str
    haut_session: float
    bas_session: float
    range_pips: float
    ouverture_session: float
    fermeture_session: float
    milieu_range: float
    equal_hauts: List[float]
    equal_bas: List[float]
    pools_liquidite: List[Dict]
    range_compresse: bool
    calcule_a: datetime = field(default_factory=datetime.utcnow)


@dataclass
class BreakoutAsiatique:
    """Détection d'un breakout du range asiatique en London Open."""
    direction: DirectionBreakout
    prix_breakout: float
    heure_breakout: datetime
    niveau_casse: float
    retest_effectue: bool
    prix_retest: Optional[float]
    heure_retest: Optional[datetime]
    est_tradeable: bool


# ── Analyseur de session asiatique ────────────────────────────────────────

class AnalyseurSessionAsiatique:
    """
    Cartographie la session asiatique XAUUSD (00h00–07h00 UTC).
    Identifie le range, les equal highs/lows, et les pools de liquidité.
    Appelé à 07h00 UTC avant le calcul du daily bias.
    """

    HEURE_DEBUT_ASIE = 0   # 00h00 UTC
    HEURE_FIN_ASIE   = 7   # 07h00 UTC

    def __init__(self, connecteur=None) -> None:
        """
        Args:
            connecteur: Instance ConnecteurMT5 (peut être None en mode test).
        """
        self.connecteur = connecteur

    def analyser(
        self,
        date_cible: Optional[date] = None,
        df_m15_externe: Optional[pd.DataFrame] = None,
    ) -> DonneesSessionAsiatique:
        """
        Analyse la session asiatique du jour cible.
        Récupère 7h × 4 bougies/h = 28 bougies M15 de 00h à 07h UTC.

        Args:
            date_cible: Date à analyser (défaut: aujourd'hui UTC).
            df_m15_externe: DataFrame M15 injecté (pour les tests).

        Returns:
            DonneesSessionAsiatique avec tous les niveaux.

        Raises:
            ValueError: Si les données sont insuffisantes.
        """
        if date_cible is None:
            date_cible = datetime.utcnow().date()

        # Récupérer les données M15
        if df_m15_externe is not None:
            df_m15 = df_m15_externe
        elif self.connecteur is not None:
            import MetaTrader5 as mt5
            df_m15 = self.connecteur.get_ohlcv(
                CONFIG.SYMBOLE, mt5.TIMEFRAME_M15, n_bars=50
            )
        else:
            raise ValueError("Connecteur MT5 absent et pas de données injectées")

        # Filtrer uniquement les bougies de la session asiatique
        debut_session = pd.Timestamp(
            year=date_cible.year, month=date_cible.month, day=date_cible.day,
            hour=self.HEURE_DEBUT_ASIE, tz="UTC"
        )
        fin_session = pd.Timestamp(
            year=date_cible.year, month=date_cible.month, day=date_cible.day,
            hour=self.HEURE_FIN_ASIE, tz="UTC"
        )

        # Adapter selon que l'index est tz-aware ou non
        if df_m15.index.tz is not None:
            df_asie = df_m15[
                (df_m15.index >= debut_session)
                & (df_m15.index < fin_session)
            ]
        else:
            df_asie = df_m15[
                (df_m15.index >= debut_session.replace(tzinfo=None))
                & (df_m15.index < fin_session.replace(tzinfo=None))
            ]

        if len(df_asie) < 4:
            raise ValueError(
                f"Données insuffisantes pour la session asiatique du {date_cible} "
                f"({len(df_asie)} bougies M15 trouvées)"
            )

        haut_session  = float(df_asie["high"].max())
        bas_session   = float(df_asie["low"].min())
        ouv_session   = float(df_asie["open"].iloc[0])
        fer_session   = float(df_asie["close"].iloc[-1])
        range_taille  = haut_session - bas_session
        range_pips    = range_taille / 0.10  # 1 pip XAUUSD = 0.10$

        # ATR H4 moyen pour évaluer si le range est compressé
        atr_moyen = self._calculer_atr_h4_moyen()
        range_compresse = range_taille < atr_moyen * 0.3

        return DonneesSessionAsiatique(
            date_utc=date_cible.strftime("%Y-%m-%d"),
            haut_session=haut_session,
            bas_session=bas_session,
            range_pips=range_pips,
            ouverture_session=ouv_session,
            fermeture_session=fer_session,
            milieu_range=(haut_session + bas_session) / 2,
            equal_hauts=self._trouver_equal_levels(df_asie, "high"),
            equal_bas=self._trouver_equal_levels(df_asie, "low"),
            pools_liquidite=self._identifier_pools_liquidite(df_asie),
            range_compresse=range_compresse,
        )

    # ── Détection equal highs/lows ────────────────────────────────────────

    def _trouver_equal_levels(
        self,
        df: pd.DataFrame,
        cote: str,
        tolerance_pct: float = 0.05,
    ) -> List[float]:
        """
        Trouve les equal highs ou equal lows dans la session asiatique.
        Equal = deux pics/creux à moins de tolerance_pct% l'un de l'autre.
        Ces niveaux représentent des pools de liquidité (stop hunts probables).

        Args:
            df: DataFrame M15 de la session asiatique.
            cote: "high" ou "low".
            tolerance_pct: Tolérance en % (0.05% ≈ 1$ sur 2000$).

        Returns:
            Liste des niveaux equal triés.
        """
        niveaux = df[cote].values
        equals = []

        for i in range(len(niveaux)):
            for j in range(i + 1, len(niveaux)):
                if niveaux[i] <= 0:
                    continue
                diff_pct = abs(niveaux[i] - niveaux[j]) / niveaux[i] * 100
                if diff_pct <= tolerance_pct:
                    niveau_moyen = (niveaux[i] + niveaux[j]) / 2
                    # Éviter les doublons proches
                    est_doublon = any(
                        abs(niveau_moyen - e) / max(niveau_moyen, 0.01) * 100 < 0.1
                        for e in equals
                    )
                    if not est_doublon:
                        equals.append(round(niveau_moyen, 2))

        return sorted(equals)

    # ── Identification des pools de liquidité ─────────────────────────────

    def _identifier_pools_liquidite(self, df: pd.DataFrame) -> List[Dict]:
        """
        Identifie les pools de liquidité dans la session asiatique.
        Un pool = zone où le prix est revenu ≥ 3 fois sans casser.

        Types :
          BSL (Buy-Side Liquidity)  : stops des shorts au-dessus → sweep haussier probable
          SSL (Sell-Side Liquidity) : stops des longs en dessous → sweep baissier probable

        Args:
            df: DataFrame M15 de la session asiatique.

        Returns:
            Liste de dicts décrivant chaque pool.
        """
        pools = []
        haut_range = float(df["high"].max())
        bas_range  = float(df["low"].min())

        if haut_range <= bas_range:
            return pools

        # BSL : niveaux proches du high (>= 70% du range)
        seuil_bsl = bas_range + (haut_range - bas_range) * 0.70
        touches_hautes = df[df["high"] >= seuil_bsl]["high"].values
        if len(touches_hautes) >= 3:
            pools.append({
                "niveau": round(float(touches_hautes.mean()), 2),
                "type": "BSL",
                "touches": len(touches_hautes),
                "description": "Buy-Side Liquidity (stops shorts) — sweep possible",
            })

        # SSL : niveaux proches du low (<= 30% du range)
        seuil_ssl = bas_range + (haut_range - bas_range) * 0.30
        touches_basses = df[df["low"] <= seuil_ssl]["low"].values
        if len(touches_basses) >= 3:
            pools.append({
                "niveau": round(float(touches_basses.mean()), 2),
                "type": "SSL",
                "touches": len(touches_basses),
                "description": "Sell-Side Liquidity (stops longs) — sweep possible",
            })

        return pools

    # ── Détection breakout London Open ────────────────────────────────────

    def detecter_breakout_london(
        self,
        donnees_asie: DonneesSessionAsiatique,
        prix_actuel: float,
        heure_actuelle: datetime,
        df_m15: pd.DataFrame,
    ) -> BreakoutAsiatique:
        """
        Détecte si le prix vient de casser le range asiatique en London Open.
        Valable uniquement entre 07h00 et 10h00 UTC.
        Un breakout n'est tradeable qu'après confirmation par un retest.

        Args:
            donnees_asie: Données de la session asiatique.
            prix_actuel: Prix courant.
            heure_actuelle: Datetime UTC actuel.
            df_m15: DataFrame M15 pour analyse du retest.

        Returns:
            BreakoutAsiatique avec statut is_tradeable.
        """
        heure = heure_actuelle.hour
        if not (7 <= heure < 10):
            return BreakoutAsiatique(
                direction=DirectionBreakout.AUCUN,
                prix_breakout=prix_actuel,
                heure_breakout=heure_actuelle,
                niveau_casse=0.0,
                retest_effectue=False,
                prix_retest=None,
                heure_retest=None,
                est_tradeable=False,
            )

        # Analyser les 3 dernières bougies M15 fermées (exclure la bougie en cours)
        recentes = df_m15.iloc[-4:-1]

        breakout_haussier = any(
            row["close"] > donnees_asie.haut_session
            and row["close"] > row["open"]  # Bougie haussière
            for _, row in recentes.iterrows()
        )
        breakout_baissier = any(
            row["close"] < donnees_asie.bas_session
            and row["close"] < row["open"]  # Bougie baissière
            for _, row in recentes.iterrows()
        )

        if not breakout_haussier and not breakout_baissier:
            return BreakoutAsiatique(
                direction=DirectionBreakout.AUCUN,
                prix_breakout=prix_actuel,
                heure_breakout=heure_actuelle,
                niveau_casse=0.0,
                retest_effectue=False,
                prix_retest=None,
                heure_retest=None,
                est_tradeable=False,
            )

        direction = (
            DirectionBreakout.HAUSSIER if breakout_haussier
            else DirectionBreakout.BAISSIER
        )
        niveau_casse = (
            donnees_asie.haut_session if breakout_haussier
            else donnees_asie.bas_session
        )

        # Chercher le retest
        retest = self._detecter_retest(df_m15, niveau_casse, direction, heure_actuelle)

        return BreakoutAsiatique(
            direction=direction,
            prix_breakout=prix_actuel,
            heure_breakout=heure_actuelle,
            niveau_casse=niveau_casse,
            retest_effectue=retest is not None,
            prix_retest=retest["prix"] if retest else None,
            heure_retest=retest["heure"] if retest else None,
            est_tradeable=retest is not None,
        )

    def _detecter_retest(
        self,
        df_m15: pd.DataFrame,
        niveau_casse: float,
        direction: DirectionBreakout,
        heure_actuelle: datetime,
        tolerance_pct: float = 0.05,
    ) -> Optional[Dict]:
        """
        Détecte si le prix a retesté le niveau cassé après le breakout.

        Args:
            df_m15: DataFrame M15.
            niveau_casse: Niveau du range asiatique cassé.
            direction: Direction du breakout.
            heure_actuelle: Heure de référence.
            tolerance_pct: Tolérance en % pour considérer un retest valide.

        Returns:
            Dict avec prix et heure du retest, ou None.
        """
        if niveau_casse <= 0:
            return None

        tolerance = niveau_casse * tolerance_pct / 100
        limite_temps = heure_actuelle - pd.Timedelta(hours=2)

        for idx, row in df_m15.iterrows():
            ts = pd.Timestamp(idx)
            # Harmoniser les timezones pour la comparaison
            ts_naive = ts.replace(tzinfo=None) if ts.tzinfo else ts
            limite_naive = limite_temps.replace(tzinfo=None) if hasattr(limite_temps, 'tzinfo') and limite_temps.tzinfo else limite_temps
            if ts_naive < limite_naive:
                continue

            if direction == DirectionBreakout.HAUSSIER:
                # Retest = prix revient toucher le haut asiatique depuis le haut
                dans_zone = abs(row["low"] - niveau_casse) <= tolerance
            else:
                # Retest = prix revient toucher le bas asiatique depuis le bas
                dans_zone = abs(row["high"] - niveau_casse) <= tolerance

            if dans_zone:
                return {
                    "prix": niveau_casse,
                    "heure": ts.to_pydatetime() if hasattr(ts, "to_pydatetime") else heure_actuelle,
                }

        return None

    def _calculer_atr_h4_moyen(self) -> float:
        """Calcule l'ATR H4 moyen des 5 dernières bougies."""
        if self.connecteur is None:
            return 8.0  # Valeur par défaut pour XAUUSD

        try:
            import MetaTrader5 as mt5
            df_h4 = self.connecteur.get_ohlcv(CONFIG.SYMBOLE, mt5.TIMEFRAME_H4, n_bars=20)
            atr_serie = Indicateurs.atr(df_h4, CONFIG.ATR_PERIODE)
            return float(atr_serie.tail(5).mean())
        except Exception:
            return 8.0
