"""
NIFTY Options Trading Dashboard — FastAPI Backend
"""
import os
import json
import threading
import asyncio
from datetime import datetime
import pytz
from typing import Optional, List, Set, Dict
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel
from pathlib import Path
from dotenv import load_dotenv

from fyers_client import get_client
from engine.signals import generate_signals
from engine.ai_engine import ai_engine
from engine.strikes import select_strike, get_strike_recommendations
from engine.logger import log_signal, log_trade, get_signal_history
from engine.automation import TradingState

# Dynamic path resolution for portability
BASE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = BASE_DIR.parent
ENV_PATH = PROJECT_ROOT / "fyers-mcp-server" / ".env"
LOG_DIR = BASE_DIR / "logs"

# Ensure logs directory exists
os.makedirs(LOG_DIR, exist_ok=True)

load_dotenv(ENV_PATH)

app = FastAPI(title="Sritej Trading Dashboard")
VERSION = "3.0.0-beta"
state = TradingState()

# Performance-optimized Global Caches
# Configuration now managed via state.active_symbols
@app.get("/api/scripts")
async def get_scripts():
    return {"scripts": state.active_symbols}

@app.post("/api/scripts/add")
async def add_script(data: Dict):
    symbol = data.get("symbol", "").strip().upper()
    if not symbol: return {"success": False, "message": "Symbol required"}
    
    # Auto-format for common shorthand
    # 1. If no prefix, add NSE:
    if ":" not in symbol:
        symbol = f"NSE:{symbol}"
    
    # 2. If no suffix, check if it's a known index or option
    if "-" not in symbol:
        indices = ["NIFTY50", "NIFTYBANK", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "INDIAVIX", "SENSEX", "NIFTYNXT50"]
        is_index = any(idx in symbol for idx in indices)
        # Options usually have long numeric strings like NIFTY26505...
        is_option = len(symbol) > 12 and any(char.isdigit() for char in symbol)
        
        if is_index:
            symbol = f"{symbol}-INDEX"
        elif not is_option:
            symbol = f"{symbol}-EQ"
    
    # Special case: SBI -> SBIN
    if "NSE:SBI-EQ" in symbol: symbol = "NSE:SBIN-EQ"
    if "NSE:BANKNIFTY-INDEX" in symbol: symbol = "NSE:NIFTYBANK-INDEX"
    if "NSE:BANKNIFTY-EQ" in symbol: symbol = "NSE:NIFTYBANK-INDEX"

    state.add_symbol(symbol)
    # Trigger an immediate WebSocket sync
    if data_socket_instance:
        on_data_open(sync_only=True)
    return {"success": True, "scripts": state.active_symbols, "formatted": symbol}

@app.post("/api/scripts/remove")
async def remove_script(data: Dict):
    symbol = data.get("symbol", "")
    if not symbol: return {"success": False, "message": "Symbol required"}
    state.remove_symbol(symbol)
    # Trigger an immediate WebSocket sync
    on_data_open(sync_only=True)
    return {"success": True, "scripts": state.active_symbols}

_analysis_cache = {
    "data": {"signals": [], "strike_recommendations": [], "key_levels": []},
    "timestamp": 0
}

_market_cache = {
    "spot": {},        # Will store { symbol: quote_data }
    "vix": None,
    "analysis": {},    # Will store { symbol: analysis_result }
    "positions": [],
    "active_positions": [],
    "total_pnl": 0.0,
    "realized_pnl": 0.0,
    "unrealized_pnl": 0.0,
    "orders": [],
    "funds": None,
    "strikes": {},     # Will store { symbol: { "ce": [], "pe": [], "expiry": None } }
    "last_update": 0
}

# Lock for Signal Parameters to prevent flickering
_signal_lock = {
    "type": None,      # "CALL" or "PUT"
    "strike": None,
    "buy_price": 0,
    "sl_points": 0,
    "target_points": 0,
    "timestamp": 0
}

# WebSocket Global State
data_socket_instance = None
main_loop = None

def on_data_message(msg):
    """Callback for real-time price updates."""
    # Sometimes Fyers sends a list of messages
    if isinstance(msg, list):
        for m in msg: on_data_message(m)
        return
    if not isinstance(msg, dict): return
    
    symbol = msg.get('symbol')
    if not symbol: return
    
    # Fyers v3 can use 'lp' or 'ltp' depending on the mode
    price = msg.get("lp") or msg.get("ltp") or 0
    if price == 0: return
    
    updated = False
    if symbol == "NSE:INDIAVIX-INDEX":
        _market_cache["vix"] = { "lp": price, "ch": msg.get("ch", 0), "chp": msg.get("chp", 0) }
        updated = True
    elif symbol in state.active_symbols:
        _market_cache["spot"][symbol] = {
            "lp": price,
            "ch": msg.get("ch", 0),
            "chp": msg.get("chp", 0),
            "high_price": msg.get("high_price", 0),
            "low_price": msg.get("low_price", 0),
            "open_price": msg.get("open_price", 0),
            "prev_close_price": msg.get("prev_close_price", 0)
        }
        updated = True
        
    # Also update strikes if we have them cached
    if symbol.endswith("CE") or symbol.endswith("PE"):
        for base in _market_cache["strikes"]:
            for side in ["ce", "pe"]:
                for s in _market_cache["strikes"][base].get(side, []):
                    if s.get("symbol") == symbol:
                        s["ltp"] = price
                        updated = True

    if updated:
        from datetime import datetime
        _market_cache["last_update"] = datetime.now().timestamp()

def start_data_socket_thread():
    """Background thread to maintain the Fyers Data Socket."""
    global data_socket_instance
    # Prevent multiple threads
    if data_socket_instance:
        try:
            data_socket_instance.close()
        except: pass
    
    client = get_client()
    data_socket_instance = client.start_data_socket(
        on_message=on_data_message,
        on_error=on_data_error,
        on_close=on_data_close,
        on_open=on_data_open
    )
    if data_socket_instance:
        data_socket_instance.keep_running()

