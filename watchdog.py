"""
watchdog.py — Processus de surveillance indépendant du bot principal.

Lance ce script en PARALLÈLE de main.py dans un second terminal :
    python watchdog.py

Il surveille le fichier data/heartbeat.json et alerte via Telegram
si le bot principal n'a pas écrit de heartbeat depuis TIMEOUT_MINUTES.

Ce script est TOTALEMENT INDÉPENDANT :
- N'importe RIEN du projet (pas de config.py, pas de mt5_connector.py)
- Fonctionne même si main.py crashe complètement
- Ne nécessite que la bibliothèque standard + requests + python-dotenv

Déploiement Windows :
    Planificateur de tâches → python watchdog.py au démarrage
"""

import json
import os
import sys
import time
from datetime import datetime, timedelta

# Chargement optionnel de .env
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

try:
    import requests as _requests
    REQUESTS_DISPONIBLE = True
except ImportError:
    REQUESTS_DISPONIBLE = False
    print("[WATCHDOG] 'requests' non disponible — alertes Telegram désactivées")

# ── Configuration ──────────────────────────────────────────────────────────

HEARTBEAT_PATH      = "data/heartbeat.json"
TIMEOUT_MINUTES     = 15       # Alerter si pas de heartbeat depuis 15 min
CHECK_INTERVAL_SEC  = 60       # Vérifier toutes les 60 secondes
TELEGRAM_TOKEN      = os.getenv("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID    = os.getenv("TELEGRAM_CHAT_ID", "")


# ── Envoi Telegram ─────────────────────────────────────────────────────────

def envoyer_alerte_telegram(message: str) -> bool:
    """
    Envoie une alerte Telegram directement via l'API REST.
    Pas de dépendance au reste du projet — appel HTTP simple.
    """
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print(f"[WATCHDOG] Telegram non configuré — {message[:80]}")
        return False

    if not REQUESTS_DISPONIBLE:
        print(f"[WATCHDOG] {message[:80]}")
        return False

    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": f"🐕 <b>[WATCHDOG]</b> {message}",
        "parse_mode": "HTML",
        "disable_notification": False,  # Toujours avec notification sonore
    }
    try:
        resp = _requests.post(url, json=payload, timeout=10)
        return resp.status_code == 200
    except Exception as e:
        print(f"[WATCHDOG] Erreur Telegram : {e}")
        return False


# ── Lecture du heartbeat ───────────────────────────────────────────────────

