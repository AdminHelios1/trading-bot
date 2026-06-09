import MetaTrader5 as mt5
mt5.initialize()
syms = mt5.symbols_get()
mots = ["NAS", "US5", "NDX", "DOW", "WTI", "OIL", "XTI", "CRUDE", "SP5", "SPX"]
print("Symboles disponibles contenant :", mots)
print()
for s in syms:
    for m in mots:
        if m.upper() in s.name.upper():
            tick = mt5.symbol_info_tick(s.name)
            print(f"  {s.name:25s} | visible:{s.visible} | bid:{tick.bid if tick else 'N/A'}")
            break
mt5.shutdown()
