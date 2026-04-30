import asyncio
import os
from fyers_client import FyersClient

async def find_correct_symbol():
    client = FyersClient()
    spot = 24200.0
    atm = 24250
    
    # Try different formats
    formats = [
        f"NFO:NIFTY26423{atm}PE",    # Weekly v3
        f"NFO:NIFTY26APR{atm}PE",   # Monthly v3
        f"NSE:NIFTY26423{atm}PE",    # Weekly v3 (NSE)
        f"NSE:NIFTY26APR{atm}PE",   # Monthly v3 (NSE)
        f"NFO:NIFTY26423{atm}PE",    # Weekly v3 (NFO)
        f"NFO:NIFTY23APR26{atm}PE",  # Alternate?
    ]
    
    print("Probing symbols:")
    for sym in formats:
        quote = await asyncio.to_thread(client.get_quote, sym)
        lp = quote.get("lp", "N/A") if quote else "FAIL"
        print(f"  {sym}: {lp}")

if __name__ == "__main__":
    asyncio.run(find_correct_symbol())
