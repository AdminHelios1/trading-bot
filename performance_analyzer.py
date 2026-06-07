"""
performance_analyzer.py — Calcul des métriques de performance avancées.

Calcule Sharpe, Profit Factor, Max Drawdown, distribution R:R et génère
des recommandations automatiques pour optimiser la stratégie SMC.
"""

import statistics
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional

from loguru import logger

from config import CONFIG


# ── Dataclasses ────────────────────────────────────────────────────────────

@dataclass
class RapportHebdomadaire:
    """Rapport de performance hebdomadaire automatique."""
    semaine_debut: datetime
    semaine_fin: datetime
    total_trades: int
    trades_gagnants: int
    trades_perdants: int
    trades_breakeven: int
    win_rate_pct: float
    pnl_total_usd: float
    r_total: float
    r_moyen_par_trade: float
    profit_factor: float
    sharpe_ratio: float
    max_drawdown_pct: float
    max_pertes_consecutives: int
    meilleur_trade_r: float
    pire_trade_r: float

    # Distribution R:R
    distribution_r: Dict[str, int]

    # Par heure
    meilleure_heure_utc: int
    pire_heure_utc: int
    pnl_par_heure: Dict[int, float]

    # Analyse filtres
    filtre_principal_rejet: str
    nb_rejets_par_filtre: Dict[str, int]

    # OB scores
    score_ob_moyen_gagnants: float
    score_ob_moyen_perdants: float
    meilleure_force_ob: str

    # Recommandations automatiques (max 5)
    recommandations: List[str]


# Alias anglais
WeeklyReport = RapportHebdomadaire


# ── Analyseur de performance ───────────────────────────────────────────────

