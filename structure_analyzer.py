"""
structure_analyzer.py — Analyse de la structure de marché H4 avec validation Displacement.
Chaque BOS est classé FORT (avec Displacement) ou FAIBLE (sans Displacement).
Seuls les BOS_FORT sont utilisés pour les décisions de trading.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import List, Optional, Tuple
import pandas as pd
from loguru import logger

from config import CONFIG
from indicators import Indicateurs
from displacement_checker import ValidateurDisplacement, ResultatDisplacement


# ── Énumérations ───────────────────────────────────────────────────────────

class TypeBOS(Enum):
    """Type et force d'un Break of Structure."""
    BOS_HAUSSIER = "BOS_HAUSSIER_FORT"      # Avec Displacement → tradeable
    BOS_BAISSIER = "BOS_BAISSIER_FORT"      # Avec Displacement → tradeable
    CHOCH_HAUSSIER = "CHOCH_HAUSSIER"       # Alias pour compatibilité
    CHOCH_BAISSIER = "CHOCH_BAISSIER"       # Alias pour compatibilité
    # Nouveaux types avec qualification force
    BULLISH_STRONG = "BOS_HAUSSIER_FORT"
    BULLISH_WEAK = "BOS_HAUSSIER_FAIBLE"    # Sans Displacement → ignoré
    BEARISH_STRONG = "BOS_BAISSIER_FORT"
    BEARISH_WEAK = "BOS_BAISSIER_FAIBLE"    # Sans Displacement → ignoré


class TypeCHoCH(Enum):
    """Type et confirmation d'un Change of Character."""
    BULLISH_CONFIRMED = "CHOCH_HAUSSIER_CONFIRMÉ"    # Displacement validé
    BULLISH_TENTATIVE = "CHOCH_HAUSSIER_TENTATIVE"   # Sans Displacement — surveiller
    BEARISH_CONFIRMED = "CHOCH_BAISSIER_CONFIRMÉ"
    BEARISH_TENTATIVE = "CHOCH_BAISSIER_TENTATIVE"


class Tendance(Enum):
    """Tendance de marché identifiée."""
    HAUSSIERE = "BULLISH"
    BAISSIERE = "BEARISH"
    NEUTRE = "NEUTRE"
    RANGE = "RANGE"


# ── Dataclasses ────────────────────────────────────────────────────────────

@dataclass
class EvenementBOS:
    """Un Break of Structure détecté sur H4."""
    type_bos: TypeBOS
    niveau_casse: float              # Niveau de swing cassé
    index_bougie_bos: int
    temps_bougie_bos: datetime
    prix_cloture_bos: float
    displacement: ResultatDisplacement
    temps_swing_origine: Optional[datetime]
    taille_leg: float                # Taille du leg depuis l'origine du swing ($)
    est_valide: bool                 # True uniquement si Displacement confirmé

    @property
    def est_haussier(self) -> bool:
        return self.type_bos in (
            TypeBOS.BOS_HAUSSIER, TypeBOS.BULLISH_STRONG,
            TypeBOS.CHOCH_HAUSSIER,
        )

    # Compatibilité avec l'ancien code (structure_analyzer v1)
    @property
    def type(self) -> TypeBOS:
        return self.type_bos


@dataclass
class EvenementCHoCH:
    """Un Change of Character détecté sur H4."""
    type_choch: TypeCHoCH
    tendance_precedente: Tendance
    niveau_casse: float
    temps_bougie: datetime
    displacement: ResultatDisplacement
    sequence_bos: List[EvenementBOS]
    est_confirme: bool

    # Compatibilité
    @property
    def type(self) -> TypeBOS:
        if self.type_choch in (TypeCHoCH.BULLISH_CONFIRMED, TypeCHoCH.BULLISH_TENTATIVE):
            return TypeBOS.CHOCH_HAUSSIER
        return TypeBOS.CHOCH_BAISSIER

    @property
    def timestamp(self) -> datetime:
        return self.temps_bougie


