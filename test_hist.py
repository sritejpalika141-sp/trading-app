import os
from fyers_client import FyersClient
client = FyersClient()
candles_5m = client.get_historical("NSE:NIFTY50-INDEX", "5", 3)
print(f"5M candles count: {len(candles_5m)}")
if len(candles_5m) == 0:
    print("Why is it empty? Let's check response.")
    from datetime import datetime, timedelta
    end_date = datetime.now()
    start_date = end_date - timedelta(days=3)
    data = {
        "symbol": "NSE:NIFTY50-INDEX",
        "resolution": "5",
        "date_format": "1",
        "range_from": start_date.strftime("%Y-%m-%d"),
        "range_to": end_date.strftime("%Y-%m-%d"),
        "cont_flag": "1"
    }
    print(client.client.history(data))
