import sys
import asyncio
from fyers_client import FyersClient
from engine.order_blocks import detect_order_blocks

client = FyersClient()
candles = client.get_historical("NSE:NIFTY50-INDEX", "5", 3)
obs = detect_order_blocks(candles)
print(f"Total OBs detected: {len(obs)}")
for ob in obs:
    print(ob['direction'], ob['timestamp'], ob['active'])
