"""
position_tracker.py — Persistance des trades dans data/open_positions.json.
Permet de reprendre la gestion d'un trade après un redémarrage du bot.
"""

import json
from datetime import datetime
from pathlib import Path
from typing import Optional, List
from loguru import logger


FICHIER_POSITIONS = Path("data/open_positions.json")
FICHIER_HISTORIQUE = Path("data/trades_history.json")
TAILLE_MAX_HISTORIQUE = 500  # Trades conservés en historique


class SuiveurPosition:
    """
    Sérialise et persiste l'état des trades actifs.
    Écrit à chaque changement d'état pour survivre aux redémarrages.
    """

    def __init__(self) -> None:
        FICHIER_POSITIONS.parent.mkdir(exist_ok=True)

    # ── Sauvegarde ────────────────────────────────────────────────────────

    def sauvegarder(self, trade) -> None:
        """
        Sérialise et sauvegarde le trade actif.
        Écrasement complet à chaque appel — état toujours à jour.

        Args:
            trade: Instance TradeGere à persister.
        """
        try:
            data = self._serialiser(trade)
            FICHIER_POSITIONS.write_text(
                json.dumps(data, indent=2, ensure_ascii=False, default=str),
                encoding="utf-8",
            )
        except Exception as e:
            logger.error(f"Impossible de sauvegarder le trade : {e}")

    # ── Chargement ────────────────────────────────────────────────────────

    def charger(self) -> Optional[object]:
        """
        Charge le dernier trade actif depuis le fichier JSON.
        Vérifie que la position est toujours ouverte sur MT5.

        Returns:
            TradeGere reconstruit ou None si aucun trade actif.
        """
        if not FICHIER_POSITIONS.exists():
            return None

        try:
            data = json.loads(FICHIER_POSITIONS.read_text(encoding="utf-8"))
            trade = self._deserialiser(data)

            if trade is None:
                return None

            # Vérifier si déjà fermé
            if trade.est_ferme:
                logger.debug("Trade chargé déjà marqué comme fermé")
                return None

            # Vérifier que la position est toujours ouverte sur MT5
            if not self._verifier_position_mt5(trade.ticket_mt5, trade.symbole):
                logger.warning(
                    f"Trade {trade.id_trade} absent sur MT5 (ticket {trade.ticket_mt5}) "
                    f"— marqué comme fermé"
                )
                trade.est_ferme = True
                return None

            logger.info(
                f"Trade rechargé [{trade.id_trade}] | "
                f"Phase: {trade.phase.value} | "
                f"Lots restants: {trade.lots_restants}"
            )
            return trade

        except Exception as e:
            logger.error(f"Impossible de charger le trade : {e}")
            return None

    # ── Archivage ─────────────────────────────────────────────────────────

    def archiver(self, trade) -> None:
        """
        Archive un trade fermé dans data/trades_history.json.
        Limite l'historique à TAILLE_MAX_HISTORIQUE entrées.

        Args:
            trade: TradeGere fermé à archiver.
        """
        try:
            historique = []
            if FICHIER_HISTORIQUE.exists():
                try:
                    historique = json.loads(
                        FICHIER_HISTORIQUE.read_text(encoding="utf-8")
                    )
                except Exception:
                    historique = []

            historique.append(self._serialiser(trade))

            # Limiter la taille
            if len(historique) > TAILLE_MAX_HISTORIQUE:
                historique = historique[-TAILLE_MAX_HISTORIQUE:]

            FICHIER_HISTORIQUE.write_text(
                json.dumps(historique, indent=2, ensure_ascii=False, default=str),
                encoding="utf-8",
            )
            logger.debug(f"Trade archivé : {trade.id_trade}")

            # Supprimer le fichier positions actives
            if FICHIER_POSITIONS.exists():
                FICHIER_POSITIONS.unlink()

        except Exception as e:
            logger.error(f"Impossible d'archiver le trade : {e}")

    # ── Sérialisation ─────────────────────────────────────────────────────

    def _serialiser(self, trade) -> dict:
        """
        Convertit un TradeGere en dict JSON-compatible.
        Gère les types datetime, Enum et None.
        """
        from trade_manager import (
            TradeGere, NiveauPartiel, NiveauSL,
            PhaseTrade, DirectionTrade, RaisonFermeture,
        )

        def dt_str(dt) -> Optional[str]:
            if dt is None:
                return None
            return dt.isoformat() if isinstance(dt, datetime) else str(dt)

        def niveau_dict(n: NiveauPartiel) -> dict:
            return {
                "id_niveau": n.id_niveau,
                "multiple_r": n.multiple_r,
                "prix": n.prix,
                "pct_position": n.pct_position,
                "lots": n.lots,
                "atteint": n.atteint,
                "heure_atteinte": dt_str(n.heure_atteinte),
                "prix_atteint": n.prix_atteint,
                "pnl_usd": n.pnl_usd,
            }

        return {
            "id_trade": trade.id_trade,
            "ticket_mt5": trade.ticket_mt5,
            "tickets_fermetures": trade.tickets_fermetures,
            "symbole": trade.symbole,
            "direction": trade.direction.value,
            "prix_entree": trade.prix_entree,
            "heure_entree": dt_str(trade.heure_entree),
            "lots_initial": trade.lots_initial,
            "lots_restants": trade.lots_restants,
            "sl": {
                "prix_actuel": trade.sl.prix_actuel,
                "prix_initial": trade.sl.prix_initial,
                "est_breakeven": trade.sl.est_breakeven,
                "est_a_1r": trade.sl.est_a_1r,
                "est_trailing": trade.sl.est_trailing,
                "historique": trade.sl.historique,
            },
            "tp1": niveau_dict(trade.tp1),
            "tp2": niveau_dict(trade.tp2),
            "tp3": niveau_dict(trade.tp3),
            "phase": trade.phase.value,
            "ob_score": trade.ob_score,
            "ob_force": trade.ob_force,
            "confluences": trade.confluences,
            "trailing_actif": trade.trailing_actif,
            "trailing_prix": trade.trailing_prix,
            "trailing_distance_atr": trade.trailing_distance_atr,
            "mfe": trade.mfe,
            "mae": trade.mae,
            "pnl_realise_usd": trade.pnl_realise_usd,
            "pnl_flottant_usd": trade.pnl_flottant_usd,
            "est_ferme": trade.est_ferme,
            "raison_fermeture": (
                trade.raison_fermeture.value
                if trade.raison_fermeture else None
            ),
            "heure_fermeture": dt_str(trade.heure_fermeture),
            "pnl_total_usd": trade.pnl_total_usd,
            "r_total_realise": trade.r_total_realise,
        }

    def _deserialiser(self, data: dict) -> Optional[object]:
        """
        Reconstruit un TradeGere depuis un dict JSON.

        Args:
            data: Dict chargé depuis le fichier JSON.

        Returns:
            TradeGere reconstruit ou None en cas d'erreur.
        """
        try:
            from trade_manager import (
                TradeGere, NiveauPartiel, NiveauSL,
                PhaseTrade, DirectionTrade, RaisonFermeture,
            )

            def parse_dt(s: Optional[str]) -> Optional[datetime]:
                if not s:
                    return None
                try:
                    return datetime.fromisoformat(s)
                except Exception:
                    return None

            def parse_niveau(d: dict) -> NiveauPartiel:
                return NiveauPartiel(
                    id_niveau=d["id_niveau"],
                    multiple_r=d["multiple_r"],
                    prix=d["prix"],
                    pct_position=d["pct_position"],
                    lots=d["lots"],
                    atteint=d.get("atteint", False),
                    heure_atteinte=parse_dt(d.get("heure_atteinte")),
                    prix_atteint=d.get("prix_atteint"),
                    pnl_usd=d.get("pnl_usd"),
                )

            sl_data = data["sl"]
            sl = NiveauSL(
                prix_actuel=sl_data["prix_actuel"],
                prix_initial=sl_data["prix_initial"],
                est_breakeven=sl_data.get("est_breakeven", False),
                est_a_1r=sl_data.get("est_a_1r", False),
                est_trailing=sl_data.get("est_trailing", False),
                historique=sl_data.get("historique", []),
            )

            # Reconstituer la raison de fermeture
            raison_str = data.get("raison_fermeture")
            raison = None
            if raison_str:
                for r in RaisonFermeture:
                    if r.value == raison_str:
                        raison = r
                        break

            trade = TradeGere(
                id_trade=data["id_trade"],
                ticket_mt5=data["ticket_mt5"],
                tickets_fermetures=data.get("tickets_fermetures", []),
                symbole=data["symbole"],
                direction=DirectionTrade(data["direction"]),
                prix_entree=data["prix_entree"],
                heure_entree=parse_dt(data["heure_entree"]) or datetime.utcnow(),
                lots_initial=data["lots_initial"],
                lots_restants=data["lots_restants"],
                sl=sl,
                tp1=parse_niveau(data["tp1"]),
                tp2=parse_niveau(data["tp2"]),
                tp3=parse_niveau(data["tp3"]),
                phase=PhaseTrade(data.get("phase", PhaseTrade.PHASE_1.value)),
                ob_score=data.get("ob_score", 0),
                ob_force=data.get("ob_force", ""),
                confluences=data.get("confluences", []),
                trailing_actif=data.get("trailing_actif", False),
                trailing_prix=data.get("trailing_prix"),
                trailing_distance_atr=data.get("trailing_distance_atr", 0.0),
                mfe=data.get("mfe", 0.0),
                mae=data.get("mae", 0.0),
                pnl_realise_usd=data.get("pnl_realise_usd", 0.0),
                pnl_flottant_usd=data.get("pnl_flottant_usd", 0.0),
                est_ferme=data.get("est_ferme", False),
                raison_fermeture=raison,
                heure_fermeture=parse_dt(data.get("heure_fermeture")),
                pnl_total_usd=data.get("pnl_total_usd"),
                r_total_realise=data.get("r_total_realise"),
            )
            return trade

        except Exception as e:
            logger.error(f"Erreur désérialisation trade : {e}")
            return None

    # ── Vérification MT5 ──────────────────────────────────────────────────

    def _verifier_position_mt5(self, ticket: int, symbole: str) -> bool:
        """
        Vérifie que la position est toujours ouverte sur MT5.

        Args:
            ticket: Numéro de ticket MT5.
            symbole: Symbole de la position.

        Returns:
            True si la position existe encore sur MT5.
        """
        try:
            import MetaTrader5 as mt5
            positions = mt5.positions_get(symbol=symbole)
            if positions is None:
                return False
            return any(p.ticket == ticket for p in positions)
        except Exception:
            # MT5 non disponible (mode test) → supposer que la position existe
            return True

    def get_historique(self, n_derniers: int = 10) -> List[dict]:
        """
        Retourne les N derniers trades fermés.

        Args:
            n_derniers: Nombre de trades à retourner.

        Returns:
            Liste de dicts représentant les trades archivés.
        """
        if not FICHIER_HISTORIQUE.exists():
            return []
        try:
            historique = json.loads(FICHIER_HISTORIQUE.read_text(encoding="utf-8"))
            return historique[-n_derniers:]
        except Exception:
            return []
