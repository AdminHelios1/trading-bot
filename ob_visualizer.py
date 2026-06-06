"""
ob_visualizer.py — Visualisation ASCII des Order Blocks dans les logs.
Facilite le débogage et la surveillance en temps réel.
"""

from typing import List
from loguru import logger


def logger_resume_ob(obs: list, prix_actuel: float) -> None:
    """
    Affiche un résumé ASCII des OB actifs dans les logs.

    Args:
        obs: Liste d'OBMultiTimeframe.
        prix_actuel: Prix courant de XAUUSD.
    """
    if not obs:
        logger.info("═══ ORDER BLOCKS : Aucun OB multi-TF valide détecté ═══")
        return

    from ob_detector import TypeOB, StatutOB

    logger.info(f"═══ ORDER BLOCKS ACTIFS ({len(obs)}) ═══")
    for i, ob in enumerate(obs, 1):
        direction = "▲ HAUSSIER" if ob.type_ob == TypeOB.HAUSSIER else "▼ BAISSIER"
        symboles_tf = ""
        symboles_tf += "H4✓ " if ob.zone_h4 else "H4✗ "
        symboles_tf += "H1✓ " if ob.zone_h1 else "H1✗ "
        symboles_tf += "M15✓" if ob.zone_m15 else "M15✗"

        distance = abs(prix_actuel - ob.zone_entree_milieu)
        distance_pct = (distance / prix_actuel * 100) if prix_actuel > 0 else 0.0

        statut_emoji = {
            StatutOB.ACTIF: "🟢",
            StatutOB.TESTE: "🟡",
            StatutOB.EPUISE: "🔴",
            StatutOB.INVALIDE: "❌",
        }.get(ob.statut, "❓")

        confluences_str = " | ".join(ob.confluences) if ob.confluences else "aucune"

        logger.info(
            f"  #{i} {direction} | "
            f"Score: {ob.score}/100 [{ob.force.value}] | "
            f"TF: {symboles_tf} | "
            f"Zone: {ob.zone_entree_bas:.2f}–{ob.zone_entree_haut:.2f} | "
            f"Distance: {distance_pct:.2f}% | "
            f"Statut: {statut_emoji} {ob.statut.value} | "
            f"Confluences: {confluences_str}"
        )

    logger.info("═══════════════════════════════════════════")


def logger_ob_detecte(ob, prix_actuel: float) -> None:
    """
    Logue un OB spécifique détecté avec tous ses détails.

    Args:
        ob: OBMultiTimeframe nouvellement détecté.
        prix_actuel: Prix courant.
    """
    from ob_detector import TypeOB

    direction = "▲ HAUSSIER" if ob.type_ob == TypeOB.HAUSSIER else "▼ BAISSIER"
    distance_pct = abs(prix_actuel - ob.zone_entree_milieu) / prix_actuel * 100 if prix_actuel > 0 else 0

    logger.success(
        f"🏛️  NOUVEAU OB {direction} | "
        f"Score: {ob.score}/100 [{ob.force.value}] | "
        f"Zone: {ob.zone_entree_bas:.2f}–{ob.zone_entree_haut:.2f} | "
        f"Milieu: {ob.zone_entree_milieu:.2f} | "
        f"Distance: {distance_pct:.2f}% | "
        f"Invalidation: {ob.niveau_invalidation:.2f} | "
        f"Confluences: {', '.join(ob.confluences)}"
    )


def logger_ob_invalide(ob, prix_actuel: float) -> None:
    """
    Logue l'invalidation d'un OB.

    Args:
        ob: OBMultiTimeframe invalidé.
        prix_actuel: Prix courant.
    """
    from ob_detector import TypeOB
    direction = "HAUSSIER" if ob.type_ob == TypeOB.HAUSSIER else "BAISSIER"
    logger.warning(
        f"❌ OB {direction} INVALIDÉ | "
        f"Zone: {ob.zone_entree_bas:.2f}–{ob.zone_entree_haut:.2f} | "
        f"Prix actuel: {prix_actuel:.2f} | "
        f"Invalidation: {ob.niveau_invalidation:.2f}"
    )


def afficher_tableau_ascii(obs: list, prix_actuel: float) -> str:
    """
    Génère un tableau ASCII formaté pour le dashboard.

    Args:
        obs: Liste d'OBMultiTimeframe.
        prix_actuel: Prix courant.

    Returns:
        Chaîne de caractères ASCII du tableau.
    """
    if not obs:
        return "  Aucun OB multi-TF actif\n"

    from ob_detector import TypeOB

    lignes = []
    en_tete = f"  {'Direction':<12} {'Score':<8} {'Zone':<20} {'TF':<12} {'Dist%':<7} {'Force':<15}"
    separateur = "  " + "─" * 74
    lignes.append(separateur)
    lignes.append(en_tete)
    lignes.append(separateur)

    for ob in obs[:5]:  # Limiter à 5 pour le dashboard
        direction = "▲ HAUSSIER" if ob.type_ob == TypeOB.HAUSSIER else "▼ BAISSIER"
        zone_str = f"{ob.zone_entree_bas:.2f}–{ob.zone_entree_haut:.2f}"
        tf_str = f"{'H4' if ob.zone_h4 else '  '} {'H1' if ob.zone_h1 else '  '} {'M15' if ob.zone_m15 else '   '}"
        distance_pct = abs(prix_actuel - ob.zone_entree_milieu) / prix_actuel * 100 if prix_actuel > 0 else 0

        ligne = (
            f"  {direction:<12} "
            f"{ob.score}/100   "
            f"{zone_str:<20} "
            f"{tf_str:<12} "
            f"{distance_pct:.1f}%   "
            f"{ob.force.value:<15}"
        )
        lignes.append(ligne)

    lignes.append(separateur)
    return "\n".join(lignes)
