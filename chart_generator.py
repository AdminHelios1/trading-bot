"""
chart_generator.py — Génération de graphiques OHLCV pour le journal de trades.

Style professionnel fond sombre. Toujours en mode non-interactif (Agg).
La génération ne bloque jamais la boucle principale — toute erreur est catchée.
"""

import os
from datetime import datetime, timedelta
from typing import Optional

from loguru import logger

from config import CONFIG

# Mode non-interactif AVANT tout import matplotlib — obligatoire car le bot
# tourne en arrière-plan sans écran dédié (Windows Server / headless)
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.gridspec as gridspec
import matplotlib.patches as patches


# ── Constantes de style ────────────────────────────────────────────────────

STYLE = {
    "bg_color":      "#0D1117",    # Fond très sombre
    "grid_color":    "#21262D",    # Grille subtile
    "bull_candle":   "#26A69A",    # Bougies haussières : vert teal
    "bear_candle":   "#EF5350",    # Bougies baissières : rouge
    "ob_bull":       "#26A69A33",  # Zone OB bullish (transparent)
    "ob_bear":       "#EF535033",  # Zone OB bearish (transparent)
    "asian_zone":    "#FFD70018",  # Range asiatique (or transparent)
    "entry_color":   "#FFFFFF",    # Entrée blanche
    "sl_color":      "#FF4444",    # SL rouge
    "tp1_color":     "#44FF44",    # TP1 vert clair
    "tp2_color":     "#00CC88",    # TP2 vert émeraude
    "tp3_color":     "#0088FF",    # TP3 bleu
    "text_color":    "#E6EDF3",    # Texte clair
    "accent_color":  "#F0883E",    # Accents orange
    "volume_color":  "#58A6FF44",  # Volume bleu transparent
}