def on_data_error(err):
    print(f"📡 Data Socket Error: {err}")
    global data_socket_instance
    
    err_str = str(err)
    
    # Handle token expiration loop
    if "-300" in err_str and "token" in err_str.lower():
        if data_socket_instance:
            try:
                print("🛑 Token expired (-300). Closing WebSocket to prevent loop.")
                data_socket_instance.close()
            except: pass
            data_socket_instance = None
            
        if main_loop:
            asyncio.run_coroutine_threadsafe(broadcast_log("⚠️ Data Stream Token Expired. Waiting for auth...", "error"), main_loop)
    else:
        if main_loop:
            asyncio.run_coroutine_threadsafe(broadcast_log(f"⚠️ Data Stream Error: {err}", "error"), main_loop)

def on_data_close(msg=None):
    print(f"📡 Data Socket Closed: {msg}")
    global data_socket_instance
    data_socket_instance = None # Ensure it gets restarted by the background worker if authenticated
    if main_loop:
        asyncio.run_coroutine_threadsafe(broadcast_log(f"📡 Data Stream Disconnected: {msg}", "warning"), main_loop)

def on_data_open(sync_only=False):
    """Subscribe to symbols once the socket is open."""
    global data_socket_instance
    if not sync_only:
        print("📡 Data Socket Opened.")
    if data_socket_instance:
        symbols = state.active_symbols + ["NSE:INDIAVIX-INDEX"]
        # Subscribe to any already recommended strikes
        for base in _market_cache.get("strikes", {}):
            for key in ["ce", "pe"]:
                for s in _market_cache["strikes"][base].get(key, []):
                    if s.get("symbol"): symbols.append(s["symbol"])
        
        data_socket_instance.subscribe(symbols=list(set(symbols)), data_type="SymbolUpdate")
        if main_loop and not sync_only:
            asyncio.run_coroutine_threadsafe(broadcast_log("📡 Real-time Data Stream Connected.", "success"), main_loop)


# Serve static files

# Serve static files
app.mount("/static", StaticFiles(directory="static"), name="static")

# Track active WebSocket connections
active_connections: Set[WebSocket] = set()

async def broadcast_log(msg: str, level: str = "info"):
    """Send a log message to all connected clients in IST."""
    if not active_connections:
        return
    
    import pytz
    from datetime import datetime
    ist = pytz.timezone('Asia/Kolkata')
    timestamp = datetime.now(ist).strftime("%H:%M:%S")
    payload = {
        "type": "log",
        "msg": msg,
        "level": level,
        "time": timestamp
    }
    
    disconnected = set()
    for ws in active_connections:
        try:
            await ws.send_json(payload)
        except:
            disconnected.add(ws)
    
    for ws in disconnected:
        active_connections.discard(ws)


@app.get("/")
async def index():
    """Serve the main dashboard."""
    return FileResponse("static/index.html")


@app.get("/api/status")
async def get_status():
    """Get connection and auth status."""
    client = get_client()
    authenticated = await asyncio.to_thread(client.is_authenticated)
    return {
        "authenticated": authenticated,
        "timestamp": datetime.now().isoformat(),
        "market_open": True,
    }


@app.get("/api/quotes")
@app.get("/api/spot")
async def get_spot():
    """Get live NIFTY spot price and VIX."""
    client = get_client()
    quotes = client.get_quotes(state.active_symbols + ["NSE:INDIAVIX-INDEX"])

    # Transform for compatibility
    res = {symbol: quotes.get(symbol, {}) for symbol in state.active_symbols}
    res["vix"] = quotes.get("NSE:INDIAVIX-INDEX", {})
    return res


@app.get("/api/candles/{resolution}")
async def get_candles(resolution: str, days: int = 10):
    """Get historical candles. resolution: 1, 5, 15, 60, D"""
    client = get_client()
    candles = await asyncio.to_thread(client.get_historical, "NSE:NIFTY50-INDEX", resolution, days)
    return {"candles": candles, "resolution": resolution, "count": len(candles)}


@app.get("/api/login")
async def login():
    """Generate and return Fyers login URL."""
    try:
        client = get_client()
        url = await asyncio.to_thread(client.get_login_url)
        return {"success": True, "url": url}
    except Exception as e:
        return {"success": False, "message": str(e)}

class AuthSubmission(BaseModel):
    code: str

@app.post("/api/submit-auth-code")
async def submit_auth_code(data: AuthSubmission):
    """Receive auth code (or full redirect URL) and exchange for token."""
    try:
        client = get_client()
        code = data.code.strip()
        
        # If the user pasted the full URL, extract the code
        if "auth_code=" in code:
            import urllib.parse as urlparse
            parsed = urlparse.urlparse(code)
            params = urlparse.parse_qs(parsed.query)
            if 'auth_code' in params:
                code = params['auth_code'][0]
        
        # Perform exchange
        res = await asyncio.to_thread(client.set_auth_code, code)
        return res
    except Exception as e:
        return {"success": False, "message": str(e)}

@app.get("/api/auth-status")
async def auth_status():
    """Check if we are authenticated."""
    try:
        client = get_client()
        status = await asyncio.to_thread(client.is_authenticated)
        token = os.getenv("FYERS_ACCESS_TOKEN", "")
        return {"authenticated": status, "token": token}
    except:
        return {"authenticated": False}


