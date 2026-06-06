"""
news_filter.py — Logique de blocage du trading autour des annonces économiques.
Deux niveaux : événements CRITIQUES pour XAUUSD et événements HIGH standard.
Ne lève jamais d'exception vers l'appelant — retourne toujours (bool, str).
"""

from datetime import datetime, timezone, timedelta
from typing import List, Optional, Tuple
from loguru import logger

from news_fetcher import RecuperateurCalendrier, EvenementNews


# ── Événements CRITIQUES pour XAUUSD ──────────────────────────────────────
# Ces événements créent des mouvements extrêmes sur l'or.
# Fenêtre de blocage étendue : 45 min avant / 90 min après.
EVENEMENTS_CRITIQUES_XAUUSD = {
    "non-farm payrolls",
    "nfp",
    "fed interest rate decision",
    "federal reserve interest rate decision",
    "fomc statement",
    "fomc meeting minutes",
    "fed press conference",
    "fed chair speech",
    "powell speech",
    "cpi m/m",
    "core cpi m/m",
    "cpi y/y",
    "core cpi y/y",
    "gdp q/q",
    "advance gdp q/q",
    "preliminary gdp q/q",
    "ism manufacturing pmi",
    "jackson hole symposium",
    "us holiday",
}

# Fenêtres de blocage en minutes
BLOCAGE_CRITIQUE_AVANT_MIN = 45
BLOCAGE_CRITIQUE_APRES_MIN = 90
BLOCAGE_STANDARD_AVANT_MIN = 30
BLOCAGE_STANDARD_APRES_MIN = 60

# Âge maximum du calendrier avant de considérer comme périmé
MAX_AGE_CACHE_HEURES = 48