class GenerateurGraphiques:
    """
    Génère les graphiques OHLCV matplotlib pour chaque trade fermé.

    Un graphique = 3 panels :
    - Principal (60%) : bougies M15 + OB zone + SL/TP + entrée/sortie
    - Volume (20%)    : barres de volume colorées
    - RSI (20%)       : RSI(14) avec zones surachat/survente

    Toujours en mode Agg (non-interactif). Jamais d'exception vers l'appelant.
    """

    def __init__(self, connecteur=None, config=None) -> None:
        self.connecteur = connecteur
        self.config = config or CONFIG

    # ── Interface principale ───────────────────────────────────────────────

    def generer_graphique_trade(
        self,
        entree,             # TradeJournalEntry
        session_asiatique,  # Optional[DonneesSessionAsiatique]
        chemin_sortie: str,
    ) -> str:
        """
        Génère le graphique complet d'un trade et le sauvegarde en PNG.

        Args:
            entree            : Entrée de journal du trade fermé.
            session_asiatique : Données session asiatique (ou None).
            chemin_sortie     : Chemin de sauvegarde du fichier PNG.

        Returns:
            Chemin du fichier généré, ou chaîne vide si échec.
        """
        try:
            import pandas as pd
            from indicators import Indicateurs

            df = self._get_donnees_graphique(entree)
            if df is None or len(df) < 10:
                logger.warning(
                    f"Données insuffisantes pour le graphique [{entree.id_trade}]"
                )
                return ""

            # Calculer RSI
            try:
                rsi_serie = Indicateurs.rsi(df, period=14)
            except Exception:
                rsi_serie = None

            # Créer la figure
            fig = plt.figure(figsize=(16, 10), facecolor=STYLE["bg_color"])
            gs = gridspec.GridSpec(3, 1, height_ratios=[5, 1.5, 1.5], hspace=0.05)

            ax_main = fig.add_subplot(gs[0])
            ax_vol  = fig.add_subplot(gs[1], sharex=ax_main)
            ax_rsi  = fig.add_subplot(gs[2], sharex=ax_main)

            self._appliquer_style(ax_main, ax_vol, ax_rsi)

            x = list(range(len(df)))

            # Panels
            self._dessiner_bougies(ax_main, df, x)
            self._dessiner_zone_ob(ax_main, entree, df)
            self._dessiner_range_asiatique(ax_main, entree, session_asiatique, df)
            self._dessiner_niveaux_sl_tp(ax_main, entree, df)
            self._dessiner_marqueurs_entree_sortie(ax_main, entree, df)
            self._dessiner_annotations(ax_main, entree)
            self._dessiner_volume(ax_vol, df, x)
            self._dessiner_rsi(ax_rsi, df, x, rsi_serie)
            self._ajouter_legende(ax_main, entree)

            # Titre
            direction_emoji = "📈" if entree.direction == "LONG" else "📉"
            result_emoji = "🟢" if entree.pnl_total_usd > 0 else "🔴"
            titre = (
                f"{result_emoji} {direction_emoji} {entree.symbole} M15 | "
                f"[{entree.id_trade}] | "
                f"{entree.heure_entree.strftime('%Y-%m-%d %H:%M')} UTC | "
                f"R:{entree.total_r_realise:+.2f} | "
                f"${entree.pnl_total_usd:+.2f} | "
                f"Raison: {entree.raison_fermeture} | "
                f"OB:{entree.ob_score}/100 [{entree.ob_force}]"
            )
            fig.suptitle(
                titre, color=STYLE["text_color"],
                fontsize=10, fontweight="bold", y=0.98
            )

            # X-ticks sur panel RSI uniquement
            plt.setp(ax_main.get_xticklabels(), visible=False)
            plt.setp(ax_vol.get_xticklabels(), visible=False)
            n = len(df)
            tick_pos = list(range(0, n, max(1, n // 8)))
            ax_rsi.set_xticks(tick_pos)
            ax_rsi.set_xticklabels(
                [df.index[i].strftime("%H:%M") for i in tick_pos
                 if i < len(df.index)],
                color=STYLE["text_color"], fontsize=8
            )

            # Sauvegarder
            os.makedirs(os.path.dirname(chemin_sortie), exist_ok=True)
            plt.savefig(
                chemin_sortie, dpi=120, bbox_inches="tight",
                facecolor=STYLE["bg_color"]
            )
            plt.close(fig)

            logger.info(f"Graphique trade généré : {chemin_sortie}")
            return chemin_sortie

        except Exception as e:
            logger.error(
                f"Erreur génération graphique trade [{entree.id_trade}] : {e}"
            )
            try:
                plt.close("all")
            except Exception:
                pass
            return ""

    # Alias anglais
    def generate_trade_chart(
        self, entry, asian_session, output_path: str
    ) -> str:
        return self.generer_graphique_trade(entry, asian_session, output_path)

    def generer_courbe_equity(
        self,
        entrees: list,
        semaine_str: str,
        dossier: str = "journal/charts",
    ) -> str:
        """
        Génère le graphique d'equity curve (R cumulé) pour le rapport hebdomadaire.

        Args:
            entrees    : Liste des entrées de journal triées par date.
            semaine_str: String de la semaine (ex: "2025-06-02").
            dossier    : Répertoire de sortie.

        Returns:
            Chemin du fichier PNG généré.
        """
        try:
            fig, ax = plt.subplots(figsize=(12, 4), facecolor=STYLE["bg_color"])
            ax.set_facecolor(STYLE["bg_color"])

            tries = sorted(entrees, key=lambda x: x.heure_entree)
            r_cumule = [0.0]
            for e in tries:
                r_cumule.append(r_cumule[-1] + e.total_r_realise)

            x = list(range(len(r_cumule)))
            ax.plot(x, r_cumule, color="#58A6FF", linewidth=1.5, zorder=3)
            ax.fill_between(x, r_cumule, color="#58A6FF", alpha=0.1)
            ax.axhline(y=0, color="#8B949E", linewidth=0.5, linestyle="--")

            ax.set_title("Equity Curve (R cumulé)", color=STYLE["text_color"], fontsize=11)
            ax.tick_params(colors=STYLE["text_color"])
            ax.grid(True, color=STYLE["grid_color"], alpha=0.5)
            for spine in ax.spines.values():
                spine.set_color(STYLE["grid_color"])
            ax.yaxis.label.set_color(STYLE["text_color"])

            chemin = os.path.join(dossier, f"equity_{semaine_str}.png")
            os.makedirs(dossier, exist_ok=True)
            plt.savefig(chemin, dpi=100, bbox_inches="tight",
                        facecolor=STYLE["bg_color"])
            plt.close(fig)
            return chemin
        except Exception as e:
            logger.error(f"Erreur génération equity curve : {e}")
            try:
                plt.close("all")
            except Exception:
                pass
            return ""

    def generer_distribution_r(
        self,
        distribution: dict,
        semaine_str: str,
        dossier: str = "journal/charts",
    ) -> str:
        """Génère le graphique de distribution des R:R réalisés."""
        try:
            fig, ax = plt.subplots(figsize=(10, 4), facecolor=STYLE["bg_color"])
            ax.set_facecolor(STYLE["bg_color"])

            labels = list(distribution.keys())
            valeurs = list(distribution.values())
            couleurs = [
                STYLE["bear_candle"], STYLE["bear_candle"], "#FFD700",
                STYLE["bull_candle"], STYLE["bull_candle"], "#00CC88",
            ][:len(labels)]

            bars = ax.bar(labels, valeurs, color=couleurs, alpha=0.85, width=0.6)
            for bar, val in zip(bars, valeurs):
                if val > 0:
                    ax.text(
                        bar.get_x() + bar.get_width() / 2,
                        bar.get_height() + 0.05, str(val),
                        ha="center", va="bottom",
                        color=STYLE["text_color"], fontsize=10, fontweight="bold"
                    )

            ax.set_title("Distribution des R:R réalisés",
                         color=STYLE["text_color"], fontsize=11)
            ax.tick_params(colors=STYLE["text_color"])
            ax.grid(True, color=STYLE["grid_color"], alpha=0.5, axis="y")
            for spine in ax.spines.values():
                spine.set_color(STYLE["grid_color"])

            chemin = os.path.join(dossier, f"distribution_{semaine_str}.png")
            os.makedirs(dossier, exist_ok=True)
            plt.savefig(chemin, dpi=100, bbox_inches="tight",
                        facecolor=STYLE["bg_color"])
            plt.close(fig)
            return chemin
        except Exception as e:
            logger.error(f"Erreur génération distribution R : {e}")
            try:
                plt.close("all")
            except Exception:
                pass
            return ""

    # ── Données OHLCV ──────────────────────────────────────────────────────

    def _get_donnees_graphique(self, entree) -> Optional[object]:
        """Récupère 50 bougies M15 centrées sur le trade."""
        if self.connecteur is None:
            return None
        try:
            import MetaTrader5 as mt5
            df = self.connecteur.get_ohlcv(
                entree.symbole, mt5.TIMEFRAME_M15, n_bougies=80
            )
            if df is None or len(df) < 10:
                return None

            # Trouver l'index le plus proche de l'heure d'entrée
            cible = entree.heure_entree
            if hasattr(df.index, "tzinfo") and df.index.tzinfo is not None:
                import pandas as pd
                cible = pd.Timestamp(entree.heure_entree, tz="UTC")

            diffs = (df.index - cible).abs()
            idx_centre = diffs.argmin()
            debut = max(0, idx_centre - 25)
            fin = min(len(df), idx_centre + 26)
            return df.iloc[debut:fin].copy()

        except Exception as e:
            logger.debug(f"Données graphique non disponibles : {e}")
            return None

    # ── Dessin des éléments ────────────────────────────────────────────────

    def _appliquer_style(self, *axes) -> None:
        """Applique le style sombre à tous les axes."""
        for ax in axes:
            ax.set_facecolor(STYLE["bg_color"])
            ax.tick_params(colors=STYLE["text_color"], labelsize=8)
            for spine in ax.spines.values():
                spine.set_color(STYLE["grid_color"])
            ax.grid(True, color=STYLE["grid_color"], alpha=0.4, linewidth=0.5)
            ax.yaxis.label.set_color(STYLE["text_color"])

    def _dessiner_bougies(self, ax, df, x: list) -> None:
        """Dessine les bougies japonaises."""
        for i, (idx, row) in enumerate(df.iterrows()):
            est_haussiere = row["close"] >= row["open"]
            couleur = STYLE["bull_candle"] if est_haussiere else STYLE["bear_candle"]
            corps_bas = min(row["open"], row["close"])
            corps_h = abs(row["close"] - row["open"]) or 0.01
            ax.bar(i, corps_h, bottom=corps_bas, width=0.8, color=couleur, alpha=0.9)
            ax.plot([i, i], [row["low"], row["high"]],
                    color=couleur, linewidth=0.8, alpha=0.8)

    def _dessiner_zone_ob(self, ax, entree, df) -> None:
        """Dessine la zone Order Block."""
        est_long = entree.direction == "LONG"
        couleur_recto = STYLE["ob_bull"] if est_long else STYLE["ob_bear"]
        couleur_bord = STYLE["bull_candle"] if est_long else STYLE["bear_candle"]

        ob_haut = entree.sl_initial if not est_long else entree.prix_entree
        ob_bas = entree.prix_entree if not est_long else entree.sl_initial

        if ob_haut > ob_bas and ob_haut > 0:
            rect = plt.Rectangle(
                (0, ob_bas), len(df), ob_haut - ob_bas,
                facecolor=couleur_recto, edgecolor=couleur_bord,
                linewidth=1, linestyle="--", alpha=0.6
            )
            ax.add_patch(rect)
            ax.text(
                1, ob_bas + (ob_haut - ob_bas) * 0.5,
                f"OB Zone\n{entree.ob_force}\nScore:{entree.ob_score}/100",
                color=couleur_bord, fontsize=7, va="center", alpha=0.9
            )

    def _dessiner_range_asiatique(self, ax, entree, session_asiatique, df) -> None:
        """Dessine le range de la session asiatique si disponible."""
        if session_asiatique is None:
            return
        try:
            haut = float(session_asiatique.haut_session)
            bas  = float(session_asiatique.bas_session)
            if haut <= bas or haut <= 0:
                return

            rect = plt.Rectangle(
                (0, bas), len(df), haut - bas,
                facecolor=STYLE["asian_zone"], edgecolor="#FFD700",
                linewidth=0.5, linestyle=":", alpha=0.5
            )
            ax.add_patch(rect)
            ax.axhline(y=haut, color="#FFD700", linewidth=0.5, linestyle=":", alpha=0.6)
            ax.axhline(y=bas,  color="#FFD700", linewidth=0.5, linestyle=":", alpha=0.6)
            ax.text(len(df) - 1, haut + 0.5,
                    f"Asian H:{haut:.2f}",
                    color="#FFD700", fontsize=7, ha="right", alpha=0.8)
            ax.text(len(df) - 1, bas - 1.5,
                    f"Asian L:{bas:.2f}",
                    color="#FFD700", fontsize=7, ha="right", alpha=0.8)
        except Exception:
            pass

    def _dessiner_niveaux_sl_tp(self, ax, entree, df) -> None:
        """Dessine les lignes SL, TP1, TP2, TP3."""
        niveaux = [
            (entree.sl_initial, STYLE["sl_color"],  "SL",  "--", 1.2),
            (entree.tp1,        STYLE["tp1_color"], "TP1", "-",  0.8),
            (entree.tp2,        STYLE["tp2_color"], "TP2", "-",  1.0),
            (entree.tp3,        STYLE["tp3_color"], "TP3", "-",  0.8),
        ]
        for prix, couleur, label, style, lw in niveaux:
            if prix and prix > 0:
                ax.axhline(y=prix, color=couleur, linewidth=lw,
                           linestyle=style, alpha=0.8)
                ax.text(len(df) - 0.5, prix,
                        f" {label}:{prix:.2f}",
                        color=couleur, fontsize=8, va="center", fontweight="bold")

    def _dessiner_marqueurs_entree_sortie(self, ax, entree, df) -> None:
        """Marque les points d'entrée et de sortie sur le graphique."""
        idx_entree = self._trouver_index_bougie(df, entree.heure_entree)
        idx_sortie = self._trouver_index_bougie(df, entree.heure_sortie)
        est_long = entree.direction == "LONG"

        if idx_entree is not None:
            marker = "^" if est_long else "v"
            ax.scatter([idx_entree], [entree.prix_entree],
                       marker=marker, s=200, color=STYLE["entry_color"],
                       zorder=5, linewidths=2)
            ax.text(idx_entree, entree.prix_entree,
                    f"\n  ENTRY\n  {entree.prix_entree:.2f}",
                    color=STYLE["entry_color"], fontsize=7, fontweight="bold")

        if idx_sortie is not None:
            couleur_sortie = "#44FF44" if entree.pnl_total_usd > 0 else "#FF4444"
            marker = "v" if est_long else "^"
            ax.scatter([idx_sortie], [entree.prix_sortie],
                       marker=marker, s=200, color=couleur_sortie, zorder=5)
            ax.text(idx_sortie, entree.prix_sortie,
                    f"\n  EXIT\n  {entree.prix_sortie:.2f}\n"
                    f"  {entree.total_r_realise:+.2f}R",
                    color=couleur_sortie, fontsize=7, fontweight="bold")

    def _dessiner_annotations(self, ax, entree) -> None:
        """Ajoute les annotations texte en bas du panel principal."""
        texte = (
            f"Bias: {entree.biais_journalier} [{entree.force_biais}] | "
            f"Trend H4: {entree.tendance_h4} | "
            f"Session: {entree.session_entree} | "
            f"MFE: {entree.mfe_r:.2f}R | MAE: {entree.mae_r:.2f}R"
        )
        ax.text(
            0.01, 0.02, texte, transform=ax.transAxes,
            color=STYLE["text_color"], fontsize=7, va="bottom",
            bbox=dict(boxstyle="round,pad=0.3",
                      facecolor=STYLE["grid_color"], alpha=0.8)
        )

    def _dessiner_volume(self, ax, df, x: list) -> None:
        """Dessine les barres de volume."""
        col_vol = "volume" if "volume" in df.columns else (
            "tick_volume" if "tick_volume" in df.columns else None
        )
        if col_vol is None:
            ax.text(0.5, 0.5, "Volume N/A",
                    transform=ax.transAxes, color=STYLE["text_color"],
                    ha="center", va="center", fontsize=8)
            return

        couleurs = [
            STYLE["bull_candle"] if df["close"].iloc[i] >= df["open"].iloc[i]
            else STYLE["bear_candle"]
            for i in range(len(df))
        ]
        ax.bar(x, df[col_vol], color=couleurs, alpha=0.7, width=0.8)
        ax.set_ylabel("Volume", color=STYLE["text_color"], fontsize=8)

    def _dessiner_rsi(self, ax, df, x: list, rsi_serie) -> None:
        """Dessine le RSI(14)."""
        if rsi_serie is None or len(rsi_serie) < len(df):
            ax.text(0.5, 0.5, "RSI N/A",
                    transform=ax.transAxes, color=STYLE["text_color"],
                    ha="center", va="center", fontsize=8)
            return

        # Aligner RSI avec le DataFrame
        rsi_vals = rsi_serie.values[-len(df):]
        ax.plot(x[:len(rsi_vals)], rsi_vals,
                color=STYLE["accent_color"], linewidth=1.2)
        ax.axhline(y=70, color=STYLE["bear_candle"],
                   linewidth=0.5, linestyle="--", alpha=0.5)
        ax.axhline(y=30, color=STYLE["bull_candle"],
                   linewidth=0.5, linestyle="--", alpha=0.5)
        ax.fill_between(x[:len(rsi_vals)], 70, 100,
                        color=STYLE["bear_candle"], alpha=0.05)
        ax.fill_between(x[:len(rsi_vals)], 0, 30,
                        color=STYLE["bull_candle"], alpha=0.05)
        ax.set_ylim(0, 100)
        ax.set_ylabel("RSI(14)", color=STYLE["text_color"], fontsize=8)
        ax.set_yticks([30, 50, 70])
        ax.set_yticklabels(["30", "50", "70"],
                           color=STYLE["text_color"], fontsize=7)

    def _ajouter_legende(self, ax, entree) -> None:
        """Ajoute la légende au panel principal."""
        from matplotlib.patches import Patch
        from matplotlib.lines import Line2D

        elements = [
            Patch(facecolor=STYLE["ob_bull"], edgecolor=STYLE["bull_candle"],
                  label=f"OB Zone ({entree.ob_force})"),
            Line2D([0], [0], color=STYLE["sl_color"], linestyle="--",
                   label=f"SL: {entree.sl_initial:.2f}"),
            Line2D([0], [0], color=STYLE["tp1_color"],
                   label=f"TP1: {entree.tp1:.2f}"),
            Line2D([0], [0], color=STYLE["tp2_color"],
                   label=f"TP2: {entree.tp2:.2f}"),
        ]
        ax.legend(
            handles=elements, loc="upper left",
            facecolor=STYLE["grid_color"],
            edgecolor=STYLE["grid_color"],
            labelcolor=STYLE["text_color"],
            fontsize=7, framealpha=0.8
        )

    def _trouver_index_bougie(self, df, cible: Optional[datetime]) -> Optional[int]:
        """Trouve l'index DataFrame le plus proche d'un datetime (±30 min)."""
        if cible is None or df is None or len(df) == 0:
            return None
        try:
            import pandas as pd
            if hasattr(df.index, "tzinfo") and df.index.tzinfo is not None:
                cible_ts = pd.Timestamp(cible, tz="UTC")
            else:
                cible_ts = pd.Timestamp(cible)
            diffs = (df.index - cible_ts).abs()
            idx = diffs.argmin()
            if diffs.min().total_seconds() > 1800:  # > 30 min
                return None
            return idx
        except Exception:
            return None


# Alias anglais
ChartGenerator = GenerateurGraphiques
