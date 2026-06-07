"""
remote_control.py — Contrôle du bot à distance via commandes Telegram.

Écoute les messages entrants et exécute les commandes autorisées.
Appelé par le scheduler APScheduler toutes les 30 secondes.

Commandes disponibles :
  /help       — Liste des commandes
  /status     — État complet du bot
  /pause      — Mettre le bot en pause (no new trades)
  /resume     — Reprendre le trading
  /positions  — Lister les positions ouvertes
  /balance    — Balance et P&L du jour
  /cb         — État du circuit breaker
  /stop       — Arrêt propre du bot
"""

import os
import signal
from datetime import datetime
from typing import Optional

from loguru import logger

from telegram_notifier import (
    TelegramNotifier,
    NiveauAlerte,
    StatutBot,
)
from health_monitor import MoniteurSante

# Alias anglais
AlertLevel = NiveauAlerte
BotStatus = StatutBot


class TelecommandeBOT:
    """
    Contrôle le bot à distance via des commandes Telegram.

    Sécurité : seuls les messages provenant du chat_id configuré
    sont traités. Les autres sont ignorés silencieusement.
    """

    COMMANDES_AUTORISEES = [
        "/help", "/status", "/pause", "/resume",
        "/positions", "/balance", "/cb", "/stop",
    ]

    def __init__(
        self,
        notifier: TelegramNotifier,
        moniteur_sante: MoniteurSante,
        config=None,
        trade_manager=None,
        circuit_breaker=None,
    ) -> None:
        self.notifier = notifier
        self.moniteur_sante = moniteur_sante
        self.config = config
        self.trade_manager = trade_manager
        self.circuit_breaker = circuit_breaker

        self._dernier_update_id: int = 0
        self._en_pause_distante: bool = False

    # ── Boucle d'écoute ────────────────────────────────────────────────────

    def poll_and_execute(self) -> None:
        """
        Vérifie les nouvelles commandes Telegram et les exécute.
        Appelé par APScheduler toutes les 30 secondes.
        """
        try:
            updates = self.notifier.get_updates(
                offset=self._dernier_update_id + 1
            )
        except Exception as e:
            logger.debug(f"Erreur récupération updates Telegram : {e}")
            return

        for update in updates:
            self._dernier_update_id = update.get("update_id", 0)
            message = update.get("message", {})
            texte = message.get("text", "").strip()
            chat_id = str(message.get("chat", {}).get("id", ""))

            # Vérifier que le message vient du bon chat (sécurité)
            if chat_id != self.notifier.chat_id:
                logger.debug(f"Commande ignorée — chat ID inconnu: {chat_id}")
                continue

            if texte.startswith("/"):
                commande = texte.split()[0].lower()
                logger.info(f"Commande Telegram reçue : {commande}")
                self._executer_commande(commande)

    # ── Exécution des commandes ────────────────────────────────────────────

    def _executer_commande(self, commande: str) -> None:
        """Route et exécute une commande Telegram."""
        if commande == "/help":
            self._cmd_help()
        elif commande == "/status":
            self._cmd_status()
        elif commande == "/pause":
            self._cmd_pause()
        elif commande == "/resume":
            self._cmd_resume()
        elif commande == "/positions":
            self._cmd_positions()
        elif commande == "/balance":
            self._cmd_balance()
        elif commande == "/cb":
            self._cmd_circuit_breaker()
        elif commande == "/stop":
            self._cmd_stop()
        else:
            self.notifier.send(
                f"❓ Commande inconnue : <code>{commande}</code>\n"
                "Utiliser /help pour la liste des commandes.",
                level=NiveauAlerte.INFO,
            )

    def _cmd_help(self) -> None:
        """Envoie la liste des commandes disponibles."""
        self.notifier.send(
            "<b>📋 Commandes disponibles</b>\n\n"
            "/status — État complet du bot\n"
            "/pause — Mettre en pause (no new trades)\n"
            "/resume — Reprendre le trading\n"
            "/positions — Positions ouvertes\n"
            "/balance — Balance et P&L jour\n"
            "/cb — État circuit breaker\n"
            "/stop — Arrêt propre du bot\n\n"
            "ℹ️ Le bot exécute automatiquement les alertes\n"
            "et les pings de santé toutes les 5 minutes.",
            level=NiveauAlerte.INFO,
        )

    def _cmd_status(self) -> None:
        """Envoie l'état complet du bot via un ping de santé."""
        try:
            sante = self.moniteur_sante._collecter_donnees()
            self.notifier.send_ping(sante)
        except Exception as e:
            self.notifier.send(
                f"❌ Erreur lors de la collecte de l'état : {e}",
                level=NiveauAlerte.WARNING,
            )

    def _cmd_pause(self) -> None:
        """Met le bot en pause distante (no new trades)."""
        self._en_pause_distante = True
        self.moniteur_sante.definir_statut_bot(StatutBot.EN_PAUSE)
        self.notifier.send(
            "⏸️ <b>Bot mis en PAUSE</b>\n"
            "Aucun nouveau trade ne sera ouvert.\n"
            "Les positions existantes sont gérées normalement.\n"
            "Utiliser /resume pour reprendre.",
            level=NiveauAlerte.WARNING,
        )
        logger.warning("Bot mis en pause via Telegram")

    def _cmd_resume(self) -> None:
        """Reprend le trading si le bot était en pause distante."""
        if self._en_pause_distante:
            self._en_pause_distante = False
            self.moniteur_sante.definir_statut_bot(StatutBot.EN_COURS)
            self.notifier.send(
                "▶️ <b>Bot REPRIS</b>\n"
                "Le trading reprend normalement.",
                level=NiveauAlerte.RECOVERY,
            )
            logger.info("Bot repris via Telegram")
        else:
            self.notifier.send(
                "ℹ️ Le bot n'était pas en pause distante.",
                level=NiveauAlerte.INFO,
            )

    def _cmd_positions(self) -> None:
        """Envoie l'état des positions ouvertes."""
        try:
            import MetaTrader5 as mt5
            symbole = getattr(self.config, "SYMBOLE", "XAUUSD") if self.config else "XAUUSD"
            positions = mt5.positions_get(symbol=symbole)

            if not positions:
                self.notifier.send(
                    f"📊 Aucune position ouverte sur {symbole}",
                    level=NiveauAlerte.INFO,
                )
                return

            msg = f"📊 <b>{len(positions)} position(s) ouverte(s)</b>\n\n"
            for pos in positions:
                direction = "LONG" if pos.type == mt5.ORDER_TYPE_BUY else "SHORT"
                emoji = "🟢" if pos.profit >= 0 else "🔴"
                signe = "+" if pos.profit >= 0 else ""
                msg += (
                    f"{emoji} {direction} {pos.volume} lots @ "
                    f"{pos.price_open:.2f}\n"
                    f"P&L: {signe}{pos.profit:.2f}$ | "
                    f"SL: {pos.sl:.2f} | TP: {pos.tp:.2f}\n\n"
                )
            self.notifier.send(msg, level=NiveauAlerte.INFO)

        except Exception as e:
            self.notifier.send(
                f"❌ Positions non disponibles : {e}",
                level=NiveauAlerte.WARNING,
            )

    def _cmd_balance(self) -> None:
        """Envoie la balance et le P&L du jour."""
        try:
            import MetaTrader5 as mt5
            compte = mt5.account_info()
            if compte is None:
                self.notifier.send(
                    "❌ Impossible de lire les infos du compte",
                    level=NiveauAlerte.WARNING,
                )
                return

            signe_profit = "+" if compte.profit >= 0 else ""
            self.notifier.send(
                f"💰 <b>État du compte</b>\n\n"
                f"Balance: {compte.balance:.2f}$\n"
                f"Equity: {compte.equity:.2f}$\n"
                f"Marge libre: {compte.margin_free:.2f}$\n"
                f"P&L flottant: {signe_profit}{compte.profit:.2f}$",
                level=NiveauAlerte.INFO,
            )

        except Exception as e:
            self.notifier.send(
                f"❌ Balance non disponible : {e}",
                level=NiveauAlerte.WARNING,
            )

    def _cmd_circuit_breaker(self) -> None:
        """Envoie l'état du circuit breaker."""
        if self.circuit_breaker is None:
            self.notifier.send(
                "❌ Circuit breaker non disponible",
                level=NiveauAlerte.WARNING,
            )
            return

        try:
            status = self.circuit_breaker.get_status()
            niveau = status.get("niveau", "?")
            emoji_niveau = {
                "AUCUN":   "✅",
                "WARNING": "⚠️",
                "PAUSE":   "🔴",
                "ARRETE":  "⛔",
            }.get(niveau, "❓")

            self.notifier.send(
                f"🔌 <b>Circuit Breaker</b>\n\n"
                f"Niveau: {emoji_niveau} {niveau}\n"
                f"Pertes consécutives: "
                f"{status.get('pertes_consecutives', 0)}\n"
                f"DD journalier: {status.get('drawdown_pct', 0):.2f}%\n"
                f"P&L jour: {status.get('pnl_usd', 0):+.2f}$\n"
                f"Trades bloqués: {status.get('trades_bloques', 0)}\n"
                f"Risk actuel: {status.get('risk_pct', 1.0):.1f}%",
                level=NiveauAlerte.INFO,
            )
        except Exception as e:
            self.notifier.send(
                f"❌ Erreur lecture CB : {e}",
                level=NiveauAlerte.WARNING,
            )

    def _cmd_stop(self) -> None:
        """Envoie un signal d'arrêt propre au processus principal."""
        self.notifier.send(
            "⛔ <b>Arrêt du bot demandé</b>\n"
            "Arrêt propre en cours...\n"
            "Les positions ouvertes sont conservées.",
            level=NiveauAlerte.WARNING,
        )
        logger.warning("Arrêt demandé via Telegram — envoi SIGTERM")
        # Envoie SIGTERM au processus pour déclencher le handler d'arrêt propre
        os.kill(os.getpid(), signal.SIGTERM)

    # ── Propriétés ─────────────────────────────────────────────────────────

    @property
    def est_en_pause_distante(self) -> bool:
        """True si le bot est en pause suite à une commande Telegram."""
        return self._en_pause_distante

    # Alias anglais
    @property
    def is_paused_by_remote(self) -> bool:
        return self._en_pause_distante


# Alias anglais
RemoteControl = TelecommandeBOT
