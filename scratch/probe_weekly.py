import asyncio
from fyers_client import FyersClient

async def probe_weekly_format():
    client = FyersClient()
    formats = [
        "NSE:NIFTY2642324250CE", # YYMDD
        "NSE:NIFTY26APR2324250CE", # YYMMMDD
        "NSE:NIFTY26042324250CE", # YYMMDD
        "NSE:NIFTY2642324250CE",
    ]
    
    print("--- PROBING WEEKLY FORMATS ---")
    for sym in formats:
        quote = await asyncio.to_thread(client.get_quote, sym)
        lp = quote.get("lp", "N/A") if quote else "FAIL"
        print(f"  {sym}: {lp}")

if __name__ == "__main__":
    asyncio.run(probe_weekly_format())
