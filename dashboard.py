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
            "dernier_bos": "",
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
        """Panneau affichant la structure de marché et les zones actives."""
        e = self._etat
        table = Table(box=None, show_header=False, padding=(0, 1))
        table.add_column("Clé", style="dim", width=20)
        table.add_column("Valeur", width=30)

        couleur_tendance = "green" if "BULL" in e["tendance_h4"] else "red"
        table.add_row("Trend H4", Text(e["tendance_h4"], style=f"bold {couleur_tendance}"))
        table.add_row("Dernier BOS/CHoCH", e["dernier_bos"] or "—")
        table.add_row("Zone OB active", e["zone_ob_active"] or "Aucune")

        # Signal
        couleur_signal = "yellow"
        if "LONG" in e["signal_actuel"]:
            couleur_signal = "bold green"
        elif "SHORT" in e["signal_actuel"]:
            couleur_signal = "bold red"
        table.add_row("Signal actuel", Text(e["signal_actuel"], style=couleur_signal))

        return Panel(table, title="📊 MARCHÉ", border_style="cyan")

    def _construire_panel_positions(self) -> Panel:
        """Panneau affichant les positions ouvertes."""
        positions = self._etat["positions"]

        if not positions:
            contenu = Text("Aucune position ouverte", style="dim italic")
            return Panel(contenu, title="📈 POSITIONS", border_style="green")

        table = Table(box=box.SIMPLE, show_header=True, header_style="bold")
        table.add_column("Direction", width=8)
        table.add_column("Volume", width=8)
        table.add_column("Entrée", width=10)
        table.add_column("SL", width=10)
        table.add_column("TP2", width=10)
        table.add_column("P&L", width=12)
        table.add_column("R actuel", width=8)
        table.add_column("TP1", width=5)

        for pos in positions:
            couleur = "green" if pos["direction"] == "LONG" else "red"
            profit = pos["profit"]
            couleur_pnl = "green" if profit >= 0 else "red"
            signe = "+" if profit >= 0 else ""

            table.add_row(
                Text(pos["direction"], style=f"bold {couleur}"),
                f"{pos['volume']:.2f}",
                f"{pos['prix_entree']:.3f}",
                f"{pos['sl']:.3f}",
                f"{pos['tp2']:.3f}",
                Text(f"{signe}{profit:.2f}", style=couleur_pnl),
                f"{pos['r_actuel']:.2f}R",
                "✅" if pos["tp1_atteint"] else "⏳",
            )

        return Panel(table, title="📈 POSITIONS OUVERTES", border_style="green")

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
            Text(""),  # Colonne vide pour équilibrer
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
