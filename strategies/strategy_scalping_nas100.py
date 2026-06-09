"""
strategies/strategy_scalping_nas100.py — Scalping NAS100 sur M1.

Hérite de ScalpingStrategy et ajoute :
- Filtre Killzone ICT obligatoire (NY Open 13h30 / NY Power 15h30)
- Réduction du risque pendant les earnings seasons
- EMAs 8/13/34 adaptées au M1 (plus réactives)
"""

from datetime import datetime
from typing import Optional, Tuple

from loguru import logger

from strategies.strategy_scalping_base import ScalpingStrategy
from strategies.strategy_base import SignalResult


class NAS100ScalpingStrategy(ScalpingStrategy):
    """
    Stratégie scalping NAS100 sur M1.
    Les Killzones ICT sont les seules fenêtres où le NASDAQ est institutionnellement
    prévisible. En dehors → signal refusé immédiatement.
    """

    def get_strategy_name(self) -> str:
        return "SCALPING_HYBRID_NAS100_M1"

    def evaluate_signal(
        self,
        current_time: Optional[datetime] = None,
    ) -> SignalResult:
        now = current_time or datetime.utcnow()

        # ── Filtre Killzone AVANT tout calcul (rapide) ─────────────────────
        if getattr(self.config, "KILLZONES_ENABLED", False):
            in_kz, kz_reason = self._check_killzone(now)
            if not in_kz:
                return SignalResult(
                    signal=None, rejected_by="killzone", reason=kz_reason
                )

        # ── Adapter le risque en période de résultats ──────────────────────
        if self._is_earnings_season(now):
            earnings_risk = getattr(self.config, "EARNINGS_RISK_PCT", 0.25)
            logger.debug(
                f"NAS100 earnings season — risque réduit à {earnings_risk}%"
            )

        # ── Signal scalping de base ────────────────────────────────────────
        result = super().evaluate_signal(now)

        # Marquer le risque réduit si earnings
        if result.signal is not None and self._is_earnings_season(now):
            result.risk_multiplier = getattr(
                self.config, "EARNINGS_RISK_PCT", 0.25
            ) / max(getattr(self.config, "RISQUE_PAR_TRADE_PCT", 0.5), 0.001)

        return result

    # ── Vérification Killzone ──────────────────────────────────────────────

    def _check_killzone(self, now: datetime) -> Tuple[bool, str]:
        """
        Vérifie si on est dans une Killzone ICT active.
        NAS100 : NY Open 13h30–15h30 | NY Power 15h30–17h00 UTC.
        """
        sessions = getattr(self.config, "SESSIONS", [])

        for kz in sessions:
            h_debut = kz.get("open", 0)
            m_debut = kz.get("open_min", kz.get("minute_open", 0))
            h_fin   = kz.get("close", 0)
            m_fin   = kz.get("close_min", kz.get("minute_close", 0))

            minutes_now   = now.hour * 60 + now.minute
            minutes_debut = h_debut * 60 + m_debut
            minutes_fin   = h_fin * 60 + m_fin

            if minutes_debut <= minutes_now <= minutes_fin:
                return True, f"Killzone ICT active : {kz.get('name', '?')}"

        return (
            False,
            f"Hors Killzone NAS100 ({now.strftime('%H:%M')} UTC) | "
            f"NY Open 13h30 | NY Power 15h30"
        )

    def _is_earnings_season(self, now: datetime) -> bool:
        """Détecte les mois de résultats trimestriels."""
        mois = getattr(self.config, "EARNINGS_SEASON_MONTHS", [1, 4, 7, 10])
        return now.month in mois
