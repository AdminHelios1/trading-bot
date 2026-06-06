"""
mt5_connector.py — Interface complète avec MetaTrader 5.
Gère la connexion, la lecture des données OHLCV et l'exécution des ordres.
"""

import time
from typing import Optional, Dict, List, Any
import pandas as pd
import MetaTrader5 as mt5
from dotenv import load_dotenv
import os
from loguru import logger

from config import CONFIG


class ConnecteurMT5:
    """Wrapper complet pour l'API MetaTrader 5."""

    def __init__(self) -> None:
        load_dotenv()
        self.login: int = int(os.getenv("MT5_LOGIN", "0"))
        self.password: str = os.getenv("MT5_PASSWORD", "")
        self.server: str = os.getenv("MT5_SERVER", "")
        self.connecte: bool = False

    # ── Connexion ──────────────────────────────────────────────────────────

    def connecter(self, max_tentatives: int = CONFIG.MAX_TENTATIVES_CONNEXION) -> bool:
        """
        Initialise la connexion MT5 avec retry exponentiel.

        Args:
            max_tentatives: Nombre maximum de tentatives avant exception.

        Returns:
            True si connexion réussie.

        Raises:
            ConnectionError: Si toutes les tentatives échouent.
        """
        for tentative in range(1, max_tentatives + 1):
            if mt5.initialize(
                login=self.login,
                password=self.password,
                server=self.server,
            ):
                info = mt5.account_info()
                logger.success(
                    f"MT5 connecté | Compte: {info.login} | "
                    f"Broker: {info.company} | "
                    f"Balance: {info.balance:.2f} {info.currency} | "
                    f"Levier: 1:{info.leverage}"
                )
                self.connecte = True
                return True

            erreur = mt5.last_error()
            logger.warning(
                f"Tentative {tentative}/{max_tentatives} échouée — "
                f"Erreur MT5: {erreur}"
            )
            time.sleep(5 * tentative)  # Backoff exponentiel

        raise ConnectionError(
            f"Impossible de se connecter à MT5 après {max_tentatives} tentatives. "
            f"Dernière erreur: {mt5.last_error()}"
        )

    def deconnecter(self) -> None:
        """Ferme proprement la connexion MT5."""
        mt5.shutdown()
        self.connecte = False
        logger.info("Déconnexion MT5 effectuée")

    def verifier_connexion(self) -> bool:
        """Vérifie si la connexion est active et tente une reconnexion si nécessaire."""
        if not self.connecte or mt5.account_info() is None:
            logger.warning("Connexion MT5 perdue — tentative de reconnexion...")
            try:
                return self.connecter()
            except ConnectionError as e:
                logger.error(f"Reconnexion échouée: {e}")
                return False
        return True

    # ── Données de marché ──────────────────────────────────────────────────

    def get_ohlcv(
        self,
        symbole: str,
        timeframe: int,
        n_bougies: int,
    ) -> pd.DataFrame:
        """
        Récupère les données OHLCV depuis MT5.

        Args:
            symbole: Symbole MT5 (ex: "XAUUSD").
            timeframe: Constante MT5 (ex: mt5.TIMEFRAME_H4).
            n_bougies: Nombre de bougies à récupérer.

        Returns:
            DataFrame avec colonnes [open, high, low, close, tick_volume, spread].
            Index = datetime UTC.

        Raises:
            ValueError: Si les données ne peuvent pas être récupérées.
        """
        rates = mt5.copy_rates_from_pos(symbole, timeframe, 0, n_bougies)
        if rates is None or len(rates) == 0:
            raise ValueError(
                f"Impossible de récupérer {n_bougies} bougies "
                f"{symbole} TF={timeframe} — {mt5.last_error()}"
            )
        df = pd.DataFrame(rates)
        df["time"] = pd.to_datetime(df["time"], unit="s", utc=True)
        df = df.set_index("time")
        df = df.rename(columns={"tick_volume": "volume"})
        return df[["open", "high", "low", "close", "volume", "spread"]]

    def get_ohlcv_plage(
        self,
        symbole: str,
        timeframe: int,
        date_debut: "datetime",
        date_fin: "datetime",
    ) -> pd.DataFrame:
        """
        Récupère les données OHLCV sur une plage de dates (pour le backtest).

        Args:
            symbole: Symbole MT5.
            timeframe: Timeframe MT5.
            date_debut: Date de début (datetime UTC-aware).
            date_fin: Date de fin (datetime UTC-aware).

        Returns:
            DataFrame OHLCV.
        """
        rates = mt5.copy_rates_range(symbole, timeframe, date_debut, date_fin)
        if rates is None or len(rates) == 0:
            raise ValueError(
                f"Aucune donnée pour {symbole} "
                f"du {date_debut} au {date_fin} — {mt5.last_error()}"
            )
        df = pd.DataFrame(rates)
        df["time"] = pd.to_datetime(df["time"], unit="s", utc=True)
        df = df.set_index("time")
        df = df.rename(columns={"tick_volume": "volume"})
        return df[["open", "high", "low", "close", "volume", "spread"]]

    def get_tick(self, symbole: str) -> Optional[Dict]:
        """Retourne le tick courant (bid/ask/spread) pour un symbole."""
        tick = mt5.symbol_info_tick(symbole)
        if tick is None:
            return None
        return {
            "bid": tick.bid,
            "ask": tick.ask,
            "spread": round((tick.ask - tick.bid) / mt5.symbol_info(symbole).point, 1),
            "time": pd.Timestamp(tick.time, unit="s", tz="UTC"),
        }

    def get_info_symbole(self, symbole: str) -> Optional[Any]:
        """Retourne les informations techniques du symbole (point, volume_min, etc.)."""
        info = mt5.symbol_info(symbole)
        if info is None:
            logger.error(f"Symbole introuvable: {symbole}")
        return info

    # ── Compte ────────────────────────────────────────────────────────────

    def get_info_compte(self) -> Optional[Dict]:
        """
        Retourne les informations clés du compte.

        Returns:
            Dict avec balance, equity, margin, profit, currency.
        """
        info = mt5.account_info()
        if info is None:
            return None
        return {
            "balance": info.balance,
            "equity": info.equity,
            "margin": info.margin,
            "margin_libre": info.margin_free,
            "profit": info.profit,
            "devise": info.currency,
            "login": info.login,
        }

    def calculer_drawdown_actuel(self, balance_depart_journee: float) -> float:
        """
        Calcule le drawdown journalier en pourcentage.

        Args:
            balance_depart_journee: Balance au début de la session.

        Returns:
            Drawdown journalier en % (valeur positive = perte).
        """
        info = self.get_info_compte()
        if info is None:
            return 0.0
        equity = info["equity"]
        if balance_depart_journee <= 0:
            return 0.0
        drawdown = (balance_depart_journee - equity) / balance_depart_journee * 100
        return max(0.0, drawdown)

    # ── Positions ─────────────────────────────────────────────────────────

    def get_positions_ouvertes(self, symbole: Optional[str] = None) -> List[Dict]:
        """
        Retourne les positions ouvertes filtrées par magic number.

        Args:
            symbole: Filtrer par symbole (None = toutes).

        Returns:
            Liste de dicts décrivant chaque position.
        """
        if symbole:
            positions = mt5.positions_get(symbol=symbole)
        else:
            positions = mt5.positions_get()

        if positions is None:
            return []

        resultat = []
        for pos in positions:
            if pos.magic == CONFIG.MAGIC_NUMBER:
                resultat.append({
                    "ticket": pos.ticket,
                    "symbole": pos.symbol,
                    "type": "LONG" if pos.type == mt5.ORDER_TYPE_BUY else "SHORT",
                    "volume": pos.volume,
                    "prix_entree": pos.price_open,
                    "sl": pos.sl,
                    "tp": pos.tp,
                    "profit": pos.profit,
                    "temps_ouverture": pd.Timestamp(pos.time, unit="s", tz="UTC"),
                    "commentaire": pos.comment,
                })
        return resultat

    # ── Ordres ────────────────────────────────────────────────────────────

    def placer_ordre(
        self,
        symbole: str,
        type_ordre: int,
        volume: float,
        prix: float,
        sl: float,
        tp: float,
        commentaire: str = "SMC_BOT",
    ) -> Optional[Dict]:
        """
        Place un ordre marché avec SL et TP.

        Args:
            symbole: Symbole cible.
            type_ordre: mt5.ORDER_TYPE_BUY ou mt5.ORDER_TYPE_SELL.
            volume: Taille du lot.
            prix: Prix d'exécution demandé (bid/ask courant).
            sl: Niveau Stop Loss.
            tp: Niveau Take Profit principal.
            commentaire: Commentaire visible dans MT5.

        Returns:
            Dict du résultat MT5, ou None en cas d'échec.
        """
        requete = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": symbole,
            "volume": volume,
            "type": type_ordre,
            "price": prix,
            "sl": sl,
            "tp": tp,
            "deviation": 10,
            "magic": CONFIG.MAGIC_NUMBER,
            "comment": commentaire,
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_IOC,
        }

        resultat = mt5.order_send(requete)
        if resultat is None or resultat.retcode != mt5.TRADE_RETCODE_DONE:
            code = resultat.retcode if resultat else "N/A"
            msg = resultat.comment if resultat else mt5.last_error()
            logger.error(
                f"Ordre rejeté | {symbole} | Volume: {volume} | "
                f"Code: {code} | Message: {msg}"
            )
            return None

        direction = "LONG" if type_ordre == mt5.ORDER_TYPE_BUY else "SHORT"
        logger.success(
            f"Ordre exécuté | {direction} {volume} lots {symbole} "
            f"@ {resultat.price:.5f} | SL: {sl:.5f} | TP: {tp:.5f} | "
            f"Ticket: {resultat.order}"
        )
        return resultat._asdict()

    def modifier_position(
        self,
        ticket: int,
        nouveau_sl: float,
        nouveau_tp: float,
    ) -> bool:
        """
        Modifie le SL et/ou TP d'une position ouverte.

        Args:
            ticket: Numéro de ticket de la position.
            nouveau_sl: Nouveau Stop Loss (0 pour ne pas modifier).
            nouveau_tp: Nouveau Take Profit (0 pour ne pas modifier).

        Returns:
            True si modification réussie.
        """
        requete = {
            "action": mt5.TRADE_ACTION_SLTP,
            "position": ticket,
            "sl": nouveau_sl,
            "tp": nouveau_tp,
        }
        resultat = mt5.order_send(requete)
        if resultat is None or resultat.retcode != mt5.TRADE_RETCODE_DONE:
            code = resultat.retcode if resultat else "N/A"
            logger.error(f"Modification position {ticket} échouée — Code: {code}")
            return False

        logger.info(f"Position {ticket} modifiée | SL: {nouveau_sl:.5f} | TP: {nouveau_tp:.5f}")
        return True

    def fermer_position(self, ticket: int, symbole: str, volume: float, type_pos: int) -> bool:
        """
        Ferme une position ouverte (entièrement ou partiellement).

        Args:
            ticket: Ticket de la position.
            symbole: Symbole de la position.
            volume: Volume à fermer (peut être inférieur au volume total).
            type_pos: Type de la position ouverte (ORDER_TYPE_BUY ou SELL).

        Returns:
            True si fermeture réussie.
        """
        tick = self.get_tick(symbole)
        if tick is None:
            logger.error(f"Impossible de récupérer le tick pour fermer {ticket}")
            return False

        # Ordre inverse pour fermer
        type_fermeture = (
            mt5.ORDER_TYPE_SELL if type_pos == mt5.ORDER_TYPE_BUY
            else mt5.ORDER_TYPE_BUY
        )
        prix = tick["bid"] if type_fermeture == mt5.ORDER_TYPE_SELL else tick["ask"]

        requete = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": symbole,
            "volume": volume,
            "type": type_fermeture,
            "position": ticket,
            "price": prix,
            "deviation": 10,
            "magic": CONFIG.MAGIC_NUMBER,
            "comment": "SMC_BOT_FERMETURE",
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_IOC,
        }
        resultat = mt5.order_send(requete)
        if resultat is None or resultat.retcode != mt5.TRADE_RETCODE_DONE:
            code = resultat.retcode if resultat else "N/A"
            logger.error(f"Fermeture position {ticket} échouée — Code: {code}")
            return False

        logger.info(f"Position {ticket} fermée | Volume: {volume} | Prix: {prix:.5f}")
        return True

    def get_historique_positions(self, depuis_timestamp: int) -> List[Dict]:
        """
        Récupère l'historique des positions fermées depuis un timestamp.

        Args:
            depuis_timestamp: Timestamp UNIX UTC de début.

        Returns:
            Liste de dicts décrivant chaque position fermée.
        """
        historique = mt5.history_deals_get(depuis_timestamp, int(time.time()))
        if historique is None:
            return []

        resultat = []
        for deal in historique:
            if deal.magic == CONFIG.MAGIC_NUMBER and deal.entry == mt5.DEAL_ENTRY_OUT:
                resultat.append({
                    "ticket": deal.ticket,
                    "symbole": deal.symbol,
                    "volume": deal.volume,
                    "prix": deal.price,
                    "profit": deal.profit,
                    "temps": pd.Timestamp(deal.time, unit="s", tz="UTC"),
                    "commentaire": deal.comment,
                })
        return resultat
