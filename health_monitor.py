"""
health_monitor.py — Monitoring de santé du bot SMC XAUUSD.

Surveille en continu l'état du bot, de la connexion MT5 et des ressources
système. Envoie des pings périodiques et des alertes différenciées via Telegram.
Écrit un heartbeat.json lu par le watchdog externe pour détecter les freezes.

Ce module est passif — il ne bloque jamais la boucle principale.
Toutes les erreurs sont catchées et loggées sans propagation.
"""

import json
import os
from datetime import datetime
from typing import Dict, List, Optional

from loguru import logger

from config import CONFIG
from telegram_notifier import (
    TelegramNotifier,
    NiveauAlerte,
    AlerteSante,
    SanteSysteme,
    StatutBot,
    StatutConnexionMT5,
)

# Alias anglais exportés pour compatibilité
AlertLevel = NiveauAlerte
BotStatus = StatutBot
MT5ConnectionStatus = StatutConnexionMT5
HealthAlert = AlerteSante
SystemHealth = SanteSysteme


class MoniteurSante:
    """
    Surveille en continu la santé du bot et de ses dépendances.

    Responsabilités :
    - Collecter l'état du système (MT5, compte, CPU/RAM/disque)
    - Évaluer les conditions d'alerte et notifier via Telegram
    - Écrire le heartbeat.json pour le watchdog externe
    - Générer le rapport de disponibilité journalier

    Intégration : appelé via APScheduler, jamais directement dans la boucle.
    """

    # ── Chemin heartbeat (surchargeable dans les tests) ──
    HEARTBEAT_PATH = "data/heartbeat.json"

    # ── Seuils d'alerte système ───────────────────────────────────────────
    CPU_WARNING_PCT      = 80.0
    CPU_CRITICAL_PCT     = 95.0
    RAM_WARNING_PCT      = 80.0
    RAM_CRITICAL_PCT     = 92.0
    DISK_WARNING_GO      = 2.0
    DISK_CRITICAL_GO     = 0.5
    MT5_PING_WARNING_MS  = 500
    MT5_PING_CRITICAL_MS = 2000

    def __init__(
        self,
        connecteur=None,
        notifier: Optional[TelegramNotifier] = None,
        config=None,
        trade_manager=None,
        circuit_breaker=None,
        daily_stats_tracker=None,
    ) -> None:
        self.connecteur = connecteur
        self.notifier = notifier or TelegramNotifier()
        self.config = config or CONFIG
        self.trade_manager = trade_manager
        self.circuit_breaker = circuit_breaker
        self.daily_stats_tracker = daily_stats_tracker

        self._heure_demarrage = datetime.utcnow()
        self._alertes_actives: Dict[str, AlerteSante] = {}
        self._nb_pings = 0
        self._dernier_ping: Optional[datetime] = None
        self._statut_bot = StatutBot.EN_COURS

        logger.info("MoniteurSante initialisé")

    # ── Ping périodique ────────────────────────────────────────────────────

    def send_health_ping(self) -> None:
        """
        Envoie le ping de santé.
        Appelé par APScheduler toutes les 5 minutes.
        """
        try:
            sante = self._collecter_donnees()
            self._ecrire_heartbeat(sante)
            self._evaluer_alertes(sante)

            succes = self.notifier.send_ping(sante)
            if succes:
                self._nb_pings += 1
                self._dernier_ping = datetime.utcnow()
                logger.debug(
                    f"Ping #{self._nb_pings} envoyé | "
                    f"Statut: {sante.statut_bot.value} | "
                    f"MT5: {sante.statut_mt5.value}"
                )
            else:
                logger.warning("Ping non envoyé — Telegram indisponible")
        except Exception as e:
            logger.error(f"Erreur send_health_ping : {e}")

    # ── Collecte des données de santé ─────────────────────────────────────

    def _collecter_donnees(self) -> SanteSysteme:
        """Collecte toutes les métriques de santé du système."""
        maintenant = datetime.utcnow()
        uptime = int((maintenant - self._heure_demarrage).total_seconds())

        # ── Ressources système (psutil) ────────────────────────────────────
        cpu_pct = 0.0
        ram_pct = 0.0
        disque_go = 10.0
        memoire_mo = 0.0

        try:
            import psutil
            cpu_pct = float(psutil.cpu_percent(interval=0.1))
            ram_pct = float(psutil.virtual_memory().percent)

            # Disque — utiliser / sur Linux/Mac, C: sur Windows
            try:
                disque_go = psutil.disk_usage("/").free / (1024 ** 3)
            except Exception:
                try:
                    disque_go = psutil.disk_usage("C:\\").free / (1024 ** 3)
                except Exception:
                    disque_go = 10.0

            # Mémoire du processus Python courant
            proc = psutil.Process(os.getpid())
            memoire_mo = float(proc.memory_info().rss / (1024 ** 2))
        except Exception as e:
            logger.debug(f"psutil non disponible : {e}")

        # ── État MT5 ───────────────────────────────────────────────────────
        statut_mt5 = StatutConnexionMT5.DECONNECTE
        ping_ms = None
        balance = 0.0
        equity = 0.0
        marge_libre = 0.0
        positions_ouvertes = 0
        serveur_mt5 = ""
        login_mt5 = 0

        try:
            import MetaTrader5 as mt5
            if mt5.terminal_info() is not None:
                debut_ping = datetime.utcnow()
                account = mt5.account_info()
                ping_ms = int(
                    (datetime.utcnow() - debut_ping).total_seconds() * 1000
                )

                if account is not None:
                    statut_mt5 = (
                        StatutConnexionMT5.DEGRADE
                        if ping_ms > self.MT5_PING_WARNING_MS
                        else StatutConnexionMT5.CONNECTE
                    )
                    balance = float(account.balance)
                    equity = float(account.equity)
                    marge_libre = float(account.margin_free)
                    login_mt5 = int(account.login)

                # Positions ouvertes sur le symbole
                symbole = getattr(self.config, "SYMBOLE", "XAUUSD")
                positions = mt5.positions_get(symbol=symbole)
                positions_ouvertes = len(positions) if positions else 0

                # Serveur
                info_terminal = mt5.terminal_info()
                if info_terminal:
                    serveur_mt5 = getattr(info_terminal, "server", "") or ""

        except Exception as e:
            logger.debug(f"Données MT5 non disponibles : {e}")

        # ── P&L et drawdown journaliers ────────────────────────────────────
        pnl_journalier = 0.0
        dd_journalier = 0.0

        if self.daily_stats_tracker is not None:
            try:
                stats = getattr(self.daily_stats_tracker, "stats", None)
                if stats is not None:
                    dd_journalier = float(
                        getattr(stats, "drawdown_pct", 0.0)
                    )
                    # P&L = balance actuelle - balance ouverture
                    bal_actuelle = float(
                        getattr(stats, "balance_actuelle", balance)
                    )
                    bal_ouverture = float(
                        getattr(stats, "balance_ouverture", balance)
                    )
                    pnl_journalier = bal_actuelle - bal_ouverture
            except Exception as e:
                logger.debug(f"Stats journalières non disponibles : {e}")

        # ── Circuit Breaker ────────────────────────────────────────────────
        niveau_cb = "AUCUN"
        if self.circuit_breaker is not None:
            try:
                status_cb = self.circuit_breaker.get_status()
                niveau_cb = status_cb.get("niveau", "AUCUN")
            except Exception:
                pass

        # ── Dernier trade connu ────────────────────────────────────────────
        dernier_trade_ouvert = None
        if self.trade_manager is not None:
            try:
                trade_actif = getattr(self.trade_manager, "_trade_actif", None)
                if trade_actif and hasattr(trade_actif, "heure_entree"):
                    dernier_trade_ouvert = trade_actif.heure_entree
            except Exception:
                pass

        return SanteSysteme(
            timestamp_utc=maintenant,
            statut_bot=self._statut_bot,
            uptime_secondes=uptime,
            derniere_evaluation=None,
            dernier_trade_ouvert=dernier_trade_ouvert,
            dernier_trade_ferme=None,
            statut_mt5=statut_mt5,
            ping_mt5_ms=ping_ms,
            serveur_mt5=serveur_mt5,
            login_mt5=login_mt5,
            balance=balance,
            equity=equity,
            marge_libre=marge_libre,
            positions_ouvertes=positions_ouvertes,
            pnl_journalier_usd=pnl_journalier,
            drawdown_journalier_pct=dd_journalier,
            circuit_breaker_niveau=niveau_cb,
            cpu_pct=cpu_pct,
            ram_pct=ram_pct,
            disque_libre_go=disque_go,
            memoire_python_mo=memoire_mo,
            alertes_actives=list(self._alertes_actives.keys()),
        )

    # ── Heartbeat ──────────────────────────────────────────────────────────

    def _ecrire_heartbeat(self, sante: SanteSysteme) -> None:
        """
        Écrit le fichier heartbeat.json toutes les 5 minutes.
        Lu par le watchdog externe pour détecter les freezes.
        """
        try:
            os.makedirs(os.path.dirname(self.HEARTBEAT_PATH) or ".", exist_ok=True)
            heartbeat = {
                "timestamp_utc": sante.timestamp_utc.isoformat(),
                "bot_status": sante.statut_bot.value,
                "mt5_status": sante.statut_mt5.value,
                "uptime_seconds": sante.uptime_secondes,
                "open_positions": sante.positions_ouvertes,
                "daily_pnl_usd": sante.pnl_journalier_usd,
                "ping_count": self._nb_pings,
            }
            with open(self.HEARTBEAT_PATH, "w", encoding="utf-8") as f:
                json.dump(heartbeat, f, indent=2, default=str)
        except Exception as e:
            logger.debug(f"Erreur écriture heartbeat : {e}")

    # ── Évaluation des alertes ─────────────────────────────────────────────

    def _evaluer_alertes(self, sante: SanteSysteme) -> None:
        """Évalue toutes les conditions d'alerte."""
        self._verifier_connexion_mt5(sante)
        self._verifier_ressources_systeme(sante)
        self._verifier_sante_compte(sante)

    def _verifier_connexion_mt5(self, sante: SanteSysteme) -> None:
        """Vérifie l'état de la connexion MT5."""
        alert_id = "mt5_deconnecte"

        if sante.statut_mt5 == StatutConnexionMT5.DECONNECTE:
            if alert_id not in self._alertes_actives:
                alerte = AlerteSante(
                    alert_id=alert_id,
                    level=NiveauAlerte.CRITICAL,
                    category="mt5",
                    title="🔴 MT5 DÉCONNECTÉ",
                    message=(
                        "La connexion à MetaTrader 5 est perdue.\n"
                        "Tentative de reconnexion automatique.\n"
                        "Aucun nouveau trade ne sera ouvert.\n"
                        "⚠️ Vérifier les positions manuellement."
                    ),
                    timestamp_utc=datetime.utcnow(),
                )
                self._alertes_actives[alert_id] = alerte
                self.notifier.send_alert(alerte)
                self._tenter_reconnexion_mt5()

        elif sante.statut_mt5 == StatutConnexionMT5.DEGRADE:
            deg_id = "mt5_degrade"
            if (deg_id not in self._alertes_actives
                    and sante.ping_mt5_ms
                    and sante.ping_mt5_ms > self.MT5_PING_WARNING_MS):
                alerte = AlerteSante(
                    alert_id=deg_id,
                    level=NiveauAlerte.WARNING,
                    category="mt5",
                    title="⚠️ MT5 DÉGRADÉ",
                    message=(
                        f"Latence MT5 élevée : {sante.ping_mt5_ms}ms\n"
                        f"(seuil normal : {self.MT5_PING_WARNING_MS}ms)"
                    ),
                    timestamp_utc=datetime.utcnow(),
                )
                self._alertes_actives[deg_id] = alerte
                self.notifier.send_alert(alerte)
        else:
            # MT5 connecté → résoudre les alertes MT5
            for aid in [alert_id, "mt5_degrade"]:
                if aid in self._alertes_actives:
                    self._resoudre_alerte(aid, "Connexion MT5 rétablie")

    def _verifier_ressources_systeme(self, sante: SanteSysteme) -> None:
        """Vérifie les ressources système."""
        # CPU
        if sante.cpu_pct > self.CPU_CRITICAL_PCT:
            self._lever_alerte(
                "cpu_critique", NiveauAlerte.CRITICAL, "system",
                "🔴 CPU CRITIQUE",
                f"Utilisation CPU : {sante.cpu_pct:.0f}% "
                f"(seuil: {self.CPU_CRITICAL_PCT}%)\n"
                "Le bot peut fonctionner de façon erratique."
            )
        elif sante.cpu_pct > self.CPU_WARNING_PCT:
            self._lever_alerte(
                "cpu_warning", NiveauAlerte.WARNING, "system",
                "⚠️ CPU ÉLEVÉ",
                f"Utilisation CPU : {sante.cpu_pct:.0f}%"
            )
        else:
            self._resoudre_alerte("cpu_critique", "CPU revenu à la normale")
            self._resoudre_alerte("cpu_warning", "CPU revenu à la normale")

        # RAM
        if sante.ram_pct > self.RAM_CRITICAL_PCT:
            self._lever_alerte(
                "ram_critique", NiveauAlerte.CRITICAL, "system",
                "🔴 RAM CRITIQUE",
                f"RAM : {sante.ram_pct:.0f}%\n"
                "Risque de crash par manque de mémoire."
            )
        elif sante.ram_pct > self.RAM_WARNING_PCT:
            self._lever_alerte(
                "ram_warning", NiveauAlerte.WARNING, "system",
                "⚠️ RAM ÉLEVÉE",
                f"RAM : {sante.ram_pct:.0f}%"
            )
        else:
            self._resoudre_alerte("ram_critique", "RAM revenue à la normale")
            self._resoudre_alerte("ram_warning", "RAM revenue à la normale")

        # Disque
        if sante.disque_libre_go < self.DISK_CRITICAL_GO:
            self._lever_alerte(
                "disque_critique", NiveauAlerte.CRITICAL, "system",
                "🔴 DISQUE PLEIN",
                f"Espace libre : {sante.disque_libre_go:.2f} Go\n"
                "Libérer de l'espace immédiatement."
            )
        elif sante.disque_libre_go < self.DISK_WARNING_GO:
            self._lever_alerte(
                "disque_warning", NiveauAlerte.WARNING, "system",
                "⚠️ DISQUE FAIBLE",
                f"Espace libre : {sante.disque_libre_go:.2f} Go"
            )

    def _verifier_sante_compte(self, sante: SanteSysteme) -> None:
        """Vérifie la santé du compte de trading."""
        max_dd = getattr(self.config, "MAX_DRAWDOWN_JOURNALIER_PCT", 3.0)

        if sante.drawdown_journalier_pct > max_dd * 0.80:
            pct_limite = sante.drawdown_journalier_pct / max_dd * 100
            self._lever_alerte(
                "dd_warning", NiveauAlerte.WARNING, "account",
                "⚠️ DRAWDOWN ÉLEVÉ",
                f"DD journalier : {sante.drawdown_journalier_pct:.2f}%\n"
                f"Limite : {max_dd}%\n"
                f"À {pct_limite:.1f}% de la limite."
            )
        else:
            self._resoudre_alerte("dd_warning", "Drawdown revenu sous le seuil")

        if (sante.balance > 0
                and sante.marge_libre / sante.balance < 0.20):
            pct_marge = sante.marge_libre / sante.balance * 100
            self._lever_alerte(
                "marge_warning", NiveauAlerte.WARNING, "account",
                "⚠️ MARGE LIBRE FAIBLE",
                f"Marge libre : {sante.marge_libre:.2f}$ "
                f"({pct_marge:.1f}% du capital)"
            )

    # ── Reconnexion MT5 ────────────────────────────────────────────────────

    def _tenter_reconnexion_mt5(self, max_tentatives: int = 5) -> bool:
        """Tente de reconnecter MT5 automatiquement avec backoff."""
        self._statut_bot = StatutBot.RECONNEXION
        logger.warning("Tentative de reconnexion MT5...")

        for tentative in range(1, max_tentatives + 1):
            try:
                import MetaTrader5 as mt5
                import time
                mt5.shutdown()
                time.sleep(3)

                if self.connecteur is not None:
                    succes = self.connecteur.connecter(max_tentatives=1)
                else:
                    succes = bool(mt5.initialize())

                if succes:
                    self._statut_bot = StatutBot.EN_COURS
                    self.notifier.send(
                        f"✅ <b>MT5 reconnecté</b>\n"
                        f"Succès après {tentative} tentative(s).",
                        level=NiveauAlerte.RECOVERY
                    )
                    logger.success(f"MT5 reconnecté (tentative {tentative})")
                    return True

            except Exception as e:
                logger.error(f"Tentative reconnexion {tentative} : {e}")

            import time
            time.sleep(tentative * 30)

        self._statut_bot = StatutBot.ERREUR
        self.notifier.send(
            f"🔴 <b>RECONNEXION MT5 ÉCHOUÉE</b>\n"
            f"{max_tentatives} tentatives sans succès.\n"
            f"⚠️ Intervention manuelle requise.",
            level=NiveauAlerte.CRITICAL
        )
        return False

    # ── Gestion des alertes ────────────────────────────────────────────────

    def _lever_alerte(
        self,
        alert_id: str,
        level: NiveauAlerte,
        category: str,
        title: str,
        message: str,
    ) -> None:
        """Active une nouvelle alerte si elle n'est pas déjà active."""
        if alert_id not in self._alertes_actives:
            alerte = AlerteSante(
                alert_id=alert_id,
                level=level,
                category=category,
                title=title,
                message=message,
                timestamp_utc=datetime.utcnow(),
            )
            self._alertes_actives[alert_id] = alerte
            self.notifier.send_alert(alerte)
            logger.warning(f"Alerte levée : [{alert_id}] {title}")

    def _resoudre_alerte(self, alert_id: str, resolution: str = "") -> None:
        """Marque une alerte comme résolue et notifie."""
        if alert_id in self._alertes_actives:
            alerte = self._alertes_actives.pop(alert_id)
            alerte.resolved = True
            alerte.resolved_at = datetime.utcnow()
            alerte.resolution_message = resolution

            duree_min = int(
                (datetime.utcnow() - alerte.timestamp_utc).total_seconds() / 60
            )
            self.notifier.send(
                f"✅ <b>Alerte résolue : {alerte.title}</b>\n"
                f"{resolution}\n"
                f"Durée: {duree_min}min",
                level=NiveauAlerte.RECOVERY
            )
            logger.info(f"Alerte résolue : [{alert_id}] {resolution}")

    # ── Interface publique ─────────────────────────────────────────────────

    def definir_statut_bot(self, statut: StatutBot) -> None:
        """Met à jour le statut opérationnel du bot."""
        if statut != self._statut_bot:
            ancien = self._statut_bot
            self._statut_bot = statut
            logger.info(f"Statut bot : {ancien.value} → {statut.value}")

    # Alias anglais
    def set_bot_status(self, status: StatutBot) -> None:
        self.definir_statut_bot(status)

    def get_active_alerts(self) -> List[AlerteSante]:
        return list(self._alertes_actives.values())

    # ── Rapport uptime journalier ──────────────────────────────────────────

    def generer_rapport_uptime(self) -> str:
        """
        Génère et envoie le rapport de disponibilité journalier.
        Appelé par APScheduler chaque jour à 23h55 UTC.
        """
        maintenant = datetime.utcnow()
        uptime_sec = int((maintenant - self._heure_demarrage).total_seconds())
        uptime_pct = min(100.0, uptime_sec / 86400 * 100)

        nb_critiques = sum(
            1 for a in self._alertes_actives.values()
            if a.level == NiveauAlerte.CRITICAL
        )

        message = (
            f"📊 <b>Disponibilité journalière — "
            f"{maintenant.strftime('%Y-%m-%d')}</b>\n\n"
            f"⏱️ Uptime: {uptime_pct:.1f}% "
            f"({uptime_sec // 3600}h{(uptime_sec % 3600) // 60:02d}m)\n"
            f"📡 Pings envoyés : {self._nb_pings}\n"
            f"🔔 Alertes actives : {len(self._alertes_actives)}\n"
            f"🔴 Alertes critiques : {nb_critiques}"
        )

        self.notifier.send(message, level=NiveauAlerte.INFO)
        logger.info(
            f"Rapport uptime : {uptime_pct:.1f}% | {self._nb_pings} pings"
        )
        return message

    # Alias anglais
    def generate_uptime_report(self) -> str:
        return self.generer_rapport_uptime()


# Alias anglais
HealthMonitor = MoniteurSante
