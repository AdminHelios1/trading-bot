"""
spread_monitor.py — Collecte et historique du spread XAUUSD en temps réel.
Tourne en arrière-plan (via scheduler) et enregistre le spread toutes les 60 secondes.
Continue de collecter même quand le bot est en pause (circuit breaker, drawdown).
"""

import json
import os
from collections import deque, defaultdict
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional
from loguru import logger

from config import CONFIG

# Chemin du fichier d'historique
FICHIER_HISTORIQUE = Path("data/spread_history.json")
TAILLE_MAX_DEQUE = 3000  # ~50h à 1 enregistrement/minute


@dataclass
class SnapshotSpread:
    """Un enregistrement instantané du spread."""
    timestamp_utc: str        # Format ISO : "2025-02-07T13:30:00"
    spread_points: float      # Spread brut en points MT5
    spread_pips: float        # Spread en pips (points / 10 pour XAUUSD)
    spread_usd: float         # Spread en USD pour 1 lot standard
    heure_utc: int            # Heure UTC (0–23) pour stats horaires
    jour_semaine: int         # 0=lundi, 6=dimanche

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(d: dict) -> "SnapshotSpread":
        return SnapshotSpread(
            timestamp_utc=d["timestamp_utc"],
            spread_points=float(d["spread_points"]),
            spread_pips=float(d["spread_pips"]),
            spread_usd=float(d.get("spread_usd", 0.0)),
            heure_utc=int(d["heure_utc"]),
            jour_semaine=int(d["jour_semaine"]),
        )

    def timestamp_dt(self) -> datetime:
        """Retourne le timestamp comme datetime."""
        return datetime.strptime(self.timestamp_utc[:19], "%Y-%m-%dT%H:%M:%S")


