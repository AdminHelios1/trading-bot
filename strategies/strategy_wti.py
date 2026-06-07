"""
strategies/strategy_wti.py — Stratégie WTI : SMC + Liquidity Sweep.

Le pétrole WTI est fréquemment manipulé par des stop hunts institutionnels
avant les gros mouvements directionnels. Le Liquidity Sweep est le signal
primaire : une longue mèche qui dépasse un niveau clé (stop hunt) suivie
d'une clôture de retour = confirmation institutionnelle.

Spécificités WTI :
- Session asiatique autorisée (02h–06h UTC) avec paramètres renforcés
- Risque 0.5% en session asiatique (vs 1% standard)
- OB score minimum 75 la nuit (vs 60 standard)
- Spread max 30 pts la nuit (vs 20 pts)
- Liquidity Sweep obligatoire comme signal primaire
"""

from datetime import datetime
from typing import Dict, Optional, Tuple

from loguru import logger

from strategies.strategy_base import StrategieBase, SignalResult


class StrategieWTI(StrategieBase):
    """
    Stratégie WTI : SMC Order Block + Liquidity Sweep.

    Le pétrole est un actif très manipulé — les stop hunts précèdent
    systématiquement les gros mouvements. La stratégie :
    1. Détecter un Liquidity Sweep (mèche longue + clôture de retour)
    2. Confirmer avec un OB dans la direction du sweep
    3. Entrer sur le retest de la zone OB
    """

    def get_strategy_name(self) -> str:
        return "SMC_LIQUIDITY_SWEEP_WTI"

    def evaluate_signal(
        self,
        current_time: Optional[datetime] = None,
    ) -> SignalResult:
        """
        Évalue le signal WTI.
        Ordre : filtres communs → session asiatique → sweep → OB → portfolio.
        """
        now = current_time or datetime.utcnow()

        # ── 1. Filtres communs ─────────────────────────────────────────────
        common_ok, common_reason = self.run_common_filters(now)
        if not common_ok:
            return SignalResult(
                signal=None,
                rejected_by="common_filters",
                reason=common_reason,
            )

        # ── 2. Adapter les paramètres selon la session ─────────────────────
        est_session_asiatique = self._est_session_asiatique(now)
        risque_effectif = self._get_risque_effectif(est_session_asiatique)
        ob_score_min = (
            getattr(self.config, "ASIAN_OB_SCORE_MIN", 75)
            if est_session_asiatique
            else getattr(self.config, "OB_SCORE_MIN", 60)
        )

        if est_session_asiatique:
            logger.debug(
                f"WTI session asiatique — risque: {risque_effectif}%, "
                f"OB min: {ob_score_min}"
            )

        # ── 3. Détecter un Liquidity Sweep récent ─────────────────────────
        sweep = self._detecter_liquidity_sweep()
        if not sweep:
            return SignalResult(
                signal=None,
                rejected_by="sweep_detector",
                reason="Aucun Liquidity Sweep récent sur WTI H1",
            )

        direction_sweep = sweep["type"]  # "bullish_sweep" ou "bearish_sweep"

        # ── 4. OB dans la direction du sweep ──────────────────────────────
        if self.ob_detector is None:
            return SignalResult(
                signal=None,
                rejected_by="ob_detector",
                reason="OB detector non initialisé pour WTI",
            )

        try:
            df_h4 = self._get_ohlcv(getattr(self.config, "TIMEFRAME_HTF", 16388), 200)
            df_m15 = self._get_ohlcv(getattr(self.config, "TIMEFRAME_LTF", 15), 150)

            if df_h4 is None or df_m15 is None:
                return SignalResult(
                    signal=None,
                    rejected_by="data",
                    reason="Données OHLCV WTI non disponibles",
                )

            obs_actifs = self.ob_detector.detecter_multi_tf(df_h4, df_m15=df_m15)

            # Filtrer par direction du sweep ET score minimum
            try:
                from ob_detector import TypeOB
                ob_type_cible = (
                    TypeOB.HAUSSIER if direction_sweep == "bullish_sweep"
                    else TypeOB.BAISSIER
                )
                obs_filtres = [
                    ob for ob in obs_actifs
                    if ob.type_ob == ob_type_cible
                    and ob.score >= ob_score_min
                ]
            except Exception:
                obs_filtres = [
                    ob for ob in obs_actifs
                    if ob.score >= ob_score_min
                ]

            if not obs_filtres:
                return SignalResult(
                    signal=None,
                    rejected_by="ob_after_sweep",
                    reason=(
                        f"Sweep WTI détecté ({direction_sweep}) mais "
                        f"aucun OB valide (score min {ob_score_min})"
                    ),
                )

            meilleur_ob = obs_filtres[0]

        except Exception as e:
            return SignalResult(
                signal=None,
                rejected_by="ob_detector",
                reason=f"Erreur détection OB WTI : {e}",
            )

        # ── 5. Direction finale ────────────────────────────────────────────
        direction = (
            "bullish" if direction_sweep == "bullish_sweep" else "bearish"
        )

        # ── 6. Vérification portfolio ──────────────────────────────────────
        if self.portfolio_rm is not None:
            try:
                symbole = getattr(self.config, "SYMBOLE", "XTIUSD")
                portfolio_ok, portfolio_raison = self.portfolio_rm.can_open_trade(
                    symbole, risque_effectif, ob_score=meilleur_ob.score
                )
                if not portfolio_ok:
                    return SignalResult(
                        signal=None,
                        rejected_by="portfolio",
                        reason=portfolio_raison,
                    )
            except Exception as e:
                logger.debug(f"Portfolio check WTI erreur : {e}")

        # ── Signal validé ──────────────────────────────────────────────────
        prix = self._get_prix_actuel()
        session_str = "Asiatique" if est_session_asiatique else "London/NY"
        return SignalResult(
            signal=direction,
            rejected_by=None,
            reason=(
                f"Signal WTI validé | Sweep: {direction_sweep} | "
                f"OB: {meilleur_ob.score}/100 | Session: {session_str}"
            ),
            ob_score=meilleur_ob.score,
            ob_strength=str(getattr(meilleur_ob, "force", "")),
            entry_price=prix,
            risk_multiplier=risque_effectif,
            ob=meilleur_ob,
        )

    # ── Détection Liquidity Sweep ──────────────────────────────────────────

    def _detecter_liquidity_sweep(self) -> Optional[Dict]:
        """
        Détecte un Liquidity Sweep récent sur H1 WTI.

        Bullish Sweep : longue mèche basse + clôture au-dessus
        → stop hunt sous un swing low → signal d'achat

        Bearish Sweep : longue mèche haute + clôture en-dessous
        → stop hunt au-dessus d'un swing high → signal de vente
        """
        if self.connecteur is None:
            return None

        try:
            from indicators import Indicateurs

            df = self._get_ohlcv(
                getattr(self.config, "TIMEFRAME_MTF", 16385), 30
            )
            if df is None or len(df) < 5:
                return None

            df = df.iloc[:-1]  # Bougies fermées uniquement

            # Calculer ATR
            atr_serie = Indicateurs.atr(df, getattr(self.config, "ATR_PERIODE", 14))
            if len(atr_serie) < 2:
                return None

            min_wick_atr = getattr(self.config, "SWEEP_MIN_WICK_ATR_RATIO", 0.8)
            max_body_ratio = getattr(self.config, "SWEEP_MAX_BODY_RATIO", 0.40)
            lookback = getattr(self.config, "SWEEP_LOOKBACK_CANDLES", 30)

            # Analyser les 4 dernières bougies fermées
            for i in range(max(1, len(df) - 4), len(df)):
                candle = df.iloc[i]
                atr = float(atr_serie.iloc[i]) if i < len(atr_serie) else 0.0
                if atr <= 0:
                    continue

                high = float(candle["high"])
                low = float(candle["low"])
                ouvert = float(candle["open"])
                ferme = float(candle["close"])
                range_total = high - low

                if range_total <= 0:
                    continue

                corps = abs(ferme - ouvert)
                ratio_corps = corps / range_total

                # ── Bullish Sweep : mèche basse + clôture haussière ──────
                meche_basse = ouvert - low
                if (
                    meche_basse >= atr * min_wick_atr
                    and meche_basse / range_total > 0.50
                    and ratio_corps <= max_body_ratio
                    and ferme > ouvert
                ):
                    debut = max(0, i - lookback)
                    swing_low = float(df["low"].iloc[debut:i].min())
                    if low < swing_low:
                        return {
                            "type": "bullish_sweep",
                            "sweep_low": low,
                            "niveau_sweepe": swing_low,
                            "prix_rentree": ferme,
                            "wick_atr_ratio": round(meche_basse / atr, 2),
                        }

                # ── Bearish Sweep : mèche haute + clôture baissière ──────
                meche_haute = high - ouvert
                if (
                    meche_haute >= atr * min_wick_atr
                    and meche_haute / range_total > 0.50
                    and ratio_corps <= max_body_ratio
                    and ferme < ouvert
                ):
                    debut = max(0, i - lookback)
                    swing_high = float(df["high"].iloc[debut:i].max())
                    if high > swing_high:
                        return {
                            "type": "bearish_sweep",
                            "sweep_high": high,
                            "niveau_sweepe": swing_high,
                            "prix_rentree": ferme,
                            "wick_atr_ratio": round(meche_haute / atr, 2),
                        }

        except Exception as e:
            logger.debug(f"Détection sweep WTI erreur : {e}")

        return None

    # ── Utilitaires session ────────────────────────────────────────────────

    def _est_session_asiatique(self, now: datetime) -> bool:
        """True si on est dans la session asiatique WTI (02h–06h UTC)."""
        debut = getattr(self.config, "ASIAN_SESSION_DEBUT", 2)
        fin = getattr(self.config, "ASIAN_SESSION_FIN", 6)
        return debut <= now.hour < fin

    def _get_risque_effectif(self, est_session_asiatique: bool) -> float:
        """Retourne le risque effectif selon la session."""
        risque_base = getattr(
            self.config, "RISQUE_PAR_TRADE_PCT",
            getattr(self.config, "RISK_PER_TRADE_PCT", 1.0)
        )
        if est_session_asiatique:
            mult_asiatique = getattr(self.config, "ASIAN_RISK_MULTIPLIER", 0.5)
            return risque_base * mult_asiatique
        return risque_base * self._dernier_multiplicateur_risk


# Alias anglais
WTIStrategy = StrategieWTI
