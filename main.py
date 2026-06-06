"""
main.py — Point d'entrée du bot SMC XAUUSD.
Modes : live (trading réel), paper (simulation sans ordres), backtest.

Usage :
    python main.py --mode live
    python main.py --mode paper
    python main.py --mode backtest --from 2023-01-01 --to 2024-12-31
"""

import argparse
import signal
import sys
import time
import traceback
from datetime import datetime, timezone
from typing import Optional

import MetaTrader5 as mt5
from loguru import logger

from config import CONFIG
from logger_config import configurer_logger
from mt5_connector import ConnecteurMT5
from strategy import StrategieSMC, DirectionSignal
from risk_manager import GestionnaireRisque
from trade_manager import GestionnairePositions
from dashboard import Dashboard
from notifier import NotificateurTelegram
from backtest import Backtester


# ── Gestion des signaux systèmes (Ctrl+C, SIGTERM) ────────────────────────
_arret_demande = False

def _gestionnaire_signal(signum, frame):
    global _arret_demande
    _arret_demande = True
    logger.warning(f"Signal {signum} reçu — arrêt propre en cours...")


signal.signal(signal.SIGINT, _gestionnaire_signal)
signal.signal(signal.SIGTERM, _gestionnaire_signal)


# ── Helpers ────────────────────────────────────────────────────────────────

def est_en_session_active(heure_utc: int) -> bool:
    """
    Vérifie si l'heure actuelle est dans une session de trading autorisée.
    Interdit les 30 minutes avant/après la clôture journalière XAUUSD.

    Args:
        heure_utc: Heure UTC actuelle.

    Returns:
        True si le trading est autorisé.
    """
    # Fenêtre de clôture journalière interdite
    if CONFIG.CLOTURE_JOURNALIERE_DEBUT_UTC <= heure_utc <= CONFIG.CLOTURE_JOURNALIERE_FIN_UTC:
        return False

    return CONFIG.SESSION_LONDON_OUVERTURE <= heure_utc < CONFIG.SESSION_FERMETURE


def detecter_nouvelle_bougie_m5(dernier_timestamp: Optional[object], df_m5) -> bool:
    """
    Détecte si une nouvelle bougie M5 s'est fermée depuis la dernière vérification.

    Args:
        dernier_timestamp: Timestamp de la dernière bougie M5 connue.
        df_m5: DataFrame M5 courant.

    Returns:
        True si une nouvelle bougie M5 est disponible.
    """
    if df_m5 is None or len(df_m5) == 0:
        return False
    nouveau_ts = df_m5.index[-2]  # Avant-dernière = dernière bougie fermée
    return dernier_timestamp is None or nouveau_ts != dernier_timestamp


# ── Mode Live / Paper ─────────────────────────────────────────────────────