def lire_heartbeat() -> dict:
    """
    Lit le fichier heartbeat.json écrit par le bot principal.

    Returns:
        Dictionnaire avec les données du heartbeat, ou {} si introuvable/invalide.
    """
    try:
        with open(HEARTBEAT_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return {}
    except json.JSONDecodeError:
        return {}


# ── Boucle principale du watchdog ─────────────────────────────────────────

def main() -> None:
    """Boucle principale du watchdog. Tourne indéfiniment."""
    print(
        f"[WATCHDOG] Démarré — vérification toutes les {CHECK_INTERVAL_SEC}s\n"
        f"[WATCHDOG] Timeout : {TIMEOUT_MINUTES}min sans heartbeat\n"
        f"[WATCHDOG] Heartbeat path : {HEARTBEAT_PATH}\n"
        f"[WATCHDOG] Telegram : {'configuré' if TELEGRAM_TOKEN else 'NON configuré'}"
    )

    envoyer_alerte_telegram(
        f"Watchdog démarré\n"
        f"Surveillance du bot SMC XAUUSD\n"
        f"Timeout : {TIMEOUT_MINUTES}min | "
        f"Vérification : toutes les {CHECK_INTERVAL_SEC}s"
    )

    alerte_envoyee = False
    echecs_consecutifs = 0

    while True:
        heartbeat = lire_heartbeat()
        maintenant = datetime.utcnow()

        if not heartbeat:
            # Fichier absent ou corrompu
            echecs_consecutifs += 1
            print(
                f"[WATCHDOG] {maintenant.strftime('%H:%M:%S')} — "
                f"Heartbeat introuvable (échec #{echecs_consecutifs})"
            )

            if echecs_consecutifs >= 3 and not alerte_envoyee:
                envoyer_alerte_telegram(
                    "🔴 <b>FICHIER HEARTBEAT INTROUVABLE</b>\n"
                    f"3 vérifications sans fichier {HEARTBEAT_PATH}\n"
                    "Le bot ne semble pas avoir démarré,\n"
                    "ou le dossier data/ est inaccessible.\n"
                    "⚠️ Vérifier la VM et relancer main.py"
                )
                alerte_envoyee = True
        else:
            echecs_consecutifs = 0

            try:
                # Parser le timestamp du dernier heartbeat
                ts_str = heartbeat.get("timestamp_utc", "")
                if not ts_str:
                    time.sleep(CHECK_INTERVAL_SEC)
                    continue

                # Compatibilité Python 3.9 (pas de fromisoformat sur tous formats)
                try:
                    dernier_beat = datetime.fromisoformat(ts_str[:19])
                except ValueError:
                    dernier_beat = datetime.strptime(ts_str[:19], "%Y-%m-%dT%H:%M:%S")

                age_minutes = (
                    maintenant - dernier_beat
                ).total_seconds() / 60.0

                if age_minutes > TIMEOUT_MINUTES:
                    # Bot non répondant
                    if not alerte_envoyee:
                        statut_connu = heartbeat.get("bot_status", "INCONNU")
                        positions = heartbeat.get("open_positions", 0)
                        pnl = heartbeat.get("daily_pnl_usd", 0)

                        envoyer_alerte_telegram(
                            f"🔴 <b>BOT NON RÉPONDANT</b>\n"
                            f"Dernier heartbeat : {int(age_minutes)}min il y a\n"
                            f"Dernier statut connu : {statut_connu}\n"
                            f"Positions ouvertes connues : {positions}\n"
                            f"P&L jour connu : {pnl:+.2f}$\n"
                            f"⚠️ Le bot est peut-être gelé ou crashé.\n"
                            f"Vérifier la VM Windows et redémarrer si nécessaire."
                        )
                        alerte_envoyee = True
                        print(
                            f"[WATCHDOG] 🔴 ALERTE — Bot non répondant depuis "
                            f"{int(age_minutes)}min"
                        )
                else:
                    # Heartbeat frais
                    if alerte_envoyee:
                        # Retour à la normale
                        statut = heartbeat.get("bot_status", "?")
                        nb_pings = heartbeat.get("ping_count", 0)
                        envoyer_alerte_telegram(
                            f"✅ <b>Bot de nouveau actif</b>\n"
                            f"Heartbeat reçu après silence\n"
                            f"Statut : {statut} | "
                            f"Pings envoyés : {nb_pings}"
                        )
                        alerte_envoyee = False
                        print(
                            f"[WATCHDOG] ✅ Bot actif — heartbeat reçu"
                        )

                    # Log normal
                    statut = heartbeat.get("bot_status", "?")
                    pings = heartbeat.get("ping_count", 0)
                    positions = heartbeat.get("open_positions", 0)
                    print(
                        f"[WATCHDOG] {maintenant.strftime('%H:%M:%S')} — "
                        f"OK ({int(age_minutes)}min) | "
                        f"Statut: {statut} | "
                        f"Positions: {positions} | "
                        f"Pings: {pings}"
                    )

            except (KeyError, ValueError) as e:
                print(f"[WATCHDOG] Heartbeat invalide : {e}")

        time.sleep(CHECK_INTERVAL_SEC)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n[WATCHDOG] Arrêté par l'utilisateur")
        envoyer_alerte_telegram("Watchdog arrêté manuellement")
        sys.exit(0)
