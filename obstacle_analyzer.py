"""
obstacle_analyzer.py — Détection des obstacles entre le prix d'entrée et les TP.
Un obstacle = niveau de résistance/support qui bloque la trajectoire du prix.
"""

from typing import List, Optional, Dict
from loguru import logger

from config import CONFIG
from level_confluence import SourceNiveau


class AnalyseurObstacles:
    """
    Vérifie si des niveaux de structure bloquent la trajectoire
    entre le prix d'entrée et un Take Profit donné.

    Règles :
    - Un obstacle doit être entre 10% et 85% de la distance totale
    - Sévérité MAJOR pour les niveaux institutionnels (BSL, SSL, equal high/low, swing)
    - Sévérité MINOR pour les autres niveaux (high/low asiatique simple, etc.)
    """

    DISTANCE_MIN_PCT = 10.0   # Ignorer si obstacle à < 10% de la distance totale
    DISTANCE_MAX_PCT = 85.0   # Ignorer si obstacle à > 85% (trop près du TP)

    # Alias anglais
    OBSTACLE_MIN_DISTANCE_PCT = DISTANCE_MIN_PCT
    OBSTACLE_MAX_DISTANCE_PCT = DISTANCE_MAX_PCT

    # Sources considérées comme obstacles MAJOR
    SOURCES_MAJOR = {
        SourceNiveau.ASIE_BSL,
        SourceNiveau.ASIE_SSL,
        SourceNiveau.ASIE_EQUAL_HAUT,
        SourceNiveau.ASIE_EQUAL_BAS,
        SourceNiveau.SWING_HAUT_H4,
        SourceNiveau.SWING_BAS_H4,
    }

    # Sources résistance pour LONG
    SOURCES_RESISTANCE = {
        SourceNiveau.ASIE_HAUT,
        SourceNiveau.ASIE_EQUAL_HAUT,
        SourceNiveau.ASIE_BSL,
        SourceNiveau.SWING_HAUT_H4,
        SourceNiveau.PDH,
    }

    # Sources support pour SHORT
    SOURCES_SUPPORT = {
        SourceNiveau.ASIE_BAS,
        SourceNiveau.ASIE_EQUAL_BAS,
        SourceNiveau.ASIE_SSL,
        SourceNiveau.SWING_BAS_H4,
        SourceNiveau.PDL,
    }

    def trouver_obstacles(
        self,
        entree: float,
        tp: float,
        direction: str,
        niveaux: List[tuple],
    ) -> List[Dict]:
        """
        Cherche des obstacles entre l'entrée et le TP.

        Args:
            entree: Prix d'entrée.
            tp: Prix du take profit.
            direction: "bullish" ou "bearish".
            niveaux: Liste de (prix, source) — tous les niveaux connus.

        Returns:
            Liste d'obstacles triée par position (plus proche en premier).
            Format : [{price, source, position_pct, severity, description}]
        """
        distance_totale = abs(tp - entree)
        if distance_totale <= 0:
            return []

        obstacles = []

        for niveau_prix, source_niveau in niveaux:
            if niveau_prix <= 0:
                continue

            # L'obstacle doit être dans la trajectoire
            if direction == "bullish":
                if not (entree < niveau_prix < tp):
                    continue
                # Pour un LONG : chercher les résistances
                if source_niveau not in self.SOURCES_RESISTANCE:
                    continue
            else:
                if not (tp < niveau_prix < entree):
                    continue
                # Pour un SHORT : chercher les supports
                if source_niveau not in self.SOURCES_SUPPORT:
                    continue

            # Position relative dans la trajectoire
            if direction == "bullish":
                distance_depuis_entree = niveau_prix - entree
            else:
                distance_depuis_entree = entree - niveau_prix

            position_pct = (distance_depuis_entree / distance_totale) * 100

            # Ignorer les obstacles trop proches de l'entrée ou du TP
            if position_pct < self.DISTANCE_MIN_PCT:
                continue
            if position_pct > self.DISTANCE_MAX_PCT:
                continue

            severite = "major" if source_niveau in self.SOURCES_MAJOR else "minor"

            obstacles.append({
                "price": niveau_prix,
                "source": source_niveau.value,
                "position_pct": round(position_pct, 1),
                "severity": severite,
                "description": (
                    f"{source_niveau.value} @ {niveau_prix:.2f} "
                    f"({position_pct:.0f}% de la trajectoire)"
                ),
            })

        return sorted(obstacles, key=lambda x: x["position_pct"])

    # Alias anglais
    def find_obstacles(
        self,
        entry: float,
        tp: float,
        direction: str,
        structure_levels: List[tuple],
    ) -> List[Dict]:
        return self.trouver_obstacles(entry, tp, direction, structure_levels)

    def a_obstacle_majeur(self, obstacles: List[Dict]) -> bool:
        """True si au moins un obstacle MAJOR est présent."""
        return any(o["severity"] == "major" for o in obstacles)

    # Alias anglais
    def has_major_obstacle(self, obstacles: List[Dict]) -> bool:
        return self.a_obstacle_majeur(obstacles)

    def get_nearest_obstacle(self, obstacles: List[Dict]) -> Optional[Dict]:
        """Retourne l'obstacle le plus proche de l'entrée."""
        return obstacles[0] if obstacles else None

    def suggerer_tp_ajuste(
        self,
        entree: float,
        tp_original: float,
        obstacles: List[Dict],
        direction: str,
        rr_min: float,
        distance_sl: float,
    ) -> Optional[float]:
        """
        Si un obstacle MAJOR est détecté, suggère un TP juste avant l'obstacle.
        Marge de sécurité = 30% de la distance SL.
        Retourne None si le TP ajusté ne respecte pas le R:R minimum.

        Args:
            entree: Prix d'entrée.
            tp_original: TP original à ajuster.
            obstacles: Liste d'obstacles détectés.
            direction: "bullish" ou "bearish".
            rr_min: R:R minimum requis.
            distance_sl: Distance SL en prix (= 1R).

        Returns:
            Prix du TP ajusté ou None.
        """
        obstacles_majeurs = [o for o in obstacles if o["severity"] == "major"]
        if not obstacles_majeurs:
            return None

        obstacle_proche = obstacles_majeurs[0]
        marge = distance_sl * 0.3  # 30% du SL comme marge de sécurité

        if direction == "bullish":
            tp_ajuste = obstacle_proche["price"] - marge
            rr_ajuste = (tp_ajuste - entree) / distance_sl if distance_sl > 0 else 0
        else:
            tp_ajuste = obstacle_proche["price"] + marge
            rr_ajuste = (entree - tp_ajuste) / distance_sl if distance_sl > 0 else 0

        if rr_ajuste < rr_min:
            logger.warning(
                f"TP ajusté {tp_ajuste:.2f} insuffisant — "
                f"R:R {rr_ajuste:.2f} < minimum {rr_min} | "
                f"Obstacle: {obstacle_proche['description']}"
            )
            return None

        logger.info(
            f"TP ajusté pour éviter obstacle : {tp_original:.2f} → {tp_ajuste:.2f} | "
            f"Obstacle: {obstacle_proche['description']} | "
            f"R:R ajusté: {rr_ajuste:.2f}"
        )
        return round(tp_ajuste, 2)

    # Alias anglais
    def suggest_adjusted_tp(
        self,
        entry: float,
        original_tp: float,
        obstacles: List[Dict],
        direction: str,
        min_rr: float,
        sl_distance: float,
    ) -> Optional[float]:
        return self.suggerer_tp_ajuste(
            entry, original_tp, obstacles, direction, min_rr, sl_distance
        )


# Alias anglais
ObstacleAnalyzer = AnalyseurObstacles
