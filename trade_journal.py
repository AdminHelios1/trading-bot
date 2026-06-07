"""
trade_journal.py — Journal automatique de trades pour le SMC Trading Bot XAUUSD.

À chaque fermeture de position, enregistre toutes les données dans un CSV structuré,
génère un graphique OHLCV et trace les signaux rejetés pour analyse post-mortem.

Le CSV est le format primaire — les graphiques et rapports HTML sont secondaires.
Si la génération du graphique échoue, le CSV est préservé (chart_path = None est valide).
"""

import base64
import csv
import json
import os
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Dict, List, Optional

from loguru import logger

from chart_generator import GenerateurGraphiques
from config import CONFIG
from performance_analyzer import AnalyseurPerformance


# ── Dataclasses ────────────────────────────────────────────────────────────

@dataclass
class SignalRejete:
    """Signal rejeté par un filtre — enregistré pour analyse des filtres."""
    timestamp_utc: datetime
    direction: str              # "bullish" / "bearish" / "LONG" / "SHORT"
    rejete_par: str             # Nom du filtre (ex: "spread_filter", "news_filter")
    raison: str                 # Raison lisible
    prix_actuel: float
    ob_score: Optional[int]
    ob_force: Optional[str]
    tendance_h4: str
    biais_journalier: str
    heure_utc: int              # Heure UTC du rejet


@dataclass
class EntreeJournal:
    """Entrée complète du journal pour un trade fermé."""
    # Identifiants
    journal_id: str
    id_trade: str
    ticket_mt5: int

    # Trade de base
    symbole: str
    direction: str              # "LONG" ou "SHORT"
    prix_entree: float
    heure_entree: datetime
    prix_sortie: float
    heure_sortie: Optional[datetime]
    duree_minutes: int

    # Lots
    lots_initial: float

    # Niveaux
    sl_initial: float
    sl_final: float
    tp1: float
    tp2: float
    tp3: float

    # Résultat
    raison_fermeture: str
    tp1_atteint: bool
    tp2_atteint: bool
    tp3_atteint: bool
    total_r_realise: float
    pnl_total_usd: float

    # Métriques de qualité
    mfe_r: float
    mae_r: float
    ob_score: int
    ob_force: str
    confluences: List[str]

    # Contexte marché
    tendance_h4: str
    biais_journalier: str
    force_biais: str
    session_entree: str         # "LONDON" ou "NEW_YORK" ou "AUTRE"
    heure_entree_utc: int
    asian_high: Optional[float] = None
    asian_low: Optional[float] = None

    # Add-on pyramiding
    avec_addon: bool = False
    addon_pnl_usd: float = 0.0
    addon_r: float = 0.0

    # Paper trading
    slippage_entree_pts: float = 0.0
    slippage_sortie_pts: float = 0.0
    qualite_execution: str = "N/A"

    # Fichiers générés
    chemin_graphique: Optional[str] = None


# Alias anglais
TradeJournalEntry = EntreeJournal
RejectedSignal = SignalRejete


# ── Journal principal ──────────────────────────────────────────────────────

