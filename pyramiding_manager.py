"""
pyramiding_manager.py — Pyramiding léger et contrôlé sur les trades gagnants.

Principe : quand TP1 du trade principal est atteint, évaluer l'ouverture
d'UN SEUL add-on risquant 0.5% du capital. Si les conditions sont remplies,
l'add-on est ouvert avec son propre SL (= breakeven du parent), son propre
TP1 (1R), son propre TP2 (2R) et un trailing stop après TP1.

L'add-on est entièrement indépendant du trade principal : si il perd,
il perd au breakeven (P&L ≈ 0$). Le trade principal n'est jamais affecté.
"""

import json
import os
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Dict, List, Optional

from loguru import logger

from addon_validator import ValidateurAddon, RaisonAnnulationAddon
from config import CONFIG
from indicators import Indicateurs
from trade_manager import TradeGere, DirectionTrade


# ── Énumérations ───────────────────────────────────────────────────────────

class StatutAddon(Enum):
    """Cycle de vie d'un add-on pyramiding."""
    EN_ATTENTE     = "EN_ATTENTE"       # Évalué, pas encore ouvert
    OUVERT         = "OUVERT"           # Add-on actif sur MT5
    FERME_GAGNANT  = "FERMÉ_GAGNANT"   # Fermé avec profit
    FERME_BE       = "FERMÉ_BREAKEVEN"  # Fermé au breakeven (≈ 0$)
    FERME_PERTE    = "FERMÉ_PERTE"      # Fermé en perte (cas exceptionnel)
    ANNULE         = "ANNULÉ"           # Conditions invalidées avant ouverture


# Alias anglais
AddonStatus = StatutAddon


# ── Dataclasses ────────────────────────────────────────────────────────────

@dataclass
class ParametresAddon:
    """
    Paramètres calculés pour un add-on potentiel.
    Produit par _calculer_entree() quand TP1 du parent est atteint.
    """
    id_trade_parent: str         # Trade principal associé
    direction: str               # "bullish" ou "bearish"
    entree_proposee: float       # Prix d'entrée proposé (market order)
    sl_propose: float            # SL = breakeven du parent (= prix_entree parent)
    tp1_propose: float           # TP1 : 1R depuis l'entrée de l'add-on
    tp2_propose: float           # TP2 : 2R depuis l'entrée de l'add-on
    lots: float                  # Calculé sur 0.5% du capital
    risque_usd: float            # Montant risqué en USD
    rr_vers_tp2_parent: float    # R:R vers le TP2 du trade principal
    ob_utilise: Optional[object] = None  # OBMultiTimeframe validant l'add-on
    score_ob: int = 0            # Score de cet OB
    calcule_a: datetime = field(default_factory=datetime.utcnow)

    # Alias anglais
    @property
    def proposed_entry(self) -> float: return self.entree_proposee
    @property
    def proposed_sl(self) -> float: return self.sl_propose
    @property
    def proposed_tp1(self) -> float: return self.tp1_propose
    @property
    def proposed_tp2(self) -> float: return self.tp2_propose
    @property
    def lot_size(self) -> float: return self.lots
    @property
    def risk_amount_usd(self) -> float: return self.risque_usd
    @property
    def ob_score(self) -> int: return self.score_ob


@dataclass
class TradeAddon:
    """
    Un add-on ouvert sur MT5, géré de façon entièrement indépendante.
    Persisté dans data/pyramiding_{parent_id}.json
    """
    addon_id: str                          # UUID court
    id_trade_parent: str                   # Trade principal
    ticket_mt5: int                        # Ticket MT5
    statut: StatutAddon = StatutAddon.OUVERT

    # Paramètres d'entrée
    prix_entree: float = 0.0
    heure_entree: datetime = field(default_factory=datetime.utcnow)
    direction: str = ""
    lots: float = 0.0
    risque_pct: float = 0.5               # Toujours 0.5%

    # Niveaux
    sl_prix: float = 0.0                  # = breakeven du trade principal
    tp1_prix: float = 0.0                 # 1R add-on
    tp2_prix: float = 0.0                 # 2R add-on
    sl_actuel: float = 0.0               # SL courant (peut monter après TP1)

    # Gestion partielle
    tp1_atteint: bool = False
    heure_tp1: Optional[datetime] = None
    lots_fermes_tp1: float = 0.0
    lots_restants: float = 0.0

    # Trailing stop (actif après TP1)
    trailing_actif: bool = False
    trailing_price: Optional[float] = None
    trailing_atr_distance: float = 0.0

    # Métriques
    pnl_realise_usd: float = 0.0
    pnl_flottant_usd: float = 0.0
    mfe: float = 0.0                      # Max Favorable Excursion en R
    raison_fermeture: Optional[RaisonAnnulationAddon] = None
    heure_fermeture: Optional[datetime] = None
    pnl_total_usd: float = 0.0
    r_total_realise: float = 0.0

    # Aliases anglais (propriétés de lecture)
    @property
    def status(self) -> StatutAddon: return self.statut
    @property
    def entry_price(self) -> float: return self.prix_entree
    @property
    def sl_price(self) -> float: return self.sl_prix
    @property
    def tp1_price(self) -> float: return self.tp1_prix
    @property
    def tp2_price(self) -> float: return self.tp2_prix
    @property
    def current_sl(self) -> float: return self.sl_actuel
    @property
    def lot_size(self) -> float: return self.lots