class FiltreNews:
    """
    Filtre de trading basé sur le calendrier économique.

    Interface principale : is_trading_allowed(datetime) -> (bool, str)
    Ne lève JAMAIS d'exception — fail-safe = bloquer le trading.
    """

    def __init__(
        self,
        fetcher: Optional[RecuperateurCalendrier] = None,
        evenements: Optional[List[EvenementNews]] = None,
    ) -> None:
        """
        Args:
            fetcher: Instance de RecuperateurCalendrier (injection de dépendance).
            evenements: Liste d'événements pré-chargés (pour les tests).
        """
        self._fetcher = fetcher or RecuperateurCalendrier()
        self._evenements: List[EvenementNews] = evenements or []
        self._timestamp_derniere_maj: Optional[datetime] = None
        self._source_active: str = "non_initialisé"

    # ── Chargement ────────────────────────────────────────────────────────

    def force_refresh(self) -> bool:
        """
        Force le rechargement immédiat du calendrier depuis toutes les sources.

        Returns:
            True si au moins une source a fourni des données.
        """
        try:
            evenements = self._fetcher.recuperer_calendrier()
            self._evenements = evenements
            self._timestamp_derniere_maj = datetime.utcnow()

            if evenements:
                self._source_active = evenements[0].source if evenements else "vide"
                logger.info(
                    f"Calendrier news rechargé : {len(evenements)} events "
                    f"(source: {self._source_active})"
                )
                return True
            else:
                self._source_active = "vide"
                logger.warning("Calendrier news vide après rechargement")
                return False

        except Exception as e:
            logger.error(f"Erreur lors du rechargement du calendrier : {e}")
            return False

    def charger_si_necessaire(self) -> None:
        """Charge le calendrier uniquement si non initialisé."""
        if not self._evenements:
            self.force_refresh()

    # ── Interface principale ───────────────────────────────────────────────

    def is_trading_allowed(
        self,
        check_time: Optional[datetime] = None,
    ) -> Tuple[bool, str]:
        """
        Vérifie si le trading est autorisé à l'instant donné.

        Args:
            check_time: Datetime UTC à vérifier. Si None → datetime.utcnow().

        Returns:
            (True, "OK") si le trading est autorisé.
            (False, raison) si le trading est bloqué.
            En cas d'erreur totale → (False, "Calendrier indisponible") par sécurité.
        """
        try:
            if check_time is None:
                check_time = datetime.utcnow()

            # S'assurer que check_time est naive (sans timezone) pour la comparaison
            if check_time.tzinfo is not None:
                check_time = check_time.astimezone(timezone.utc).replace(tzinfo=None)

            # Charger si nécessaire
            self.charger_si_necessaire()

            # Si aucun événement chargé → bloquer par sécurité
            if not self._evenements:
                return False, "Calendrier news indisponible — trading bloqué par sécurité"

            # Vérifier les événements dans une fenêtre de ±4h
            evenements_proches = self._get_evenements_proches(check_time, fenetre_heures=4)

            for event in evenements_proches:
                est_critique = self._est_critique(event.title)

                if est_critique:
                    avant = timedelta(minutes=BLOCAGE_CRITIQUE_AVANT_MIN)
                    apres = timedelta(minutes=BLOCAGE_CRITIQUE_APRES_MIN)
                    label = "CRITIQUE"
                else:
                    avant = timedelta(minutes=BLOCAGE_STANDARD_AVANT_MIN)
                    apres = timedelta(minutes=BLOCAGE_STANDARD_APRES_MIN)
                    label = "HIGH"

                debut_blocage = event.datetime_utc - avant
                fin_blocage = event.datetime_utc + apres

                if debut_blocage <= check_time <= fin_blocage:
                    if check_time < event.datetime_utc:
                        minutes_restantes = int(
                            (event.datetime_utc - check_time).total_seconds() / 60
                        )
                        raison = (
                            f"News {label} dans {minutes_restantes}min : "
                            f"{event.title} ({event.currency})"
                        )
                    else:
                        minutes_ecoules = int(
                            (check_time - event.datetime_utc).total_seconds() / 60
                        )
                        raison = (
                            f"Post-news {label} ({minutes_ecoules}min écoulées) : "
                            f"{event.title} ({event.currency})"
                        )

                    logger.debug(
                        f"Trading bloqué | {raison} | "
                        f"Fenêtre: {debut_blocage.strftime('%H:%M')}–"
                        f"{fin_blocage.strftime('%H:%M')} UTC"
                    )
                    return False, raison

            return True, "OK"

        except Exception as e:
            # Fail-safe absolu : en cas d'exception imprévue, bloquer
            logger.error(f"Erreur FiltreNews.is_trading_allowed : {e}")
            return False, f"Erreur filtre news — trading bloqué par sécurité ({e})"

    # ── Méthodes utilitaires ──────────────────────────────────────────────

    def get_next_news(
        self,
        from_time: Optional[datetime] = None,
    ) -> Optional[EvenementNews]:
        """
        Retourne le prochain événement HIGH/CRITICAL à venir.

        Args:
            from_time: Référence temporelle (défaut: maintenant UTC).

        Returns:
            Prochain EvenementNews ou None si aucun.
        """
        if from_time is None:
            from_time = datetime.utcnow()
        if from_time.tzinfo is not None:
            from_time = from_time.replace(tzinfo=None)

        self.charger_si_necessaire()
        futurs = [e for e in self._evenements if e.datetime_utc > from_time]
        return futurs[0] if futurs else None

    def get_today_events(self) -> List[EvenementNews]:
        """
        Retourne tous les événements du jour en cours (UTC).

        Returns:
            Liste d'EvenementNews du jour, triée par heure.
        """
        self.charger_si_necessaire()
        aujourd_hui = datetime.utcnow().date()
        return [e for e in self._evenements if e.datetime_utc.date() == aujourd_hui]

    def get_events_this_week(self) -> List[EvenementNews]:
        """
        Retourne tous les événements de la semaine (7 prochains jours).

        Returns:
            Liste d'EvenementNews de la semaine.
        """
        self.charger_si_necessaire()
        maintenant = datetime.utcnow()
        dans_7_jours = maintenant + timedelta(days=7)
        return [
            e for e in self._evenements
            if maintenant.replace(tzinfo=None) <= e.datetime_utc <= dans_7_jours.replace(tzinfo=None)
        ]

    def is_calendar_fresh(self) -> bool:
        """
        Vérifie si le calendrier a été mis à jour dans les MAX_AGE_CACHE_HEURES dernières heures.

        Returns:
            True si le calendrier est frais.
        """
        if self._timestamp_derniere_maj is None:
            return False
        age = (datetime.utcnow() - self._timestamp_derniere_maj).total_seconds() / 3600
        return age < MAX_AGE_CACHE_HEURES

    def get_status(self) -> dict:
        """
        Retourne le statut complet du filtre pour le dashboard.

        Returns:
            Dict avec source, dernière MAJ, nb events, prochaine news.
        """
        self.charger_si_necessaire()
        prochaine = self.get_next_news()
        maintenant = datetime.utcnow()

        if prochaine:
            delta = prochaine.datetime_utc - maintenant
            total_min = int(delta.total_seconds() / 60)
            jours = total_min // (24 * 60)
            heures = (total_min % (24 * 60)) // 60
            minutes = total_min % 60

            if jours > 0:
                texte_prochaine = f"{prochaine.title} dans {jours}j {heures}h"
            elif heures > 0:
                texte_prochaine = f"{prochaine.title} dans {heures}h{minutes:02d}m"
            else:
                texte_prochaine = f"{prochaine.title} dans {minutes}min"
        else:
            texte_prochaine = "Aucune annonce prévue"

        age_heures = None
        if self._timestamp_derniere_maj:
            age_heures = round(
                (maintenant - self._timestamp_derniere_maj).total_seconds() / 3600, 1
            )

        trading_ok, raison = self.is_trading_allowed(maintenant)

        return {
            "source": self._source_active,
            "derniere_maj": (
                self._timestamp_derniere_maj.strftime("%d/%m %H:%M UTC")
                if self._timestamp_derniere_maj else "jamais"
            ),
            "age_cache_heures": age_heures,
            "events_charges": len(self._evenements),
            "events_cette_semaine": len(self.get_events_this_week()),
            "prochaine_news": texte_prochaine,
            "calendar_frais": self.is_calendar_fresh(),
            "trading_autorise": trading_ok,
            "raison_blocage": raison if not trading_ok else "",
        }

    def resume_pour_dashboard(self) -> str:
        """Retourne un résumé court (1 ligne) pour le dashboard."""
        prochaine = self.get_next_news()
        if prochaine is None:
            return "Aucune annonce prévue"

        delta = prochaine.datetime_utc - datetime.utcnow()
        total_min = int(delta.total_seconds() / 60)

        if total_min < 0:
            return f"Post-news: {prochaine.title}"

        jours = total_min // (24 * 60)
        heures = (total_min % (24 * 60)) // 60
        minutes = total_min % 60

        if jours > 0:
            return f"{prochaine.currency} {prochaine.title} dans {jours}j {heures}h"
        elif heures > 0:
            return f"{prochaine.currency} {prochaine.title} dans {heures}h{minutes:02d}m"
        else:
            return f"⚠️ {prochaine.currency} {prochaine.title} dans {minutes}min"

    # ── Méthodes privées ──────────────────────────────────────────────────

    def _est_critique(self, titre: str) -> bool:
        """
        Vérifie si un événement est critique pour XAUUSD.

        Args:
            titre: Nom de l'événement.

        Returns:
            True si l'événement est dans la liste critique.
        """
        titre_lower = titre.lower().strip()
        return any(critique in titre_lower for critique in EVENEMENTS_CRITIQUES_XAUUSD)

    def _get_evenements_proches(
        self,
        reference: datetime,
        fenetre_heures: float = 4.0,
    ) -> List[EvenementNews]:
        """
        Retourne les événements dans une fenêtre temporelle autour de reference.

        Args:
            reference: Datetime de référence (UTC naive).
            fenetre_heures: Fenêtre en heures de chaque côté.

        Returns:
            Liste d'événements dans la fenêtre, triés par datetime_utc.
        """
        fenetre = timedelta(hours=fenetre_heures)
        debut = reference - fenetre
        fin = reference + fenetre
        return [
            e for e in self._evenements
            if debut <= e.datetime_utc <= fin
        ]
