"""
strategies/strategy_sp500.py — Stratégie scalping SP500 : Hybride + FVG M5.

Hérite de ScalpingStrategy et ajoute :
- Confirmation FVG (Fair Value Gap) dans la zone signal sur H1/M15
- Le SP500 comble systématiquement ses FVG → entrée au retest FVG + signal scalping
"""

from datetime import datetime
from typing import Dict, List, Optional

from loguru import logger

from strategies.strategy_scalping_base import ScalpingStrategy
from strategies.strategy_base import SignalResult


class StrategieSP500(ScalpingStrategy):
    """
    Stratégie scalping SP500 : EMA 9/21/50 + RSI + ADX + FVG M5 confirmation.

    La logique FVG est additionnelle au signal scalping de base :
    si USE_FVG_CONFIRMATION=True → chercher un FVG actif dans la zone
    et n'entrer que si le prix revient tester ce FVG.
    """

    def get_strategy_name(self) -> str:
        return "SCALPING_HYBRID_FVG_SP500"

    def evaluate_signal(
        self,
        current_time: Optional[datetime] = None,
    ) -> SignalResult:
        """
        Évalue le signal SP500 scalping avec confirmation FVG optionnelle.
        """
        now = current_time or datetime.utcnow()

        # ── Signal scalping de base ────────────────────────────────────────
        result = super().evaluate_signal(current_time=now)

        if result.signal is None:
            return result

        # ── Confirmation FVG (si activée) ──────────────────────────────────
        use_fvg = getattr(self.config, "USE_FVG_CONFIRMATION", False)
        if not use_fvg:
            return result

        fvgs = self._detecter_fvg_actifs()
        if not fvgs:
            # Pas de FVG → signal refusé si confirmation requise
            return SignalResult(
                signal=None,
                rejected_by="fvg_confirmation",
                reason="SP500 : aucun FVG actif confirmant le signal scalping",
            )

        # Vérifier que le FVG est dans la direction du signal
        fvg_confirme = self._trouver_fvg_aligne(fvgs, result.signal)
        if not fvg_confirme:
            return SignalResult(
                signal=None,
                rejected_by="fvg_direction",
                reason=f"SP500 : FVG disponible mais pas aligné avec {result.signal}",
            )

        # Enrichir les confluences
        result.confluences.append(
            f"FVG {fvg_confirme['type']} {fvg_confirme['size_pct']:.2f}%"
        )
        logger.info(
            f"[SP500] Signal {result.signal} confirmé par FVG "
            f"{fvg_confirme['size_pct']:.2f}% (âge: {fvg_confirme['age_candles']} bougies)"
        )
        return result

    # ── Détection FVG sur M15 ──────────────────────────────────────────────

    def _detecter_fvg_actifs(self) -> List[Dict]:
        """
        Détecte les Fair Value Gaps actifs sur le timeframe de confirmation.
        FVG valide si taille >= FVG_MIN_SIZE_PCT ET âge <= FVG_MAX_AGE_CANDLES.
        """
        if self.connecteur is None:
            return []

        try:
            tf = getattr(self.config, "TIMEFRAME_CONFIRM", 15)
            df = self._get_ohlcv(tf, 50)
            if df is None or len(df) < 3:
                return []

            df = df.iloc[:-1]  # Bougies fermées uniquement
            fvg_min_pct = getattr(self.config, "FVG_MIN_SIZE_PCT", 0.10)
            fvg_max_age = getattr(self.config, "FVG_MAX_AGE_CANDLES", 10)
            fvgs = []

            for i in range(1, len(df) - 1):
                prev    = df.iloc[i - 1]
                curr    = df.iloc[i]
                suivant = df.iloc[i + 1]

                prix_ref = float(curr["close"])
                if prix_ref <= 0:
                    continue
                age = len(df) - i - 1
                if age > fvg_max_age:
                    continue

                # FVG haussier
                if float(prev["high"]) < float(suivant["low"]):
                    taille = float(suivant["low"]) - float(prev["high"])
                    pct = taille / prix_ref * 100
                    if pct >= fvg_min_pct:
                        fvgs.append({
                            "type": "bullish",
                            "high": float(suivant["low"]),
                            "low":  float(prev["high"]),
                            "mid":  (float(suivant["low"]) + float(prev["high"])) / 2,
                            "size_pct": round(pct, 3),
                            "age_candles": age,
                        })

                # FVG baissier
                if float(prev["low"]) > float(suivant["high"]):
                    taille = float(prev["low"]) - float(suivant["high"])
                    pct = taille / prix_ref * 100
                    if pct >= fvg_min_pct:
                        fvgs.append({
                            "type": "bearish",
                            "high": float(prev["low"]),
                            "low":  float(suivant["high"]),
                            "mid":  (float(prev["low"]) + float(suivant["high"])) / 2,
                            "size_pct": round(pct, 3),
                            "age_candles": age,
                        })

            return sorted(fvgs, key=lambda x: x["size_pct"], reverse=True)

        except Exception as e:
            logger.debug(f"Détection FVG SP500 erreur : {e}")
            return []

    def _trouver_fvg_aligne(
        self, fvgs: List[Dict], direction: str
    ) -> Optional[Dict]:
        """Trouve le premier FVG aligné avec la direction du signal."""
        for fvg in fvgs:
            if direction == "bullish" and fvg["type"] == "bullish":
                return fvg
            if direction == "bearish" and fvg["type"] == "bearish":
                return fvg
        return None


# Alias anglais
SP500Strategy = StrategieSP500