@dataclass
class EtatPyramiding:
    """
    État complet du pyramiding pour un trade principal.
    Un seul add-on possible par trade (addon_max_par_trade = 1).
    """
    id_trade_parent: str
    addon_evalue: bool = False          # True si l'add-on a déjà été évalué
    addon_ouvert: bool = False          # True si un add-on est actif
    addon: Optional[TradeAddon] = None
    heure_evaluation: Optional[datetime] = None
    raison_annulation: Optional[RaisonAnnulationAddon] = None

    # Stats cumulées de la session
    total_addons_ouverts: int = 0
    total_addons_gagnants: int = 0
    pnl_total_addons: float = 0.0

    # Aliases anglais
    @property
    def parent_trade_id(self) -> str: return self.id_trade_parent
    @property
    def cancel_reason(self) -> Optional[RaisonAnnulationAddon]: return self.raison_annulation
    @property
    def total_addons_opened(self) -> int: return self.total_addons_ouverts
    @property
    def total_addons_won(self) -> int: return self.total_addons_gagnants
    @property
    def total_addons_pnl_usd(self) -> float: return self.pnl_total_addons


# Alias anglais
AddonEntry = ParametresAddon
AddonTrade = TradeAddon
PyramidingState = EtatPyramiding


# ── Gestionnaire principal ─────────────────────────────────────────────────