@dataclass
class StructureMarche:
    """État complet de la structure de marché H4."""
    tendance: Tendance
    dernier_bos: Optional[EvenementBOS]
    dernier_choch: Optional[EvenementCHoCH]
    liste_bos_recents: List[EvenementBOS]   # 5 derniers BOS (forts + faibles)
    swings_hauts: List[Tuple[datetime, float]]
    swings_bas: List[Tuple[datetime, float]]
    age_tendance_bougies: int = 0
    bos_forts_consecutifs: int = 0
    mis_a_jour: datetime = field(default_factory=datetime.utcnow)

    # Alias pour compatibilité avec l'ancien code
    @property
    def derniere_cassure(self) -> Optional[EvenementBOS]:
        return self.dernier_bos

    @property
    def choch_recent(self) -> Optional[EvenementCHoCH]:
        return self.dernier_choch

    @property
    def dernier_swing_haut(self):
        if not self.swings_hauts:
            return None
        return _SwingPoint(self.swings_hauts[-1][0], self.swings_hauts[-1][1], "HIGH")

    @property
    def dernier_swing_bas(self):
        if not self.swings_bas:
            return None
        return _SwingPoint(self.swings_bas[-1][0], self.swings_bas[-1][1], "LOW")

    @property
    def dernier_leg_haut(self) -> float:
        return max((p for _, p in self.swings_hauts[-3:]), default=0.0)

    @property
    def dernier_leg_bas(self) -> float:
        return min((p for _, p in self.swings_bas[-3:]), default=0.0)


class _SwingPoint:
    """Objet léger pour compatibilité avec l'ancien code."""
    def __init__(self, timestamp, prix: float, type_swing: str):
        self.timestamp = timestamp
        self.prix = prix
        self.type = type_swing


# ── Analyseur principal ────────────────────────────────────────────────────

