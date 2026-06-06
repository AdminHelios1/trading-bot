"""
conftest.py — Configuration pytest : mock automatique de MetaTrader5.
Permet de lancer les tests sur macOS/Linux sans MT5 installé.
"""

import sys
from unittest.mock import MagicMock

# Créer un module MetaTrader5 fictif avant tout import
mt5_mock = MagicMock()

# Constantes MT5 utilisées dans le code
mt5_mock.TIMEFRAME_H4 = 16388
mt5_mock.TIMEFRAME_M15 = 15
mt5_mock.TIMEFRAME_M5 = 5
mt5_mock.ORDER_TYPE_BUY = 0
mt5_mock.ORDER_TYPE_SELL = 1
mt5_mock.TRADE_ACTION_DEAL = 1
mt5_mock.TRADE_ACTION_SLTP = 6
mt5_mock.ORDER_TIME_GTC = 1
mt5_mock.ORDER_FILLING_IOC = 1
mt5_mock.TRADE_RETCODE_DONE = 10009
mt5_mock.DEAL_ENTRY_OUT = 1

sys.modules["MetaTrader5"] = mt5_mock
