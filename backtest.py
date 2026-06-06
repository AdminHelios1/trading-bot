"""
backtest.py — Backtest vectorisé du setup SMC sur données historiques MT5.
Utilise un cache CSV local pour éviter les re-téléchargements.
Peut fonctionner sans connexion MT5 active si le cache est disponible.
"""

import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional, Dict, Tuple
import pandas as pd
import numpy as np
import matplotlib
matplotlib.use("Agg")  # Mode sans affichage (server)
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from loguru import logger

from config import CONFIG
from structure_analyzer import AnalyseurStructure, Tendance, TypeBOS
from ob_detector import DetecteurOB
from indicators import Indicateurs


class ResultatTrade:
    """Représente un trade simulé lors du backtest."""
    def __init__(
        self,
        timestamp_entree: pd.Timestamp,
        direction: str,
        prix_entree: float,
        sl: float,
        tp1: float,
        tp2: float,
        sl_distance: float,
    ) -> None:
        self.timestamp_entree = timestamp_entree
        self.direction = direction
        self.prix_entree = prix_entree
        self.sl = sl
        self.tp1 = tp1
        self.tp2 = tp2
        self.sl_distance = sl_distance
        # Résultats
        self.timestamp_sortie: Optional[pd.Timestamp] = None
        self.prix_sortie: float = 0.0
        self.resultat: str = ""  # "WIN", "LOSS", "BREAKEVEN"
        self.profit_r: float = 0.0  # En multiples de R
        self.profit_monetaire: float = 0.0
        self.duree_heures: float = 0.0


