"""
strategies/strategy_scalping_sp500.py — Scalping SP500 sur M5 + FVG.

Hérite de ScalpingStrategy et ajoute :
- Confirmation Fair Value Gap (FVG) sur M15 optionnelle
- Le SP500 comble systématiquement ses FVG → confirmation réduit les faux signaux
"""

import pandas as pd
from datetime import datetime
from typing import Dict, List, Optional

from loguru import logger

from strategies.strategy_scalping_base import ScalpingStrategy
from strategies.strategy_base import SignalResult


class SP500ScalpingStrategy(ScalpingStrategy):
    """
    Stratégie scalping SP500 sur M5 avec confirmation FVG M15.

    Si USE_FVG_CONFIRMATION=True : seuls les signaux avec un FVG aligné
    sont validés. Le FVG est détecté sur le timeframe de confirmation (M15).
    """

    def get_strategy_name(self) -> str:
        return "SCALPING_HYBRID_FVG_SP500"

    def evaluate_signal(
        self,
        current_time: Optional[datetime] = None,
    ) -> SignalResult:
        # ── Signal scalping de base ────────────────────────────────────────
        result = super().evaluate_signal(current_time)

        if result.signal is None:
            return result

        # ── Confirmation FVG (si activée) ──────────────────────────────────
        if not getattr(self.config, "USE_FVG_CONFIRMATION", False):
            return result

        fvg = self._detect_fvg(result.signal)
        if fvg is None:
            return SignalResult(
                signal=None,
                rejected_by="fvg_confirmation",
                reason=f"Pas de FVG M15 confirmant le signal SP500 ({result.signal})",
            )

        result.confluences.append(
            f"FVG {fvg['type']} {fvg['size_pct']:.2f}% (âge:{fvg['age']}b)"
        )
        logger.debug(
            f"[SP500] Signal {result.signal} confirmé par FVG "
            f"{fvg['size_pct']:.2f}%"
        )
        return result

    # ── Détection FVG sur M15 ──────────────────────────────────────────────

    def _detect_fvg(self, direction: str) -> Optional[Dict]:
        """
        Détecte le FVG le plus récent sur le timeframe de confirmation (M15).
        FVG haussier : high[N-1] < low[N+1] → zone de déséquilibre haussier
        FVG baissier : low[N-1] > high[N+1] → zone de déséquilibre baissier
        """
        if self.connecteur is None:
            return None

        try:
            tf = getattr(self.config, "TIMEFRAME_CONFIRM", 15)
            df = self._get_ohlcv(tf, 30)
            if df is None or len(df) < 3:
                return None

            df = df.iloc[:-1]  # Bougies fermées uniquement
            fvg_min_pct  = getattr(self.config, "FVG_MIN_SIZE_PCT", 0.10)
            fvg_max_age  = getattr(self.config, "FVG_MAX_AGE_CANDLES", 10)

            for i in range(1, len(df) - 1):
                prev_c = df.iloc[i - 1]
                curr_c = df.iloc[i]
                next_c = df.iloc[i + 1]
                age = len(df) - i - 1

                if age > fvg_max_age:
                    continue

                prix_ref = float(curr_c["close"])
                if prix_ref <= 0:
                    continue

                # FVG haussier
                if direction == "bullish" and float(prev_c["high"]) < float(next_c["low"]):
                    taille = float(next_c["low"]) - float(prev_c["high"])
                    pct = taille / prix_ref * 100
                    if pct >= fvg_min_pct:
                        return {"type": "bull", "size_pct": round(pct, 3), "age": age}

                # FVG baissier
                if direction == "bearish" and float(prev_c["low"]) > float(next_c["high"]):
                    taille = float(prev_c["low"]) - float(next_c["high"])
                    pct = taille / prix_ref * 100
                    if pct >= fvg_min_pct:
                        return {"type": "bear", "size_pct": round(pct, 3), "age": age}

        except Exception as e:
            logger.debug(f"Détection FVG SP500 erreur : {e}")

        return None
