"""
trade_manager.py — Gestion complète du cycle de vie des trades sur 3 niveaux.
Phase 1 : TP1 @1R → SL au breakeven
Phase 2 : TP2 @2R → SL à 1R (profit garanti)
Phase 3 : TP3 @structure → trailing stop actif

Résultat garanti si TP1+TP2 atteints puis TP3 revient au SL : +1.25R minimum.
"""

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional, List, Dict
from loguru import logger

from config import CONFIG
from indicators import Indicateurs


# ── Énumérations (définies AVANT les dataclasses) ─────────────────────────

class PhaseTrade(Enum):
    PHASE_1 = "PHASE_1_ATTENTE_TP1"
    PHASE_2 = "PHASE_2_TP1_ATTEINT"
    PHASE_3 = "PHASE_3_TP2_ATTEINT"
    FERME   = "FERMÉ"


class DirectionTrade(Enum):
    LONG  = "LONG"
    SHORT = "SHORT"


class RaisonFermeture(Enum):
    TP1_ATTEINT        = "TP1 atteint"
    TP2_ATTEINT        = "TP2 atteint"
    TP3_ATTEINT        = "TP3 atteint (structure)"
    SL_TOUCHE          = "Stop Loss touché"
    BREAKEVEN_TOUCHE   = "Breakeven touché"
    TRAILING_DECLENCHE = "Trailing stop déclenché"
    INVALIDATION       = "Invalidation de setup (structure H4)"
    MANUEL             = "Fermeture manuelle"
    DRAWDOWN_MAX       = "Drawdown max atteint"
    FIN_SESSION        = "Fin de session"


# ── Dataclasses ────────────────────────────────────────────────────────────

@dataclass
class NiveauPartiel:
    """Définit un niveau de prise de profit partielle."""
    id_niveau: int
    multiple_r: float
    prix: float
    pct_position: float
    lots: float
    atteint: bool = False
    heure_atteinte: Optional[datetime] = None
    prix_atteint: Optional[float] = None
    pnl_usd: Optional[float] = None


@dataclass
class NiveauSL:
    """État du Stop Loss avec historique complet des déplacements."""
    prix_actuel: float
    prix_initial: float
    est_breakeven: bool = False
    est_a_1r: bool = False
    est_trailing: bool = False
    historique: List[Dict] = field(default_factory=list)


@dataclass
class TradeGere:
    """Trade complet avec gestion multi-niveaux, persisté en JSON."""
    id_trade: str
    ticket_mt5: int
    tickets_fermetures: List[int]
    symbole: str
    direction: DirectionTrade
    prix_entree: float
    heure_entree: datetime
    lots_initial: float
    lots_restants: float
    sl: NiveauSL
    tp1: NiveauPartiel
    tp2: NiveauPartiel
    tp3: NiveauPartiel
    phase: PhaseTrade = PhaseTrade.PHASE_1
    ob_score: int = 0
    ob_force: str = ""
    confluences: List[str] = field(default_factory=list)
    trailing_actif: bool = False
    trailing_prix: Optional[float] = None
    trailing_distance_atr: float = 0.0
    mfe: float = 0.0
    mae: float = 0.0
    pnl_realise_usd: float = 0.0
    pnl_flottant_usd: float = 0.0
    est_ferme: bool = False
    raison_fermeture: Optional[RaisonFermeture] = None
    heure_fermeture: Optional[datetime] = None
    pnl_total_usd: Optional[float] = None
    r_total_realise: Optional[float] = None

    @property
    def distance_risque(self) -> float:
        """Distance initiale SL en prix = 1R."""
        return abs(self.prix_entree - self.sl.prix_initial)

    def est_long(self) -> bool:
        return self.direction == DirectionTrade.LONG


# ── Gestionnaire principal ─────────────────────────────────────────────────