class GestionnairePyramiding:
    """
    Gère le pyramiding léger : 1 add-on maximum par trade, 0.5% de risque.
    S'intègre dans trade_manager via on_tp1_reached() et update().

    Utilisation :
        pyramiding = GestionnairePyramiding(connecteur, validator=validator)
        pyramiding.set_contexte_marche(structure, obs_actifs, df_m15)
        addon = pyramiding.on_tp1_reached(trade_parent)
        pyramiding.update(trade_parent, df_m15)
    """

    def __init__(
        self,
        connecteur=None,
        config=None,
        validator: Optional[ValidateurAddon] = None,
        capital_initial: float = 10000.0,
    ) -> None:
        self.connecteur = connecteur
        self.config = config or CONFIG
        self.validator = validator
        self._capital_initial = capital_initial  # Utilisé en mode paper/test
        self._states: Dict[str, EtatPyramiding] = {}

        # Contexte marché — mis à jour par set_contexte_marche() à chaque boucle
        self._structure_courante = None
        self._obs_courants: List = []
        self._df_m15 = None

    # ── Contexte marché ────────────────────────────────────────────────────

    def set_contexte_marche(
        self,
        structure=None,
        obs_actifs: Optional[List] = None,
        df_m15=None,
    ) -> None:
        """
        Met à jour le contexte marché utilisé par l'évaluation des add-ons.
        Appeler à chaque itération de la boucle principale, avant gerer_positions().
        """
        self._structure_courante = structure
        self._obs_courants = obs_actifs or []
        self._df_m15 = df_m15

    # ── Évaluation et ouverture ────────────────────────────────────────────

    def on_tp1_reached(self, trade_parent: TradeGere) -> Optional[TradeAddon]:
        """
        Appelé par GestionnairePositions._gerer_tp1() quand TP1 est atteint.
        Évalue les conditions et ouvre un add-on si tout est valide.

        Returns:
            TradeAddon ouvert, ou None si conditions non remplies.
        """
        if not self.config.PYRAMIDING_ENABLED:
            return None

        parent_id = trade_parent.id_trade

        # Initialiser l'état si c'est la première fois
        if parent_id not in self._states:
            self._states[parent_id] = EtatPyramiding(id_trade_parent=parent_id)

        etat = self._states[parent_id]

        # Maximum 1 add-on par trade
        if etat.addon_evalue or etat.addon_ouvert:
            logger.debug(
                f"Pyramiding ignoré [{parent_id}] — add-on déjà évalué"
            )
            return None

        etat.addon_evalue = True
        etat.heure_evaluation = datetime.utcnow()

        # Prix actuel
        prix = self._get_prix_actuel(
            trade_parent.symbole, trade_parent.direction
        )
        if prix <= 0:
            logger.warning("Pyramiding : impossible de lire le prix actuel")
            return None

        maintenant = self._obtenir_heure_utc()

        # Validation des conditions
        if self.validator is not None:
            valide, raison, msg = self.validator.validate(
                trade_parent=trade_parent,
                prix_actuel=prix,
                heure_actuelle=maintenant,
                structure=self._structure_courante,
                obs_actifs=self._obs_courants if self._obs_courants else None,
            )
        else:
            # Pas de validator injecté — mode test permissif
            valide, raison, msg = True, None, "Pas de validator — mode test"

        if not valide:
            etat.raison_annulation = raison
            logger.info(
                f"Add-on annulé [{parent_id}] | "
                f"Raison : {raison.value if raison else '?'} | {msg}"
            )
            return None

        # Calculer les paramètres de l'add-on
        params = self._calculer_entree(trade_parent, prix)
        if params is None:
            etat.raison_annulation = RaisonAnnulationAddon.RR_INSUFFISANT
            logger.info(
                f"Add-on annulé [{parent_id}] — calcul des paramètres impossible"
            )
            return None

        # Vérifier le R:R minimal
        distance_sl = abs(params.entree_proposee - params.sl_propose)
        if distance_sl <= 0:
            etat.raison_annulation = RaisonAnnulationAddon.RR_INSUFFISANT
            return None

        rr_addon = abs(params.tp2_propose - params.entree_proposee) / distance_sl
        if rr_addon < self.config.RR_MINIMUM:
            etat.raison_annulation = RaisonAnnulationAddon.RR_INSUFFISANT
            logger.info(
                f"Add-on annulé [{parent_id}] — R:R insuffisant : "
                f"{rr_addon:.2f} < {self.config.RR_MINIMUM}"
            )
            return None

        # Ouvrir l'add-on
        addon = self._ouvrir_addon(trade_parent, params)
        if addon is None:
            logger.error(f"Ouverture add-on échouée [{parent_id}]")
            return None

        etat.addon_ouvert = True
        etat.addon = addon
        etat.total_addons_ouverts += 1
        self._sauvegarder(etat)

        logger.success(
            f"🔺 Add-on ouvert [{addon.addon_id}] → parent [{parent_id}] | "
            f"Direction : {addon.direction} | "
            f"Lots : {addon.lots} (0.5% risque) | "
            f"Entry : {addon.prix_entree:.2f} | "
            f"SL : {addon.sl_prix:.2f} (BE parent) | "
            f"TP1 : {addon.tp1_prix:.2f} | TP2 : {addon.tp2_prix:.2f} | "
            f"OB Score : {params.score_ob}/100"
        )
        return addon

    def _calculer_entree(
        self,
        trade_parent: TradeGere,
        prix_actuel: float,
    ) -> Optional[ParametresAddon]:
        """
        Calcule les paramètres de l'add-on.

        Règles :
        - Entrée   : prix actuel (market order)
        - SL       : breakeven du trade principal (= prix_entree du parent)
        - TP1      : 1R depuis l'entrée de l'add-on (50% fermé ici)
        - TP2      : 2R depuis l'entrée de l'add-on (trailing ensuite)
        - Lots     : 0.5% du capital / distance SL
        """
        # SL de l'add-on = breakeven du parent
        sl_addon = trade_parent.prix_entree
        distance_sl = abs(prix_actuel - sl_addon)

        if distance_sl < 0.10:
            logger.warning(
                f"Distance SL add-on trop petite : {distance_sl:.2f}$ "
                f"— prix trop proche du breakeven parent"
            )
            return None

        est_long = trade_parent.est_long()

        if est_long:
            tp1 = prix_actuel + distance_sl * 1.0
            tp2 = prix_actuel + distance_sl * 2.0
        else:
            tp1 = prix_actuel - distance_sl * 1.0
            tp2 = prix_actuel - distance_sl * 2.0

        # R:R vers le TP2 du trade principal
        rr_parent_tp2 = abs(trade_parent.tp2.prix - prix_actuel) / distance_sl

        # Calcul du lot size sur 0.5% du capital
        capital = self._get_capital()
        risque_usd = capital * (self.config.ADDON_RISQUE_PCT / 100.0)  # 0.5%

        # XAUUSD : 1 lot = 100$/$ de mouvement (règle métier bot)
        lots = risque_usd / (distance_sl * 100.0)
        lots = max(0.01, round(round(lots / 0.01) * 0.01, 2))

        # Vérifier que la taille permet les partiaux (≥ 2 × lot_min)
        if lots < 0.02:
            logger.warning(
                f"Lot add-on trop petit pour les partiaux : {lots} lots"
            )
            return None

        # Trouver l'OB utilisé (le meilleur proche)
        ob_proche = self._trouver_ob_proche(est_long, prix_actuel)

        return ParametresAddon(
            id_trade_parent=trade_parent.id_trade,
            direction="bullish" if est_long else "bearish",
            entree_proposee=round(prix_actuel, 2),
            sl_propose=round(sl_addon, 2),
            tp1_propose=round(tp1, 2),
            tp2_propose=round(tp2, 2),
            lots=lots,
            risque_usd=round(risque_usd, 2),
            rr_vers_tp2_parent=round(rr_parent_tp2, 2),
            ob_utilise=ob_proche,
            score_ob=getattr(ob_proche, "score", 0) if ob_proche else 0,
        )

    def _ouvrir_addon(
        self,
        trade_parent: TradeGere,
        params: ParametresAddon,
    ) -> Optional[TradeAddon]:
        """
        Ouvre l'add-on sur MT5 (mode live) ou simule l'ouverture (mode paper).
        Retourne le TradeAddon créé ou None si échec.
        """
        addon_id = str(uuid.uuid4())[:8]
        est_long = params.direction == "bullish"

        if self.connecteur is None:
            # ── Mode paper / test ──────────────────────────────────────────
            addon = TradeAddon(
                addon_id=addon_id,
                id_trade_parent=params.id_trade_parent,
                ticket_mt5=90000 + int(addon_id[:4], 16) % 9999,
                statut=StatutAddon.OUVERT,
                prix_entree=params.entree_proposee,
                heure_entree=datetime.utcnow(),
                direction=params.direction,
                lots=params.lots,
                sl_prix=params.sl_propose,
                tp1_prix=params.tp1_propose,
                tp2_prix=params.tp2_propose,
                sl_actuel=params.sl_propose,
                lots_restants=params.lots,
            )
            return addon

        # ── Mode live MT5 ─────────────────────────────────────────────────
        try:
            import MetaTrader5 as mt5

            type_ordre = mt5.ORDER_TYPE_BUY if est_long else mt5.ORDER_TYPE_SELL
            tick = mt5.symbol_info_tick(trade_parent.symbole)
            if tick is None:
                return None
            prix_execution = tick.ask if est_long else tick.bid

            requete = {
                "action": mt5.TRADE_ACTION_DEAL,
                "symbol": trade_parent.symbole,
                "volume": params.lots,
                "type": type_ordre,
                "price": prix_execution,
                "sl": params.sl_propose,
                "tp": params.tp1_propose,   # TP MT5 = TP1 uniquement
                "deviation": 15,
                "magic": self.config.ADDON_MAGIC_NUMBER,
                "comment": f"SMC_ADDON_{params.id_trade_parent[:6]}",
                "type_time": mt5.ORDER_TIME_GTC,
                "type_filling": mt5.ORDER_FILLING_IOC,
            }

            for tentative in range(1, 4):
                resultat = mt5.order_send(requete)
                if resultat and resultat.retcode == mt5.TRADE_RETCODE_DONE:
                    addon = TradeAddon(
                        addon_id=addon_id,
                        id_trade_parent=params.id_trade_parent,
                        ticket_mt5=resultat.order,
                        statut=StatutAddon.OUVERT,
                        prix_entree=prix_execution,
                        heure_entree=datetime.utcnow(),
                        direction=params.direction,
                        lots=params.lots,
                        sl_prix=params.sl_propose,
                        tp1_prix=params.tp1_propose,
                        tp2_prix=params.tp2_propose,
                        sl_actuel=params.sl_propose,
                        lots_restants=params.lots,
                    )
                    return addon

                retcode = getattr(resultat, "retcode", "None") if resultat else "None"
                logger.warning(
                    f"Tentative add-on {tentative}/3 échouée — code : {retcode}"
                )
                time.sleep(tentative * 2)

            logger.error("Ouverture add-on échouée après 3 tentatives")
            return None

        except ImportError:
            logger.error("MetaTrader5 non disponible — impossible d'ouvrir l'add-on")
            return None

    # ── Gestion en cours ───────────────────────────────────────────────────

    def update(
        self,
        trade_parent: TradeGere,
        df_m15=None,
    ) -> Optional[TradeAddon]:
        """
        Met à jour l'add-on actif. Appelé à chaque bougie M1/M15 fermée.
        Gère TP1 partiel → trailing → fermeture.

        Returns:
            Le TradeAddon mis à jour (ou None si pas d'add-on actif).
        """
        if not self.config.PYRAMIDING_ENABLED:
            return None

        parent_id = trade_parent.id_trade
        etat = self._states.get(parent_id)

        if etat is None or not etat.addon_ouvert or etat.addon is None:
            return None

        addon = etat.addon
        if addon.statut != StatutAddon.OUVERT:
            return addon

        # Trade parent fermé → fermer l'add-on
        if trade_parent.est_ferme:
            self._fermer_addon_complet(
                addon, etat, self._get_prix_actuel(trade_parent.symbole, trade_parent.direction),
                RaisonAnnulationAddon.TRADE_PARENT_FERME
            )
            return addon

        # Prix actuel
        prix = self._get_prix_actuel(trade_parent.symbole, trade_parent.direction)
        if prix <= 0:
            return addon

        est_long = addon.direction == "bullish"

        # Mise à jour MFE
        distance_risque = abs(addon.prix_entree - addon.sl_prix)
        if distance_risque > 0:
            favorable = (
                (prix - addon.prix_entree) / distance_risque if est_long
                else (addon.prix_entree - prix) / distance_risque
            )
            addon.mfe = max(addon.mfe, favorable)

        # Gestion selon la phase
        df = df_m15 if df_m15 is not None else self._df_m15
        if not addon.tp1_atteint:
            self._gerer_phase1(addon, etat, prix, est_long, df)
        else:
            self._gerer_phase2(addon, etat, prix, est_long)

        # Conditions d'invalidation (Phase 1 uniquement)
        if addon.statut == StatutAddon.OUVERT and not addon.tp1_atteint:
            self._verifier_invalidation(addon, etat, trade_parent, prix)

        self._sauvegarder(etat)
        return addon

    def _gerer_phase1(
        self,
        addon: TradeAddon,
        etat: EtatPyramiding,
        prix: float,
        est_long: bool,
        df_m15=None,
    ) -> None:
        """Phase 1 : attente TP1 de l'add-on (premier 1R)."""
        tp1_atteint = (
            prix >= addon.tp1_prix if est_long else prix <= addon.tp1_prix
        )

        if tp1_atteint:
            # Fermer 50% de l'add-on
            lots_a_fermer = max(0.01, round(round(addon.lots * 0.50 / 0.01) * 0.01, 2))
            self._fermer_partiel_addon(addon, lots_a_fermer, prix, "TP1_addon")

            addon.tp1_atteint = True
            addon.heure_tp1 = datetime.utcnow()
            addon.lots_fermes_tp1 = lots_a_fermer
            addon.lots_restants = max(0.0, round(addon.lots - lots_a_fermer, 2))

            # SL → breakeven de l'add-on (= entry de l'add-on)
            self._deplacer_sl_addon(addon, addon.prix_entree, "BE add-on après TP1")

            # Activer le trailing stop
            atr = self._calculer_atr(df_m15)
            addon.trailing_atr_distance = atr * self.config.ADDON_TRAILING_ATR_MULT
            addon.trailing_actif = True
            addon.trailing_price = (
                prix - addon.trailing_atr_distance if est_long
                else prix + addon.trailing_atr_distance
            )

            logger.success(
                f"🔺 Add-on TP1 [{addon.addon_id}] @ {prix:.2f} | "
                f"50% fermé ({lots_a_fermer} lots) | "
                f"SL → BE @ {addon.prix_entree:.2f} | "
                f"Trailing @ {addon.trailing_price:.2f}"
            )

        elif (est_long and prix <= addon.sl_actuel) or \
             (not est_long and prix >= addon.sl_actuel):
            # SL touché avant TP1
            pnl = self._calculer_pnl(addon, addon.lots_restants, prix)
            addon.pnl_total_usd = addon.pnl_realise_usd + pnl
            addon.statut = (
                StatutAddon.FERME_BE
                if abs(addon.pnl_total_usd) < 2.0
                else StatutAddon.FERME_PERTE
            )
            addon.heure_fermeture = datetime.utcnow()
            self._finaliser_addon(addon, etat, prix, "SL avant TP1")

    def _gerer_phase2(
        self,
        addon: TradeAddon,
        etat: EtatPyramiding,
        prix: float,
        est_long: bool,
    ) -> None:
        """Phase 2 : TP1 atteint, trailing actif sur les 50% restants."""
        if addon.lots_restants <= 0:
            addon.statut = StatutAddon.FERME_GAGNANT
            addon.heure_fermeture = datetime.utcnow()
            return

        # TP2 atteint ?
        tp2_atteint = (
            prix >= addon.tp2_prix if est_long else prix <= addon.tp2_prix
        )
        if tp2_atteint:
            self._fermer_addon_complet(addon, etat, prix, None)
            return

        # Trailing stop déclenché ?
        if addon.trailing_actif and addon.trailing_price is not None:
            trailing_declenche = (
                (est_long and prix <= addon.trailing_price)
                or (not est_long and prix >= addon.trailing_price)
            )
            if trailing_declenche:
                self._fermer_addon_complet(addon, etat, prix, None)
                return

            # Mettre à jour le trailing (ne peut que progresser en faveur)
            self._maj_trailing_addon(addon, prix, est_long)

        # SL (BE add-on) touché en Phase 2 ?
        sl_touche = (
            (est_long and prix <= addon.sl_actuel)
            or (not est_long and prix >= addon.sl_actuel)
        )
        if sl_touche:
            self._fermer_addon_complet(addon, etat, prix, None)

    def _maj_trailing_addon(
        self,
        addon: TradeAddon,
        prix: float,
        est_long: bool,
    ) -> None:
        """Met à jour le trailing stop de l'add-on. Ne peut jamais reculer."""
        if not addon.trailing_actif or addon.trailing_price is None:
            return

        if est_long:
            nouveau = prix - addon.trailing_atr_distance
            if nouveau > addon.trailing_price:
                addon.trailing_price = round(nouveau, 2)
                logger.debug(
                    f"Trailing add-on [{addon.addon_id}] ↑ → {addon.trailing_price:.2f}"
                )
        else:
            nouveau = prix + addon.trailing_atr_distance
            if nouveau < addon.trailing_price:
                addon.trailing_price = round(nouveau, 2)
                logger.debug(
                    f"Trailing add-on [{addon.addon_id}] ↓ → {addon.trailing_price:.2f}"
                )

    def _verifier_invalidation(
        self,
        addon: TradeAddon,
        etat: EtatPyramiding,
        trade_parent: TradeGere,
        prix: float,
    ) -> None:
        """
        Vérifie les conditions d'invalidation pendant la Phase 1.
        En Phase 2, le BE + trailing gèrent tout — pas besoin de check externe.
        """
        maintenant = datetime.utcnow()

        # News imminente
        if self.validator and self.validator.filtre_news:
            try:
                news_ok, raison = self.validator.filtre_news.is_trading_allowed(maintenant)
                if not news_ok:
                    logger.warning(f"Add-on fermé d'urgence — news : {raison}")
                    self._fermer_addon_complet(
                        addon, etat, prix, RaisonAnnulationAddon.NEWS_IMMINENTE
                    )
                    return
            except Exception:
                pass

        # Structure inversée
        if self._structure_courante is not None:
            try:
                from structure_analyzer import Tendance
                tendance = getattr(self._structure_courante, "tendance", None)
                est_long = addon.direction == "bullish"
                if tendance is not None:
                    ok = (
                        (est_long and tendance == Tendance.HAUSSIERE)
                        or (not est_long and tendance == Tendance.BAISSIERE)
                    )
                    if not ok:
                        logger.warning(
                            f"Add-on fermé — structure inversée : {tendance.value}"
                        )
                        self._fermer_addon_complet(
                            addon, etat, prix, RaisonAnnulationAddon.STRUCTURE_INVERSEE
                        )
                        return
            except Exception:
                pass

        # Fin de session
        heure = maintenant.hour
        if not (self.config.SESSION_LONDON_OUVERTURE <= heure < self.config.SESSION_FERMETURE):
            logger.warning(f"Add-on fermé — fin de session ({heure}h UTC)")
            self._fermer_addon_complet(
                addon, etat, prix, RaisonAnnulationAddon.HORS_SESSION
            )

    # ── Fermeture ──────────────────────────────────────────────────────────

    def _fermer_partiel_addon(
        self,
        addon: TradeAddon,
        lots: float,
        prix: float,
        label: str,
    ) -> None:
        """Fermeture partielle de l'add-on (mode paper ou MT5 live)."""
        pnl = self._calculer_pnl(addon, lots, prix)
        addon.pnl_realise_usd += pnl

        if self.connecteur is not None:
            try:
                import MetaTrader5 as mt5
                est_long = addon.direction == "bullish"
                type_fermeture = mt5.ORDER_TYPE_SELL if est_long else mt5.ORDER_TYPE_BUY
                tick = mt5.symbol_info_tick(self.config.SYMBOLE)
                prix_exec = tick.bid if est_long else tick.ask

                requete = {
                    "action": mt5.TRADE_ACTION_DEAL,
                    "symbol": self.config.SYMBOLE,
                    "volume": lots,
                    "type": type_fermeture,
                    "position": addon.ticket_mt5,
                    "price": prix_exec,
                    "deviation": 15,
                    "magic": self.config.ADDON_MAGIC_NUMBER,
                    "comment": f"SMC_ADDON_{label}",
                    "type_time": mt5.ORDER_TIME_GTC,
                    "type_filling": mt5.ORDER_FILLING_IOC,
                }

                for tentative in range(1, 4):
                    r = mt5.order_send(requete)
                    if r and r.retcode == mt5.TRADE_RETCODE_DONE:
                        break
                    time.sleep(tentative * 2)
            except Exception as e:
                logger.error(f"Fermeture partielle add-on échouée : {e}")

        logger.info(
            f"Partiel add-on {label} [{addon.addon_id}] | "
            f"{lots} lots @ {prix:.2f} | P&L : {pnl:+.2f}$"
        )

    def _fermer_addon_complet(
        self,
        addon: TradeAddon,
        etat: EtatPyramiding,
        prix: float,
        raison: Optional[RaisonAnnulationAddon],
    ) -> None:
        """Ferme tous les lots restants de l'add-on et finalise les métriques."""
        if addon.lots_restants <= 0:
            addon.statut = StatutAddon.FERME_GAGNANT
            addon.heure_fermeture = datetime.utcnow()
            return

        pnl = self._calculer_pnl(addon, addon.lots_restants, prix)
        addon.pnl_realise_usd += pnl
        addon.pnl_total_usd = addon.pnl_realise_usd

        if self.connecteur is not None:
            try:
                import MetaTrader5 as mt5
                est_long = addon.direction == "bullish"
                type_fermeture = mt5.ORDER_TYPE_SELL if est_long else mt5.ORDER_TYPE_BUY
                tick = mt5.symbol_info_tick(self.config.SYMBOLE)
                prix_exec = tick.bid if est_long else tick.ask

                label_raison = raison.value[:8] if raison else "FIN"
                requete = {
                    "action": mt5.TRADE_ACTION_DEAL,
                    "symbol": self.config.SYMBOLE,
                    "volume": addon.lots_restants,
                    "type": type_fermeture,
                    "position": addon.ticket_mt5,
                    "price": prix_exec,
                    "deviation": 15,
                    "magic": self.config.ADDON_MAGIC_NUMBER,
                    "comment": f"SMC_ADDON_{label_raison}",
                    "type_time": mt5.ORDER_TIME_GTC,
                    "type_filling": mt5.ORDER_FILLING_IOC,
                }

                for tentative in range(1, 4):
                    r = mt5.order_send(requete)
                    if r and r.retcode == mt5.TRADE_RETCODE_DONE:
                        break
                    time.sleep(tentative * 2)
            except Exception as e:
                logger.error(f"Fermeture complète add-on échouée : {e}")

        self._finaliser_addon(addon, etat, prix, raison.value if raison else "TP/Trailing")

    def _finaliser_addon(
        self,
        addon: TradeAddon,
        etat: EtatPyramiding,
        prix: float,
        raison_str: str,
    ) -> None:
        """Calcule les métriques finales et met à jour l'état."""
        addon.lots_restants = 0.0
        addon.heure_fermeture = datetime.utcnow()

        distance_risque = abs(addon.prix_entree - addon.sl_prix)
        if distance_risque > 0:
            addon.r_total_realise = addon.pnl_total_usd / (distance_risque * addon.lots * 100.0)

        if addon.statut == StatutAddon.OUVERT:
            if addon.pnl_total_usd > 1.0:
                addon.statut = StatutAddon.FERME_GAGNANT
            elif abs(addon.pnl_total_usd) <= 1.0:
                addon.statut = StatutAddon.FERME_BE
            else:
                addon.statut = StatutAddon.FERME_PERTE

        etat.pnl_total_addons += addon.pnl_total_usd
        if addon.statut == StatutAddon.FERME_GAGNANT:
            etat.total_addons_gagnants += 1

        emoji = "🟢" if addon.pnl_total_usd >= 0 else "🔴"
        logger.info(
            f"{emoji} Add-on fermé [{addon.addon_id}] | "
            f"Raison : {raison_str} | "
            f"R réalisé : {addon.r_total_realise:+.2f}R | "
            f"P&L : ${addon.pnl_total_usd:+.2f} | "
            f"MFE : {addon.mfe:.2f}R"
        )
        self._sauvegarder(etat)

    def _deplacer_sl_addon(
        self,
        addon: TradeAddon,
        nouveau_sl: float,
        raison: str,
    ) -> bool:
        """Déplace le SL de l'add-on. Ne peut jamais reculer."""
        est_long = addon.direction == "bullish"
        if est_long and nouveau_sl <= addon.sl_actuel:
            return False
        if not est_long and nouveau_sl >= addon.sl_actuel:
            return False

        if self.connecteur is not None:
            try:
                import MetaTrader5 as mt5
                requete = {
                    "action": mt5.TRADE_ACTION_SLTP,
                    "symbol": self.config.SYMBOLE,
                    "position": addon.ticket_mt5,
                    "sl": nouveau_sl,
                    "tp": addon.tp1_prix if not addon.tp1_atteint else 0.0,
                }
                for tentative in range(1, 4):
                    r = mt5.order_send(requete)
                    if r and r.retcode == mt5.TRADE_RETCODE_DONE:
                        break
                    time.sleep(tentative * 2)
            except Exception as e:
                logger.error(f"Déplacement SL add-on échoué : {e}")
                return False

        addon.sl_actuel = nouveau_sl
        logger.info(
            f"SL add-on [{addon.addon_id}] → {nouveau_sl:.2f} | {raison}"
        )
        return True

    # ── Interface publique ─────────────────────────────────────────────────

    def get_addon_pour_trade(self, id_trade_parent: str) -> Optional[TradeAddon]:
        """Retourne l'add-on actif pour un trade parent donné."""
        etat = self._states.get(id_trade_parent)
        return etat.addon if etat else None

    def get_stats_pyramiding(self) -> dict:
        """Stats globales du pyramiding pour le dashboard."""
        total_ouverts = sum(e.total_addons_ouverts for e in self._states.values())
        total_gagnants = sum(e.total_addons_gagnants for e in self._states.values())
        pnl_total = sum(e.pnl_total_addons for e in self._states.values())
        addons_actifs = sum(
            1 for e in self._states.values()
            if e.addon and e.addon.statut == StatutAddon.OUVERT
        )
        return {
            "total_addons_ouverts": total_ouverts,
            "total_addons_gagnants": total_gagnants,
            "win_rate_pct": (
                total_gagnants / total_ouverts * 100
                if total_ouverts > 0 else 0.0
            ),
            "pnl_total_usd": round(pnl_total, 2),
            "addons_actifs": addons_actifs,
        }

    # Alias anglais
    def get_addon_for_trade(self, parent_trade_id: str) -> Optional[TradeAddon]:
        return self.get_addon_pour_trade(parent_trade_id)

    def get_pyramiding_stats(self) -> dict:
        return self.get_stats_pyramiding()

    # ── Utilitaires internes ───────────────────────────────────────────────

    def _get_prix_actuel(self, symbole: str, direction) -> float:
        """Prix bid (LONG) ou ask (SHORT)."""
        if self.connecteur is None:
            return 2010.0  # Prix fictif en mode paper/test

        try:
            tick = self.connecteur.get_tick(symbole)
            if tick is None:
                return 0.0
            est_long = (
                direction == DirectionTrade.LONG
                or direction == "bullish"
            )
            return tick["bid"] if est_long else tick["ask"]
        except Exception:
            return 0.0

    def _get_capital(self) -> float:
        """Capital en USD du compte. Fallback sur capital_initial en paper mode."""
        if self.connecteur is None:
            return self._capital_initial

        try:
            import MetaTrader5 as mt5
            account = mt5.account_info()
            if account is None:
                return self._capital_initial
            return float(account.balance)
        except (TypeError, AttributeError, ImportError, ValueError):
            return self._capital_initial

    def _calculer_atr(self, df_m15=None) -> float:
        """ATR(14) M15 courant, utilisé pour le trailing de l'add-on."""
        df = df_m15 if df_m15 is not None else self._df_m15
        try:
            if df is not None and len(df) >= 15:
                serie = Indicateurs.atr(df, self.config.ATR_PERIODE)
                val = float(serie.iloc[-2])
                return val if val > 0 else 5.0
        except Exception:
            pass
        return 5.0  # Valeur par défaut XAUUSD

    def _calculer_pnl(
        self,
        addon: TradeAddon,
        lots: float,
        prix_fermeture: float,
    ) -> float:
        """P&L USD d'une tranche. XAUUSD : 1 lot = 100$/$ de mouvement."""
        if lots <= 0:
            return 0.0
        est_long = addon.direction == "bullish"
        diff = (
            prix_fermeture - addon.prix_entree if est_long
            else addon.prix_entree - prix_fermeture
        )
        return round(diff * lots * 100.0, 2)

    def _trouver_ob_proche(
        self,
        est_long: bool,
        prix: float,
        tolerance: float = 0.005,
    ) -> Optional[object]:
        """Trouve le meilleur OB proche du prix actuel dans le contexte courant."""
        if not self._obs_courants:
            return None
        try:
            from ob_detector import TypeOB
            obs_direction = [
                ob for ob in self._obs_courants
                if (est_long and getattr(ob, "ob_type", None) == TypeOB.HAUSSIER)
                or (not est_long and getattr(ob, "ob_type", None) == TypeOB.BAISSIER)
            ]
            obs_proches = [
                ob for ob in obs_direction
                if abs(getattr(ob, "entry_zone_mid", prix) - prix) / prix < tolerance
            ]
            if not obs_proches:
                return None
            return max(obs_proches, key=lambda ob: getattr(ob, "score", 0))
        except Exception:
            return None

    def _obtenir_heure_utc(self) -> datetime:
        """Retourne l'heure UTC actuelle. Surchargeable dans les tests."""
        return datetime.utcnow()

    def _sauvegarder(self, etat: EtatPyramiding) -> None:
        """Persiste l'état en JSON."""
        try:
            os.makedirs("data", exist_ok=True)
            chemin = f"data/pyramiding_{etat.id_trade_parent}.json"
            with open(chemin, "w", encoding="utf-8") as f:
                json.dump(self._serialiser(etat), f, indent=2, default=str)
        except Exception as e:
            logger.debug(f"Sauvegarde pyramiding échouée : {e}")

    def _serialiser(self, etat: EtatPyramiding) -> dict:
        """Sérialise l'état en dictionnaire JSON-compatible."""
        addon_dict = None
        if etat.addon is not None:
            a = etat.addon
            addon_dict = {
                "addon_id": a.addon_id,
                "id_trade_parent": a.id_trade_parent,
                "ticket_mt5": a.ticket_mt5,
                "statut": a.statut.value,
                "direction": a.direction,
                "prix_entree": a.prix_entree,
                "lots": a.lots,
                "sl_prix": a.sl_prix,
                "tp1_prix": a.tp1_prix,
                "tp2_prix": a.tp2_prix,
                "sl_actuel": a.sl_actuel,
                "tp1_atteint": a.tp1_atteint,
                "trailing_actif": a.trailing_actif,
                "trailing_price": a.trailing_price,
                "trailing_atr_distance": a.trailing_atr_distance,
                "pnl_realise_usd": a.pnl_realise_usd,
                "pnl_total_usd": a.pnl_total_usd,
                "r_total_realise": a.r_total_realise,
                "mfe": a.mfe,
                "lots_restants": a.lots_restants,
            }
        return {
            "id_trade_parent": etat.id_trade_parent,
            "addon_evalue": etat.addon_evalue,
            "addon_ouvert": etat.addon_ouvert,
            "total_addons_ouverts": etat.total_addons_ouverts,
            "total_addons_gagnants": etat.total_addons_gagnants,
            "pnl_total_addons": etat.pnl_total_addons,
            "addon": addon_dict,
        }


# Alias anglais
PyramidingManager = GestionnairePyramiding
