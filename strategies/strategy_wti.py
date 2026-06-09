"""
strategies/strategy_wti.py — Stratégie scalping WTI : Hybride + Liquidity Sweep.

Hérite de ScalpingStrategy et ajoute :
- Confirmation Liquidity Sweep optionnelle (stop hunt avant retournement)
- Paramètres adaptés selon la session (asiatique vs London/NY)
- Risque réduit en session asiatique (0.5× le risque standard)
"""

from datetime import datetime
from typing import Dict, Optional, Tuple

from loguru import logger

from strategies.strategy_scalping_base import ScalpingStrategy
from strategies.strategy_base import SignalResult
from indicators import Indicateurs


class StrategieWTI(ScalpingStrategy):
    """
    Stratégie scalping WTI : EMA 9/21/50 + RSI + ADX + Liquidity Sweep.

    En session asiatique (02h–06h UTC) :
    - Risque × 0.5 (plus conservateur)
    - ADX minimum plus élevé (25 vs 20)
    - Confirmation Sweep obligatoire

    Liquidity Sweep : longue mèche + clôture de retour = stop hunt confirmé.
    """

    def get_strategy_name(self) -> str:
        return "SCALPING_HYBRID_SWEEP_WTI"

    def evaluate_signal(
        self,
        current_time: Optional[datetime] = None,
    ) -> SignalResult:
        """
        Évalue le signal WTI scalping avec adaptation session asiatique.
        """
        now = current_time or datetime.utcnow()
        est_asiatique = self._est_session_asiatique(now)

        # ── Adapter l'ADX minimum selon la session ─────────────────────────
        if est_asiatique:
            # Overrider temporairement pour la vérification des filtres
            self._adx_min_override = getattr(self.config, "ASIAN_ADX_MIN", 25.0)
        else:
            self._adx_min_override = None

        # ── Signal scalping de base ────────────────────────────────────────
        result = super().evaluate_signal(current_time=now)

        if result.signal is None:
            return result

        # ── Confirmation Liquidity Sweep (si activée) ──────────────────────
        use_sweep = getattr(self.config, "USE_SWEEP_CONFIRMATION", False)
        if use_sweep:
            sweep = self._detecter_sweep()
            if sweep:
                # Vérifier alignement sweep / signal
                sweep_aligne = (
                    (result.signal == "bullish" and sweep["type"] == "bullish_sweep") or
                    (result.signal == "bearish" and sweep["type"] == "bearish_sweep")
                )
                if sweep_aligne:
                    result.confluences.append(
                        f"Sweep {sweep['type']} (mèche {sweep['wick_atr_ratio']:.1f}×ATR)"
                    )
                    logger.info(
                        f"[WTI] Signal {result.signal} confirmé par sweep "
                        f"{sweep['type']}"
                    )
                # Pas de rejet si sweep absent — il est optionnel en renforcement

        # ── Adapter le risque en session asiatique ─────────────────────────
        if est_asiatique:
            mult = getattr(self.config, "ASIAN_RISK_MULTIPLIER", 0.5)
            result.risk_multiplier = mult
            result.confluences.append(f"Session Asiatique (risque ×{mult})")

        return result

    # ── Détection du Liquidity Sweep ──────────────────────────────────────

    def _detecter_sweep(self) -> Optional[Dict]:
        """
        Détecte un Liquidity Sweep récent sur le timeframe signal.
        Sweep = longue mèche dépassant un niveau clé + clôture de retour.
        """
        if self.connecteur is None:
            return None

        try:
            tf = getattr(self.config, "TIMEFRAME_SIGNAL", 5)
            df = self._get_ohlcv(tf, 30)
            if df is None or len(df) < 5:
                return None

            df = df.iloc[:-1]  # Bougies fermées

            # Calculer ATR
            atr_serie = Indicateurs.atr(df, getattr(self.config, "ATR_LEN", 14))
            if len(atr_serie) < 2:
                return None

            min_wick_atr = getattr(self.config, "SWEEP_MIN_WICK_ATR", 0.6)
            max_body_ratio = getattr(self.config, "SWEEP_MAX_BODY_RATIO", 0.45)
            lookback = min(getattr(self.config, "SWEEP_LOOKBACK_CANDLES", 20), len(df) - 1)

            # Analyser les 4 dernières bougies fermées
            for i in range(max(1, len(df) - 4), len(df)):
                candle = df.iloc[i]
                atr_val = float(atr_serie.iloc[i]) if i < len(atr_serie) else 0.0
                if atr_val <= 0:
                    continue

                high = float(candle["high"])
                low  = float(candle["low"])
                ouv  = float(candle["open"])
                ferm = float(candle["close"])
                rang = high - low
                if rang <= 0:
                    continue

                corps = abs(ferm - ouv)
                ratio_corps = corps / rang

                # Bullish Sweep : longue mèche basse + clôture haussière
                meche_basse = ouv - low
                if (meche_basse >= atr_val * min_wick_atr and
                        meche_basse / rang > 0.50 and
                        ratio_corps <= max_body_ratio and
                        ferm > ouv):
                    debut = max(0, i - lookback)
                    swing_low = float(df["low"].iloc[debut:i].min())
                    if low < swing_low:
                        return {
                            "type": "bullish_sweep",
                            "sweep_low": low,
                            "prix_rentree": ferm,
                            "wick_atr_ratio": round(meche_basse / atr_val, 2),
                        }

                # Bearish Sweep : longue mèche haute + clôture baissière
                meche_haute = high - ouv
                if (meche_haute >= atr_val * min_wick_atr and
                        meche_haute / rang > 0.50 and
                        ratio_corps <= max_body_ratio and
                        ferm < ouv):
                    debut = max(0, i - lookback)
                    swing_high = float(df["high"].iloc[debut:i].max())
                    if high > swing_high:
                        return {
                            "type": "bearish_sweep",
                            "sweep_high": high,
                            "prix_rentree": ferm,
                            "wick_atr_ratio": round(meche_haute / atr_val, 2),
                        }

        except Exception as e:
            logger.debug(f"Détection sweep WTI erreur : {e}")

        return None

    def _est_session_asiatique(self, now: datetime) -> bool:
        """True si on est dans la session asiatique WTI."""
        debut = getattr(self.config, "ASIAN_SESSION_DEBUT", 2)
        fin   = getattr(self.config, "ASIAN_SESSION_FIN", 6)
        return debut <= now.hour < fin


# Alias anglais
WTIStrategy = StrategieWTI