class AnalyseurPerformance:
    """
    Calcule les métriques de performance sur l'historique des trades du journal.
    Génère des recommandations actionnables pour optimiser la stratégie SMC.
    """

    def __init__(self, config=None) -> None:
        self.config = config or CONFIG

    # ── Rapport hebdomadaire ───────────────────────────────────────────────

    def calculer_rapport_hebdomadaire(
        self,
        entrees: List,          # List[TradeJournalEntry]
        signaux_rejetes: List,  # List[SignalRejete]
        semaine_debut: datetime,
        semaine_fin: datetime,
    ) -> RapportHebdomadaire:
        """
        Calcule toutes les métriques pour le rapport hebdomadaire.

        Args:
            entrees         : Liste des entrées de journal de la semaine.
            signaux_rejetes : Signaux refusés par les filtres.
            semaine_debut   : Début de la semaine (lundi 00h00 UTC).
            semaine_fin     : Fin de la semaine (dimanche 23h59 UTC).

        Returns:
            RapportHebdomadaire complet avec métriques et recommandations.

        Raises:
            ValueError: Si aucun trade à analyser.
        """
        if not entrees:
            raise ValueError("Aucun trade à analyser pour cette semaine")

        # ── Métriques de base ──────────────────────────────────────────────
        gagnants  = [e for e in entrees if e.pnl_total_usd > 0]
        perdants  = [e for e in entrees if e.pnl_total_usd < 0]
        breakeven = [e for e in entrees if abs(e.pnl_total_usd) < 0.50]

        win_rate = len(gagnants) / len(entrees) * 100
        pnl_total = sum(e.pnl_total_usd for e in entrees)
        r_total = sum(e.total_r_realise for e in entrees)
        r_moyen = r_total / len(entrees)

        # ── Profit Factor ──────────────────────────────────────────────────
        profit_brut = sum(e.pnl_total_usd for e in gagnants)
        perte_brute = abs(sum(e.pnl_total_usd for e in perdants))
        profit_factor = (
            profit_brut / perte_brute if perte_brute > 0 else float("inf")
        )

        # ── Sharpe Ratio ──────────────────────────────────────────────────
        sharpe = self._calculer_sharpe(entrees)

        # ── Drawdown max ──────────────────────────────────────────────────
        max_dd = self._calculer_max_drawdown(entrees)

        # ── Pertes consécutives max ────────────────────────────────────────
        max_consec = self._calculer_max_pertes_consecutives(entrees)

        # ── Distribution R:R ──────────────────────────────────────────────
        distribution_r = self._calculer_distribution_r(entrees)

        # ── Analyse par heure ──────────────────────────────────────────────
        pnl_par_heure = self._calculer_pnl_par_heure(entrees)
        meilleure_heure = max(pnl_par_heure, key=pnl_par_heure.get) if pnl_par_heure else 9
        pire_heure = min(pnl_par_heure, key=pnl_par_heure.get) if pnl_par_heure else 0

        # ── Analyse des rejets ─────────────────────────────────────────────
        nb_rejets: Dict[str, int] = defaultdict(int)
        for signal in signaux_rejetes:
            filtre = getattr(signal, "rejete_par", "inconnu")
            nb_rejets[filtre] += 1
        nb_rejets = dict(nb_rejets)
        filtre_principal = (
            max(nb_rejets, key=nb_rejets.get) if nb_rejets else "Aucun"
        )

        # ── OB scores ─────────────────────────────────────────────────────
        score_moyen_gagnants = (
            sum(e.ob_score for e in gagnants) / len(gagnants)
            if gagnants else 0.0
        )
        score_moyen_perdants = (
            sum(e.ob_score for e in perdants) / len(perdants)
            if perdants else 0.0
        )
        meilleure_force_ob = self._trouver_meilleure_force_ob(entrees)

        # ── Recommandations ────────────────────────────────────────────────
        recommandations = self._generer_recommandations(
            win_rate=win_rate,
            profit_factor=profit_factor,
            sharpe=sharpe,
            max_dd=max_dd,
            nb_rejets=nb_rejets,
            score_ob_gagnants=score_moyen_gagnants,
            score_ob_perdants=score_moyen_perdants,
            pnl_par_heure=pnl_par_heure,
            entrees=entrees,
        )

        return RapportHebdomadaire(
            semaine_debut=semaine_debut,
            semaine_fin=semaine_fin,
            total_trades=len(entrees),
            trades_gagnants=len(gagnants),
            trades_perdants=len(perdants),
            trades_breakeven=len(breakeven),
            win_rate_pct=round(win_rate, 1),
            pnl_total_usd=round(pnl_total, 2),
            r_total=round(r_total, 2),
            r_moyen_par_trade=round(r_moyen, 2),
            profit_factor=round(profit_factor, 2),
            sharpe_ratio=round(sharpe, 2),
            max_drawdown_pct=round(max_dd, 2),
            max_pertes_consecutives=max_consec,
            meilleur_trade_r=max(e.total_r_realise for e in entrees),
            pire_trade_r=min(e.total_r_realise for e in entrees),
            distribution_r=distribution_r,
            meilleure_heure_utc=meilleure_heure,
            pire_heure_utc=pire_heure,
            pnl_par_heure=pnl_par_heure,
            filtre_principal_rejet=filtre_principal,
            nb_rejets_par_filtre=nb_rejets,
            score_ob_moyen_gagnants=round(score_moyen_gagnants, 1),
            score_ob_moyen_perdants=round(score_moyen_perdants, 1),
            meilleure_force_ob=meilleure_force_ob,
            recommandations=recommandations,
        )

    # Alias anglais
    def calculate_weekly_report(
        self,
        entries: List,
        rejected_signals: List,
        week_start: datetime,
        week_end: datetime,
    ) -> RapportHebdomadaire:
        return self.calculer_rapport_hebdomadaire(
            entries, rejected_signals, week_start, week_end
        )

    # ── Métriques individuelles ────────────────────────────────────────────

    def _calculer_sharpe(
        self, entrees: List, taux_sans_risque: float = 0.05
    ) -> float:
        """
        Calcule le Sharpe Ratio annualisé sur les R journaliers.
        Groupe les trades par jour et calcule la moyenne/écart-type des R quotidiens.
        """
        r_par_jour: Dict[str, float] = defaultdict(float)
        for e in entrees:
            jour = e.heure_entree.strftime("%Y-%m-%d")
            r_par_jour[jour] += e.total_r_realise

        if len(r_par_jour) < 2:
            return 0.0

        valeurs = list(r_par_jour.values())
        moyenne = sum(valeurs) / len(valeurs)
        try:
            ecart_type = statistics.stdev(valeurs)
        except statistics.StatisticsError:
            return 0.0

        if ecart_type == 0:
            return 0.0

        # Annualiser sur 252 jours de trading
        sharpe = (moyenne - taux_sans_risque / 252) / ecart_type * (252 ** 0.5)
        return sharpe

    def _calculer_max_drawdown(self, entrees: List) -> float:
        """Calcule le Max Drawdown en % sur l'equity curve des R."""
        if not entrees:
            return 0.0

        equity = 0.0
        pic = 0.0
        max_dd = 0.0

        for e in sorted(entrees, key=lambda x: x.heure_entree):
            equity += e.pnl_total_usd
            if equity > pic:
                pic = equity
            dd = (pic - equity) / pic * 100 if pic > 0 else 0.0
            max_dd = max(max_dd, dd)

        return max_dd

    def _calculer_max_pertes_consecutives(self, entrees: List) -> int:
        """Calcule la plus longue série de pertes consécutives."""
        streak_max = 0
        streak_courant = 0
        for e in sorted(entrees, key=lambda x: x.heure_entree):
            if e.pnl_total_usd < 0:
                streak_courant += 1
                streak_max = max(streak_max, streak_courant)
            else:
                streak_courant = 0
        return streak_max

    def _calculer_distribution_r(self, entrees: List) -> Dict[str, int]:
        """Répartit les trades par tranche de R:R réalisé."""
        buckets: Dict[str, int] = {
            "< -1R": 0, "-1R à 0": 0, "0 à 1R": 0,
            "1R à 2R": 0, "2R à 3R": 0, "> 3R": 0,
        }
        for e in entrees:
            r = e.total_r_realise
            if r < -1.0:
                buckets["< -1R"] += 1
            elif r < 0:
                buckets["-1R à 0"] += 1
            elif r < 1.0:
                buckets["0 à 1R"] += 1
            elif r < 2.0:
                buckets["1R à 2R"] += 1
            elif r < 3.0:
                buckets["2R à 3R"] += 1
            else:
                buckets["> 3R"] += 1
        return buckets

    def _calculer_pnl_par_heure(self, entrees: List) -> Dict[int, float]:
        """Calcule le R cumulé par heure d'entrée UTC."""
        par_heure: Dict[int, float] = defaultdict(float)
        for e in entrees:
            par_heure[e.heure_entree.hour] += e.total_r_realise
        return dict(par_heure)

    def _trouver_meilleure_force_ob(self, entrees: List) -> str:
        """Trouve la catégorie de force OB avec le meilleur win rate."""
        wins_par_force: Dict[str, int] = defaultdict(int)
        total_par_force: Dict[str, int] = defaultdict(int)

        for e in entrees:
            f = e.ob_force
            if not f:
                continue
            total_par_force[f] += 1
            if e.pnl_total_usd > 0:
                wins_par_force[f] += 1

        meilleure = ""
        meilleur_wr = 0.0
        for f, total in total_par_force.items():
            wr = wins_par_force[f] / total if total > 0 else 0.0
            if wr > meilleur_wr:
                meilleur_wr = wr
                meilleure = f

        return meilleure

    def _generer_recommandations(
        self,
        win_rate: float,
        profit_factor: float,
        sharpe: float,
        max_dd: float,
        nb_rejets: Dict[str, int],
        score_ob_gagnants: float,
        score_ob_perdants: float,
        pnl_par_heure: Dict[int, float],
        entrees: List,
    ) -> List[str]:
        """
        Génère des recommandations actionnables (max 5).
        Basé sur les métriques calculées — chaque recommandation est spécifique
        et inclut une action concrète avec valeur numérique.
        """
        recommandations: List[str] = []

        # Win rate trop bas
        if win_rate < 40:
            recommandations.append(
                f"⚠️ Win rate faible ({win_rate:.1f}%) — "
                f"Augmenter le score minimum OB à 70 pour filtrer "
                f"les signaux de faible qualité"
            )

        # Profit Factor insuffisant
        if profit_factor < 1.5:
            recommandations.append(
                f"⚠️ Profit Factor insuffisant ({profit_factor:.2f}) — "
                f"Vérifier que R:R ≥ {self.config.RR_MINIMUM} est appliqué "
                f"sur tous les trades"
            )

        # Drawdown trop élevé
        if max_dd > 5.0:
            risque_reduit = round(self.config.RISQUE_PAR_TRADE_PCT * 0.7, 1)
            recommandations.append(
                f"🔴 Drawdown max élevé ({max_dd:.1f}%) — "
                f"Réduire le risque par trade de "
                f"{self.config.RISQUE_PAR_TRADE_PCT}% à {risque_reduit}% "
                f"jusqu'à stabilisation"
            )

        # Filtre le plus rejetant
        if nb_rejets:
            filtre_top = max(nb_rejets, key=nb_rejets.get)
            count_top = nb_rejets[filtre_top]
            total_rejets = sum(nb_rejets.values())
            pct_rejet = count_top / total_rejets * 100 if total_rejets > 0 else 0
            if pct_rejet > 40:
                recommandations.append(
                    f"📊 Filtre '{filtre_top}' rejette {pct_rejet:.0f}% des signaux "
                    f"({count_top}/{total_rejets}) — "
                    f"Vérifier si le seuil est trop strict sur cette période"
                )

        # Écart OB score gagnants vs perdants
        if score_ob_gagnants > 0 and score_ob_perdants > 0:
            ecart = score_ob_gagnants - score_ob_perdants
            if ecart > 10:
                seuil_suggere = int(score_ob_perdants + 5)
                recommandations.append(
                    f"✅ OB score moyen : gagnants={score_ob_gagnants:.0f} vs "
                    f"perdants={score_ob_perdants:.0f} (+{ecart:.0f} pts) — "
                    f"Augmenter le score minimum OB à {seuil_suggere}"
                )

        # Heures défavorables
        if pnl_par_heure:
            pires = sorted(pnl_par_heure.items(), key=lambda x: x[1])[:2]
            if pires and pires[0][1] < -0.5:
                heures_str = ", ".join(f"{h}h UTC" for h, _ in pires)
                recommandations.append(
                    f"⏰ Heures peu rentables : {heures_str} — "
                    f"Envisager un filtre horaire supplémentaire"
                )

        return recommandations[:5]  # Maximum 5 recommandations


# Alias anglais
PerformanceAnalyzer = AnalyseurPerformance
