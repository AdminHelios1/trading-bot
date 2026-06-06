"""
ob_scorer.py — Système de scoring multi-critères pour les Order Blocks.
Score de 0 à 100 basé sur l'alignement TF, la qualité du FVG,
la force de l'impulsion et les confluences supplémentaires.
"""

from typing import List, Optional, Dict
import pandas as pd
from loguru import logger


# Import différé pour éviter la circularité
def _get_ob_types():
    from ob_detector import TypeOB, OBMultiTimeframe
    return TypeOB, OBMultiTimeframe


# ── Barème de scoring ──────────────────────────────────────────────────────

GRILLE_SCORING = {
    # Alignement timeframes (40 pts max — critère principal)
    "h4_present":           20,   # OB détecté sur H4 (obligatoire)
    "h1_aligne":            15,   # OB H1 aligné avec H4
    "m15_aligne":            5,   # OB M15 aligné avec H1

    # Qualité du FVG (25 pts max)
    "fvg_h4_present":       12,   # FVG sur H4
    "fvg_h1_present":        8,   # FVG sur H1
    "fvg_m15_present":       5,   # FVG sur M15

    # Force de l'impulsion (20 pts max)
    "impulsion_h4_forte":   10,   # Mouvement H4 ≥ 2× ATR
    "impulsion_h1_forte":    6,   # Mouvement H1 ≥ 2× ATR
    "impulsion_m15_forte":   4,   # Mouvement M15 ≥ 1.5× ATR

    # Confluences supplémentaires (15 pts max)
    "confluence_swing":      6,   # Zone aligne avec swing H/L sur H4
    "confluence_fibonacci":  5,   # Zone dans le 61.8%–78.6% (OTE)
    "confluence_ancien_ob":  4,   # Zone aligne avec ancien OB retesté

    # Pénalités (à soustraire)
    "ob_deja_teste":        -8,   # touch_count == 1 → moins fraîche
    "ob_sans_fvg":          -5,   # Aucun FVG sur aucun TF
    "impulsion_sous_atr":  -10,   # Impulsion < 1× ATR → pas institutionnel
}


