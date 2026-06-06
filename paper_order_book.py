"""
paper_order_book.py — Carnet d'ordres simulé pour le mode paper trading.

Maintient l'état de toutes les positions simulées indépendamment de MT5.
Les tickets simulés commencent à 100001 pour ne jamais entrer en conflit
avec les vrais tickets MT5 (qui sont dans un espace beaucoup plus grand).
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional

from loguru import logger

from config import CONFIG


@dataclass
class PositionSimulee:
    """Une position ouverte en mode paper trading."""
    ticket: int
    symbole: str
    direction: str              # "BUY" ou "SELL"
    lots: float
    prix_entree: float
    sl: float
    tp: float
    heure_ouverture: datetime
    slippage_entree_pts: float  # Slippage appliqué à l'entrée
    qualite_execution: str      # Valeur de ExecutionQuality
    lots_restants: float = 0.0  # Mis à jour lors des fermetures partielles

    def __post_init__(self):
        self.lots_restants = self.lots


class CarnetOrdresPaper:
    """
    Carnet d'ordres en mémoire pour le paper trading.

    Gère le cycle de vie des positions simulées :
    - Enregistrement à l'ouverture
    - Mise à jour SL/TP
    - Fermeture partielle
    - Fermeture complète

    Les tickets simulés (≥ 100001) ne sont jamais envoyés à MT5.
    """

    TICKET_DEBUT = 100001  # Espace de tickets réservé au paper trading

    def __init__(self, config=None) -> None:
        self.config = config or CONFIG
        self._positions: Dict[int, PositionSimulee] = {}
        self._positions_fermees: List[dict] = []
        self._prochain_ticket: int = self.TICKET_DEBUT

    # ── Enregistrement ────────────────────────────────────────────────────

    def enregistrer(
        self,
        execution,          # SimulatedExecution — import circulaire évité
        sl: float,
        tp: float,
    ) -> int:
        """
        Enregistre une nouvelle position simulée.

        Args:
            execution: SimulatedExecution résultant de simulate_order().
            sl: Stop Loss demandé.
            tp: Take Profit demandé.

        Returns:
            Numéro de ticket simulé, ou -1 si l'exécution est rejetée (lots = 0).
        """
        if execution.executed_lots == 0:
            logger.debug("[PAPER] Position non enregistrée — exécution rejetée (lots = 0)")
            return -1

        ticket = self._prochain_ticket
        self._prochain_ticket += 1

        position = PositionSimulee(
            ticket=ticket,
            symbole=execution.symbol,
            direction=execution.order_type,
            lots=execution.executed_lots,
            prix_entree=execution.executed_price,
            sl=sl,
            tp=tp,
            heure_ouverture=execution.executed_at,
            slippage_entree_pts=execution.slippage_pts,
            qualite_execution=execution.execution_quality.value,
        )
        self._positions[ticket] = position

        logger.debug(
            f"[PAPER] Position #{ticket} ouverte | "
            f"{execution.order_type} {execution.executed_lots} lots "
            f"@ {execution.executed_price:.2f} | "
            f"SL:{sl:.2f} TP:{tp:.2f}"
        )
        return ticket

    # Alias anglais
    def record(self, execution, sl: float, tp: float) -> int:
        return self.enregistrer(execution, sl, tp)

    # ── Modification ──────────────────────────────────────────────────────

    def modifier_sl_tp(self, ticket: int, nouveau_sl: float, nouveau_tp: float) -> bool:
        """Met à jour le SL et/ou TP d'une position simulée."""
        if ticket not in self._positions:
            logger.warning(f"[PAPER] Ticket #{ticket} introuvable pour modification SL/TP")
            return False
        self._positions[ticket].sl = nouveau_sl
        self._positions[ticket].tp = nouveau_tp
        return True

    # Alias anglais
    def update_sl_tp(self, ticket: int, new_sl: float, new_tp: float) -> bool:
        return self.modifier_sl_tp(ticket, new_sl, new_tp)

    # ── Fermeture partielle ───────────────────────────────────────────────

    def fermer_partiel(
        self,
        ticket: int,
        lots: float,
        prix_fermeture: float,
    ) -> bool:
        """
        Ferme partiellement une position simulée.
        Si les lots restants tombent à ≤ 0.001 → position entièrement fermée.

        Args:
            ticket: Ticket de la position.
            lots: Lots à fermer.
            prix_fermeture: Prix simulé de fermeture.

        Returns:
            True si l'opération a réussi.
        """
        if ticket not in self._positions:
            logger.warning(f"[PAPER] Ticket #{ticket} introuvable pour fermeture partielle")
            return False

        pos = self._positions[ticket]
        pos.lots_restants = round(pos.lots_restants - lots, 8)

        if pos.lots_restants <= 0.001:
            # Position entièrement fermée
            self._archiver_position(pos, prix_fermeture)
            del self._positions[ticket]
            logger.debug(f"[PAPER] Position #{ticket} entièrement fermée @ {prix_fermeture:.2f}")
        else:
            logger.debug(
                f"[PAPER] Fermeture partielle #{ticket} | "
                f"{lots} lots @ {prix_fermeture:.2f} | "
                f"Restant : {pos.lots_restants} lots"
            )
        return True

    # Alias anglais
    def close_partial(self, ticket: int, lots: float, close_price: float) -> bool:
        return self.fermer_partiel(ticket, lots, close_price)

    # ── Consultation ──────────────────────────────────────────────────────

    def get_position(self, ticket: int) -> Optional[dict]:
        """
        Retourne les détails d'une position simulée.

        Returns:
            Dict avec les attributs de la position, ou None si inexistante.
        """
        pos = self._positions.get(ticket)
        if pos is None:
            return None
        return {
            "ticket": pos.ticket,
            "symbole": pos.symbole,
            "direction": pos.direction,
            "lots": pos.lots,
            "lots_restants": pos.lots_restants,
            "prix_entree": pos.prix_entree,
            "sl": pos.sl,
            "tp": pos.tp,
            "heure_ouverture": pos.heure_ouverture,
            "slippage_entree_pts": pos.slippage_entree_pts,
        }

    def get_toutes_positions(self) -> List[dict]:
        """Retourne toutes les positions ouvertes."""
        return [self.get_position(t) for t in self._positions]

    # Alias anglais
    def get_all_positions(self) -> List[dict]:
        return self.get_toutes_positions()

    def get_pnl_flottant(self, prix_actuel: float) -> float:
        """
        Calcule le P&L flottant de toutes les positions ouvertes.
        Approximation XAUUSD : 1$ de mouvement = 100$/lot.

        Args:
            prix_actuel: Prix bid ou ask courant.

        Returns:
            P&L total en USD.
        """
        total = 0.0
        for pos in self._positions.values():
            est_long = pos.direction == "BUY"
            diff = (
                prix_actuel - pos.prix_entree if est_long
                else pos.prix_entree - prix_actuel
            )
            total += diff * pos.lots_restants * 100.0
        return round(total, 2)

    # Alias anglais
    def get_unrealized_pnl(self, current_price: float) -> float:
        return self.get_pnl_flottant(current_price)

    def nb_positions_ouvertes(self) -> int:
        """Nombre de positions actuellement ouvertes."""
        return len(self._positions)

    # ── Utilitaires internes ───────────────────────────────────────────────

    def _archiver_position(self, pos: PositionSimulee, prix_fermeture: float) -> None:
        """Archive une position fermée pour l'historique."""
        self._positions_fermees.append({
            "ticket": pos.ticket,
            "symbole": pos.symbole,
            "direction": pos.direction,
            "lots": pos.lots,
            "prix_entree": pos.prix_entree,
            "prix_fermeture": prix_fermeture,
            "sl": pos.sl,
            "tp": pos.tp,
            "slippage_entree_pts": pos.slippage_entree_pts,
        })
        # Garder seulement les 200 dernières positions fermées
        if len(self._positions_fermees) > 200:
            self._positions_fermees = self._positions_fermees[-200:]


# Alias anglais
PaperOrderBook = CarnetOrdresPaper