class GestionnairePositions:
    """
    Gestion multi-niveaux des trades SMC (3 phases).
    Interface compatible avec l'ancien code du bot.
    """

    def __init__(self, connecteur=None, config=None) -> None:
        self.connecteur = connecteur
        self.config = config or CONFIG    # Config scalping injectée ou CONFIG global
        self._trade_actif: Optional[TradeGere] = None
        self._position_tracker = None
        self._partial_closer = None
        self.pyramiding_manager = None   # Injecté depuis main.py si PYRAMIDING_ENABLED
        self.journal = None              # Injecté depuis main.py (JournalTrades)

    def _init_sous_modules(self) -> None:
        """Initialise les sous-modules au premier usage (lazy init)."""
        if self._position_tracker is None:
            try:
                from position_tracker import SuiveurPosition
                self._position_tracker = SuiveurPosition()
            except Exception as e:
                logger.debug(f"SuiveurPosition non disponible : {e}")

        if self._partial_closer is None and self.connecteur is not None:
            try:
                from partial_closer import FermeturePartielle
                self._partial_closer = FermeturePartielle(self.connecteur)
            except Exception as e:
                logger.debug(f"FermeturePartielle non disponible : {e}")

    # ── Interface publique ─────────────────────────────────────────────────

    def enregistrer_position(
        self,
        ticket: int,
        symbole: str,
        direction: str,
        volume: float,
        prix_entree: float,
        sl: float,
        tp1: float,
        tp2: float,
        tp3: float,
        sl_distance: float,
        zone_reference=None,
        ob_score: int = 0,
        ob_force: str = "",
        confluences: Optional[List[str]] = None,
    ) -> None:
        """
        Enregistre un nouveau trade avec gestion 3 niveaux.
        Distribution automatique : 50% / 25% / 25%.
        """
        self._init_sous_modules()

        direction_enum = (
            DirectionTrade.LONG if direction == "LONG"
            else DirectionTrade.SHORT
        )

        # Distribution des lots
        lots_tp1 = round(volume * 0.50, 2)
        lots_tp2 = round(volume * 0.25, 2)
        lots_tp3 = max(0.01, round(volume - lots_tp1 - lots_tp2, 2))

        trade = TradeGere(
            id_trade=str(uuid.uuid4())[:8],
            ticket_mt5=ticket,
            tickets_fermetures=[],
            symbole=symbole,
            direction=direction_enum,
            prix_entree=prix_entree,
            heure_entree=datetime.utcnow(),
            lots_initial=volume,
            lots_restants=volume,
            sl=NiveauSL(prix_actuel=sl, prix_initial=sl),
            tp1=NiveauPartiel(1, 1.0, tp1, 50.0, lots_tp1),
            tp2=NiveauPartiel(2, 2.0, tp2, 25.0, lots_tp2),
            tp3=NiveauPartiel(3, CONFIG.RR_CIBLE, tp3, 25.0, lots_tp3),
            ob_score=ob_score,
            ob_force=ob_force,
            confluences=confluences or [],
        )

        self._trade_actif = trade
        self._sauvegarder(trade)

        logger.success(
            f"Trade [{trade.id_trade}] | {direction} {volume} lots {symbole} "
            f"@ {prix_entree:.2f} | SL: {sl:.2f} | "
            f"TP1: {tp1:.2f} | TP2: {tp2:.2f} | TP3: {tp3:.2f} | "
            f"Lots: {lots_tp1}/{lots_tp2}/{lots_tp3}"
        )

    def gerer_positions(self, df_m15) -> List[int]:
        """
        Gère le trade actif. Appelé à chaque nouvelle bougie M5/M15 fermée.

        Returns:
            Liste des tickets de positions fermées pendant cette itération.
        """
        self._init_sous_modules()

        if self._trade_actif is None or self._trade_actif.est_ferme:
            return []

        trade = self._trade_actif
        prix = self._get_prix_actuel(trade.symbole, trade.direction)
        if prix <= 0:
            return []

        self._maj_excursions(trade, prix)

        tickets_fermes = []

        if trade.phase == PhaseTrade.PHASE_1:
            if self._tp1_atteint(trade, prix):
                self._gerer_tp1(trade, prix, df_m15)
            elif self._sl_touche(trade, prix):
                self._fermer_tout(trade, RaisonFermeture.SL_TOUCHE, prix)
                tickets_fermes.append(trade.ticket_mt5)

        elif trade.phase == PhaseTrade.PHASE_2:
            if self._tp2_atteint(trade, prix):
                self._gerer_tp2(trade, prix, df_m15)
            elif self._sl_touche(trade, prix):
                self._fermer_tout(trade, RaisonFermeture.BREAKEVEN_TOUCHE, prix)
                tickets_fermes.append(trade.ticket_mt5)

        elif trade.phase == PhaseTrade.PHASE_3:
            if self._tp3_atteint(trade, prix):
                self._fermer_tout(trade, RaisonFermeture.TP3_ATTEINT, prix)
                tickets_fermes.append(trade.ticket_mt5)
            elif self._trailing_declenche(trade, prix):
                self._fermer_tout(trade, RaisonFermeture.TRAILING_DECLENCHE, prix)
                tickets_fermes.append(trade.ticket_mt5)
            elif self._sl_touche(trade, prix):
                self._fermer_tout(trade, RaisonFermeture.SL_TOUCHE, prix)
                tickets_fermes.append(trade.ticket_mt5)
            else:
                self._maj_trailing(trade, prix, df_m15)

        self._sauvegarder(trade)

        # ── Mise à jour de l'add-on pyramiding si actif ───────────────────
        if (CONFIG.PYRAMIDING_ENABLED
                and self.pyramiding_manager is not None
                and self._trade_actif is not None  # peut avoir changé si fermé
                and not self._trade_actif.est_ferme):
            try:
                self.pyramiding_manager.update(self._trade_actif, df_m15)
            except Exception as e:
                logger.error(f"Erreur pyramiding.update : {e}")

        return tickets_fermes

    def fermer_position_invalidation(self, ticket: int, raison: str) -> bool:
        """Ferme une position suite à invalidation de setup."""
        if self._trade_actif is None or self._trade_actif.ticket_mt5 != ticket:
            return False
        prix = self._get_prix_actuel(
            self._trade_actif.symbole, self._trade_actif.direction
        )
        self._fermer_tout(self._trade_actif, RaisonFermeture.INVALIDATION, prix)
        logger.warning(f"Position {ticket} fermée par invalidation : {raison}")
        return True

    def a_position_ouverte(self, symbole: Optional[str] = None) -> bool:
        """
        Vérifie si une position est actuellement ouverte.
        Double vérification : état interne + MT5 direct.
        Évite l'ouverture d'une 2ème position après redémarrage du bot.
        """
        # ── Vérification état interne ──────────────────────────────────────
        interne = (
            self._trade_actif is not None
            and not self._trade_actif.est_ferme
            and (symbole is None or self._trade_actif.symbole == symbole)
        )
        if interne:
            return True

        # ── Vérification MT5 directe (sécurité après redémarrage) ─────────
        try:
            import MetaTrader5 as mt5
            magic = getattr(self.config, "MAGIC_NUMBER", CONFIG.MAGIC_NUMBER)
            if symbole:
                positions = mt5.positions_get(symbol=symbole)
            else:
                positions = mt5.positions_get()
            if positions:
                for pos in positions:
                    if int(pos.magic) == magic:
                        logger.warning(
                            f"[TradeManager] Position MT5 orpheline détectée "
                            f"#{pos.ticket} {pos.symbol} — bot redémarré sans fermeture. "
                            f"Nouveau trade bloqué."
                        )
                        return True
        except Exception as e:
            logger.debug(f"[TradeManager] Vérif MT5 positions : {e}")

        return False

    def get_resume_positions(self) -> List[Dict]:
        """Résumé complet pour le dashboard."""
        if self._trade_actif is None or self._trade_actif.est_ferme:
            return []

        trade = self._trade_actif
        prix = self._get_prix_actuel(trade.symbole, trade.direction)
        r_actuel = 0.0
        if trade.distance_risque > 0 and prix > 0:
            r_actuel = (
                (prix - trade.prix_entree) / trade.distance_risque
                if trade.est_long()
                else (trade.prix_entree - prix) / trade.distance_risque
            )

        return [{
            "ticket": trade.ticket_mt5,
            "symbole": trade.symbole,
            "direction": trade.direction.value,
            "volume": trade.lots_restants,
            "prix_entree": trade.prix_entree,
            "sl": trade.sl.prix_actuel,
            "tp2": trade.tp2.prix,
            "profit": trade.pnl_flottant_usd,
            "r_actuel": round(r_actuel, 2),
            "tp1_atteint": trade.tp1.atteint,
            "tp2_atteint": trade.tp2.atteint,
            "trailing_actif": trade.trailing_actif,
            "phase": trade.phase.value,
            "ob_score": trade.ob_score,
            "ob_force": trade.ob_force,
            "confluences": trade.confluences,
            "mfe": round(trade.mfe, 2),
            "mae": round(trade.mae, 2),
            "pnl_realise": round(trade.pnl_realise_usd, 2),
            "tp1_prix": trade.tp1.prix,
            "tp2_prix": trade.tp2.prix,
            "tp3_prix": trade.tp3.prix,
            "trailing_prix": trade.trailing_prix,
            "id_trade": trade.id_trade,
        }]

    def supprimer_position(self, ticket: int) -> None:
        """Marque une position comme fermée."""
        if self._trade_actif and self._trade_actif.ticket_mt5 == ticket:
            self._trade_actif.est_ferme = True

    # ── Gestion des phases ─────────────────────────────────────────────────

    def _gerer_tp1(self, trade: TradeGere, prix: float, df_m15) -> None:
        """TP1 → fermer 50%, SL au breakeven, évaluer le pyramiding."""
        logger.info(f"🎯 TP1 @ {prix:.2f} — fermeture 50%")

        if not self._fermer_partiel(trade, trade.tp1, prix):
            logger.error("Fermeture partielle TP1 échouée — maintien Phase 1")
            return

        nouveau_sl = trade.prix_entree
        if self._deplacer_sl(trade, nouveau_sl, "Breakeven après TP1"):
            trade.sl.est_breakeven = True

        trade.phase = PhaseTrade.PHASE_2
        logger.success(
            f"✅ Phase 2 | SL → BE @ {nouveau_sl:.2f} | "
            f"Restant: {trade.lots_restants} lots | "
            f"P&L réalisé: +${trade.pnl_realise_usd:.2f}"
        )

        # ── Pyramiding : évaluer et éventuellement ouvrir un add-on ──────
        if (CONFIG.PYRAMIDING_ENABLED
                and self.pyramiding_manager is not None):
            try:
                addon = self.pyramiding_manager.on_tp1_reached(trade)
                if addon:
                    logger.info(
                        f"🔺 Pyramiding activé — add-on [{addon.addon_id}] ouvert"
                    )
                else:
                    logger.debug("Pyramiding évalué — conditions non remplies")
            except Exception as e:
                logger.error(f"Erreur pyramiding.on_tp1_reached : {e}")

    def _gerer_tp2(self, trade: TradeGere, prix: float, df_m15) -> None:
        """TP2 → fermer 25%, SL à 1R de gain, activer trailing."""
        logger.info(f"🎯 TP2 @ {prix:.2f} — fermeture 25%")

        if not self._fermer_partiel(trade, trade.tp2, prix):
            logger.error("Fermeture partielle TP2 échouée — maintien Phase 2")
            return

        # SL à 1R de gain — verrouillage du profit minimum
        nouveau_sl = (
            trade.prix_entree + trade.distance_risque
            if trade.est_long()
            else trade.prix_entree - trade.distance_risque
        )
        if self._deplacer_sl(trade, nouveau_sl, "Verrouillage 1R après TP2"):
            trade.sl.est_a_1r = True

        # Trailing stop initial
        atr = self._calculer_atr_m15(df_m15)
        distance = atr * CONFIG.TRAILING_DISTANCE_ATR
        trade.trailing_distance_atr = distance
        trade.trailing_actif = True
        trade.trailing_prix = (
            prix - distance if trade.est_long()
            else prix + distance
        )

        trade.phase = PhaseTrade.PHASE_3
        logger.success(
            f"✅ Phase 3 | SL → 1R @ {nouveau_sl:.2f} | "
            f"Trailing @ {trade.trailing_prix:.2f} | "
            f"Restant: {trade.lots_restants} lots | "
            f"P&L réalisé: +${trade.pnl_realise_usd:.2f}"
        )

    def _maj_trailing(self, trade: TradeGere, prix: float, df_m15) -> None:
        """Met à jour le trailing — ne peut que progresser en faveur."""
        if not trade.trailing_actif or trade.trailing_prix is None:
            return

        atr = self._calculer_atr_m15(df_m15)
        distance = atr * CONFIG.TRAILING_DISTANCE_ATR
        trade.trailing_distance_atr = distance

        if trade.est_long():
            nouveau = prix - distance
            if nouveau > trade.trailing_prix:
                ancien = trade.trailing_prix
                trade.trailing_prix = nouveau
                logger.debug(f"Trailing ↑ : {ancien:.2f} → {nouveau:.2f}")
        else:
            nouveau = prix + distance
            if nouveau < trade.trailing_prix:
                ancien = trade.trailing_prix
                trade.trailing_prix = nouveau
                logger.debug(f"Trailing ↓ : {ancien:.2f} → {nouveau:.2f}")

    # ── Conditions ────────────────────────────────────────────────────────

    def _tp1_atteint(self, t: TradeGere, p: float) -> bool:
        if t.tp1.atteint:
            return False
        return p >= t.tp1.prix if t.est_long() else p <= t.tp1.prix

    def _tp2_atteint(self, t: TradeGere, p: float) -> bool:
        if t.tp2.atteint:
            return False
        return p >= t.tp2.prix if t.est_long() else p <= t.tp2.prix

    def _tp3_atteint(self, t: TradeGere, p: float) -> bool:
        if t.tp3.atteint:
            return False
        return p >= t.tp3.prix if t.est_long() else p <= t.tp3.prix

    def _sl_touche(self, t: TradeGere, p: float) -> bool:
        return p <= t.sl.prix_actuel if t.est_long() else p >= t.sl.prix_actuel

    def _trailing_declenche(self, t: TradeGere, p: float) -> bool:
        if not t.trailing_actif or t.trailing_prix is None:
            return False
        return p <= t.trailing_prix if t.est_long() else p >= t.trailing_prix

    # ── SL ───────────────────────────────────────────────────────────────

    def _deplacer_sl(self, trade: TradeGere, nouveau_sl: float, raison: str) -> bool:
        """
        Déplace le SL. RÈGLE ABSOLUE : ne peut jamais reculer.
        """
        if trade.est_long() and nouveau_sl <= trade.sl.prix_actuel:
            logger.warning(
                f"SL refusé : {nouveau_sl:.2f} ≤ actuel {trade.sl.prix_actuel:.2f} (LONG)"
            )
            return False
        if not trade.est_long() and nouveau_sl >= trade.sl.prix_actuel:
            logger.warning(
                f"SL refusé : {nouveau_sl:.2f} ≥ actuel {trade.sl.prix_actuel:.2f} (SHORT)"
            )
            return False

        ancien = trade.sl.prix_actuel
        succes = True

        if self.connecteur is not None:
            succes = self.connecteur.modifier_position(trade.ticket_mt5, nouveau_sl, 0.0)

        if succes:
            trade.sl.historique.append({
                "heure": datetime.utcnow().isoformat(),
                "de": ancien,
                "vers": nouveau_sl,
                "raison": raison,
            })
            trade.sl.prix_actuel = nouveau_sl
            logger.info(f"SL : {ancien:.2f} → {nouveau_sl:.2f} | {raison}")
        return succes

    # ── Fermeture partielle ───────────────────────────────────────────────

    def _fermer_partiel(
        self,
        trade: TradeGere,
        niveau: NiveauPartiel,
        prix: float,
    ) -> bool:
        """
        Ferme partiellement. NE passe jamais à la phase suivante sans confirmation.
        """
        if niveau.atteint:
            return False

        if self._partial_closer is not None:
            # Mode live avec MT5
            return self._partial_closer.fermer_partiel(trade, niveau, prix)

        # Mode paper / test
        niveau.atteint = True
        niveau.heure_atteinte = datetime.utcnow()
        niveau.prix_atteint = prix
        niveau.pnl_usd = self._calculer_pnl(trade, niveau.lots, prix)
        trade.lots_restants = max(0.0, round(trade.lots_restants - niveau.lots, 2))
        trade.pnl_realise_usd += niveau.pnl_usd or 0.0
        return True

    # ── Fermeture complète ────────────────────────────────────────────────

    def _fermer_tout(
        self,
        trade: TradeGere,
        raison: RaisonFermeture,
        prix: float,
    ) -> None:
        """Ferme complètement la position restante."""
        if trade.lots_restants > 0 and self.connecteur is not None:
            import MetaTrader5 as mt5
            type_fermeture = (
                mt5.ORDER_TYPE_SELL if trade.est_long()
                else mt5.ORDER_TYPE_BUY
            )
            self.connecteur.fermer_position(
                trade.ticket_mt5, trade.symbole,
                trade.lots_restants, type_fermeture
            )

        pnl_final = self._calculer_pnl(trade, trade.lots_restants, prix)
        trade.pnl_realise_usd += pnl_final
        trade.lots_restants = 0.0
        trade.est_ferme = True
        trade.raison_fermeture = raison
        trade.heure_fermeture = datetime.utcnow()
        trade.phase = PhaseTrade.FERME
        trade.pnl_total_usd = trade.pnl_realise_usd

        risque_usd = trade.distance_risque * trade.lots_initial * 100.0
        trade.r_total_realise = (
            trade.pnl_total_usd / risque_usd if risque_usd > 0 else 0.0
        )

        self._logger_fermeture(trade)

        try:
            if self._position_tracker:
                self._position_tracker.archiver(trade)
        except Exception:
            pass

        self._trade_actif = None

    # ── Utilitaires ───────────────────────────────────────────────────────

    def _maj_excursions(self, trade: TradeGere, prix: float) -> None:
        """MFE et MAE en R."""
        if trade.distance_risque <= 0:
            return
        if trade.est_long():
            favorable = (prix - trade.prix_entree) / trade.distance_risque
            adverse = (trade.prix_entree - prix) / trade.distance_risque
        else:
            favorable = (trade.prix_entree - prix) / trade.distance_risque
            adverse = (prix - trade.prix_entree) / trade.distance_risque

        trade.mfe = max(trade.mfe, favorable)
        trade.mae = max(trade.mae, max(0.0, adverse))

    def _calculer_pnl(self, trade: TradeGere, lots: float, prix: float) -> float:
        """P&L USD d'une tranche. XAUUSD : 1 lot ≈ 100$/$ de mouvement."""
        if lots <= 0:
            return 0.0
        diff = (
            prix - trade.prix_entree if trade.est_long()
            else trade.prix_entree - prix
        )
        return round(diff * lots * 100.0, 2)

    def _calculer_atr_m15(self, df_m15) -> float:
        """ATR M15 sur la bougie fermée."""
        try:
            serie = Indicateurs.atr(df_m15, CONFIG.ATR_PERIODE)
            return float(serie.iloc[-2]) if len(serie) >= 2 else 5.0
        except Exception:
            return 5.0

    # ── Méthodes scalping ────────────────────────────────────────────────

    def _check_adverse_candle_exit(
        self,
        trade: "TradeGere",
        current_candle: dict,
    ) -> bool:
        """
        Sortie anticipée scalping si une bougie contraire forte apparaît.
        LONG en cours  → engulfing baissier + clôture tiers inférieur → sortir.
        SHORT en cours → engulfing haussier + clôture tiers supérieur → sortir.

        Appelé à chaque mise à jour de bougie fermée en scalping.
        """
        if not getattr(self.config, "ADVERSE_CANDLE_EXIT", True):
            return False

        est_long = (
            trade.direction == DirectionTrade.LONG
            if hasattr(trade, "direction")
            else current_candle.get("direction", "bullish") == "bullish"
        )

        if est_long:
            adverse = (
                current_candle.get("bear_engulf", False) and
                current_candle.get("close_pos_pct", 50) <= 35
            )
        else:
            adverse = (
                current_candle.get("bull_engulf", False) and
                current_candle.get("close_pos_pct", 50) >= 65
            )

        if adverse:
            logger.warning(
                f"[SCALP] Bougie adverse → sortie anticipée "
                f"[{getattr(trade, 'id_trade', '?')}] "
                f"@ {current_candle.get('close', 0):.2f}"
            )
        return adverse

    def _check_max_duration(
        self,
        trade: "TradeGere",
        bars_open: int,
    ) -> bool:
        """
        Ferme le trade si la durée max en bougies est dépassée.
        En scalping M5 : 20 bougies = 100 minutes max.
        Un trade scalping qui tient sans résultat = signal raté.
        """
        max_bars = getattr(self.config, "MAX_TRADE_DURATION_BARS", 20)
        if bars_open >= max_bars:
            logger.info(
                f"[SCALP] Durée max {max_bars} bougies atteinte "
                f"→ fermeture [{getattr(trade, 'id_trade', '?')}]"
            )
            return True
        return False

    def _update_trailing_stop_scalping(
        self,
        trade: "TradeGere",
        current_price: float,
        atr: float,
    ) -> Optional[float]:
        """
        Trailing stop scalping — activé dès +0.5R (pas +1R comme en swing).
        Distance : TRAILING_ATR × ATR (1.0× par défaut, plus serré qu'en swing 1.5×).

        Returns:
            Nouveau SL si le trailing doit être mis à jour, None sinon.
        """
        activation_r = getattr(self.config, "TRAILING_ACTIVATION_R",
                                getattr(self.config, "TRAILING_ACTIVATION_RR", 0.5))
        trailing_atr = getattr(self.config, "TRAILING_ATR",
                                getattr(self.config, "TRAILING_DISTANCE_ATR", 1.0))
        digits = getattr(self.config, "PRICE_DIGITS", 2)

        # Calculer le SL actuel du trade
        sl_actuel = float(trade.sl.prix_actuel) if hasattr(trade, "sl") else getattr(trade, "stop_loss", 0.0)
        entry = float(trade.prix_entree) if hasattr(trade, "prix_entree") else getattr(trade, "entry_price", 0.0)
        distance_risque = abs(entry - sl_actuel) if sl_actuel > 0 else atr

        activation_price = (
            entry + distance_risque * activation_r
            if (hasattr(trade, "direction") and trade.direction == DirectionTrade.LONG)
            or getattr(trade, "direction", "bullish") == "bullish"
            else entry - distance_risque * activation_r
        )

        est_long = (
            trade.direction == DirectionTrade.LONG
            if hasattr(trade, "direction") and hasattr(trade.direction, "value")
            else True
        )

        if est_long and current_price >= activation_price:
            new_sl = round(current_price - atr * trailing_atr, digits)
            if new_sl > sl_actuel:
                return new_sl

        if not est_long and current_price <= activation_price:
            new_sl = round(current_price + atr * trailing_atr, digits)
            if sl_actuel > 0 and new_sl < sl_actuel:
                return new_sl

        return None

    def _verifier_bougie_adverse_scalping(
        self,
        trade: "TradeGere",
        indicateurs: dict,
    ) -> bool:
        """
        Sortie anticipée scalping si une bougie adverse forte apparaît.
        LONG : engulfing baissier + clôture dans le tiers inférieur → sortir.
        SHORT : engulfing haussier + clôture dans le tiers supérieur → sortir.

        Args:
            trade       : Trade en cours.
            indicateurs : Dict d'indicateurs calculés (depuis ScalpingStrategy).

        Returns:
            True si la bougie adverse justifie une sortie immédiate.
        """
        if not indicateurs:
            return False

        if trade.direction == DirectionTrade.LONG:
            adverse = (
                indicateurs.get("bear_engulf", False) and
                indicateurs.get("close_pos_pct", 50) <= 35
            )
        else:
            adverse = (
                indicateurs.get("bull_engulf", False) and
                indicateurs.get("close_pos_pct", 50) >= 65
            )

        if adverse:
            logger.warning(
                f"[SCALP] Bougie adverse détectée — sortie anticipée "
                f"[{trade.id_trade}] @ {indicateurs.get('close', 0):.2f}"
            )
        return adverse

    def _verifier_duree_max_scalping(
        self,
        trade: "TradeGere",
        max_bars: int = 20,
        tf_minutes: int = 5,
    ) -> bool:
        """
        Ferme le trade si la durée maximale est dépassée en scalping.
        En scalping, un trade qui n'a pas atteint SL/TP après N bougies
        doit être fermé pour libérer l'exposition.

        Args:
            trade      : Trade en cours.
            max_bars   : Nombre maximum de bougies signal avant fermeture.
            tf_minutes : Durée d'une bougie signal en minutes.

        Returns:
            True si la durée max est dépassée → fermer.
        """
        if trade.heure_entree is None:
            return False

        duree_max_sec = max_bars * tf_minutes * 60
        ecoule = (datetime.utcnow() - trade.heure_entree).total_seconds()

        if ecoule >= duree_max_sec:
            logger.info(
                f"[SCALP] Durée max atteinte ({int(ecoule / 60)}min / "
                f"{max_bars} bougies) — fermeture [{trade.id_trade}]"
            )
            return True
        return False

    def _get_prix_actuel(self, symbole: str, direction: DirectionTrade) -> float:
        """Prix courant bid (LONG) ou ask (SHORT)."""
        if self.connecteur is None:
            return 0.0
        try:
            tick = self.connecteur.get_tick(symbole)
            if tick is None:
                return 0.0
            return tick["bid"] if direction == DirectionTrade.LONG else tick["ask"]
        except Exception:
            return 0.0

    def _sauvegarder(self, trade: TradeGere) -> None:
        """Persiste l'état du trade."""
        try:
            if self._position_tracker:
                self._position_tracker.sauvegarder(trade)
        except Exception:
            pass

    def _logger_fermeture(self, trade: TradeGere) -> None:
        """Log complet de la clôture et enregistrement dans le journal."""
        phases = []
        if trade.tp1.atteint:
            phases.append("TP1(+1R×50%)")
        if trade.tp2.atteint:
            phases.append("TP2(+2R×25%)")
        if trade.tp3.atteint:
            phases.append(f"TP3(+{trade.tp3.multiple_r:.1f}R×25%)")

        emoji = "🟢" if (trade.pnl_total_usd or 0) > 0 else "🔴"
        logger.info(
            f"{emoji} Trade [{trade.id_trade}] | "
            f"{trade.raison_fermeture.value if trade.raison_fermeture else '?'} | "
            f"R: {trade.r_total_realise:+.2f}R | "
            f"P&L: ${trade.pnl_total_usd:+.2f} | "
            f"Phases: {' → '.join(phases) or 'Aucune'} | "
            f"MFE: {trade.mfe:.2f}R | MAE: {trade.mae:.2f}R"
        )

        # ── Enregistrement dans le journal de trades ──────────────────────
        if self.journal is not None:
            try:
                addon = None
                if (self.pyramiding_manager is not None
                        and trade.id_trade in self.pyramiding_manager._states):
                    etat_py = self.pyramiding_manager._states[trade.id_trade]
                    if etat_py.addon is not None:
                        addon = etat_py.addon

                self.journal.enregistrer_trade(
                    trade=trade,
                    addon=addon,
                )
            except Exception as e:
                logger.error(f"Erreur enregistrement journal trade : {e}")
