import asyncio
import os
from fyers_client import get_client

async def test_exchanges():
    client = get_client()
    spot = 24250.0
    
    # Try both NSE and NFO
    symbols = [
        "NSE:NIFTY2642324300CE",
        "NFO:NIFTY2642324300CE",
        "NSE:NIFTY26APR24300CE",
        "NFO:NIFTY26APR24300CE"
    ]
    
    print(f"Testing exchanges for symbols...")
    for s in symbols:
        quote = await asyncio.to_thread(client.get_quote, s)
        print(f"  {s}: {quote.get('lp', 'N/A')}")

if __name__ == "__main__":
    asyncio.run(test_exchanges())