class Backtester:
    """Backtest vectorisé du setup SMC Breaker Block + Order Block."""

    def __init__(self) -> None:
        self.analyseur = AnalyseurStructure()
        self.detecteur_ob = DetecteurOB()
        Path("reports").mkdir(exist_ok=True)

    # ── Chargement des données ─────────────────────────────────────────────

    def charger_donnees(
        self,
        symbole: str,
        timeframe: int,
        date_debut: datetime,
        date_fin: datetime,
        tf_nom: str = "tf",
    ) -> pd.DataFrame:
        """
        Charge les données OHLCV depuis le cache CSV ou MT5.

        Args:
            symbole: Symbole MT5.
            timeframe: Constante MT5.
            date_debut: Date de début.
            date_fin: Date de fin.
            tf_nom: Nom court du timeframe pour le cache (ex: "H4", "M15").

        Returns:
            DataFrame OHLCV.
        """
        chemin_cache = Path(
            CONFIG.BACKTEST_CSV_CACHE.format(symbole=symbole, tf=tf_nom)
        )

        if chemin_cache.exists():
            logger.info(f"Chargement depuis le cache: {chemin_cache}")
            df = pd.read_csv(chemin_cache, index_col=0, parse_dates=True)
            df.index = pd.to_datetime(df.index, utc=True)
            # Filtrer sur la plage demandée
            df = df[
                (df.index >= pd.Timestamp(date_debut, tz="UTC"))
                & (df.index <= pd.Timestamp(date_fin, tz="UTC"))
            ]
            if len(df) > 100:
                return df

        # Téléchargement depuis MT5
        try:
            import MetaTrader5 as mt5
            if not mt5.initialize():
                raise ImportError("MT5 non disponible")

            rates = mt5.copy_rates_range(symbole, timeframe, date_debut, date_fin)
            if rates is None or len(rates) == 0:
                raise ValueError(f"Aucune donnée MT5 pour {symbole}")

            df = pd.DataFrame(rates)
            df["time"] = pd.to_datetime(df["time"], unit="s", utc=True)
            df = df.set_index("time")
            df = df.rename(columns={"tick_volume": "volume"})
            df = df[["open", "high", "low", "close", "volume", "spread"]]

            # Sauvegarder le cache
            chemin_cache.parent.mkdir(exist_ok=True)
            df.to_csv(chemin_cache)
            logger.info(f"Cache sauvegardé: {chemin_cache} ({len(df)} bougies)")
            return df

        except Exception as e:
            logger.error(f"Impossible de télécharger les données: {e}")
            raise

    # ── Simulation bougie par bougie ───────────────────────────────────────

    def executer(
        self,
        date_debut: str = CONFIG.BACKTEST_DATE_DEBUT,
        date_fin: str = CONFIG.BACKTEST_DATE_FIN,
        capital_initial: float = CONFIG.BACKTEST_CAPITAL_INITIAL,
    ) -> List[ResultatTrade]:
        """
        Exécute le backtest complet sur la plage de dates spécifiée.

        Args:
            date_debut: Date de début (format YYYY-MM-DD).
            date_fin: Date de fin (format YYYY-MM-DD).
            capital_initial: Capital de départ en USD.

        Returns:
            Liste des trades simulés.
        """
        import MetaTrader5 as mt5

        debut = datetime.strptime(date_debut, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        fin = datetime.strptime(date_fin, "%Y-%m-%d").replace(tzinfo=timezone.utc)

        logger.info(f"Backtest {CONFIG.SYMBOLE} | {date_debut} → {date_fin} | Capital: {capital_initial}")

        # Charger les données
        df_h4 = self.charger_donnees(CONFIG.SYMBOLE, mt5.TIMEFRAME_H4, debut, fin, "H4")
        df_m15 = self.charger_donnees(CONFIG.SYMBOLE, mt5.TIMEFRAME_M15, debut, fin, "M15")

        logger.info(f"Données chargées | H4: {len(df_h4)} bougies | M15: {len(df_m15)} bougies")

        trades: List[ResultatTrade] = []
        capital = capital_initial
        pertes_consecutives = 0
        circuit_breaker_fin: Optional[pd.Timestamp] = None

        # Fenêtre glissante : simuler bougie par bougie sur H4
        fenetre_h4 = 100
        fenetre_m15 = 150

        for i in range(fenetre_h4, len(df_h4) - 1):
            timestamp_courant = df_h4.index[i]

            # Circuit breaker
            if circuit_breaker_fin and timestamp_courant < circuit_breaker_fin:
                continue

            # Données jusqu'à la bougie courante (pas de lookahead!)
            df_h4_fenetre = df_h4.iloc[i - fenetre_h4:i + 1]

            # Données M15 correspondantes
            m15_mask = (df_m15.index <= timestamp_courant)
            df_m15_fenetre = df_m15[m15_mask].tail(fenetre_m15)

            if len(df_m15_fenetre) < 30:
                continue

            # Vérifier la session
            heure = timestamp_courant.hour
            en_session = CONFIG.SESSION_LONDON_OUVERTURE <= heure < CONFIG.SESSION_FERMETURE

            # Éviter la clôture journalière
            if CONFIG.CLOTURE_JOURNALIERE_DEBUT_UTC <= heure <= CONFIG.CLOTURE_JOURNALIERE_FIN_UTC:
                continue

            if not en_session:
                continue

            # Analyser la structure
            analyse = self.analyseur.analyser(df_h4_fenetre)
            ob_actifs, breakers = self.detecteur_ob.detecter_toutes_zones(df_h4_fenetre)
            toutes_zones = ob_actifs + breakers

            if not toutes_zones:
                continue

            # Vérifier ATR
            if not Indicateurs.atr_volatilite_suffisante(df_h4_fenetre):
                continue

            # Simuler le signal
            signal = self._simuler_signal(
                analyse, toutes_zones, df_h4_fenetre, df_m15_fenetre
            )
            if signal is None:
                continue

            direction, zone, prix_entree, atr_m15 = signal

            # Calculer SL/TP
            marge_sl = atr_m15 * CONFIG.SL_MARGE_ATR
            if direction == "LONG":
                sl = zone.prix_bas - marge_sl
                sl_distance = prix_entree - sl
            else:
                sl = zone.prix_haut + marge_sl
                sl_distance = sl - prix_entree

            if sl_distance <= 0:
                continue

            # Vérifier R:R minimum
            if direction == "LONG":
                tp1 = prix_entree + sl_distance * CONFIG.TP1_RR
                tp2 = prix_entree + sl_distance * CONFIG.TP2_RR
            else:
                tp1 = prix_entree - sl_distance * CONFIG.TP1_RR
                tp2 = prix_entree - sl_distance * CONFIG.TP2_RR

            # Simuler la sortie sur les données futures
            trade = self._simuler_sortie(
                df_m15, timestamp_courant, direction, prix_entree, sl, tp1, tp2, sl_distance
            )
            if trade is None:
                continue

            # Calculer le P&L
            lot = (capital * CONFIG.RISQUE_PAR_TRADE_PCT / 100) / sl_distance
            lot = max(0.01, min(lot, 100.0))  # Clamp simplifié pour backtest
            trade.profit_monetaire = trade.profit_r * capital * CONFIG.RISQUE_PAR_TRADE_PCT / 100
            capital += trade.profit_monetaire

            trades.append(trade)

            # Circuit breaker
            if trade.resultat == "LOSS":
                pertes_consecutives += 1
                if pertes_consecutives >= CONFIG.CIRCUIT_BREAKER_PERTES_CONSECUTIVES:
                    circuit_breaker_fin = trade.timestamp_sortie + pd.Timedelta(
                        hours=CONFIG.CIRCUIT_BREAKER_PAUSE_HEURES
                    )
                    logger.warning(f"Circuit breaker activé après {pertes_consecutives} pertes")
                    pertes_consecutives = 0
            else:
                pertes_consecutives = 0

        logger.info(f"Backtest terminé | {len(trades)} trades simulés | Capital final: {capital:.2f}")
        return trades

    def _simuler_signal(
        self, analyse, toutes_zones, df_h4_fenetre, df_m15_fenetre
    ) -> Optional[Tuple]:
        """Simule l'évaluation du signal sur les données historiques."""
        from ob_detector import TypeZone

        prix_actuel = float(df_m15_fenetre["close"].iloc[-1])
        rsi_m15 = Indicateurs.rsi(df_m15_fenetre)
        atr_m15_serie = Indicateurs.atr(df_m15_fenetre)
        atr_m15 = float(atr_m15_serie.iloc[-1]) if len(atr_m15_serie) > 0 else 0.0

        if atr_m15 <= 0:
            return None

        # Signal LONG
        if analyse.tendance == Tendance.HAUSSIERE and analyse.choch_recent:
            zones_h = [z for z in toutes_zones if z.est_haussier]
            if zones_h:
                zone = self.detecteur_ob.trouver_zone_la_plus_proche(zones_h, prix_actuel, "BULL")
                if zone and zone.contient(prix_actuel) and float(rsi_m15.iloc[-1]) < CONFIG.RSI_SEUIL_LONG:
                    if Indicateurs.bougie_rejet_haussiere(df_m15_fenetre, len(df_m15_fenetre) - 2):
                        return ("LONG", zone, prix_actuel, atr_m15)

        # Signal SHORT
        if analyse.tendance == Tendance.BAISSIERE and analyse.choch_recent:
            zones_b = [z for z in toutes_zones if not z.est_haussier]
            if zones_b:
                zone = self.detecteur_ob.trouver_zone_la_plus_proche(zones_b, prix_actuel, "BEAR")
                if zone and zone.contient(prix_actuel) and float(rsi_m15.iloc[-1]) > CONFIG.RSI_SEUIL_SHORT:
                    if Indicateurs.bougie_rejet_baissiere(df_m15_fenetre, len(df_m15_fenetre) - 2):
                        return ("SHORT", zone, prix_actuel, atr_m15)

        return None

    def _simuler_sortie(
        self,
        df_m15: pd.DataFrame,
        timestamp_entree: pd.Timestamp,
        direction: str,
        prix_entree: float,
        sl: float,
        tp1: float,
        tp2: float,
        sl_distance: float,
    ) -> Optional[ResultatTrade]:
        """Simule la sortie d'un trade sur les données M15 futures."""
        trade = ResultatTrade(timestamp_entree, direction, prix_entree, sl, tp1, tp2, sl_distance)
        tp1_atteint = False
        sl_breakeven = False

        # Données futures
        futures = df_m15[df_m15.index > timestamp_entree].head(500)  # Max ~5 jours

        for idx, row in futures.iterrows():
            high = row["high"]
            low = row["low"]

            if direction == "LONG":
                # TP1 partiel
                if not tp1_atteint and high >= tp1:
                    tp1_atteint = True
                    sl_breakeven = True

                # SL (ou breakeven après TP1)
                sl_effectif = prix_entree if sl_breakeven else sl
                if low <= sl_effectif:
                    trade.timestamp_sortie = idx
                    trade.prix_sortie = sl_effectif
                    if sl_breakeven:
                        trade.resultat = "WIN"  # TP1 atteint, breakeven sur le reste
                        trade.profit_r = CONFIG.TP1_RR * CONFIG.TP1_FRACTION
                    else:
                        trade.resultat = "LOSS"
                        trade.profit_r = -1.0
                    break

                # TP2 (objectif principal)
                if high >= tp2:
                    trade.timestamp_sortie = idx
                    trade.prix_sortie = tp2
                    trade.resultat = "WIN"
                    trade.profit_r = (
                        CONFIG.TP1_RR * CONFIG.TP1_FRACTION
                        + CONFIG.TP2_RR * (1 - CONFIG.TP1_FRACTION)
                        if tp1_atteint
                        else CONFIG.TP2_RR
                    )
                    break

            else:  # SHORT
                if not tp1_atteint and low <= tp1:
                    tp1_atteint = True
                    sl_breakeven = True

                sl_effectif = prix_entree if sl_breakeven else sl
                if high >= sl_effectif:
                    trade.timestamp_sortie = idx
                    trade.prix_sortie = sl_effectif
                    if sl_breakeven:
                        trade.resultat = "WIN"
                        trade.profit_r = CONFIG.TP1_RR * CONFIG.TP1_FRACTION
                    else:
                        trade.resultat = "LOSS"
                        trade.profit_r = -1.0
                    break

                if low <= tp2:
                    trade.timestamp_sortie = idx
                    trade.prix_sortie = tp2
                    trade.resultat = "WIN"
                    trade.profit_r = (
                        CONFIG.TP1_RR * CONFIG.TP1_FRACTION
                        + CONFIG.TP2_RR * (1 - CONFIG.TP1_FRACTION)
                        if tp1_atteint
                        else CONFIG.TP2_RR
                    )
                    break

        if trade.timestamp_sortie is None:
            return None  # Trade non terminé dans la fenêtre

        if trade.timestamp_sortie:
            duree = (trade.timestamp_sortie - timestamp_entree).total_seconds() / 3600
            trade.duree_heures = round(duree, 1)

        return trade

    # ── Calcul des métriques ───────────────────────────────────────────────

    def calculer_metriques(
        self,
        trades: List[ResultatTrade],
        capital_initial: float,
    ) -> Dict:
        """
        Calcule toutes les métriques de performance du backtest.

        Args:
            trades: Liste des trades simulés.
            capital_initial: Capital de départ.

        Returns:
            Dictionnaire de métriques.
        """
        if not trades:
            return {"erreur": "Aucun trade simulé"}

        profits = [t.profit_monetaire for t in trades]
        wins = [p for p in profits if p > 0]
        losses = [p for p in profits if p < 0]

        nb_trades = len(trades)
        nb_wins = len(wins)
        win_rate = nb_wins / nb_trades * 100 if nb_trades > 0 else 0

        # Profit factor
        total_gains = sum(wins) if wins else 0
        total_pertes = abs(sum(losses)) if losses else 1e-9
        profit_factor = total_gains / total_pertes

        # Equity curve
        capitaux = [capital_initial]
        for p in profits:
            capitaux.append(capitaux[-1] + p)

        # Max drawdown
        capital_max = capitaux[0]
        drawdowns = []
        for c in capitaux:
            if c > capital_max:
                capital_max = c
            dd = (capital_max - c) / capital_max * 100 if capital_max > 0 else 0
            drawdowns.append(dd)
        max_drawdown_pct = max(drawdowns) if drawdowns else 0
        max_drawdown_abs = max(capital_max - c for c in capitaux) if capitaux else 0

        # Sharpe ratio (rendements journaliers approximatifs)
        rendements_r = [t.profit_r for t in trades]
        if len(rendements_r) > 1:
            mean_r = np.mean(rendements_r)
            std_r = np.std(rendements_r)
            sharpe = (mean_r / std_r * np.sqrt(252)) if std_r > 0 else 0
        else:
            sharpe = 0

        # Calmar ratio
        rendement_annuel_pct = (capitaux[-1] / capital_initial - 1) * 100
        calmar = rendement_annuel_pct / max_drawdown_pct if max_drawdown_pct > 0 else 0

        # Série de pertes consécutives max
        serie_max = 0
        serie_courante = 0
        for p in profits:
            if p < 0:
                serie_courante += 1
                serie_max = max(serie_max, serie_courante)
            else:
                serie_courante = 0

        # R:R moyen des trades gagnants
        rr_moyen = np.mean([t.profit_r for t in trades if t.resultat == "WIN"]) if wins else 0

        return {
            "nb_trades": nb_trades,
            "nb_gagnants": nb_wins,
            "nb_perdants": len(losses),
            "win_rate_pct": round(win_rate, 1),
            "profit_factor": round(profit_factor, 2),
            "sharpe_annualise": round(sharpe, 2),
            "calmar_ratio": round(calmar, 2),
            "max_drawdown_pct": round(max_drawdown_pct, 2),
            "max_drawdown_abs": round(max_drawdown_abs, 2),
            "rr_moyen_gagnants": round(rr_moyen, 2),
            "serie_pertes_max": serie_max,
            "capital_initial": capital_initial,
            "capital_final": round(capitaux[-1], 2),
            "rendement_total_pct": round(rendement_annuel_pct, 1),
            "profit_net": round(capitaux[-1] - capital_initial, 2),
            "equity_curve": capitaux,
        }

    # ── Génération des rapports ────────────────────────────────────────────

    def generer_rapport(
        self,
        trades: List[ResultatTrade],
        metriques: Dict,
        date_debut: str,
        date_fin: str,
    ) -> None:
        """
        Génère les fichiers de rapport : CSV, PNG (equity curve), TXT (résumé).

        Args:
            trades: Liste des trades.
            metriques: Métriques calculées.
            date_debut: Date de début du backtest.
            date_fin: Date de fin du backtest.
        """
        from datetime import date
        date_rapport = date.today().strftime("%Y%m%d")

        # ── CSV détaillé ───────────────────────────────────────────────────
        chemin_csv = CONFIG.RAPPORT_CSV.format(
            symbole=CONFIG.SYMBOLE, date=date_rapport
        )
        rows = []
        for t in trades:
            rows.append({
                "entree": t.timestamp_entree,
                "sortie": t.timestamp_sortie,
                "direction": t.direction,
                "prix_entree": t.prix_entree,
                "prix_sortie": t.prix_sortie,
                "sl": t.sl,
                "tp1": t.tp1,
                "tp2": t.tp2,
                "resultat": t.resultat,
                "profit_r": t.profit_r,
                "profit_monetaire": t.profit_monetaire,
                "duree_heures": t.duree_heures,
            })
        pd.DataFrame(rows).to_csv(chemin_csv, index=False)
        logger.info(f"CSV sauvegardé: {chemin_csv}")

        # ── Equity curve PNG ───────────────────────────────────────────────
        chemin_png = CONFIG.RAPPORT_PNG.format(symbole=CONFIG.SYMBOLE)
        equity = metriques.get("equity_curve", [])
        if equity:
            fig, axes = plt.subplots(2, 1, figsize=(14, 8), facecolor="#1a1a2e")
            fig.suptitle(
                f"SMC Bot — {CONFIG.SYMBOLE} | {date_debut} → {date_fin}",
                color="white", fontsize=14, fontweight="bold"
            )

            # Equity curve
            ax1 = axes[0]
            ax1.set_facecolor("#16213e")
            couleurs = ["#00ff88" if equity[i] >= equity[i-1] else "#ff4444"
                       for i in range(1, len(equity))]
            for i in range(1, len(equity)):
                ax1.plot([i-1, i], [equity[i-1], equity[i]], color=couleurs[i-1], linewidth=1.5)
            ax1.fill_between(range(len(equity)), equity, min(equity), alpha=0.15, color="#00ff88")
            ax1.set_ylabel("Capital ($)", color="white")
            ax1.tick_params(colors="white")
            ax1.grid(True, alpha=0.2, color="gray")
            ax1.set_title("Equity Curve", color="white", fontsize=11)

            # Distribution des R
            ax2 = axes[1]
            ax2.set_facecolor("#16213e")
            r_values = [t.profit_r for t in trades]
            couleurs_r = ["#00ff88" if r > 0 else "#ff4444" for r in r_values]
            ax2.bar(range(len(r_values)), r_values, color=couleurs_r, alpha=0.8)
            ax2.axhline(y=0, color="white", linewidth=0.8, linestyle="--")
            ax2.set_xlabel("N° Trade", color="white")
            ax2.set_ylabel("R", color="white")
            ax2.tick_params(colors="white")
            ax2.grid(True, alpha=0.2, color="gray")
            ax2.set_title("Distribution des R par trade", color="white", fontsize=11)

            # Annotation métriques
            texte = (
                f"Win Rate: {metriques['win_rate_pct']}% | "
                f"PF: {metriques['profit_factor']} | "
                f"Sharpe: {metriques['sharpe_annualise']} | "
                f"Max DD: {metriques['max_drawdown_pct']}%"
            )
            fig.text(0.5, 0.01, texte, ha="center", color="#aaaaaa", fontsize=9)

            plt.tight_layout(rect=[0, 0.03, 1, 0.95])
            plt.savefig(chemin_png, dpi=150, facecolor="#1a1a2e", bbox_inches="tight")
            plt.close()
            logger.info(f"Equity curve sauvegardée: {chemin_png}")

        # ── Résumé TXT ─────────────────────────────────────────────────────
        chemin_txt = CONFIG.RAPPORT_TXT.format(symbole=CONFIG.SYMBOLE)
        with open(chemin_txt, "w", encoding="utf-8") as f:
            f.write(f"{'='*60}\n")
            f.write(f"  BACKTEST SMC BOT — {CONFIG.SYMBOLE}\n")
            f.write(f"  Période: {date_debut} → {date_fin}\n")
            f.write(f"{'='*60}\n\n")
            f.write(f"  Total trades       : {metriques['nb_trades']}\n")
            f.write(f"  Gagnants           : {metriques['nb_gagnants']} ({metriques['win_rate_pct']}%)\n")
            f.write(f"  Perdants           : {metriques['nb_perdants']}\n")
            f.write(f"  Profit Factor      : {metriques['profit_factor']}\n")
            f.write(f"  Sharpe Ratio       : {metriques['sharpe_annualise']}\n")
            f.write(f"  Calmar Ratio       : {metriques['calmar_ratio']}\n")
            f.write(f"  Max Drawdown       : {metriques['max_drawdown_pct']}% "
                    f"({metriques['max_drawdown_abs']:.2f}$)\n")
            f.write(f"  R:R moyen gagnants : {metriques['rr_moyen_gagnants']}\n")
            f.write(f"  Pertes consécutives max: {metriques['serie_pertes_max']}\n\n")
            f.write(f"  Capital initial    : {metriques['capital_initial']:.2f}$\n")
            f.write(f"  Capital final      : {metriques['capital_final']:.2f}$\n")
            f.write(f"  Profit net         : {metriques['profit_net']:.2f}$\n")
            f.write(f"  Rendement total    : {metriques['rendement_total_pct']}%\n")
            f.write(f"\n{'='*60}\n")
        logger.info(f"Résumé sauvegardé: {chemin_txt}")

        # Affichage console
        from rich.console import Console
        from rich.table import Table as RichTable
        c = Console()
        t = RichTable(title=f"Résultats Backtest {CONFIG.SYMBOLE}", show_header=True)
        t.add_column("Métrique", style="cyan")
        t.add_column("Valeur", style="bold")
        for k, v in metriques.items():
            if k == "equity_curve":
                continue
            t.add_row(k, str(v))
        c.print(t)
