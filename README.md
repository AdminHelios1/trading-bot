# SMC Trading Bot — XAUUSD

Bot de trading algorithmique 100% automatisé, connecté à MetaTrader 5, spécialisé sur
**XAUUSD (Or spot)**. Exécute un unique setup : **Breaker Block + Order Block Confluence**
issu de la méthodologie **Smart Money Concepts (SMC)**.

---

## Objectifs de performance

| Indicateur | Cible |
|---|---|
| Risk:Reward minimum | 2:1 |
| R:R cible | 2.5:1 |
| Win Rate cible | ≥ 45% |
| Profit Factor cible | > 1.8 |
| Drawdown journalier max | 3% |
| Drawdown total max | 10% → arrêt d'urgence |

---

## Architecture

```
trading_bot/
├── main.py                  # Point d'entrée — args --mode live/backtest/paper
├── config.py                # TOUS les paramètres configurables
├── mt5_connector.py         # Connexion, ordres, lecture des données MT5
├── strategy.py              # Logique du setup SMC Breaker Block + Order Block
├── structure_analyzer.py    # Détection Market Structure (HH, HL, LH, LL, BOS, CHoCH)
├── ob_detector.py           # Détection Order Blocks et Breaker Blocks
├── indicators.py            # Indicateurs techniques (ATR, RSI, bougies de rejet)
├── risk_manager.py          # Position sizing, SL/TP, gestion drawdown, circuit breaker
├── trade_manager.py         # Suivi positions ouvertes, trailing stop, partiels
├── backtest.py              # Backtest vectorisé sur données historiques MT5
├── dashboard.py             # Interface terminal Rich (live P&L, positions, stats)
├── notifier.py              # Alertes Telegram (optionnel)
├── logger_config.py         # Config loguru — logs rotatifs par jour
├── tests/
│   ├── fixtures.py          # Données OHLCV fictives pour les tests
│   ├── test_strategy.py     # Tests logique de signal
│   ├── test_risk_manager.py # Tests position sizing et règles de risque
│   └── test_structure.py    # Tests détection structure de marché
├── logs/                    # Logs journaliers (auto-créé)
├── reports/                 # Rapports backtest CSV + PNG
├── .env.example             # Template des variables d'environnement
└── requirements.txt
```

---

## Prérequis

