import MetaTrader5 as mt5
mt5.initialize()
acc = mt5.account_info()
pos = mt5.positions_get()
print("Balance:", acc.balance)
print("Positions ouvertes:", len(pos) if pos else 0)
if pos:
    for p in pos:
        t = "LONG" if p.type == 0 else "SHORT"
        print(f"  {p.symbol} | {t} | {p.volume} lots | Profit: {p.profit}")
mt5.shutdown()
