"""
risk_manager.py — Gestion du risque : position sizing, drawdown, circuit breaker.
Toutes les règles de risque sont centralisées ici et doivent être respectées sans exception.
"""

import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional, List
from loguru import logger

import MetaTrader5 as mt5

from config import CONFIG


@dataclass
class EtatRisque:
    """État du système de risque à un instant donné."""
    balance_depart_journee: float = 0.0
    balance_depart_total: float = 0.0
    drawdown_journalier_pct: float = 0.0
    drawdown_total_pct: float = 0.0
    pertes_consecutives: int = 0
    bot_arrete: bool = False
    circuit_breaker_actif: bool = False
    circuit_breaker_fin_timestamp: float = 0.0
    historique_resultats: List[float] = field(default_factory=list)  # +profit ou -perte


class GestionnaireRisque:
    """
    Calcule le position sizing, surveille les drawdowns et gère le circuit breaker.
    Doit être instancié une seule fois et partagé entre tous les modules.
    """

    def __init__(self) -> None:
        self.etat = EtatRisque()

    # ── Initialisation de la session ───────────────────────────────────────

    def initialiser_session(self, balance: float) -> None:
        """
        Initialise les valeurs de référence pour la nouvelle session de trading.
        À appeler une fois par jour au démarrage du bot.

        Args:
            balance: Balance du compte au début de la session.
        """
        self.etat.balance_depart_journee = balance
        if self.etat.balance_depart_total == 0.0:
            self.etat.balance_depart_total = balance
        logger.info(
            f"Session initialisée | Balance: {balance:.2f} | "
            f"Drawdown max journalier: {CONFIG.MAX_DRAWDOWN_JOURNALIER_PCT}% | "
            f"Drawdown max total: {CONFIG.MAX_DRAWDOWN_TOTAL_PCT}%"
        )

    # ── Calcul du lot size ─────────────────────────────────────────────────

    def calculer_lot_size(
        self,
        capital: float,
        risque_pct: float,
        sl_points: float,
        symbole: str,
        multiplicateur_risk: float = 1.0,
    ) -> float:
        """
        Calcule le lot size basé sur le capital, le risque % et la distance du SL.

        Formule : lot = montant_risqué / (sl_points × valeur_pip_par_lot)

        Args:
            capital: Solde du compte.
            risque_pct: Pourcentage du capital à risquer (ex: 1.0).
            sl_points: Distance du stop loss en points (unités MT5).
            symbole: Symbole MT5 (ex: "XAUUSD").
            multiplicateur_risk: Multiplier CB (1.0 normal, 0.5 en WARNING CB).

        Returns:
            Lot size arrondi aux contraintes du broker.
        """
        # Appliquer le multiplicateur du circuit breaker
        risque_pct_effectif = risque_pct * multiplicateur_risk
        if multiplicateur_risk < 1.0:
            logger.info(
                f"Risk réduit par CB : {risque_pct}% × {multiplicateur_risk} "
                f"= {risque_pct_effectif:.2f}%"
            )
        risque_pct = risque_pct_effectif
        if sl_points <= 0:
            logger.error("Calcul lot size impossible : sl_points <= 0")
            return 0.0

        info = mt5.symbol_info(symbole)
        if info is None:
            logger.error(f"Impossible de récupérer les infos du symbole {symbole}")
            return 0.0

        montant_risque = capital * (risque_pct / 100.0)

        # Valeur d'1 point pour 1 lot standard
        valeur_point = info.trade_tick_value  # Valeur d'1 tick (= 1 point)

        if valeur_point <= 0:
            logger.error(f"Valeur du tick invalide pour {symbole}: {valeur_point}")
            return 0.0

        lot_calcule = montant_risque / (sl_points * valeur_point)

        # Arrondir au volume_step du broker
        pas_volume = info.volume_step
        lot_arrondi = round(lot_calcule / pas_volume) * pas_volume
        lot_arrondi = round(lot_arrondi, 2)

        # Respecter les limites du broker
        lot_final = max(info.volume_min, min(lot_arrondi, info.volume_max))

        logger.debug(
            f"Position sizing | Capital: {capital:.2f} | Risque: {risque_pct}% = "
            f"{montant_risque:.2f} | SL: {sl_points:.1f}pts | "
            f"Valeur tick: {valeur_point:.4f} | Lot calculé: {lot_calcule:.3f} | "
            f"Lot final: {lot_final}"
        )
        return lot_final

    # ── Vérification drawdown ──────────────────────────────────────────────

    def mettre_a_jour_drawdown(self, equity_actuelle: float) -> None:
        """
        Met à jour les métriques de drawdown.

        Args:
            equity_actuelle: Equity actuelle du compte.
        """
        if self.etat.balance_depart_journee > 0:
            self.etat.drawdown_journalier_pct = max(
                0.0,
                (self.etat.balance_depart_journee - equity_actuelle)
                / self.etat.balance_depart_journee * 100,
            )
        if self.etat.balance_depart_total > 0:
            self.etat.drawdown_total_pct = max(
                0.0,
                (self.etat.balance_depart_total - equity_actuelle)
                / self.etat.balance_depart_total * 100,
            )

    def verifier_drawdown_journalier(self) -> bool:
        """
        Vérifie si le drawdown journalier est dans les limites.

        Returns:
            True si le drawdown est acceptable (trading autorisé).
        """
        if self.etat.drawdown_journalier_pct >= CONFIG.MAX_DRAWDOWN_JOURNALIER_PCT:
            logger.warning(
                f"⛔ DRAWDOWN JOURNALIER ATTEINT: {self.etat.drawdown_journalier_pct:.2f}% "
                f"≥ {CONFIG.MAX_DRAWDOWN_JOURNALIER_PCT}% — Pas de nouveau trade aujourd'hui"
            )
            return False
        return True

    def verifier_drawdown_total(self) -> bool:
        """
        Vérifie si le drawdown total est dans les limites.
        Si dépassé → arrêt total du bot.

        Returns:
            True si le drawdown total est acceptable.
        """
        if self.etat.drawdown_total_pct >= CONFIG.MAX_DRAWDOWN_TOTAL_PCT:
            logger.critical(
                f"🚨 DRAWDOWN TOTAL CRITIQUE: {self.etat.drawdown_total_pct:.2f}% "
                f"≥ {CONFIG.MAX_DRAWDOWN_TOTAL_PCT}% — ARRÊT D'URGENCE DU BOT"
            )
            self.etat.bot_arrete = True
            return False
        return True

    # ── Circuit breaker ────────────────────────────────────────────────────

    def enregistrer_resultat_trade(self, profit: float) -> None:
        """
        Enregistre le résultat d'un trade fermé et met à jour le circuit breaker.

        Args:
            profit: Profit ou perte du trade (positif = gain, négatif = perte).
        """
        self.etat.historique_resultats.append(profit)

        if profit < 0:
            self.etat.pertes_consecutives += 1
            logger.debug(f"Perte enregistrée | Pertes consécutives: {self.etat.pertes_consecutives}")
        else:
            self.etat.pertes_consecutives = 0

        # Activer le circuit breaker si N pertes consécutives
        if self.etat.pertes_consecutives >= CONFIG.CIRCUIT_BREAKER_PERTES_CONSECUTIVES:
            duree_pause_sec = CONFIG.CIRCUIT_BREAKER_PAUSE_HEURES * 3600
            self.etat.circuit_breaker_actif = True
            self.etat.circuit_breaker_fin_timestamp = time.time() + duree_pause_sec
            logger.warning(
                f"⚠️ CIRCUIT BREAKER ACTIVÉ: {self.etat.pertes_consecutives} pertes "
                f"consécutives — Pause de {CONFIG.CIRCUIT_BREAKER_PAUSE_HEURES}h"
            )

    def verifier_circuit_breaker(self) -> bool:
        """
        Vérifie si le circuit breaker est actif.

        Returns:
            True si le trading est autorisé (circuit breaker inactif).
        """
        if not self.etat.circuit_breaker_actif:
            return True

        temps_restant = self.etat.circuit_breaker_fin_timestamp - time.time()
        if temps_restant <= 0:
            self.etat.circuit_breaker_actif = False
            self.etat.pertes_consecutives = 0
            logger.info("Circuit breaker désactivé — reprise du trading autorisée")
            return True

        heures = int(temps_restant / 3600)
        minutes = int((temps_restant % 3600) / 60)
        logger.debug(
            f"Circuit breaker actif — reprise dans {heures}h{minutes:02d}m"
        )
        return False

    # ── Validation globale avant trade ────────────────────────────────────

    def trading_autorise(self, equity: float) -> bool:
        """
        Validation complète avant d'ouvrir un trade.
        Agrège toutes les vérifications de risque.

        Args:
            equity: Equity actuelle du compte.

        Returns:
            True si toutes les conditions de risque sont satisfaites.
        """
        self.mettre_a_jour_drawdown(equity)

        if self.etat.bot_arrete:
            logger.error("Bot arrêté définitivement (drawdown total dépassé)")
            return False

        if not self.verifier_drawdown_total():
            return False

        if not self.verifier_drawdown_journalier():
            return False

        if not self.verifier_circuit_breaker():
            return False

        return True

    # ── Calcul SL/TP ──────────────────────────────────────────────────────

    def calculer_sl_tp(
        self,
        prix_entree: float,
        prix_ob_bas: float,
        prix_ob_haut: float,
        atr_m15: float,
        direction: str,
    ) -> tuple:
        """
        Calcule le Stop Loss et les Take Profit selon la méthodologie SMC.

        SL placé sous/au-dessus de l'Order Block + marge ATR pour éviter les stop hunts.
        TP calculés selon les ratios R:R configurés.

        Args:
            prix_entree: Prix d'entrée du trade.
            prix_ob_bas: Bas de l'Order Block de référence.
            prix_ob_haut: Haut de l'Order Block de référence.
            atr_m15: ATR(14) sur M15 au moment du trade.
            direction: "LONG" ou "SHORT".

        Returns:
            Tuple (sl, tp1, tp2, tp3, sl_points).
        """
        marge_sl = atr_m15 * CONFIG.SL_MARGE_ATR

        if direction == "LONG":
            sl = prix_ob_bas - marge_sl
            if sl >= prix_entree:
                logger.warning(f"SL invalide pour LONG: SL {sl:.5f} >= entrée {prix_entree:.5f}")
                return None, None, None, None, None
            sl_distance = prix_entree - sl
        else:  # SHORT
            sl = prix_ob_haut + marge_sl
            if sl <= prix_entree:
                logger.warning(f"SL invalide pour SHORT: SL {sl:.5f} <= entrée {prix_entree:.5f}")
                return None, None, None, None, None
            sl_distance = sl - prix_entree

        # Take profits basés sur les multiples de R
        if direction == "LONG":
            tp1 = prix_entree + sl_distance * CONFIG.TP1_RR
            tp2 = prix_entree + sl_distance * CONFIG.TP2_RR
            tp3 = prix_entree + sl_distance * CONFIG.TP3_RR
        else:
            tp1 = prix_entree - sl_distance * CONFIG.TP1_RR
            tp2 = prix_entree - sl_distance * CONFIG.TP2_RR
            tp3 = prix_entree - sl_distance * CONFIG.TP3_RR

        # Vérifier le R:R minimum
        rr_effectif = sl_distance * CONFIG.RR_MINIMUM
        if direction == "LONG" and tp2 < prix_entree + rr_effectif:
            logger.debug(
                f"R:R insuffisant: {(tp2 - prix_entree) / sl_distance:.2f} "
                f"< {CONFIG.RR_MINIMUM}"
            )
            return None, None, None, None, None
        if direction == "SHORT" and tp2 > prix_entree - rr_effectif:
            logger.debug(f"R:R insuffisant pour SHORT")
            return None, None, None, None, None

        logger.debug(
            f"SL/TP calculés | {direction} @ {prix_entree:.5f} | "
            f"SL: {sl:.5f} ({sl_distance:.2f}pts) | "
            f"TP1: {tp1:.5f} | TP2: {tp2:.5f} | TP3: {tp3:.5f} | "
            f"R:R effectif: {CONFIG.TP2_RR:.1f}"
        )
        return sl, tp1, tp2, tp3, sl_distance

    # ── Rapport ────────────────────────────────────────────────────────────

    def get_resume(self) -> dict:
        """Retourne un résumé de l'état du risque pour le dashboard."""
        nb_trades = len(self.etat.historique_resultats)
        nb_gagnants = sum(1 for r in self.etat.historique_resultats if r > 0)
        win_rate = (nb_gagnants / nb_trades * 100) if nb_trades > 0 else 0.0

        return {
            "drawdown_journalier_pct": round(self.etat.drawdown_journalier_pct, 2),
            "drawdown_total_pct": round(self.etat.drawdown_total_pct, 2),
            "pertes_consecutives": self.etat.pertes_consecutives,
            "circuit_breaker_actif": self.etat.circuit_breaker_actif,
            "bot_arrete": self.etat.bot_arrete,
            "nb_trades": nb_trades,
            "win_rate": round(win_rate, 1),
        }
