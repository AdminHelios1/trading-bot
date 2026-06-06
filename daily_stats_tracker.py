"""
daily_stats_tracker.py — Suivi des statistiques journalières de trading.
Reset automatique à 00h00 UTC. Alimenté par trade_manager.py à chaque clôture.
"""

import json
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import List, Optional
from loguru import logger

from config import CONFIG

FICHIER_STATS = Path("data/daily_stats.json")
DOSSIER_ARCHIVES = Path("data/daily_archives")


@dataclass
class ResultatTrade:
    """Résultat d'un trade clôturé — alimenté par trade_manager.py."""
    id_trade: str
    heure_fermeture: str          # ISO format
    direction: str                # "LONG" ou "SHORT"
    r_realise: float              # R réalisé (+1.5, -1.0, etc.)
    pnl_usd: float
    pnl_pct: float                # % du capital
    est_gagnant: bool
    raison_fermeture: str
    ob_score: int
    condition_marche: str         # Tendance H4 au moment de l'entrée


@dataclass
class StatsJournalieres:
    """Statistiques de la journée courante — reset à 00h00 UTC."""
    date_utc: str
    balance_ouverture: float
    balance_actuelle: float
    balance_pic: float
    trades: List[dict] = field(default_factory=list)  # sérialisés en dict
    pertes_consecutives: int = 0
    gains_consecutifs: int = 0
    pnl_usd: float = 0.0
    pnl_pct: float = 0.0
    drawdown_pct: float = 0.0        # Depuis le pic de la journée
    drawdown_max_pct: float = 0.0    # Pic de drawdown du jour
    nb_trades: int = 0
    nb_gagnants: int = 0
    nb_perdants: int = 0
    win_rate: float = 0.0
    derniere_maj: str = field(default_factory=lambda: datetime.utcnow().isoformat())


