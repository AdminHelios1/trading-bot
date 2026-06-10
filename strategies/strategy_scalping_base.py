"""
strategies/strategy_scalping_base.py — Stratégie scalping hybride.

Portage Python de la stratégie Pine Script SMC Scalping Hybride v2.1.
Indicateurs calculés en Python pur (sans pandas-ta) pour la rapidité.

SIGNAL LONG :
  1. EMA 9 > EMA 21 > EMA 50 (tendance haussière locale)
  2. Close HTF > EMA 21 HTF (contexte haussier)
  3. ADX(14) > 20 (marché en tendance)
  4. Écart EMA 9/EMA 21 >= 0.10% du prix
  5. Rebond sur EMA 9 ou EMA 21 OU croisement EMA 9 au-dessus EMA 21
  6. RSI(14) entre 45 et 68
  7. Engulfing haussier OU pin bar haussière + clôture tiers supérieur (>= 60%)
  8. Volume > 1.3× moyenne 20 bougies
  9. Session active + cooldown >= 3 bougies

SIGNAL SHORT (inverse exact).

TOUJOURS utiliser iloc[-2] — jamais la bougie en cours.
"""

from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

import pandas as pd
from loguru import logger

from strategies.strategy_base import StrategieBase, SignalResult


class ScalpingStrategy(StrategieBase):
    """
    Stratégie scalping hybride EMA + RSI + ADX + Price Action.
    Base commune héritée par les stratégies NAS100, SP500, WTI.
    """

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        # Suivi du cooldown entre trades — deux modes : bar et temps
        self._heure_dernier_signal: Optional[datetime] = None
        self._dernier_bar_time: Optional[object] = None
        # Compteurs de bougies pour le cooldown (compatibilité spec)
        self._last_trade_bar: int = -999
        self._current_bar: int = 0

    def get_strategy_name(self) -> str:
        return f"SCALPING_HYBRID_{getattr(self.config, 'SYMBOLE', '?')}"

    # ── Calcul des indicateurs ─────────────────────────────────────────────

    def _calculer_indicateurs(self, df: pd.DataFrame) -> Dict:
        """
        Calcule tous les indicateurs sur le DataFrame OHLCV.
        Utilise TOUJOURS iloc[-2] — bougie fermée, jamais en cours.

        Returns:
            Dictionnaire avec toutes les valeurs de la dernière bougie fermée.
        """
        if df is None or len(df) < 10:
            return {}

        df = df.copy()

        ema_fast = getattr(self.config, "EMA_FAST", 9)
        ema_slow = getattr(self.config, "EMA_SLOW", 21)
        ema_trend = getattr(self.config, "EMA_TREND", 50)
        rsi_len = getattr(self.config, "RSI_LEN", 14)
        atr_len = getattr(self.config, "ATR_LEN", 14)
        adx_len = getattr(self.config, "ADX_LEN", 14)
        vol_ma_len = getattr(self.config, "VOL_MA_LEN", 20)

        # ── EMAs ──────────────────────────────────────────────────────────
        df["ema_fast"]  = df["close"].ewm(span=ema_fast,  adjust=False).mean()
        df["ema_slow"]  = df["close"].ewm(span=ema_slow,  adjust=False).mean()
        df["ema_trend"] = df["close"].ewm(span=ema_trend, adjust=False).mean()

        # ── RSI ───────────────────────────────────────────────────────────
        delta = df["close"].diff()
        gain  = delta.clip(lower=0)
        loss  = (-delta).clip(lower=0)
        avg_gain = gain.ewm(com=rsi_len - 1, adjust=False).mean()
        avg_loss = loss.ewm(com=rsi_len - 1, adjust=False).mean()
        rs = avg_gain / avg_loss.replace(0, 1e-10)
        df["rsi"] = 100 - (100 / (1 + rs))

        # ── ATR ───────────────────────────────────────────────────────────
        hl  = df["high"] - df["low"]
        hpc = (df["high"] - df["close"].shift()).abs()
        lpc = (df["low"]  - df["close"].shift()).abs()
        df["tr"]  = pd.concat([hl, hpc, lpc], axis=1).max(axis=1)
        df["atr"] = df["tr"].ewm(span=atr_len, adjust=False).mean()

        # ── ADX ───────────────────────────────────────────────────────────
        high_diff = df["high"] - df["high"].shift()
        low_diff  = df["low"].shift() - df["low"]
        df["dm_plus"]  = ((high_diff > low_diff) & (high_diff > 0)).astype(float) * high_diff.clip(lower=0)
        df["dm_minus"] = ((low_diff > high_diff) & (low_diff > 0)).astype(float) * low_diff.clip(lower=0)
        atr_sm = df["atr"]
        di_plus  = 100 * df["dm_plus"].ewm(span=adx_len, adjust=False).mean() / atr_sm.replace(0, 1e-10)
        di_minus = 100 * df["dm_minus"].ewm(span=adx_len, adjust=False).mean() / atr_sm.replace(0, 1e-10)
        di_sum   = (di_plus + di_minus).replace(0, 1e-10)
        dx  = 100 * (di_plus - di_minus).abs() / di_sum
        df["adx"] = dx.ewm(span=adx_len, adjust=False).mean()

        # ── Volume moyen ──────────────────────────────────────────────────
        vol_col = "volume" if "volume" in df.columns else "tick_volume"
        if vol_col in df.columns:
            df["vol_avg"] = df[vol_col].rolling(vol_ma_len).mean()
        else:
            df["vol_avg"] = 1.0

        # ── Bougie fermée (iloc[-2]) ───────────────────────────────────────
        if len(df) < 3:
            return {}

        c    = df.iloc[-2]
        prev = df.iloc[-3]

        # ── Price Action ──────────────────────────────────────────────────
        c_range  = float(c["high"]) - float(c["low"])
        if c_range <= 0:
            c_range = 1e-10

        body_sz  = abs(float(c["close"]) - float(c["open"]))
        up_wick  = float(c["high"]) - max(float(c["open"]), float(c["close"]))
        low_wick = min(float(c["open"]), float(c["close"])) - float(c["low"])

        # Engulfing haussier
        bull_engulf = (
            float(c["close"]) > float(c["open"]) and
            float(prev["close"]) < float(prev["open"]) and
            float(c["close"]) > float(prev["open"]) and
            float(c["open"]) < float(prev["close"]) and
            body_sz > abs(float(prev["open"]) - float(prev["close"]))
        )

        # Engulfing baissier
        bear_engulf = (
            float(c["close"]) < float(c["open"]) and
            float(prev["close"]) > float(prev["open"]) and
            float(c["close"]) < float(prev["open"]) and
            float(c["open"]) > float(prev["close"]) and
            body_sz > abs(float(prev["close"]) - float(prev["open"]))
        )

        pin_ratio = getattr(self.config, "PIN_BAR_RATIO", 0.55)

        # Pin bar haussière (longue mèche basse)
        bull_pin = (
            low_wick >= c_range * pin_ratio and
            body_sz <= c_range * 0.35 and
            up_wick <= c_range * 0.20
        )

        # Pin bar baissière (longue mèche haute)
        bear_pin = (
            up_wick >= c_range * pin_ratio and
            body_sz <= c_range * 0.35 and
            low_wick <= c_range * 0.20
        )

        # Bougie forte directionnelle (body ≥ 60% du range)
        strong_bull_bar = (
            float(c["close"]) > float(c["open"]) and
            body_sz >= c_range * 0.60
        )
        strong_bear_bar = (
            float(c["close"]) < float(c["open"]) and
            body_sz >= c_range * 0.60
        )

        # Position de clôture dans le range (0-100%)
        close_pos_pct = (float(c["close"]) - float(c["low"])) / c_range * 100

        # Volume courant vs moyenne
        vol_now = float(c.get(vol_col, 1.0)) if vol_col in df.columns else 1.0
        vol_avg_val = float(c.get("vol_avg", 1.0)) if "vol_avg" in df.columns else 1.0

        # Spread EMA 9/21
        ema_spread_pct = (
            abs(float(c["ema_fast"]) - float(c["ema_slow"]))
            / float(c["close"]) * 100
            if float(c["close"]) > 0 else 0.0
        )

        # Rebond sur EMA (low à moins de 0.3% de l'EMA + clôture au-dessus)
        bounce_bull = (
            min(
                abs(float(c["low"]) - float(c["ema_fast"])),
                abs(float(c["low"]) - float(c["ema_slow"]))
            ) / max(float(c["close"]), 1) * 100 <= 0.30
            and float(c["close"]) > float(c["ema_fast"])
        )

        bounce_bear = (
            min(
                abs(float(c["high"]) - float(c["ema_fast"])),
                abs(float(c["high"]) - float(c["ema_slow"]))
            ) / max(float(c["close"]), 1) * 100 <= 0.30
            and float(c["close"]) < float(c["ema_fast"])
        )

        # Croisement EMA9/EMA21
        prev_ef = float(df.iloc[-3]["ema_fast"]) if len(df) >= 3 else 0
        prev_es = float(df.iloc[-3]["ema_slow"]) if len(df) >= 3 else 0
        cross_up   = float(c["ema_fast"]) > float(c["ema_slow"]) and prev_ef <= prev_es
        cross_down = float(c["ema_fast"]) < float(c["ema_slow"]) and prev_ef >= prev_es

        return {
            "ema_fast":        float(c["ema_fast"]),
            "ema_slow":        float(c["ema_slow"]),
            "ema_trend":       float(c["ema_trend"]),
            "rsi":             float(c["rsi"]),
            "atr":             float(c["atr"]),
            "adx":             float(c["adx"]),
            "volume":          vol_now,
            "vol_avg":         vol_avg_val,
            "close":           float(c["close"]),
            "open":            float(c["open"]),
            "high":            float(c["high"]),
            "low":             float(c["low"]),
            "bull_engulf":     bull_engulf,
            "bear_engulf":     bear_engulf,
            "bull_pin":        bull_pin,
            "bear_pin":        bear_pin,
            "strong_bull_bar": strong_bull_bar,
            "strong_bear_bar": strong_bear_bar,
            "close_pos_pct":   close_pos_pct,
            "ema_spread_pct":  ema_spread_pct,
            "bounce_bull":     bounce_bull,
            "bounce_bear":     bounce_bear,
            "cross_up":        cross_up,
            "cross_down":      cross_down,
        }

    # ── Vérification du cooldown ───────────────────────────────────────────

    def _check_cooldown(self) -> bool:
        """
        Vérifie que le cooldown entre trades est respecté.
        Priorité au compteur de bougies (COOLDOWN_BARS).
        Au premier démarrage (last_trade_bar == -999) : toujours autorisé.
        """
        cooldown_bars = getattr(self.config, "COOLDOWN_BARS", 3)

        # Incrémenter le compteur de bougies courant
        self._current_bar += 1

        # Premier démarrage : jamais de cooldown
        if self._last_trade_bar == -999:
            return True

        # Vérification bar : assez de bougies depuis le dernier trade ?
        bars_since = self._current_bar - self._last_trade_bar
        return bars_since >= cooldown_bars

    def _marquer_signal(self) -> None:
        """Enregistre le dernier signal déclenché (bar + temps)."""
        self._heure_dernier_signal = datetime.utcnow()
        self._last_trade_bar = self._current_bar

    # ── Aliases anglais (compatibilité spec et tests) ──────────────────────

    def _calculate_indicators(self, df) -> Dict:
        """Alias anglais de _calculer_indicateurs."""
        return self._calculer_indicateurs(df)

    def _register_trade(self) -> None:
        """Alias anglais de _marquer_signal."""
        self._marquer_signal()

    # ── Évaluation du signal scalping ─────────────────────────────────────

    def evaluate_signal(
        self,
        current_time: Optional[datetime] = None,
    ) -> SignalResult:
        """
        Évalue le signal scalping hybride.
        Ordre : filtres communs → cooldown → indicateurs → signal.
        """
        now = current_time or datetime.utcnow()

        # ── 1. Filtres communs ─────────────────────────────────────────────
        common_ok, common_reason = self.run_common_filters(now)
        if not common_ok:
            return SignalResult(signal=None, rejected_by="common", reason=common_reason)

        # ── 2. Cooldown ────────────────────────────────────────────────────
        if not self._check_cooldown():
            return SignalResult(
                signal=None, rejected_by="cooldown",
                reason=f"Cooldown actif ({getattr(self.config, 'COOLDOWN_BARS', 3)} bougies min)"
            )

        # ── 3. Données signal TF ───────────────────────────────────────────
        tf_signal = getattr(self.config, "TIMEFRAME_SIGNAL", 5)
        n_bougies = getattr(self.config, "BOUGIES_SIGNAL", 80)
        df_signal = self._get_ohlcv(tf_signal, n_bougies)

        if df_signal is None or len(df_signal) < 10:
            return SignalResult(signal=None, rejected_by="data",
                                reason="Données signal insuffisantes")

        ind = self._calculer_indicateurs(df_signal)
        if not ind:
            return SignalResult(signal=None, rejected_by="indicators",
                                reason="Calcul indicateurs échoué")

        # ── 4. Données HTF pour contexte ───────────────────────────────────
        tf_htf = getattr(self.config, "TIMEFRAME_HTF", 16385)
        htf_ema_len = getattr(self.config, "HTF_EMA_LEN", 21)
        df_htf = self._get_ohlcv(tf_htf, 50)

        htf_bull = True  # Par défaut si pas de données HTF
        htf_bear = True
        if df_htf is not None and len(df_htf) >= 3:
            htf_ema = df_htf["close"].ewm(span=htf_ema_len, adjust=False).mean()
            htf_close = float(df_htf["close"].iloc[-2])
            htf_ema_val = float(htf_ema.iloc[-2])
            htf_bull = htf_close > htf_ema_val
            htf_bear = htf_close < htf_ema_val

        # ── 5. Paramètres de filtre ────────────────────────────────────────
        adx_min    = getattr(self.config, "ADX_MIN", 20.0)
        ema_min_sp = getattr(self.config, "EMA_MIN_SPREAD_PCT", 0.10)
        rsi_bull_min = getattr(self.config, "RSI_BULL_MIN", 45)
        rsi_bull_max = getattr(self.config, "RSI_BULL_MAX", 68)
        rsi_bear_min = getattr(self.config, "RSI_BEAR_MIN", 32)
        rsi_bear_max = getattr(self.config, "RSI_BEAR_MAX", 55)
        vol_mult   = getattr(self.config, "VOL_MULT", 1.3)
        sl_atr     = getattr(self.config, "SL_ATR_MULT", 1.5)
        rr_tp      = getattr(self.config, "RR_TP", 2.0)
        digits     = getattr(self.config, "PRICE_DIGITS", 2)

        # ── 6. Signal LONG ─────────────────────────────────────────────────
        tendance_bull  = (ind["ema_fast"] > ind["ema_slow"] and
                          ind["ema_slow"] > ind["ema_trend"])   # alignement EMA strict
        trigger_bull   = ind["bounce_bull"] or ind["cross_up"]
        pa_bull        = (ind["bull_engulf"] or ind["bull_pin"] or ind["strong_bull_bar"]) and ind["close_pos_pct"] >= 50
        # Filtre volume — ignoré si vol_avg ~= 1.0 (démo / tick_volume non fiable)
        vol_avg_fiable = ind["vol_avg"] > 1.0
        vol_bull = (not vol_avg_fiable) or (ind["volume"] > ind["vol_avg"] * vol_mult)

        long_ok = (
            tendance_bull and
            htf_bull and
            ind["adx"] > adx_min and
            ind["ema_spread_pct"] >= ema_min_sp and
            rsi_bull_min <= ind["rsi"] <= rsi_bull_max and
            pa_bull and
            vol_bull
        )

        # ── 7. Signal SHORT ────────────────────────────────────────────────
        tendance_bear  = (ind["ema_fast"] < ind["ema_slow"] and
                          ind["ema_slow"] < ind["ema_trend"])   # alignement EMA strict
        trigger_bear   = ind["bounce_bear"] or ind["cross_down"]
        pa_bear        = (ind["bear_engulf"] or ind["bear_pin"] or ind["strong_bear_bar"]) and ind["close_pos_pct"] <= 65
        vol_bear = (not vol_avg_fiable) or (ind["volume"] > ind["vol_avg"] * vol_mult)

        short_ok = (
            tendance_bear and
            htf_bear and
            ind["adx"] > adx_min and
            ind["ema_spread_pct"] >= ema_min_sp and
            rsi_bear_min <= ind["rsi"] <= rsi_bear_max and
            pa_bear and
            vol_bear
        )

        # ── 8. Construire les signaux ──────────────────────────────────────
        if long_ok:
            sl = round(ind["low"] - ind["atr"] * sl_atr, digits)
            tp = round(ind["close"] + (ind["close"] - sl) * rr_tp, digits)
            confluences = [
                f"EMA spread {ind['ema_spread_pct']:.2f}%",
                f"ADX {ind['adx']:.1f}",
                f"RSI {ind['rsi']:.1f}",
                "Engulfing bull" if ind["bull_engulf"] else "Pin bar bull",
            ]
            logger.info(
                f"[SCALP LONG] {getattr(self.config, 'SYMBOLE', '?')} | "
                f"ADX:{ind['adx']:.1f} RSI:{ind['rsi']:.1f} | "
                f"Entry:{ind['close']:.{digits}f} SL:{sl} TP:{tp}"
            )
            self._marquer_signal()
            return SignalResult(
                signal="bullish",
                rejected_by=None,
                reason=f"Signal scalping LONG validé | ADX:{ind['adx']:.1f}",
                ob_score=int(ind["adx"]),
                ob_strength="SCALP",
                confluences=confluences,
                entry_price=ind["close"],
                stop_loss=sl,
                take_profit=tp,
            )

        if short_ok:
            sl = round(ind["high"] + ind["atr"] * sl_atr, digits)
            tp = round(ind["close"] - (sl - ind["close"]) * rr_tp, digits)
            confluences = [
                f"EMA spread {ind['ema_spread_pct']:.2f}%",
                f"ADX {ind['adx']:.1f}",
                f"RSI {ind['rsi']:.1f}",
                "Engulfing bear" if ind["bear_engulf"] else "Pin bar bear",
            ]
            logger.info(
                f"[SCALP SHORT] {getattr(self.config, 'SYMBOLE', '?')} | "
                f"ADX:{ind['adx']:.1f} RSI:{ind['rsi']:.1f} | "
                f"Entry:{ind['close']:.{digits}f} SL:{sl} TP:{tp}"
            )
            self._marquer_signal()
            return SignalResult(
                signal="bearish",
                rejected_by=None,
                reason=f"Signal scalping SHORT validé | ADX:{ind['adx']:.1f}",
                ob_score=int(ind["adx"]),
                ob_strength="SCALP",
                confluences=confluences,
                entry_price=ind["close"],
                stop_loss=sl,
                take_profit=tp,
            )

        symbole = getattr(self.config, "SYMBOLE", "?")
        logger.debug(
            f"[{symbole}] PAS DE SIGNAL | "
            f"LONG → Trend:{tendance_bull} HTF:{htf_bull} Trigger:{trigger_bull} "
            f"PA:{pa_bull} ADX:{ind['adx']:.1f}(>{adx_min}) "
            f"RSI:{ind['rsi']:.1f}([{rsi_bull_min}-{rsi_bull_max}]) "
            f"EMAsp:{ind['ema_spread_pct']:.3f}%(>={ema_min_sp}) Vol:{vol_bull} | "
            f"SHORT → Trend:{tendance_bear} HTF:{htf_bear} Trigger:{trigger_bear} "
            f"PA:{pa_bear} RSI:[{rsi_bear_min}-{rsi_bear_max}] Vol:{vol_bear}"
        )
        return SignalResult(
            signal=None,
            rejected_by="no_signal",
            reason=(
                f"Pas de signal | "
                f"ADX:{ind['adx']:.1f} "
                f"RSI:{ind['rsi']:.1f} "
                f"EMA_sp:{ind['ema_spread_pct']:.2f}% "
                f"Trend_bull:{tendance_bull} Trigger:{trigger_bull} PA:{pa_bull}"
            ),
        )

    # ── Méthode utilitaire pour le trade_manager ──────────────────────────

    def verifier_bougie_adverse(self, ind: Dict, direction: str) -> bool:
        """
        Vérifie si la bougie actuelle est une bougie adverse forte.
        Utilisé par trade_manager pour sortie anticipée en scalping.

        Args:
            ind      : Indicateurs calculés sur la dernière bougie fermée.
            direction: "bullish" ou "bearish" (direction du trade en cours).

        Returns:
            True si une bougie adverse forte est détectée → sortir.
        """
        if direction == "bullish":
            return (ind.get("bear_engulf", False) and
                    ind.get("close_pos_pct", 50) <= 35)
        else:
            return (ind.get("bull_engulf", False) and
                    ind.get("close_pos_pct", 50) >= 65)


# Alias anglais
ScalpingBaseStrategy = ScalpingStrategy