1. **Windows 10/11** (l'API Python MetaTrader5 est Windows uniquement)
2. **MetaTrader 5** installé et connecté à un broker
3. **Python 3.11+** installé
4. Un compte demo chez un broker compatible MT5 avec XAUUSD (ex: ICMarkets, Pepperstone, XM)

> **Sous Linux/Mac** : utiliser Wine ou une VM Windows. L'API MT5 native ne fonctionne pas sur ces OS.

---

## Installation

### 1. Cloner ou copier le projet

```bash
git clone <repo_url> trading_bot
cd trading_bot
```

### 2. Créer un environnement virtuel (recommandé)

```bash
python -m venv venv
# Windows :
venv\Scripts\activate
# Linux/Mac (Wine) :
source venv/bin/activate
```

### 3. Installer les dépendances

```bash
pip install -r requirements.txt
```

### 4. Configurer les credentials

```bash
cp .env.example .env
# Éditer .env avec vos identifiants MT5
```

Contenu du `.env` à remplir :
```
MT5_LOGIN=123456
MT5_PASSWORD=votre_mot_de_passe
MT5_SERVER=VotreBroker-Demo
LOG_LEVEL=INFO
```

---

## Utilisation

### Étape 1 — Lancer le backtest (obligatoire avant le live)

```bash
python main.py --mode backtest
# Ou sur une période personnalisée :
python main.py --mode backtest --from 2023-01-01 --to 2024-12-31
```

Sorties générées dans `reports/` :
- `backtest_XAUUSD_YYYYMMDD.csv` — trades détaillés
- `equity_curve_XAUUSD.png` — courbe equity + distribution des R
- `summary_XAUUSD.txt` — résumé texte des métriques

**Vérifier que les résultats sont cohérents avant de continuer :**
- Profit Factor > 1.5
- Win Rate > 40%
- Max Drawdown < 15%

### Étape 2 — Mode Paper (simulation sans ordres réels)

```bash
python main.py --mode paper
```

Le bot se connecte à MT5 pour les données de marché mais **n'envoie aucun ordre**.
Observer pendant 1–2 semaines pour valider le comportement en conditions réelles.

### Étape 3 — Mode Live

```bash
python main.py --mode live
```

> ⚠️ **Utiliser exclusivement sur un compte DEMO au départ.**
> Ne passer en compte réel qu'après validation sur au moins 3 mois de paper/demo.

---

## Configuration avancée

Tous les paramètres sont dans `config.py` :

```python
# Actif (modifiable — adapter les seuils ATR si changement)
SYMBOLE = "XAUUSD"

# Risque
RISQUE_PAR_TRADE_PCT = 1.0    # 1% du capital par trade
MAX_DRAWDOWN_JOURNALIER_PCT = 3.0
MAX_DRAWDOWN_TOTAL_PCT = 10.0

# Sessions (UTC)
SESSION_LONDON_OUVERTURE = 7   # 08h00 Paris
SESSION_FERMETURE = 20         # 21h00 Paris

# R:R
RR_MINIMUM = 2.0
RR_CIBLE = 2.5
```

---

## Exécution continue 24/7 (Windows)

### Via le Planificateur de tâches Windows

1. Ouvrir le **Planificateur de tâches** (`taskschd.msc`)
2. Créer une tâche de base :
   - **Déclencheur** : Au démarrage du système
   - **Action** : `python C:\chemin\vers\trading_bot\main.py --mode live`
   - **Démarrer dans** : `C:\chemin\vers\trading_bot\`
3. Ajouter un second déclencheur "toutes les heures" comme failsafe

### Via NSSM (Non-Sucking Service Manager) — recommandé

```bash
# Installer NSSM depuis https://nssm.cc/
nssm install SMCTradingBot "python" "C:\trading_bot\main.py --mode live"
nssm set SMCTradingBot AppDirectory "C:\trading_bot"
nssm start SMCTradingBot
```

---

## Surveillance et logs

Les logs sont stockés dans `logs/` avec rotation journalière :
- `bot_YYYY-MM-DD.log` — tous les événements (niveau DEBUG)
- `erreurs_YYYY-MM-DD.log` — erreurs uniquement, conservés 90 jours

### Notifications Telegram (optionnel)

Ajouter dans `.env` :
```
TELEGRAM_TOKEN=votre_token_bot_telegram
TELEGRAM_CHAT_ID=votre_chat_id
```

Le bot envoie des alertes pour :
- Ouverture / fermeture de trade
- Activation du circuit breaker
- Drawdown important
- Erreurs critiques

---

## Tests unitaires

```bash
# Lancer tous les tests
pytest tests/ -v

# Tests d'un module spécifique
pytest tests/test_risk_manager.py -v
pytest tests/test_structure.py -v
pytest tests/test_strategy.py -v
```

---

## Le setup SMC expliqué

### Setup unique : Breaker Block + Order Block Confluence

Le bot n'exécute **qu'un seul setup**, répété avec discipline :

1. **Structure H4 haussière** (BOS haussier + CHoCH récent)
2. **Zone de demande H4** : Order Block ou Breaker Block haussier dans la zone de discount (< 50% du dernier leg)
3. **Confirmation M15** : Le prix entre dans la zone + bougie de rejet haussière (hammer/engulfing) + RSI < 45
4. **Déclencheur M5** : Clôture bullish qui casse le high de la bougie de rejet M15 + volume supérieur à la moyenne
5. **Session active** : Entre l'ouverture de Londres (08h UTC) et la clôture NY (20h UTC)

### Gestion de la position

```
Entrée → SL sous l'OB (+ 0.5×ATR M15)
       → TP1 à 1R : fermeture 50% + SL au breakeven
       → TP2 à 2.5R : fermeture des 50% restants
       → Trailing stop activé après TP1 (1.5×ATR M15)
```

### Règles de sécurité non contournables

- Maximum **1 position ouverte** simultanément
- Arrêt automatique si drawdown journalier > **3%**
- Arrêt définitif si drawdown total > **10%**
- **Circuit breaker** : pause 24h après 3 pertes consécutives
- Aucun trade **22h–23h59 UTC** (clôture journalière XAUUSD)
- Spread > 3× la moyenne → trade refusé
- Marché plat (ATR < 50% de la moyenne) → trade refusé

---

## Avertissements

> **Le trading algorithmique comporte des risques de perte en capital.**
>
> Ce bot est fourni à titre éducatif. Les performances passées ne garantissent pas
> les performances futures. Ne jamais risquer de l'argent que vous ne pouvez pas
> vous permettre de perdre.
>
> Tester impérativement en **compte démo** avant tout passage en compte réel.
> Valider la stratégie sur **au moins 6 mois** de données historiques et
> **3 mois de paper trading** avant de considérer un déploiement réel.

---

## Dépendances

| Librairie | Version | Usage |
|---|---|---|
| MetaTrader5 | 5.0.45 | Connexion broker |
| pandas | 2.2.3 | Traitement des données |
| numpy | 1.26.4 | Calculs numériques |
| pandas-ta | 0.3.14b0 | Indicateurs techniques |
| loguru | 0.7.2 | Logging structuré |
| python-dotenv | 1.0.1 | Variables d'environnement |
| matplotlib | 3.9.0 | Equity curve |
| rich | 13.7.1 | Dashboard terminal |
| pytest | 8.2.2 | Tests unitaires |
| requests | 2.32.3 | Notifications Telegram |


---

## Monitoring & Watchdog

### Lancement avec watchdog (recommandé pour le live)

```bash
# Terminal 1 — Bot principal
python main.py --mode paper

# Terminal 2 — Watchdog indépendant (surveille le bot)
python watchdog.py
```

Le watchdog surveille `data/heartbeat.json` toutes les 60 secondes.
Si le bot ne répond plus depuis **15 minutes**, il envoie une alerte Telegram critique.

### Configuration Telegram

```bash
# 1. Créer un bot via @BotFather sur Telegram
# 2. Ajouter dans .env :
TELEGRAM_TOKEN=123456789:ABCdef...
TELEGRAM_CHAT_ID=987654321

# 3. Trouver votre chat_id :
curl https://api.telegram.org/bot{TOKEN}/getUpdates
```

### Commandes de contrôle à distance

| Commande | Action |
|---|---|
| `/status` | État complet du bot |
| `/pause` | Mettre en pause (no new trades) |
| `/resume` | Reprendre le trading |
| `/positions` | Positions ouvertes |
| `/balance` | Balance et P&L du jour |
| `/cb` | État circuit breaker |
| `/stop` | Arrêt propre du bot |
| `/help` | Liste des commandes |

### Planificateur Windows (démarrage automatique)

Créer deux tâches dans le **Planificateur de tâches Windows** :
1. `python main.py --mode paper` → au démarrage de la session
2. `python watchdog.py` → au démarrage de la session

### Pings de santé

Le bot envoie automatiquement un message Telegram silencieux toutes les **5 minutes** avec :
- Statut du bot et de la connexion MT5
- Nombre de positions ouvertes + P&L jour
- Niveau du circuit breaker
- CPU / RAM du serveur
- Uptime

### Rapport de disponibilité

Chaque jour à **23h55 UTC**, un rapport automatique indique :
- Uptime en % + durée
- Nombre de pings envoyés
- Alertes actives et critiques
