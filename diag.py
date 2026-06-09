"""
diag.py — Diagnostic complet des conditions de signal en temps réel.
Affiche exactement pourquoi chaque actif ne trade pas.
"""
import sys
from datetime import datetime
import MetaTrader5 as mt5
import pandas as pd

# ── Connexion MT5 ──────────────────────────────────────────────────────────
if not mt5.initialize():
    print("ERREUR: MT5 non connecté")
    sys.exit(1)

acc = mt5.account_info()
print(f"\n{'='*60}")
print(f"Compte: {acc.login} | Balance: {acc.balance:.0f}€ | Heure UTC: {datetime.utcnow().strftime('%H:%M:%S')}")
print(f"{'='*60}\n")

# ── Paramètres par actif ───────────────────────────────────────────────────
ACTIFS = [
    {"sym": "XAUUSD",  "tf": 5,  "digits": 2},
    {"sym": "NAS100",  "tf": 1,  "digits": 1},
    {"sym": "US500",   "tf": 5,  "digits": 1},
    {"sym": "XTIUSD",  "tf": 5,  "digits": 2},
]

def ema(s, n):
    return s.ewm(span=n, adjust=False).mean()

def rsi(s, n=14):
    d = s.diff()
    g = d.clip(lower=0).ewm(com=n-1, adjust=False).mean()
    l = (-d).clip(lower=0).ewm(com=n-1, adjust=False).mean()
    return 100 - 100/(1 + g/l.replace(0,1e-10))

def adx(df, n=14):
    hl = df.high - df.low
    hpc = (df.high - df.close.shift()).abs()
    lpc = (df.low - df.close.shift()).abs()
    tr = pd.concat([hl, hpc, lpc], axis=1).max(axis=1)
    atr = tr.ewm(span=n, adjust=False).mean()
    hdiff = df.high - df.high.shift()
    ldiff = df.low.shift() - df.low
    dmp = ((hdiff > ldiff) & (hdiff > 0)).astype(float) * hdiff.clip(lower=0)
    dmm = ((ldiff > hdiff) & (ldiff > 0)).astype(float) * ldiff.clip(lower=0)
    dip = 100 * dmp.ewm(span=n, adjust=False).mean() / atr.replace(0,1e-10)
    dim = 100 * dmm.ewm(span=n, adjust=False).mean() / atr.replace(0,1e-10)
    dx = 100 * (dip - dim).abs() / (dip + dim).replace(0,1e-10)
    return dx.ewm(span=n, adjust=False).mean()

def atr(df, n=14):
    hl = df.high - df.low
    hpc = (df.high - df.close.shift()).abs()
    lpc = (df.low - df.close.shift()).abs()
    tr = pd.concat([hl, hpc, lpc], axis=1).max(axis=1)
    return tr.ewm(span=n, adjust=False).mean()

