"""
partial_closer.py — Fermeture partielle de positions via l'API MT5.
Gère le retry automatique et la confirmation obligatoire avant mise à jour d'état.
"""

import time
from datetime import datetime
from typing import TYPE_CHECKING
from loguru import logger

from config import CONFIG

if TYPE_CHECKING:
    from trade_manager import TradeGere, NiveauPartiel


class FermeturePartielle:
    """
    Exécute les fermetures partielles sur MT5 avec retry (3 tentatives).
    NE modifie jamais l'état du trade sans confirmation MT5 (retcode DONE).
    """

    def __init__(self, connecteur) -> None:
        """
        Args:
            connecteur: Instance ConnecteurMT5.
        """
        self.connecteur = connecteur

    def fermer_partiel(
        self,
        trade: "TradeGere",
        niveau: "NiveauPartiel",
        prix_actuel: float,
    ) -> bool:
        """
        Ferme partiellement une position MT5.

        Règle absolue : ne modifie l'état du trade QU'APRÈS confirmation MT5.
        Si MT5 échoue 3 fois → retourne False sans changer l'état.

        Args:
            trade: Trade actif à modifier.
            niveau: Niveau partiel à fermer (TP1, TP2 ou TP3).
            prix_actuel: Prix courant utilisé pour le calcul P&L.

        Returns:
            True si fermeture confirmée par MT5.
        """
        import MetaTrader5 as mt5

        if niveau.atteint:
            logger.warning(f"TP{niveau.id_niveau} déjà atteint — fermeture ignorée")
            return False

        # Récupérer les infos du symbole pour validation des lots
        info = mt5.symbol_info(trade.symbole)
        if info is None:
            logger.error(f"symbol_info() retourne None pour {trade.symbole}")
            return False

        # Arrondir les lots au volume_step du broker
        lots = round(
            round(niveau.lots / info.volume_step) * info.volume_step,
            8
        )
        lots = max(info.volume_min, lots)

        # Vérifier que les lots ne dépassent pas la position restante
        if lots > trade.lots_restants + 0.001:
            logger.error(
                f"Lots à fermer ({lots}) > position restante ({trade.lots_restants}) "
                f"— ajustement automatique"
            )
            lots = trade.lots_restants

        if lots < info.volume_min:
            logger.error(
                f"Lots calculés ({lots}) < volume_min ({info.volume_min}) — abandon"
            )
            return False

        # Prix de fermeture : bid pour LONG, ask pour SHORT
        from trade_manager import DirectionTrade
        tick = mt5.symbol_info_tick(trade.symbole)
        if tick is None:
            logger.error(f"Tick indisponible pour {trade.symbole}")
            return False

        prix_fermeture = (
            tick.bid if trade.direction == DirectionTrade.LONG else tick.ask
        )
        type_fermeture = (
            mt5.ORDER_TYPE_SELL if trade.direction == DirectionTrade.LONG
            else mt5.ORDER_TYPE_BUY
        )

        requete = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": trade.symbole,
            "volume": lots,
            "type": type_fermeture,
            "position": trade.ticket_mt5,
            "price": prix_fermeture,
            "deviation": 15,
            "magic": CONFIG.MAGIC_NUMBER,
            "comment": f"SMC_PARTIAL_TP{niveau.id_niveau}",
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_IOC,
        }

        # Tentatives avec backoff exponentiel
        for tentative in range(1, 4):
            resultat = mt5.order_send(requete)

            if resultat and resultat.retcode == mt5.TRADE_RETCODE_DONE:
                # ── Confirmation reçue → mettre à jour l'état ──────────────
                pnl = self._calculer_pnl(trade, lots, prix_actuel)

                niveau.atteint = True
                niveau.heure_atteinte = datetime.utcnow()
                niveau.prix_atteint = prix_actuel
                niveau.pnl_usd = pnl

                trade.lots_restants = max(
                    0.0,
                    round(
                        round((trade.lots_restants - lots) / info.volume_step)
                        * info.volume_step,
                        8
                    )
                )
                trade.pnl_realise_usd += pnl
                trade.tickets_fermetures.append(resultat.order)

                logger.success(
                    f"✅ Fermeture partielle TP{niveau.id_niveau} | "
                    f"{lots} lots @ {prix_actuel:.2f} | "
                    f"P&L: +${pnl:.2f} | "
                    f"Restant: {trade.lots_restants} lots"
                )
                return True

            code = resultat.retcode if resultat else "None"
            msg = resultat.comment if resultat else mt5.last_error()
            logger.warning(
                f"Tentative {tentative}/3 échouée — "
                f"Code: {code} | {msg} | Attente {tentative * 2}s..."
            )
            time.sleep(tentative * 2)

        logger.error(
            f"❌ Fermeture partielle TP{niveau.id_niveau} échouée après 3 tentatives"
        )
        return False

    def _calculer_pnl(
        self,
        trade: "TradeGere",
        lots: float,
        prix_sortie: float,
    ) -> float:
        """
        Calcule le P&L USD d'une fermeture partielle.
        XAUUSD : 1 lot standard = 100 oz → chaque $ de mouvement = 100$/lot.

        Args:
            trade: Trade de référence.
            lots: Lots fermés.
            prix_sortie: Prix de sortie.

        Returns:
            P&L en USD arrondi à 2 décimales.
        """
        import MetaTrader5 as mt5
        from trade_manager import DirectionTrade

        info = mt5.symbol_info(trade.symbole)
        if info is None or info.trade_tick_size == 0:
            # Fallback approximatif pour XAUUSD
            diff = (
                prix_sortie - trade.prix_entree
                if trade.direction == DirectionTrade.LONG
                else trade.prix_entree - prix_sortie
            )
            return round(diff * lots * 100.0, 2)

        diff = (
            prix_sortie - trade.prix_entree
            if trade.direction == DirectionTrade.LONG
            else trade.prix_entree - prix_sortie
        )
        pnl = diff * lots * (info.trade_tick_value / info.trade_tick_size)
        return round(pnl, 2)
