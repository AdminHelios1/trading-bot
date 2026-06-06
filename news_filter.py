"""
news_filter.py — Filtre automatique des annonces économiques haute importance.
Source : Forex Factory JSON (gratuit, sans clé API).
Bloque le trading 30 minutes avant et 30 minutes après chaque annonce High Impact.
"""

import json
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import List, Optional
from loguru import logger

try:
    import requests
    REQUESTS_DISPONIBLE = True
except ImportError:
    REQUESTS_DISPONIBLE = False


# ── Constantes ─────────────────────────────────────────────────────────────
CACHE_FICHIER = Path("reports/news_cache.json")
CACHE_DUREE_HEURES = 12   # Rafraîchir le cache toutes les 12h
MARGE_AVANT_MINUTES = 30  # Bloquer 30 min avant l'annonce
MARGE_APRES_MINUTES = 30  # Bloquer 30 min après l'annonce

# Devises à surveiller pour XAUUSD
DEVISES_SURVEILLEES = {"USD", "EUR", "GBP"}

# URL Forex Factory JSON (format non officiel mais stable)
FF_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"


@dataclass_style = None  # éviter l'import dataclass pour compatibilité


class Annonce:
    """Une annonce économique du calendrier."""
    def __init__(self, titre: str, devise: str, impact: str, timestamp_utc: datetime) -> None:
        self.titre = titre
        self.devise = devise
        self.impact = impact  # "High", "Medium", "Low"
        self.timestamp_utc = timestamp_utc

    def __repr__(self) -> str:
        return f"{self.timestamp_utc.strftime('%H:%M UTC')} [{self.impact}] {self.devise} — {self.titre}"