async def get_analysis(symbol="NSE:NIFTY50-INDEX"):
    """
    Core analysis logic for a specific symbol.
    Fetches historical data, detects levels, and generates signals.
    """
    now = datetime.now()
    now_ts = now.timestamp()
    
    # 15-second cache to respect Fyers API limits
    if symbol in _market_cache["analysis"]:
        cache = _market_cache["analysis"][symbol]
        if cache:
            cache_time = datetime.fromtimestamp(cache.get("timestamp", 0))
            if cache_time.date() == now.date() and (now_ts - cache.get("timestamp", 0) < 15):
                return cache["data"]

    client = get_client()
    try:
        # Use cached spot and VIX
        spot_data = _market_cache["spot"].get(symbol)
        vix_data = _market_cache.get("vix")
        
        if not spot_data:
            # Fallback to direct quote
            quotes = await asyncio.to_thread(client.get_quotes, [symbol])
            spot_data = quotes.get(symbol)
            if not spot_data: return None
        
        spot = spot_data.get("lp", 0)
        vix = (vix_data or {}).get("lp", 15.0)

        # Parallelize historical candle fetches
        # Use get_historical instead of get_candles
        tasks = [
            asyncio.to_thread(client.get_historical, symbol, "60", days_back=3),  # 1H candles
            asyncio.to_thread(client.get_historical, symbol, "5", days_back=4),   # 5M candles
            asyncio.to_thread(client.get_historical, symbol, "D", days_back=10),  # Daily candles
        ]
        
        candles_1h, candles_5m, candles_daily = await asyncio.gather(*tasks)

        if not candles_1h or not candles_5m:
            return None

        # Run signal engine
        result = generate_signals(candles_1h, candles_5m, spot, candles_daily, vix)

        # Find nearest expiry
        expiry = (_market_cache.get("strikes", {}).get(symbol) or {}).get("expiry")
        if not expiry:
            try:
                expiry = await asyncio.to_thread(client.find_nearest_expiry, spot)
            except:
                expiry = None
        result["expiry"] = expiry
        
        # Option Chain Analysis for AI
        if expiry:
            try:
                # Fetch 5 strikes on each side of ATM for OI analysis
                oc_data = await asyncio.to_thread(client.get_option_chain_strikes, spot, expiry["code"], 5)
                result["option_chain"] = oc_data
            except Exception as e:
                logger.error(f"Option Chain Fetch Error: {e}")
                result["option_chain"] = None
        else:
            result["option_chain"] = None

        # Filter OBs and FVGs strictly near Key Levels
        filtered_obs = []
        for ob in result.get("order_blocks", []):
            is_near = False
            for kl in result.get("key_levels", []):
                if abs(ob["top"] - kl["price"]) / kl["price"] < 0.004 or \
                   abs(ob["bottom"] - kl["price"]) / kl["price"] < 0.004:
                    is_near = True
                    break
            if is_near: filtered_obs.append(ob)
        
        filtered_fvgs = []
        for fvg in result.get("fvgs", []):
            is_near = False
            for kl in result.get("key_levels", []):
                if abs(fvg["top"] - kl["price"]) / kl["price"] < 0.004 or \
                   abs(fvg["bottom"] - kl["price"]) / kl["price"] < 0.004:
                    is_near = True
                    break
            if is_near: filtered_fvgs.append(fvg)
        
        result["order_blocks"] = filtered_obs
        result["fvgs"] = filtered_fvgs
        result["active_order_blocks"] = [ob for ob in filtered_obs if ob.get("active")]
        result["candles_5m"] = candles_5m
        
        # BANKNIFTY Correlation Data (v3.2.0)
        try:
            bnf_symbol = "NSE:NIFTYBANK-INDEX"
            bnf_quotes = await asyncio.to_thread(client.get_quotes, [bnf_symbol])
            bnf_data = bnf_quotes.get(bnf_symbol, {})
            result["bnf_spot"] = bnf_data.get("lp", 0)
            
            # Fetch 1H candles for BNF trend
            bnf_candles_1h = await asyncio.to_thread(client.get_historical, bnf_symbol, "60", days_back=2)
            from engine.key_levels import detect_trend
            bnf_trend_res = detect_trend(bnf_candles_1h)
            result["bnf_trend"] = bnf_trend_res.get("trend", "NEUTRAL")
        except Exception as e:
            logger.error(f"BNF Correlation Fetch Error: {e}")
            result["bnf_spot"] = 0
            result["bnf_trend"] = "UNKNOWN"
        
        # Heavyweight Monitoring (v3.3.0)
        try:
            heavy_symbols = ["NSE:RELIANCE-EQ", "NSE:HDFCBANK-EQ"]
            h_quotes = await asyncio.to_thread(client.get_quotes, heavy_symbols)
            h_data_list = []
            for hs in heavy_symbols:
                q = h_quotes.get(hs, {})
                # Fetch trend for each heavyweight
                h_candles_1h = await asyncio.to_thread(client.get_historical, hs, "60", days_back=2)
                from engine.key_levels import detect_trend
                h_tr = detect_trend(h_candles_1h)
                h_data_list.append({
                    "symbol": hs,
                    "lp": q.get("lp", 0),
                    "trend": h_tr.get("trend", "NEUTRAL")
                })
            result["heavyweights"] = h_data_list
        except Exception as e:
            logger.error(f"Heavyweight Fetch Error: {e}")
            result["heavyweights"] = []

        # Pass PnL context for AI
        result["pnl_today"] = state.pnl_today
        result["profit_target_met"] = state.profit_target_met

        # Filter out skipped signals
        if result["signals"]:
            filtered_signals = []
            for sig in result["signals"]:
                sig["symbol"] = symbol
                bottom = round(sig.get('entry_zone_bottom', 0), 2)
                sig_id = f"{sig.get('type')}_{sig.get('reason')}_{bottom}"
                if sig_id not in state.skipped_signals:
                    filtered_signals.append(sig)
            
            # AI Confirmation for filtered signals
            confirmed_signals = []
            for sig in filtered_signals:
                if sig.get("type") in ("CALL", "PUT"):
                    ai_result = await ai_engine.confirm_signal(symbol, sig, result)
                    sig.update(ai_result)
                confirmed_signals.append(sig)
                
            result["signals"] = confirmed_signals
        
        # Store in per-symbol cache
        _market_cache["analysis"][symbol] = {
            "data": result,
            "timestamp": now_ts
        }
        return result
        
    except Exception as e:
        print(f"⚠️ Analysis failed for {symbol}: {e}")
        return None

