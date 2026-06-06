"""
ob_detector.py — Détection des Order Blocks et Breaker Blocks (SMC).
Un OB est la dernière bougie opposée avant un mouvement impulsif avec FVG.
Un Breaker Block est un OB invalidé qui devient une zone de retournement.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional, Tuple
import pandas as pd
import numpy as np
from loguru import logger

from config import CONFIG


class TypeZone(Enum):
    """Nature de la zone institutionnelle."""
    ORDER_BLOCK_HAUSSIER = "OB_BULL"
    ORDER_BLOCK_BAISSIER = "OB_BEAR"
    BREAKER_BLOCK_HAUSSIER = "BB_BULL"  # Ancien OB baissier revalidé
    BREAKER_BLOCK_BAISSIER = "BB_BEAR"  # Ancien OB haussier revalidé


@dataclass
class ZoneInstitutionnelle:
    """Un Order Block ou Breaker Block identifié."""
    type: TypeZone
    prix_bas: float
    prix_haut: float
    timestamp: pd.Timestamp
    index_bougie: int
    nb_touches: int = 0
    est_actif: bool = True
    # Bougie qui a créé le mouvement impulsif après l'OB
    index_impulsion: int = 0
    # Force du FVG associé (en %)
    force_fvg_pct: float = 0.0

    @property
    def milieu(self) -> float:
        """Centre de la zone."""
        return (self.prix_bas + self.prix_haut) / 2

    @property
    def est_haussier(self) -> bool:
        return self.type in (TypeZone.ORDER_BLOCK_HAUSSIER, TypeZone.BREAKER_BLOCK_HAUSSIER)

    def contient(self, prix: float) -> bool:
        """Vérifie si le prix est à l'intérieur de la zone."""
        return self.prix_bas <= prix <= self.prix_haut


