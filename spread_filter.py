"""
spread_filter.py — Filtrage complet du spread et de la volatilité pour XAUUSD.
Hard cap absolu, détection des périodes dangereuses, filtre ATR.
Ne lève jamais d'exception vers l'appelant — fail-safe = bloquer.
"""

from datetime import datetime, timedelta
from typing import Tuple, Optional
from loguru import logger

from config import CONFIG
from spread_monitor import MoniteurSpread
from indicators import Indicateurs

# ── Périodes dangereuses XAUUSD ──────────────────────────────────────────────
# Ces plages sont bloquées INDÉPENDAMMENT du spread actuel car le spread
# peut exploser en quelques secondes sans avertissement.
# Format : (weekday_ou_None, heure_debut_utc, heure_fin_utc, minute_debut, minute_fin, label)

PERIODES_DANGEREUSES = [
    # Clôture journalière XAUUSD : spread > 100 pts fréquemment
    {
        "weekday": None,   # Tous les jours
        "heure_debut": 21, "minute_debut": 45,
        "heure_fin": 22,   "minute_fin": 15,
        "label": "Clôture journalière XAUUSD (21h45–22h15 UTC)",
    },
    # Vendredi soir : liquidité en baisse avant week-end
    {
        "weekday": 4,      # Vendredi
        "heure_debut": 19, "minute_debut": 0,
        "heure_fin": 24,   "minute_fin": 0,
        "label": "Vendredi soir (>19h UTC) — liquidité XAUUSD en baisse",
    },
    # Samedi : marché fermé
    {
        "weekday": 5,      # Samedi
        "heure_debut": 0,  "minute_debut": 0,
        "heure_fin": 24,   "minute_fin": 0,
        "label": "Samedi — marché XAUUSD fermé",
    },
    # Dimanche : réouverture hebdomadaire, spread élevé avant 23h UTC
    {
        "weekday": 6,      # Dimanche
        "heure_debut": 0,  "minute_debut": 0,
        "heure_fin": 23,   "minute_fin": 0,
        "label": "Dimanche (réouverture <23h UTC) — spread hebdomadaire élevé",
    },
]