@app.get("/api/analysis")
async def get_analysis_api(symbol: str = "NSE:NIFTY50-INDEX"):
    """Fetch analysis for a specific symbol."""
    res = await get_analysis(symbol)
    if res: return res
    raise HTTPException(429, f"Rate limited or no data for {symbol}")

@app.get("/api/candles")
async def get_candles_api(symbol: str = "NSE:NIFTY50-INDEX", resolution: str = "5", days: int = 3):
    """Fetch candles for a specific symbol."""
    client = get_client()
    candles = await asyncio.to_thread(client.get_historical, symbol, resolution, days_back=days)
    return {"candles": candles}


@app.get("/api/test-signal")
async def test_signal():
    """Manually trigger a test signal to verify UI and History logic."""
    try:
        # Simulate a high-confidence signal
        test_sig = {
            "type": "CALL",
            "reason": "BREAKOUT ABOVE RESISTANCE (TEST)",
            "confidence": 98,
            "entry_zone_bottom": 24350.0,
            "entry_zone_top": 24380.0,
            "ai_confidence": 92,
            "ai_rationale": "High confluence of OB + FVG near Support. Trend is strongly bullish.",
            "ai_status": "confirmed",
            "advisory_only": False,
            "timestamp": datetime.now().timestamp()
        }
        
        # Get current spot from cache
        spot = _market_cache.get("spot", {}).get("lp", 24400.0)
        
        # Mock Strike Selection
        trade_details = {
            "strike": "24400 CE (TEST)",
            "entry": 150.0,
            "sl": 138.0,
            "target": 174.0,
            "score": 95.5,
            "moneyness": "ATM",
            "type_label": "ATM CALL (OI Optimized)",
            "ltp": 150.0,
            "symbol": "NSE:NIFTY24APR24400CE"
        }
            
        # 1. Log to history
        from engine.logger import log_signal
        log_signal([test_sig], spot, "⚪ TEST SIMULATED", trade_details)
        
        # 2. Forcibly Inject into Global Analysis Cache so UI picks it up
        global _analysis_cache
        _analysis_cache = {
            "timestamp": datetime.now().timestamp(),
            "data": {
                "signals": [test_sig],
                "strike_recommendations": [trade_details],
                "trend": {"trend": "BULLISH", "strength": 95},
                "key_levels": [],
                "order_blocks": [],
                "active_order_blocks": [],
                "fvgs": [],
                "bos_events": [],
                "spot": spot,
                "timestamp": datetime.now().timestamp()
            }
        }
        
        return {"success": True, "message": "Test signal injected and logged."}
    except Exception as e:
        return {"success": False, "message": str(e)}


@app.get("/api/option-chain")
async def get_option_chain():
    """Get option chain around ATM."""
    client = get_client()
    spot_data = client.get_quote("NSE:NIFTY50-INDEX")
    if not spot_data:
        raise HTTPException(500, "Could not fetch spot")
    spot = spot_data["lp"]

    expiry = client.find_nearest_expiry(spot)
    if not expiry:
        raise HTTPException(500, "No expiry found")

    chain = client.get_option_chain_strikes(spot, expiry["code"], 8)
    chain["expiry"] = expiry
    chain["spot"] = spot
    return chain


@app.get("/api/positions")
async def get_positions_api():
    """Get current positions."""
    client = get_client()
    positions_data = client.get_positions()
    overall_pnl = sum(p.get("pl", 0) for p in positions_data) if positions_data else 0
    return {"netPositions": positions_data, "overallPnl": overall_pnl}


@app.get("/api/orders")
async def get_orders():
    """Get order book."""
    client = get_client()
    orders = client.get_orders()
    return {"orders": orders}


@app.get("/api/funds")
async def get_funds():
    """Get account funds."""
    client = get_client()
    funds = client.get_funds()
    return {"funds": funds}

@app.get("/api/signal-history")
async def get_signal_history_api():
    """Get signal history from logger."""
    history = get_signal_history()
    return {"success": True, "history": history}


class SkipSignalRequest(BaseModel):
    index: int

@app.post("/api/skip-signal")
async def skip_signal(req: SkipSignalRequest):
    global _analysis_cache, _signal_lock, _skipped_signals
    if not _analysis_cache.get("data") or not _analysis_cache["data"].get("signals"):
        return {"success": False, "message": "No signals to skip"}
    
    try:
        signals = _analysis_cache["data"]["signals"]
        if 0 <= req.index < len(signals):
            sig = signals.pop(req.index)
            
            # Unique identifier for the signal to track skipping (rounded bottom)
            bottom = round(sig.get('entry_zone_bottom', 0), 2)
            sig_id = f"{sig.get('type')}_{sig.get('reason')}_{bottom}"
            state.add_skipped_signal(sig_id)
            
            spot = _market_cache.get("spot", {}).get("lp", 0)
            
            trade_details = None
            if _analysis_cache["data"].get("strike_recommendations"):
                best = _analysis_cache["data"]["strike_recommendations"][0]
                trade_details = {
                    "strike": best.get("strike"),
                    "entry": best.get("locked_price", best.get("ltp")),
                    "sl": best.get("sl"),
                    "target": best.get("target")
                }
            
            # Log to history
            from engine.logger import log_signal
            log_signal([sig], spot, "⚪ SKIPPED (User Manual Skip)", trade_details)
            
            # Clear signal lock if it was for this type
            if _signal_lock["type"] == sig.get("type"):
                 _signal_lock = {
                    "type": None,
                    "strike": None,
                    "buy_price": 0,
                    "sl_points": 0,
                    "target_points": 0,
                    "timestamp": 0
                 }
                 _analysis_cache["data"]["strike_recommendations"] = []
                 
            return {"success": True}
        else:
            return {"success": False, "message": "Invalid index"}
    except Exception as e:
         return {"success": False, "message": str(e)}


