"""
strategies/strategy_sp500.py — Stratégie SP500 : SMC + FVG prioritaire.

Le S&P 500 comble systématiquement ses Fair Value Gaps avant de repartir.
Le FVG est utilisé comme trigger précis à l'intérieur d'une zone OB.
Sans confluence FVG + OB → pas de trade SP500.

Spécificités SP500 :
- FVG obligatoire dans la zone OB (double confluence)
- FVG minimum 0.15% du prix sur H1
- FVG valide max 20 bougies H1
- Corrélation 0.95 avec NAS100 → jamais ouverts ensemble
"""

from datetime import datetime
from typing import Dict, List, Optional, Tuple

from loguru import logger

from strategies.strategy_base import StrategieBase, SignalResult


class StrategieSP500(StrategieBase):
    """
    Stratégie SP500 : SMC Order Block + Fair Value Gap prioritaire.

    La logique : identifier un OB institutionnel H4, puis attendre qu'un
    FVG se forme dans cette zone sur H1. L'entrée se fait quand le prix
    teste le FVG à l'intérieur de la zone OB — double confluence = haute
    probabilité de rebond institutionnel.
    """

    def get_strategy_name(self) -> str:
        return "SMC_FVG_PRIORITY_SP500"

    def evaluate_signal(
        self,
        current_time: Optional[datetime] = None,
    ) -> SignalResult:
        """
        Évalue le signal SP500.
        Ordre : filtres communs → structure → FVG → OB → confluence FVG+OB → portfolio.
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

        # ── 2. Structure H4 ────────────────────────────────────────────────
        tendance_str = "?"
        try:
            from structure_analyzer import Tendance
            df_h4 = self._get_ohlcv(getattr(self.config, "TIMEFRAME_HTF", 16388), 200)
            if df_h4 is not None and self.structure_analyzer is not None:
                analyse = self.structure_analyzer.analyser(df_h4)
                tendance_str = analyse.tendance.value
                if analyse.tendance in (Tendance.NEUTRE, Tendance.RANGE):
                    return SignalResult(
                        signal=None,
                        rejected_by="structure",
                        reason=f"SP500 tendance {tendance_str} — pas de trade",
                    )
        except Exception as e:
            logger.debug(f"Structure SP500 erreur : {e}")

        # ── 3. Détection des FVG actifs H1 ────────────────────────────────
        fvgs = self._detecter_fvg_actifs()
        if not fvgs:
            return SignalResult(
                signal=None,
                rejected_by="fvg_detector",
                reason="Aucun FVG actif sur SP500 H1",
            )

        # ── 4. Détection OB multi-TF ───────────────────────────────────────
        if self.ob_detector is None:
            return SignalResult(
                signal=None,
                rejected_by="ob_detector",
                reason="OB detector non initialisé pour SP500",
            )

        try:
            df_h4 = self._get_ohlcv(getattr(self.config, "TIMEFRAME_HTF", 16388), 200)
            df_m15 = self._get_ohlcv(getattr(self.config, "TIMEFRAME_LTF", 15), 150)

            if df_h4 is None or df_m15 is None:
                return SignalResult(
                    signal=None,
                    rejected_by="data",
                    reason="Données OHLCV SP500 non disponibles",
                )

            obs_actifs = self.ob_detector.detecter_multi_tf(df_h4, df_m15=df_m15)
            if not obs_actifs:
                return SignalResult(
                    signal=None,
                    rejected_by="ob_detector",
                    reason="Aucun OB SP500 valide",
                )

            meilleur_ob = obs_actifs[0]

        except Exception as e:
            return SignalResult(
                signal=None,
                rejected_by="ob_detector",
                reason=f"Erreur détection OB SP500 : {e}",
            )

        # ── 5. Vérifier que le FVG est dans la zone OB (confluence) ─────────
        fvg_in_ob = self._trouver_fvg_dans_zone_ob(fvgs, meilleur_ob)
        if not fvg_in_ob:
            return SignalResult(
                signal=None,
                rejected_by="fvg_ob_confluence",
                reason="FVG SP500 pas dans la zone OB — confluence insuffisante",
            )

        direction = self._determiner_direction(meilleur_ob)
        if direction is None:
            return SignalResult(
                signal=None,
                rejected_by="ob_direction",
                reason="OB SP500 sans direction claire",
            )

        # ── 6. Vérification portfolio avec score OB réel ───────────────────
        if self.portfolio_rm is not None:
            try:
                risque = getattr(
                    self.config, "RISQUE_PAR_TRADE_PCT",
                    getattr(self.config, "RISK_PER_TRADE_PCT", 1.0)
                )
                symbole = getattr(self.config, "SYMBOLE", "US500")
                portfolio_ok, portfolio_raison = self.portfolio_rm.can_open_trade(
                    symbole, risque, ob_score=meilleur_ob.score
                )
                if not portfolio_ok:
                    return SignalResult(
                        signal=None,
                        rejected_by="portfolio",
                        reason=portfolio_raison,
                    )
            except Exception as e:
                logger.debug(f"Portfolio check SP500 erreur : {e}")

        # ── Signal validé ──────────────────────────────────────────────────
        prix = self._get_prix_actuel()
        return SignalResult(
            signal=direction,
            rejected_by=None,
            reason=(
                f"Signal SP500 validé | OB: {meilleur_ob.score}/100 | "
                f"FVG: {fvg_in_ob['size_pct']:.2f}% dans zone OB"
            ),
            ob_score=meilleur_ob.score,
            ob_strength=str(getattr(meilleur_ob, "force", "")),
            entry_price=prix,
            risk_multiplier=self._dernier_multiplicateur_risk,
            ob=meilleur_ob,
        )

    # ── Détection FVG ─────────────────────────────────────────────────────

    def _detecter_fvg_actifs(self) -> List[Dict]:
        """
        Détecte les Fair Value Gaps actifs sur H1.

        FVG haussier : high[N-1] < low[N+1] — zone d'imbalance vers le bas
        FVG baissier : low[N-1] > high[N+1] — zone d'imbalance vers le haut

        Filtres : taille >= FVG_MIN_SIZE_PCT ET âge <= FVG_MAX_AGE_CANDLES
        """
        if self.connecteur is None:
            return []

        try:
            import pandas as pd
            df = self._get_ohlcv(
                getattr(self.config, "TIMEFRAME_MTF", 16385), 50
            )
            if df is None or len(df) < 3:
                return []

            df = df.iloc[:-1]  # Bougies fermées uniquement
            fvg_min_pct = getattr(self.config, "FVG_MIN_SIZE_PCT", 0.15)
            fvg_max_age = getattr(self.config, "FVG_MAX_AGE_CANDLES", 20)
            fvgs: List[Dict] = []

            for i in range(1, len(df) - 1):
                prev = df.iloc[i - 1]
                curr = df.iloc[i]
                suivant = df.iloc[i + 1]

                prix_ref = float(curr["close"])
                if prix_ref <= 0:
                    continue

                age = len(df) - i - 1
                if age > fvg_max_age:
                    continue

                # ── FVG haussier ──────────────────────────────────────────
                if float(prev["high"]) < float(suivant["low"]):
                    taille = float(suivant["low"]) - float(prev["high"])
                    taille_pct = taille / prix_ref * 100
                    if taille_pct >= fvg_min_pct:
                        fvgs.append({
                            "type": "bullish",
                            "high": float(suivant["low"]),
                            "low": float(prev["high"]),
                            "mid": (float(suivant["low"]) + float(prev["high"])) / 2,
                            "size_pct": round(taille_pct, 3),
                            "age_candles": age,
                        })

                # ── FVG baissier ──────────────────────────────────────────
                if float(prev["low"]) > float(suivant["high"]):
                    taille = float(prev["low"]) - float(suivant["high"])
                    taille_pct = taille / prix_ref * 100
                    if taille_pct >= fvg_min_pct:
                        fvgs.append({
                            "type": "bearish",
                            "high": float(prev["low"]),
                            "low": float(suivant["high"]),
                            "mid": (float(prev["low"]) + float(suivant["high"])) / 2,
                            "size_pct": round(taille_pct, 3),
                            "age_candles": age,
                        })

            # Trier par taille décroissante
            return sorted(fvgs, key=lambda x: x["size_pct"], reverse=True)

        except Exception as e:
            logger.debug(f"Détection FVG SP500 erreur : {e}")
            return []

    def _trouver_fvg_dans_zone_ob(
        self,
        fvgs: List[Dict],
        ob,
    ) -> Optional[Dict]:
        """
        Trouve le premier FVG dont le midpoint est dans la zone OB,
        avec direction cohérente.
        """
        try:
            ob_bas = float(getattr(ob, "zone_entree_bas", 0))
            ob_haut = float(getattr(ob, "zone_entree_haut", 0))

            from ob_detector import TypeOB
            ob_haussier = (ob.type_ob == TypeOB.HAUSSIER)

            for fvg in fvgs:
                mid = fvg["mid"]
                if ob_bas <= mid <= ob_haut:
                    # Direction doit correspondre
                    if ob_haussier and fvg["type"] == "bullish":
                        return fvg
                    if not ob_haussier and fvg["type"] == "bearish":
                        return fvg
        except Exception as e:
            logger.debug(f"FVG dans OB erreur : {e}")
        return None

    def _determiner_direction(self, ob) -> Optional[str]:
        """Détermine la direction depuis l'OB."""
        try:
            from ob_detector import TypeOB
            if ob.type_ob == TypeOB.HAUSSIER:
                return "bullish"
            if ob.type_ob == TypeOB.BAISSIER:
                return "bearish"
        except Exception:
            pass
        return None


# Alias anglais
SP500Strategy = StrategieSP500
