"""
session_manager.py — Gestion des sessions de trading par actif.

Chaque actif a ses propres fenêtres horaires définies dans sa config.
Le SessionManager vérifie si une session est active pour un actif donné
et retourne la prochaine session à venir si aucune n'est active.

Aucune dépendance MT5 — pure logique temporelle.
"""

from datetime import datetime
from typing import Dict, List, Optional, Tuple

from loguru import logger


class GestionnaireSession:
    """
    Gère les sessions de trading par actif.

    Chaque actif a des sessions définies dans sa config sous la forme :
        [{"name": "London", "open": 7, "close": 13}, ...]

    Les Killzones (NAS100) ont des minutes précises :
        [{"name": "NY_Open_KZ", "open": 13, "minute_open": 30,
                                 "close": 16, "minute_close": 0}]
    """

    def __init__(self, configs: Dict) -> None:
        """
        Args:
            configs: Dictionnaire {symbole_key: config_actif}
                     Ex: {"XAUUSD": ConfigXAUUSD(), "NAS100": ConfigNAS100(), ...}
        """
        self._configs = configs

    def is_session_active(
        self,
        symbol: str,
        now: Optional[datetime] = None,
    ) -> Tuple[bool, str]:
        """
        Vérifie si une session de trading est active pour cet actif.

        Args:
            symbol  : Symbole MT5 (ex: "XAUUSD", "NAS100", "US500", "XTIUSD").
            now     : Datetime UTC à vérifier (défaut: datetime.utcnow()).

        Returns:
            (active, raison)
            - active = True si une session est en cours
            - raison = description de la session active ou raison du blocage
        """
        if now is None:
            now = datetime.utcnow()

        config = self._trouver_config(symbol)
        if config is None:
            return False, f"Symbole {symbol} non configuré"

        heure = now.hour
        minute = now.minute
        jour_semaine = now.weekday()  # 0=lundi, 6=dimanche

        # ── Week-end : jamais de trading ──────────────────────────────────
        if jour_semaine == 5:
            return False, f"Samedi — marché {symbol} fermé"
        if jour_semaine == 6 and heure < 23:
            return False, f"Dimanche — marché {symbol} fermé avant 23h UTC"

        # ── Vérifier chaque session configurée ────────────────────────────
        sessions = getattr(config, "SESSIONS", [])
        for session in sessions:
            est_active, raison = self._verifier_session(session, heure, minute)
            if est_active:
                return True, raison

        # ── Aucune session active ──────────────────────────────────────────
        prochaine = self._get_prochaine_session(sessions, heure, minute)
        return False, f"{symbol} hors session | Prochaine : {prochaine}"

    def _verifier_session(
        self,
        session: dict,
        heure: int,
        minute: int,
    ) -> Tuple[bool, str]:
        """
        Vérifie si on est dans cette session.
        Gère les sessions simples (heure) et les Killzones (heure + minute).
        """
        nom = session.get("name", "Session")
        debut_h = session.get("open", 0)
        fin_h = session.get("close", 0)

        # Session avec minutes précises (Killzone)
        if "minute_open" in session:
            debut_min = session.get("minute_open", 0)
            fin_min = session.get("minute_close", 0)

            # Convertir en minutes depuis minuit pour comparaison
            maintenant_min = heure * 60 + minute
            debut_total = debut_h * 60 + debut_min
            fin_total = fin_h * 60 + fin_min

            if debut_total <= maintenant_min <= fin_total:
                return True, f"Session active : {nom}"
            return False, ""

        # Session simple (heures pleines)
        if debut_h <= heure < fin_h:
            return True, f"Session active : {nom}"
        return False, ""

    def _get_prochaine_session(
        self,
        sessions: List[dict],
        heure_actuelle: int,
        minute_actuelle: int,
    ) -> str:
        """Retourne la description de la prochaine session à venir."""
        heure_minute_actuelle = heure_actuelle * 60 + minute_actuelle

        # Chercher la prochaine session aujourd'hui
        for session in sorted(sessions, key=lambda s: s["open"]):
            debut_h = session["open"]
            debut_min = session.get("minute_open", 0)
            debut_total = debut_h * 60 + debut_min

            if debut_total > heure_minute_actuelle:
                nom = session.get("name", "Session")
                if debut_min > 0:
                    return f"{nom} à {debut_h}h{debut_min:02d} UTC"
                return f"{nom} à {debut_h}h00 UTC"

        # Toutes les sessions sont passées → demain
        if sessions:
            premiere = sessions[0]
            debut_h = premiere["open"]
            debut_min = premiere.get("minute_open", 0)
            nom = premiere.get("name", "Session")
            if debut_min > 0:
                return f"{nom} demain {debut_h}h{debut_min:02d} UTC"
            return f"{nom} demain {debut_h}h00 UTC"

        return "Aucune session configurée"

    def _trouver_config(self, symbol: str):
        """Trouve la config par symbole MT5 ou par clé du dictionnaire."""
        # Chercher par symbole MT5 exact
        for config in self._configs.values():
            symbole_config = getattr(config, "SYMBOLE", None)
            if symbole_config == symbol:
                return config

        # Chercher par clé du dictionnaire
        if symbol in self._configs:
            return self._configs[symbol]

        return None

    def get_sessions_actives(self, now: Optional[datetime] = None) -> Dict[str, str]:
        """
        Retourne l'état de session de tous les actifs configurés.

        Returns:
            {symbole_key: "Session active: London" | "Hors session | Prochaine: ..."}
        """
        if now is None:
            now = datetime.utcnow()

        resultats: Dict[str, str] = {}
        for cle, config in self._configs.items():
            symbole = getattr(config, "SYMBOLE", cle)
            active, raison = self.is_session_active(symbole, now)
            resultats[cle] = raison

        return resultats


# Alias anglais
SessionManager = GestionnaireSession
