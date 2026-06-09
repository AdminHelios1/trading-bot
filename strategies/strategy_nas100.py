"""
strategies/strategy_nas100.py — Stratégie scalping NAS100 : Hybride + Killzones ICT.

Hérite de ScalpingStrategy et ajoute :
- Vérification obligatoire des Killzones ICT (NY Open / NY Power)
- Réduction du risque pendant les earnings seasons
- Paramètres EMAs 8/13/34 adaptés au M1
"""

from datetime import datetime
from typing import Optional, Tuple

from loguru import logger

from strategies.strategy_scalping_base import ScalpingStrategy
from strategies.strategy_base import SignalResult


class StrategieNAS100(ScalpingStrategy):
    """
    Stratégie scalping NAS100 : EMA 8/13/34 + RSI + ADX + Killzones ICT.

    Le NASDAQ est le plus prévisible dans les Killzones ICT :
    - NY Open KZ  : 13h30–15h30 UTC
    - NY Power KZ : 15h30–17h00 UTC

    En dehors de ces fenêtres → signal refusé.
    """

    def get_strategy_name(self) -> str:
        return "SCALPING_HYBRID_NAS100_M1"

    def evaluate_signal(
        self,
        current_time: Optional[datetime] = None,
    ) -> SignalResult:
        """
        Évalue le signal NAS100 scalping avec vérification Killzone.
        """
        now = current_time or datetime.utcnow()

        # ── 1. Filtres communs ─────────────────────────────────────────────
        common_ok, common_reason = self.run_common_filters(now)
        if not common_ok:
            return SignalResult(signal=None, rejected_by="common", reason=common_reason)

        # ── 2. Killzone obligatoire pour NAS100 ────────────────────────────
        kz_ok, kz_reason = self._verifier_killzone(now)
        if not kz_ok:
            return SignalResult(signal=None, rejected_by="killzone", reason=kz_reason)

        # ── 3. Earnings season → risque réduit (log seulement) ────────────
        if self._est_periode_earnings(now):
            logger.info(
                f"NAS100 : période de résultats trimestriels "
                f"— risque réduit à {self._get_risque_effectif(now)}%"
            )

        # ── 4. Cooldown ────────────────────────────────────────────────────
        if not self._check_cooldown():
            return SignalResult(signal=None, rejected_by="cooldown",
                                reason="Cooldown NAS100 actif")

        # ── 5. Signal scalping de base ─────────────────────────────────────
        # Appel de la logique commune depuis ScalpingStrategy
        # mais on adapte le risque si earnings
        result = super().evaluate_signal(current_time=now)

        # Adapter le risque pendant les earnings
        if result.signal is not None and self._est_periode_earnings(now):
            result.risk_multiplier = self._get_risque_effectif(now)

        return result

    # ── Méthodes spécifiques NAS100 ────────────────────────────────────────

    def _verifier_killzone(self, now: datetime) -> Tuple[bool, str]:
        """Vérifie si on est dans une Killzone ICT active."""
        killzones = getattr(self.config, "KILLZONES", None)
        if not killzones:
            # Utiliser les sessions si KILLZONES non défini
            return True, "Sessions utilisées comme Killzones"

        hm = now.hour * 60 + now.minute

        for kz in killzones:
            debut = kz.get("open", 0) * 60 + kz.get("minute_open", 0)
            fin   = kz.get("close", 0) * 60 + kz.get("minute_close", 0)
            if debut <= hm <= fin:
                return True, f"Killzone ICT active : {kz.get('name', '?')}"

        return (
            False,
            f"Hors Killzone NAS100 ({now.strftime('%H:%M')} UTC) | "
            f"Prochaine : NY Open 13h30 UTC"
        )

    def _est_periode_earnings(self, now: datetime) -> bool:
        """Détecte les mois de résultats trimestriels."""
        mois = getattr(self.config, "EARNINGS_SEASON_MONTHS", [1, 4, 7, 10])
        return now.month in mois

    def _get_risque_effectif(self, now: datetime) -> float:
        """Risque effectif — réduit pendant les earnings."""
        risque_base = getattr(self.config, "RISQUE_PAR_TRADE_PCT", 0.5)
        earnings_risque = getattr(self.config, "EARNINGS_RISK_PCT", 0.25)
        reduce = getattr(self.config, "REDUCE_RISK_EARNINGS", True)

        if self._est_periode_earnings(now) and reduce:
            return earnings_risque
        return risque_base


# Alias anglais
NAS100Strategy = StrategieNAS100
