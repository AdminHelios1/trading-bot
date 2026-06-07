"""
correlation_monitor.py — Surveillance des corrélations entre actifs.

Matrice de corrélation statique basée sur les corrélations historiques
entre les 4 actifs du portefeuille. Utilisé par le PortfolioRiskManager
pour bloquer les trades qui créeraient une exposition corrélée dangereuse.

Corrélations clés :
- NAS100 ↔ SP500 : 0.95 (BLOQUÉ — trop corrélés)
- XAUUSD ↔ WTI   : 0.40 (AUTORISÉ — corrélation modérée)
- NAS100 ↔ XAUUSD: -0.30 (AUTORISÉ — légèrement inversé)

Aucune dépendance MT5 — logique pure.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional, Tuple

from loguru import logger


@dataclass
class ResultatCorrelation:
    """Résultat d'une vérification de corrélation entre deux actifs."""
    symbole_1: str
    symbole_2: str
    coefficient: float
    bloque: bool
    raison: str

    @property
    def blocked(self) -> bool:
        return self.bloque

    @property
    def correlation(self) -> float:
        return self.coefficient


class MoniteurCorrelation:
    """
    Surveille les corrélations entre les actifs du portefeuille.

    La matrice de corrélation est statique — basée sur les corrélations
    historiques moyennes sur 1 an. En conditions de marché extrêmes,
    toutes les corrélations tendent vers 1 (risk-off global).

    RÈGLE ABSOLUE : NAS100 + SP500 ne peuvent JAMAIS être ouverts ensemble.
    Leur corrélation de 0.95 rendrait l'exposition équivalente à 2× le risque.
    """

    # ── Matrice de corrélation statique ────────────────────────────────────
    # Corrélation positive élevée = risques similaires = doublement du risque
    # Corrélation négative = hedge naturel = autorisé
    MATRICE_CORRELATION: Dict[Tuple[str, str], float] = {
        # ── Symboles MT5 officiels ────────────────────────────────────────
        ("NAS100", "US500"):   0.95,   # BLOQUÉ — indices américains quasi-identiques
        ("NAS100", "XAUUSD"): -0.30,   # Autorisé — légèrement inversé (risk-off vs tech)
        ("NAS100", "XTIUSD"): -0.20,   # Autorisé
        ("US500",  "XAUUSD"): -0.30,   # Autorisé
        ("US500",  "XTIUSD"): -0.20,   # Autorisé
        ("XAUUSD", "XTIUSD"):  0.40,   # Autorisé — actifs réels, corrélation modérée
        # ── Aliases clés config (SP500 = US500, WTI = XTIUSD) ────────────
        ("NAS100", "SP500"):   0.95,   # Alias SP500→US500 — BLOQUÉ
        ("SP500",  "XAUUSD"): -0.30,   # Alias
        ("SP500",  "XTIUSD"): -0.20,   # Alias
        ("SP500",  "WTI"):    -0.20,   # Alias
        ("XAUUSD", "WTI"):    0.40,   # Alias WTI→XTIUSD
        ("NAS100", "WTI"):   -0.20,   # Alias
        ("US500",  "WTI"):   -0.20,   # Alias
    }

    # Seuil au-delà duquel deux actifs ne peuvent pas être ouverts ensemble
    SEUIL_BLOCAGE: float = 0.70

    def __init__(self, seuil_blocage: Optional[float] = None) -> None:
        """
        Args:
            seuil_blocage: Surcharge du seuil de blocage (défaut: 0.70).
        """
        if seuil_blocage is not None:
            self.SEUIL_BLOCAGE = seuil_blocage

    def check_pair(
        self,
        symbole_1: str,
        symbole_2: str,
    ) -> ResultatCorrelation:
        """
        Vérifie si deux actifs peuvent être ouverts simultanément.

        Args:
            symbole_1: Premier symbole (ex: "NAS100").
            symbole_2: Second symbole (ex: "US500").

        Returns:
            ResultatCorrelation avec blocked=True si corrélation > seuil.
        """
        coefficient = self._get_correlation(symbole_1, symbole_2)

        if coefficient is None:
            # Paire inconnue → autoriser par défaut
            return ResultatCorrelation(
                symbole_1=symbole_1,
                symbole_2=symbole_2,
                coefficient=0.0,
                bloque=False,
                raison=f"Paire {symbole_1}/{symbole_2} non répertoriée — autorisée",
            )

        bloque = coefficient >= self.SEUIL_BLOCAGE
        if bloque:
            raison = (
                f"Corrélation {symbole_1} ↔ {symbole_2} = {coefficient:.2f} "
                f"> seuil {self.SEUIL_BLOCAGE} — trade bloqué"
            )
        else:
            raison = (
                f"Corrélation {symbole_1} ↔ {symbole_2} = {coefficient:.2f} "
                f"≤ seuil {self.SEUIL_BLOCAGE} — autorisé"
            )

        return ResultatCorrelation(
            symbole_1=symbole_1,
            symbole_2=symbole_2,
            coefficient=coefficient,
            bloque=bloque,
            raison=raison,
        )

    def verifier_nouveau_trade(
        self,
        nouveau_symbole: str,
        symboles_ouverts: List[str],
    ) -> Tuple[bool, str]:
        """
        Vérifie si ouvrir un nouveau trade créerait une corrélation dangereuse
        avec les positions déjà ouvertes.

        Args:
            nouveau_symbole  : Symbole qu'on veut ouvrir.
            symboles_ouverts : Liste des symboles actuellement en position.

        Returns:
            (bloque, raison)
        """
        for symbole_ouvert in symboles_ouverts:
            if symbole_ouvert == nouveau_symbole:
                continue

            resultat = self.check_pair(nouveau_symbole, symbole_ouvert)
            if resultat.bloque:
                return True, resultat.raison

        return False, "Aucune corrélation dangereuse détectée"

    # Alias anglais
    def check_new_trade(
        self,
        new_symbol: str,
        open_symbols: List[str],
    ) -> Tuple[bool, str]:
        return self.verifier_nouveau_trade(new_symbol, open_symbols)

    def get_correlation(self, symbole_1: str, symbole_2: str) -> Optional[float]:
        """Retourne le coefficient de corrélation entre deux actifs (ou None)."""
        return self._get_correlation(symbole_1, symbole_2)

    def get_matrice_complete(self) -> Dict[str, Dict[str, float]]:
        """
        Retourne la matrice de corrélation complète sous forme de dictionnaire
        imbriqué pour le dashboard.

        Returns:
            {"NAS100": {"US500": 0.95, "XAUUSD": -0.30, ...}, ...}
        """
        matrice: Dict[str, Dict[str, float]] = {}
        for (s1, s2), coeff in self.MATRICE_CORRELATION.items():
            if s1 not in matrice:
                matrice[s1] = {}
            if s2 not in matrice:
                matrice[s2] = {}
            matrice[s1][s2] = coeff
            matrice[s2][s1] = coeff  # Symétrique
        return matrice

    def get_paires_bloquees(self) -> List[Tuple[str, str, float]]:
        """
        Retourne toutes les paires dont la corrélation bloque le trading simultané.

        Returns:
            Liste de (symbole_1, symbole_2, coefficient)
        """
        return [
            (s1, s2, coeff)
            for (s1, s2), coeff in self.MATRICE_CORRELATION.items()
            if coeff >= self.SEUIL_BLOCAGE
        ]

    def _get_correlation(
        self,
        symbole_1: str,
        symbole_2: str,
    ) -> Optional[float]:
        """Cherche le coefficient dans les deux sens."""
        coeff = self.MATRICE_CORRELATION.get((symbole_1, symbole_2))
        if coeff is None:
            coeff = self.MATRICE_CORRELATION.get((symbole_2, symbole_1))
        return coeff


# Alias anglais
CorrelationMonitor = MoniteurCorrelation