class ScorerOB:
    """Calcule le score et détecte les confluences d'un OBMultiTimeframe."""

    def calculer_score(
        self,
        mtf_ob,  # OBMultiTimeframe
        df_h4: pd.DataFrame,
        df_h1: Optional[pd.DataFrame],
        df_m15: Optional[pd.DataFrame],
    ) -> int:
        """
        Calcule le score global d'un OB multi-TF selon la grille de scoring.

        Args:
            mtf_ob: OBMultiTimeframe à scorer.
            df_h4: DataFrame H4 avec ATR.
            df_h1: DataFrame H1 avec ATR (optionnel).
            df_m15: DataFrame M15 avec ATR (optionnel).

        Returns:
            Score entre 0 et 100 (clampé).
        """
        score = 0

        # ── Alignement timeframes ──────────────────────────────────────────
        # H4 est toujours présent (obligatoire pour arriver ici)
        score += GRILLE_SCORING["h4_present"]

        if mtf_ob.zone_h1 is not None:
            score += GRILLE_SCORING["h1_aligne"]

        if mtf_ob.zone_m15 is not None:
            score += GRILLE_SCORING["m15_aligne"]

        # ── Qualité FVG ────────────────────────────────────────────────────
        if mtf_ob.zone_h4 and mtf_ob.zone_h4.fvg_present:
            score += GRILLE_SCORING["fvg_h4_present"]

        if mtf_ob.zone_h1 and mtf_ob.zone_h1.fvg_present:
            score += GRILLE_SCORING["fvg_h1_present"]

        if mtf_ob.zone_m15 and mtf_ob.zone_m15.fvg_present:
            score += GRILLE_SCORING["fvg_m15_present"]

        # ── Force de l'impulsion ───────────────────────────────────────────
        atr_h4 = self._get_atr_actuel(df_h4)
        atr_h1 = self._get_atr_actuel(df_h1) if df_h1 is not None else 1.0
        atr_m15 = self._get_atr_actuel(df_m15) if df_m15 is not None else 1.0

        if mtf_ob.zone_h4 and atr_h4 > 0:
            if mtf_ob.zone_h4.taille_impulsion >= 2.0 * atr_h4:
                score += GRILLE_SCORING["impulsion_h4_forte"]

        if mtf_ob.zone_h1 and atr_h1 > 0:
            if mtf_ob.zone_h1.taille_impulsion >= 2.0 * atr_h1:
                score += GRILLE_SCORING["impulsion_h1_forte"]

        if mtf_ob.zone_m15 and atr_m15 > 0:
            if mtf_ob.zone_m15.taille_impulsion >= 1.5 * atr_m15:
                score += GRILLE_SCORING["impulsion_m15_forte"]

        # ── Pénalités ──────────────────────────────────────────────────────
        # Zone déjà touchée
        if mtf_ob.nb_touches >= 1:
            score += GRILLE_SCORING["ob_deja_teste"]

        # Aucun FVG sur aucun TF
        a_fvg = any([
            mtf_ob.zone_h4 and mtf_ob.zone_h4.fvg_present,
            mtf_ob.zone_h1 and mtf_ob.zone_h1.fvg_present,
            mtf_ob.zone_m15 and mtf_ob.zone_m15.fvg_present,
        ])
        if not a_fvg:
            score += GRILLE_SCORING["ob_sans_fvg"]

        # Impulsion faible sur H4
        if mtf_ob.zone_h4 and atr_h4 > 0:
            if mtf_ob.zone_h4.taille_impulsion < atr_h4:
                score += GRILLE_SCORING["impulsion_sous_atr"]

        # Clamp entre 0 et 100
        score_final = max(0, min(100, score))
        logger.debug(
            f"Score OB {mtf_ob.type_ob.value} : {score_final}/100 "
            f"(H4:{mtf_ob.zone_h4 is not None} "
            f"H1:{mtf_ob.zone_h1 is not None} "
            f"M15:{mtf_ob.zone_m15 is not None})"
        )
        return score_final

    def detecter_confluences(
        self,
        mtf_ob,  # OBMultiTimeframe
        df_h4: pd.DataFrame,
    ) -> List[str]:
        """
        Détecte les confluences supplémentaires et retourne des labels texte.
        Ces labels s'affichent dans le dashboard et le journal.

        Args:
            mtf_ob: OBMultiTimeframe à analyser.
            df_h4: DataFrame H4 pour les calculs structurels.

        Returns:
            Liste de labels de confluence.
        """
        confluences = []

        try:
            # ── Nombre de TF alignés ──────────────────────────────────────
            nb_tf = mtf_ob.nb_timeframes
            confluences.append(f"{nb_tf}/3 TF alignés")

            # ── FVG présents ──────────────────────────────────────────────
            if mtf_ob.zone_h4 and mtf_ob.zone_h4.fvg_present:
                confluences.append("FVG H4")
            if mtf_ob.zone_h1 and mtf_ob.zone_h1.fvg_present:
                confluences.append("FVG H1")
            if mtf_ob.zone_m15 and mtf_ob.zone_m15.fvg_present:
                confluences.append("FVG M15")

            # ── Confluence Fibonacci OTE (61.8%–78.6%) ───────────────────
            niveaux_fib = self._calculer_fibonacci(df_h4)
            if niveaux_fib and mtf_ob.zone_entree_milieu > 0:
                ote_bas = niveaux_fib.get("61.8", 0)
                ote_haut = niveaux_fib.get("78.6", 0)
                milieu = mtf_ob.zone_entree_milieu
                if ote_bas > 0 and ote_haut > 0:
                    if min(ote_bas, ote_haut) <= milieu <= max(ote_bas, ote_haut):
                        confluences.append("OTE Fibonacci 61.8%–78.6%")

            # ── Confluence swing level H4 ─────────────────────────────────
            from ob_detector import TypeOB
            dernier_swing = self._get_dernier_swing(df_h4, mtf_ob.type_ob)
            if dernier_swing and mtf_ob.zone_entree_milieu > 0:
                range_zone = mtf_ob.zone_entree_haut - mtf_ob.zone_entree_bas
                if range_zone > 0 and abs(dernier_swing - mtf_ob.zone_entree_milieu) <= range_zone:
                    label = "Swing Low H4" if mtf_ob.type_ob == TypeOB.HAUSSIER else "Swing High H4"
                    confluences.append(label)

        except Exception as e:
            logger.debug(f"Erreur détection confluences : {e}")

        return confluences

    # ── Méthodes privées ──────────────────────────────────────────────────

    def _calculer_fibonacci(self, df_h4: pd.DataFrame) -> Optional[Dict[str, float]]:
        """
        Calcule les niveaux Fibonacci du dernier leg significatif sur H4.
        OTE = Optimal Trade Entry (concept ICT/SMC) : 61.8% – 78.6%.

        Args:
            df_h4: DataFrame H4.

        Returns:
            Dict {niveau: prix} ou None si données insuffisantes.
        """
        if df_h4 is None or len(df_h4) < 10:
            return None

        recent = df_h4.iloc[-50:]
        swing_haut = float(recent["high"].max())
        swing_bas = float(recent["low"].min())
        leg = swing_haut - swing_bas

        if leg <= 0:
            return None

        return {
            "23.6": swing_haut - leg * 0.236,
            "38.2": swing_haut - leg * 0.382,
            "50.0": swing_haut - leg * 0.500,
            "61.8": swing_haut - leg * 0.618,
            "78.6": swing_haut - leg * 0.786,
        }

    def _get_dernier_swing(
        self,
        df_h4: pd.DataFrame,
        type_ob,
    ) -> Optional[float]:
        """
        Retourne le dernier swing Low (pour OB haussier) ou swing High (pour OB baissier).

        Args:
            df_h4: DataFrame H4.
            type_ob: TypeOB enum.

        Returns:
            Niveau du swing ou None.
        """
        if df_h4 is None or len(df_h4) < 5:
            return None

        from ob_detector import TypeOB
        recent = df_h4.iloc[-30:]

        if type_ob == TypeOB.HAUSSIER:
            return float(recent["low"].min())
        else:
            return float(recent["high"].max())

    @staticmethod
    def _get_atr_actuel(df: Optional[pd.DataFrame]) -> float:
        """
        Retourne la valeur ATR actuelle depuis un DataFrame avec colonne ATR.

        Args:
            df: DataFrame avec colonne atr_*.

        Returns:
            Valeur ATR ou 1.0 si indisponible.
        """
        if df is None or len(df) == 0:
            return 1.0

        cols_atr = [c for c in df.columns if c.startswith("atr_")]
        if not cols_atr:
            return 1.0

        valeur = df[cols_atr[0]].iloc[-1]
        return float(valeur) if not pd.isna(valeur) and valeur > 0 else 1.0
