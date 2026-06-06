"""
news_fetcher.py — Téléchargement et parsing du calendrier économique.
Logique en cascade : Investing.com → ForexFactory JSON → cache local → hardcoded.
"""

import json
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import List, Optional
from dataclasses import dataclass, asdict
from loguru import logger

try:
    import requests
    REQUESTS_DISPONIBLE = True
except ImportError:
    REQUESTS_DISPONIBLE = False

try:
    from bs4 import BeautifulSoup
    BS4_DISPONIBLE = True
except ImportError:
    BS4_DISPONIBLE = False

try:
    import pytz
    PYTZ_DISPONIBLE = True
except ImportError:
    PYTZ_DISPONIBLE = False


# ── Constantes ─────────────────────────────────────────────────────────────
DOSSIER_DATA = Path("data")
FICHIER_CACHE = DOSSIER_DATA / "news_calendar.json"
FICHIER_BACKUP = DOSSIER_DATA / "news_calendar_backup.json"
FICHIER_HARDCODE = DOSSIER_DATA / "news_hardcoded.json"

# Devises impactant XAUUSD
DEVISES_SURVEILLEES = {"USD", "XAU", "GBP", "EUR"}

# URL sources
URL_INVESTING = (
    "https://economic-calendar.investing.com/economic-calendar/"
    "Service/getCalendarFilteredData"
)
URL_FOREXFACTORY = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"


@dataclass
class EvenementNews:
    """Un événement économique du calendrier."""
    datetime_utc: datetime
    title: str
    currency: str
    impact: str   # "High", "Medium", "Low"
    source: str   # "investing", "forexfactory", "cache", "hardcoded"

    def to_dict(self) -> dict:
        """Sérialise en dict JSON-compatible."""
        return {
            "datetime_utc": self.datetime_utc.strftime("%Y-%m-%dT%H:%M:%S"),
            "title": self.title,
            "currency": self.currency,
            "impact": self.impact,
            "source": self.source,
        }

    @staticmethod
    def from_dict(d: dict, source: str = "cache") -> "EvenementNews":
        """Désérialise depuis un dict."""
        dt_str = d.get("datetime_utc", d.get("date", ""))
        # Gérer plusieurs formats de date
        for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%d %H:%M:%S"):
            try:
                dt = datetime.strptime(dt_str[:19], fmt[:len(dt_str[:19])])
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                break
            except ValueError:
                continue
        else:
            raise ValueError(f"Format de date non reconnu: {dt_str}")

        return EvenementNews(
            datetime_utc=dt.astimezone(timezone.utc).replace(tzinfo=None),
            title=d.get("title", ""),
            currency=d.get("currency", d.get("country", "USD")).upper(),
            impact=d.get("impact", "High"),
            source=source,
        )