class DetecteurOB:
    """Détecte et gère les Order Blocks et Breaker Blocks sur les données H4."""

    def __init__(self) -> None:
        self.lookback: int = CONFIG.OB_LOOKBACK_BOUGIES
        self.min_imbalance_pct: float = CONFIG.OB_MIN_IMBALANCE_PCT
        self.max_touches: int = CONFIG.OB_MAX_TOUCHES
        self.bb_lookback: int = CONFIG.BB_LOOKBACK_BOUGIES

    # ── Détection FVG ─────────────────────────────────────────────────────

    def _calculer_fvg(
        self,
        df: pd.DataFrame,
        index_impulsion: int,
        direction: str,
    ) -> float:
        """
        Calcule le Fair Value Gap (FVG) associé à un mouvement impulsif.
        FVG haussier : espace entre le High de bougie[i-1] et le Low de bougie[i+1].
        FVG baissier : espace entre le Low de bougie[i-1] et le High de bougie[i+1].

        Args:
            df: DataFrame OHLCV.
            index_impulsion: Index de la bougie impulsive.
            direction: "BULL" ou "BEAR".

        Returns:
            FVG en pourcentage du prix.
        """
        if index_impulsion <= 0 or index_impulsion >= len(df) - 1:
            return 0.0

        prix_ref = df["close"].iloc[index_impulsion]
        if prix_ref <= 0:
            return 0.0

        if direction == "BULL":
            # Gap entre high[i-1] et low[i+1]
            fvg = df["low"].iloc[index_impulsion + 1] - df["high"].iloc[index_impulsion - 1]
        else:
            # Gap entre low[i-1] et high[i+1]
            fvg = df["low"].iloc[index_impulsion - 1] - df["high"].iloc[index_impulsion + 1]

        fvg_pct = abs(fvg) / prix_ref * 100
        return max(0.0, fvg_pct)

    # ── Détection mouvement impulsif ───────────────────────────────────────

    def _est_impulsif(
        self,
        df: pd.DataFrame,
        index: int,
        direction: str,
        seuil_corps_pct: float = 0.5,
    ) -> bool:
        """
        Vérifie si une bougie est impulsive (corps > 50% de la range totale).

        Args:
            df: DataFrame OHLCV.
            index: Index de la bougie.
            direction: "BULL" pour haussier, "BEAR" pour baissier.
            seuil_corps_pct: Part minimale du corps sur la range totale.

        Returns:
            True si la bougie est impulsive.
        """
        bougie = df.iloc[index]
        range_totale = bougie["high"] - bougie["low"]
        if range_totale <= 0:
            return False

        corps = abs(bougie["close"] - bougie["open"])
        ratio_corps = corps / range_totale

        if ratio_corps < seuil_corps_pct:
            return False

        if direction == "BULL":
            return bougie["close"] > bougie["open"]
        return bougie["close"] < bougie["open"]

    # ── Détection Order Blocks ─────────────────────────────────────────────

    def detecter_order_blocks(self, df: pd.DataFrame) -> List[ZoneInstitutionnelle]:
        """
        Détecte tous les Order Blocks valides dans la fenêtre lookback.

        Algorithme :
        1. Identifier les mouvements impulsifs (bougies avec FVG ≥ seuil)
        2. Pour chaque impulsion haussière → OB = dernière bougie baissière avant
        3. Pour chaque impulsion baissière → OB = dernière bougie haussière avant
        4. Vérifier que la zone est encore "fraîche" (non traversée)

        Args:
            df: DataFrame OHLCV H4.

        Returns:
            Liste des OB actifs dans l'ordre chronologique.
        """
        zones: List[ZoneInstitutionnelle] = []
        debut = max(2, len(df) - self.lookback)

        for i in range(debut, len(df) - 1):
            # ── OB Haussier ────────────────────────────────────────────────
            if self._est_impulsif(df, i, "BULL"):
                fvg_pct = self._calculer_fvg(df, i, "BULL")
                if fvg_pct >= self.min_imbalance_pct:
                    # Chercher la dernière bougie baissière AVANT i
                    ob_index = self._trouver_derniere_bougie_opposee(df, i, "BEAR")
                    if ob_index is not None:
                        bougie_ob = df.iloc[ob_index]
                        zone = ZoneInstitutionnelle(
                            type=TypeZone.ORDER_BLOCK_HAUSSIER,
                            prix_bas=bougie_ob["low"],
                            prix_haut=bougie_ob["high"],
                            timestamp=df.index[ob_index],
                            index_bougie=ob_index,
                            index_impulsion=i,
                            force_fvg_pct=fvg_pct,
                        )
                        zones.append(zone)

            # ── OB Baissier ────────────────────────────────────────────────
            if self._est_impulsif(df, i, "BEAR"):
                fvg_pct = self._calculer_fvg(df, i, "BEAR")
                if fvg_pct >= self.min_imbalance_pct:
                    ob_index = self._trouver_derniere_bougie_opposee(df, i, "BULL")
                    if ob_index is not None:
                        bougie_ob = df.iloc[ob_index]
                        zone = ZoneInstitutionnelle(
                            type=TypeZone.ORDER_BLOCK_BAISSIER,
                            prix_bas=bougie_ob["low"],
                            prix_haut=bougie_ob["high"],
                            timestamp=df.index[ob_index],
                            index_bougie=ob_index,
                            index_impulsion=i,
                            force_fvg_pct=fvg_pct,
                        )
                        zones.append(zone)

        # Filtrer les OB encore actifs et mettre à jour les touches
        zones_actives = self._filtrer_zones_actives(df, zones)

        logger.debug(
            f"OB détectés: {len(zones)} total | "
            f"{len(zones_actives)} actifs | "
            f"Bull: {sum(1 for z in zones_actives if z.est_haussier)} | "
            f"Bear: {sum(1 for z in zones_actives if not z.est_haussier)}"
        )
        return zones_actives

    def _trouver_derniere_bougie_opposee(
        self,
        df: pd.DataFrame,
        index_impulsion: int,
        direction_opposee: str,
        lookback_local: int = 10,
    ) -> Optional[int]:
        """
        Trouve la dernière bougie dans la direction opposée avant l'impulsion.

        Args:
            df: DataFrame OHLCV.
            index_impulsion: Index de la bougie impulsive.
            direction_opposee: "BULL" ou "BEAR".
            lookback_local: Fenêtre de recherche.

        Returns:
            Index de la bougie OB, ou None.
        """
        debut = max(0, index_impulsion - lookback_local)
        for i in range(index_impulsion - 1, debut - 1, -1):
            bougie = df.iloc[i]
            if direction_opposee == "BEAR" and bougie["close"] < bougie["open"]:
                return i
            if direction_opposee == "BULL" and bougie["close"] > bougie["open"]:
                return i
        return None

    def _filtrer_zones_actives(
        self,
        df: pd.DataFrame,
        zones: List[ZoneInstitutionnelle],
    ) -> List[ZoneInstitutionnelle]:
        """
        Filtre les zones encore actives et compte les touches.
        Un OB est invalidé si le prix clôture complètement au-delà.

        Args:
            df: DataFrame OHLCV complet.
            zones: Toutes les zones détectées.

        Returns:
            Zones encore actives avec nb_touches mis à jour.
        """
        closes = df["close"].values
        highs = df["high"].values
        lows = df["low"].values

        zones_actives = []
        for zone in zones:
            # Analyser les bougies APRÈS la création de l'OB
            touches = 0
            invalide = False

            for i in range(zone.index_bougie + 1, len(df)):
                # Compter les touches (prix entre dans la zone)
                if zone.contient(lows[i]) or zone.contient(highs[i]):
                    touches += 1

                # Invalidation : close au-delà de la zone (prix traverse complètement)
                if zone.est_haussier and closes[i] < zone.prix_bas:
                    invalide = True
                    break
                if not zone.est_haussier and closes[i] > zone.prix_haut:
                    invalide = True
                    break

            zone.nb_touches = touches
            zone.est_actif = not invalide and touches <= self.max_touches

            if zone.est_actif:
                zones_actives.append(zone)

        return zones_actives

    # ── Détection Breaker Blocks ───────────────────────────────────────────

    def detecter_breaker_blocks(
        self,
        df: pd.DataFrame,
        order_blocks_invalides: List[ZoneInstitutionnelle],
    ) -> List[ZoneInstitutionnelle]:
        """
        Convertit les OB invalidés en Breaker Blocks si le prix les reteste.
        Un Breaker Block = OB traversé + retour dans la zone par l'autre côté.

        Args:
            df: DataFrame OHLCV H4.
            order_blocks_invalides: OB dont est_actif=False.

        Returns:
            Liste des Breaker Blocks actifs.
        """
        breakers: List[ZoneInstitutionnelle] = []
        closes = df["close"].values

        for ob in order_blocks_invalides:
            # Chercher si après l'invalidation, le prix revient dans la zone
            for i in range(ob.index_bougie + 1, len(df)):
                # Breaker Block haussier : OB baissier invalidé par mouvement haussier
                # → Le prix repasse dans la zone (pullback) → zone de demande
                if (
                    ob.type == TypeZone.ORDER_BLOCK_BAISSIER
                    and closes[i] > ob.prix_haut  # Traverse vers le haut
                ):
                    # Chercher un retour dans la zone
                    for j in range(i + 1, len(df)):
                        if ob.contient(df["low"].iloc[j]) or ob.contient(df["high"].iloc[j]):
                            breaker = ZoneInstitutionnelle(
                                type=TypeZone.BREAKER_BLOCK_HAUSSIER,
                                prix_bas=ob.prix_bas,
                                prix_haut=ob.prix_haut,
                                timestamp=ob.timestamp,
                                index_bougie=ob.index_bougie,
                                index_impulsion=i,
                                force_fvg_pct=ob.force_fvg_pct,
                                nb_touches=1,
                                est_actif=True,
                            )
                            breakers.append(breaker)
                            break
                    break

                # Breaker Block baissier : OB haussier invalidé
                if (
                    ob.type == TypeZone.ORDER_BLOCK_HAUSSIER
                    and closes[i] < ob.prix_bas
                ):
                    for j in range(i + 1, len(df)):
                        if ob.contient(df["low"].iloc[j]) or ob.contient(df["high"].iloc[j]):
                            breaker = ZoneInstitutionnelle(
                                type=TypeZone.BREAKER_BLOCK_BAISSIER,
                                prix_bas=ob.prix_bas,
                                prix_haut=ob.prix_haut,
                                timestamp=ob.timestamp,
                                index_bougie=ob.index_bougie,
                                index_impulsion=i,
                                force_fvg_pct=ob.force_fvg_pct,
                                nb_touches=1,
                                est_actif=True,
                            )
                            breakers.append(breaker)
                            break
                    break

        logger.debug(f"Breaker Blocks détectés: {len(breakers)}")
        return breakers

    def detecter_toutes_zones(
        self,
        df: pd.DataFrame,
    ) -> Tuple[List[ZoneInstitutionnelle], List[ZoneInstitutionnelle]]:
        """
        Détecte TOUS les OB et Breaker Blocks en une passe.

        Args:
            df: DataFrame OHLCV H4.

        Returns:
            Tuple (order_blocks_actifs, breaker_blocks_actifs).
        """
        # Détecter tous les OB (actifs + invalidés pour les BB)
        tous_ob = self._detecter_tous_ob_bruts(df)
        ob_actifs = [z for z in tous_ob if z.est_actif]
        ob_invalides = [z for z in tous_ob if not z.est_actif]

        # Détecter les BB à partir des OB invalidés
        breakers = self.detecter_breaker_blocks(df, ob_invalides)

        return ob_actifs, breakers

    def _detecter_tous_ob_bruts(self, df: pd.DataFrame) -> List[ZoneInstitutionnelle]:
        """Variante interne qui retourne TOUS les OB (actifs ET invalidés)."""
        zones: List[ZoneInstitutionnelle] = []
        debut = max(2, len(df) - self.bb_lookback)

        for i in range(debut, len(df) - 1):
            if self._est_impulsif(df, i, "BULL"):
                fvg_pct = self._calculer_fvg(df, i, "BULL")
                if fvg_pct >= self.min_imbalance_pct:
                    ob_index = self._trouver_derniere_bougie_opposee(df, i, "BEAR")
                    if ob_index is not None:
                        bougie_ob = df.iloc[ob_index]
                        zones.append(ZoneInstitutionnelle(
                            type=TypeZone.ORDER_BLOCK_HAUSSIER,
                            prix_bas=bougie_ob["low"],
                            prix_haut=bougie_ob["high"],
                            timestamp=df.index[ob_index],
                            index_bougie=ob_index,
                            index_impulsion=i,
                            force_fvg_pct=fvg_pct,
                        ))

            if self._est_impulsif(df, i, "BEAR"):
                fvg_pct = self._calculer_fvg(df, i, "BEAR")
                if fvg_pct >= self.min_imbalance_pct:
                    ob_index = self._trouver_derniere_bougie_opposee(df, i, "BULL")
                    if ob_index is not None:
                        bougie_ob = df.iloc[ob_index]
                        zones.append(ZoneInstitutionnelle(
                            type=TypeZone.ORDER_BLOCK_BAISSIER,
                            prix_bas=bougie_ob["low"],
                            prix_haut=bougie_ob["high"],
                            timestamp=df.index[ob_index],
                            index_bougie=ob_index,
                            index_impulsion=i,
                            force_fvg_pct=fvg_pct,
                        ))

        # Marquer actifs vs invalidés
        return self._marquer_invalidations(df, zones)

    def _marquer_invalidations(
        self,
        df: pd.DataFrame,
        zones: List[ZoneInstitutionnelle],
    ) -> List[ZoneInstitutionnelle]:
        """Marque est_actif=False pour les OB dont le prix a clôturé au-delà."""
        closes = df["close"].values
        highs = df["high"].values
        lows = df["low"].values

        for zone in zones:
            touches = 0
            for i in range(zone.index_bougie + 1, len(df)):
                if zone.contient(lows[i]) or zone.contient(highs[i]):
                    touches += 1
                if zone.est_haussier and closes[i] < zone.prix_bas:
                    zone.est_actif = False
                    break
                if not zone.est_haussier and closes[i] > zone.prix_haut:
                    zone.est_actif = False
                    break
            zone.nb_touches = touches
            if zone.est_actif and touches > self.max_touches:
                zone.est_actif = False

        return zones

    def trouver_zone_la_plus_proche(
        self,
        zones: List[ZoneInstitutionnelle],
        prix_actuel: float,
        direction: str,
    ) -> Optional[ZoneInstitutionnelle]:
        """
        Trouve la zone OB/BB la plus proche du prix actuel dans la bonne direction.

        Args:
            zones: Liste des zones actives.
            prix_actuel: Prix courant.
            direction: "BULL" pour chercher des zones de support, "BEAR" pour résistance.

        Returns:
            Zone la plus pertinente, ou None.
        """
        candidates = []
        for zone in zones:
            if direction == "BULL" and zone.est_haussier and prix_actuel >= zone.prix_bas:
                distance = prix_actuel - zone.milieu
                if distance >= 0:
                    candidates.append((distance, zone))
            elif direction == "BEAR" and not zone.est_haussier and prix_actuel <= zone.prix_haut:
                distance = zone.milieu - prix_actuel
                if distance >= 0:
                    candidates.append((distance, zone))

        if not candidates:
            return None

        # Retourner la zone la plus proche (distance minimale)
        candidates.sort(key=lambda x: x[0])
        return candidates[0][1]