def boucle_principale(mode: str) -> None:
    """
    Boucle principale du bot (live et paper).
    Tourne toutes les CONFIG.INTERVALLE_BOUCLE_SECONDES secondes.

    Args:
        mode: "live" ou "paper".
    """
    global _arret_demande

    # ── Initialisation ──────────────────────────────────────────────────
    configurer_logger()
    logger.info(f"{'='*60}")
    logger.info(f"SMC Trading Bot démarré — MODE: {mode.upper()}")
    logger.info(f"Actif: {CONFIG.SYMBOLE} | Risque: {CONFIG.RISQUE_PAR_TRADE_PCT}%/trade")
    logger.info(f"{'='*60}")

    connecteur = ConnecteurMT5()
    if mode == "live":
        connecteur.connecter()
    else:
        # Paper mode : connexion MT5 pour les données uniquement (pas d'ordres envoyés)
        try:
            connecteur.connecter()
        except ConnectionError:
            logger.warning("Paper mode : MT5 non disponible, simulation en mode dégradé")

    gestionnaire_risque = GestionnaireRisque()
    gestionnaire_positions = GestionnairePositions(connecteur)
    strategie = StrategieSMC()
    dashboard = Dashboard(mode=mode)
    notifier = NotificateurTelegram()

    # Initialiser la session
    info_compte = connecteur.get_info_compte()
    if info_compte:
        gestionnaire_risque.initialiser_session(info_compte["balance"])
        dashboard.mettre_a_jour(
            balance=info_compte["balance"],
            equity=info_compte["equity"],
            devise=info_compte["devise"],
        )

    dashboard.demarrer()

    dernier_timestamp_m5 = None
    heure_debut_journee = datetime.now(timezone.utc).date()

    try:
        while not _arret_demande:
            debut_iteration = time.time()

            # ── 1. Vérifier la connexion MT5 ─────────────────────────────
            if not connecteur.verifier_connexion():
                logger.error("Connexion MT5 perdue — attente 30s avant retry")
                time.sleep(30)
                continue

            # ── 2. Réinitialiser la balance journalière à minuit ──────────
            aujourd_hui = datetime.now(timezone.utc).date()
            if aujourd_hui != heure_debut_journee:
                info = connecteur.get_info_compte()
                if info:
                    gestionnaire_risque.initialiser_session(info["balance"])
                heure_debut_journee = aujourd_hui
                logger.info("Nouvelle journée — balance journalière réinitialisée")

            # ── 3. Vérifier les règles de risque globales ─────────────────
            info_compte = connecteur.get_info_compte()
            if info_compte:
                equity = info_compte["equity"]
                balance = info_compte["balance"]

                dashboard.mettre_a_jour(
                    balance=balance,
                    equity=equity,
                    profit_latent=equity - balance,
                )

                if not gestionnaire_risque.trading_autorise(equity):
                    resume_risque = gestionnaire_risque.get_resume()
                    dashboard.mettre_a_jour(
                        drawdown_journalier_pct=resume_risque["drawdown_journalier_pct"],
                        drawdown_total_pct=resume_risque["drawdown_total_pct"],
                        circuit_breaker=resume_risque["circuit_breaker_actif"],
                        bot_arrete=resume_risque["bot_arrete"],
                        signal_actuel="BOT SUSPENDU",
                    )
                    if resume_risque["bot_arrete"]:
                        notifier.alerte_arret_urgence(resume_risque["drawdown_total_pct"])
                        logger.critical("Bot arrêté définitivement — drawdown total dépassé")
                        break
                    time.sleep(CONFIG.INTERVALLE_BOUCLE_SECONDES)
                    continue

            # ── 4. Vérifier la session de trading ─────────────────────────
            maintenant = datetime.now(timezone.utc)
            heure_utc = maintenant.hour
            en_session = est_en_session_active(heure_utc)

            # ── 5. Récupérer les données OHLCV ────────────────────────────
            try:
                df_h4 = connecteur.get_ohlcv(CONFIG.SYMBOLE, CONFIG.TIMEFRAME_HTF, CONFIG.BOUGIES_HTF)
                df_m15 = connecteur.get_ohlcv(CONFIG.SYMBOLE, CONFIG.TIMEFRAME_LTF, CONFIG.BOUGIES_LTF)
                df_m5 = connecteur.get_ohlcv(CONFIG.SYMBOLE, CONFIG.TIMEFRAME_ENTREE, CONFIG.BOUGIES_ENTREE)
            except ValueError as e:
                logger.error(f"Erreur récupération données: {e}")
                time.sleep(CONFIG.INTERVALLE_BOUCLE_SECONDES)
                continue

            # ── 6. Vérifier nouvelle bougie M5 ────────────────────────────
            nouvelle_bougie = detecter_nouvelle_bougie_m5(dernier_timestamp_m5, df_m5)
            if not nouvelle_bougie and gestionnaire_positions.a_position_ouverte():
                # Pas de nouvelle bougie mais position ouverte → gérer le trailing
                gestionnaire_positions.gerer_positions(df_m15)

            if not nouvelle_bougie and not gestionnaire_positions.a_position_ouverte():
                time.sleep(min(30, CONFIG.INTERVALLE_BOUCLE_SECONDES))
                continue

            if nouvelle_bougie:
                dernier_timestamp_m5 = df_m5.index[-2]

            # ── 7. Gérer les positions ouvertes ───────────────────────────
            tickets_fermes = gestionnaire_positions.gerer_positions(df_m15)

            # Enregistrer les résultats des positions fermées
            for ticket in tickets_fermes:
                positions_fermees = connecteur.get_historique_positions(
                    int(maintenant.timestamp()) - 3600
                )
                for pos_fermee in positions_fermees:
                    if pos_fermee["ticket"] == ticket:
                        gestionnaire_risque.enregistrer_resultat_trade(pos_fermee["profit"])
                        notifier.alerte_trade_ferme(
                            "?", CONFIG.SYMBOLE,
                            pos_fermee["profit"],
                            pos_fermee["profit"] / max(abs(pos_fermee["profit"]), 1),
                        )

            # Vérifier invalidations
            for ticket, etat_pos in list(gestionnaire_positions.positions.items()):
                invalide, raison = strategie.position_invalidee(
                    df_h4, df_m15, etat_pos.direction, etat_pos.zone_reference
                )
                if invalide:
                    logger.warning(f"Invalidation position {ticket}: {raison}")
                    gestionnaire_positions.fermer_position_invalidation(ticket, raison)

            # ── 8. Évaluer un nouveau setup si aucune position ouverte ─────
            if (
                not gestionnaire_positions.a_position_ouverte(CONFIG.SYMBOLE)
                and gestionnaire_positions.a_position_ouverte() is False
                and en_session
            ):
                signal = strategie.evaluer(df_h4, df_m15, df_m5, heure_utc, en_session)

                if signal.valide and signal.direction != DirectionSignal.AUCUN:
                    # Calcul du SL/TP
                    sl, tp1, tp2, tp3, sl_distance = gestionnaire_risque.calculer_sl_tp(
                        prix_entree=signal.prix_entree_suggere,
                        prix_ob_bas=signal.zone_reference.prix_bas,
                        prix_ob_haut=signal.zone_reference.prix_haut,
                        atr_m15=signal.atr_m15,
                        direction=signal.direction.value,
                    )

                    if sl is not None:
                        # Calculer le lot size
                        info_compte_frais = connecteur.get_info_compte()
                        lot = gestionnaire_risque.calculer_lot_size(
                            capital=info_compte_frais["balance"],
                            risque_pct=CONFIG.RISQUE_PAR_TRADE_PCT,
                            sl_points=sl_distance,
                            symbole=CONFIG.SYMBOLE,
                        )

                        if lot > 0:
                            type_ordre = (
                                mt5.ORDER_TYPE_BUY
                                if signal.direction == DirectionSignal.LONG
                                else mt5.ORDER_TYPE_SELL
                            )

                            if mode == "live":
                                # Placer l'ordre réel
                                resultat = connecteur.placer_ordre(
                                    symbole=CONFIG.SYMBOLE,
                                    type_ordre=type_ordre,
                                    volume=lot,
                                    prix=signal.prix_entree_suggere,
                                    sl=sl,
                                    tp=tp2,
                                    commentaire=f"SMC_{signal.direction.value}",
                                )
                                if resultat:
                                    ticket = resultat.get("order", 0)
                                    gestionnaire_positions.enregistrer_position(
                                        ticket=ticket,
                                        symbole=CONFIG.SYMBOLE,
                                        direction=signal.direction.value,
                                        volume=lot,
                                        prix_entree=signal.prix_entree_suggere,
                                        sl=sl,
                                        tp1=tp1,
                                        tp2=tp2,
                                        tp3=tp3 or tp2,
                                        sl_distance=sl_distance,
                                        zone_reference=signal.zone_reference,
                                    )
                                    notifier.alerte_trade_ouvert(
                                        signal.direction.value, CONFIG.SYMBOLE,
                                        lot, signal.prix_entree_suggere, sl, tp2
                                    )
                            else:
                                # Paper mode : simuler sans envoyer d'ordre
                                logger.info(
                                    f"[PAPER] Signal {signal.direction.value} | "
                                    f"Lot: {lot} | Entrée: {signal.prix_entree_suggere:.5f} | "
                                    f"SL: {sl:.5f} | TP2: {tp2:.5f}"
                                )

                dashboard.mettre_a_jour(signal_actuel=signal.raison_rejet or f"SIGNAL {signal.direction.value}")

            # ── 9. Mise à jour du dashboard ────────────────────────────────
            from structure_analyzer import AnalyseurStructure
            try:
                analyseur = AnalyseurStructure()
                analyse = analyseur.analyser(df_h4)
                derniere_cassure = analyse.derniere_cassure
                texte_bos = (
                    f"{derniere_cassure.type.value} @ "
                    f"{derniere_cassure.timestamp.strftime('%m-%d %H:%M')}"
                    if derniere_cassure else "Aucun"
                )
                dashboard.mettre_a_jour(
                    tendance_h4=analyse.tendance.value,
                    dernier_bos=texte_bos,
                )
            except Exception:
                pass

            resume_risque = gestionnaire_risque.get_resume()
            dashboard.mettre_a_jour(
                drawdown_journalier_pct=resume_risque["drawdown_journalier_pct"],
                drawdown_total_pct=resume_risque["drawdown_total_pct"],
                circuit_breaker=resume_risque["circuit_breaker_actif"],
                positions=gestionnaire_positions.get_resume_positions(),
                total_trades=resume_risque["nb_trades"],
                win_rate=resume_risque["win_rate"],
            )

            # ── 10. Respecter l'intervalle de la boucle ───────────────────
            duree_iteration = time.time() - debut_iteration
            attente = max(0, CONFIG.INTERVALLE_BOUCLE_SECONDES - duree_iteration)
            if attente > 0:
                time.sleep(attente)

    except Exception as e:
        logger.critical(f"Exception non catchée: {e}\n{traceback.format_exc()}")
        notifier.alerte_erreur(str(e))
    finally:
        dashboard.arreter()
        connecteur.deconnecter()
        logger.info("Bot arrêté proprement")


