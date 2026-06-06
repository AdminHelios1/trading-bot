"""
logger_config.py — Configuration centralisée de loguru.
Rotation journalière automatique, niveau configurable via .env.
"""

import sys
import os
from pathlib import Path
from loguru import logger
from dotenv import load_dotenv

load_dotenv()


def configurer_logger() -> None:
    """
    Initialise loguru avec :
    - Sortie console colorée
    - Fichier rotatif journalier dans logs/
    - Niveau configurable via LOG_LEVEL dans .env
    """
    niveau = os.getenv("LOG_LEVEL", "INFO").upper()
    dossier_logs = Path("logs")
    dossier_logs.mkdir(exist_ok=True)

    # Supprimer le handler par défaut de loguru
    logger.remove()

    # ── Console ───────────────────────────────────────────────────────────
    logger.add(
        sys.stderr,
        level=niveau,
        format=(
            "<green>{time:HH:mm:ss}</green> | "
            "<level>{level: <8}</level> | "
            "<cyan>{name}</cyan> — <level>{message}</level>"
        ),
        colorize=True,
        backtrace=True,
        diagnose=True,
    )

    # ── Fichier rotatif journalier ────────────────────────────────────────
    logger.add(
        dossier_logs / "bot_{time:YYYY-MM-DD}.log",
        level="DEBUG",
        format=(
            "{time:YYYY-MM-DD HH:mm:ss.SSS} | "
            "{level: <8} | "
            "{name}:{function}:{line} — {message}"
        ),
        rotation="00:00",       # Nouveau fichier à minuit
        retention="30 days",    # Conserver 30 jours
        compression="zip",      # Compresser les anciens logs
        encoding="utf-8",
        backtrace=True,
        diagnose=True,
    )

    # ── Fichier erreurs uniquement ────────────────────────────────────────
    logger.add(
        dossier_logs / "erreurs_{time:YYYY-MM-DD}.log",
        level="ERROR",
        format=(
            "{time:YYYY-MM-DD HH:mm:ss.SSS} | "
            "{level: <8} | "
            "{name}:{function}:{line}\n{message}\n"
        ),
        rotation="00:00",
        retention="90 days",
        compression="zip",
        encoding="utf-8",
        backtrace=True,
        diagnose=True,
    )

    logger.info(f"Logger initialisé — niveau console: {niveau}")