class AnalyseurStructure:
    """
    Analyse la structure de marché H4 avec validation Displacement sur chaque BOS.
    Compatible avec l'ancienne interface via les méthodes `analyser()` et `detecter_swings()`.
    """

    def __init__(self) -> None:
        self.validateur = ValidateurDisplacement()
        self._derniere_structure: Optional[StructureMarche] = None

    # ── Interface principale ───────────────────────────────────────────────

    def analyser(self, df: pd.DataFrame) -> StructureMarche:
        """
        Analyse complète de la structure de marché sur un DataFrame H4.
        Utilise UNIQUEMENT les bougies fermées (exclut iloc[-1] si en cours).

        Args:
            df: DataFrame H4 OHLCV (minimum 30 bougies recommandé).

        Returns:
            StructureMarche avec tendance, BOS, CHoCH et swings.
        """
        if len(df) < 10:
            return self._structure_vide()

        # Ajouter ATR
        df_atr = self._ajouter_atr(df)

        # Détecter swings
        swings_hauts, swings_bas = self.detecter_swings(df_atr)

        # Détecter BOS avec Displacement
        liste_bos = self._detecter_tous_bos(df_atr, swings_hauts, swings_bas)

        # Détecter CHoCH
        liste_choch = self._detecter_choch(df_atr, liste_bos, swings_hauts, swings_bas)

        # Tendance
        tendance = self._determiner_tendance(liste_bos, liste_choch)

        # Métriques complémentaires
        bos_consecutifs = self._compter_bos_forts_consecutifs(liste_bos, tendance)
        age_tendance = self._calculer_age_tendance(df_atr, liste_choch)

        structure = StructureMarche(
            tendance=tendance,
            dernier_bos=next((b for b in reversed(liste_bos) if b.est_valide), None),
            dernier_choch=next((c for c in reversed(liste_choch) if c.est_confirme), None),
            liste_bos_recents=liste_bos[-5:],
            swings_hauts=swings_hauts[-5:],
            swings_bas=swings_bas[-5:],
            age_tendance_bougies=age_tendance,
            bos_forts_consecutifs=bos_consecutifs,
        )

        self._derniere_structure = structure
        self._logger_resume(structure)
        return structure

    # ── Détection des swings ───────────────────────────────────────────────

    def detecter_swings(
        self,
        df: pd.DataFrame,
        n_bougies: Optional[int] = None,
    ) -> Tuple[List[Tuple], List[Tuple]]:
        """
        Détecte les swing highs et lows significatifs.
        Un swing est valide si N bougies de chaque côté sont inférieures/supérieures
        ET si le leg qui l'a créé est ≥ SWING_MIN_SIZE_ATR_RATIO × ATR.

        Args:
            df: DataFrame OHLCV avec ou sans colonne ATR.
            n_bougies: Nombre de bougies de chaque côté (défaut: CONFIG).

        Returns:
            Tuple (swings_hauts, swings_bas) — listes de (timestamp, prix).
        """
        n = n_bougies or CONFIG.SWING_DETECTION_LOOKBACK
        swings_hauts: List[Tuple] = []
        swings_bas: List[Tuple] = []

        # Récupérer ATR si disponible
        cols_atr = [c for c in df.columns if c.startswith("atr_")]
        a_atr = bool(cols_atr)
        col_atr = cols_atr[0] if cols_atr else None

        highs = df["high"].values
        lows = df["low"].values
        timestamps = df.index

        for i in range(n, len(df) - n):
            atr = float(df[col_atr].iloc[i]) if a_atr and not pd.isna(df[col_atr].iloc[i]) else 5.0
            taille_min_swing = atr * CONFIG.SWING_MIN_SIZE_ATR_RATIO

            # ── Swing High ────────────────────────────────────────────────
            est_swing_haut = (
                highs[i] == max(highs[i - n:i + n + 1])
                and highs[i] > highs[i - 1]
            )
            if est_swing_haut:
                leg_depuis_gauche = highs[i] - min(lows[i - n:i])
                if leg_depuis_gauche >= taille_min_swing:
                    swings_hauts.append((timestamps[i], float(highs[i])))

            # ── Swing Low ─────────────────────────────────────────────────
            est_swing_bas = (
                lows[i] == min(lows[i - n:i + n + 1])
                and lows[i] < lows[i - 1]
            )
            if est_swing_bas:
                leg_depuis_gauche = max(highs[i - n:i]) - lows[i]
                if leg_depuis_gauche >= taille_min_swing:
                    swings_bas.append((timestamps[i], float(lows[i])))

        return swings_hauts, swings_bas

    # ── Détection BOS ─────────────────────────────────────────────────────

    def _detecter_tous_bos(
        self,
        df: pd.DataFrame,
        swings_hauts: List[Tuple],
        swings_bas: List[Tuple],
    ) -> List[EvenementBOS]:
        """
        Détecte tous les BOS sur les LOOKBACK dernières bougies fermées.
        Pour chaque BOS, valide le Displacement de la bougie causante.
        Un BOS sans Displacement est classé WEAK — loggé mais jamais supprimé.

        Args:
            df: DataFrame H4 avec ATR.
            swings_hauts: Liste de (timestamp, prix) des swings hauts.
            swings_bas: Liste de (timestamp, prix) des swings bas.

        Returns:
            Liste de EvenementBOS (FORT + FAIBLE).
        """
        cols_atr = [c for c in df.columns if c.startswith("atr_")]
        col_atr = cols_atr[0] if cols_atr else None

        liste_bos: List[EvenementBOS] = []
        lookback = min(CONFIG.BOS_LOOKBACK_CANDLES, len(df) - 1)
        df_recent = df.iloc[-lookback:]

        for i in range(1, len(df_recent) - 1):  # -1 pour exclure la bougie en cours
            bougie = df_recent.iloc[i]
            prev = df_recent.iloc[i - 1]
            atr = float(df_recent[col_atr].iloc[i]) if col_atr and not pd.isna(df_recent[col_atr].iloc[i]) else 5.0

            temps_courant = df_recent.index[i]

            # ── BOS haussier ───────────────────────────────────────────────
            for swing_temps, swing_niveau in reversed(swings_hauts):
                if swing_temps >= temps_courant:
                    continue
                if prev["close"] < swing_niveau <= bougie["close"]:
                    displacement = self.validateur.verifier(bougie, atr, "bullish")
                    # Fallback séquence si bougie seule insuffisante
                    if not displacement.est_displacement and i >= 2:
                        displacement = self.validateur.verifier_sequence(
                            df_recent, i - 2, "bullish", atr, fenetre=3
                        )

                    type_bos = (
                        TypeBOS.BULLISH_STRONG
                        if displacement.est_displacement
                        else TypeBOS.BULLISH_WEAK
                    )
                    origine_temps, origine_prix = self._trouver_origine_swing(
                        swings_bas, swing_temps
                    )
                    taille_leg = swing_niveau - origine_prix if origine_prix else 0.0

                    bos = EvenementBOS(
                        type_bos=type_bos,
                        niveau_casse=swing_niveau,
                        index_bougie_bos=i,
                        temps_bougie_bos=self._to_datetime(temps_courant),
                        prix_cloture_bos=float(bougie["close"]),
                        displacement=displacement,
                        temps_swing_origine=origine_temps,
                        taille_leg=taille_leg,
                        est_valide=displacement.est_displacement,
                    )
                    liste_bos.append(bos)
                    logger.debug(
                        f"BOS {'FORT' if bos.est_valide else 'FAIBLE'} HAUSSIER @ "
                        f"{temps_courant} | Niveau: {swing_niveau:.2f} | "
                        f"{displacement.raison}"
                    )
                    break

            # ── BOS baissier ───────────────────────────────────────────────
            for swing_temps, swing_niveau in reversed(swings_bas):
                if swing_temps >= temps_courant:
                    continue
                if prev["close"] > swing_niveau >= bougie["close"]:
                    displacement = self.validateur.verifier(bougie, atr, "bearish")
                    if not displacement.est_displacement and i >= 2:
                        displacement = self.validateur.verifier_sequence(
                            df_recent, i - 2, "bearish", atr, fenetre=3
                        )

                    type_bos = (
                        TypeBOS.BEARISH_STRONG
                        if displacement.est_displacement
                        else TypeBOS.BEARISH_WEAK
                    )
                    origine_temps, origine_prix = self._trouver_origine_swing(
                        swings_hauts, swing_temps
                    )
                    taille_leg = origine_prix - swing_niveau if origine_prix else 0.0

                    bos = EvenementBOS(
                        type_bos=type_bos,
                        niveau_casse=swing_niveau,
                        index_bougie_bos=i,
                        temps_bougie_bos=self._to_datetime(temps_courant),
                        prix_cloture_bos=float(bougie["close"]),
                        displacement=displacement,
                        temps_swing_origine=origine_temps,
                        taille_leg=taille_leg,
                        est_valide=displacement.est_displacement,
                    )
                    liste_bos.append(bos)
                    logger.debug(
                        f"BOS {'FORT' if bos.est_valide else 'FAIBLE'} BAISSIER @ "
                        f"{temps_courant} | Niveau: {swing_niveau:.2f} | "
                        f"{displacement.raison}"
                    )
                    break

        return liste_bos

    # ── Détection CHoCH ───────────────────────────────────────────────────

    def _detecter_choch(
        self,
        df: pd.DataFrame,
        liste_bos: List[EvenementBOS],
        swings_hauts: List[Tuple],
        swings_bas: List[Tuple],
    ) -> List[EvenementCHoCH]:
        """
        Détecte les CHoCH = premier BOS contre la tendance dominante précédente.
        CONFIRMÉ si le BOS associé a un Displacement validé.
        TENTATIVE sinon — loggé mais non utilisé pour les trades.

        Args:
            df: DataFrame H4.
            liste_bos: Tous les BOS détectés.
            swings_hauts: Swings hauts.
            swings_bas: Swings bas.

        Returns:
            Liste de EvenementCHoCH.
        """
        liste_choch: List[EvenementCHoCH] = []
        if len(liste_bos) < 2:
            return liste_choch

        nb_bougies = len(df)

        for i in range(1, len(liste_bos)):
            bos_courant = liste_bos[i]

            # Âge : ignorer si trop ancien
            age_bougies = nb_bougies - bos_courant.index_bougie_bos
            if age_bougies > CONFIG.CHOCH_MAX_AGE_CANDLES:
                continue

            # Tendance précédente sur les 3 derniers BOS
            bos_precedents = liste_bos[max(0, i - 3):i]
            tendance_prec = self._inferer_tendance_depuis_bos(bos_precedents)

            # CHoCH haussier : tendance précédente baissière + BOS haussier
            est_choch_haussier = (
                tendance_prec == Tendance.BAISSIERE
                and bos_courant.type_bos in (TypeBOS.BULLISH_STRONG, TypeBOS.BULLISH_WEAK)
            )
            # CHoCH baissier : tendance précédente haussière + BOS baissier
            est_choch_baissier = (
                tendance_prec == Tendance.HAUSSIERE
                and bos_courant.type_bos in (TypeBOS.BEARISH_STRONG, TypeBOS.BEARISH_WEAK)
            )

            if not (est_choch_haussier or est_choch_baissier):
                continue

            est_confirme = bos_courant.est_valide

            if est_choch_haussier:
                type_choch = (
                    TypeCHoCH.BULLISH_CONFIRMED
                    if est_confirme
                    else TypeCHoCH.BULLISH_TENTATIVE
                )
            else:
                type_choch = (
                    TypeCHoCH.BEARISH_CONFIRMED
                    if est_confirme
                    else TypeCHoCH.BEARISH_TENTATIVE
                )

            choch = EvenementCHoCH(
                type_choch=type_choch,
                tendance_precedente=tendance_prec,
                niveau_casse=bos_courant.niveau_casse,
                temps_bougie=bos_courant.temps_bougie_bos,
                displacement=bos_courant.displacement,
                sequence_bos=bos_precedents,
                est_confirme=est_confirme,
            )
            liste_choch.append(choch)
            logger.info(
                f"CHoCH {'CONFIRMÉ' if est_confirme else 'TENTATIVE'} "
                f"{'HAUSSIER' if est_choch_haussier else 'BAISSIER'} @ "
                f"{bos_courant.temps_bougie_bos} | "
                f"Tendance précédente : {tendance_prec.value}"
            )

        return liste_choch

    # ── Détermination de la tendance ──────────────────────────────────────

    def _determiner_tendance(
        self,
        liste_bos: List[EvenementBOS],
        liste_choch: List[EvenementCHoCH],
    ) -> Tendance:
        """
        Détermine la tendance selon cet ordre de priorité :
        1. CHoCH CONFIRMÉ récent → nouvelle direction
        2. Dernier BOS_STRONG → direction dominante
        3. Aucun BOS_STRONG → RANGE
        4. BOS alternés → NEUTRE

        Args:
            liste_bos: Tous les BOS détectés.
            liste_choch: Tous les CHoCH détectés.

        Returns:
            Tendance (HAUSSIERE / BAISSIERE / NEUTRE / RANGE).
        """
        # Règle 1 : CHoCH confirmé récent
        chochs_confirmes = [c for c in liste_choch if c.est_confirme]
        if chochs_confirmes:
            dernier = chochs_confirmes[-1]
            if dernier.type_choch == TypeCHoCH.BULLISH_CONFIRMED:
                return Tendance.HAUSSIERE
            elif dernier.type_choch == TypeCHoCH.BEARISH_CONFIRMED:
                return Tendance.BAISSIERE

        # Règle 2 : Dernier BOS_STRONG
        bos_forts = [b for b in liste_bos if b.est_valide]
        if bos_forts:
            dernier_fort = bos_forts[-1]
            if dernier_fort.type_bos == TypeBOS.BULLISH_STRONG:
                return Tendance.HAUSSIERE
            elif dernier_fort.type_bos == TypeBOS.BEARISH_STRONG:
                return Tendance.BAISSIERE

        # Règle 3 : Aucun BOS_STRONG → RANGE
        if not bos_forts:
            return Tendance.RANGE

        return Tendance.NEUTRE

    # ── Méthodes de calcul ─────────────────────────────────────────────────

    def _inferer_tendance_depuis_bos(
        self,
        bos_list: List[EvenementBOS],
    ) -> Tendance:
        """Infère la tendance depuis une liste de BOS récents."""
        if not bos_list:
            return Tendance.NEUTRE

        haussiers = sum(1 for b in bos_list if b.est_haussier)
        baissiers = len(bos_list) - haussiers

        if haussiers > baissiers:
            return Tendance.HAUSSIERE
        if baissiers > haussiers:
            return Tendance.BAISSIERE
        return Tendance.NEUTRE

    def _compter_bos_forts_consecutifs(
        self,
        liste_bos: List[EvenementBOS],
        tendance: Tendance,
    ) -> int:
        """Compte les BOS_STRONG consécutifs dans la direction de la tendance."""
        if not liste_bos or tendance == Tendance.NEUTRE:
            return 0

        bos_forts = [b for b in liste_bos if b.est_valide]
        if not bos_forts:
            return 0

        compteur = 0
        for bos in reversed(bos_forts):
            est_aligne = (
                (tendance == Tendance.HAUSSIERE and bos.type_bos == TypeBOS.BULLISH_STRONG)
                or (tendance == Tendance.BAISSIERE and bos.type_bos == TypeBOS.BEARISH_STRONG)
            )
            if est_aligne:
                compteur += 1
            else:
                break
        return compteur

    def _calculer_age_tendance(
        self,
        df: pd.DataFrame,
        liste_choch: List[EvenementCHoCH],
    ) -> int:
        """Nombre de bougies H4 depuis le dernier CHoCH confirmé."""
        chochs_confirmes = [c for c in liste_choch if c.est_confirme]
        if not chochs_confirmes:
            return len(df)

        dernier_choch = chochs_confirmes[-1]
        try:
            idx = df.index.get_loc(dernier_choch.temps_bougie)
            return len(df) - idx
        except (KeyError, TypeError):
            return len(df)

    def _trouver_origine_swing(
        self,
        swings: List[Tuple],
        avant_temps: object,
    ) -> Tuple[Optional[datetime], float]:
        """Trouve le swing le plus récent avant un timestamp donné."""
        for temps, prix in reversed(swings):
            if temps < avant_temps:
                return self._to_datetime(temps), prix
        return None, 0.0

    def _ajouter_atr(self, df: pd.DataFrame, periode: int = 14) -> pd.DataFrame:
        """Ajoute la colonne ATR au DataFrame."""
        if df is None or len(df) < periode + 1:
            return df
        df = df.copy()
        atr_serie = Indicateurs.atr(df, periode)
        df[f"atr_{periode}"] = atr_serie
        return df

    @staticmethod
    def _to_datetime(ts) -> datetime:
        """Convertit un timestamp pandas en datetime Python."""
        if isinstance(ts, datetime):
            return ts
        try:
            dt = pd.Timestamp(ts).to_pydatetime()
            if dt.tzinfo is not None:
                dt = dt.replace(tzinfo=None)
            return dt
        except Exception:
            return datetime.utcnow()

    def _structure_vide(self) -> StructureMarche:
        """Retourne une structure vide si les données sont insuffisantes."""
        return StructureMarche(
            tendance=Tendance.NEUTRE,
            dernier_bos=None,
            dernier_choch=None,
            liste_bos_recents=[],
            swings_hauts=[],
            swings_bas=[],
        )

    def _logger_resume(self, structure: StructureMarche) -> None:
        """Logue un résumé lisible de la structure."""
        bos_forts = sum(1 for b in structure.liste_bos_recents if b.est_valide)
        bos_faibles = sum(1 for b in structure.liste_bos_recents if not b.est_valide)

        dernier_bos_str = "Aucun"
        if structure.dernier_bos:
            db = structure.dernier_bos
            dernier_bos_str = (
                f"{db.type_bos.value} @ "
                f"{db.temps_bougie_bos.strftime('%d/%m %Hh')} "
                f"(niveau {db.niveau_casse:.2f}) | "
                f"Corps: {db.displacement.ratio_corps_atr:.2f}× ATR"
            )

        dernier_choch_str = "Aucun"
        if structure.dernier_choch:
            dc = structure.dernier_choch
            dernier_choch_str = (
                f"{dc.type_choch.value} @ "
                f"{dc.temps_bougie.strftime('%d/%m %Hh')}"
            )

        logger.info(
            f"STRUCTURE H4 | Tendance: {structure.tendance.value} "
            f"(âge: {structure.age_tendance_bougies} bougies) | "
            f"BOS forts: {bos_forts} | BOS faibles ignorés: {bos_faibles} | "
            f"Dernier BOS: {dernier_bos_str} | "
            f"Dernier CHoCH: {dernier_choch_str}"
        )

    # ── Méthodes de compatibilité avec l'ancien code ──────────────────────

    def detecter_bos_choch(
        self,
        df: pd.DataFrame,
        swings_hauts: list,
        swings_bas: list,
    ) -> list:
        """Compatibilité : retourne les BOS comme avant."""
        df_atr = self._ajouter_atr(df)
        return self._detecter_tous_bos(df_atr, swings_hauts, swings_bas)

    def identifier_tendance(self, swings_hauts, swings_bas, cassures) -> Tendance:
        """Compatibilité : identifie la tendance depuis des cassures."""
        bos_forts = [c for c in cassures if hasattr(c, "est_valide") and c.est_valide]
        if not bos_forts:
            return self._tendance_par_swings(swings_hauts, swings_bas)
        dernier = bos_forts[-1]
        if dernier.est_haussier:
            return Tendance.HAUSSIERE
        return Tendance.BAISSIERE

    def _tendance_par_swings(self, swings_hauts, swings_bas) -> Tendance:
        """Fallback tendance via séquence HH/HL ou LH/LL."""
        if len(swings_hauts) >= 2:
            if swings_hauts[-1][1] > swings_hauts[-2][1]:
                return Tendance.HAUSSIERE
        if len(swings_bas) >= 2:
            if swings_bas[-1][1] < swings_bas[-2][1]:
                return Tendance.BAISSIERE
        return Tendance.NEUTRE

    def est_dans_discount(self, prix: float, structure: StructureMarche) -> bool:
        """Prix dans la zone de discount (< 50% du dernier leg)."""
        milieu = (structure.dernier_leg_haut + structure.dernier_leg_bas) / 2
        return prix < milieu and milieu > 0

    def est_dans_premium(self, prix: float, structure: StructureMarche) -> bool:
        """Prix dans la zone de premium (> 50% du dernier leg)."""
        milieu = (structure.dernier_leg_haut + structure.dernier_leg_bas) / 2
        return prix > milieu and milieu > 0