class MoniteurSpread:
    """
    Collecte le spread XAUUSD en temps réel et expose des statistiques.
    Doit être appelé par le scheduler APScheduler toutes les 60 secondes.
    """

    def __init__(self, connecteur=None) -> None:
        """
        Args:
            connecteur: Instance ConnecteurMT5 (peut être None en mode test).
        """
        self.connecteur = connecteur
        self.historique: deque = deque(maxlen=TAILLE_MAX_DEQUE)
        Path("data").mkdir(exist_ok=True)
        self._charger_historique()

    # ── Enregistrement ────────────────────────────────────────────────────

    def enregistrer_spread_actuel(self) -> Optional[SnapshotSpread]:
        """
        Enregistre le spread actuel depuis MT5.
        Appelé par le scheduler toutes les 60 secondes.

        Returns:
            SnapshotSpread enregistré, ou None si MT5 indisponible.
        """
        try:
            import MetaTrader5 as mt5
            info = mt5.symbol_info(CONFIG.SYMBOLE)
            if info is None:
                logger.debug(f"symbol_info() retourne None pour {CONFIG.SYMBOLE}")
                return None

            spread_points = float(info.spread)
            point = info.point if info.point > 0 else 0.01
            tick_value = info.trade_tick_value if info.trade_tick_value > 0 else 1.0
            tick_size = info.trade_tick_size if info.trade_tick_size > 0 else 0.01

            # Spread en USD pour 1 lot standard
            spread_usd = spread_points * point * tick_value / tick_size

            maintenant = datetime.utcnow()
            snapshot = SnapshotSpread(
                timestamp_utc=maintenant.strftime("%Y-%m-%dT%H:%M:%S"),
                spread_points=spread_points,
                spread_pips=spread_points / 10.0,
                spread_usd=round(spread_usd, 4),
                heure_utc=maintenant.hour,
                jour_semaine=maintenant.weekday(),
            )
            self.historique.append(snapshot)
            return snapshot

        except Exception as e:
            logger.debug(f"Impossible d'enregistrer le spread : {e}")
            return None

    def ajouter_snapshot_manuel(self, snapshot: SnapshotSpread) -> None:
        """
        Ajoute un snapshot manuellement (utilisé pour les tests et mocks).

        Args:
            snapshot: SnapshotSpread à ajouter.
        """
        self.historique.append(snapshot)

    # ── Accès aux données ─────────────────────────────────────────────────

    def get_spread_actuel(self) -> float:
        """
        Retourne le spread actuel en points.
        Si l'historique est vide, tente de lire depuis MT5.

        Returns:
            Spread en points, ou 0.0 si indisponible.
        """
        if self.historique:
            return self.historique[-1].spread_points

        snapshot = self.enregistrer_spread_actuel()
        return snapshot.spread_points if snapshot else 0.0

    def get_spread_moyen(self, minutes: int = 50) -> float:
        """
        Calcule le spread moyen sur les N dernières minutes.

        Args:
            minutes: Fenêtre de calcul en minutes.

        Returns:
            Spread moyen en points, ou 0.0 si pas assez de données.
        """
        limite = datetime.utcnow() - timedelta(minutes=minutes)
        recents = [
            s.spread_points for s in self.historique
            if s.timestamp_dt() >= limite
        ]
        return sum(recents) / len(recents) if recents else 0.0

    def get_nb_samples_recents(self, minutes: int = 50) -> int:
        """
        Retourne le nombre de samples dans la fenêtre.

        Args:
            minutes: Fenêtre en minutes.

        Returns:
            Nombre de samples.
        """
        limite = datetime.utcnow() - timedelta(minutes=minutes)
        return sum(1 for s in self.historique if s.timestamp_dt() >= limite)

    def get_spread_par_heure(self) -> Dict[int, float]:
        """
        Calcule le spread moyen par heure UTC sur tout l'historique.
        Utile pour identifier les heures les plus chères.

        Returns:
            Dict {heure_utc: spread_moyen_points}.
        """
        par_heure: defaultdict = defaultdict(list)
        for snap in self.historique:
            par_heure[snap.heure_utc].append(snap.spread_points)
        return {h: sum(v) / len(v) for h, v in par_heure.items()}

    def get_stats_24h(self) -> dict:
        """
        Statistiques complètes des dernières 24 heures.

        Returns:
            Dict avec spread actuel, min/max, moyenne, heure la plus chère.
        """
        limite_24h = datetime.utcnow() - timedelta(hours=24)
        snapshots_24h = [
            s for s in self.historique
            if s.timestamp_dt() >= limite_24h
        ]

        if not snapshots_24h:
            return {
                "spread_actuel": self.get_spread_actuel(),
                "spread_min_24h": None,
                "spread_max_24h": None,
                "spread_moyen_24h": None,
                "heure_min": None,
                "heure_max": None,
                "nb_samples_24h": 0,
            }

        spreads = [s.spread_points for s in snapshots_24h]
        spread_min = min(spreads)
        spread_max = max(spreads)

        snap_min = min(snapshots_24h, key=lambda s: s.spread_points)
        snap_max = max(snapshots_24h, key=lambda s: s.spread_points)

        return {
            "spread_actuel": self.get_spread_actuel(),
            "spread_moyen_50min": round(self.get_spread_moyen(50), 1),
            "spread_min_24h": round(spread_min, 1),
            "spread_max_24h": round(spread_max, 1),
            "spread_moyen_24h": round(sum(spreads) / len(spreads), 1),
            "heure_min": f"{snap_min.heure_utc:02d}h UTC",
            "heure_max": f"{snap_max.heure_utc:02d}h UTC",
            "nb_samples_24h": len(snapshots_24h),
        }

    # ── Persistance ───────────────────────────────────────────────────────

    def sauvegarder_historique(self) -> None:
        """
        Sauvegarde les N derniers jours d'historique dans data/spread_history.json.
        Nettoie automatiquement les entrées > SPREAD_HISTORY_RETENTION_DAYS.
        Limite la taille du fichier à SPREAD_HISTORY_MAX_MB.
        """
        try:
            limite = datetime.utcnow() - timedelta(
                days=CONFIG.SPREAD_HISTORY_RETENTION_DAYS
            )
            recents = [
                s.to_dict() for s in self.historique
                if s.timestamp_dt() >= limite
            ]

            data = {
                "symbole": CONFIG.SYMBOLE,
                "derniere_maj": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S"),
                "nb_snapshots": len(recents),
                "snapshots": recents,
            }

            contenu = json.dumps(data, ensure_ascii=False, separators=(",", ":"))

            # Vérifier la taille (5 MB max)
            taille_mb = len(contenu.encode("utf-8")) / (1024 * 1024)
            if taille_mb > CONFIG.SPREAD_HISTORY_MAX_MB:
                # Réduire de moitié
                milieu = len(recents) // 2
                data["snapshots"] = recents[milieu:]
                data["nb_snapshots"] = len(data["snapshots"])
                contenu = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
                logger.warning(
                    f"Historique spread tronqué ({taille_mb:.1f} MB > "
                    f"{CONFIG.SPREAD_HISTORY_MAX_MB} MB)"
                )

            FICHIER_HISTORIQUE.write_text(contenu, encoding="utf-8")
            logger.debug(
                f"Historique spread sauvegardé : "
                f"{data['nb_snapshots']} snapshots"
            )

        except Exception as e:
            logger.error(f"Impossible de sauvegarder l'historique spread : {e}")

    def _charger_historique(self) -> None:
        """Charge l'historique depuis le fichier JSON au démarrage."""
        if not FICHIER_HISTORIQUE.exists():
            logger.debug("Aucun historique spread existant — démarrage à zéro")
            return
        try:
            data = json.loads(FICHIER_HISTORIQUE.read_text(encoding="utf-8"))
            snapshots = data.get("snapshots", [])
            limite = datetime.utcnow() - timedelta(
                days=CONFIG.SPREAD_HISTORY_RETENTION_DAYS
            )
            for d in snapshots:
                try:
                    snap = SnapshotSpread.from_dict(d)
                    if snap.timestamp_dt() >= limite:
                        self.historique.append(snap)
                except Exception:
                    continue
            logger.info(
                f"Historique spread chargé : {len(self.historique)} snapshots"
            )
        except Exception as e:
            logger.warning(f"Impossible de charger l'historique spread : {e}")
