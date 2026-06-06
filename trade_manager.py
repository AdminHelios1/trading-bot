"""
trade_manager.py — Suivi des trades ouverts, trailing stop, prises de profit partielles.
Gère le cycle de vie complet d'une position depuis l'entrée jusqu'à la clôture.
"""

from dataclasses import dataclass, field
from typing import Optional, Dict, List
import pandas as pd
from loguru import logger

import MetaTrader5 as mt5

from config import CONFIG
from indicators import Indicateurs
from ob_detector import ZoneInstitutionnelle


@dataclass
class EtatPosition:
    """État complet d'une position ouverte gérée par le bot."""
    ticket: int
    symbole: str
    direction: str           # "LONG" ou "SHORT"
    volume_initial: float
    volume_restant: float
    prix_entree: float
    sl_initial: float
    sl_actuel: float
    tp1: float
    tp2: float
    tp3: float
    sl_distance: float       # Distance initiale SL en points
    zone_reference: Optional[ZoneInstitutionnelle] = None
    tp1_atteint: bool = False
    breakeven_actif: bool = False
    trailing_actif: bool = False
    profit_max_r: float = 0.0   # Maximum de R atteint (pour le trailing)


class GestionnairePositions:
    """Gère toutes les positions ouvertes du bot."""

    def __init__(self, connecteur) -> None:
        """
        Args:
            connecteur: Instance ConnecteurMT5.
        """
        self.connecteur = connecteur
        self.positions: Dict[int, EtatPosition] = {}  # ticket → EtatPosition

    def enregistrer_position(
        self,
        ticket: int,
        symbole: str,
        direction: str,
        volume: float,
        prix_entree: float,
        sl: float,
        tp1: float,
        tp2: float,
        tp3: float,
        sl_distance: float,
        zone_reference: Optional[ZoneInstitutionnelle] = None,
    ) -> None:
        """
        Enregistre une nouvelle position dans le gestionnaire.

        Args:
            ticket: Numéro de ticket MT5.
            symbole: Symbole de l'actif.
            direction: "LONG" ou "SHORT".
            volume: Volume initial du trade.
            prix_entree: Prix d'entrée.
            sl: Stop Loss initial.
            tp1: Take Profit 1 (50% de la position).
            tp2: Take Profit 2 (objectif principal).
            tp3: Take Profit 3 (optionnel, si structure favorable).
            sl_distance: Distance SL en points (pour calculer le R).
            zone_reference: Zone OB/BB de référence.
        """
        self.positions[ticket] = EtatPosition(
            ticket=ticket,
            symbole=symbole,
            direction=direction,
            volume_initial=volume,
            volume_restant=volume,
            prix_entree=prix_entree,
            sl_initial=sl,
            sl_actuel=sl,
            tp1=tp1,
            tp2=tp2,
            tp3=tp3,
            sl_distance=sl_distance,
            zone_reference=zone_reference,
        )
        logger.info(
            f"Position enregistrée | Ticket: {ticket} | {direction} {volume} {symbole} "
            f"@ {prix_entree:.5f} | SL: {sl:.5f} | TP1: {tp1:.5f} | TP2: {tp2:.5f}"
        )

    def gerer_positions(self, df_m15: pd.DataFrame) -> List[int]:
        """
        Gère toutes les positions ouvertes : trailing stop, prises de profit partielles.
        À appeler à chaque nouvelle bougie M5 fermée.

        Args:
            df_m15: DataFrame M15 pour le calcul de l'ATR du trailing.

        Returns:
            Liste des tickets de positions fermées pendant cette itération.
        """
        tickets_fermes = []
        atr_m15 = float(Indicateurs.atr(df_m15).iloc[-1])

        for ticket, pos in list(self.positions.items()):
            # Récupérer les positions toujours ouvertes dans MT5
            positions_mt5 = self.connecteur.get_positions_ouvertes(pos.symbole)
            ticket_toujours_ouvert = any(p["ticket"] == ticket for p in positions_mt5)

            if not ticket_toujours_ouvert:
                # Position fermée par SL ou TP dans MT5 → nettoyer
                logger.info(f"Position {ticket} fermée par MT5 (SL/TP atteint)")
                tickets_fermes.append(ticket)
                del self.positions[ticket]
                continue

            # Prix courant
            tick = self.connecteur.get_tick(pos.symbole)
            if tick is None:
                continue
            prix_actuel = tick["bid"] if pos.direction == "LONG" else tick["ask"]

            # Calculer le R actuel
            if pos.sl_distance > 0:
                if pos.direction == "LONG":
                    r_actuel = (prix_actuel - pos.prix_entree) / pos.sl_distance
                else:
                    r_actuel = (pos.prix_entree - prix_actuel) / pos.sl_distance
                pos.profit_max_r = max(pos.profit_max_r, r_actuel)
            else:
                r_actuel = 0.0

            # ── Gestion TP1 (prise de profit partielle à 1R) ───────────────
            if not pos.tp1_atteint:
                tp1_atteint = (
                    (pos.direction == "LONG" and prix_actuel >= pos.tp1)
                    or (pos.direction == "SHORT" and prix_actuel <= pos.tp1)
                )
                if tp1_atteint:
                    volume_partiel = round(pos.volume_initial * CONFIG.TP1_FRACTION, 2)
                    type_pos = mt5.ORDER_TYPE_BUY if pos.direction == "LONG" else mt5.ORDER_TYPE_SELL
                    succes = self.connecteur.fermer_position(
                        ticket, pos.symbole, volume_partiel, type_pos
                    )
                    if succes:
                        pos.tp1_atteint = True
                        pos.volume_restant -= volume_partiel

                        # Déplacer SL au breakeven
                        nouveau_sl = pos.prix_entree
                        if self.connecteur.modifier_position(ticket, nouveau_sl, pos.tp2):
                            pos.sl_actuel = nouveau_sl
                            pos.breakeven_actif = True
                            logger.info(
                                f"TP1 atteint {ticket} | Volume partiel fermé: {volume_partiel} | "
                                f"SL déplacé au breakeven: {nouveau_sl:.5f}"
                            )

            # ── Trailing Stop (activé après 1R de gain) ────────────────────
            if pos.tp1_atteint and r_actuel >= CONFIG.TRAILING_ACTIVATION_RR:
                distance_trailing = atr_m15 * CONFIG.TRAILING_DISTANCE_ATR

                if pos.direction == "LONG":
                    nouveau_sl_trailing = prix_actuel - distance_trailing
                    # Avancer le SL seulement (ne jamais reculer)
                    if nouveau_sl_trailing > pos.sl_actuel:
                        if self.connecteur.modifier_position(ticket, nouveau_sl_trailing, pos.tp2):
                            ancien_sl = pos.sl_actuel
                            pos.sl_actuel = nouveau_sl_trailing
                            pos.trailing_actif = True
                            logger.debug(
                                f"Trailing LONG {ticket} | SL: {ancien_sl:.5f} → "
                                f"{nouveau_sl_trailing:.5f} | R actuel: {r_actuel:.2f}"
                            )
                else:  # SHORT
                    nouveau_sl_trailing = prix_actuel + distance_trailing
                    # Reculer le SL seulement (ne jamais avancer pour un SHORT)
                    if nouveau_sl_trailing < pos.sl_actuel:
                        if self.connecteur.modifier_position(ticket, nouveau_sl_trailing, pos.tp2):
                            ancien_sl = pos.sl_actuel
                            pos.sl_actuel = nouveau_sl_trailing
                            pos.trailing_actif = True
                            logger.debug(
                                f"Trailing SHORT {ticket} | SL: {ancien_sl:.5f} → "
                                f"{nouveau_sl_trailing:.5f} | R actuel: {r_actuel:.2f}"
                            )

        return tickets_fermes

    def fermer_position_invalidation(self, ticket: int, raison: str) -> bool:
        """
        Ferme une position suite à une invalidation de la thèse du trade.

        Args:
            ticket: Ticket de la position à fermer.
            raison: Raison textuelle de l'invalidation.

        Returns:
            True si fermeture réussie.
        """
        if ticket not in self.positions:
            return False

        pos = self.positions[ticket]
        type_pos = mt5.ORDER_TYPE_BUY if pos.direction == "LONG" else mt5.ORDER_TYPE_SELL

        succes = self.connecteur.fermer_position(
            ticket, pos.symbole, pos.volume_restant, type_pos
        )
        if succes:
            logger.warning(
                f"Position {ticket} fermée par invalidation | Raison: {raison}"
            )
            del self.positions[ticket]
        return succes

    def get_resume_positions(self) -> List[Dict]:
        """Retourne un résumé des positions pour le dashboard."""
        positions_mt5 = {}
        for symbole in set(p.symbole for p in self.positions.values()):
            for pos_mt5 in self.connecteur.get_positions_ouvertes(symbole):
                positions_mt5[pos_mt5["ticket"]] = pos_mt5

        resume = []
        for ticket, pos in self.positions.items():
            info_mt5 = positions_mt5.get(ticket, {})
            profit = info_mt5.get("profit", 0.0)

            r_actuel = 0.0
            if pos.sl_distance > 0:
                if pos.direction == "LONG":
                    prix_ref = info_mt5.get("prix_entree", pos.prix_entree)
                    prix_actuel_approx = prix_ref + profit / max(pos.volume_restant, 0.01)
                    r_actuel = (prix_actuel_approx - pos.prix_entree) / pos.sl_distance
                # Approximation simplifiée pour l'affichage dashboard

            resume.append({
                "ticket": ticket,
                "symbole": pos.symbole,
                "direction": pos.direction,
                "volume": pos.volume_restant,
                "prix_entree": pos.prix_entree,
                "sl": pos.sl_actuel,
                "tp2": pos.tp2,
                "profit": profit,
                "r_actuel": round(r_actuel, 2),
                "tp1_atteint": pos.tp1_atteint,
                "trailing_actif": pos.trailing_actif,
            })
        return resume

    def a_position_ouverte(self, symbole: Optional[str] = None) -> bool:
        """Vérifie si une position est actuellement ouverte."""
        if symbole:
            return any(p.symbole == symbole for p in self.positions.values())
        return len(self.positions) > 0

    def supprimer_position(self, ticket: int) -> None:
        """Supprime une position du gestionnaire (après fermeture MT5)."""
        if ticket in self.positions:
            del self.positions[ticket]
