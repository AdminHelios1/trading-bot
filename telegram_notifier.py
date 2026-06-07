"""
telegram_notifier.py — Client Telegram enrichi pour le monitoring du bot.

Enrichit le NotificateurTelegram existant (notifier.py) avec :
- Niveaux d'alerte (INFO / WARNING / CRITICAL / RECOVERY)
- Rate limiting anti-spam (2s minimum entre messages)
- Retry automatique avec backoff exponentiel
- Format HTML riche pour les pings de santé
- Validation des credentials au démarrage

Ce module coexiste avec notifier.py — il ne le remplace pas.
"""

import os
import time
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Dict, List, Optional

from loguru import logger

try:
    import requests as _requests
    REQUESTS_DISPONIBLE = True
except ImportError:
    REQUESTS_DISPONIBLE = False


# ── Niveaux d'alerte ───────────────────────────────────────────────────────

class NiveauAlerte(Enum):
    """Niveau de gravité d'une alerte de monitoring."""
    INFO     = "ℹ️"    # État normal — ping périodique (silencieux)
    WARNING  = "⚠️"   # Anomalie — surveillance renforcée (notification)
    CRITICAL = "🔴"   # Problème sérieux — intervention requise (notification)
    RECOVERY = "✅"   # Retour à la normale après anomalie (notification)


# Alias anglais
AlertLevel = NiveauAlerte


# ── Dataclasses de monitoring ──────────────────────────────────────────────

class StatutBot(Enum):
    """Statut opérationnel du bot."""
    EN_COURS      = "EN_COURS"
    EN_PAUSE      = "EN_PAUSE"
    EN_ATTENTE    = "EN_ATTENTE"
    ERREUR        = "ERREUR"
    ARRETE        = "ARRÊTÉ"
    RECONNEXION   = "RECONNEXION"


class StatutConnexionMT5(Enum):
    """État de la connexion à MetaTrader 5."""
    CONNECTE      = "CONNECTÉ"
    DECONNECTE    = "DÉCONNECTÉ"
    DEGRADE       = "DÉGRADÉ"
    RECONNEXION   = "RECONNEXION"


# Aliases anglais
BotStatus = StatutBot
MT5ConnectionStatus = StatutConnexionMT5


@dataclass
class SanteSysteme:
    """État de santé complet du système à un instant T."""
    timestamp_utc: datetime

    # Bot
    statut_bot: StatutBot
    uptime_secondes: int
    derniere_evaluation: Optional[datetime]
    dernier_trade_ouvert: Optional[datetime]
    dernier_trade_ferme: Optional[datetime]

    # MT5
    statut_mt5: StatutConnexionMT5
    ping_mt5_ms: Optional[int]
    serveur_mt5: str
    login_mt5: int

    # Compte
    balance: float
    equity: float
    marge_libre: float
    positions_ouvertes: int
    pnl_journalier_usd: float
    drawdown_journalier_pct: float
    circuit_breaker_niveau: str

    # Système
    cpu_pct: float
    ram_pct: float
    disque_libre_go: float
    memoire_python_mo: float

    # Alertes actives
    alertes_actives: List[str]

    # Aliases anglais
    @property
    def bot_status(self) -> StatutBot: return self.statut_bot
    @property
    def mt5_status(self) -> StatutConnexionMT5: return self.statut_mt5
    @property
    def mt5_ping_ms(self) -> Optional[int]: return self.ping_mt5_ms
    @property
    def mt5_server(self) -> str: return self.serveur_mt5
    @property
    def mt5_account_login(self) -> int: return self.login_mt5
    @property
    def account_balance(self) -> float: return self.balance
    @property
    def account_equity(self) -> float: return self.equity
    @property
    def account_margin_free(self) -> float: return self.marge_libre
    @property
    def open_positions_count(self) -> int: return self.positions_ouvertes
    @property
    def daily_pnl_usd(self) -> float: return self.pnl_journalier_usd
    @property
    def daily_drawdown_pct(self) -> float: return self.drawdown_journalier_pct
    @property
    def circuit_breaker_level(self) -> str: return self.circuit_breaker_niveau
    @property
    def cpu_usage_pct(self) -> float: return self.cpu_pct
    @property
    def ram_usage_pct(self) -> float: return self.ram_pct
    @property
    def disk_free_gb(self) -> float: return self.disque_libre_go
    @property
    def python_memory_mb(self) -> float: return self.memoire_python_mo
    @property
    def active_alerts(self) -> List[str]: return self.alertes_actives
    @property
    def uptime_seconds(self) -> int: return self.uptime_secondes


