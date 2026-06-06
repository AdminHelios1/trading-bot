"""
slippage_simulator.py — Simulation réaliste du slippage et de la latence
pour le mode paper trading XAUUSD.

Basé sur des données empiriques de brokers ECN/STP sur XAUUSD :
- Slippage moyen en conditions normales : 2–4 points
- Slippage sur news (NFP, Fed) : 5–25 points
- Latence broker ECN : 80–150ms en moyenne
- Probabilité de requote : ~3% normal, 25% sur news

Ce module n'est JAMAIS instancié en mode live.
En mode live, mt5_connector.placer_ordre() appelle directement MT5.
"""

import csv
import json
import os
import random
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Dict, List, Optional

from loguru import logger

from config import CONFIG
from paper_order_book import CarnetOrdresPaper


# ── Énumérations ───────────────────────────────────────────────────────────

class QualiteExecution(Enum):
    """Qualité d'une exécution simulée."""
    PARFAIT  = "PARFAIT"        # Aucun slippage (< 5% des cas)
    BON      = "BON"            # Slippage 1–2 pts (conditions normales)
    MOYEN    = "MOYEN"          # Slippage 3–5 pts (légèrement volatil)
    MAUVAIS  = "MAUVAIS"        # Slippage 6+ pts (très volatil / news)
    REQUOTE  = "REQUOTÉ"        # Ordre refusé, nouveau prix proposé
    PARTIEL  = "PARTIEL"        # Ordre partiellement exécuté


class ConditionMarche(Enum):
    """Condition de marché au moment de l'exécution."""
    CALME     = "CALME"         # Spread normal, faible volatilité
    NORMAL    = "NORMAL"        # Conditions standard
    VOLATIL   = "VOLATIL"       # Spread élargi, slippage probable
    NEWS      = "NEWS"          # Annonce économique — exécution dégradée
    OUVERTURE = "OUVERTURE"     # London/NY Open — spread transitoire


# Aliases anglais
ExecutionQuality = QualiteExecution
MarketCondition = ConditionMarche


# ── Dataclasses ────────────────────────────────────────────────────────────

@dataclass
class ProfilSlippage:
    """Paramètres de simulation selon la condition de marché."""
    condition: ConditionMarche
    slippage_min_pts: float
    slippage_max_pts: float
    latence_min_ms: int
    latence_max_ms: int
    proba_requote: float            # 0.0–1.0
    proba_partial_fill: float       # 0.0–1.0
    description: str

    # Aliases anglais
    @property
    def latency_min_ms(self) -> int: return self.latence_min_ms
    @property
    def latency_max_ms(self) -> int: return self.latence_max_ms
    @property
    def requote_probability(self) -> float: return self.proba_requote
    @property
    def partial_fill_probability(self) -> float: return self.proba_partial_fill


@dataclass
class ExecutionSimulee:
    """Résultat d'une exécution simulée — imite la structure order_send() MT5."""
    # Ordre demandé
    order_id: str
    requested_price: float
    requested_lots: float
    order_type: str                 # "BUY" ou "SELL"
    symbol: str
    requested_at: datetime

    # Exécution
    executed_price: float
    executed_lots: float
    execution_quality: QualiteExecution
    market_condition: ConditionMarche

    # Détails
    slippage_pts: float
    slippage_usd: float
    latency_ms: int
    spread_pts: float
    price_moved_during_latency: float

    # Requote
    was_requoted: bool = False
    requote_price: Optional[float] = None
    requote_accepted: bool = False

    # Partial fill
    is_partial: bool = False
    remaining_lots: float = 0.0

    # Métriques
    execution_cost_usd: float = 0.0
    executed_at: datetime = field(default_factory=datetime.utcnow)


@dataclass
class RapportSlippage:
    """Rapport de simulation généré à la fin du paper trading."""
    periode_debut: datetime
    periode_fin: datetime
    total_executions: int
    total_ordres: int

    # Distribution qualité
    nb_parfait: int
    nb_bon: int
    nb_moyen: int
    nb_mauvais: int
    nb_requotes: int
    nb_partiels: int

    # Stats slippage
    slippage_moyen_pts: float
    slippage_max_pts: float
    slippage_total_usd: float
    latence_moyenne_ms: float

    # Impact
    impact_live_estime_pct: float
    impact_live_pire_pct: float

    # Par condition
    par_condition: Dict[str, dict]

    # Exécutions individuelles
    executions: List[ExecutionSimulee]

    # Aliases anglais
    @property
    def avg_slippage_pts(self) -> float: return self.slippage_moyen_pts
    @property
    def max_slippage_pts(self) -> float: return self.slippage_max_pts
    @property
    def total_slippage_usd(self) -> float: return self.slippage_total_usd
    @property
    def avg_latency_ms(self) -> float: return self.latence_moyenne_ms
    @property
    def estimated_live_pnl_reduction_pct(self) -> float: return self.impact_live_estime_pct
    @property
    def worst_case_pnl_reduction_pct(self) -> float: return self.impact_live_pire_pct
    @property
    def perfect_count(self) -> int: return self.nb_parfait
    @property
    def good_count(self) -> int: return self.nb_bon
    @property
    def average_count(self) -> int: return self.nb_moyen
    @property
    def poor_count(self) -> int: return self.nb_mauvais
    @property
    def requoted_count(self) -> int: return self.nb_requotes
    @property
    def partial_count(self) -> int: return self.nb_partiels