class FiltreSpread:
    """
    Filtre complet spread + volatilité pour XAUUSD.

    Interface principale : is_market_tradeable() -> (bool, str)
    Ordre d'évaluation (du moins coûteux au plus coûteux) :
      1. Hard cap absolu
      2. Période dangereuse connue
      3. Spread dynamique vs moyenne
      4. Volatilité ATR H4
    """

    def __init__(
        self,
        moniteur: MoniteurSpread,
        connecteur=None,
    ) -> None:
        """
        Args:
            moniteur: Instance MoniteurSpread injectée.
            connecteur: Instance ConnecteurMT5 (peut être None en mode test).
        """
        self.moniteur = moniteur
        self.connecteur = connecteur

    # ── Interface principale ───────────────────────────────────────────────

    def is_market_tradeable(
        self,
        check_time: Optional[datetime] = None,
    ) -> Tuple[bool, str]:
        """
        Vérifie toutes les conditions spread/volatilité dans l'ordre optimal.

        Args:
            check_time: Datetime UTC à vérifier (défaut: maintenant).

        Returns:
            (True, "OK") si le marché est tradeable.
            (False, raison) si bloqué.
            En cas d'erreur totale → (False, message) par sécurité.
        """
        try:
            maintenant = check_time or datetime.utcnow()
            if hasattr(maintenant, "tzinfo") and maintenant.tzinfo is not None:
                maintenant = maintenant.replace(tzinfo=None)

            # Check 1 — Hard cap absolu (priorité maximale, calcul instantané)
            ok, raison = self._verifier_hard_cap()
            if not ok:
                return False, raison

            # Check 2 — Période dangereuse connue (calcul instantané)
            ok, raison = self._verifier_periode_dangereuse(maintenant)
            if not ok:
                return False, raison

            # Check 3 — Spread dynamique vs moyenne (lookup historique)
            ok, raison = self._verifier_spread_dynamique()
            if not ok:
                return False, raison

            # Check 4 — Volatilité ATR (lecture OHLCV + calcul)
            ok, raison = self._verifier_volatilite_atr()
            if not ok:
                return False, raison

            return True, "OK"

        except Exception as e:
            logger.error(f"Erreur FiltreSpread.is_market_tradeable : {e}")
            return False, f"Filtre spread indisponible — trading bloqué par sécurité ({e})"

    # ── Check 1 : Hard cap absolu ─────────────────────────────────────────

    def _verifier_hard_cap(self) -> Tuple[bool, str]:
        """
        Hard cap absolu — ligne rouge non négociable.
        Jamais de trade si spread > SPREAD_HARD_CAP_POINTS.

        Returns:
            (True, "OK") ou (False, raison).
        """
        try:
            spread_actuel = self.moniteur.get_spread_actuel()

            if spread_actuel <= 0:
                # Spread non disponible → pas de blocage sur hard cap
                logger.debug("Spread actuel non disponible — hard cap ignoré")
                return True, "OK"

            if spread_actuel > CONFIG.SPREAD_HARD_CAP_POINTS:
                raison = (
                    f"Spread HARD CAP dépassé : {spread_actuel:.0f} pts "
                    f"(limite absolue : {CONFIG.SPREAD_HARD_CAP_POINTS:.0f} pts)"
                )
                logger.warning(f"⛔ {raison}")
                return False, raison

            return True, "OK"

        except Exception as e:
            logger.error(f"Erreur vérification hard cap : {e}")
            return False, f"Hard cap indisponible — bloqué par sécurité"

    # ── Check 2 : Périodes dangereuses ────────────────────────────────────

    def _verifier_periode_dangereuse(
        self,
        maintenant: datetime,
    ) -> Tuple[bool, str]:
        """
        Détecte les périodes à spread explosif connu sur XAUUSD.
        Ces plages sont bloquées même si le spread actuel semble normal.

        Args:
            maintenant: Datetime UTC naive.

        Returns:
            (True, "OK") ou (False, raison).
        """
        heure = maintenant.hour
        minute = maintenant.minute
        weekday = maintenant.weekday()

        # Convertir l'heure courante en minutes depuis minuit pour comparaison
        minutes_courantes = heure * 60 + minute

        for periode in PERIODES_DANGEREUSES:
            # Filtrer par jour de la semaine si spécifié
            if periode["weekday"] is not None and weekday != periode["weekday"]:
                continue

            debut_min = periode["heure_debut"] * 60 + periode["minute_debut"]
            fin_min = periode["heure_fin"] * 60 + periode["minute_fin"]

            # Gérer le cas fin_min = 24*60 (minuit = fin de journée)
            if fin_min >= 24 * 60:
                fin_min = 24 * 60 - 1

            # Utiliser < pour la borne supérieure (fin_min exclu)
            if debut_min <= minutes_courantes < fin_min:
                raison = periode["label"]
                logger.debug(f"Période dangereuse détectée : {raison}")
                return False, raison

        return True, "OK"

    # ── Check 3 : Spread dynamique ────────────────────────────────────────

    def _verifier_spread_dynamique(self) -> Tuple[bool, str]:
        """
        Compare le spread actuel à la moyenne des 50 dernières minutes.
        Bloque si spread actuel > SPREAD_DYNAMIC_MULTIPLIER × moyenne.
        Ignoré si pas assez de samples (< SPREAD_MIN_HISTORY_SAMPLES).

        Returns:
            (True, "OK") ou (False, raison).
        """
        try:
            nb_samples = self.moniteur.get_nb_samples_recents(
                CONFIG.SPREAD_AVERAGE_WINDOW_MIN
            )

            if nb_samples < CONFIG.SPREAD_MIN_HISTORY_SAMPLES:
                logger.debug(
                    f"Historique spread insuffisant ({nb_samples} samples) "
                    f"— filtre dynamique ignoré"
                )
                return True, "OK"

            spread_actuel = self.moniteur.get_spread_actuel()
            spread_moyen = self.moniteur.get_spread_moyen(
                CONFIG.SPREAD_AVERAGE_WINDOW_MIN
            )

            if spread_moyen <= 0:
                return True, "OK"

            ratio = spread_actuel / spread_moyen
            seuil = CONFIG.SPREAD_DYNAMIC_MULTIPLIER

            if ratio > seuil:
                raison = (
                    f"Spread anormal : {spread_actuel:.0f} pts "
                    f"({ratio:.1f}× la moyenne de {spread_moyen:.0f} pts) "
                    f"— seuil : {seuil}×"
                )
                logger.warning(f"⛔ {raison}")
                return False, raison

            logger.debug(
                f"Spread OK : {spread_actuel:.0f} pts "
                f"({ratio:.1f}× moy {spread_moyen:.0f} pts)"
            )
            return True, "OK"

        except Exception as e:
            logger.error(f"Erreur vérification spread dynamique : {e}")
            return False, f"Filtre spread dynamique indisponible — bloqué par sécurité"

    # ── Check 4 : Volatilité ATR ──────────────────────────────────────────

    def _verifier_volatilite_atr(self) -> Tuple[bool, str]:
        """
        Filtre de volatilité ATR sur H4 :
        - Trop calme (ATR < 50% moyenne) → pas de momentum → skip
        - Trop violent (ATR > 3× moyenne) → marché en panique → skip

        Returns:
            (True, "OK") ou (False, raison).
        """
        try:
            if self.connecteur is None:
                logger.debug("ConnecteurMT5 absent — filtre ATR ignoré")
                return True, "OK"

            df_h4 = self.connecteur.get_ohlcv(
                CONFIG.SYMBOLE,
                CONFIG.TIMEFRAME_HTF,
                64,  # 50 bougies + 14 pour ATR
            )

            if len(df_h4) < 30:
                logger.debug("Données H4 insuffisantes — filtre ATR ignoré")
                return True, "OK"

            atr_serie = Indicateurs.atr(df_h4, CONFIG.ATR_PERIODE)

            if len(atr_serie) < 2:
                return True, "OK"

            atr_actuel = float(atr_serie.iloc[-1])
            # Moyenne des 50 bougies précédentes (exclure la dernière)
            atr_moyen = float(atr_serie.iloc[-51:-1].mean()) if len(atr_serie) >= 52 else float(atr_serie.mean())

            if atr_moyen <= 0:
                return True, "OK"

            ratio = atr_actuel / atr_moyen

            if ratio < CONFIG.ATR_MIN_RATIO:
                raison = (
                    f"Volatilité trop faible : ATR={atr_actuel:.2f} "
                    f"({ratio:.2f}× la moyenne {atr_moyen:.2f}) — marché en range"
                )
                logger.debug(f"⛔ {raison}")
                return False, raison

            if ratio > CONFIG.ATR_MAX_RATIO:
                raison = (
                    f"Volatilité excessive : ATR={atr_actuel:.2f} "
                    f"({ratio:.1f}× la moyenne {atr_moyen:.2f}) — marché en panique"
                )
                logger.warning(f"⛔ {raison}")
                return False, raison

            logger.debug(
                f"Volatilité OK : ATR={atr_actuel:.2f} "
                f"({ratio:.2f}× moy {atr_moyen:.2f})"
            )
            return True, "OK"

        except Exception as e:
            logger.error(f"Erreur filtre ATR : {e}")
            return False, f"Filtre ATR indisponible — bloqué par sécurité ({e})"

    # ── Statut pour dashboard ─────────────────────────────────────────────

    def get_status(self) -> dict:
        """
        Retourne le statut complet pour le dashboard.

        Returns:
            Dict avec toutes les métriques spread/volatilité.
        """
        tradeable, raison = self.is_market_tradeable()
        stats = self.moniteur.get_stats_24h()

        return {
            "tradeable": tradeable,
            "raison_blocage": raison if not tradeable else "",
            "spread_actuel": stats.get("spread_actuel", 0),
            "spread_moyen_50min": stats.get("spread_moyen_50min", 0),
            "spread_min_24h": stats.get("spread_min_24h"),
            "spread_max_24h": stats.get("spread_max_24h"),
            "heure_min": stats.get("heure_min", "—"),
            "heure_max": stats.get("heure_max", "—"),
            "hard_cap": CONFIG.SPREAD_HARD_CAP_POINTS,
            "nb_samples": self.moniteur.get_nb_samples_recents(50),
        }
