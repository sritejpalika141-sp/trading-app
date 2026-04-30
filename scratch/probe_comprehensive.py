import asyncio
import os
from fyers_client import FyersClient

async def probe_symbols():
    client = FyersClient()
    
    # Symbols to test
    indices = [
        "NSE:NIFTY50-INDEX",
        "NSE:NIFTY-INDEX",
        "NSE:NIFTY 50-INDEX",
        "NSE:INDIAVIX-INDEX",
        "NSE:INDIA VIX-INDEX",
        "INDEX:NIFTY50",
        "INDEX:NIFTY-INDEX",
    ]
    
    options = [
        "NSE:NIFTY26APR24250PE",
        "NFO:NIFTY26APR24250PE",
        "NSE:NIFTY2642324250PE",
        "NFO:NIFTY2642324250PE"
    ]
    
    print("--- PROBING INDICES ---")
    for sym in indices:
        quote = await asyncio.to_thread(client.get_quote, sym)
        lp = quote.get("lp", "N/A") if quote else "FAIL"
        print(f"  {sym}: {lp}")
        
    print("\n--- PROBING OPTIONS ---")
    for sym in options:
        quote = await asyncio.to_thread(client.get_quote, sym)
        lp = quote.get("lp", "N/A") if quote else "FAIL"
        print(f"  {sym}: {lp}")

if __name__ == "__main__":
    asyncio.run(probe_symbols())