class JournalTrades:
    """
    Journal automatique de trades — coordinateur principal.

    Responsabilités :
    1. Enregistrer chaque trade fermé dans trades.csv (priorité absolue)
    2. Enregistrer les signaux rejetés dans rejected_signals.csv (léger)
    3. Générer le graphique OHLCV en arrière-plan (secondaire)
    4. Générer le rapport HTML hebdomadaire

    Architecture : CSV first, tout le reste est optionnel.
    """

    CHEMIN_CSV          = "journal/trades.csv"
    CHEMIN_CSV_REJETES  = "journal/rejected_signals.csv"
    DOSSIER_CHARTS      = "journal/charts"
    DOSSIER_RAPPORTS    = "journal/weekly_reports"
    DOSSIER_SNAPSHOTS   = "journal/snapshots"

    def __init__(
        self,
        connecteur=None,
        config=None,
        generateur_graphiques: Optional[GenerateurGraphiques] = None,
        analyseur_performance: Optional[AnalyseurPerformance] = None,
    ) -> None:
        self.connecteur = connecteur
        self.config = config or CONFIG
        self.gen_graphiques = generateur_graphiques
        self.analyseur = analyseur_performance or AnalyseurPerformance(self.config)

        self._entrees: List[EntreeJournal] = []
        self._signaux_rejetes: List[SignalRejete] = []

        self._creer_dossiers()
        self._charger_entrees_existantes()

    # ── Setup ──────────────────────────────────────────────────────────────

    def _creer_dossiers(self) -> None:
        for dossier in [
            self.DOSSIER_CHARTS, self.DOSSIER_RAPPORTS, self.DOSSIER_SNAPSHOTS,
        ]:
            os.makedirs(dossier, exist_ok=True)
        os.makedirs("journal", exist_ok=True)

    def _charger_entrees_existantes(self) -> None:
        """Charge le nombre d'entrées existantes au démarrage (pour le log)."""
        if not os.path.exists(self.CHEMIN_CSV):
            return
        try:
            with open(self.CHEMIN_CSV, "r", encoding="utf-8") as f:
                nb = sum(1 for _ in csv.DictReader(f))
            logger.info(f"Journal de trades chargé : {nb} trades existants")
        except Exception as e:
            logger.warning(f"Impossible de lire le journal existant : {e}")

    # ── Enregistrement d'un trade fermé ───────────────────────────────────

    def enregistrer_trade(
        self,
        trade,                          # TradeGere (trade_manager.py)
        structure=None,                 # StructureMarche (optionnel)
        biais=None,                     # BiaisJournalier (optionnel)
        session_asiatique=None,         # DonneesSessionAsiatique (optionnel)
        niveau_cb: str = "AUCUN",       # Niveau circuit breaker
        addon=None,                     # TradeAddon (optionnel)
        execution_entree=None,          # ExecutionSimulee (optionnel)
        execution_sortie=None,          # ExecutionSimulee (optionnel)
    ) -> Optional[EntreeJournal]:
        """
        Enregistre un trade fermé dans le journal.

        Priorité : CSV d'abord, graphique ensuite.
        Jamais d'exception propagée vers trade_manager.

        Args:
            trade            : TradeGere depuis trade_manager.py.
            structure        : StructureMarche H4 au moment de l'entrée.
            biais            : BiaisJournalier du jour.
            session_asiatique: Données session asiatique (range, BSL, SSL).
            niveau_cb        : Niveau du circuit breaker.
            addon            : TradeAddon pyramiding (ou None).
            execution_entree : ExecutionSimulee du simulateur paper (ou None).
            execution_sortie : ExecutionSimulee de sortie (ou None).

        Returns:
            EntreeJournal créée, ou None si erreur critique.
        """
        try:
            entree = self._construire_entree(
                trade, structure, biais, session_asiatique, niveau_cb,
                addon, execution_entree, execution_sortie
            )

            # 1. CSV en premier (priorité absolue)
            self._ecrire_csv(entree)
            self._entrees.append(entree)

            # 2. Graphique (secondaire — jamais bloquant)
            if self.gen_graphiques is not None:
                chemin_chart = self._generer_graphique(entree, session_asiatique)
                entree.chemin_graphique = chemin_chart

            logger.info(
                f"📓 Trade journalisé [{entree.journal_id}] | "
                f"{entree.direction} | "
                f"R:{entree.total_r_realise:+.2f} | "
                f"${entree.pnl_total_usd:+.2f} | "
                f"Graphique: {entree.chemin_graphique or 'N/A'}"
            )
            return entree

        except Exception as e:
            logger.error(f"Erreur enregistrement trade dans le journal : {e}")
            return None

    # Alias anglais
    def record_trade(
        self,
        trade,
        structure=None,
        daily_bias=None,
        asian_session=None,
        cb_level: str = "AUCUN",
        addon=None,
        slippage_entry=None,
        slippage_exit=None,
    ) -> Optional[EntreeJournal]:
        return self.enregistrer_trade(
            trade, structure, daily_bias, asian_session,
            cb_level, addon, slippage_entry, slippage_exit
        )

    def _construire_entree(
        self, trade, structure, biais, session_asiatique,
        niveau_cb, addon, execution_entree, execution_sortie
    ) -> EntreeJournal:
        """Construit l'EntreeJournal depuis les objets du bot."""
        # Direction
        direction_str = (
            trade.direction.value
            if hasattr(trade.direction, "value")
            else str(trade.direction)
        )

        # Raison de fermeture
        raison_str = (
            trade.raison_fermeture.value
            if trade.raison_fermeture and hasattr(trade.raison_fermeture, "value")
            else str(trade.raison_fermeture or "")
        )

        # Durée
        heure_sortie = getattr(trade, "heure_fermeture", None)
        duree_min = 0
        if heure_sortie and trade.heure_entree:
            duree_min = int(
                (heure_sortie - trade.heure_entree).total_seconds() / 60
            )

        # Prix de sortie (dernier TP atteint ou prix_entree par défaut)
        prix_sortie = trade.prix_entree
        if trade.tp3.atteint and trade.tp3.prix_atteint:
            prix_sortie = trade.tp3.prix_atteint
        elif trade.tp2.atteint and trade.tp2.prix_atteint:
            prix_sortie = trade.tp2.prix_atteint
        elif trade.tp1.atteint and trade.tp1.prix_atteint:
            prix_sortie = trade.tp1.prix_atteint
        elif trade.sl.prix_actuel:
            prix_sortie = trade.sl.prix_actuel

        # Contexte marché
        tendance_h4 = "N/A"
        if structure is not None:
            try:
                tendance_h4 = structure.tendance.value
            except Exception:
                pass

        biais_direction = "N/A"
        force_biais = "N/A"
        if biais is not None:
            try:
                biais_direction = biais.direction.value
                force_biais = biais.force.value
            except Exception:
                pass

        # Session selon l'heure d'entrée
        heure_entree_utc = trade.heure_entree.hour
        if 7 <= heure_entree_utc < 13:
            session = "LONDON"
        elif 13 <= heure_entree_utc < 20:
            session = "NEW_YORK"
        else:
            session = "AUTRE"

        # Range asiatique
        asian_high = None
        asian_low = None
        if session_asiatique is not None:
            try:
                asian_high = float(session_asiatique.haut_session)
                asian_low  = float(session_asiatique.bas_session)
            except Exception:
                pass

        # Add-on
        addon_pnl = 0.0
        addon_r   = 0.0
        avec_addon = addon is not None
        if addon is not None:
            addon_pnl = getattr(addon, "pnl_total_usd", 0.0)
            addon_r   = getattr(addon, "r_total_realise", 0.0)

        # Slippage paper
        slip_entree = getattr(execution_entree, "slippage_pts", 0.0) if execution_entree else 0.0
        slip_sortie = getattr(execution_sortie, "slippage_pts", 0.0) if execution_sortie else 0.0
        qual_exec   = (
            execution_entree.execution_quality.value
            if execution_entree and hasattr(execution_entree, "execution_quality")
            else "N/A"
        )

        return EntreeJournal(
            journal_id=str(uuid.uuid4())[:8],
            id_trade=trade.id_trade,
            ticket_mt5=trade.ticket_mt5,
            symbole=trade.symbole,
            direction=direction_str,
            prix_entree=trade.prix_entree,
            heure_entree=trade.heure_entree,
            prix_sortie=round(prix_sortie, 2),
            heure_sortie=heure_sortie,
            duree_minutes=duree_min,
            lots_initial=trade.lots_initial,
            sl_initial=trade.sl.prix_initial,
            sl_final=trade.sl.prix_actuel,
            tp1=trade.tp1.prix,
            tp2=trade.tp2.prix,
            tp3=trade.tp3.prix,
            raison_fermeture=raison_str,
            tp1_atteint=trade.tp1.atteint,
            tp2_atteint=trade.tp2.atteint,
            tp3_atteint=trade.tp3.atteint,
            total_r_realise=round(trade.r_total_realise or 0.0, 3),
            pnl_total_usd=round(trade.pnl_total_usd or 0.0, 2),
            mfe_r=round(trade.mfe, 3),
            mae_r=round(trade.mae, 3),
            ob_score=trade.ob_score,
            ob_force=trade.ob_force,
            confluences=trade.confluences or [],
            tendance_h4=tendance_h4,
            biais_journalier=biais_direction,
            force_biais=force_biais,
            session_entree=session,
            heure_entree_utc=heure_entree_utc,
            asian_high=asian_high,
            asian_low=asian_low,
            avec_addon=avec_addon,
            addon_pnl_usd=addon_pnl,
            addon_r=addon_r,
            slippage_entree_pts=slip_entree,
            slippage_sortie_pts=slip_sortie,
            qualite_execution=qual_exec,
        )

    # ── Signaux rejetés ────────────────────────────────────────────────────

    def enregistrer_signal_rejete(
        self,
        direction: str,
        rejete_par: str,
        raison: str,
        prix_actuel: float,
        structure=None,
        biais=None,
        ob_score: Optional[int] = None,
        ob_force: Optional[str] = None,
    ) -> None:
        """
        Enregistre un signal rejeté par un filtre.

        Méthode volontairement légère (pas de calcul lourd) car appelée
        très fréquemment depuis strategy.py.
        """
        tendance_h4 = "N/A"
        if structure is not None:
            try:
                tendance_h4 = structure.tendance.value
            except Exception:
                pass

        biais_dir = "N/A"
        if biais is not None:
            try:
                biais_dir = biais.direction.value
            except Exception:
                pass

        signal = SignalRejete(
            timestamp_utc=datetime.utcnow(),
            direction=direction,
            rejete_par=rejete_par,
            raison=raison,
            prix_actuel=prix_actuel,
            ob_score=ob_score,
            ob_force=ob_force,
            tendance_h4=tendance_h4,
            biais_journalier=biais_dir,
            heure_utc=datetime.utcnow().hour,
        )

        self._signaux_rejetes.append(signal)
        self._ecrire_csv_rejete(signal)

    # Alias anglais
    def record_rejected_signal(
        self,
        signal_result=None,
        current_price: float = 0.0,
        structure=None,
        daily_bias=None,
        ob_score: Optional[int] = None,
        ob_force: Optional[str] = None,
    ) -> None:
        """
        Alias anglais compatible avec la spec.
        Extrait les champs depuis un SignalTrading ou les paramètres directs.
        """
        if signal_result is not None:
            raison = getattr(signal_result, "raison_rejet", "")
            direction = getattr(signal_result, "direction", "")
            if hasattr(direction, "value"):
                direction = direction.value

            # Extraire le nom du filtre depuis le préfixe "⛔ NEWS:", "⛔ SPREAD:", etc.
            rejete_par = "inconnu"
            if raison.startswith("⛔ NEWS"):
                rejete_par = "news_filter"
            elif raison.startswith("⛔ SPREAD"):
                rejete_par = "spread_filter"
            elif raison.startswith("⛔ BIAIS"):
                rejete_par = "daily_bias"
            elif "Circuit Breaker" in raison or "CB" in raison:
                rejete_par = "circuit_breaker"
            elif "Structure" in raison or "BOS" in raison:
                rejete_par = "structure"
            elif "OB" in raison or "Order Block" in raison:
                rejete_par = "ob_filter"
            elif "RSI" in raison:
                rejete_par = "rsi_filter"
            elif "Volume" in raison:
                rejete_par = "volume_filter"
            elif "session" in raison.lower():
                rejete_par = "session_filter"
            elif "Volatilité" in raison:
                rejete_par = "volatility_filter"
        else:
            direction = ""
            raison = ""
            rejete_par = "inconnu"

        self.enregistrer_signal_rejete(
            direction=direction,
            rejete_par=rejete_par,
            raison=raison,
            prix_actuel=current_price,
            structure=structure,
            biais=daily_bias,
            ob_score=ob_score,
            ob_force=ob_force,
        )

    # ── CSV ────────────────────────────────────────────────────────────────

    _EN_TETES_CSV = [
        "journal_id", "id_trade", "ticket_mt5", "symbole", "direction",
        "prix_entree", "heure_entree", "prix_sortie", "heure_sortie",
        "duree_minutes", "lots_initial",
        "sl_initial", "sl_final", "tp1", "tp2", "tp3",
        "raison_fermeture",
        "tp1_atteint", "tp2_atteint", "tp3_atteint",
        "total_r_realise", "pnl_total_usd",
        "mfe_r", "mae_r",
        "ob_score", "ob_force", "confluences",
        "tendance_h4", "biais_journalier", "force_biais",
        "session_entree", "heure_entree_utc",
        "asian_high", "asian_low",
        "avec_addon", "addon_pnl_usd", "addon_r",
        "slippage_entree_pts", "slippage_sortie_pts",
        "qualite_execution", "chemin_graphique",
    ]

    def _ecrire_csv(self, entree: EntreeJournal) -> None:
        """Écrit une ligne dans trades.csv. Crée l'en-tête si nécessaire."""
        fichier_existe = os.path.exists(self.CHEMIN_CSV)
        with open(self.CHEMIN_CSV, "a", newline="", encoding="utf-8-sig") as f:
            writer = csv.writer(f)
            if not fichier_existe:
                writer.writerow(self._EN_TETES_CSV)
            writer.writerow([
                entree.journal_id, entree.id_trade, entree.ticket_mt5,
                entree.symbole, entree.direction,
                entree.prix_entree,
                entree.heure_entree.isoformat(),
                entree.prix_sortie,
                entree.heure_sortie.isoformat() if entree.heure_sortie else "",
                entree.duree_minutes, entree.lots_initial,
                entree.sl_initial, entree.sl_final,
                entree.tp1, entree.tp2, entree.tp3,
                entree.raison_fermeture,
                entree.tp1_atteint, entree.tp2_atteint, entree.tp3_atteint,
                entree.total_r_realise, entree.pnl_total_usd,
                entree.mfe_r, entree.mae_r,
                entree.ob_score, entree.ob_force,
                "|".join(entree.confluences),
                entree.tendance_h4, entree.biais_journalier, entree.force_biais,
                entree.session_entree, entree.heure_entree_utc,
                entree.asian_high or "", entree.asian_low or "",
                entree.avec_addon, entree.addon_pnl_usd, entree.addon_r,
                entree.slippage_entree_pts, entree.slippage_sortie_pts,
                entree.qualite_execution,
                entree.chemin_graphique or "",
            ])

    _EN_TETES_CSV_REJETES = [
        "timestamp_utc", "direction", "rejete_par", "raison",
        "prix_actuel", "ob_score", "ob_force",
        "tendance_h4", "biais_journalier", "heure_utc",
    ]

    def _ecrire_csv_rejete(self, signal: SignalRejete) -> None:
        """Écrit une ligne dans rejected_signals.csv."""
        fichier_existe = os.path.exists(self.CHEMIN_CSV_REJETES)
        with open(self.CHEMIN_CSV_REJETES, "a", newline="",
                  encoding="utf-8-sig") as f:
            writer = csv.writer(f)
            if not fichier_existe:
                writer.writerow(self._EN_TETES_CSV_REJETES)
            writer.writerow([
                signal.timestamp_utc.isoformat(),
                signal.direction, signal.rejete_par, signal.raison,
                signal.prix_actuel, signal.ob_score or "", signal.ob_force or "",
                signal.tendance_h4, signal.biais_journalier, signal.heure_utc,
            ])

    # ── Graphique ──────────────────────────────────────────────────────────

    def _generer_graphique(
        self, entree: EntreeJournal, session_asiatique
    ) -> Optional[str]:
        """Génère le graphique du trade — jamais bloquant."""
        if self.gen_graphiques is None:
            return None
        try:
            nom_fichier = (
                f"trade_{entree.id_trade}_"
                f"{entree.heure_entree.strftime('%Y%m%d_%H%M')}.png"
            )
            chemin = os.path.join(self.DOSSIER_CHARTS, nom_fichier)
            return self.gen_graphiques.generer_graphique_trade(
                entree, session_asiatique, chemin
            ) or None
        except Exception as e:
            logger.error(f"Erreur génération graphique journal : {e}")
            return None

    # ── Rapport hebdomadaire ───────────────────────────────────────────────

    def generer_rapport_hebdomadaire(
        self,
        semaine_debut: datetime,
        semaine_fin: datetime,
    ) -> str:
        """
        Génère le rapport HTML hebdomadaire.
        Appelé par le scheduler chaque lundi à 00h05 UTC.

        Returns:
            Chemin du fichier HTML généré, ou chaîne vide si erreur.
        """
        entrees_semaine = [
            e for e in self._entrees
            if semaine_debut <= e.heure_entree <= semaine_fin
        ]
        rejetes_semaine = [
            s for s in self._signaux_rejetes
            if semaine_debut <= s.timestamp_utc <= semaine_fin
        ]

        if not entrees_semaine:
            logger.info("Aucun trade cette semaine — rapport non généré")
            return ""

        try:
            rapport = self.analyseur.calculer_rapport_hebdomadaire(
                entrees_semaine, rejetes_semaine, semaine_debut, semaine_fin
            )
            semaine_str = semaine_debut.strftime("%Y-%m-%d")

            # Graphiques pour le rapport
            chemin_equity = ""
            chemin_distrib = ""
            if self.gen_graphiques is not None:
                chemin_equity = self.gen_graphiques.generer_courbe_equity(
                    entrees_semaine, semaine_str, self.DOSSIER_CHARTS
                )
                chemin_distrib = self.gen_graphiques.generer_distribution_r(
                    rapport.distribution_r, semaine_str, self.DOSSIER_CHARTS
                )

            html_path = self._rendre_rapport_html(
                rapport, entrees_semaine, chemin_equity, chemin_distrib, semaine_str
            )
            logger.success(f"📊 Rapport hebdomadaire généré : {html_path}")
            return html_path
        except Exception as e:
            logger.error(f"Erreur génération rapport hebdomadaire : {e}")
            return ""

    # Alias anglais
    def generate_weekly_report(
        self,
        week_start: datetime,
        week_end: datetime,
    ) -> str:
        return self.generer_rapport_hebdomadaire(week_start, week_end)

    def _rendre_rapport_html(
        self,
        rapport,
        entrees: List[EntreeJournal],
        chemin_equity: str,
        chemin_distrib: str,
        semaine_str: str,
    ) -> str:
        """Génère le rapport HTML auto-suffisant (images en base64)."""
        nom_fichier = f"weekly_report_{semaine_str}.html"
        chemin_sortie = os.path.join(self.DOSSIER_RAPPORTS, nom_fichier)

        equity_b64  = self._image_en_base64(chemin_equity)
        distrib_b64 = self._image_en_base64(chemin_distrib)

        # Lignes de trades
        lignes_trades = "\n".join(
            f"<tr>"
            f"<td>{e.id_trade}</td>"
            f"<td>{'📈' if e.direction=='LONG' else '📉'} {e.direction}</td>"
            f"<td>{e.prix_entree:.2f} @ {e.heure_entree.strftime('%d/%m %H:%M')}</td>"
            f"<td class=\"{'winner' if e.total_r_realise>0 else 'loser'}\">"
            f"{e.total_r_realise:+.2f}R</td>"
            f"<td class=\"{'winner' if e.pnl_total_usd>0 else 'loser'}\">"
            f"${e.pnl_total_usd:+.2f}</td>"
            f"<td>{e.raison_fermeture}</td>"
            f"<td>{e.ob_score}/100 [{e.ob_force}]</td>"
            f"<td>{e.biais_journalier}</td>"
            f"</tr>"
            for e in sorted(entrees, key=lambda x: x.heure_entree)
        )

        # Lignes rejets
        total_rejets = sum(rapport.nb_rejets_par_filtre.values())
        lignes_rejets = "\n".join(
            f"<tr><td>{f}</td><td>{c}</td>"
            f"<td>{c/total_rejets*100:.1f}%</td></tr>"
            for f, c in sorted(
                rapport.nb_rejets_par_filtre.items(),
                key=lambda x: x[1], reverse=True
            )
        ) if total_rejets > 0 else "<tr><td colspan='3'>Aucun rejet enregistré</td></tr>"

        # Lignes par heure
        lignes_heures = "\n".join(
            f"<tr><td>{h}h UTC</td>"
            f"<td class=\"{'winner' if r>0 else 'loser'}\">{r:+.2f}R</td>"
            f"<td>{'✅ Favorable' if r>0 else '⚠️ À éviter'}</td></tr>"
            for h, r in sorted(rapport.pnl_par_heure.items())
        )

        # Recommandations
        reco_html = "\n".join(
            f'<div class="recommendation">{r}</div>'
            for r in rapport.recommandations
        ) or "<p style='color:#8B949E'>Aucune recommandation — métriques satisfaisantes.</p>"

        pf_color = "positive" if rapport.profit_factor >= 1.5 else "negative"
        wr_color = "positive" if rapport.win_rate_pct >= 45 else "negative"
        sr_color = "positive" if rapport.sharpe_ratio >= 1.0 else "negative"
        dd_color = "negative" if rapport.max_drawdown_pct > 5 else "positive"
        r_color  = "positive" if rapport.r_moyen_par_trade > 0 else "negative"
        pnl_color = "positive" if rapport.pnl_total_usd > 0 else "negative"
        pnl_signe = "+" if rapport.pnl_total_usd >= 0 else ""
        r_signe   = "+" if rapport.r_moyen_par_trade >= 0 else ""

        html = f"""<!DOCTYPE html>
<html lang="fr">
<head>
<meta charset="UTF-8">
<title>Rapport Hebdomadaire SMC Bot — {semaine_str}</title>
<style>
  body {{ background:#0D1117; color:#E6EDF3;
         font-family:'Segoe UI',sans-serif; margin:0; padding:20px; }}
  h1   {{ color:#F0883E; border-bottom:2px solid #21262D; padding-bottom:10px; }}
  h2   {{ color:#58A6FF; margin-top:30px; }}
  .grid {{ display:grid; grid-template-columns:repeat(4,1fr); gap:15px; margin:20px 0; }}
  .card {{ background:#161B22; border:1px solid #21262D; border-radius:8px;
           padding:15px; text-align:center; }}
  .val  {{ font-size:1.8em; font-weight:bold; }}
  .val.positive {{ color:#26A69A; }}
  .val.negative {{ color:#EF5350; }}
  .lbl  {{ font-size:0.8em; color:#8B949E; margin-top:5px; }}
  table {{ width:100%; border-collapse:collapse; margin:15px 0; }}
  th    {{ background:#21262D; color:#58A6FF; padding:10px; text-align:left; }}
  td    {{ padding:8px 10px; border-bottom:1px solid #21262D; }}
  tr:hover {{ background:#161B22; }}
  .winner {{ color:#26A69A; }}
  .loser  {{ color:#EF5350; }}
  .recommendation {{ background:#161B22; border-left:4px solid #F0883E;
                     padding:10px 15px; margin:8px 0; border-radius:4px; }}
  img {{ max-width:100%; border-radius:8px; margin:10px 0; }}
  footer {{ color:#8B949E; margin-top:40px; font-size:0.8em; }}
</style>
</head>
<body>
<h1>📊 Rapport Hebdomadaire SMC Bot XAUUSD</h1>
<p style="color:#8B949E;">Semaine du {rapport.semaine_debut.strftime('%d/%m/%Y')}
  au {rapport.semaine_fin.strftime('%d/%m/%Y')}</p>

<h2>📈 Métriques Clés</h2>
<div class="grid">
  <div class="card">
    <div class="val {pnl_color}">{pnl_signe}{rapport.pnl_total_usd:.2f}$</div>
    <div class="lbl">P&L Total</div>
  </div>
  <div class="card">
    <div class="val {wr_color}">{rapport.win_rate_pct:.1f}%</div>
    <div class="lbl">Win Rate ({rapport.trades_gagnants}G/{rapport.trades_perdants}P)</div>
  </div>
  <div class="card">
    <div class="val {pf_color}">{rapport.profit_factor:.2f}</div>
    <div class="lbl">Profit Factor</div>
  </div>
  <div class="card">
    <div class="val {sr_color}">{rapport.sharpe_ratio:.2f}</div>
    <div class="lbl">Sharpe Ratio</div>
  </div>
  <div class="card">
    <div class="val">{rapport.total_trades}</div>
    <div class="lbl">Trades totaux</div>
  </div>
  <div class="card">
    <div class="val {r_color}">{r_signe}{rapport.r_moyen_par_trade:.2f}R</div>
    <div class="lbl">R moyen / trade</div>
  </div>
  <div class="card">
    <div class="val {dd_color}">{rapport.max_drawdown_pct:.1f}%</div>
    <div class="lbl">Max Drawdown</div>
  </div>
  <div class="card">
    <div class="val">{rapport.max_pertes_consecutives}</div>
    <div class="lbl">Pertes consécutives max</div>
  </div>
</div>

{"<h2>📉 Equity Curve</h2><img src='data:image/png;base64," + equity_b64 + "' alt='Equity Curve'>" if equity_b64 else ""}
{"<h2>📊 Distribution R:R</h2><img src='data:image/png;base64," + distrib_b64 + "' alt='Distribution'>" if distrib_b64 else ""}

<h2>🔍 Signaux Rejetés par Filtre</h2>
<p>Filtre principal : <strong>{rapport.filtre_principal_rejet}</strong></p>
<table>
  <tr><th>Filtre</th><th>Rejets</th><th>% du total</th></tr>
  {lignes_rejets}
</table>

<h2>⏰ Performance par Heure UTC</h2>
<table>
  <tr><th>Heure UTC</th><th>R cumulé</th><th>Appréciation</th></tr>
  {lignes_heures}
</table>

<h2>📋 Détail des Trades</h2>
<table>
  <tr><th>Trade</th><th>Dir.</th><th>Entrée</th>
      <th>R réalisé</th><th>P&L</th><th>Raison</th>
      <th>OB Score</th><th>Bias</th></tr>
  {lignes_trades}
</table>

<h2>💡 Recommandations</h2>
{reco_html}

<footer>
  Généré par SMC Trading Bot —
  {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}
</footer>
</body>
</html>"""

        with open(chemin_sortie, "w", encoding="utf-8") as f:
            f.write(html)

        return chemin_sortie

    def _image_en_base64(self, chemin: str) -> str:
        """Convertit une image PNG en base64 pour intégration HTML."""
        if not chemin or not os.path.exists(chemin):
            return ""
        try:
            with open(chemin, "rb") as f:
                return base64.b64encode(f.read()).decode("utf-8")
        except Exception:
            return ""

    # ── Accesseurs publics ─────────────────────────────────────────────────

    def get_stats_journal(self) -> dict:
        """Stats rapides pour le dashboard."""
        nb = len(self._entrees)
        nb_rejetes = len(self._signaux_rejetes)
        if nb == 0:
            return {
                "nb_trades": 0,
                "nb_signaux_rejetes": nb_rejetes,
                "dernier_trade": None,
            }
        dernier = self._entrees[-1]
        return {
            "nb_trades": nb,
            "nb_signaux_rejetes": nb_rejetes,
            "dernier_trade": dernier.id_trade,
            "dernier_r": dernier.total_r_realise,
            "dernier_pnl": dernier.pnl_total_usd,
        }


# Alias anglais
TradeJournal = JournalTrades