# ── Mode Backtest ─────────────────────────────────────────────────────────

def mode_backtest(date_debut: str, date_fin: str) -> None:
    """
    Lance le backtest vectorisé et génère les rapports.

    Args:
        date_debut: Date de début (YYYY-MM-DD).
        date_fin: Date de fin (YYYY-MM-DD).
    """
    configurer_logger()
    logger.info(f"Lancement backtest {CONFIG.SYMBOLE} | {date_debut} → {date_fin}")

    backtester = Backtester()
    trades = backtester.executer(date_debut, date_fin)
    metriques = backtester.calculer_metriques(trades, CONFIG.BACKTEST_CAPITAL_INITIAL)
    backtester.generer_rapport(trades, metriques, date_debut, date_fin)


# ── CLI ───────────────────────────────────────────────────────────────────

def main() -> None:
    """Point d'entrée CLI avec parsing des arguments."""
    parser = argparse.ArgumentParser(
        description="SMC Trading Bot — XAUUSD",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Exemples :
  python main.py --mode live
  python main.py --mode paper
  python main.py --mode backtest --from 2023-01-01 --to 2024-12-31
        """
    )
    parser.add_argument(
        "--mode",
        choices=["live", "paper", "backtest"],
        default="paper",
        help="Mode d'exécution (défaut: paper)"
    )
    parser.add_argument(
        "--from",
        dest="date_debut",
        default=CONFIG.BACKTEST_DATE_DEBUT,
        help="Date de début du backtest (YYYY-MM-DD)"
    )
    parser.add_argument(
        "--to",
        dest="date_fin",
        default=CONFIG.BACKTEST_DATE_FIN,
        help="Date de fin du backtest (YYYY-MM-DD)"
    )

    args = parser.parse_args()

    if args.mode == "backtest":
        mode_backtest(args.date_debut, args.date_fin)
    else:
        boucle_principale(args.mode)


if __name__ == "__main__":
    main()