class OrderRequest(BaseModel):
    symbol: str
    qty: int
    side: str  # BUY or SELL
    order_type: str = "MARKET"
    product: str = "INTRADAY"
    limit_price: float = 0
    stop_price: float = 0
    sl_points: float = 12.0
    target_points: float = 0.0


@app.post("/api/order")
async def place_order(order: OrderRequest):
    """Place a manual trade order with safety checks."""
    client = get_client()
    
    # Strict Position Check for NIFTY
    if order.symbol.startswith("NSE:NIFTY"):
        live_pos = await asyncio.to_thread(client.get_positions)
        if any(p.get("symbol").startswith("NSE:NIFTY") and p.get("netQty", 0) != 0 for p in live_pos):
            return {"success": False, "message": "Active trade in progress. Close it before firing another."}

    result = await asyncio.to_thread(client.place_order, 
        symbol=order.symbol,
        qty=order.qty,
        side=order.side,
        order_type=order.order_type,
        product=order.product,
        limit_price=order.limit_price,
        stop_price=order.stop_price,
        sl_points=order.sl_points,
        target_points=order.target_points
    )
    # Log the trade action
    log_trade({
        "symbol": order.symbol,
        "qty": order.qty,
        "side": order.side,
        "price": 0, # Market price usually
        "status": "SUCCESS" if result.get("success") else "FAILED"
    })
    
    return result


