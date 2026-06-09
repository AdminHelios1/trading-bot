import json, os
from datetime import datetime

os.makedirs("data", exist_ok=True)

# Reset circuit breaker
cb = {
    "niveau": "AUCUN", "raison": "", "declenche_a": None,
    "pause_jusqu_a": None, "reset_a_minuit": False,
    "risk_pct_actuel": 0.5, "trades_bloques": 0,
    "dernier_reset": None, "historique": []
}
with open("data/circuit_breaker_state.json", "w") as f:
    json.dump(cb, f, indent=2)

# Reset daily stats
ds = {
    "date_utc": datetime.utcnow().strftime("%Y-%m-%d"),
    "balance_ouverture": 100000.0, "balance_actuelle": 100000.0,
    "balance_pic": 100000.0, "trades": [],
    "pertes_consecutives": 0, "gains_consecutifs": 0,
    "pnl_usd": 0.0, "pnl_pct": 0.0, "drawdown_pct": 0.0,
    "drawdown_max_pct": 0.0, "nb_trades": 0, "nb_gagnants": 0,
    "nb_perdants": 0, "win_rate": 0.0,
    "derniere_maj": datetime.utcnow().isoformat()
}
with open("data/daily_stats.json", "w") as f:
    json.dump(ds, f, indent=2)

print("Reset OK - Circuit Breaker: AUCUN - Daily Stats: vide")
