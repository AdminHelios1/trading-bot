"""
news_scheduler.py — Mise à jour automatique du calendrier économique via APScheduler.
MAJ hebdomadaire le dimanche + mi-semaine le mardi + vérification de fraîcheur toutes les 6h.
"""

from loguru import logger

try:
    from apscheduler.schedulers.background import BackgroundScheduler
    APSCHEDULER_DISPONIBLE = True
except ImportError:
    APSCHEDULER_DISPONIBLE = False

from news_filter import FiltreNews


def demarrer_scheduler_news(filtre_news: FiltreNews) -> object:
    """
    Démarre le scheduler APScheduler pour la mise à jour automatique du calendrier.

    Planning :
    - Dimanche 22h00 UTC : rechargement hebdomadaire principal
    - Mardi 06h00 UTC : rechargement mi-semaine (certains events publiés en cours de semaine)
    - Toutes les 6h : vérification de fraîcheur (force refresh si > 48h)

    Args:
        filtre_news: Instance FiltreNews à mettre à jour.

    Returns:
        Instance du scheduler démarré, ou None si APScheduler non disponible.
    """
    if not APSCHEDULER_DISPONIBLE:
        logger.warning(
            "APScheduler non disponible — mise à jour automatique du calendrier désactivée. "
            "Installer avec : pip install APScheduler"
        )
        return None

    scheduler = BackgroundScheduler(timezone="UTC")

    # ── MAJ hebdomadaire : dimanche 22h00 UTC ─────────────────────────────
    scheduler.add_job(
        func=_refresh_avec_log,
        args=[filtre_news, "hebdomadaire (dimanche)"],
        trigger="cron",
        day_of_week="sun",
        hour=22,
        minute=0,
        id="maj_news_hebdomadaire",
        name="MAJ hebdomadaire calendrier économique",
        replace_existing=True,
        misfire_grace_time=3600,  # Tolérance 1h si le bot était arrêté
    )

    # ── MAJ mi-semaine : mardi 06h00 UTC ──────────────────────────────────
    scheduler.add_job(
        func=_refresh_avec_log,
        args=[filtre_news, "mi-semaine (mardi)"],
        trigger="cron",
        day_of_week="tue",
        hour=6,
        minute=0,
        id="maj_news_mi_semaine",
        name="MAJ mi-semaine calendrier économique",
        replace_existing=True,
        misfire_grace_time=3600,
    )

    # ── Vérification fraîcheur : toutes les 6h ────────────────────────────
    scheduler.add_job(
        func=_verifier_fraicheur,
        args=[filtre_news],
        trigger="interval",
        hours=6,
        id="verification_fraicheur_news",
        name="Vérification fraîcheur calendrier",
        replace_existing=True,
    )

    scheduler.start()
    logger.info(
        "Scheduler news démarré | "
        "MAJ : dimanche 22h UTC + mardi 06h UTC + vérification toutes les 6h"
    )
    return scheduler


def arreter_scheduler_news(scheduler: object) -> None:
    """
    Arrête proprement le scheduler news.

    Args:
        scheduler: Instance du scheduler retourné par demarrer_scheduler_news().
    """
    if scheduler is not None and APSCHEDULER_DISPONIBLE:
        try:
            scheduler.shutdown(wait=False)
            logger.info("Scheduler news arrêté")
        except Exception as e:
            logger.warning(f"Erreur arrêt scheduler news : {e}")


def _refresh_avec_log(filtre_news: FiltreNews, contexte: str) -> None:
    """
    Wrapper de force_refresh() avec logging contextualisé.

    Args:
        filtre_news: Instance FiltreNews.
        contexte: Description du déclencheur (ex: "hebdomadaire").
    """
    logger.info(f"Rechargement calendrier news ({contexte})...")
    succes = filtre_news.force_refresh()
    if succes:
        status = filtre_news.get_status()
        logger.success(
            f"Calendrier rechargé ({contexte}) | "
            f"{status['events_charges']} events | "
            f"Source: {status['source']} | "
            f"Prochaine: {status['prochaine_news']}"
        )
    else:
        logger.error(f"Échec rechargement calendrier ({contexte}) — fallback actif")


def _verifier_fraicheur(filtre_news: FiltreNews) -> None:
    """
    Vérifie si le calendrier est frais. Force un refresh si > 48h.

    Args:
        filtre_news: Instance FiltreNews.
    """
    if not filtre_news.is_calendar_fresh():
        logger.warning(
            "Calendrier news périmé (> 48h) — rechargement forcé..."
        )
        _refresh_avec_log(filtre_news, "fraîcheur automatique")
    else:
        status = filtre_news.get_status()
        logger.debug(
            f"Calendrier news frais | "
            f"Âge: {status.get('age_cache_heures', '?')}h | "
            f"Prochaine: {status.get('prochaine_news', '?')}"
        )