# ===== GLOBAL DATA WORKER =====
async def market_data_worker():
    """Background task to fetch all market data and update the shared cache."""
    import pytz
    from datetime import datetime
    print("🚀 Market Data Worker started.", flush=True)
    await broadcast_log("🚀 Market Data Worker started.", "success")
    tick = 0
    while True:
        try:
            client = get_client()
            if not await asyncio.to_thread(client.is_authenticated):
                await broadcast_log("🔐 Fyers disconnected. Waiting for auth...", "warning")
                
                # Cleanup socket if unauthenticated to prevent bad loops
                global data_socket_instance
                if data_socket_instance:
                    try:
                        data_socket_instance.close()
                    except: pass
                    data_socket_instance = None
                    
                await asyncio.sleep(5)
                continue
                
            # Restart websocket if it was closed due to error but we are now authenticated
            if not data_socket_instance:
                threading.Thread(target=start_data_socket_thread, daemon=True).start()
                await asyncio.sleep(2) # Give it time to connect

            # --- DYNAMIC TASK MAPPING ---
            task_defs = {} # Name -> Coroutine
            
            # 1. Quotes (Fallback Polling if WebSocket is stale > 10s)
            is_stale = (datetime.now().timestamp() - _market_cache.get("last_update", 0)) > 10
            if is_stale:
                base_symbols = state.active_symbols + ["NSE:INDIAVIX-INDEX"]
                for base in _market_cache.get("strikes", {}):
                    for side in ["ce", "pe"]:
                        for s in _market_cache["strikes"][base].get(side, []):
                            if s.get("symbol"): base_symbols.append(s.get("symbol"))
                task_defs["quotes"] = asyncio.to_thread(client.get_quotes, list(set(base_symbols)))
            
            # 2. Sync Core Data (Funds, Positions, Orders) - Every 6s
            if tick % 2 == 0:
                task_defs["synced"] = asyncio.to_thread(client.get_synced_data)

            # 3. Analysis (every 30s) for all active symbols
            if tick % 10 == 0:
                for symbol in state.active_symbols:
                    task_defs[f"analysis_{symbol}"] = get_analysis(symbol)

            # 3. Option Chain Refresh (every 40s) for indices
            if tick % 15 == 0:
                for symbol in state.active_symbols:
                    if "INDEX" in symbol:
                        task_defs[f"chain_{symbol}"] = None # Handled below manually to stay safe
            if tick % 15 == 0:
                task_defs["chain"] = None # Handled below

            # Execute all active tasks
            task_names = list(task_defs.keys())
            active_coroutines = [task_defs[name] for name in task_names if task_defs[name] is not None]
            
            if active_coroutines:
                results_list = await asyncio.gather(*active_coroutines, return_exceptions=True)
                results = dict(zip([n for n in task_names if task_defs[n] is not None], results_list))
            else:
                results = {}

            # --- PROCESS RESULTS ---
            
            # Handle Quotes & Rate Limits
            quotes = results.get("quotes", {})
            if isinstance(quotes, Exception):
                await broadcast_log(f"⚠️ Quote Fetch Error: {quotes}", "error")
            elif isinstance(quotes, dict) and quotes.get("s") == "error":
                msg = quotes.get("message", "Unknown error")
                if "limit reached" in msg.lower() or "429" in msg.lower() or quotes.get("code") == 429:
                    await broadcast_log("⏳ Fyers Rate Limit hit (429). Cooling down for 30s...", "warning")
                    # Clear stale update flag to prevent immediate retry
                    _market_cache["last_update"] = datetime.now().timestamp()
                    await asyncio.sleep(30)
                else:
                    await broadcast_log(f"❌ Fyers API Error: {msg}", "error")
            else:
                for symbol in state.active_symbols:
                    if symbol in quotes:
                        _market_cache["spot"][symbol] = quotes[symbol]
                
                if "NSE:INDIAVIX-INDEX" in quotes:
                    _market_cache["vix"] = quotes["NSE:INDIAVIX-INDEX"]
                
                _market_cache["last_update"] = datetime.now().timestamp()
                
                # Update LTPs in strike cache
                for base in _market_cache["strikes"]:
                    for side in ["ce", "pe"]:
                        for s in _market_cache["strikes"][base].get(side, []):
                            sym = s.get("symbol")
                            if sym in quotes:
                                s["ltp"] = quotes[sym].get("lp", s["ltp"])
                                s["bid"] = quotes[sym].get("bid", s["bid"])
                                s["ask"] = quotes[sym].get("ask", s["ask"])

            synced_data = results.get("synced") or {}
            
            # Handle Positions
            positions = synced_data.get("positions")
            if isinstance(positions, list):
                total_pnl = sum(p.get("pl", 0) for p in positions)
                state.update_pnl(total_pnl)
                _market_cache["positions"] = positions
                _market_cache["active_positions"] = [p for p in positions if p.get("netQty", 0) != 0]
                _market_cache["total_pnl"] = round(total_pnl, 2)
                
                # Auto-trade cleanup
                for t in state.active_auto_trades[:]:
                    if not any(p.get("symbol") == t["symbol"] and p.get("netQty", 0) != 0 for p in positions):
                        state.remove_active_trade(t["symbol"])
            elif isinstance(positions, dict) and positions.get("s") == "error":
                await broadcast_log(f"⚠️ Positions Update Failed: {positions.get('message')}", "warning")

            # Handle Synced Data (Funds, Positions, Orders)
            synced = results.get("synced", {})
            if isinstance(synced, dict):
                # Update Market Cache with synced results
                if synced.get("positions"): _market_cache["active_positions"] = synced["positions"]
                if synced.get("orders"): _market_cache["orders"] = synced["orders"][-10:]
                if synced.get("funds"): _market_cache["funds"] = synced["funds"]
                
                if synced.get("cooldown"):
                    await broadcast_log("⏳ Fyers Global Cooldown active. Waiting...", "warning")
                    await asyncio.sleep(5)

            # Handle Fallback Quotes (if WebSocket is stale)
            quotes_res = results.get("quotes")
            if isinstance(quotes_res, dict):
                if "NSE:NIFTY50-INDEX" in quotes_res:
                    _market_cache["spot"] = quotes_res["NSE:NIFTY50-INDEX"]
                if "NSE:INDIAVIX-INDEX" in quotes_res:
                    _market_cache["vix"] = quotes_res["NSE:INDIAVIX-INDEX"]
                for key in ["ce_strikes", "pe_strikes"]:
                    for s in _market_cache.get(key, []):
                        if s.get("symbol") in quotes_res:
                            s["ltp"] = quotes_res[s["symbol"]].get("lp", s.get("ltp", 0))
                _market_cache["last_update"] = datetime.now().timestamp()
            
            # Handle Orders
            orders = results.get("orders")

            # --- PnL and LOSS TRACKING (v3.2.0) ---
            positions = results.get("positions")
            if isinstance(positions, dict) and positions.get("code") == 200:
                pos_list = positions.get("netPositions", [])
                # Store active positions for UI
                _market_cache["active_positions"] = [p for p in pos_list if p.get("netQty", 0) != 0]
                
                realized_pnl = sum(float(p.get("realized_profit", 0)) for p in pos_list)
                total_pnl = sum(float(p.get("realized_profit", 0)) + float(p.get("unrealized_profit", 0)) for p in pos_list)
                
                # Detect Loss (Realized PnL decreased)
                if hasattr(state, '_last_realized_pnl') and realized_pnl < state._last_realized_pnl:
                    state.record_loss()
                    print(f"🛑 Loss Detected! Realized PnL: {realized_pnl} (was {state._last_realized_pnl})")
                
                state._last_realized_pnl = realized_pnl
                state.update_pnl(total_pnl)

            # --- OPTION CHAIN SEQUENTIAL REFRESH (to avoid more 429) ---
            if tick % 15 == 0:
                for symbol in state.active_symbols:
                    if "INDEX" not in symbol: continue
                    cached_spot = _market_cache["spot"].get(symbol)
                    spot = (cached_spot or {}).get("lp", 0)
                    if spot > 0:
                        try:
                            expiry = await asyncio.to_thread(client.find_nearest_expiry, spot)
                            if expiry:
                                chain = await asyncio.to_thread(client.get_option_chain_strikes, spot, expiry["code"], 8)
                                dte = 5
                                try:
                                    exp_date = datetime.strptime(expiry["date"], "%Y-%m-%d").date()
                                    dte = (exp_date - datetime.now().date()).days
                                except: pass
                                
                                if symbol not in _market_cache["strikes"]: _market_cache["strikes"][symbol] = {}
                                _market_cache["strikes"][symbol]["ce"] = get_strike_recommendations(chain, "CALL", spot, dte=dte)
                                _market_cache["strikes"][symbol]["pe"] = get_strike_recommendations(chain, "PUT", spot, dte=dte)
                                _market_cache["strikes"][symbol]["expiry"] = expiry
                                await asyncio.sleep(1) # Small gap between symbols
                        except Exception as e:
                            print(f"⚠️ Chain refresh failed for {symbol}: {e}")

            # --- HARD INTRADAY EXIT (15:15 IST) ---
            try:
                ist = pytz.timezone('Asia/Kolkata')
                now_ist = datetime.now(ist)
                # Only run between 15:15 and 15:30 to avoid continuous loops all evening
                if now_ist.hour == 15 and 15 <= now_ist.minute <= 30:
                    active_pos = _market_cache.get("active_positions", [])
                    if active_pos:
                        # Use a local flag or state to avoid spamming every 3 seconds
                        if not getattr(state, 'hard_exit_triggered', False):
                            await broadcast_log(f"⏰ HARD EXIT (15:15 IST): Squaring off {len(active_pos)} positions.", "warning")
                            for p in active_pos:
                                try:
                                    qty = abs(p.get("netQty", 0))
                                    if qty > 0:
                                        client.place_order(symbol=p["symbol"], qty=qty, side="SELL" if p["netQty"] > 0 else "BUY")
                                except Exception as e:
                                    print(f"❌ Hard Exit order failed for {p['symbol']}: {e}")
                            state.hard_exit_triggered = True
                else:
                    # Reset flag outside the window so it works tomorrow
                    state.hard_exit_triggered = False
            except Exception as e:
                print(f"⚠️ Hard Exit Monitor Error: {e}")

            tick += 1
            await asyncio.sleep(3.0)
        except Exception as e:
            print(f"⚠️ Market Data Worker Error: {e}")
            await asyncio.sleep(2)

