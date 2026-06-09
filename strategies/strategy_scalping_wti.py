"""
strategies/strategy_scalping_wti.py — Scalping WTI sur M5 + Liquidity Sweep.

Hérite de ScalpingStrategy et ajoute :
- Confirmation Liquidity Sweep (stop hunt avant retournement)
- Paramètres adaptatifs : session asiatique → risque ×0.5, ADX min 25
"""

from datetime import datetime
from typing import Dict, Optional

from loguru import logger

from strategies.strategy_scalping_base import ScalpingStrategy
from strategies.strategy_base import SignalResult
from indicators import Indicateurs


class WTIScalpingStrategy(ScalpingStrategy):
    """
    Stratégie scalping WTI sur M5 avec Liquidity Sweep et session asiatique.

    Session asiatique (02h–06h UTC) :
    - Risque × ASIAN_RISK_MULT (défaut 0.5)
    - ADX minimum ASIAN_ADX_MIN (défaut 25 au lieu de 20)

    Liquidity Sweep (optionnel) : confirmation que les stops ont été chassés
    avant le signal = probabilité plus élevée de retournement réel.
    """

    def get_strategy_name(self) -> str:
        return "SCALPING_HYBRID_SWEEP_WTI"

    def evaluate_signal(
        self,
        current_time: Optional[datetime] = None,
    ) -> SignalResult:
        now = current_time or datetime.utcnow()
        est_asiatique = self._est_session_asiatique(now)

        # ── Adapter l'ADX minimum en session asiatique ─────────────────────
        adx_min_original = getattr(self.config, "ADX_MIN", 20.0)
        if est_asiatique:
            self.config.ADX_MIN = getattr(self.config, "ASIAN_ADX_MIN", 25.0)

        # ── Signal scalping de base ────────────────────────────────────────
        result = super().evaluate_signal(now)

        # Restaurer l'ADX min original
        self.config.ADX_MIN = adx_min_original

        if result.signal is None:
            return result

        # ── Adapter le risque en session asiatique ─────────────────────────
        if est_asiatique:
            mult = getattr(self.config, "ASIAN_RISK_MULT", 0.5)
            result.risk_multiplier = mult
            result.confluences.append(f"Session asiatique (risque ×{mult})")

        # ── Confirmation Liquidity Sweep (optionnel) ───────────────────────
        if getattr(self.config, "USE_SWEEP_CONFIRMATION", False):
            sweep = self._detect_liquidity_sweep(result.signal)
            if sweep:
                result.confluences.append(
                    f"Sweep détecté ({sweep['wick_atr']:.1f}×ATR)"
                )

        return result

    # ── Détection Liquidity Sweep ──────────────────────────────────────────

    def _detect_liquidity_sweep(self, direction: str) -> Optional[Dict]:
        """
        Détecte un Liquidity Sweep récent sur le timeframe signal.
        Sweep = longue mèche dépassant un niveau clé + clôture de retour.
        """
        if self.connecteur is None:
            return None

        try:
            tf = getattr(self.config, "TIMEFRAME_SIGNAL", 5)
            df = self._get_ohlcv(tf, 15)
            if df is None or len(df) < 3:
                return None

            df = df.iloc[:-1]
            atr_serie = Indicateurs.atr(df, getattr(self.config, "ATR_LEN", 14))
            if len(atr_serie) < 2:
                return None

            min_wick_atr  = getattr(self.config, "SWEEP_MIN_WICK_ATR", 0.6)
            max_body_ratio = getattr(self.config, "SWEEP_MAX_BODY_RATIO", 0.45)

            for i in range(max(1, len(df) - 4), len(df)):
                c    = df.iloc[i]
                atr  = float(atr_serie.iloc[i]) if i < len(atr_serie) else 0.0
                if atr <= 0:
                    continue

                h = float(c["high"])
                l = float(c["low"])
                o = float(c["open"])
                f = float(c["close"])
                rang = h - l
                if rang <= 0:
                    continue

                corps = abs(f - o)
                ratio_corps = corps / rang

                # Bullish sweep : longue mèche basse + clôture haussière
                if direction == "bullish":
                    meche_basse = min(o, f) - l
                    wick_atr = meche_basse / atr
                    if (wick_atr >= min_wick_atr and
                            meche_basse / rang > 0.50 and
                            ratio_corps <= max_body_ratio and
                            f > o):
                        return {"wick_atr": round(wick_atr, 2)}

                # Bearish sweep : longue mèche haute + clôture baissière
                if direction == "bearish":
                    meche_haute = h - max(o, f)
                    wick_atr = meche_haute / atr
                    if (wick_atr >= min_wick_atr and
                            meche_haute / rang > 0.50 and
                            ratio_corps <= max_body_ratio and
                            f < o):
                        return {"wick_atr": round(wick_atr, 2)}

        except Exception as e:
            logger.debug(f"Détection sweep WTI erreur : {e}")

        return None

    def _est_session_asiatique(self, now: datetime) -> bool:
        """True si on est dans la session asiatique WTI (02h–06h UTC)."""
        if not getattr(self.config, "ASIAN_SESSION_ENABLED", False):
            return False
        debut = getattr(self.config, "ASIAN_SESSION_DEBUT",
                        getattr(self.config, "ASIAN_SESSION_START", 2))
        fin   = getattr(self.config, "ASIAN_SESSION_FIN",
                        getattr(self.config, "ASIAN_SESSION_END", 6))
        return debut <= now.hour < fin
