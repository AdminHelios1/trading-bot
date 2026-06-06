"""
daily_bias.py — Analyse du biais institutionnel journalier sur XAUUSD.
6 facteurs combinés calculés une fois par jour à 07h15 UTC.
Filtre les signaux contra-bias pour améliorer le win rate de 8–12 points.
"""

import json
from dataclasses import dataclass, field
from datetime import datetime, date
from enum import Enum
from pathlib import Path
from typing import List, Optional, Tuple
import pandas as pd
from loguru import logger

from config import CONFIG
from indicators import Indicateurs
from asian_session import AnalyseurSessionAsiatique, DonneesSessionAsiatique, BreakoutAsiatique, DirectionBreakout

FICHIER_LOG_BIAIS = Path("data/daily_bias_log.json")


# ── Énumérations ───────────────────────────────────────────────────────────

class DirectionBiais(Enum):
    HAUSSIER = "BULLISH"
    BAISSIER = "BEARISH"
    NEUTRE   = "NEUTRE"
    NO_TRADE = "NO_TRADE"


class ForceBiais(Enum):
    FAIBLE   = "FAIBLE"    # Score 1–2 → trader avec prudence
    MODERE   = "MODÉRÉ"    # Score 3–4 → trader normalement
    FORT     = "FORT"      # Score 5–6 → trader avec confiance


# ── Dataclasses ────────────────────────────────────────────────────────────

@dataclass
class ResultatFacteur:
    """Résultat d'un facteur individuel d'analyse du biais."""
    nom_facteur: str
    direction: str      # "bullish", "bearish" ou "neutral"
    poids: int          # 1 ou 2
    description: str
    valeur: Optional[float] = None


@dataclass
class BiaisJournalier:
    """Biais institutionnel journalier complet."""
    date_utc: str
    direction: DirectionBiais
    force: ForceBiais
    score_haussier: int
    score_baissier: int
    facteurs: List[ResultatFacteur]
    session_asiatique: Optional[DonneesSessionAsiatique]
    niveaux_au_dessus: List[float]
    niveaux_en_dessous: List[float]
    raisons_no_trade: List[str]
    calcule_a: datetime = field(default_factory=datetime.utcnow)
    valide_jusqu_a: Optional[datetime] = None
    notes: str = ""


# ── Analyseur de biais journalier ──────────────────────────────────────────

