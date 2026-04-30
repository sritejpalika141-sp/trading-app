import os
from engine.signals import generate_signals
from fyers_client import FyersClient
client = FyersClient()
candles_1h = client.get_historical("NSE:NIFTY50-INDEX", "60", 15)
print(f"1H candles count: {len(candles_1h)}")