class RecuperateurCalendrier:
    """
    Récupère le calendrier économique depuis plusieurs sources en cascade.
    Ne lève jamais d'exception vers l'appelant — retourne toujours une liste.
    """

    def __init__(self) -> None:
        DOSSIER_DATA.mkdir(exist_ok=True)

    # ── Point d'entrée principal ──────────────────────────────────────────

    def recuperer_calendrier(self) -> List[EvenementNews]:
        """
        Tente de récupérer le calendrier dans l'ordre de priorité.
        Retourne toujours une liste (vide en dernier recours absolu).

        Returns:
            Liste d'EvenementNews triée par datetime_utc croissant.
        """
        # Tentative 1 : Investing.com (14 jours)
        if REQUESTS_DISPONIBLE and BS4_DISPONIBLE:
            try:
                evenements = self._recuperer_investing_com()
                if evenements:
                    self._sauvegarder_cache(evenements, "investing")
                    self._sauvegarder_backup(evenements)
                    logger.success(
                        f"Calendrier Investing.com : {len(evenements)} events chargés"
                    )
                    return evenements
            except Exception as e:
                logger.warning(f"Investing.com indisponible : {e}")

        # Tentative 2 : ForexFactory JSON API
        if REQUESTS_DISPONIBLE:
            try:
                evenements = self._recuperer_forexfactory_json()
                if evenements:
                    self._sauvegarder_cache(evenements, "forexfactory")
                    logger.success(
                        f"Calendrier ForexFactory : {len(evenements)} events chargés"
                    )
                    return evenements
            except Exception as e:
                logger.warning(f"ForexFactory indisponible : {e}")

        # Tentative 3 : Cache local
        try:
            evenements = self._charger_cache()
            age = self._age_cache_heures()
            logger.warning(
                f"Utilisation cache local ({age:.1f}h) — {len(evenements)} events"
            )
            return evenements
        except Exception as e:
            logger.error(f"Cache local invalide : {e}")

        # Tentative 4 : Backup
        try:
            evenements = self._charger_backup()
            if evenements:
                logger.error(
                    f"Utilisation backup — {len(evenements)} events"
                )
                return evenements
        except Exception as e:
            logger.error(f"Backup invalide : {e}")

        # Tentative 5 : Dates hardcodées
        try:
            evenements = self._charger_hardcode()
            logger.critical(
                f"FALLBACK CRITIQUE : dates hardcodées uniquement ({len(evenements)} events)"
            )
            return evenements
        except Exception as e:
            logger.critical(f"Hardcode invalide : {e}")

        # Échec total → liste vide (le filtre bloquera par sécurité)
        logger.critical("AUCUNE SOURCE DISPONIBLE — filtre news désactivé (mode fail-safe)")
        return []

    # ── Source 1 : Investing.com ──────────────────────────────────────────

    def _recuperer_investing_com(self) -> List[EvenementNews]:
        """
        Télécharge le calendrier depuis Investing.com (14 jours).
        Parse la réponse HTML embarquée dans le JSON.

        Returns:
            Liste d'EvenementNews filtrées.
        """
        maintenant = datetime.now(timezone.utc)
        date_debut = maintenant.date()
        date_fin = (maintenant + timedelta(days=14)).date()

        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            "X-Requested-With": "XMLHttpRequest",
            "Referer": "https://www.investing.com/economic-calendar/",
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.8",
            "Content-Type": "application/x-www-form-urlencoded",
        }

        payload = {
            "country[]": ["5", "22", "4"],  # USA, UK, Zone Euro
            "importance[]": ["3"],            # High uniquement
            "timeZone": "8",                  # UTC
            "timeFilter": "timeRemain",
            "currentTab": "custom",
            "submitFilters": "1",
            "limit_from": "0",
            "dateFrom": date_debut.strftime("%Y-%m-%d"),
            "dateTo": date_fin.strftime("%Y-%m-%d"),
        }

        resp = requests.post(URL_INVESTING, headers=headers, data=payload, timeout=15)
        resp.raise_for_status()
        data = resp.json()

        html_content = data.get("data", "")
        if not html_content:
            raise ValueError("Réponse Investing.com vide")

        return self._parser_html_investing(html_content, date_debut)

    def _parser_html_investing(
        self,
        html: str,
        date_ref: "date",
    ) -> List[EvenementNews]:
        """
        Parse le HTML retourné par Investing.com.

        Args:
            html: Contenu HTML des lignes du calendrier.
            date_ref: Date de référence pour résoudre les heures.

        Returns:
            Liste d'EvenementNews.
        """
        soup = BeautifulSoup(html, "lxml")
        evenements = []
        date_courante = date_ref

        for ligne in soup.find_all("tr", {"class": True}):
            classes = " ".join(ligne.get("class", []))

            # Ligne de date
            if "theDay" in classes:
                try:
                    texte_date = ligne.get_text(strip=True)
                    # Format : "Friday, February 7, 2025"
                    date_courante = datetime.strptime(
                        texte_date, "%A, %B %d, %Y"
                    ).date()
                except ValueError:
                    pass
                continue

            # Ligne d'événement
            if "js-event-item" not in classes:
                continue

            try:
                # Impact
                icone_impact = ligne.find("td", {"class": "sentiment"})
                if not icone_impact:
                    continue
                bull_icons = icone_impact.find_all("i", {"class": "grayFullBullishIcon"})
                nb_bulls = len([i for i in bull_icons if "grayFullBullishIcon" in i.get("class", [])])
                # Investing.com : 3 bulls = High
                if nb_bulls < 3:
                    continue

                # Heure
                cellule_heure = ligne.find("td", {"class": "time"})
                if not cellule_heure:
                    continue
                heure_str = cellule_heure.get_text(strip=True)
                if not heure_str or heure_str.lower() in ("all day", "tentative", ""):
                    continue

                # Parser l'heure (format : "1:30pm")
                try:
                    heure_dt = datetime.strptime(heure_str, "%I:%M%p")
                    dt_utc = datetime(
                        date_courante.year, date_courante.month, date_courante.day,
                        heure_dt.hour, heure_dt.minute,
                        tzinfo=timezone.utc
                    )
                except ValueError:
                    continue

                # Devise
                drapeau = ligne.find("td", {"class": "flagCur"})
                devise = drapeau.get_text(strip=True).upper() if drapeau else "USD"

                if devise not in DEVISES_SURVEILLEES:
                    continue

                # Titre
                titre_elem = ligne.find("td", {"class": "event"})
                titre = titre_elem.get_text(strip=True) if titre_elem else ""
                if not titre:
                    continue

                evenements.append(EvenementNews(
                    datetime_utc=dt_utc.replace(tzinfo=None),
                    title=titre,
                    currency=devise,
                    impact="High",
                    source="investing",
                ))

            except Exception:
                continue

        return sorted(evenements, key=lambda e: e.datetime_utc)

    # ── Source 2 : ForexFactory JSON ──────────────────────────────────────

    def _recuperer_forexfactory_json(self) -> List[EvenementNews]:
        """
        Télécharge le calendrier depuis l'API JSON ForexFactory (semaine courante).

        Returns:
            Liste d'EvenementNews filtrées.
        """
        headers = {
            "User-Agent": "Mozilla/5.0 (compatible; TradingBot/2.0)",
            "Accept": "application/json",
        }
        resp = requests.get(URL_FOREXFACTORY, headers=headers, timeout=10)
        resp.raise_for_status()
        data = resp.json()

        evenements = []
        for item in data:
            try:
                impact = item.get("impact", "").title()
                if impact != "High":
                    continue

                devise = item.get("country", "").upper()
                if devise not in DEVISES_SURVEILLEES:
                    continue

                titre = item.get("title", "")
                date_str = item.get("date", "")  # Format: "2025-02-07T00:00:00-05:00"

                if not date_str or not titre:
                    continue

                # Parser la date avec timezone
                try:
                    # Retirer les secondes après +/- pour le parsing
                    if "T" in date_str:
                        # Format ISO avec timezone
                        dt = datetime.fromisoformat(date_str)
                        dt_utc = dt.astimezone(timezone.utc).replace(tzinfo=None)
                    else:
                        continue
                except (ValueError, AttributeError):
                    continue

                evenements.append(EvenementNews(
                    datetime_utc=dt_utc,
                    title=titre,
                    currency=devise,
                    impact="High",
                    source="forexfactory",
                ))

            except Exception:
                continue

        return sorted(evenements, key=lambda e: e.datetime_utc)

    # ── Gestion cache ─────────────────────────────────────────────────────

    def _sauvegarder_cache(
        self,
        evenements: List[EvenementNews],
        source: str,
    ) -> None:
        """Sauvegarde les événements dans le fichier cache JSON."""
        try:
            data = {
                "source": source,
                "timestamp_maj": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S"),
                "nb_events": len(evenements),
                "events": [e.to_dict() for e in evenements],
            }
            with open(FICHIER_CACHE, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            logger.debug(f"Cache news sauvegardé : {FICHIER_CACHE}")
        except Exception as e:
            logger.error(f"Impossible de sauvegarder le cache news : {e}")

    def _sauvegarder_backup(self, evenements: List[EvenementNews]) -> None:
        """Sauvegarde une copie de backup du cache."""
        try:
            if FICHIER_CACHE.exists():
                import shutil
                shutil.copy2(FICHIER_CACHE, FICHIER_BACKUP)
        except Exception as e:
            logger.debug(f"Backup news échoué : {e}")

    def _charger_cache(self) -> List[EvenementNews]:
        """Charge les événements depuis le cache local."""
        with open(FICHIER_CACHE, "r", encoding="utf-8") as f:
            data = json.load(f)
        evenements = [
            EvenementNews.from_dict(e, source="cache")
            for e in data.get("events", [])
        ]
        return sorted(evenements, key=lambda e: e.datetime_utc)

    def _charger_backup(self) -> List[EvenementNews]:
        """Charge les événements depuis le backup."""
        with open(FICHIER_BACKUP, "r", encoding="utf-8") as f:
            data = json.load(f)
        evenements = [
            EvenementNews.from_dict(e, source="cache_backup")
            for e in data.get("events", [])
        ]
        return sorted(evenements, key=lambda e: e.datetime_utc)

    def _charger_hardcode(self) -> List[EvenementNews]:
        """Charge les événements hardcodés depuis le fichier statique."""
        with open(FICHIER_HARDCODE, "r", encoding="utf-8") as f:
            data = json.load(f)
        evenements = [
            EvenementNews.from_dict(e, source="hardcoded")
            for e in data.get("events", [])
        ]
        return sorted(evenements, key=lambda e: e.datetime_utc)

    def _age_cache_heures(self) -> float:
        """Retourne l'âge du cache en heures."""
        try:
            with open(FICHIER_CACHE, "r") as f:
                data = json.load(f)
            ts = data.get("timestamp_maj", "")
            dt = datetime.strptime(ts, "%Y-%m-%dT%H:%M:%S")
            age = (datetime.utcnow() - dt).total_seconds() / 3600
            return round(age, 1)
        except Exception:
            return 999.0

    def cache_existe(self) -> bool:
        """Vérifie si le fichier cache existe."""
        return FICHIER_CACHE.exists()

    def cache_frais(self, max_heures: float = 24.0) -> bool:
        """Vérifie si le cache est suffisamment récent."""
        return self.cache_existe() and self._age_cache_heures() < max_heures
