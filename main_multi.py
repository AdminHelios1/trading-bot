"""
main_multi.py — Point d'entrée du bot SMC multi-actif.

Lance simultanément 4 moteurs de trading indépendants (XAUUSD, NAS100, SP500, WTI)
supervisés par un Portfolio Risk Manager central qui limite l'exposition à 5%.

Usage :
    python main_multi.py --mode paper
    python main_multi.py --mode live --symbols XAUUSD NAS100
    python main_multi.py --mode paper --symbols XAUUSD WTI
"""

import argparse
import os
import signal
import sys
import time
from datetime import datetime, timedelta, timezone
from typing import Dict, List

from loguru import logger


def main() -> None:
    """Point d'entrée principal du bot multi-actif."""
    parser = argparse.ArgumentParser(
        description="SMC Trading Bot Multi-Actif XAUUSD/NAS100/SP500/WTI"
    )
    parser.add_argument(
        "--mode",
        choices=["live", "paper", "backtest"],
        default="paper",
        help="Mode d'exécution (défaut: paper)",
    )
    parser.add_argument(
        "--symbols",
        nargs="+",
        default=["XAUUSD", "NAS100", "SP500", "WTI"],
        choices=["XAUUSD", "NAS100", "SP500", "WTI"],
        help="Actifs à trader (défaut: tous les 4)",
    )
    args = parser.parse_args()

    logger.info(
        f"{'='*60}\n"
        f"SMC Bot Multi-Actif démarré\n"
        f"Mode: {args.mode.upper()} | "
        f"Actifs: {', '.join(args.symbols)}\n"
        f"{'='*60}"
    )

    # ── Imports dynamiques ─────────────────────────────────────────────────
    try:
        from configs.config_base import ConfigBase
        from configs.config_xauusd import ConfigXAUUSD
        from configs.config_nas100 import ConfigNAS100
        from configs.config_sp500 import ConfigSP500
        from configs.config_wti import ConfigWTI
        from session_manager import GestionnaireSession
        from portfolio_risk_manager import GestionnaireRisquePortefeuille
        from asset_engine import MoteurActif
        from telegram_notifier import TelegramNotifier, NiveauAlerte, StatutBot
        from health_monitor import MoniteurSante
        from logger_config import configurer_logger
    except ImportError as e:
        print(f"Erreur import module : {e}")
        sys.exit(1)

    configurer_logger()

    # ── Connexion MT5 partagée ─────────────────────────────────────────────
    try:
        from mt5_connector import ConnecteurMT5
        connecteur = ConnecteurMT5()
        if args.mode == "live":
            connecteur.connecter()
        else:
            try:
                connecteur.connecter()
            except ConnectionError:
                logger.warning(
                    "Mode paper : MT5 non disponible — simulation dégradée"
                )
    except Exception as e:
        logger.error(f"Erreur connexion MT5 : {e}")
        connecteur = None

    # ── Notifier Telegram ──────────────────────────────────────────────────
    notifier = TelegramNotifier(
        token=os.getenv("TELEGRAM_TOKEN", ""),
        chat_id=os.getenv("TELEGRAM_CHAT_ID", ""),
    )

    # ── Mapping configs par actif ──────────────────────────────────────────
    CONFIGS_DISPONIBLES = {
        "XAUUSD": ConfigXAUUSD(),
        "NAS100": ConfigNAS100(),
        "SP500":  ConfigSP500(),
        "WTI":    ConfigWTI(),
    }

    configs_actifs = {s: CONFIGS_DISPONIBLES[s] for s in args.symbols}

    # ── Portfolio Risk Manager central ─────────────────────────────────────
    portfolio_rm = GestionnaireRisquePortefeuille(
        connecteur=connecteur,
        configs=configs_actifs,
        notifier=notifier,
    )
    portfolio_rm.initialiser_equity_journaliere()

    # ── Session Manager partagé ────────────────────────────────────────────
    session_manager = GestionnaireSession(configs_actifs)

    # ── Créer un AssetEngine par actif ─────────────────────────────────────
    moteurs: Dict[str, MoteurActif] = {}
    for symbole in args.symbols:
        config = configs_actifs[symbole]
        moteurs[symbole] = MoteurActif(
            config=config,
            connecteur=connecteur,
            portfolio_rm=portfolio_rm,
            notifier=notifier,
            session_manager=session_manager,
        )
        logger.info(
            f"MoteurActif [{symbole}] prêt | "
            f"Setup: {getattr(config, 'TYPE_STRATEGIE', '?')}"
        )

    # ── Message de démarrage Telegram ──────────────────────────────────────
    symboles_str = " | ".join(args.symbols)
    setups = {
        "XAUUSD": "SMC Breaker Block",
        "NAS100": "SMC + Killzones ICT",
        "SP500":  "SMC + FVG Priority",
        "WTI":    "SMC + Liquidity Sweep",
    }
    setups_str = "\n".join(
        f"  {s} → {setups.get(s, '?')}"
        for s in args.symbols
    )
    notifier.send(
        f"🚀 <b>SMC Bot Multi-Actif démarré</b>\n"
        f"Mode: <b>{args.mode.upper()}</b>\n"
        f"Actifs: {symboles_str}\n"
        f"Exposition max: 5% | Risk/trade: 1%\n"
        f"Setups:\n{setups_str}\n"
        f"🕐 {datetime.utcnow().strftime('%H:%M UTC')}",
        level=NiveauAlerte.INFO,
    )

    # ── Monitoring de santé ────────────────────────────────────────────────
    moniteur_sante = MoniteurSante(
        connecteur=connecteur,
        notifier=notifier,
    )

    # ── Scheduler ──────────────────────────────────────────────────────────
    try:
        from apscheduler.schedulers.background import BackgroundScheduler
        scheduler = BackgroundScheduler(timezone="UTC")

        # Cycle de trading — décalage 15s entre chaque actif
        for i, (symbole, moteur) in enumerate(moteurs.items()):
            scheduler.add_job(
                func=moteur.run_cycle,
                trigger="interval",
                seconds=60,
                start_date=datetime.now(timezone.utc) + timedelta(seconds=i * 15),
                id=f"cycle_{symbole}",
                name=f"Cycle trading {symbole}",
            )
            logger.info(
                f"Job cycle {symbole} ajouté (démarrage dans {i*15}s)"
            )

        # Ping de santé global toutes les 5 minutes
        scheduler.add_job(
            func=moniteur_sante.send_health_ping,
            trigger="interval",
            minutes=5,
            id="health_ping",
            name="Ping de santé Telegram",
        )

        # Reset journalier portfolio à minuit UTC
        scheduler.add_job(
            func=portfolio_rm.reset_daily_equity,
            trigger="cron",
            hour=0,
            minute=1,
            id="portfolio_daily_reset",
            name="Reset equity journalière portefeuille",
        )

        scheduler.start()
        logger.info("Scheduler multi-actif démarré")

    except Exception as e:
        logger.error(f"Erreur démarrage scheduler : {e}")
        scheduler = None

    # ── Handlers d'arrêt propre ────────────────────────────────────────────
    _arret_demande = [False]

    def on_shutdown(signum, frame):
        _arret_demande[0] = True
        logger.warning(f"Signal {signum} reçu — arrêt propre en cours...")
        moniteur_sante.definir_statut_bot(StatutBot.ARRETE)
        notifier.send(
            f"⛔ <b>Bot Multi-Actif arrêté</b>\n"
            f"Actifs: {symboles_str}\n"
            f"🕐 {datetime.utcnow().strftime('%H:%M UTC')}",
            level=NiveauAlerte.WARNING,
        )
        if scheduler:
            try:
                scheduler.shutdown(wait=False)
            except Exception:
                pass
        try:
            import MetaTrader5 as mt5
            mt5.shutdown()
        except Exception:
            pass
        sys.exit(0)

    signal.signal(signal.SIGTERM, on_shutdown)
    signal.signal(signal.SIGINT, on_shutdown)

    # ── Boucle principale ──────────────────────────────────────────────────
    logger.info("Bot multi-actif opérationnel — en attente des signaux...")
    try:
        while not _arret_demande[0]:
            time.sleep(1)
    except KeyboardInterrupt:
        on_shutdown(None, None)


if __name__ == "__main__":
    main()
