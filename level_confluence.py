"""
level_confluence.py — Calcul de confluence multi-sources pour les niveaux SL/TP.
Regroupe les niveaux de prix de sources multiples et les score selon leur confluence.
"""

from dataclasses import dataclass
from enum import Enum
from typing import List, Tuple, Optional
from loguru import logger

from config import CONFIG


# ── Énumération des sources ────────────────────────────────────────────────

class SourceNiveau(Enum):
    """Source d'un niveau de prix."""
    ORDER_BLOCK_H4    = "OB H4"
    ORDER_BLOCK_H1    = "OB H1"
    ORDER_BLOCK_M15   = "OB M15"
    ASIE_HAUT         = "High asiatique"
    ASIE_BAS          = "Low asiatique"
    ASIE_MILIEU       = "Midpoint asiatique"
    ASIE_EQUAL_HAUT   = "Equal High asiatique"
    ASIE_EQUAL_BAS    = "Equal Low asiatique"
    ASIE_BSL          = "BSL asiatique (liquidité shorts)"
    ASIE_SSL          = "SSL asiatique (liquidité longs)"
    SWING_HAUT_H4     = "Swing High H4"
    SWING_BAS_H4      = "Swing Low H4"
    ATR_BASE          = "ATR-based"
    FIBONACCI_OTE     = "Fibonacci OTE 61.8–78.6%"
    PDH               = "PDH"
    PDL               = "PDL"

    # Alias anglais pour compatibilité
    @classmethod
    def from_str(cls, s: str) -> "SourceNiveau":
        mapping = {
            "ORDER_BLOCK_H4": cls.ORDER_BLOCK_H4,
            "ORDER_BLOCK_H1": cls.ORDER_BLOCK_H1,
            "ASIAN_HIGH": cls.ASIE_HAUT,
            "ASIAN_LOW": cls.ASIE_BAS,
            "ASIAN_BSL": cls.ASIE_BSL,
            "ASIAN_SSL": cls.ASIE_SSL,
            "ASIAN_EQUAL_HIGH": cls.ASIE_EQUAL_HAUT,
            "ASIAN_EQUAL_LOW": cls.ASIE_EQUAL_BAS,
            "SWING_HIGH_H4": cls.SWING_HAUT_H4,
            "SWING_LOW_H4": cls.SWING_BAS_H4,
            "ATR_BASED": cls.ATR_BASE,
        }
        return mapping.get(s, cls.ATR_BASE)


# Alias anglais pour compatibilité avec les prompts existants
LevelSource = SourceNiveau


# ── Dataclasses ────────────────────────────────────────────────────────────

@dataclass
class NiveauConfluent:
    """Un niveau de prix avec ses sources de confluence."""
    prix: float
    sources: List[SourceNiveau]
    score_confluence: int
    tolerance_pct: float
    description: str

    # Alias anglais
    @property
    def price(self) -> float:
        return self.prix

    @property
    def confluence_score(self) -> int:
        return self.score_confluence


# Alias anglais
ConfluenceLevel = NiveauConfluent


# ── Calculateur de confluence ──────────────────────────────────────────────