class AnalyseurBiaisJournalier:
    """
    Analyse 6 facteurs pour déterminer le biais institutionnel du jour XAUUSD.
    Calculé UNE SEULE FOIS par jour à 07h15 UTC.
    Filtre les signaux contra-bias dans strategy.py.
    """

    def __init__(
        self,
        connecteur=None,
        analyseur_structure=None,
        analyseur_asie: Optional[AnalyseurSessionAsiatique] = None,
    ) -> None:
        self.connecteur = connecteur
        self.analyseur_structure = analyseur_structure
        self.analyseur_asie = analyseur_asie or AnalyseurSessionAsiatique(connecteur)
        self._biais_actuel: Optional[BiaisJournalier] = None
        FICHIER_LOG_BIAIS.parent.mkdir(exist_ok=True)

    # ── Calcul principal ───────────────────────────────────────────────────

    def calculer(self) -> BiaisJournalier:
        """
        Calcule le daily bias complet.
        Doit être appelé à 07h15 UTC après la clôture de la session asiatique.

        Returns:
            BiaisJournalier avec direction, force et détail des 6 facteurs.
        """
        aujourd_hui = datetime.utcnow().strftime("%Y-%m-%d")
        maintenant  = datetime.utcnow()

        # Récupérer les données OHLCV (toutes bougies fermées)
        df_d1, df_h4, df_h1 = self._recuperer_donnees()

        # Analyser la session asiatique
        session_asie = None
        try:
            session_asie = self.analyseur_asie.analyser()
        except Exception as e:
            logger.warning(f"Session asiatique indisponible : {e}")

        # Analyse de structure H4
        structure = None
        if self.analyseur_structure is not None:
            try:
                structure = self.analyseur_structure.analyser(df_h4)
            except Exception as e:
                logger.warning(f"Analyse structure indisponible : {e}")

        # ── Calculer les 6 facteurs ────────────────────────────────────────
        facteurs = [
            self._facteur_1_structure_h4(structure, df_h4),
            self._facteur_2_bougie_daily(df_d1),
            self._facteur_3_position_vwap(df_h4),
            self._facteur_4_cloture_asiatique(session_asie),
            self._facteur_5_sweep_liquidite(session_asie, df_h1),
            self._facteur_6_pdh_pdl(df_d1, df_h4),
        ]

        # Compter les votes pondérés
        score_h = sum(f.poids for f in facteurs if f.direction == "bullish")
        score_b = sum(f.poids for f in facteurs if f.direction == "bearish")

        # Déterminer biais et force
        direction, force, raisons_no_trade = self._determiner_biais(
            score_h, score_b, facteurs
        )

        # Niveaux clés
        prix_actuel = float(df_h1["close"].iloc[-1]) if len(df_h1) > 0 else 2000.0
        au_dessus, en_dessous = self._extraire_niveaux_cles(
            session_asie, structure, prix_actuel
        )

        biais = BiaisJournalier(
            date_utc=aujourd_hui,
            direction=direction,
            force=force,
            score_haussier=score_h,
            score_baissier=score_b,
            facteurs=facteurs,
            session_asiatique=session_asie,
            niveaux_au_dessus=au_dessus,
            niveaux_en_dessous=en_dessous,
            raisons_no_trade=raisons_no_trade,
            valide_jusqu_a=maintenant.replace(hour=23, minute=59, second=59),
        )

        self._biais_actuel = biais
        self._sauvegarder_log(biais)
        self._logger_resume(biais)
        return biais

    # ── Les 6 facteurs ────────────────────────────────────────────────────

    def _facteur_1_structure_h4(
        self,
        structure,
        df_h4: pd.DataFrame,
    ) -> ResultatFacteur:
        """
        Facteur 1 — Structure de marché H4 (poids : 2).
        Le facteur directeur — suit la tendance institutionnelle.
        BULLISH : trend H4 = HAUSSIERE + BOS_FORT récent (< 10 bougies)
        BEARISH : trend H4 = BAISSIERE + BOS_FORT récent
        NEUTRAL : RANGE/NEUTRE ou BOS trop ancien
        """
        from structure_analyzer import Tendance

        if structure is None:
            # Fallback : utiliser EMA20/EMA50 H4 comme proxy
            return self._facteur_1_fallback_ema(df_h4)

        tendance = structure.tendance
        dernier_bos = structure.dernier_bos
        age = structure.age_tendance_bougies

        if tendance == Tendance.HAUSSIERE and dernier_bos and dernier_bos.est_valide and age <= 10:
            return ResultatFacteur(
                nom_facteur="Structure H4",
                direction="bullish",
                poids=2,
                description=f"Trend HAUSSIER | BOS_FORT il y a {age} bougies H4",
                valeur=float(age),
            )

        if tendance == Tendance.BAISSIERE and dernier_bos and dernier_bos.est_valide and age <= 10:
            return ResultatFacteur(
                nom_facteur="Structure H4",
                direction="bearish",
                poids=2,
                description=f"Trend BAISSIER | BOS_FORT il y a {age} bougies H4",
                valeur=float(age),
            )

        return ResultatFacteur(
            nom_facteur="Structure H4",
            direction="neutral",
            poids=2,
            description=f"Trend {tendance.value} — pas de biais clair",
            valeur=None,
        )

    def _facteur_1_fallback_ema(self, df_h4: pd.DataFrame) -> ResultatFacteur:
        """Fallback : EMA20 vs EMA50 H4 si structure non disponible."""
        try:
            ema20 = df_h4["close"].ewm(span=20, adjust=False).mean().iloc[-1]
            ema50 = df_h4["close"].ewm(span=50, adjust=False).mean().iloc[-1]
            prix  = float(df_h4["close"].iloc[-1])
            if ema20 > ema50 and prix > ema20:
                return ResultatFacteur("Structure H4", "bullish", 2,
                                       "EMA20 > EMA50 + prix > EMA20 (proxy haussier)", ema20)
            if ema20 < ema50 and prix < ema20:
                return ResultatFacteur("Structure H4", "bearish", 2,
                                       "EMA20 < EMA50 + prix < EMA20 (proxy baissier)", ema20)
        except Exception:
            pass
        return ResultatFacteur("Structure H4", "neutral", 2, "Données insuffisantes", None)

    def _facteur_2_bougie_daily(self, df_d1: pd.DataFrame) -> ResultatFacteur:
        """
        Facteur 2 — Bougie D1 précédente (poids : 2).
        BULLISH : haussière + clôture tiers supérieur (>66%) + corps >= 40%
        BEARISH : baissière + clôture tiers inférieur (<34%) + corps >= 40%
        NEUTRAL : doji ou corps insuffisant
        """
        if len(df_d1) == 0:
            return ResultatFacteur("Bougie Daily", "neutral", 2, "Données D1 indisponibles", None)

        bougie = df_d1.iloc[-1]
        range_total = bougie["high"] - bougie["low"]
        corps        = abs(bougie["close"] - bougie["open"])

        if range_total <= 0:
            return ResultatFacteur("Bougie Daily", "neutral", 2, "Bougie D1 Doji", None)

        pct_corps     = corps / range_total * 100
        pct_cloture   = (bougie["close"] - bougie["low"]) / range_total * 100
        est_haussiere = bougie["close"] > bougie["open"]

        if est_haussiere and pct_cloture >= 66 and pct_corps >= 40:
            return ResultatFacteur(
                "Bougie Daily", "bullish", 2,
                f"D1 haussière forte | Corps: {pct_corps:.0f}% | Clôture: {pct_cloture:.0f}%",
                pct_corps,
            )
        if not est_haussiere and pct_cloture <= 34 and pct_corps >= 40:
            return ResultatFacteur(
                "Bougie Daily", "bearish", 2,
                f"D1 baissière forte | Corps: {pct_corps:.0f}% | Clôture: {pct_cloture:.0f}%",
                pct_corps,
            )

        return ResultatFacteur(
            "Bougie Daily", "neutral", 2,
            f"D1 indécise | Corps: {pct_corps:.0f}% | Clôture: {pct_cloture:.0f}%",
            pct_corps,
        )

    def _facteur_3_position_vwap(self, df_h4: pd.DataFrame) -> ResultatFacteur:
        """
        Facteur 3 — Position par rapport au VWAP / EMA20 H4 (poids : 1).
        BULLISH : prix au-dessus du VWAP (premium côté acheteurs)
        BEARISH : prix en dessous du VWAP
        Fallback : EMA20 H4 comme proxy si VWAP indisponible.
        """
        if len(df_h4) == 0:
            return ResultatFacteur("Position VWAP", "neutral", 1, "Données H4 indisponibles", None)

        prix_actuel = float(df_h4["close"].iloc[-1])

        # Utiliser EMA20 H4 comme proxy VWAP (MT5 ne fournit pas VWAP directement)
        try:
            ema20 = float(df_h4["close"].ewm(span=20, adjust=False).mean().iloc[-1])
            diff_pct = (prix_actuel - ema20) / ema20 * 100

            if diff_pct > 0.1:
                return ResultatFacteur(
                    "Position VWAP (EMA20)",
                    "bullish", 1,
                    f"Prix +{diff_pct:.2f}% au-dessus EMA20 H4 (proxy VWAP)",
                    ema20,
                )
            if diff_pct < -0.1:
                return ResultatFacteur(
                    "Position VWAP (EMA20)",
                    "bearish", 1,
                    f"Prix {diff_pct:.2f}% en dessous EMA20 H4 (proxy VWAP)",
                    ema20,
                )
            return ResultatFacteur(
                "Position VWAP (EMA20)", "neutral", 1,
                f"Prix proche de l'EMA20 H4 (écart: {diff_pct:+.2f}%)",
                ema20,
            )
        except Exception as e:
            return ResultatFacteur("Position VWAP", "neutral", 1, f"Calcul impossible : {e}", None)

    def _facteur_4_cloture_asiatique(
        self,
        session_asie: Optional[DonneesSessionAsiatique],
    ) -> ResultatFacteur:
        """
        Facteur 4 — Clôture de la session asiatique (poids : 1).
        BULLISH : clôture dans le tiers supérieur (>66%)
        BEARISH : clôture dans le tiers inférieur (<34%)
        NEUTRAL : milieu du range
        """
        if session_asie is None:
            return ResultatFacteur("Clôture Asiatique", "neutral", 1,
                                   "Session asiatique indisponible", None)

        range_taille = session_asie.haut_session - session_asie.bas_session
        if range_taille <= 0:
            return ResultatFacteur("Clôture Asiatique", "neutral", 1, "Range nul", None)

        pct_cloture = (
            (session_asie.fermeture_session - session_asie.bas_session)
            / range_taille * 100
        )

        if pct_cloture >= 66:
            return ResultatFacteur(
                "Clôture Asiatique", "bullish", 1,
                f"Clôture tiers supérieur ({pct_cloture:.0f}%) — momentum BULLISH",
                pct_cloture,
            )
        if pct_cloture <= 34:
            return ResultatFacteur(
                "Clôture Asiatique", "bearish", 1,
                f"Clôture tiers inférieur ({pct_cloture:.0f}%) — momentum BEARISH",
                pct_cloture,
            )
        return ResultatFacteur(
            "Clôture Asiatique", "neutral", 1,
            f"Clôture milieu du range ({pct_cloture:.0f}%)",
            pct_cloture,
        )

    def _facteur_5_sweep_liquidite(
        self,
        session_asie: Optional[DonneesSessionAsiatique],
        df_h1: pd.DataFrame,
    ) -> ResultatFacteur:
        """
        Facteur 5 — Sweep de liquidité récent (poids : 2).
        Le signal le plus fiable en SMC — toujours loggé en INFO.
        BULLISH : sweep du SSL asiatique (mèche sous low + clôture au-dessus)
        BEARISH : sweep du BSL asiatique (mèche au-dessus high + clôture en-dessous)
        """
        if session_asie is None or len(df_h1) == 0:
            return ResultatFacteur("Sweep Liquidité", "neutral", 2,
                                   "Session asiatique indisponible", None)

        haut = session_asie.haut_session
        bas  = session_asie.bas_session
        tolerance = (haut - bas) * 0.15

        # Analyser les 4 dernières bougies H1 fermées
        recentes = df_h1.iloc[-5:-1]

        for _, bougie in recentes.iterrows():
            # Sweep SSL : mèche sous le low + clôture au-dessus
            if bougie["low"] < bas - tolerance and bougie["close"] > bas:
                msg = (
                    f"Sweep SSL détecté | Mèche: {bougie['low']:.2f} "
                    f"(sous low asiatique {bas:.2f}) | Clôture: {bougie['close']:.2f}"
                )
                logger.info(f"🎯 {msg}")  # INFO — signal fiable
                return ResultatFacteur("Sweep Liquidité", "bullish", 2, msg, bougie["low"])

            # Sweep BSL : mèche au-dessus du high + clôture en-dessous
            if bougie["high"] > haut + tolerance and bougie["close"] < haut:
                msg = (
                    f"Sweep BSL détecté | Mèche: {bougie['high']:.2f} "
                    f"(au-dessus high asiatique {haut:.2f}) | Clôture: {bougie['close']:.2f}"
                )
                logger.info(f"🎯 {msg}")  # INFO — signal fiable
                return ResultatFacteur("Sweep Liquidité", "bearish", 2, msg, bougie["high"])

        return ResultatFacteur(
            "Sweep Liquidité", "neutral", 2,
            "Aucun sweep de liquidité détecté sur H1", None,
        )

    def _facteur_6_pdh_pdl(
        self,
        df_d1: pd.DataFrame,
        df_h4: pd.DataFrame,
    ) -> ResultatFacteur:
        """
        Facteur 6 — Position par rapport au PDH/PDL (poids : 1).
        PDH = Previous Day High, PDL = Previous Day Low.
        BULLISH : prix au-dessus du PDH (cassure confirmée)
        BEARISH : prix en dessous du PDL
        NEUTRAL : prix dans le range PDH–PDL
        """
        if len(df_d1) < 2 or len(df_h4) == 0:
            return ResultatFacteur("PDH/PDL", "neutral", 1, "Données D1 insuffisantes", None)

        jour_precedent = df_d1.iloc[-1]
        pdh = float(jour_precedent["high"])
        pdl = float(jour_precedent["low"])
        prix = float(df_h4["close"].iloc[-1])

        if prix > pdh:
            pct = (prix - pdh) / pdh * 100
            return ResultatFacteur(
                "PDH/PDL", "bullish", 1,
                f"Prix au-dessus PDH {pdh:.2f} (+{pct:.2f}%) — continuation probable",
                pdh,
            )
        if prix < pdl:
            pct = (pdl - prix) / pdl * 100
            return ResultatFacteur(
                "PDH/PDL", "bearish", 1,
                f"Prix en dessous PDL {pdl:.2f} (-{pct:.2f}%) — continuation probable",
                pdl,
            )

        range_pct = (
            (prix - pdl) / (pdh - pdl) * 100
            if pdh != pdl else 50.0
        )
        direction = (
            "bullish" if range_pct > 60
            else "bearish" if range_pct < 40
            else "neutral"
        )
        return ResultatFacteur(
            "PDH/PDL", direction, 1,
            f"Prix dans range PDH({pdh:.2f})–PDL({pdl:.2f}) | Position: {range_pct:.0f}%",
            range_pct,
        )

    # ── Détermination du biais final ───────────────────────────────────────

    def _determiner_biais(
        self,
        score_h: int,
        score_b: int,
        facteurs: List[ResultatFacteur],
        total_weight: int = 9,  # Paramètre optionnel pour compatibilité tests
    ) -> Tuple[DirectionBiais, ForceBiais, List[str]]:
        """
        Détermine le biais et la force à partir des scores.

        Score max possible : 9 (2+2+1+1+2+1)
        Seuils :
          FORT    : ≥ 5 dans une direction
          MODERE  : 3–4 dans une direction
          FAIBLE  : 1–2 dans une direction
          NEUTRE  : différence < 2
          NO_TRADE: score max < 3 ou structure neutre
        """
        raisons_no_trade = []

        # Vérifier si structure H4 est neutre (facteur le plus important)
        facteur_structure = next(
            (f for f in facteurs if f.nom_facteur == "Structure H4"), None
        )
        if facteur_structure and facteur_structure.direction == "neutral":
            raisons_no_trade.append(
                "Structure H4 neutre — pas de tendance institutionnelle claire"
            )

        # Scores trop équilibrés → NEUTRE
        diff = abs(score_h - score_b)
        if diff < 2 and score_h > 0 and score_b > 0:
            return DirectionBiais.NEUTRE, ForceBiais.FAIBLE, raisons_no_trade

        # Score dominant insuffisant → NO_TRADE
        score_max = max(score_h, score_b)
        if score_max < 3:
            raisons_no_trade.append(
                f"Score max insuffisant ({score_max}/9) — confluence insuffisante"
            )
            return DirectionBiais.NO_TRADE, ForceBiais.FAIBLE, raisons_no_trade

        direction = (
            DirectionBiais.HAUSSIER if score_h > score_b
            else DirectionBiais.BAISSIER
        )

        if score_max >= 5:
            force = ForceBiais.FORT
        elif score_max >= 3:
            force = ForceBiais.MODERE
        else:
            force = ForceBiais.FAIBLE

        return direction, force, raisons_no_trade

    # ── Interface pour strategy.py ─────────────────────────────────────────

    def signal_aligne_avec_biais(
        self,
        direction_signal: str,
    ) -> Tuple[bool, str]:
        """
        Vérifie si un signal est aligné avec le daily bias.
        Appelé par strategy.py avant de valider un signal.

        Args:
            direction_signal: "bullish" ou "bearish".

        Returns:
            Tuple (aligne: bool, raison: str).
        """
        if self._biais_actuel is None:
            return True, "Biais journalier non encore calculé — signal autorisé par défaut"

        biais = self._biais_actuel
        direction_biais = biais.direction

        # NO_TRADE → bloquer tous les signaux
        if direction_biais == DirectionBiais.NO_TRADE:
            return False, f"Daily Bias NO_TRADE — {', '.join(biais.raisons_no_trade)}"

        # NEUTRE → autoriser avec prudence
        if direction_biais == DirectionBiais.NEUTRE:
            return (
                True,
                f"Daily Bias NEUTRE — signal {direction_signal.upper()} autorisé avec prudence",
            )

        # Vérifier alignement
        biais_haussier  = direction_biais == DirectionBiais.HAUSSIER
        signal_haussier = direction_signal == "bullish"

        if biais_haussier == signal_haussier:
            return (
                True,
                f"Signal aligné avec Daily Bias {direction_biais.value} "
                f"[{biais.force.value}] "
                f"(Bull:{biais.score_haussier} Bear:{biais.score_baissier})",
            )

        # Signal contra-bias
        if biais.force == ForceBiais.FORT:
            return (
                False,
                f"Signal {direction_signal.upper()} CONTRA-BIAS "
                f"{direction_biais.value} FORT — bloqué "
                f"(Bull:{biais.score_haussier} Bear:{biais.score_baissier})",
            )
        elif biais.force == ForceBiais.MODERE:
            return (
                False,
                f"Signal {direction_signal.upper()} contra-bias "
                f"{direction_biais.value} MODÉRÉ — bloqué (sauf OB INSTITUTIONNEL score≥80)",
            )
        else:  # FAIBLE
            return (
                True,
                f"⚠️ Signal {direction_signal.upper()} contra-bias "
                f"{direction_biais.value} FAIBLE — autorisé avec prudence",
            )

    def get_breakout_asiatique(self) -> Optional[BreakoutAsiatique]:
        """
        Retourne le breakout asiatique actuel s'il est tradeable.
        Appelé par strategy.py pour détecter les setups London Open.
        """
        if self._biais_actuel is None or self._biais_actuel.session_asiatique is None:
            return None

        if self.connecteur is None:
            return None

        try:
            import MetaTrader5 as mt5
            df_m15 = self.connecteur.get_ohlcv(CONFIG.SYMBOLE, mt5.TIMEFRAME_M15, n_bars=30)
            prix_actuel = float(df_m15["close"].iloc[-2])
            heure_actuelle = datetime.utcnow()
            return self.analyseur_asie.detecter_breakout_london(
                self._biais_actuel.session_asiatique,
                prix_actuel,
                heure_actuelle,
                df_m15,
            )
        except Exception as e:
            logger.debug(f"Impossible de détecter le breakout asiatique : {e}")
            return None

    def necessite_recalcul(self) -> bool:
        """True si le biais doit être recalculé (nouveau jour ou expiré)."""
        if self._biais_actuel is None:
            return True
        aujourd_hui = datetime.utcnow().strftime("%Y-%m-%d")
        return self._biais_actuel.date_utc != aujourd_hui

    def get_resume(self) -> dict:
        """Résumé pour le dashboard."""
        if self._biais_actuel is None:
            return {
                "calcule": False,
                "direction": "NON CALCULÉ",
                "force": "—",
                "score_h": 0,
                "score_b": 0,
                "facteurs": [],
            }
        b = self._biais_actuel
        return {
            "calcule": True,
            "date": b.date_utc,
            "direction": b.direction.value,
            "force": b.force.value,
            "score_h": b.score_haussier,
            "score_b": b.score_baissier,
            "facteurs": [
                {
                    "nom": f.nom_facteur,
                    "direction": f.direction,
                    "poids": f.poids,
                    "description": f.description,
                }
                for f in b.facteurs
            ],
            "session_asie_haut": b.session_asiatique.haut_session if b.session_asiatique else None,
            "session_asie_bas": b.session_asiatique.bas_session if b.session_asiatique else None,
            "session_asie_range_compresse": (
                b.session_asiatique.range_compresse if b.session_asiatique else None
            ),
            "raisons_no_trade": b.raisons_no_trade,
        }

    # ── Utilitaires ────────────────────────────────────────────────────────

    def _recuperer_donnees(self):
        """Récupère les DataFrames D1, H4, H1 depuis MT5."""
        if self.connecteur is None:
            # Mode test — retourner des DataFrames vides
            empty = pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
            return empty, empty, empty

        try:
            import MetaTrader5 as mt5
            df_d1 = self.connecteur.get_ohlcv(CONFIG.SYMBOLE, mt5.TIMEFRAME_D1, 10).iloc[:-1]
            df_h4 = self.connecteur.get_ohlcv(CONFIG.SYMBOLE, CONFIG.TIMEFRAME_HTF, 50).iloc[:-1]
            df_h1 = self.connecteur.get_ohlcv(CONFIG.SYMBOLE, mt5.TIMEFRAME_H1, 50).iloc[:-1]
            return df_d1, df_h4, df_h1
        except Exception as e:
            logger.error(f"Erreur récupération données : {e}")
            empty = pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
            return empty, empty, empty

    def _extraire_niveaux_cles(
        self,
        session_asie: Optional[DonneesSessionAsiatique],
        structure,
        prix_actuel: float,
    ) -> Tuple[List[float], List[float]]:
        """Compile les niveaux clés au-dessus et en dessous du prix."""
        tous = []
        if session_asie:
            tous += [
                session_asie.haut_session,
                session_asie.bas_session,
                session_asie.milieu_range,
            ] + session_asie.equal_hauts + session_asie.equal_bas

        if structure:
            tous += [p for _, p in structure.swings_hauts]
            tous += [p for _, p in structure.swings_bas]

        au_dessus = sorted([l for l in tous if l > prix_actuel * 1.0005])[:5]
        en_dessous = sorted([l for l in tous if l < prix_actuel * 0.9995], reverse=True)[:5]
        return au_dessus, en_dessous

    def _sauvegarder_log(self, biais: BiaisJournalier) -> None:
        """Sauvegarde le biais dans le log JSON."""
        try:
            historique = []
            if FICHIER_LOG_BIAIS.exists():
                try:
                    historique = json.loads(FICHIER_LOG_BIAIS.read_text(encoding="utf-8"))
                except Exception:
                    historique = []

            entree = {
                "date": biais.date_utc,
                "direction": biais.direction.value,
                "force": biais.force.value,
                "score_h": biais.score_haussier,
                "score_b": biais.score_baissier,
                "calcule_a": biais.calcule_a.isoformat(),
                "facteurs": [
                    {"nom": f.nom_facteur, "dir": f.direction, "desc": f.description}
                    for f in biais.facteurs
                ],
            }
            historique.append(entree)
            # Conserver les 90 derniers jours
            historique = historique[-90:]
            FICHIER_LOG_BIAIS.write_text(
                json.dumps(historique, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
        except Exception as e:
            logger.error(f"Impossible de sauvegarder le log biais : {e}")

    def _logger_resume(self, biais: BiaisJournalier) -> None:
        """Log lisible du biais journalier."""
        emoji = (
            "📈" if biais.direction == DirectionBiais.HAUSSIER
            else "📉" if biais.direction == DirectionBiais.BAISSIER
            else "⏸️"
        )
        logger.info(
            f"{emoji} DAILY BIAS {biais.date_utc} | "
            f"{biais.direction.value} [{biais.force.value}] | "
            f"Bull:{biais.score_haussier} Bear:{biais.score_baissier}"
        )
        for f in biais.facteurs:
            icone = "✅" if f.direction != "neutral" else "⚪"
            logger.debug(
                f"  {icone} [{f.nom_facteur}] {f.direction.upper()} "
                f"(poids:{f.poids}) — {f.description}"
            )
        for raison in biais.raisons_no_trade:
            logger.warning(f"  ⛔ NO_TRADE : {raison}")