for actif in ACTIFS:
    sym = actif["sym"]
    tf  = actif["tf"]
    print(f"── {sym} (M{tf}) {'─'*40}")

    # Données
    rates = mt5.copy_rates_from_pos(sym, tf, 0, 100)
    if rates is None or len(rates) < 20:
        print(f"  ❌ Données indisponibles\n")
        continue

    df = pd.DataFrame(rates)
    df.columns = [c if c != 'tick_volume' else 'volume' for c in df.columns]

    df["e9"]  = ema(df.close, 9)
    df["e21"] = ema(df.close, 21)
    df["e50"] = ema(df.close, 50)
    df["rsi"] = rsi(df.close)
    df["adx"] = adx(df)
    df["atr"] = atr(df)
    df["vol_avg"] = df.volume.rolling(20).mean()

    c    = df.iloc[-2]
    prev = df.iloc[-3]

    # Price action
    cr = float(c.high - c.low)
    cr = cr if cr > 0 else 1e-10
    body = abs(float(c.close - c.open))
    uw = float(c.high) - max(float(c.close), float(c.open))
    lw = min(float(c.close), float(c.open)) - float(c.low)
    close_pct = (float(c.close) - float(c.low)) / cr * 100

    bull_eng = (float(c.close) > float(c.open) and
                float(prev.close) < float(prev.open) and
                float(c.close) > float(prev.open) and
                float(c.open) < float(prev.close))
    bear_eng = (float(c.close) < float(c.open) and
                float(prev.close) > float(prev.open) and
                float(c.close) < float(prev.open) and
                float(c.open) > float(prev.close))
    bull_pin = lw >= cr*0.55 and body <= cr*0.35
    bear_pin = uw >= cr*0.55 and body <= cr*0.35

    bounce_bull = (min(abs(float(c.low)-float(c.e9)), abs(float(c.low)-float(c.e21)))
                   / max(float(c.close),1)*100 <= 0.30 and float(c.close) > float(c.e9))
    bounce_bear = (min(abs(float(c.high)-float(c.e9)), abs(float(c.high)-float(c.e21)))
                   / max(float(c.close),1)*100 <= 0.30 and float(c.close) < float(c.e9))
    prev2 = df.iloc[-3]
    cross_up   = float(c.e9) > float(c.e21) and float(prev2.e9) <= float(prev2.e21)
    cross_down = float(c.e9) < float(c.e21) and float(prev2.e9) >= float(prev2.e21)

    ema_sp = abs(float(c.e9)-float(c.e21)) / float(c.close) * 100

    # HTF H1
    rates_h1 = mt5.copy_rates_from_pos(sym, 16385, 0, 30)
    htf_bull = htf_bear = True
    if rates_h1 is not None and len(rates_h1) >= 3:
        dfh = pd.DataFrame(rates_h1)
        htf_e21 = dfh.close.ewm(span=21, adjust=False).mean()
        htf_bull = float(dfh.close.iloc[-2]) > float(htf_e21.iloc[-2])
        htf_bear = float(dfh.close.iloc[-2]) < float(htf_e21.iloc[-2])

    # Conditions LONG
    trend_bull  = float(c.e9) > float(c.e21) and float(c.close) > float(c.e50)
    trigger_bull= bounce_bull or cross_up
    pa_bull     = (bull_eng or bull_pin) and close_pct >= 60
    adx_ok      = float(c.adx) > 20
    ema_sp_ok   = ema_sp >= 0.10
    rsi_bull_ok = 45 <= float(c.rsi) <= 68

    # Conditions SHORT
    trend_bear  = float(c.e9) < float(c.e21) and float(c.close) < float(c.e50)
    trigger_bear= bounce_bear or cross_down
    pa_bear     = (bear_eng or bear_pin) and close_pct <= 40
    rsi_bear_ok = 32 <= float(c.rsi) <= 55

    print(f"  Prix: {float(c.close):.{actif['digits']}f} | ATR: {float(c.atr):.{actif['digits']}f}")
    print(f"  EMA9:{float(c.e9):.{actif['digits']}f}  EMA21:{float(c.e21):.{actif['digits']}f}  EMA50:{float(c.e50):.{actif['digits']}f}")
    print(f"  ADX: {float(c.adx):.1f} {'✅>20' if adx_ok else '❌<20'} | RSI: {float(c.rsi):.1f} | EMA spread: {ema_sp:.3f}% {'✅' if ema_sp_ok else '❌<0.10%'}")
    print(f"  HTF H1: {'📈 Haussier' if htf_bull else '📉 Baissier'}")
    print(f"  Close pos: {close_pct:.0f}% du range")
    print()
    print(f"  LONG  → Trend:{trend_bull} HTF:{htf_bull} Trigger:{trigger_bull} PA:{pa_bull} RSI:{rsi_bull_ok} ADX:{adx_ok} EMA:{ema_sp_ok}")
    long_ok = trend_bull and htf_bull and trigger_bull and pa_bull and adx_ok and ema_sp_ok and rsi_bull_ok
    if long_ok:
        print(f"  🟢 SIGNAL LONG VALIDE !")
    else:
        bloquants = []
        if not trend_bull:  bloquants.append("Tendance EMA baissière")
        if not htf_bull:    bloquants.append("HTF H1 baissier")
        if not trigger_bull:bloquants.append("Pas de trigger (bounce/cross)")
        if not pa_bull:     bloquants.append(f"Pas de PA bull (eng:{bull_eng} pin:{bull_pin} close:{close_pct:.0f}%)")
        if not adx_ok:      bloquants.append(f"ADX {float(c.adx):.1f} < 20")
        if not ema_sp_ok:   bloquants.append(f"EMA spread {ema_sp:.3f}% < 0.10%")
        if not rsi_bull_ok: bloquants.append(f"RSI {float(c.rsi):.1f} hors [45-68]")
        print(f"  🔴 LONG bloqué : {' | '.join(bloquants)}")

    print()
    print(f"  SHORT → Trend:{trend_bear} HTF:{htf_bear} Trigger:{trigger_bear} PA:{pa_bear} RSI:{rsi_bear_ok} ADX:{adx_ok} EMA:{ema_sp_ok}")
    short_ok = trend_bear and htf_bear and trigger_bear and pa_bear and adx_ok and ema_sp_ok and rsi_bear_ok
    if short_ok:
        print(f"  🟢 SIGNAL SHORT VALIDE !")
    else:
        bloquants = []
        if not trend_bear:  bloquants.append("Tendance EMA haussière")
        if not htf_bear:    bloquants.append("HTF H1 haussier")
        if not trigger_bear:bloquants.append("Pas de trigger (bounce/cross)")
        if not pa_bear:     bloquants.append(f"Pas de PA bear (eng:{bear_eng} pin:{bear_pin} close:{close_pct:.0f}%)")
        if not adx_ok:      bloquants.append(f"ADX {float(c.adx):.1f} < 20")
        if not ema_sp_ok:   bloquants.append(f"EMA spread {ema_sp:.3f}% < 0.10%")
        if not rsi_bear_ok: bloquants.append(f"RSI {float(c.rsi):.1f} hors [32-55]")
        print(f"  🔴 SHORT bloqué : {' | '.join(bloquants)}")

    print()

mt5.shutdown()