@dataclass
class AlerteSante:
    """Alerte générée par le système de monitoring."""
    alert_id: str
    level: NiveauAlerte
    category: str           # "mt5", "account", "system", "bot", "trade"
    title: str
    message: str
    timestamp_utc: datetime
    resolved: bool = False
    resolved_at: Optional[datetime] = None
    resolution_message: str = ""


# Alias anglais
SystemHealth = SanteSysteme
HealthAlert = AlerteSante


# ── Client Telegram enrichi ────────────────────────────────────────────────

class TelegramNotifier:
    """
    Client Telegram avec mise en forme riche et protection anti-spam.

    Utilise l'API Telegram Bot via requests (pas de dépendance
    python-telegram-bot pour rester léger).

    Règles d'or :
    - Ne lève JAMAIS d'exception vers le bot principal
    - Rate limiting : minimum 2s entre les messages
    - Retry automatique sur timeout/erreur réseau
    - Messages INFO = silencieux (pas de son sur téléphone)
    """

    URL_BASE = "https://api.telegram.org/bot{token}"
    LONGUEUR_MAX = 4096       # Limite Telegram
    MIN_INTERVALLE_SEC = 2    # Anti-spam
    MAX_TENTATIVES = 3

    def __init__(
        self,
        token: str = "",
        chat_id: str = "",
        config=None,
    ) -> None:
        # Charger le .env si pas encore chargé
        try:
            from dotenv import load_dotenv
            load_dotenv()
        except Exception:
            pass
        self.token = token or os.getenv("TELEGRAM_TOKEN", "")
        self.chat_id = chat_id or os.getenv("TELEGRAM_CHAT_ID", "")
        self.config = config

        self._dernier_envoi: Optional[datetime] = None
        self._nb_envoyes_auj: int = 0
        self._nb_erreurs: int = 0
        self._disponible: bool = False

        if self.token and self.chat_id and REQUESTS_DISPONIBLE:
            self._valider_credentials()
        else:
            logger.debug(
                "TelegramNotifier inactif — token/chat_id manquant "
                "ou 'requests' absent"
            )

    def _valider_credentials(self) -> None:
        """Vérifie que le bot Telegram est accessible au démarrage."""
        try:
            url = self.URL_BASE.format(token=self.token) + "/getMe"
            resp = _requests.get(url, timeout=5)
            if resp.status_code == 200:
                nom_bot = resp.json().get("result", {}).get("username", "?")
                self._disponible = True
                logger.info(f"TelegramNotifier connecté — @{nom_bot}")
            else:
                logger.warning(
                    f"TelegramNotifier : réponse {resp.status_code}"
                )
        except Exception as e:
            logger.warning(f"TelegramNotifier non accessible : {e}")

    # ── Envoi principal ────────────────────────────────────────────────────

    def send(
        self,
        message: str,
        level: NiveauAlerte = NiveauAlerte.INFO,
        parse_mode: str = "HTML",
    ) -> bool:
        """
        Envoie un message Telegram formaté selon le niveau d'alerte.

        Les messages INFO sont silencieux (pas de son).
        WARNING / CRITICAL / RECOVERY déclenchent une notification sonore.

        Returns:
            True si envoi réussi.
        """
        if not self._disponible or not REQUESTS_DISPONIBLE:
            logger.debug(f"[TELEGRAM] {level.value} {message[:80]}")
            return False

        # Rate limiting
        if self._dernier_envoi is not None:
            ecoule = (datetime.utcnow() - self._dernier_envoi).total_seconds()
            if ecoule < self.MIN_INTERVALLE_SEC:
                time.sleep(self.MIN_INTERVALLE_SEC - ecoule)

        # Formatage avec emoji de niveau
        msg_formate = f"{level.value} {message}"

        # Tronquer si trop long
        if len(msg_formate) > self.LONGUEUR_MAX:
            msg_formate = msg_formate[:self.LONGUEUR_MAX - 20] + "\n...[tronqué]"

        url = self.URL_BASE.format(token=self.token) + "/sendMessage"
        payload = {
            "chat_id": self.chat_id,
            "text": msg_formate,
            "parse_mode": parse_mode,
            "disable_notification": (level == NiveauAlerte.INFO),
        }

        for tentative in range(1, self.MAX_TENTATIVES + 1):
            try:
                resp = _requests.post(url, json=payload, timeout=10)
                if resp.status_code == 200:
                    self._dernier_envoi = datetime.utcnow()
                    self._nb_envoyes_auj += 1
                    self._nb_erreurs = 0
                    return True

                if resp.status_code == 429:
                    retry_after = (
                        resp.json().get("parameters", {}).get("retry_after", 30)
                    )
                    logger.warning(
                        f"Telegram rate limit — attente {retry_after}s"
                    )
                    time.sleep(retry_after)
                else:
                    logger.warning(
                        f"Telegram {resp.status_code} (tentative {tentative})"
                    )
            except Exception as e:
                logger.warning(f"Telegram erreur tentative {tentative} : {e}")

            if tentative < self.MAX_TENTATIVES:
                time.sleep(tentative * 3)

        self._nb_erreurs += 1
        if self._nb_erreurs >= 5:
            self._disponible = False
            logger.error(
                "TelegramNotifier marqué indisponible après 5 erreurs"
            )
        return False

    # ── Messages spécialisés ───────────────────────────────────────────────

    def send_ping(self, health: SanteSysteme) -> bool:
        """Envoie le ping de santé périodique compact."""
        emoji_statut = {
            StatutBot.EN_COURS:    "🟢",
            StatutBot.EN_PAUSE:    "⏸️",
            StatutBot.EN_ATTENTE:  "🕐",
            StatutBot.ERREUR:      "❌",
            StatutBot.ARRETE:      "⛔",
            StatutBot.RECONNEXION: "🔄",
        }.get(health.statut_bot, "❓")

        emoji_mt5 = (
            "✅" if health.statut_mt5 == StatutConnexionMT5.CONNECTE else "❌"
        )

        pos_str = (
            f"📊 {health.positions_ouvertes} position(s) ouverte(s)"
            if health.positions_ouvertes > 0
            else "📊 Aucune position"
        )

        pnl_sign = "+" if health.pnl_journalier_usd >= 0 else ""
        pnl_emoji = "📈" if health.pnl_journalier_usd >= 0 else "📉"
        pnl_str = f"{pnl_emoji} P&L jour: {pnl_sign}{health.pnl_journalier_usd:.2f}$"

        dd_str = (
            f"\n📉 DD: {health.drawdown_journalier_pct:.2f}%"
            if health.drawdown_journalier_pct > 0 else ""
        )

        h_up = health.uptime_secondes // 3600
        m_up = (health.uptime_secondes % 3600) // 60

        ping_ms_str = (
            f" ({health.ping_mt5_ms}ms)" if health.ping_mt5_ms else ""
        )

        message = (
            f"<b>🤖 SMC Bot XAUUSD — Ping</b>\n"
            f"{emoji_statut} Statut: <b>{health.statut_bot.value}</b>\n"
            f"{emoji_mt5} MT5: {health.statut_mt5.value}{ping_ms_str}\n"
            f"{pos_str}\n"
            f"{pnl_str}{dd_str}\n"
            f"⚡ CB: {health.circuit_breaker_niveau}\n"
            f"💻 CPU: {health.cpu_pct:.0f}% | "
            f"RAM: {health.ram_pct:.0f}%\n"
            f"⏱️ Uptime: {h_up}h{m_up:02d}m\n"
            f"🕐 {health.timestamp_utc.strftime('%H:%M UTC')}"
        )
        return self.send(message, level=NiveauAlerte.INFO)

    def send_alert(self, alert: AlerteSante) -> bool:
        """Envoie une alerte formatée."""
        message = (
            f"<b>{alert.title}</b>\n\n"
            f"{alert.message}\n\n"
            f"🕐 {alert.timestamp_utc.strftime('%Y-%m-%d %H:%M UTC')}\n"
            f"🏷️ Catégorie: {alert.category}"
        )
        return self.send(message, level=alert.level)

    def send_trade_notification(
        self,
        trade_type: str,
        details: dict,
    ) -> bool:
        """
        Envoie une notification de trade.

        trade_type : "OPEN", "CLOSE", "TP1", "ADDON"
        """
        if trade_type == "OPEN":
            dir_emoji = "📈" if details.get("direction") == "LONG" else "📉"
            confluences = ", ".join(details.get("confluences", []))
            message = (
                f"<b>{dir_emoji} Trade ouvert — "
                f"{details.get('symbol', '?')}</b>\n\n"
                f"Direction: <b>{details.get('direction', '?')}</b>\n"
                f"Entrée: {details.get('entry', 0):.2f}\n"
                f"SL: {details.get('sl', 0):.2f} | "
                f"TP1: {details.get('tp1', 0):.2f}\n"
                f"Lots: {details.get('lots', 0)} | "
                f"Risque: {details.get('risk_pct', 0)}%\n"
                f"OB Score: {details.get('ob_score', 0)}/100 "
                f"[{details.get('ob_strength', '?')}]\n"
                f"Confluences: {confluences}"
            )

        elif trade_type == "CLOSE":
            pnl = details.get("pnl", 0)
            r   = details.get("r", 0)
            emoji = "🟢" if pnl > 0 else ("⬜" if abs(pnl) < 1 else "🔴")
            pnl_sign = "+" if pnl >= 0 else ""
            message = (
                f"<b>{emoji} Trade fermé — "
                f"{details.get('symbol', '?')}</b>\n\n"
                f"Direction: {details.get('direction', '?')}\n"
                f"R réalisé: <b>{r:+.2f}R</b>\n"
                f"P&L: <b>{pnl_sign}{pnl:.2f}$</b>\n"
                f"Raison: {details.get('reason', '?')}\n"
                f"TP1: {'✅' if details.get('tp1_hit') else '❌'} | "
                f"TP2: {'✅' if details.get('tp2_hit') else '❌'} | "
                f"TP3: {'✅' if details.get('tp3_hit') else '❌'}"
            )

        elif trade_type == "TP1":
            message = (
                f"<b>🎯 TP1 atteint — {details.get('symbol', '?')}</b>\n"
                f"50% fermé @ {details.get('price', 0):.2f}\n"
                f"SL → Breakeven | P&L partiel: +${details.get('pnl', 0):.2f}\n"
                f"{'🔺 Add-on évalué' if details.get('addon_evaluated') else ''}"
            )

        elif trade_type == "ADDON":
            message = (
                f"<b>🔺 Add-on ouvert — {details.get('symbol', '?')}</b>\n"
                f"Entry: {details.get('entry', 0):.2f} | "
                f"SL: {details.get('sl', 0):.2f} (BE parent)\n"
                f"Lots: {details.get('lots', 0)} (0.5% risque)"
            )

        else:
            message = f"Événement trade: {trade_type}"

        return self.send(message, level=NiveauAlerte.INFO)

    def get_updates(self, offset: int = 0) -> list:
        """Récupère les nouvelles commandes envoyées au bot Telegram."""
        if not self._disponible or not REQUESTS_DISPONIBLE:
            return []
        try:
            url = self.URL_BASE.format(token=self.token) + "/getUpdates"
            resp = _requests.get(
                url,
                params={"offset": offset, "timeout": 2},
                timeout=5,
            )
            if resp.status_code == 200:
                return resp.json().get("result", [])
        except Exception:
            pass
        return []

    # ── Propriétés ────────────────────────────────────────────────────────

    @property
    def est_disponible(self) -> bool:
        return self._disponible

    @property
    def is_available(self) -> bool:
        return self._disponible