class SuiveurStatsJournalieres:
    """
    Suit les statistiques de trading de la journée.
    Reset automatique à 00h00 UTC.
    Survit aux redémarrages via persistance JSON.
    """

    def __init__(self, connecteur=None) -> None:
        """
        Args:
            connecteur: Instance ConnecteurMT5 (optionnel — pour lire la balance).
        """
        self.connecteur = connecteur
        self._stats: Optional[StatsJournalieres] = None
        FICHIER_STATS.parent.mkdir(exist_ok=True)
        DOSSIER_ARCHIVES.mkdir(exist_ok=True)
        self._charger_ou_initialiser()

    # ── Initialisation ────────────────────────────────────────────────────

    def _charger_ou_initialiser(self) -> None:
        """Charge les stats du jour ou initialise si nouveau jour."""
        aujourd_hui = datetime.utcnow().strftime("%Y-%m-%d")
        sauvegardees = self._charger_depuis_disque()

        if sauvegardees and sauvegardees.date_utc == aujourd_hui:
            self._stats = sauvegardees
            logger.info(
                f"Stats journalières chargées — {aujourd_hui} | "
                f"{self._stats.nb_trades} trades | "
                f"DD: {self._stats.drawdown_pct:.2f}%"
            )
        else:
            balance = self._lire_balance()
            self._stats = StatsJournalieres(
                date_utc=aujourd_hui,
                balance_ouverture=balance,
                balance_actuelle=balance,
                balance_pic=balance,
            )
            self._sauvegarder()
            logger.info(
                f"Nouveau jour UTC — Stats réinitialisées | "
                f"Balance ouverture: ${balance:.2f}"
            )

    # ── Enregistrement des trades ──────────────────────────────────────────

    def enregistrer_trade(self, resultat: ResultatTrade) -> None:
        """
        Enregistre un trade clôturé et met à jour toutes les métriques.
        Appelé par trade_manager.py après chaque fermeture de position.

        Args:
            resultat: ResultatTrade avec tous les détails du trade fermé.
        """
        stats = self._stats
        stats.trades.append({
            "id_trade": resultat.id_trade,
            "heure": resultat.heure_fermeture,
            "direction": resultat.direction,
            "r": resultat.r_realise,
            "pnl": resultat.pnl_usd,
            "gagnant": resultat.est_gagnant,
            "raison": resultat.raison_fermeture,
            "ob_score": resultat.ob_score,
        })

        stats.nb_trades += 1
        stats.pnl_usd += resultat.pnl_usd
        stats.balance_actuelle += resultat.pnl_usd
        stats.pnl_pct = (
            (stats.balance_actuelle - stats.balance_ouverture)
            / stats.balance_ouverture * 100
            if stats.balance_ouverture > 0 else 0.0
        )

        # Mise à jour du pic de balance
        if stats.balance_actuelle > stats.balance_pic:
            stats.balance_pic = stats.balance_actuelle

        # Drawdown depuis le pic de la journée
        stats.drawdown_pct = (
            (stats.balance_pic - stats.balance_actuelle)
            / stats.balance_pic * 100
            if stats.balance_pic > 0 else 0.0
        )
        stats.drawdown_max_pct = max(stats.drawdown_max_pct, stats.drawdown_pct)

        # Pertes/gains consécutifs
        if resultat.est_gagnant:
            stats.nb_gagnants += 1
            stats.gains_consecutifs += 1
            stats.pertes_consecutives = 0  # Reset des pertes consécutives
        else:
            stats.nb_perdants += 1
            stats.pertes_consecutives += 1
            stats.gains_consecutifs = 0

        # Win rate
        stats.win_rate = (
            stats.nb_gagnants / stats.nb_trades * 100
            if stats.nb_trades > 0 else 0.0
        )

        stats.derniere_maj = datetime.utcnow().isoformat()
        self._sauvegarder()

        logger.info(
            f"Trade enregistré [{resultat.id_trade}] | "
            f"{'WIN' if resultat.est_gagnant else 'LOSS'} {resultat.r_realise:+.2f}R | "
            f"Pertes consécutives: {stats.pertes_consecutives} | "
            f"DD jour: {stats.drawdown_pct:.2f}% | "
            f"P&L jour: ${stats.pnl_usd:+.2f}"
        )

    # ── Reset journalier ──────────────────────────────────────────────────

    def verifier_reset_minuit(self) -> bool:
        """
        Vérifie si on a changé de jour UTC et réinitialise si nécessaire.
        Appelé par le scheduler toutes les minutes.

        Returns:
            True si un reset a été effectué.
        """
        aujourd_hui = datetime.utcnow().strftime("%Y-%m-%d")
        if self._stats and self._stats.date_utc != aujourd_hui:
            logger.info(
                f"Minuit UTC — Reset des stats journalières "
                f"(jour terminé: {self._stats.date_utc})"
            )
            self._archiver_journee(self._stats)
            self._charger_ou_initialiser()
            return True
        return False

    # ── Accesseurs ────────────────────────────────────────────────────────

    @property
    def stats(self) -> StatsJournalieres:
        return self._stats

    def get_pertes_consecutives(self) -> int:
        return self._stats.pertes_consecutives if self._stats else 0

    def get_drawdown_pct(self) -> float:
        return self._stats.drawdown_pct if self._stats else 0.0

    def get_pnl_pct(self) -> float:
        return self._stats.pnl_pct if self._stats else 0.0

    def get_resume(self) -> dict:
        """Résumé pour le dashboard et le circuit breaker."""
        if not self._stats:
            return {}
        s = self._stats
        return {
            "date": s.date_utc,
            "nb_trades": s.nb_trades,
            "nb_gagnants": s.nb_gagnants,
            "nb_perdants": s.nb_perdants,
            "win_rate": round(s.win_rate, 1),
            "pertes_consecutives": s.pertes_consecutives,
            "gains_consecutifs": s.gains_consecutifs,
            "drawdown_pct": round(s.drawdown_pct, 2),
            "drawdown_max_pct": round(s.drawdown_max_pct, 2),
            "pnl_usd": round(s.pnl_usd, 2),
            "pnl_pct": round(s.pnl_pct, 2),
            "balance_ouverture": s.balance_ouverture,
            "balance_actuelle": s.balance_actuelle,
        }

    # ── Persistance ───────────────────────────────────────────────────────

    def _sauvegarder(self) -> None:
        """Sauvegarde les stats dans data/daily_stats.json."""
        try:
            data = asdict(self._stats)
            FICHIER_STATS.write_text(
                json.dumps(data, indent=2, ensure_ascii=False, default=str),
                encoding="utf-8",
            )
        except Exception as e:
            logger.error(f"Impossible de sauvegarder les stats journalières : {e}")

    def _charger_depuis_disque(self) -> Optional[StatsJournalieres]:
        """Charge les stats depuis data/daily_stats.json."""
        if not FICHIER_STATS.exists():
            return None
        try:
            data = json.loads(FICHIER_STATS.read_text(encoding="utf-8"))
            stats = StatsJournalieres(**{
                k: v for k, v in data.items()
                if k in StatsJournalieres.__dataclass_fields__
            })
            return stats
        except Exception as e:
            logger.warning(f"Impossible de charger les stats journalières : {e}")
            return None

    def _archiver_journee(self, stats: StatsJournalieres) -> None:
        """Archive les stats de la journée terminée."""
        try:
            chemin = DOSSIER_ARCHIVES / f"stats_{stats.date_utc}.json"
            data = asdict(stats)
            chemin.write_text(
                json.dumps(data, indent=2, ensure_ascii=False, default=str),
                encoding="utf-8",
            )
            logger.debug(f"Journée archivée : {chemin}")
        except Exception as e:
            logger.error(f"Impossible d'archiver les stats : {e}")

    def _lire_balance(self) -> float:
        """Lit la balance actuelle depuis MT5."""
        if self.connecteur is not None:
            try:
                info = self.connecteur.get_info_compte()
                if info:
                    return float(info.get("balance", 0.0))
            except Exception:
                pass
        return 10_000.0  # Valeur par défaut pour les tests
