"""
ob_detector.py — Détection des Order Blocks multi-timeframes (H4 + H1 + M15).
Un OB sans confirmation H4 est rejeté automatiquement.
Seuls les OB avec score >= 60 (FORT ou INSTITUTIONNEL) sont retournés.
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import List, Optional, Tuple
import pandas as pd
import numpy as np
from loguru import logger

from config import CONFIG
from indicators import Indicateurs


# ── Énumérations ───────────────────────────────────────────────────────────

class TypeOB(Enum):
    HAUSSIER = "bullish"   # Zone de demande — entrée LONG
    BAISSIER = "bearish"   # Zone d'offre — entrée SHORT


class ForceOB(Enum):
    FAIBLE = "FAIBLE"               # Score 0–39 : OB seul M15, à ignorer
    MODERE = "MODÉRÉ"               # Score 40–59 : confirmé H1 uniquement
    FORT = "FORT"                   # Score 60–79 : confirmé H4 + H1
    INSTITUTIONNEL = "INSTITUTIONNEL"  # Score 80–100 : alignement parfait 3 TF


class StatutOB(Enum):
    ACTIF = "actif"           # Zone intacte, jamais touchée
    TESTE = "testé"           # Touché 1 fois, encore valide
    EPUISE = "épuisé"         # Touché 2 fois → ignorer
    INVALIDE = "invalidé"     # Prix a clôturé au-delà


# ── Dataclasses ────────────────────────────────────────────────────────────

@dataclass
class ZoneOB:
    """Order Block détecté sur un seul timeframe."""
    timeframe: str           # "H4", "H1", "M15"
    type_ob: TypeOB
    zone_haut: float
    zone_bas: float
    zone_milieu: float
    forme_a: datetime
    bougie_open: float
    bougie_haut: float
    bougie_bas: float
    bougie_close: float
    taille_impulsion: float  # Taille du mouvement impulsif suivant (en $)
    fvg_present: bool
    fvg_taille_pct: float    # FVG en % du prix
    nb_touches: int = 0
    statut: StatutOB = StatutOB.ACTIF

    @property
    def est_haussier(self) -> bool:
        return self.type_ob == TypeOB.HAUSSIER

    def contient(self, prix: float) -> bool:
        return self.zone_bas <= prix <= self.zone_haut


@dataclass
class OBMultiTimeframe:
    """Order Block validé sur plusieurs timeframes — seul celui-ci est tradeable."""
    type_ob: TypeOB
    symbole: str

    # Zones par timeframe (None si absent)
    zone_h4: Optional[ZoneOB] = None
    zone_h1: Optional[ZoneOB] = None
    zone_m15: Optional[ZoneOB] = None

    # Zone d'entrée finale (intersection des zones alignées)
    zone_entree_haut: float = 0.0
    zone_entree_bas: float = 0.0
    zone_entree_milieu: float = 0.0

    # Scoring
    score: int = 0
    force: ForceOB = ForceOB.FAIBLE

    # Métadonnées
    cree_a: datetime = field(default_factory=datetime.utcnow)
    mis_a_jour_a: datetime = field(default_factory=datetime.utcnow)
    statut: StatutOB = StatutOB.ACTIF
    niveau_invalidation: float = 0.0
    nb_touches: int = 0

    # Confluences détectées
    confluences: List[str] = field(default_factory=list)

    @property
    def est_haussier(self) -> bool:
        return self.type_ob == TypeOB.HAUSSIER

    @property
    def nb_timeframes(self) -> int:
        return sum([
            self.zone_h4 is not None,
            self.zone_h1 is not None,
            self.zone_m15 is not None,
        ])


# ── Détecteur principal ────────────────────────────────────────────────────

class DetecteurOB:
    """
    Détecte les Order Blocks sur 3 timeframes alignés (H4 + H1 + M15).
    H4 est obligatoire — un OB sans H4 est rejeté.
    Ne retourne que les OB avec score >= 60 (FORT ou INSTITUTIONNEL).
    """

    def __init__(self, connecteur=None) -> None:
        """
        Args:
            connecteur: Instance ConnecteurMT5 (peut être None en mode test).
        """
        self.connecteur = connecteur
        self._obs_actifs: List[OBMultiTimeframe] = []

    # ── Point d'entrée principal ───────────────────────────────────────────

    def detecter_toutes_zones(
        self,
        df_h4: pd.DataFrame,
        df_h1: Optional[pd.DataFrame] = None,
        df_m15: Optional[pd.DataFrame] = None,
    ) -> Tuple[List[OBMultiTimeframe], List[OBMultiTimeframe]]:
        """
        Interface de compatibilité avec l'ancienne API.
        Retourne (ob_actifs, breakers) pour ne pas casser strategy.py.

        Args:
            df_h4: DataFrame H4 (obligatoire).
            df_h1: DataFrame H1 (optionnel).
            df_m15: DataFrame M15 (optionnel).

        Returns:
            Tuple (ob_haussiers_actifs, ob_baissiers_actifs).
        """
        obs = self.detecter_multi_tf(df_h4, df_h1, df_m15)
        haussiers = [ob for ob in obs if ob.est_haussier]
        baissiers = [ob for ob in obs if not ob.est_haussier]
        return haussiers, baissiers

    def detecter_multi_tf(
        self,
        df_h4: pd.DataFrame,
        df_h1: Optional[pd.DataFrame] = None,
        df_m15: Optional[pd.DataFrame] = None,
    ) -> List[OBMultiTimeframe]:
        """
        Détecte et retourne tous les OB multi-TF actifs, triés par score décroissant.
        Ne retourne QUE les OB de force FORT ou INSTITUTIONNEL (score >= 60).

        Args:
            df_h4: DataFrame H4 (obligatoire).
            df_h1: DataFrame H1 (optionnel, améliore le score).
            df_m15: DataFrame M15 (optionnel, affine l'entrée).

        Returns:
            Liste d'OBMultiTimeframe triée par score décroissant.
        """
        from ob_scorer import ScorerOB

        scorer = ScorerOB()

        # Calculer ATR pour chaque TF disponible
        df_h4_atr = self._ajouter_atr(df_h4)
        df_h1_atr = self._ajouter_atr(df_h1) if df_h1 is not None else None
        df_m15_atr = self._ajouter_atr(df_m15) if df_m15 is not None else None

        # Détecter les OB bruts sur chaque TF
        obs_h4 = self._detecter_sur_timeframe(df_h4_atr, "H4")
        obs_h1 = self._detecter_sur_timeframe(df_h1_atr, "H1") if df_h1_atr is not None else []
        obs_m15 = self._detecter_sur_timeframe(df_m15_atr, "M15") if df_m15_atr is not None else []

        logger.debug(
            f"OB bruts | H4: {len(obs_h4)} | H1: {len(obs_h1)} | M15: {len(obs_m15)}"
        )

        # Aligner les OB multi-TF
        obs_mtf = self._aligner_multi_timeframe(obs_h4, obs_h1, obs_m15)

        # Scorer chaque OB
        for ob in obs_mtf:
            ob.score = scorer.calculer_score(ob, df_h4_atr, df_h1_atr, df_m15_atr)
            ob.force = self._score_vers_force(ob.score)
            ob.confluences = scorer.detecter_confluences(ob, df_h4_atr)

        # Filtrer : FORT et INSTITUTIONNEL uniquement (score >= 60)
        obs_valides = [ob for ob in obs_mtf if ob.score >= 60 and ob.nb_touches < 2]

        # Trier par score décroissant
        obs_valides.sort(key=lambda x: x.score, reverse=True)

        # Mettre à jour le statut des OB
        if df_m15 is not None and len(df_m15) > 0:
            self._mettre_a_jour_statuts(obs_valides, df_m15)

        self._obs_actifs = obs_valides
        logger.info(
            f"OB multi-TF | {len(obs_h4)} H4 + {len(obs_h1)} H1 + {len(obs_m15)} M15 "
            f"→ {len(obs_valides)} OB valides (score ≥ 60)"
        )
        return obs_valides

    # ── Détection par timeframe ────────────────────────────────────────────

    def _detecter_sur_timeframe(
        self,
        df: pd.DataFrame,
        label_tf: str,
    ) -> List[ZoneOB]:
        """
        Détecte les OB bruts sur un seul timeframe.
        Utilise uniquement les bougies FERMÉES (iloc[-2] et avant).

        Args:
            df: DataFrame OHLCV avec colonne ATR.
            label_tf: "H4", "H1" ou "M15".

        Returns:
            Liste de ZoneOB détectées.
        """
        if df is None or len(df) < 5:
            return []

        obs = []
        cols_atr = [c for c in df.columns if c.startswith("atr_")]
        col_atr = cols_atr[0] if cols_atr else None

        # Analyser jusqu'à l'avant-dernière bougie ([-2]) pour éviter la bougie en cours
        limite = len(df) - 2
        debut = max(2, limite - 50)  # Fenêtre de 50 bougies

        for i in range(debut, limite):
            bougie = df.iloc[i]
            suivante1 = df.iloc[i + 1]
            suivante2 = df.iloc[i + 2] if i + 2 < len(df) else None
            atr = float(df[col_atr].iloc[i]) if col_atr and not pd.isna(df[col_atr].iloc[i]) else 1.0

            # Détection Bullish OB
            ob_haussier = self._verifier_ob_haussier(bougie, suivante1, suivante2, atr, label_tf)
            if ob_haussier:
                ob_haussier.forme_a = df.index[i].to_pydatetime() if hasattr(df.index[i], 'to_pydatetime') else datetime.utcnow()
                obs.append(ob_haussier)

            # Détection Bearish OB
            ob_baissier = self._verifier_ob_baissier(bougie, suivante1, suivante2, atr, label_tf)
            if ob_baissier:
                ob_baissier.forme_a = df.index[i].to_pydatetime() if hasattr(df.index[i], 'to_pydatetime') else datetime.utcnow()
                obs.append(ob_baissier)

        return obs

    def _verifier_ob_haussier(
        self,
        bougie: pd.Series,
        suivante1: pd.Series,
        suivante2: Optional[pd.Series],
        atr: float,
        label_tf: str,
    ) -> Optional[ZoneOB]:
        """
        Vérifie si une bougie forme un Bullish OB valide.

        Conditions :
        1. Bougie baissière OU longue mèche basse (> 60% du range)
        2. Mouvement impulsif haussier suivant ≥ 1.2× ATR
        3. FVG optionnel (améliore le score mais pas obligatoire)

        Returns:
            ZoneOB si valide, None sinon.
        """
        range_total = bougie["high"] - bougie["low"]
        if range_total <= 0:
            return None

        # Condition 1 : bougie baissière OU longue mèche basse
        est_baissiere = bougie["close"] < bougie["open"]
        meche_basse = min(bougie["open"], bougie["close"]) - bougie["low"]
        longue_meche_basse = (meche_basse / range_total) > 0.6 if range_total > 0 else False

        if not (est_baissiere or longue_meche_basse):
            return None

        # Condition 2 : impulsion haussière suivante
        impulsion1 = suivante1["close"] - suivante1["open"]
        impulsion_valide = impulsion1 >= 1.2 * atr

        if not impulsion_valide and suivante2 is not None:
            impulsion_combinee = suivante2["close"] - bougie["close"]
            impulsion_valide = impulsion_combinee >= 1.5 * atr

        if not impulsion_valide:
            return None

        # Condition 3 : FVG (optionnel — améliore le score)
        seuil_fvg = 0.2 if label_tf in ("H4", "H1") else 0.1
        if suivante2 is not None:
            fvg_taille = suivante2["open"] - bougie["high"]
        else:
            fvg_taille = suivante1["low"] - bougie["high"]

        prix_ref = bougie["close"] if bougie["close"] > 0 else 1.0
        fvg_pct = (fvg_taille / prix_ref) * 100
        fvg_present = fvg_pct >= seuil_fvg

        # Zone = du bas de la bougie OB au max(open, close)
        zone_bas = bougie["low"]
        zone_haut = max(bougie["open"], bougie["close"])
        taille_impulsion = suivante1["high"] - bougie["low"]

        return ZoneOB(
            timeframe=label_tf,
            type_ob=TypeOB.HAUSSIER,
            zone_haut=zone_haut,
            zone_bas=zone_bas,
            zone_milieu=(zone_haut + zone_bas) / 2,
            forme_a=datetime.utcnow(),  # Remplacé après
            bougie_open=float(bougie["open"]),
            bougie_haut=float(bougie["high"]),
            bougie_bas=float(bougie["low"]),
            bougie_close=float(bougie["close"]),
            taille_impulsion=float(taille_impulsion),
            fvg_present=fvg_present,
            fvg_taille_pct=float(max(0.0, fvg_pct)),
        )

    def _verifier_ob_baissier(
        self,
        bougie: pd.Series,
        suivante1: pd.Series,
        suivante2: Optional[pd.Series],
        atr: float,
        label_tf: str,
    ) -> Optional[ZoneOB]:
        """
        Vérifie si une bougie forme un Bearish OB valide (inverse exact du Bullish).

        Conditions :
        1. Bougie haussière OU longue mèche haute (> 60% du range)
        2. Mouvement impulsif baissier suivant ≥ 1.2× ATR
        3. FVG optionnel

        Returns:
            ZoneOB si valide, None sinon.
        """
        range_total = bougie["high"] - bougie["low"]
        if range_total <= 0:
            return None

        # Condition 1 : bougie haussière OU longue mèche haute
        est_haussiere = bougie["close"] > bougie["open"]
        meche_haute = bougie["high"] - max(bougie["open"], bougie["close"])
        longue_meche_haute = (meche_haute / range_total) > 0.6 if range_total > 0 else False

        if not (est_haussiere or longue_meche_haute):
            return None

        # Condition 2 : impulsion baissière suivante
        impulsion1 = suivante1["open"] - suivante1["close"]
        impulsion_valide = impulsion1 >= 1.2 * atr

        if not impulsion_valide and suivante2 is not None:
            impulsion_combinee = bougie["close"] - suivante2["close"]
            impulsion_valide = impulsion_combinee >= 1.5 * atr

        if not impulsion_valide:
            return None

        # Condition 3 : FVG baissier (optionnel)
        seuil_fvg = 0.2 if label_tf in ("H4", "H1") else 0.1
        if suivante2 is not None:
            fvg_taille = bougie["low"] - suivante2["open"]
        else:
            fvg_taille = bougie["low"] - suivante1["high"]

        prix_ref = bougie["close"] if bougie["close"] > 0 else 1.0
        fvg_pct = (fvg_taille / prix_ref) * 100
        fvg_present = fvg_pct >= seuil_fvg

        # Zone = du min(open, close) au high de la bougie OB
        zone_bas = min(bougie["open"], bougie["close"])
        zone_haut = bougie["high"]
        taille_impulsion = bougie["high"] - suivante1["low"]

        return ZoneOB(
            timeframe=label_tf,
            type_ob=TypeOB.BAISSIER,
            zone_haut=zone_haut,
            zone_bas=zone_bas,
            zone_milieu=(zone_haut + zone_bas) / 2,
            forme_a=datetime.utcnow(),
            bougie_open=float(bougie["open"]),
            bougie_haut=float(bougie["high"]),
            bougie_bas=float(bougie["low"]),
            bougie_close=float(bougie["close"]),
            taille_impulsion=float(taille_impulsion),
            fvg_present=fvg_present,
            fvg_taille_pct=float(max(0.0, fvg_pct)),
        )

    # ── Alignement multi-timeframes ───────────────────────────────────────

    def _aligner_multi_timeframe(
        self,
        obs_h4: List[ZoneOB],
        obs_h1: List[ZoneOB],
        obs_m15: List[ZoneOB],
    ) -> List[OBMultiTimeframe]:
        """
        Aligne les OB des 3 TF pour trouver les zones de confluence.
        H4 est le TF directeur — sans H4, l'OB est rejeté.
        Deux zones s'alignent si leur overlap >= 30% de la plus petite zone.

        Args:
            obs_h4: OB détectés sur H4 (obligatoires).
            obs_h1: OB détectés sur H1.
            obs_m15: OB détectés sur M15.

        Returns:
            Liste d'OBMultiTimeframe.
        """
        obs_mtf = []

        for ob_h4 in obs_h4:
            mtf = OBMultiTimeframe(
                type_ob=ob_h4.type_ob,
                symbole=CONFIG.SYMBOLE,
                zone_h4=ob_h4,
            )

            # Chercher un OB H1 aligné avec H4 (overlap >= 30%)
            meilleur_h1 = self._trouver_meilleur_aligne(ob_h4, obs_h1, overlap_min_pct=30.0)
            if meilleur_h1:
                mtf.zone_h1 = meilleur_h1

                # Chercher un OB M15 aligné avec H1 (overlap >= 40%)
                meilleur_m15 = self._trouver_meilleur_aligne(meilleur_h1, obs_m15, overlap_min_pct=40.0)
                if meilleur_m15:
                    mtf.zone_m15 = meilleur_m15

            # Zone d'entrée finale = intersection
            haut, bas = self._calculer_zone_entree(mtf)
            mtf.zone_entree_haut = haut
            mtf.zone_entree_bas = bas
            mtf.zone_entree_milieu = (haut + bas) / 2 if haut > bas else 0.0

            # Niveau d'invalidation (10% au-delà de la zone H4)
            marge = (ob_h4.zone_haut - ob_h4.zone_bas) * 0.1
            if ob_h4.type_ob == TypeOB.HAUSSIER:
                mtf.niveau_invalidation = ob_h4.zone_bas - marge
            else:
                mtf.niveau_invalidation = ob_h4.zone_haut + marge

            obs_mtf.append(mtf)

        return obs_mtf

    def _trouver_meilleur_aligne(
        self,
        reference: ZoneOB,
        candidats: List[ZoneOB],
        overlap_min_pct: float,
    ) -> Optional[ZoneOB]:
        """
        Trouve le meilleur OB aligné avec la zone de référence.
        Critères : même type + overlap >= overlap_min_pct.

        Args:
            reference: Zone de référence.
            candidats: Liste de zones candidates.
            overlap_min_pct: Overlap minimum en % de la plus petite zone.

        Returns:
            Meilleure zone alignée ou None.
        """
        meilleur = None
        meilleur_overlap = 0.0

        for candidat in candidats:
            # Même direction obligatoire
            if candidat.type_ob != reference.type_ob:
                continue

            # Calcul du chevauchement
            overlap_haut = min(reference.zone_haut, candidat.zone_haut)
            overlap_bas = max(reference.zone_bas, candidat.zone_bas)

            if overlap_haut <= overlap_bas:
                continue  # Pas de chevauchement

            taille_overlap = overlap_haut - overlap_bas
            plus_petite_zone = min(
                reference.zone_haut - reference.zone_bas,
                candidat.zone_haut - candidat.zone_bas,
            )
            overlap_pct = (taille_overlap / plus_petite_zone * 100) if plus_petite_zone > 0 else 0.0

            if overlap_pct >= overlap_min_pct and overlap_pct > meilleur_overlap:
                meilleur_overlap = overlap_pct
                meilleur = candidat

        return meilleur

    def _calculer_zone_entree(
        self,
        mtf: OBMultiTimeframe,
    ) -> Tuple[float, float]:
        """
        Calcule la zone d'entrée finale comme INTERSECTION des zones disponibles.
        Plus conservative et plus précise qu'une union.
        Si l'intersection est invalide → fallback sur la zone H4.

        Args:
            mtf: OBMultiTimeframe avec les zones renseignées.

        Returns:
            Tuple (zone_haut, zone_bas).
        """
        zones = [z for z in [mtf.zone_h4, mtf.zone_h1, mtf.zone_m15] if z is not None]
        if not zones:
            return 0.0, 0.0

        # Intersection = max des bas, min des hauts
        entree_bas = max(z.zone_bas for z in zones)
        entree_haut = min(z.zone_haut for z in zones)

        # Si intersection invalide → fallback H4
        if entree_haut <= entree_bas:
            if mtf.zone_h4:
                return mtf.zone_h4.zone_haut, mtf.zone_h4.zone_bas
            return 0.0, 0.0

        return entree_haut, entree_bas

    # ── Mise à jour des statuts ────────────────────────────────────────────

    def _mettre_a_jour_statuts(
        self,
        obs: List[OBMultiTimeframe],
        df_m15: pd.DataFrame,
    ) -> None:
        """
        Met à jour le statut des OB selon le prix actuel M15.
        Utilise la bougie fermée (iloc[-2]).

        Args:
            obs: Liste des OB à mettre à jour.
            df_m15: DataFrame M15.
        """
        if len(df_m15) < 2:
            return

        # Utiliser la DERNIÈRE BOUGIE FERMÉE (iloc[-2])
        dernier_close = float(df_m15["close"].iloc[-2])
        prix_haut = float(df_m15["high"].iloc[-2])
        prix_bas = float(df_m15["low"].iloc[-2])

        for ob in obs:
            zone_haut = ob.zone_entree_haut
            zone_bas = ob.zone_entree_bas

            # Vérifier si le prix est entré dans la zone
            prix_dans_zone = zone_bas <= dernier_close <= zone_haut or \
                             zone_bas <= prix_haut <= zone_haut or \
                             zone_bas <= prix_bas <= zone_haut

            if prix_dans_zone and ob.statut == StatutOB.ACTIF:
                ob.nb_touches += 1
                ob.statut = StatutOB.TESTE
                if ob.zone_h4:
                    ob.zone_h4.nb_touches += 1
                logger.info(
                    f"OB {ob.type_ob.value} touché "
                    f"(zone {zone_bas:.2f}–{zone_haut:.2f}) "
                    f"— touch #{ob.nb_touches}"
                )

            # Épuiser si >= 2 touches
            if ob.nb_touches >= 2:
                ob.statut = StatutOB.EPUISE
                continue

            # Invalider si prix clôture au-delà du niveau d'invalidation
            if ob.type_ob == TypeOB.HAUSSIER and dernier_close < ob.niveau_invalidation:
                ob.statut = StatutOB.INVALIDE
                logger.warning(
                    f"OB HAUSSIER invalidé — close {dernier_close:.2f} "
                    f"< invalidation {ob.niveau_invalidation:.2f}"
                )
            elif ob.type_ob == TypeOB.BAISSIER and dernier_close > ob.niveau_invalidation:
                ob.statut = StatutOB.INVALIDE
                logger.warning(
                    f"OB BAISSIER invalidé — close {dernier_close:.2f} "
                    f"> invalidation {ob.niveau_invalidation:.2f}"
                )

    # ── Helpers ───────────────────────────────────────────────────────────

    def _ajouter_atr(self, df: pd.DataFrame, periode: int = 14) -> pd.DataFrame:
        """
        Ajoute la colonne ATR au DataFrame.

        Args:
            df: DataFrame OHLCV.
            periode: Période ATR.

        Returns:
            DataFrame avec colonne atr_14.
        """
        if df is None or len(df) < periode + 1:
            return df

        df = df.copy()
        atr_serie = Indicateurs.atr(df, periode)
        df[f"atr_{periode}"] = atr_serie
        return df

    def trouver_zone_la_plus_proche(
        self,
        zones: List[OBMultiTimeframe],
        prix_actuel: float,
        direction: str,
    ) -> Optional[OBMultiTimeframe]:
        """
        Interface de compatibilité — trouve la zone la plus proche du prix.

        Args:
            zones: Liste d'OBMultiTimeframe.
            prix_actuel: Prix courant.
            direction: "BULL" ou "BEAR".

        Returns:
            Zone la plus proche ou None.
        """
        candidats = []
        for zone in zones:
            est_haussier = zone.type_ob == TypeOB.HAUSSIER
            if direction == "BULL" and est_haussier:
                if prix_actuel >= zone.zone_entree_bas:
                    distance = abs(prix_actuel - zone.zone_entree_milieu)
                    candidats.append((distance, zone))
            elif direction == "BEAR" and not est_haussier:
                if prix_actuel <= zone.zone_entree_haut:
                    distance = abs(prix_actuel - zone.zone_entree_milieu)
                    candidats.append((distance, zone))

        if not candidats:
            return None
        candidats.sort(key=lambda x: x[0])
        return candidats[0][1]

    def _detecter_tous_ob_bruts(self, df: pd.DataFrame) -> list:
        """
        Méthode de compatibilité — détecte tous les OB bruts sur H4 (sans scoring).
        Utilisée par les tests unitaires de test_structure.py.

        Args:
            df: DataFrame H4.

        Returns:
            Liste de ZoneOB brutes.
        """
        df_atr = self._ajouter_atr(df)
        return self._detecter_sur_timeframe(df_atr, "H4")

    @staticmethod
    def _score_vers_force(score: int) -> ForceOB:
        """Convertit un score numérique en catégorie de force."""
        if score >= 80:
            return ForceOB.INSTITUTIONNEL
        elif score >= 60:
            return ForceOB.FORT
        elif score >= 40:
            return ForceOB.MODERE
        return ForceOB.FAIBLE
