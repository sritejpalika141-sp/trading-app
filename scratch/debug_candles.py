import requests
import json

try:
    resp = requests.get("http://localhost:8000/api/analysis")
    data = resp.json()
    print(f"Status Code: {resp.status_code}")
    print(f"Candles count: {len(data.get('candles_5m', []))}")
    if data.get('candles_5m'):
        print(f"First candle: {data['candles_5m'][0]}")
    else:
        print("No candles in analysis data")
    
    # Check separate candles endpoint
    resp2 = requests.get("http://localhost:8000/api/candles/5?days=3")
    data2 = resp2.json()
    print(f"Candles endpoint count: {len(data2.get('candles', []))}")

except Exception as e:
    print(f"Error: {e}")