class FiltreNews:
    """
    Vérifie si le trading est autorisé selon le calendrier économique.
    Bloque automatiquement les fenêtres autour des annonces High Impact.
    """

    def __init__(self) -> None:
        self.annonces: List[Annonce] = []
        self.derniere_maj: float = 0.0
        CACHE_FICHIER.parent.mkdir(exist_ok=True)

    def charger_annonces(self, forcer: bool = False) -> bool:
        """
        Charge les annonces de la semaine depuis Forex Factory ou le cache local.

        Args:
            forcer: Forcer le rechargement même si le cache est frais.

        Returns:
            True si les annonces sont chargées avec succès.
        """
        maintenant = time.time()
        cache_age_heures = (maintenant - self.derniere_maj) / 3600

        # Utiliser le cache si récent
        if not forcer and cache_age_heures < CACHE_DUREE_HEURES and self.annonces:
            return True

        # Essayer de charger depuis Forex Factory
        if REQUESTS_DISPONIBLE:
            try:
                resp = requests.get(FF_URL, timeout=10, headers={
                    "User-Agent": "Mozilla/5.0 (compatible; TradingBot/1.0)"
                })
                if resp.status_code == 200:
                    data = resp.json()
                    self.annonces = self._parser_annonces(data)
                    self.derniere_maj = maintenant
                    # Sauvegarder le cache
                    with open(CACHE_FICHIER, "w") as f:
                        json.dump(data, f)
                    logger.info(
                        f"Calendrier économique chargé: {len(self.annonces)} annonces "
                        f"({sum(1 for a in self.annonces if a.impact == 'High')} High Impact)"
                    )
                    return True
            except Exception as e:
                logger.warning(f"Impossible de charger le calendrier FF: {e}")

        # Essayer le cache local
        if CACHE_FICHIER.exists():
            try:
                with open(CACHE_FICHIER) as f:
                    data = json.load(f)
                self.annonces = self._parser_annonces(data)
                self.derniere_maj = maintenant
                logger.warning("Calendrier chargé depuis le cache local (peut être ancien)")
                return True
            except Exception as e:
                logger.error(f"Erreur lecture cache news: {e}")

        logger.warning("Filtre news inactif — aucune donnée de calendrier disponible")
        return False

    def _parser_annonces(self, data: list) -> List[Annonce]:
        """
        Parse les données JSON de Forex Factory en objets Annonce.

        Args:
            data: Liste de dicts JSON de Forex Factory.

        Returns:
            Liste d'Annonce filtrées (High Impact uniquement, devises surveillées).
        """
        annonces = []
        for item in data:
            try:
                devise = item.get("country", "").upper()
                impact = item.get("impact", "").title()  # "High", "Medium", "Low"
                titre = item.get("title", "")
                date_str = item.get("date", "")
                heure_str = item.get("time", "")

                # Filtrer : High Impact uniquement + devises surveillées
                if impact != "High":
                    continue
                if devise not in DEVISES_SURVEILLEES:
                    continue
                if not date_str or not heure_str or heure_str in ("", "All Day", "Tentative"):
                    continue

                # Parser le timestamp
                try:
                    # Format FF : "01-06-2026" et "2:30pm"
                    dt_str = f"{date_str} {heure_str}"
                    dt = datetime.strptime(dt_str, "%m-%d-%Y %I:%M%p")
                    # Forex Factory est en heure de New York (EST/EDT)
                    # Approximation : UTC-4 en été (EDT)
                    dt_utc = dt.replace(tzinfo=timezone.utc) + timedelta(hours=4)
                    annonces.append(Annonce(titre, devise, impact, dt_utc))
                except ValueError:
                    continue

            except Exception:
                continue

        return sorted(annonces, key=lambda a: a.timestamp_utc)

    def trading_autorise(self, maintenant_utc: Optional[datetime] = None) -> tuple:
        """
        Vérifie si le trading est autorisé à l'instant donné.

        Args:
            maintenant_utc: Datetime UTC actuel (défaut: now()).

        Returns:
            Tuple (autorisé: bool, raison: str).
        """
        if maintenant_utc is None:
            maintenant_utc = datetime.now(timezone.utc)

        # Recharger les annonces si nécessaire
        self.charger_annonces()

        if not self.annonces:
            # Pas de données → ne pas bloquer (fail open)
            return True, ""

        fenetre_avant = timedelta(minutes=MARGE_AVANT_MINUTES)
        fenetre_apres = timedelta(minutes=MARGE_APRES_MINUTES)

        for annonce in self.annonces:
            debut_blocage = annonce.timestamp_utc - fenetre_avant
            fin_blocage = annonce.timestamp_utc + fenetre_apres

            if debut_blocage <= maintenant_utc <= fin_blocage:
                temps_restant = annonce.timestamp_utc - maintenant_utc
                if temps_restant.total_seconds() > 0:
                    minutes = int(temps_restant.total_seconds() / 60)
                    raison = (
                        f"Annonce HIGH IMPACT dans {minutes} min : "
                        f"{annonce.devise} — {annonce.titre}"
                    )
                else:
                    minutes = int(-temps_restant.total_seconds() / 60)
                    raison = (
                        f"Post-annonce HIGH IMPACT ({minutes} min écoulées) : "
                        f"{annonce.devise} — {annonce.titre}"
                    )
                logger.warning(f"⛔ Trading bloqué — {raison}")
                return False, raison

        return True, ""

    def prochaine_annonce(self, maintenant_utc: Optional[datetime] = None) -> Optional[Annonce]:
        """
        Retourne la prochaine annonce High Impact à venir.

        Args:
            maintenant_utc: Datetime UTC actuel.

        Returns:
            Prochaine annonce ou None.
        """
        if maintenant_utc is None:
            maintenant_utc = datetime.now(timezone.utc)

        self.charger_annonces()
        futures = [a for a in self.annonces if a.timestamp_utc > maintenant_utc]
        return futures[0] if futures else None

    def get_annonces_aujourd_hui(self) -> List[Annonce]:
        """Retourne les annonces High Impact du jour."""
        self.charger_annonces()
        aujourd_hui = datetime.now(timezone.utc).date()
        return [a for a in self.annonces if a.timestamp_utc.date() == aujourd_hui]

    def resume_pour_dashboard(self) -> str:
        """Retourne un résumé court pour le dashboard."""
        prochaine = self.prochaine_annonce()
        if prochaine is None:
            return "Aucune annonce"
        delta = prochaine.timestamp_utc - datetime.now(timezone.utc)
        heures = int(delta.total_seconds() / 3600)
        minutes = int((delta.total_seconds() % 3600) / 60)
        return f"{prochaine.devise} {prochaine.titre} dans {heures}h{minutes:02d}m"