# Alias anglais
SlippageReport = RapportSlippage
SimulatedExecution = ExecutionSimulee


# ── Simulateur principal ───────────────────────────────────────────────────

class SimulateurSlippage:
    """
    Simule le comportement réaliste d'un broker live sur XAUUSD.
    Utilisé UNIQUEMENT en mode paper trading.

    Caractéristiques :
    - Slippage TOUJOURS défavorable (jamais de prix amélioré)
    - Distribution beta (biaisée vers les petits slippages)
    - Latence variable selon la condition de marché
    - Requote simulé avec décision accept/reject
    - Partial fill sur gros lots
    """

    # ── Profils de slippage ────────────────────────────────────────────────

    PROFILS: Dict[ConditionMarche, ProfilSlippage] = {
        ConditionMarche.CALME: ProfilSlippage(
            condition=ConditionMarche.CALME,
            slippage_min_pts=0.0,
            slippage_max_pts=2.0,
            latence_min_ms=50,
            latence_max_ms=120,
            proba_requote=0.01,
            proba_partial_fill=0.02,
            description="Marché calme — exécution quasi-parfaite",
        ),
        ConditionMarche.NORMAL: ProfilSlippage(
            condition=ConditionMarche.NORMAL,
            slippage_min_pts=1.0,
            slippage_max_pts=4.0,
            latence_min_ms=80,
            latence_max_ms=180,
            proba_requote=0.03,
            proba_partial_fill=0.05,
            description="Conditions normales de trading",
        ),
        ConditionMarche.VOLATIL: ProfilSlippage(
            condition=ConditionMarche.VOLATIL,
            slippage_min_pts=3.0,
            slippage_max_pts=8.0,
            latence_min_ms=120,
            latence_max_ms=300,
            proba_requote=0.10,
            proba_partial_fill=0.12,
            description="Marché volatil — slippage élevé probable",
        ),
        ConditionMarche.NEWS: ProfilSlippage(
            condition=ConditionMarche.NEWS,
            slippage_min_pts=5.0,
            slippage_max_pts=25.0,
            latence_min_ms=200,
            latence_max_ms=500,
            proba_requote=0.25,
            proba_partial_fill=0.20,
            description="Annonce économique — exécution très dégradée",
        ),
        ConditionMarche.OUVERTURE: ProfilSlippage(
            condition=ConditionMarche.OUVERTURE,
            slippage_min_pts=2.0,
            slippage_max_pts=6.0,
            latence_min_ms=100,
            latence_max_ms=250,
            proba_requote=0.06,
            proba_partial_fill=0.08,
            description="Ouverture de session — spread transitoire élevé",
        ),
    }

    # ── Profil spread horaire (données empiriques XAUUSD ECN) ─────────────

    SPREAD_PAR_HEURE: Dict[int, float] = {
        0: 18.0, 1: 22.0, 2: 20.0, 3: 18.0, 4: 16.0, 5: 15.0,
        6: 14.0, 7: 12.0, 8: 10.0, 9:  9.0, 10:  8.0, 11:  8.0,
        12:  9.0, 13: 10.0, 14:  9.0, 15:  8.0, 16:  8.0, 17:  9.0,
        18: 10.0, 19: 12.0, 20: 14.0, 21: 18.0, 22: 25.0, 23: 20.0,
    }

    # Valeur en $ par point par lot (XAUUSD : 1 point = 0.01$, 1 lot = 100 oz)
    XAUUSD_VALEUR_POINT_PAR_LOT: float = 1.0

    def __init__(
        self,
        connecteur=None,
        moniteur_spread=None,
        filtre_news=None,
        config=None,
        seed: int = 0,
    ) -> None:
        """
        Args:
            connecteur    : ConnecteurMT5 (pour données OHLCV si disponible).
            moniteur_spread: MoniteurSpread pour le spread réel.
            filtre_news   : FiltreNews pour détecter les annonces.
            config        : Config globale.
            seed          : Graine aléatoire (0 = aléatoire, >0 = reproductible).
        """
        self.connecteur = connecteur
        self.moniteur_spread = moniteur_spread
        self.filtre_news = filtre_news
        self.config = config or CONFIG

        # RNG isolé : ne pollue pas le random global
        self._rng = random.Random(seed if seed > 0 else None)

        self._executions: List[ExecutionSimulee] = []
        self._carnet = CarnetOrdresPaper(self.config)

        # ── Attributs de contrôle pour les tests (NE PAS utiliser en prod) ─
        self._test_force_requote: bool = False
        self._test_force_partial: bool = False
        self._test_force_condition: Optional[ConditionMarche] = None
        self._test_requote_deviation_pts: Optional[float] = None

        logger.info(
            f"SlippageSimulateur initialisé — MODE PAPER TRADING | "
            f"Seed: {'aléatoire' if seed == 0 else seed}"
        )

    # ── Interface principale ───────────────────────────────────────────────

    def simulate_order(
        self,
        order_type: str,
        requested_price: float,
        lots: float,
        sl: float,
        tp: float,
        symbol: str,
        comment: str = "",
    ) -> ExecutionSimulee:
        """
        Simule l'exécution d'un ordre. Remplace mt5.order_send() en mode paper.

        Args:
            order_type     : "BUY" ou "SELL".
            requested_price: Prix demandé par le bot (bid ou ask courant).
            lots           : Volume demandé en lots.
            sl             : Stop Loss demandé.
            tp             : Take Profit demandé.
            symbol         : Symbole (ex: "XAUUSD").
            comment        : Commentaire de l'ordre.

        Returns:
            ExecutionSimulee décrivant le résultat de l'exécution simulée.
        """
        maintenant = datetime.utcnow()
        condition = self._evaluer_condition(maintenant)
        profil = self.PROFILS[condition]

        # Latence simulée
        latence_ms = self._rng.randint(profil.latence_min_ms, profil.latence_max_ms)

        # Mouvement de prix pendant la latence
        mouvement_latence = self._simuler_mouvement_latence(
            requested_price, latence_ms, condition
        )

        # Requote ?
        if self._test_force_requote or self._rng.random() < profil.proba_requote:
            return self._gerer_requote(
                order_type, requested_price, mouvement_latence,
                lots, sl, tp, symbol, condition, profil, latence_ms, maintenant
            )

        # Slippage
        slippage_pts = self._calculer_slippage(profil, lots, order_type)

        # Slippage appliqué depuis le prix DEMANDÉ (garantie de défavorabilité)
        # Le mouvement de latence est tracé séparément (informationnel)
        executed_price = self._appliquer_slippage(requested_price, slippage_pts, order_type)

        # Partial fill ?
        executed_lots = lots
        is_partial = False
        remaining_lots = 0.0

        if (self._test_force_partial or self._rng.random() < profil.proba_partial_fill) \
                and lots > 0.05:
            fill_pct = self._rng.uniform(0.60, 0.90)
            executed_lots = max(0.01, round(round(lots * fill_pct / 0.01) * 0.01, 2))
            remaining_lots = round(lots - executed_lots, 8)
            is_partial = True

        # Spread simulé
        spread_pts = self._get_spread_simule(maintenant)

        # Impact USD du slippage (XAUUSD : 1 point = ~1$/lot)
        slippage_usd = round(
            slippage_pts * executed_lots * self.XAUUSD_VALEUR_POINT_PAR_LOT, 2
        )
        # Coût total (slippage + demi-spread à l'entrée)
        cout_total = round(
            slippage_usd + spread_pts * executed_lots * self.XAUUSD_VALEUR_POINT_PAR_LOT / 2,
            2,
        )

        qualite = self._evaluer_qualite(slippage_pts, is_partial)

        execution = ExecutionSimulee(
            order_id=str(uuid.uuid4())[:8],
            requested_price=requested_price,
            requested_lots=lots,
            order_type=order_type,
            symbol=symbol,
            requested_at=maintenant,
            executed_price=round(executed_price, 2),
            executed_lots=executed_lots,
            execution_quality=qualite,
            market_condition=condition,
            slippage_pts=round(slippage_pts, 1),
            slippage_usd=slippage_usd,
            latency_ms=latence_ms,
            spread_pts=spread_pts,
            price_moved_during_latency=round(mouvement_latence, 2),
            is_partial=is_partial,
            remaining_lots=remaining_lots,
            execution_cost_usd=cout_total,
        )

        self._executions.append(execution)
        self._carnet.enregistrer(execution, sl, tp)
        self._logger_execution(execution)
        self._sauvegarder_execution(execution)

        return execution

    def simulate_sl_tp_modification(
        self,
        ticket: int,
        nouveau_sl: float,
        nouveau_tp: float,
    ) -> bool:
        """
        Simule la modification d'un SL/TP — toujours acceptée avec latence réduite.
        Les modifications de SL/TP ne génèrent pas de requote sur XAUUSD.
        """
        latence_ms = self._rng.randint(30, 100)
        logger.debug(
            f"[PAPER] Modif SL/TP #{ticket} | "
            f"SL:{nouveau_sl:.2f} TP:{nouveau_tp:.2f} | "
            f"Latence: {latence_ms}ms"
        )
        return self._carnet.modifier_sl_tp(ticket, nouveau_sl, nouveau_tp)

    def simulate_close_partial(
        self,
        ticket: int,
        lots: float,
        symbol: str,
    ) -> Optional[ExecutionSimulee]:
        """
        Simule la fermeture partielle d'une position.
        Slippage réduit de 30% par rapport à une entrée (les fermetures
        sont généralement mieux exécutées).
        """
        maintenant = datetime.utcnow()
        condition = self._evaluer_condition(maintenant)
        profil = self.PROFILS[condition]

        # Slippage réduit sur les fermetures partielles
        slippage_pts = self._calculer_slippage(profil, lots, "CLOSE") * 0.70

        pos = self._carnet.get_position(ticket)
        if pos is None:
            logger.warning(f"[PAPER] Ticket #{ticket} introuvable pour fermeture partielle")
            return None

        est_long = pos["direction"] == "BUY"
        # Prix fictif en paper mode
        prix_courant = pos["prix_entree"] + (5.0 if est_long else -5.0)
        type_fermeture = "SELL" if est_long else "BUY"
        executed_price = self._appliquer_slippage(prix_courant, slippage_pts, type_fermeture)

        slippage_usd = round(slippage_pts * lots * self.XAUUSD_VALEUR_POINT_PAR_LOT, 2)

        execution = ExecutionSimulee(
            order_id=str(uuid.uuid4())[:8],
            requested_price=prix_courant,
            requested_lots=lots,
            order_type=type_fermeture,
            symbol=symbol,
            requested_at=maintenant,
            executed_price=round(executed_price, 2),
            executed_lots=lots,
            execution_quality=self._evaluer_qualite(slippage_pts, False),
            market_condition=condition,
            slippage_pts=round(slippage_pts, 1),
            slippage_usd=slippage_usd,
            latency_ms=self._rng.randint(30, 100),
            spread_pts=self._get_spread_simule(maintenant),
            price_moved_during_latency=0.0,
        )

        self._executions.append(execution)
        self._carnet.fermer_partiel(ticket, lots, executed_price)
        return execution

    # ── Évaluation de la condition de marché ──────────────────────────────

    def _evaluer_condition(self, maintenant: datetime) -> ConditionMarche:
        """
        Détermine la condition de marché pour choisir le profil de slippage.
        Priorité : test_override → news → ouverture session → spread élevé → calme/normal.
        """
        # Override pour les tests
        if self._test_force_condition is not None:
            return self._test_force_condition

        heure = maintenant.hour
        minute = maintenant.minute

        # 1. News → exécution très dégradée
        if self.filtre_news is not None:
            try:
                news_ok, _ = self.filtre_news.is_trading_allowed(maintenant)
                if not news_ok:
                    return ConditionMarche.NEWS
            except Exception:
                pass

        # 2. London Open (07h45–08h30 UTC) ou NY Open (13h45–14h30 UTC)
        if (heure == 7 and minute >= 45) or (heure == 8 and minute <= 30):
            return ConditionMarche.OUVERTURE
        if (heure == 13 and minute >= 45) or (heure == 14 and minute <= 30):
            return ConditionMarche.OUVERTURE

        # 3. Spread élevé → volatil
        if self.moniteur_spread is not None:
            try:
                spread_actuel = self.moniteur_spread.get_spread_actuel()
                spread_moyen = self.moniteur_spread.get_spread_moyen(minutes=50)
                if spread_moyen > 0 and spread_actuel > spread_moyen * 1.8:
                    return ConditionMarche.VOLATIL
            except Exception:
                pass

        # 4. Session asiatique peu liquide → calme
        if 1 <= heure <= 6:
            return ConditionMarche.CALME

        return ConditionMarche.NORMAL

    # Alias anglais
    def _assess_market_condition(self, now: datetime) -> ConditionMarche:
        return self._evaluer_condition(now)

    # ── Calcul du slippage ─────────────────────────────────────────────────

    def _calculer_slippage(
        self,
        profil: ProfilSlippage,
        lots: float,
        order_type: str,
    ) -> float:
        """
        Calcule le slippage via une distribution beta biaisée vers les petites valeurs.
        La distribution Beta(1.5, 4.0) donne ~75% des valeurs dans le premier tiers —
        ce qui reflète la réalité : la plupart des trades ont un petit slippage.

        Ajustement lot-size : gros lots → plus difficile à exécuter.
        """
        # Distribution Beta(1.5, 4.0) → majorité dans le bas de la plage
        beta_sample = self._beta_distribution(1.5, 4.0)

        plage = profil.slippage_max_pts - profil.slippage_min_pts
        slippage_base = profil.slippage_min_pts + beta_sample * plage

        # Ajustement selon la taille des lots (gros lots → plus de slippage)
        if lots >= 0.5:
            mult_lots = 1.0 + (lots - 0.5) * 0.3
            slippage_base *= min(mult_lots, 2.0)

        # Variation aléatoire finale ±15%
        variation = self._rng.uniform(0.85, 1.15)
        return max(0.0, round(slippage_base * variation, 1))

    def _beta_distribution(self, alpha: float, beta: float) -> float:
        """
        Distribution Beta(alpha, beta) via la méthode de Johnk.
        Retourne une valeur dans [0.0, 1.0].

        Pour alpha=1.5, beta=4.0 : distribution asymétrique biaisée
        vers les petites valeurs (mode ≈ 0.25).
        """
        for _ in range(1000):  # Limite de sécurité
            u = self._rng.random()
            v = self._rng.random()
            if u == 0 or v == 0:
                continue
            x = u ** (1.0 / alpha)
            y = v ** (1.0 / beta)
            if x + y <= 1.0:
                return x / (x + y)
        return 0.3  # Fallback si convergence trop lente

    def _appliquer_slippage(
        self,
        prix: float,
        slippage_pts: float,
        order_type: str,
    ) -> float:
        """
        Applique le slippage dans la direction TOUJOURS défavorable.
        BUY  → prix exécuté plus HAUT (on paye plus cher).
        SELL → prix exécuté plus BAS (on reçoit moins).

        Point XAUUSD = 0.01$ (1/100 de dollar).
        """
        point = 0.01  # XAUUSD

        if order_type in ("BUY", "SELL_CLOSE"):
            return prix + slippage_pts * point
        else:  # SELL, BUY_CLOSE, CLOSE
            return prix - slippage_pts * point

    def _simuler_mouvement_latence(
        self,
        prix: float,
        latence_ms: int,
        condition: ConditionMarche,
    ) -> float:
        """
        Simule le mouvement de prix pendant la latence d'exécution.
        XAUUSD typique : ~0.025% par minute en conditions normales.
        Résultat biaisé défavorablement (60% de chance de mouvement adverse).
        """
        volatilite_par_min = {
            ConditionMarche.CALME:     0.010,
            ConditionMarche.NORMAL:    0.025,
            ConditionMarche.VOLATIL:   0.060,
            ConditionMarche.NEWS:      0.150,
            ConditionMarche.OUVERTURE: 0.040,
        }

        vol = volatilite_par_min.get(condition, 0.025)
        latence_min = latence_ms / 1000.0 / 60.0
        mouvement_max = prix * vol * latence_min

        # 60% de chance de mouvement défavorable
        direction = 1 if self._rng.random() < 0.60 else -1
        return round(self._rng.uniform(0, mouvement_max) * direction, 2)

    # ── Gestion du requote ─────────────────────────────────────────────────

    def _gerer_requote(
        self,
        order_type: str,
        prix_demande: float,
        mouvement: float,
        lots: float,
        sl: float,
        tp: float,
        symbol: str,
        condition: ConditionMarche,
        profil: ProfilSlippage,
        latence_ms: int,
        maintenant: datetime,
    ) -> ExecutionSimulee:
        """
        Simule un requote : le broker rejette l'ordre et propose un nouveau prix.
        Le bot accepte automatiquement si la déviation ≤ MAX_REQUOTE_DEVIATION_PTS.
        """
        # Déviation du requote : 2–8 pts par défaut, ou override test
        if self._test_requote_deviation_pts is not None:
            # En test : déviation exacte depuis le prix demandé (sans latency move)
            deviation_pts = self._test_requote_deviation_pts
            point = 0.01
            if order_type == "BUY":
                prix_requote = round(prix_demande + deviation_pts * point, 2)
            else:
                prix_requote = round(prix_demande - deviation_pts * point, 2)
        else:
            deviation_pts = self._rng.uniform(2.0, 8.0)
            point = 0.01
            # Requote TOUJOURS défavorable — utiliser abs(mouvement) pour garantir
            # que le requote empire toujours le prix, peu importe la direction de la latence
            if order_type == "BUY":
                prix_requote = round(prix_demande + abs(mouvement) + deviation_pts * point, 2)
            else:
                prix_requote = round(prix_demande - abs(mouvement) - deviation_pts * point, 2)

        point = 0.01  # XAUUSD (redéfini pour la suite)

        # Décision d'acceptation
        max_deviation = self.config.PAPER_MAX_REQUOTE_DEVIATION_PTS
        requote_accepte = abs(prix_requote - prix_demande) / point <= max_deviation

        if requote_accepte:
            slippage_pts = round(abs(prix_requote - prix_demande) / point, 1)
            slippage_usd = round(slippage_pts * lots * self.XAUUSD_VALEUR_POINT_PAR_LOT, 2)
            logger.warning(
                f"[PAPER] Requote accepté | "
                f"Demandé:{prix_demande:.2f} → Requote:{prix_requote:.2f} "
                f"({slippage_pts:.1f} pts)"
            )
            execution = ExecutionSimulee(
                order_id=str(uuid.uuid4())[:8],
                requested_price=prix_demande,
                requested_lots=lots,
                order_type=order_type,
                symbol=symbol,
                requested_at=maintenant,
                executed_price=prix_requote,
                executed_lots=lots,
                execution_quality=QualiteExecution.REQUOTE,
                market_condition=condition,
                slippage_pts=slippage_pts,
                slippage_usd=slippage_usd,
                latency_ms=latence_ms,
                spread_pts=self._get_spread_simule(maintenant),
                price_moved_during_latency=round(mouvement, 2),
                was_requoted=True,
                requote_price=prix_requote,
                requote_accepted=True,
                execution_cost_usd=slippage_usd,
            )
            self._executions.append(execution)
            self._carnet.enregistrer(execution, sl, tp)
        else:
            logger.warning(
                f"[PAPER] Requote rejeté — déviation {deviation_pts:.1f} pts "
                f"> max {max_deviation:.1f} pts"
            )
            execution = ExecutionSimulee(
                order_id=str(uuid.uuid4())[:8],
                requested_price=prix_demande,
                requested_lots=lots,
                order_type=order_type,
                symbol=symbol,
                requested_at=maintenant,
                executed_price=prix_requote,
                executed_lots=0.0,          # Ordre refusé → lots = 0
                execution_quality=QualiteExecution.REQUOTE,
                market_condition=condition,
                slippage_pts=round(deviation_pts, 1),
                slippage_usd=0.0,
                latency_ms=latence_ms,
                spread_pts=self._get_spread_simule(maintenant),
                price_moved_during_latency=round(mouvement, 2),
                was_requoted=True,
                requote_price=prix_requote,
                requote_accepted=False,
            )
            self._executions.append(execution)

        return execution

    # ── Spread simulé ──────────────────────────────────────────────────────

    def _get_spread_simule(self, maintenant: datetime) -> float:
        """
        Spread simulé = combinaison profil horaire (60%) + spread réel (40%).
        Variation aléatoire ±10% pour le réalisme.
        """
        heure = maintenant.hour
        spread_base = self.SPREAD_PAR_HEURE.get(heure, 12.0)

        if self.moniteur_spread is not None:
            try:
                spread_reel = self.moniteur_spread.get_spread_actuel()
                if spread_reel > 0:
                    spread_base = spread_base * 0.60 + spread_reel * 0.40
            except Exception:
                pass

        variation = self._rng.uniform(0.90, 1.10)
        return round(spread_base * variation, 1)

    # ── Qualité d'exécution ────────────────────────────────────────────────

    def _evaluer_qualite(
        self,
        slippage_pts: float,
        is_partial: bool,
    ) -> QualiteExecution:
        """Détermine la qualité selon le slippage obtenu."""
        if is_partial:
            return QualiteExecution.PARTIEL
        if slippage_pts == 0.0:
            return QualiteExecution.PARFAIT
        if slippage_pts <= 2.0:
            return QualiteExecution.BON
        if slippage_pts <= 5.0:
            return QualiteExecution.MOYEN
        return QualiteExecution.MAUVAIS

    # Alias anglais
    def _assess_execution_quality(
        self, slippage_pts: float, is_partial: bool
    ) -> QualiteExecution:
        return self._evaluer_qualite(slippage_pts, is_partial)

    # ── Rapport de simulation ──────────────────────────────────────────────

    def generate_report(self) -> RapportSlippage:
        """
        Génère le rapport complet de simulation.
        Appelé automatiquement à l'arrêt du bot en mode paper
        si PAPER_RAPPORT_A_L_ARRET=True.
        """
        if not self._executions:
            raise ValueError("Aucune exécution à analyser — pas de rapport possible")

        executions = self._executions
        total = len(executions)

        # Comptage par qualité
        compteurs: Dict[QualiteExecution, int] = {q: 0 for q in QualiteExecution}
        for ex in executions:
            compteurs[ex.execution_quality] += 1

        # Stats slippage
        slippages = [ex.slippage_pts for ex in executions]
        slippage_moyen = sum(slippages) / len(slippages)
        slippage_max = max(slippages)

        # Slippage total en USD (XAUUSD : 1 pt * 1 lot = 1$)
        slippage_total_usd = sum(
            ex.slippage_pts * ex.executed_lots * self.XAUUSD_VALEUR_POINT_PAR_LOT
            for ex in executions
        )

        # Latence moyenne
        latence_moy = sum(ex.latency_ms for ex in executions) / len(executions)

        # Stats par condition de marché
        par_condition: Dict[str, dict] = {}
        for condition in ConditionMarche:
            execs_cond = [ex for ex in executions if ex.market_condition == condition]
            if execs_cond:
                par_condition[condition.value] = {
                    "nb": len(execs_cond),
                    "slippage_moyen_pts": round(
                        sum(e.slippage_pts for e in execs_cond) / len(execs_cond), 2
                    ),
                    "latence_moy_ms": round(
                        sum(e.latency_ms for e in execs_cond) / len(execs_cond), 1
                    ),
                    "nb_requotes": sum(1 for e in execs_cond if e.was_requoted),
                }

        # Impact sur la performance (base 10 000$)
        capital_ref = 10_000.0
        impact_estime_pct = round((slippage_total_usd / capital_ref) * 100, 3)

        # Pire cas : slippage maximum sur toutes les exécutions
        lots_moy = sum(ex.executed_lots for ex in executions) / len(executions)
        pire_cas_usd = slippage_max * total * lots_moy * self.XAUUSD_VALEUR_POINT_PAR_LOT
        impact_pire_pct = round((pire_cas_usd / capital_ref) * 100, 3)

        rapport = RapportSlippage(
            periode_debut=executions[0].requested_at,
            periode_fin=executions[-1].requested_at,
            total_executions=total,
            total_ordres=total + compteurs[QualiteExecution.REQUOTE],
            nb_parfait=compteurs[QualiteExecution.PARFAIT],
            nb_bon=compteurs[QualiteExecution.BON],
            nb_moyen=compteurs[QualiteExecution.MOYEN],
            nb_mauvais=compteurs[QualiteExecution.MAUVAIS],
            nb_requotes=compteurs[QualiteExecution.REQUOTE],
            nb_partiels=compteurs[QualiteExecution.PARTIEL],
            slippage_moyen_pts=round(slippage_moyen, 2),
            slippage_max_pts=round(slippage_max, 2),
            slippage_total_usd=round(slippage_total_usd, 2),
            latence_moyenne_ms=round(latence_moy, 1),
            impact_live_estime_pct=impact_estime_pct,
            impact_live_pire_pct=impact_pire_pct,
            par_condition=par_condition,
            executions=executions,
        )

        self._sauvegarder_rapport(rapport)
        self._logger_rapport(rapport)
        return rapport

    def _logger_rapport(self, rapport: RapportSlippage) -> None:
        """Log le résumé du rapport."""
        logger.info(
            f"📊 RAPPORT SLIPPAGE | "
            f"{rapport.total_executions} exécutions | "
            f"Slippage moy: {rapport.slippage_moyen_pts:.1f} pts | "
            f"Coût total: ${rapport.slippage_total_usd:.2f} | "
            f"Latence moy: {rapport.latence_moyenne_ms:.0f}ms"
        )
        logger.info(
            f"📊 Qualité : Parfait={rapport.nb_parfait} "
            f"Bon={rapport.nb_bon} Moyen={rapport.nb_moyen} "
            f"Mauvais={rapport.nb_mauvais} Requoté={rapport.nb_requotes} "
            f"Partiel={rapport.nb_partiels}"
        )
        logger.warning(
            f"📊 Impact live estimé : "
            f"-{rapport.impact_live_estime_pct:.3f}% du capital | "
            f"Pire cas : -{rapport.impact_live_pire_pct:.3f}%"
        )

    def _sauvegarder_rapport(self, rapport: RapportSlippage) -> None:
        """Sauvegarde le rapport en JSON + CSV des exécutions."""
        os.makedirs("reports", exist_ok=True)
        horodatage = datetime.utcnow().strftime("%Y%m%d_%H%M")

        # JSON résumé
        chemin_json = f"reports/slippage_report_{horodatage}.json"
        try:
            with open(chemin_json, "w", encoding="utf-8") as f:
                json.dump({
                    "resume": {
                        "total_executions": rapport.total_executions,
                        "slippage_moyen_pts": rapport.slippage_moyen_pts,
                        "slippage_max_pts": rapport.slippage_max_pts,
                        "slippage_total_usd": rapport.slippage_total_usd,
                        "latence_moy_ms": rapport.latence_moyenne_ms,
                        "impact_live_estime_pct": rapport.impact_live_estime_pct,
                        "impact_pire_pct": rapport.impact_live_pire_pct,
                        "par_condition": rapport.par_condition,
                    },
                    "distribution_qualite": {
                        "parfait": rapport.nb_parfait,
                        "bon": rapport.nb_bon,
                        "moyen": rapport.nb_moyen,
                        "mauvais": rapport.nb_mauvais,
                        "requote": rapport.nb_requotes,
                        "partiel": rapport.nb_partiels,
                    },
                }, f, indent=2, default=str)
        except Exception as e:
            logger.error(f"Impossible de sauvegarder le rapport JSON : {e}")

        # CSV des exécutions
        chemin_csv = f"reports/executions_{horodatage}.csv"
        try:
            with open(chemin_csv, "w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow([
                    "order_id", "heure", "type", "prix_demande", "prix_execute",
                    "slippage_pts", "slippage_usd", "latence_ms",
                    "qualite", "condition", "requote", "partiel",
                    "lots", "spread_pts",
                ])
                for ex in rapport.executions:
                    writer.writerow([
                        ex.order_id,
                        ex.executed_at.strftime("%Y-%m-%dT%H:%M:%S"),
                        ex.order_type,
                        ex.requested_price,
                        ex.executed_price,
                        ex.slippage_pts,
                        ex.slippage_usd,
                        ex.latency_ms,
                        ex.execution_quality.value,
                        ex.market_condition.value,
                        ex.was_requoted,
                        ex.is_partial,
                        ex.executed_lots,
                        ex.spread_pts,
                    ])
            logger.info(f"Rapport sauvegardé | JSON: {chemin_json} | CSV: {chemin_csv}")
        except Exception as e:
            logger.error(f"Impossible de sauvegarder le CSV : {e}")

    def _sauvegarder_execution(self, execution: ExecutionSimulee) -> None:
        """Sauvegarde chaque exécution en temps réel dans data/paper_executions.json."""
        try:
            os.makedirs("data", exist_ok=True)
            chemin = "data/paper_executions.json"
            try:
                with open(chemin, "r", encoding="utf-8") as f:
                    data = json.load(f)
            except (FileNotFoundError, json.JSONDecodeError):
                data = []

            data.append({
                "order_id": execution.order_id,
                "heure": execution.executed_at.isoformat(),
                "type": execution.order_type,
                "prix_demande": execution.requested_price,
                "prix_execute": execution.executed_price,
                "slippage_pts": execution.slippage_pts,
                "slippage_usd": execution.slippage_usd,
                "latence_ms": execution.latency_ms,
                "qualite": execution.execution_quality.value,
                "condition": execution.market_condition.value,
            })
            data = data[-500:]  # Garder les 500 dernières
            with open(chemin, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, default=str)
        except Exception as e:
            logger.debug(f"Sauvegarde exécution paper échouée : {e}")

    def _logger_execution(self, execution: ExecutionSimulee) -> None:
        """Log chaque exécution simulée avec emoji de qualité."""
        emojis = {
            QualiteExecution.PARFAIT: "✅",
            QualiteExecution.BON:     "🟢",
            QualiteExecution.MOYEN:   "🟡",
            QualiteExecution.MAUVAIS: "🔴",
            QualiteExecution.REQUOTE: "⚠️",
            QualiteExecution.PARTIEL: "🔸",
        }
        emoji = emojis.get(execution.execution_quality, "❓")
        logger.info(
            f"[PAPER] {emoji} {execution.order_type} {execution.executed_lots}L | "
            f"Demandé:{execution.requested_price:.2f} → "
            f"Exécuté:{execution.executed_price:.2f} | "
            f"Slip:{execution.slippage_pts:.1f}pts (${execution.slippage_usd:.2f}) | "
            f"Lat:{execution.latency_ms}ms | "
            f"Marché:{execution.market_condition.value}"
        )

    # ── Accesseurs publics ────────────────────────────────────────────────

    def get_stats_actuelles(self) -> dict:
        """
        Retourne les stats courantes pour le dashboard.
        Appelé à chaque mise à jour du dashboard en mode paper.
        """
        if not self._executions:
            return {
                "nb_executions": 0,
                "slippage_moyen_pts": 0.0,
                "slippage_total_usd": 0.0,
                "latence_moy_ms": 0.0,
                "nb_requotes": 0,
                "nb_partiels": 0,
                "derniere_qualite": "—",
                "derniere_condition": "—",
                "spread_simule_actuel": self._get_spread_simule(datetime.utcnow()),
            }

        execs = self._executions
        slippage_total_usd = sum(
            ex.slippage_pts * ex.executed_lots * self.XAUUSD_VALEUR_POINT_PAR_LOT
            for ex in execs
        )
        return {
            "nb_executions": len(execs),
            "slippage_moyen_pts": round(
                sum(e.slippage_pts for e in execs) / len(execs), 2
            ),
            "slippage_total_usd": round(slippage_total_usd, 2),
            "latence_moy_ms": round(
                sum(e.latency_ms for e in execs) / len(execs), 1
            ),
            "nb_requotes": sum(1 for e in execs if e.was_requoted),
            "nb_partiels": sum(1 for e in execs if e.is_partial),
            "derniere_qualite": execs[-1].execution_quality.value,
            "derniere_condition": execs[-1].market_condition.value,
            "spread_simule_actuel": self._get_spread_simule(datetime.utcnow()),
            "impact_estime_pct": round(
                (slippage_total_usd / 10_000.0) * 100, 3
            ),
        }

    @property
    def carnet(self) -> CarnetOrdresPaper:
        """Accès au carnet d'ordres simulé."""
        return self._carnet

    # Alias anglais (compatibilité spec)
    @property
    def _paper_order_book(self) -> CarnetOrdresPaper:
        return self._carnet


# Alias anglais
SlippageSimulator = SimulateurSlippage
