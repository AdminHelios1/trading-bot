"""
strategies/strategy_nas100.py — Stratégie NAS100 : SMC + Killzones ICT.

Les Killzones ICT définissent des fenêtres temporelles précises où le NASDAQ
est le plus prévisible institutionnellement (NY Open 13h30–16h00, NY Lunch 16h–17h).
En dehors de ces fenêtres → aucun trade NAS100.

Spécificités NAS100 :
- Killzone obligatoire (pas de trade hors fenêtre ICT)
- Risque réduit à 0.5% pendant les earnings seasons (jan/avr/juil/oct)
- OB score minimum standard (60) — pas de sur-sélection
"""

from datetime import datetime
from typing import Optional, Tuple

from loguru import logger

from strategies.strategy_base import StrategieBase, SignalResult


class StrategieNAS100(StrategieBase):
    """
    Stratégie NAS100 : SMC Order Block + Killzones ICT.

    Le NASDAQ est le plus prévisible dans les 2h30 qui suivent l'ouverture
    de New York (13h30–16h00 UTC) et dans la fenêtre lunch (16h–17h UTC).
    En dehors de ces Killzones, le marché est trop erratique pour le SMC.
    """

    def get_strategy_name(self) -> str:
        return "SMC_KILLZONES_ICT_NAS100"

    def evaluate_signal(
        self,
        current_time: Optional[datetime] = None,
    ) -> SignalResult:
        """
        Évalue le signal NAS100.
        Ordre : filtres communs → killzone → earnings → structure → OB → portfolio.
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

        # ── 2. Vérification Killzone ICT ───────────────────────────────────
        kz_ok, kz_reason = self._verifier_killzone(now)
        if not kz_ok:
            return SignalResult(
                signal=None,
                rejected_by="killzone",
                reason=kz_reason,
            )

        # ── 3. Période earnings → risque réduit ────────────────────────────
        if self._est_periode_earnings(now):
            logger.info(
                f"NAS100 : période de résultats trimestriels "
                f"— risque réduit à 0.5%"
            )

        # ── 4. Structure H4 ────────────────────────────────────────────────
        if self.structure_analyzer is not None:
            try:
                from structure_analyzer import Tendance
                analyse = self.structure_analyzer.analyser(
                    self._get_ohlcv(
                        getattr(self.config, "TIMEFRAME_HTF", 16388), 200
                    )
                ) if self._get_ohlcv(
                    getattr(self.config, "TIMEFRAME_HTF", 16388), 200
                ) is not None else None

                if analyse and analyse.tendance in (
                    Tendance.NEUTRE, Tendance.RANGE
                ):
                    return SignalResult(
                        signal=None,
                        rejected_by="structure",
                        reason=f"NAS100 tendance {analyse.tendance.value} — pas de trade",
                    )
            except Exception as e:
                logger.debug(f"Structure NAS100 erreur : {e}")

        # ── 5. Détection OB multi-TF ───────────────────────────────────────
        if self.ob_detector is None:
            return SignalResult(
                signal=None,
                rejected_by="ob_detector",
                reason="OB detector non initialisé pour NAS100",
            )

        try:
            df_h4 = self._get_ohlcv(getattr(self.config, "TIMEFRAME_HTF", 16388), 200)
            df_m15 = self._get_ohlcv(getattr(self.config, "TIMEFRAME_LTF", 15), 150)

            if df_h4 is None or df_m15 is None:
                return SignalResult(
                    signal=None,
                    rejected_by="data",
                    reason="Données OHLCV NAS100 non disponibles",
                )

            obs_actifs = self.ob_detector.detecter_multi_tf(df_h4, df_m15=df_m15)
            if not obs_actifs:
                return SignalResult(
                    signal=None,
                    rejected_by="ob_detector",
                    reason="Aucun OB NAS100 valide en Killzone ICT",
                )

            meilleur_ob = obs_actifs[0]
            direction = self._determiner_direction(meilleur_ob)

            if direction is None:
                return SignalResult(
                    signal=None,
                    rejected_by="ob_direction",
                    reason="OB NAS100 sans direction claire",
                )

        except Exception as e:
            return SignalResult(
                signal=None,
                rejected_by="ob_detector",
                reason=f"Erreur détection OB NAS100 : {e}",
            )

        # ── 6. Vérification portfolio avec score OB réel ───────────────────
        if self.portfolio_rm is not None:
            try:
                risk_effectif = self._get_risque_effectif(now)
                symbole = getattr(self.config, "SYMBOLE", "NAS100")
                portfolio_ok, portfolio_raison = self.portfolio_rm.can_open_trade(
                    symbole, risk_effectif, ob_score=meilleur_ob.score
                )
                if not portfolio_ok:
                    return SignalResult(
                        signal=None,
                        rejected_by="portfolio",
                        reason=portfolio_raison,
                    )
            except Exception as e:
                logger.debug(f"Portfolio check NAS100 erreur : {e}")

        # ── Signal validé ──────────────────────────────────────────────────
        prix = self._get_prix_actuel()
        return SignalResult(
            signal=direction,
            rejected_by=None,
            reason=f"Signal NAS100 validé en Killzone | OB: {meilleur_ob.score}/100",
            ob_score=meilleur_ob.score,
            ob_strength=getattr(meilleur_ob, "force", "FORT").value
                        if hasattr(getattr(meilleur_ob, "force", ""), "value")
                        else str(getattr(meilleur_ob, "force", "")),
            entry_price=prix,
            risk_multiplier=self._get_risque_effectif(now),
            ob=meilleur_ob,
        )

    # ── Méthodes spécifiques NAS100 ────────────────────────────────────────

    def _verifier_killzone(self, now: datetime) -> Tuple[bool, str]:
        """
        Vérifie si on est dans une Killzone ICT active.
        En dehors des Killzones → signal refusé.
        """
        killzones = getattr(self.config, "KILLZONES", [])

        for kz in killzones:
            debut_min = now.hour * 60 + now.minute
            kz_debut = kz["open"] * 60 + kz.get("minute_open", 0)
            kz_fin = kz["close"] * 60 + kz.get("minute_close", 0)

            if kz_debut <= debut_min <= kz_fin:
                return True, f"Killzone ICT active : {kz['name']}"

        return (
            False,
            f"Hors Killzone NAS100 ({now.strftime('%H:%M')} UTC) | "
            f"Prochaine : NY Open 13h30–16h00 UTC",
        )

    def _est_periode_earnings(self, now: datetime) -> bool:
        """Détecte si on est en période de résultats trimestriels."""
        mois_earnings = getattr(self.config, "EARNINGS_SEASON_MONTHS", [1, 4, 7, 10])
        return now.month in mois_earnings

    def _get_risque_effectif(self, now: datetime) -> float:
        """Retourne le risque effectif — réduit en période de résultats."""
        risque_base = getattr(
            self.config, "RISQUE_PAR_TRADE_PCT",
            getattr(self.config, "RISK_PER_TRADE_PCT", 1.0)
        )
        reduce_earnings = getattr(self.config, "REDUCE_RISK_EARNINGS", True)

        if self._est_periode_earnings(now) and reduce_earnings:
            return risque_base * 0.5

        return risque_base * self._dernier_multiplicateur_risk

    def _determiner_direction(self, ob) -> Optional[str]:
        """Détermine la direction du signal depuis l'OB."""
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
NAS100Strategy = StrategieNAS100