class CalculateurConfluence:
    """
    Regroupe et score les niveaux de prix de sources multiples.
    Deux niveaux sont "confluents" s'ils sont à moins de tolerance_pct%
    l'un de l'autre — ils forment une zone de prix institutionnellement forte.
    """

    TOLERANCE_PCT_DEFAUT = 0.08  # 0.08% ≈ 1.6$ sur XAUUSD à 2000$

    # Alias anglais
    DEFAULT_TOLERANCE_PCT = TOLERANCE_PCT_DEFAUT

    def __init__(self) -> None:
        pass

    def construire_carte_confluence(
        self,
        tous_niveaux: List[Tuple[float, SourceNiveau]],
        tolerance_pct: float = TOLERANCE_PCT_DEFAUT,
    ) -> List[NiveauConfluent]:
        """
        Regroupe tous les niveaux par proximité et calcule la confluence.

        Args:
            tous_niveaux: Liste de (prix, source).
            tolerance_pct: Tolérance en % pour regrouper les niveaux proches.

        Returns:
            Liste de NiveauConfluent triée par score décroissant.
        """
        if not tous_niveaux:
            return []

        # Filtrer les prix invalides
        tous_niveaux = [(p, s) for p, s in tous_niveaux if p > 0]
        if not tous_niveaux:
            return []

        # Trier par prix croissant
        tries = sorted(tous_niveaux, key=lambda x: x[0])
        groupes: List[List[Tuple[float, SourceNiveau]]] = []
        groupe_courant = [tries[0]]

        for prix, source in tries[1:]:
            ref = groupe_courant[0][0]
            diff_pct = abs(prix - ref) / ref * 100 if ref > 0 else 100
            if diff_pct <= tolerance_pct:
                groupe_courant.append((prix, source))
            else:
                groupes.append(groupe_courant)
                groupe_courant = [(prix, source)]
        groupes.append(groupe_courant)

        niveaux_confluents = []
        for groupe in groupes:
            prix_liste = [p for p, _ in groupe]
            sources = [s for _, s in groupe]
            prix_moyen = sum(prix_liste) / len(prix_liste)
            score = len(sources)

            # Bonus selon les combinaisons de sources
            a_ob = any(s.value.startswith("OB") for s in sources)
            a_asie = any("asiatique" in s.value for s in sources)
            a_swing = any("Swing" in s.value for s in sources)

            if a_ob and a_asie:
                score += 2   # OB + niveau asiatique = très fort
            if a_ob and a_swing:
                score += 1
            if a_asie and a_swing:
                score += 1
            if len(sources) >= 3:
                score += 1   # Triple confluence bonus

            parties = [s.value for s in sources]
            niveaux_confluents.append(NiveauConfluent(
                prix=round(prix_moyen, 2),
                sources=sources,
                score_confluence=score,
                tolerance_pct=tolerance_pct,
                description=" + ".join(parties),
            ))

        return sorted(niveaux_confluents, key=lambda x: x.score_confluence, reverse=True)

    # Alias anglais
    def build_confluence_map(
        self,
        all_levels: List[Tuple[float, SourceNiveau]],
        tolerance_pct: float = TOLERANCE_PCT_DEFAUT,
    ) -> List[NiveauConfluent]:
        return self.construire_carte_confluence(all_levels, tolerance_pct)

    def trouver_meilleur_niveau_proche(
        self,
        prix_cible: float,
        carte: List[NiveauConfluent],
        fenetre_pct: float = 0.3,
    ) -> Optional[NiveauConfluent]:
        """
        Trouve le niveau confluent le plus pertinent près d'un prix cible.
        Pondération : score / (distance + 0.01) pour favoriser proches + forts.

        Args:
            prix_cible: Prix de référence.
            carte: Carte de confluence.
            fenetre_pct: Fenêtre de recherche en % autour du prix cible.

        Returns:
            NiveauConfluent le plus pertinent, ou None.
        """
        candidats = []
        for niveau in carte:
            if niveau.prix <= 0 or prix_cible <= 0:
                continue
            diff_pct = abs(niveau.prix - prix_cible) / prix_cible * 100
            if diff_pct <= fenetre_pct:
                candidats.append((diff_pct, niveau))

        if not candidats:
            return None

        meilleur = max(
            candidats,
            key=lambda x: x[1].score_confluence / (x[0] + 0.01),
        )
        return meilleur[1]

    # Alias anglais
    def find_best_level_near(
        self,
        target_price: float,
        confluence_map: List[NiveauConfluent],
        search_range_pct: float = 0.3,
    ) -> Optional[NiveauConfluent]:
        return self.trouver_meilleur_niveau_proche(target_price, confluence_map, search_range_pct)

    def scorer_niveau(
        self,
        prix: float,
        source: SourceNiveau,
        carte: List[NiveauConfluent],
    ) -> int:
        """
        Score un niveau individuel selon sa confluence (0–100).

        Args:
            prix: Prix du niveau.
            source: Source du niveau.
            carte: Carte de confluence globale.

        Returns:
            Score de 0 à 100.
        """
        plus_proche = self.trouver_meilleur_niveau_proche(prix, carte, fenetre_pct=0.15)
        if plus_proche is None:
            return 20  # Niveau isolé

        score_base = min(100, plus_proche.score_confluence * 15)

        # Bonus liquidité asiatique
        sources_liquide = {SourceNiveau.ASIE_BSL, SourceNiveau.ASIE_SSL}
        if any(s in sources_liquide for s in plus_proche.sources):
            score_base = min(100, score_base + 20)

        # Bonus equal highs/lows
        sources_equal = {SourceNiveau.ASIE_EQUAL_HAUT, SourceNiveau.ASIE_EQUAL_BAS}
        if any(s in sources_equal for s in plus_proche.sources):
            score_base = min(100, score_base + 15)

        # Bonus OB
        if any(s.value.startswith("OB") for s in plus_proche.sources):
            score_base = min(100, score_base + 10)

        return score_base

    # Alias anglais
    def score_level(
        self,
        price: float,
        source: SourceNiveau,
        confluence_map: List[NiveauConfluent],
    ) -> int:
        return self.scorer_niveau(price, source, confluence_map)


# Alias anglais de la classe
LevelConfluenceCalculator = CalculateurConfluence