@app.websocket("/ws/live")
async def websocket_live(ws: WebSocket):
    """Broadcast live market data from the global cache to connected clients."""
    await ws.accept()
    active_connections.add(ws)
    print(f"📡 WebSocket connected. Total: {len(active_connections)}")

    last_spot_update = 0
    auth_check_tick = 0
    is_auth = False
    
    # Send immediate "Connecting" state to UI
    await ws.send_json({
        "type": "auth_status",
        "authenticated": "connecting"
    })

    try:
        while True:
            # Periodic auth check (immediately and then every 10 seconds)
            if auth_check_tick % 10 == 0:
                try:
                    client = get_client()
                    is_auth = await asyncio.to_thread(client.check_auth_status)
                except:
                    is_auth = False
            
            # Send Auth Status
            if auth_check_tick % 5 == 0:
                print(f"🔐 Auth Check: {'✅' if is_auth else '❌'}")
            
            await ws.send_json({
                "type": "auth_status",
                "authenticated": is_auth,
                "message": "Connected" if is_auth else "Token Expired"
            })

            # Check if cache has new data
            if _market_cache["last_update"] > last_spot_update:
                last_spot_update = _market_cache["last_update"]
                
                # Send Spot updates for all active symbols
                spots_data = {}
                for symbol in state.active_symbols:
                    if symbol in _market_cache["spot"]:
                        s_data = _market_cache["spot"][symbol]
                        spots_data[symbol] = {
                            "lp": s_data.get("lp", 0),
                            "change": s_data.get("ch", 0),
                            "change_pct": s_data.get("chp", 0),
                        }
                
                vix = _market_cache["vix"] or {}
                await ws.send_json({
                    "type": "market_update",
                    "spots": spots_data,
                    "vix": {
                        "lp": vix.get("lp", 0),
                        "change": vix.get("chp", 0),
                    },
                    "timestamp": datetime.now().isoformat(),
                })

                # Aggregated Analysis (Signals)
                all_signals = []
                for symbol in state.active_symbols:
                    if symbol in _market_cache["analysis"]:
                        res = _market_cache["analysis"][symbol]["data"]
                        if res and res.get("signals"):
                            all_signals.extend(res["signals"])
                
                if all_signals:
                    await ws.send_json({
                        "type": "analysis",
                        "analysis": {
                            "signals": all_signals,
                            # We send the strike recommendations for the first signal found
                            "strike_recommendations": _market_cache["analysis"][all_signals[0]["symbol"]]["data"].get("strike_recommendations", [])
                        }
                    })

                # Send Positions and Stats
                await ws.send_json({
                    "type": "positions",
                    "positions": _market_cache["positions"],
                    "active_positions": _market_cache["active_positions"],
                    "total_pnl": _market_cache["total_pnl"],
                    "realized_pnl": _market_cache["realized_pnl"],
                    "unrealized_pnl": _market_cache["unrealized_pnl"],
                    "automation_stats": {
                        "trades": state.trades_today,
                        "pnl": state.pnl_today,
                        "enabled": state.automation_enabled
                    }
                })

                # Send Orders
                if _market_cache["orders"]:
                    await ws.send_json({
                        "type": "orders",
                        "orders": _market_cache["orders"],
                    })

                # Send Funds
                if _market_cache["funds"]:
                    await ws.send_json({
                        "type": "funds",
                        "funds": _market_cache["funds"],
                    })

                # Send Strike Updates (Respecting Signal Lock)
                if _market_cache.get("ce_strikes") or _market_cache.get("pe_strikes"):
                    ce_data = [dict(s) for s in _market_cache.get("ce_strikes", [])]
                    pe_data = [dict(s) for s in _market_cache.get("pe_strikes", [])]
                    
                    # Apply Lock to Live Data before broadcast
                    if _signal_lock["type"] == "CALL":
                        for s in ce_data:
                            if s["symbol"] == _signal_lock["symbol"]:
                                s["locked_price"] = _signal_lock["buy_price"]
                                s["sl"] = _signal_lock["buy_price"] - _signal_lock["sl_points"]
                                s["target"] = _signal_lock["buy_price"] + _signal_lock["target_points"]
                    elif _signal_lock["type"] == "PUT":
                        for s in pe_data:
                            if s["symbol"] == _signal_lock["symbol"]:
                                s["locked_price"] = _signal_lock["buy_price"]
                                s["sl"] = _signal_lock["buy_price"] - _signal_lock["sl_points"]
                                s["target"] = _signal_lock["buy_price"] + _signal_lock["target_points"]

                    spot_price = _market_cache["spot"].get("lp", 0) if _market_cache.get("spot") else 0
                    
                    await ws.send_json({
                        "type": "strike_update",
                        "ce_strikes": ce_data,
                        "pe_strikes": pe_data,
                        "expiry": _market_cache.get("expiry"),
                        "spot": spot_price,
                    })

            auth_check_tick += 1
            await asyncio.sleep(1) # Frequency of UI push
    except WebSocketDisconnect:
        pass
    except Exception as e:
        print(f"WebSocket error: {e}")
    finally:
        active_connections.discard(ws)
        print(f"📡 WebSocket disconnected. Total: {len(active_connections)}")

def _is_market_open() -> bool:
    """Check if market is currently open (9:15 - 15:30 IST, weekdays)."""
    now = datetime.now()
    if now.weekday() >= 5:
        return False
    market_start = now.replace(hour=9, minute=15, second=0)
    market_end = now.replace(hour=15, minute=30, second=0)
    return market_start <= now <= market_end





