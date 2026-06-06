"""
displacement_checker.py — Validation des bougies institutionnelles (Displacement).
Un Displacement = bougie avec une intention directionnelle forte, sans ambiguïté,
reflétant un ordre institutionnel massif sur XAUUSD H4.
"""

from dataclasses import dataclass
from typing import Optional
import pandas as pd
from loguru import logger

from config import CONFIG


@dataclass
class ResultatDisplacement:
    """Résultat complet de l'analyse d'une bougie pour qualification Displacement."""
    est_displacement: bool
    index_bougie: object             # Index datetime de la bougie
    taille_corps: float              # Taille du corps en $
    ratio_corps_atr: float           # Corps / ATR(14)
    pct_corps_range: float           # % du corps sur le range total
    pct_cloture_range: float         # % de la clôture (0=bas, 100=haut)
    pct_meche_rejet: float           # % de la mèche de rejet dans la direction du BOS
    raison: str                      # Explication lisible


class ValidateurDisplacement:
    """
    Valide si une bougie (ou une séquence) est un Displacement institutionnel.
    Tous les seuils proviennent de CONFIG — aucun magic number dans le code.
    """

    # ── Validation bougie unique ───────────────────────────────────────────

    def verifier(
        self,
        bougie: pd.Series,
        atr: float,
        direction: str,
    ) -> ResultatDisplacement:
        """
        Vérifie si une bougie est un Displacement valide.

        Args:
            bougie: Series OHLCV avec index datetime.
            atr: ATR(14) courant sur ce timeframe.
            direction: "bullish" ou "bearish" (direction du BOS causé).

        Returns:
            ResultatDisplacement avec est_displacement et raison détaillée.
        """
        range_total = bougie["high"] - bougie["low"]
        taille_corps = abs(bougie["close"] - bougie["open"])
        index_bougie = bougie.name if hasattr(bougie, "name") else 0

        # ── Cas dégénérés ──────────────────────────────────────────────────
        if range_total <= 0 or atr <= 0:
            return ResultatDisplacement(
                est_displacement=False,
                index_bougie=index_bougie,
                taille_corps=0.0,
                ratio_corps_atr=0.0,
                pct_corps_range=0.0,
                pct_cloture_range=50.0,
                pct_meche_rejet=0.0,
                raison="Bougie Doji ou ATR nul — pas un Displacement",
            )

        ratio_corps_atr = taille_corps / atr
        pct_corps_range = (taille_corps / range_total) * 100.0
        pct_cloture_range = ((bougie["close"] - bougie["low"]) / range_total) * 100.0

        # Mèche de rejet dans la direction du BOS
        if direction == "bullish":
            # BOS haussier : mèche haute = rejet vers le bas depuis le haut
            meche_rejet = bougie["high"] - bougie["close"]
        else:
            # BOS baissier : mèche basse = rejet vers le haut depuis le bas
            meche_rejet = bougie["close"] - bougie["low"]
        pct_meche_rejet = (meche_rejet / range_total) * 100.0

        # ── Critère 1 : Corps ≥ 1.5× ATR ──────────────────────────────────
        if ratio_corps_atr < CONFIG.DISPLACEMENT_MIN_BODY_ATR_RATIO:
            return ResultatDisplacement(
                est_displacement=False,
                index_bougie=index_bougie,
                taille_corps=taille_corps,
                ratio_corps_atr=ratio_corps_atr,
                pct_corps_range=pct_corps_range,
                pct_cloture_range=pct_cloture_range,
                pct_meche_rejet=pct_meche_rejet,
                raison=(
                    f"Corps trop petit : {ratio_corps_atr:.2f}× ATR "
                    f"(minimum : {CONFIG.DISPLACEMENT_MIN_BODY_ATR_RATIO}× ATR)"
                ),
            )

        # ── Critère 2 : Corps ≥ 60% du range total ─────────────────────────
        if pct_corps_range < CONFIG.DISPLACEMENT_MIN_BODY_RANGE_PCT:
            return ResultatDisplacement(
                est_displacement=False,
                index_bougie=index_bougie,
                taille_corps=taille_corps,
                ratio_corps_atr=ratio_corps_atr,
                pct_corps_range=pct_corps_range,
                pct_cloture_range=pct_cloture_range,
                pct_meche_rejet=pct_meche_rejet,
                raison=(
                    f"Corps insuffisant : {pct_corps_range:.1f}% du range "
                    f"(minimum : {CONFIG.DISPLACEMENT_MIN_BODY_RANGE_PCT}%)"
                ),
            )

        # ── Critère 3 : Clôture dans le bon tiers ──────────────────────────
        if direction == "bullish":
            if pct_cloture_range < CONFIG.DISPLACEMENT_CLOSE_THRESHOLD_BULLISH:
                return ResultatDisplacement(
                    est_displacement=False,
                    index_bougie=index_bougie,
                    taille_corps=taille_corps,
                    ratio_corps_atr=ratio_corps_atr,
                    pct_corps_range=pct_corps_range,
                    pct_cloture_range=pct_cloture_range,
                    pct_meche_rejet=pct_meche_rejet,
                    raison=(
                        f"Clôture trop basse dans le range : {pct_cloture_range:.1f}% "
                        f"(minimum : {CONFIG.DISPLACEMENT_CLOSE_THRESHOLD_BULLISH}% "
                        f"pour BOS haussier)"
                    ),
                )
        else:
            if pct_cloture_range > CONFIG.DISPLACEMENT_CLOSE_THRESHOLD_BEARISH:
                return ResultatDisplacement(
                    est_displacement=False,
                    index_bougie=index_bougie,
                    taille_corps=taille_corps,
                    ratio_corps_atr=ratio_corps_atr,
                    pct_corps_range=pct_corps_range,
                    pct_cloture_range=pct_cloture_range,
                    pct_meche_rejet=pct_meche_rejet,
                    raison=(
                        f"Clôture trop haute dans le range : {pct_cloture_range:.1f}% "
                        f"(maximum : {CONFIG.DISPLACEMENT_CLOSE_THRESHOLD_BEARISH}% "
                        f"pour BOS baissier)"
                    ),
                )

        # ── Critère 4 : Mèche de rejet ≤ 20% du range ─────────────────────
        if pct_meche_rejet > CONFIG.DISPLACEMENT_MAX_REJECTION_WICK_PCT:
            return ResultatDisplacement(
                est_displacement=False,
                index_bougie=index_bougie,
                taille_corps=taille_corps,
                ratio_corps_atr=ratio_corps_atr,
                pct_corps_range=pct_corps_range,
                pct_cloture_range=pct_cloture_range,
                pct_meche_rejet=pct_meche_rejet,
                raison=(
                    f"Mèche de rejet trop longue : {pct_meche_rejet:.1f}% du range "
                    f"(maximum : {CONFIG.DISPLACEMENT_MAX_REJECTION_WICK_PCT}%) "
                    f"— indique absorption / résistance"
                ),
            )

        # ── Tous les critères validés ───────────────────────────────────────
        return ResultatDisplacement(
            est_displacement=True,
            index_bougie=index_bougie,
            taille_corps=taille_corps,
            ratio_corps_atr=ratio_corps_atr,
            pct_corps_range=pct_corps_range,
            pct_cloture_range=pct_cloture_range,
            pct_meche_rejet=pct_meche_rejet,
            raison=(
                f"Displacement validé — Corps: {ratio_corps_atr:.2f}× ATR "
                f"| {pct_corps_range:.1f}% du range "
                f"| Clôture: {pct_cloture_range:.1f}% "
                f"| Mèche rejet: {pct_meche_rejet:.1f}%"
            ),
        )

    # ── Validation séquence (fallback) ────────────────────────────────────

    def verifier_sequence(
        self,
        df: pd.DataFrame,
        index_debut: int,
        direction: str,
        atr: float,
        fenetre: int = 3,
    ) -> ResultatDisplacement:
        """
        Fallback : vérifie si une SÉQUENCE de bougies forme un Displacement cumulatif.
        Activé uniquement quand la bougie seule échoue au critère 1 (corps < 1.5× ATR).

        Règle séquence :
        - Mouvement net ≥ 1.2× ATR
        - ≥ 60% des bougies dans la même direction que le BOS

        Args:
            df: DataFrame OHLCV.
            index_debut: Index de début dans le DataFrame.
            direction: "bullish" ou "bearish".
            atr: ATR(14) du timeframe.
            fenetre: Nombre de bougies à analyser (défaut: 3).

        Returns:
            ResultatDisplacement avec est_displacement et détails.
        """
        bougies = df.iloc[index_debut:index_debut + fenetre]
        index_bougie = index_debut

        if len(bougies) < 2 or atr <= 0:
            return ResultatDisplacement(
                est_displacement=False,
                index_bougie=index_bougie,
                taille_corps=0.0,
                ratio_corps_atr=0.0,
                pct_corps_range=0.0,
                pct_cloture_range=50.0,
                pct_meche_rejet=0.0,
                raison="Pas assez de bougies pour analyse en séquence",
            )

        # Mouvement net de la séquence
        mouvement_net = abs(
            bougies["close"].iloc[-1] - bougies["open"].iloc[0]
        )
        ratio_net_atr = mouvement_net / atr

        # Bougies alignées avec la direction du BOS
        if direction == "bullish":
            bougies_alignees = bougies[bougies["close"] > bougies["open"]]
        else:
            bougies_alignees = bougies[bougies["close"] < bougies["open"]]

        ratio_alignement = len(bougies_alignees) / len(bougies)
        est_valide = (ratio_net_atr >= 1.2 and ratio_alignement >= 0.6)

        return ResultatDisplacement(
            est_displacement=est_valide,
            index_bougie=index_bougie,
            taille_corps=mouvement_net,
            ratio_corps_atr=ratio_net_atr,
            pct_corps_range=ratio_alignement * 100.0,
            pct_cloture_range=50.0,
            pct_meche_rejet=0.0,
            raison=(
                f"Séquence {fenetre} bougies — Mouvement net: {ratio_net_atr:.2f}× ATR "
                f"| Alignement: {ratio_alignement * 100:.0f}% "
                f"→ {'Validé' if est_valide else 'Rejeté'}"
            ),
        )
