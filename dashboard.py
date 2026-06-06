"""
dashboard.py — Interface terminal en temps réel avec Rich.
Affiche le P&L, les positions, les statistiques et l'état du bot.
"""

from datetime import datetime, timezone
from typing import Dict, List, Optional, Any
from rich.console import Console
from rich.layout import Layout
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich import box
from loguru import logger

from config import CONFIG


console = Console()


class Dashboard:
    """Dashboard terminal en temps réel pour le bot SMC."""

    def __init__(self, mode: str = "live") -> None:
        """
        Args:
            mode: "live", "paper" ou "backtest".
        """
        self.mode = mode.upper()
        self.live: Optional[Live] = None
        self._etat: Dict[str, Any] = {
            "balance": 0.0,
            "equity": 0.0,
            "devise": "USD",
            "profit_latent": 0.0,
            "drawdown_journalier_pct": 0.0,
            "drawdown_total_pct": 0.0,
            "tendance_h4": "INCONNU",
            "age_tendance_bougies": 0,
            "dernier_bos": "",
            "dernier_bos_force": "",
            "displacement_detail": "",
            "dernier_choch_str": "—",
            "choch_confirme": False,
            "bos_faibles_ignores": 0,
            "zone_ob_active": "",
            "signal_actuel": "EN ATTENTE",
            "positions": [],
            "trades_jour": 0,
            "gagnants_jour": 0,
            "perdu_jour": 0,
            "pnl_journalier": 0.0,
            "total_trades": 0,
            "win_rate": 0.0,
            "profit_factor": 0.0,
            "circuit_breaker": False,
            "bot_arrete": False,
            "derniere_mise_a_jour": "",
            # Circuit Breaker
            "cb_niveau": "AUCUN",
            "cb_raison": "",
            "cb_pertes_consecutives": 0,
            "cb_dd_pct": 0.0,
            "cb_trades_bloques": 0,
            "cb_risk_pct": 1.0,
            "cb_pause_jusqu_a": None,
            "cb_nb_trades": 0,
            "cb_win_rate": 0.0,
            "cb_pnl_usd": 0.0,
            "cb_pnl_pct": 0.0,
            # Order Blocks multi-TF
            "obs_actifs": [],   # Liste de dicts résumés des OB
            # Filtre spread
            "spread_tradeable": True,
            "spread_raison_blocage": "",
            "spread_actuel": 0.0,
            "spread_moyen_50min": 0.0,
            "spread_min_24h": None,
            "spread_max_24h": None,
            "spread_heure_min": "—",
            "spread_heure_max": "—",
            "spread_hard_cap": 35.0,
            # Filtre news
            "news_trading_autorise": True,
            "news_raison_blocage": "",
            "news_source": "—",
            "news_events_charges": 0,
            "news_prochaine": "—",
            "news_derniere_maj": "—",
            "news_calendar_frais": True,
        }

    def demarrer(self) -> None:
        """Lance le mode Live de Rich pour le rafraîchissement continu."""
        self.live = Live(
            self._construire_layout(),
            console=console,
            refresh_per_second=1,
            screen=False,
        )
        self.live.start()
        logger.debug("Dashboard démarré")

    def arreter(self) -> None:
        """Arrête l'affichage Live proprement."""
        if self.live:
            self.live.stop()
            logger.debug("Dashboard arrêté")

    def mettre_a_jour(self, **kwargs) -> None:
        """
        Met à jour l'état du dashboard.

        Args:
            **kwargs: Clés/valeurs à mettre à jour dans l'état interne.
        """
        self._etat.update(kwargs)
        self._etat["derniere_mise_a_jour"] = datetime.now(timezone.utc).strftime("%H:%M:%S UTC")
        if self.live:
            self.live.update(self._construire_layout())

    def _couleur_drawdown(self, pct: float, limite: float) -> str:
        """Retourne la couleur Rich selon le niveau de drawdown."""
        ratio = pct / limite
        if ratio >= 0.9:
            return "bold red"
        if ratio >= 0.6:
            return "bold yellow"
        return "bold green"

    def _construire_panel_compte(self) -> Panel:
        """Panneau affichant la balance, equity et drawdowns."""
        e = self._etat
        table = Table(box=None, show_header=False, padding=(0, 1))
        table.add_column("Clé", style="dim", width=20)
        table.add_column("Valeur", width=30)

        # Balance / Equity
        profit_signe = f"+{e['profit_latent']:.2f}" if e['profit_latent'] >= 0 else f"{e['profit_latent']:.2f}"
        couleur_profit = "green" if e["profit_latent"] >= 0 else "red"
        table.add_row("Balance", f"{e['balance']:.2f} {e['devise']}")
        table.add_row(
            "Equity",
            Text(f"{e['equity']:.2f} {e['devise']}  ({profit_signe})", style=couleur_profit)
        )

        # Drawdowns
        dd_jour = e["drawdown_journalier_pct"]
        dd_total = e["drawdown_total_pct"]
        table.add_row(
            "Drawdown jour",
            Text(
                f"{dd_jour:.2f}%  ({'✅' if dd_jour < CONFIG.MAX_DRAWDOWN_JOURNALIER_PCT else '🛑'}) "
                f"(limite: {CONFIG.MAX_DRAWDOWN_JOURNALIER_PCT}%)",
                style=self._couleur_drawdown(dd_jour, CONFIG.MAX_DRAWDOWN_JOURNALIER_PCT)
            )
        )
        table.add_row(
            "Drawdown total",
            Text(
                f"{dd_total:.2f}%  ({'✅' if dd_total < CONFIG.MAX_DRAWDOWN_TOTAL_PCT else '🚨'}) "
                f"(limite: {CONFIG.MAX_DRAWDOWN_TOTAL_PCT}%)",
                style=self._couleur_drawdown(dd_total, CONFIG.MAX_DRAWDOWN_TOTAL_PCT)
            )
        )

        # Circuit breaker
        if e["circuit_breaker"]:
            table.add_row("", Text("⚠️  CIRCUIT BREAKER ACTIF", style="bold red blink"))
        if e["bot_arrete"]:
            table.add_row("", Text("🛑  BOT ARRÊTÉ (drawdown max)", style="bold red blink"))

        return Panel(table, title="💰 COMPTE", border_style="blue")

    def _construire_panel_marche(self) -> Panel:
        """Panneau structure de marché H4 avec Displacement."""
        e = self._etat
        table = Table(box=None, show_header=False, padding=(0, 1))
        table.add_column("Clé", style="dim", width=20)
        table.add_column("Valeur", width=32)

        # Tendance
        tendance = e["tendance_h4"]
        if "BULL" in tendance:
            couleur_tendance = "bold green"
            emoji_tendance = "📈"
        elif "BEAR" in tendance:
            couleur_tendance = "bold red"
            emoji_tendance = "📉"
        elif "RANGE" in tendance:
            couleur_tendance = "dim"
            emoji_tendance = "⏸️ "
        else:
            couleur_tendance = "yellow"
            emoji_tendance = "❓"

        age_str = f" (depuis {e.get('age_tendance_bougies', '?')} bougies H4)" if e.get("age_tendance_bougies") else ""
        table.add_row(
            "Tendance",
            Text(f"{emoji_tendance} {tendance}{age_str}", style=couleur_tendance)
        )

        # Dernier BOS
        bos_str = e.get("dernier_bos") or "—"
        bos_force = e.get("dernier_bos_force", "")
        if "FORT" in bos_force:
            table.add_row("Dernier BOS", Text(f"✅ {bos_str}", style="green"))
        elif bos_str != "—":
            table.add_row("Dernier BOS", Text(f"⚠️  {bos_str}", style="yellow"))
        else:
            table.add_row("Dernier BOS", Text("—", style="dim"))

        # Détails Displacement
        disp_str = e.get("displacement_detail", "")
        if disp_str:
            table.add_row("Displacement", Text(disp_str, style="dim"))

        # CHoCH
        choch_str = e.get("dernier_choch_str", "—")
        choch_confirme = e.get("choch_confirme", False)
        if choch_confirme:
            table.add_row("CHoCH", Text(f"✅ {choch_str}", style="green"))
        elif choch_str != "—":
            table.add_row("CHoCH", Text(f"⚠️  TENTATIVE — {choch_str}", style="yellow"))
        else:
            table.add_row("CHoCH", Text("—", style="dim"))

        # BOS faibles ignorés
        nb_faibles = e.get("bos_faibles_ignores", 0)
        if nb_faibles > 0:
            table.add_row(
                "BOS faibles",
                Text(f"⚠️  {nb_faibles} ignorés (sans Displacement)", style="dim yellow")
            )

        # Signal actuel
        couleur_signal = "yellow"
        signal_str = e["signal_actuel"]
        if "LONG" in signal_str:
            couleur_signal = "bold green"
        elif "SHORT" in signal_str:
            couleur_signal = "bold red"
        table.add_row("Signal", Text(signal_str, style=couleur_signal))

        return Panel(table, title="📊 STRUCTURE H4", border_style="cyan")

    def _construire_panel_positions(self) -> Panel:
        """Panneau trade actif avec gestion 3 phases."""
        positions = self._etat["positions"]

        if not positions:
            contenu = Text("Aucune position ouverte", style="dim italic")
            return Panel(contenu, title="💼 TRADE ACTIF", border_style="green")

        pos = positions[0]  # Un seul trade à la fois
        couleur = "green" if pos["direction"] == "LONG" else "red"
        id_trade = pos.get("id_trade", "?")

        table = Table(box=None, show_header=False, padding=(0, 1))
        table.add_column("Clé", style="dim", width=18)
        table.add_column("Valeur", width=38)

        # Infos de base
        table.add_row(
            "Direction",
            Text(
                f"{'🟢' if pos['direction'] == 'LONG' else '🔴'} "
                f"{pos['direction']} {pos['symbole']}",
                style=f"bold {couleur}"
            )
        )
        table.add_row("Entrée", f"{pos['prix_entree']:.3f}")

        # Phase actuelle
        phase = pos.get("phase", "?")
        phase_emoji = {"PHASE_1_ATTENTE_TP1": "⏳", "PHASE_2_TP1_ATTEINT": "✅",
                       "PHASE_3_TP2_ATTEINT": "🎯", "FERMÉ": "🔒"}.get(phase, "?")
        table.add_row("Phase", Text(f"{phase_emoji} {phase}", style="bold cyan"))

        # Niveaux SL/Trailing
        sl = pos.get("sl", 0.0)
        trailing = pos.get("trailing_prix")
        sl_label = pos.get("phase", "")
        if "PHASE_3" in sl_label:
            sl_annot = "🔒 verrouillé à +1R"
        elif "PHASE_2" in sl_label:
            sl_annot = "🔒 breakeven"
        else:
            sl_annot = "initial"
        table.add_row("Stop Loss", f"{sl:.3f}  ({sl_annot})")
        if trailing:
            table.add_row("Trailing Stop", Text(f"{trailing:.3f}  (actif)", style="yellow"))

        # TP niveaux
        tp1_atteint = pos.get("tp1_atteint", False)
        tp2_atteint = pos.get("tp2_atteint", False)
        tp1_p = pos.get("tp1_prix", 0.0)
        tp2_p = pos.get("tp2_prix", 0.0)
        tp3_p = pos.get("tp3_prix", 0.0)

        table.add_row(
            "TP1 (50% @ 1R)",
            Text(
                f"✅ ATTEINT @ {tp1_p:.2f}" if tp1_atteint
                else f"⏳ {tp1_p:.2f}",
                style="green" if tp1_atteint else "dim"
            )
        )
        table.add_row(
            "TP2 (25% @ 2R)",
            Text(
                f"✅ ATTEINT @ {tp2_p:.2f}" if tp2_atteint
                else f"⏳ {tp2_p:.2f}",
                style="green" if tp2_atteint else "dim"
            )
        )
        table.add_row("TP3 (25% @ str.)", Text(f"⏳ {tp3_p:.2f}", style="dim"))

        # P&L
        pnl_r = pos.get("r_actuel", 0.0)
        pnl_reel = pos.get("pnl_realise", 0.0)
        pnl_float = pos.get("profit", 0.0)
        pnl_total = pnl_reel + pnl_float
        couleur_pnl = "green" if pnl_total >= 0 else "red"
        signe = "+" if pnl_total >= 0 else ""

        table.add_row("P&L réalisé", f"+${pnl_reel:.2f}  (TP1+TP2)")
        table.add_row("P&L flottant", Text(f"{signe}${pnl_float:.2f}", style=couleur_pnl))
        table.add_row(
            "P&L total",
            Text(f"{signe}${pnl_total:.2f}  ({pnl_r:+.2f}R)", style=f"bold {couleur_pnl}")
        )
        table.add_row(
            "MFE / MAE",
            f"MFE: {pos.get('mfe', 0):.2f}R  |  MAE: {pos.get('mae', 0):.2f}R"
        )

        # OB info
        score = pos.get("ob_score", 0)
        force = pos.get("ob_force", "—")
        if score > 0:
            table.add_row("OB Score", f"{score}/100 [{force}]")

        confluences = pos.get("confluences", [])
        if confluences:
            table.add_row("Confluences", " | ".join(confluences[:3]))

        couleur_bord = "green" if pnl_total >= 0 else "red"
        return Panel(
            table,
            title=f"💼 TRADE ACTIF [{id_trade}]",
            border_style=couleur_bord
        )

    def _construire_panel_news(self) -> Panel:
        """Panneau affichant le statut du filtre news."""
        e = self._etat
        table = Table(box=None, show_header=False, padding=(0, 1))
        table.add_column("Clé", style="dim", width=18)
        table.add_column("Valeur", width=35)

        # Statut trading
        if e["news_trading_autorise"]:
            statut_text = Text("✅ Trading autorisé", style="bold green")
        else:
            raison = e["news_raison_blocage"][:32] + "..." if len(e["news_raison_blocage"]) > 32 else e["news_raison_blocage"]
            statut_text = Text(f"🔴 BLOQUÉ — {raison}", style="bold red")

        table.add_row("Statut", statut_text)

        # Source et fraîcheur
        frais_emoji = "✅" if e["news_calendar_frais"] else "⚠️"
        table.add_row("Source", f"{e['news_source']} {frais_emoji}")
        table.add_row("Events chargés", f"{e['news_events_charges']}")
        table.add_row("Prochaine news", Text(e["news_prochaine"], style="yellow"))
        table.add_row("Dernière MAJ", e["news_derniere_maj"])

        couleur_bordure = "red" if not e["news_trading_autorise"] else "magenta"
        return Panel(table, title="📰 FILTRE NEWS", border_style=couleur_bordure)

    def _construire_panel_spread(self) -> Panel:
        """Panneau affichant le spread actuel et la volatilité."""
        e = self._etat
        table = Table(box=None, show_header=False, padding=(0, 1))
        table.add_column("Clé", style="dim", width=18)
        table.add_column("Valeur", width=35)

        # Spread actuel vs hard cap
        spread_act = e["spread_actuel"]
        hard_cap = e["spread_hard_cap"]
        if spread_act > hard_cap:
            spread_text = Text(f"🔴 {spread_act:.0f} pts — HARD CAP DÉPASSÉ", style="bold red")
        elif spread_act > hard_cap * 0.7:
            spread_text = Text(f"⚠️  {spread_act:.0f} pts (limite: {hard_cap:.0f} pts)", style="yellow")
        else:
            spread_text = Text(f"{spread_act:.0f} pts  ✅ (limite: {hard_cap:.0f} pts)", style="green")
        table.add_row("Spread actuel", spread_text)

        # Spread moyen et ratio
        moy = e["spread_moyen_50min"]
        if moy > 0 and spread_act > 0:
            ratio = spread_act / moy
            table.add_row("Spread moy 50min", f"{moy:.0f} pts  (ratio: {ratio:.1f}×)")
        else:
            table.add_row("Spread moy 50min", f"{moy:.0f} pts")

        # Min/Max 24h
        if e["spread_min_24h"] is not None:
            table.add_row(
                "Spread min 24h",
                f"{e['spread_min_24h']:.0f} pts  ({e['spread_heure_min']})"
            )
        if e["spread_max_24h"] is not None:
            couleur_max = "yellow" if e["spread_max_24h"] > hard_cap else "dim"
            table.add_row(
                "Spread max 24h",
                Text(
                    f"{e['spread_max_24h']:.0f} pts  ({e['spread_heure_max']})"
                    + ("  ⚠️" if e["spread_max_24h"] > hard_cap else ""),
                    style=couleur_max
                )
            )

        # Statut global
        if e["spread_tradeable"]:
            statut = Text("✅ Tradeable", style="bold green")
        else:
            raison = e["spread_raison_blocage"][:30] + "..." if len(e["spread_raison_blocage"]) > 30 else e["spread_raison_blocage"]
            statut = Text(f"🔴 {raison}", style="bold red")
        table.add_row("Statut", statut)

        couleur_bord = "red" if not e["spread_tradeable"] else "blue"
        return Panel(table, title="📡 SPREAD & VOLATILITÉ", border_style=couleur_bord)

    def _construire_panel_ob(self) -> Panel:
        """Panneau affichant les Order Blocks multi-TF actifs."""
        e = self._etat
        obs = e.get("obs_actifs", [])

        if not obs:
            contenu = Text("Aucun OB multi-TF valide (score ≥ 60)", style="dim italic")
            return Panel(contenu, title="🏛️  ORDER BLOCKS ACTIFS", border_style="yellow")

        table = Table(box=box.SIMPLE, show_header=True, header_style="bold")
        table.add_column("Direction", width=11)
        table.add_column("Score", width=8)
        table.add_column("Zone", width=18)
        table.add_column("TF", width=12)
        table.add_column("Force", width=15)
        table.add_column("Confluences", width=25)

        for ob in obs[:4]:  # Afficher les 4 meilleurs
            direction = ob.get("direction", "?")
            couleur = "green" if "HAUSSIER" in direction else "red"
            score = ob.get("score", 0)
            zone = ob.get("zone", "—")
            tf = ob.get("tf", "—")
            force = ob.get("force", "—")
            confluences = ob.get("confluences_str", "—")[:22]

            table.add_row(
                Text(direction, style=f"bold {couleur}"),
                f"{score}/100",
                zone,
                tf,
                Text(force, style="bold cyan" if "INSTIT" in force else "cyan"),
                confluences,
            )

        return Panel(table, title="🏛️  ORDER BLOCKS ACTIFS (multi-TF)", border_style="yellow")

    def _construire_panel_stats(self) -> Panel:
        """Panneau affichant les statistiques du jour et globales."""
        e = self._etat
        table = Table(box=None, show_header=False, padding=(0, 1))
        table.add_column("Clé", style="dim", width=20)
        table.add_column("Valeur", width=30)

        # Stats du jour
        pnl_jour = e["pnl_journalier"]
        couleur_jour = "green" if pnl_jour >= 0 else "red"
        signe_jour = "+" if pnl_jour >= 0 else ""
        table.add_row(
            "Stats du jour",
            Text(
                f"Trades: {e['trades_jour']} | "
                f"W:{e['gagnants_jour']} L:{e['perdu_jour']} | "
                f"{signe_jour}{pnl_jour:.2f}",
                style=couleur_jour
            )
        )

        # Stats globales
        pf_couleur = "green" if e["profit_factor"] >= 1.8 else "yellow"
        wr_couleur = "green" if e["win_rate"] >= 45 else "yellow"
        table.add_row(
            "Stats totales",
            Text(
                f"Trades: {e['total_trades']} | "
                f"WR: {e['win_rate']:.0f}% | "
                f"PF: {e['profit_factor']:.1f}",
                style="dim"
            )
        )

        table.add_row("Dernière MAJ", e["derniere_mise_a_jour"])

        return Panel(table, title="📉 STATISTIQUES", border_style="magenta")

    def _construire_panel_circuit_breaker(self) -> Panel:
        """Panneau Circuit Breaker et stats journalières."""
        e = self._etat
        table = Table(box=None, show_header=False, padding=(0, 1))
        table.add_column("Clé", style="dim", width=22)
        table.add_column("Valeur", width=32)

        # Statut CB
        niveau = e.get("cb_niveau", "AUCUN")
        if niveau == "AUCUN":
            statut = Text("✅ INACTIF", style="bold green")
            couleur_bord = "green"
        elif niveau == "WARNING":
            statut = Text(f"⚠️  WARNING — risk réduit à 0.5%", style="bold yellow")
            couleur_bord = "yellow"
        elif niveau == "PAUSE":
            pause = e.get("cb_pause_jusqu_a")
            if pause:
                from datetime import datetime
                try:
                    restant = (pause - datetime.utcnow()).total_seconds()
                    h, m = int(restant // 3600), int((restant % 3600) // 60)
                    statut = Text(f"🔴 PAUSE — {h}h{m:02d}min restantes", style="bold red")
                except Exception:
                    statut = Text("🔴 PAUSE active", style="bold red")
            else:
                statut = Text("🔴 PAUSE active", style="bold red")
            couleur_bord = "red"
        else:  # ARRETE
            statut = Text("⛔ ARRÊT JOURNALIER — reset à minuit UTC", style="bold red blink")
            couleur_bord = "red"

        table.add_row("Statut CB", statut)

        raison = e.get("cb_raison", "")
        if raison:
            table.add_row("Raison", Text(raison, style="dim"))

        # Métriques
        pertes = e.get("cb_pertes_consecutives", 0)
        dd = e.get("cb_dd_pct", 0.0)
        table.add_row(
            "Pertes consécutives",
            Text(
                f"{pertes}  (seuil WARNING: 2)",
                style="yellow" if pertes >= 2 else "dim"
            )
        )
        table.add_row(
            "DD journalier",
            Text(
                f"{dd:.2f}%  (seuil WARNING: 1.5%)",
                style="yellow" if dd >= 1.5 else "dim"
            )
        )

        # P&L jour
        pnl = e.get("cb_pnl_usd", 0.0)
        pnl_pct = e.get("cb_pnl_pct", 0.0)
        signe = "+" if pnl >= 0 else ""
        couleur_pnl = "green" if pnl >= 0 else "red"
        table.add_row(
            "P&L jour",
            Text(f"{signe}${pnl:.2f}  ({signe}{pnl_pct:.2f}%)", style=couleur_pnl)
        )

        # Stats trades
        nb = e.get("cb_nb_trades", 0)
        wr = e.get("cb_win_rate", 0.0)
        bloques = e.get("cb_trades_bloques", 0)
        table.add_row("Trades aujourd'hui", f"{nb} (WR: {wr:.0f}%)")
        if bloques > 0:
            table.add_row("Trades bloqués", Text(f"{bloques}", style="yellow"))

        # Risk actuel
        risk = e.get("cb_risk_pct", CONFIG.RISQUE_PAR_TRADE_PCT)
        table.add_row(
            "Risk actuel",
            Text(
                f"{risk:.1f}%  {'⚠️ réduit' if risk < CONFIG.RISQUE_PAR_TRADE_PCT else '(normal)'}",
                style="yellow" if risk < CONFIG.RISQUE_PAR_TRADE_PCT else "dim"
            )
        )

        return Panel(
            table,
            title="🔌 CIRCUIT BREAKER & STATS JOUR",
            border_style=couleur_bord
        )

    def _construire_layout(self) -> Panel:
        """Assemble tous les panneaux en un layout complet."""
        e = self._etat

        # Titre avec mode
        couleur_mode = {"LIVE": "red", "PAPER": "yellow", "BACKTEST": "cyan"}.get(self.mode, "white")
        titre = Text()
        titre.append("🤖 SMC TRADING BOT — ", style="bold white")
        titre.append(CONFIG.SYMBOLE, style="bold cyan")
        titre.append("        [", style="dim")
        titre.append(self.mode, style=f"bold {couleur_mode}")
        titre.append("]", style="dim")

        # Assembler les panneaux
        layout = Table(box=None, padding=0, expand=True)
        layout.add_column()
        layout.add_column()

        layout.add_row(
            self._construire_panel_compte(),
            self._construire_panel_marche(),
        )
        layout.add_row(
            self._construire_panel_positions(),
            self._construire_panel_stats(),
        )
        layout.add_row(
            self._construire_panel_news(),
            self._construire_panel_spread(),
        )
        layout.add_row(
            self._construire_panel_ob(),
            self._construire_panel_circuit_breaker(),
        )

        return Panel(layout, title=titre, border_style="bright_blue", padding=(0, 1))

    def afficher_statique(self) -> None:
        """Affiche le dashboard une seule fois (mode non-live)."""
        console.print(self._construire_layout())

    def afficher_message(self, message: str, style: str = "white") -> None:
        """Affiche un message en dehors du dashboard Live."""
        if self.live:
            # En mode live, passer par le logger (affiché au-dessus du live)
            pass
        else:
            console.print(Text(message, style=style))
