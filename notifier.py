"""
notifier.py — Alertes Telegram (optionnel).
Activé uniquement si TELEGRAM_TOKEN et TELEGRAM_CHAT_ID sont définis dans .env.
"""

import os
from typing import Optional
from loguru import logger

try:
    import requests
    REQUESTS_DISPONIBLE = True
except ImportError:
    REQUESTS_DISPONIBLE = False


class NotificateurTelegram:
    """Envoie des alertes vers Telegram via l'API Bot."""

    def __init__(self) -> None:
        self.token: str = os.getenv("TELEGRAM_TOKEN", "")
        self.chat_id: str = os.getenv("TELEGRAM_CHAT_ID", "")
        self.actif: bool = bool(self.token and self.chat_id and REQUESTS_DISPONIBLE)

        if self.actif:
            logger.info("Notificateur Telegram activé")
        else:
            logger.debug(
                "Notificateur Telegram inactif "
                "(TELEGRAM_TOKEN ou TELEGRAM_CHAT_ID manquant, ou 'requests' absent)"
            )

    def envoyer(self, message: str, silencieux: bool = False) -> bool:
        """
        Envoie un message Telegram.

        Args:
            message: Texte du message (supporte le Markdown Telegram).
            silencieux: Si True, la notification n'émet pas de son sur le téléphone.

        Returns:
            True si envoi réussi.
        """
        if not self.actif:
            return False

        url = f"https://api.telegram.org/bot{self.token}/sendMessage"
        payload = {
            "chat_id": self.chat_id,
            "text": message,
            "parse_mode": "Markdown",
            "disable_notification": silencieux,
        }
        try:
            resp = requests.post(url, json=payload, timeout=10)
            if resp.status_code == 200:
                return True
            logger.warning(f"Telegram: réponse {resp.status_code} — {resp.text[:200]}")
            return False
        except Exception as e:
            logger.error(f"Erreur envoi Telegram: {e}")
            return False

    def alerte_trade_ouvert(
        self,
        direction: str,
        symbole: str,
        volume: float,
        prix_entree: float,
        sl: float,
        tp2: float,
    ) -> None:
        """Alerte lors de l'ouverture d'un trade."""
        emoji = "🟢" if direction == "LONG" else "🔴"
        msg = (
            f"{emoji} *TRADE OUVERT — {symbole}*\n"
            f"Direction: `{direction}`\n"
            f"Volume: `{volume} lots`\n"
            f"Entrée: `{prix_entree:.5f}`\n"
            f"Stop Loss: `{sl:.5f}`\n"
            f"Take Profit 2: `{tp2:.5f}`"
        )
        self.envoyer(msg)

    def alerte_trade_ferme(
        self,
        direction: str,
        symbole: str,
        profit: float,
        r_final: float,
    ) -> None:
        """Alerte lors de la fermeture d'un trade."""
        if profit >= 0:
            emoji = "✅"
            resultat = "GAGNANT"
        else:
            emoji = "❌"
            resultat = "PERDANT"

        signe = "+" if profit >= 0 else ""
        msg = (
            f"{emoji} *TRADE FERMÉ — {symbole}*\n"
            f"Résultat: `{resultat}`\n"
            f"Direction: `{direction}`\n"
            f"P&L: `{signe}{profit:.2f}$`\n"
            f"R final: `{r_final:.2f}R`"
        )
        self.envoyer(msg)

    def alerte_drawdown(self, drawdown_pct: float, type_dd: str = "journalier") -> None:
        """Alerte quand le drawdown dépasse un seuil."""
        msg = (
            f"⚠️ *ALERTE DRAWDOWN — {type_dd.upper()}*\n"
            f"Drawdown actuel: `{drawdown_pct:.2f}%`"
        )
        self.envoyer(msg)

    def alerte_circuit_breaker(self, nb_pertes: int, pause_heures: int) -> None:
        """Alerte lors de l'activation du circuit breaker."""
        msg = (
            f"🛑 *CIRCUIT BREAKER ACTIVÉ*\n"
            f"Pertes consécutives: `{nb_pertes}`\n"
            f"Pause: `{pause_heures}h`"
        )
        self.envoyer(msg)

    def alerte_arret_urgence(self, drawdown_total: float) -> None:
        """Alerte critique lors de l'arrêt d'urgence du bot."""
        msg = (
            f"🚨 *ARRÊT D'URGENCE DU BOT*\n"
            f"Drawdown total: `{drawdown_total:.2f}%`\n"
            f"⚠️ Intervention manuelle requise !"
        )
        self.envoyer(msg, silencieux=False)

    def alerte_erreur(self, erreur: str) -> None:
        """Alerte lors d'une erreur critique."""
        msg = f"❗ *ERREUR BOT*\n```\n{erreur[:500]}\n```"
        self.envoyer(msg)