@app.get("/api/automation")
async def get_automation():
    return {
        "enabled": state.automation_enabled,
        "trades_today": state.trades_today,
        "pnl_today": state.pnl_today,
        "max_trades": state.max_trades_per_day,
        "max_loss": state.max_loss_per_day
    }

@app.post("/api/automation/toggle")
async def toggle_automation(enabled: bool):
    state.automation_enabled = enabled
    state.save()
    return {"success": True, "enabled": state.automation_enabled}

@app.get("/api/version")
async def get_version():
    """Return dashboard version and system info."""
    return {
        "version": VERSION,
        "name": "Sritej Trading Dashboard",
        "active_symbols": len(state.active_symbols),
        "automation": state.automation_enabled,
        "ai_active": ai_engine.enabled
    }

async def trailing_monitor():
    """Background task to monitor active auto-trades and trail SL/Target at 1:1 RR."""
    while True:
        try:
            if not state.automation_enabled or not state.active_auto_trades:
                await asyncio.sleep(5)
                continue
            
            client = get_client()
            if not client.is_authenticated():
                await asyncio.sleep(10)
                continue
                
            # Get symbols we need to monitor
            symbols = [t["symbol"] for t in state.active_auto_trades if not t["trailed"]]
            if not symbols:
                await asyncio.sleep(5)
                continue
                
            quotes = await asyncio.to_thread(client.get_quotes, symbols)
            
            for t in state.active_auto_trades:
                if t["trailed"]: continue
                
                sym = t["symbol"]
                quote = quotes.get(sym, {})
                ltp = quote.get("lp", 0)
                
                if ltp == 0: continue
                
                entry = t["entry_price"]
                sl_pts = t["sl_points"]
                side = t.get("side", "BUY")
                
                # Check for 1:1 Risk Reward Milestone
                if side == "BUY" and ltp >= entry + sl_pts:
                    print(f"🚀 Trailing Milestone Hit for {sym}! LTP: {ltp} >= Entry {entry} + SL {sl_pts}")
                    
                    # Trail SL to Entry
                    if t["sl_order_id"]:
                        await asyncio.to_thread(
                            client.modify_order,
                            order_id=t["sl_order_id"],
                            order_type=4, # SL-LIMIT
                            stop_price=entry,
                            limit_price=entry - 1
                        )
                        print(f"🛡️ Trailed SL to Break-Even (₹{entry})")
                        
                    # Extend Target to 3x SL
                    if t["tgt_order_id"]:
                        new_target = entry + (3 * sl_pts)
                        await asyncio.to_thread(
                            client.modify_order,
                            order_id=t["tgt_order_id"],
                            order_type=1, # LIMIT
                            limit_price=new_target
                        )
                        print(f"🎯 Extended Target to ₹{new_target}")
                        
                    state.mark_trade_trailed(t["sl_order_id"])

        except Exception as e:
            print(f"🔥 Trailing monitor error: {e}")
            
        await asyncio.sleep(2) # Monitor every 2 seconds

async def automation_loop():
    """Continuously monitor symbols and execute AI-confirmed signals."""
    print("🤖 Automation Loop Started.")
    while True:
        try:
            if not state.automation_enabled:
                await asyncio.sleep(10)
                continue

            for symbol in state.active_symbols:
                # 1. Get Analysis (AI confirmation happens inside)
                analysis = await get_analysis(symbol)
                if not analysis or not analysis.get("signals"):
                    continue

                # 2. Check for actionable signals
                for sig in analysis["signals"]:
                    if sig.get("type") not in ("CALL", "PUT"):
                        continue
                    
                    # 3. Check technical confidence
                    if sig.get("confidence", 0) < 80:
                        continue
                        
                    # 4. Check AI confidence (CRITICAL)
                    # If AI key is missing, this uses the mocked confidence
                    ai_conf = sig.get("ai_confidence", 0)
                    
                    # Dynamic Threshold (v3.3.0)
                    required_conf = 85
                    if state.profit_target_met:
                        required_conf = 95
                        logger.info(f"🛡️ CONSERVATIVE MODE ACTIVE (Profit Target Met). Threshold: {required_conf}%")

                    if ai_conf < required_conf:
                        logger.info(f"⏭️ Skipping {symbol} {sig['type']}: AI Confidence {ai_conf}% < {required_conf}%.")
                        continue

                    # 5. Check if we can trade (Limits, existing trades)
                    can_trade, reason = state.can_trade(symbol.replace(':','_'))
                    if not can_trade:
                        continue

                    # 6. Execute Trade
                    logger.info(f"🚀 AI CONFIRMED TRADE: {symbol} {sig['type']} at {analysis['spot']}")
                    # Note: We'd call place_auto_order here. 
                    # For safety in this beta, I'll log it first or implement a safe wrapper.
                    # await execute_auto_trade(symbol, sig, analysis)

        except Exception as e:
            logger.error(f"Automation loop error: {e}")
        
        await asyncio.sleep(5) # Fast scan frequency (v3.2.0)


@app.on_event("startup")
async def startup_event():
    """Start background tasks."""
    global main_loop
    main_loop = asyncio.get_running_loop()
    
    # Start background tasks
    asyncio.create_task(trailing_monitor())
    asyncio.create_task(automation_loop())
    asyncio.create_task(market_data_worker())
    
    # Start the Data Stream Thread for Real-time Prices
    print("📡 Initializing Real-time Data Stream...")
    threading.Thread(target=start_data_socket_thread, daemon=True).start()

if __name__ == "__main__":
    import uvicorn
    import os
    port = int(os.environ.get("PORT", 8000))
    print(f"🚀 Starting NIFTY Trading Dashboard on http://localhost:{port}")
    uvicorn.run(app, host="0.0.0.0", port=port)
