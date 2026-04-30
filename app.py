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
VERSION = "2.1.1"
state = TradingState()

# Performance-optimized Global Caches
# Configuration now managed via state.active_symbols
@app.get("/api/scripts")
async def get_scripts():
    return {"scripts": state.active_symbols}

@app.post("/api/scripts/add")
async def add_script(data: Dict):
    symbol = data.get("symbol", "").upper()
    if not symbol: return {"success": False, "message": "Symbol required"}
    state.add_symbol(symbol)
    # Trigger an immediate WebSocket sync
    on_data_open(sync_only=True)
    return {"success": True, "scripts": state.active_symbols}

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

def on_data_close():
    print("📡 Data Socket Closed.")
    global data_socket_instance
    data_socket_instance = None # Ensure it gets restarted by the background worker if authenticated
    if main_loop:
        asyncio.run_coroutine_threadsafe(broadcast_log("📡 Data Stream Disconnected.", "warning"), main_loop)

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
        # We use client.get_candles which is more reliable than get_historical
        tasks = [
            asyncio.to_thread(client.get_candles, symbol, "1", days=3),   # 1H candles (approx)
            asyncio.to_thread(client.get_candles, symbol, "5", days=4),   # 5M candles
            asyncio.to_thread(client.get_candles, symbol, "D", days=10),  # Daily candles
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
        
        # Filter out skipped signals
        if result["signals"]:
            filtered_signals = []
            for sig in result["signals"]:
                sig["symbol"] = symbol
                bottom = round(sig.get('entry_zone_bottom', 0), 2)
                sig_id = f"{sig.get('type')}_{sig.get('reason')}_{bottom}"
                if sig_id not in state.skipped_signals:
                    filtered_signals.append(sig)
            result["signals"] = filtered_signals
        
        # Store in per-symbol cache
        _market_cache["analysis"][symbol] = {
            "data": result,
            "timestamp": now_ts
        }
        return result
        
    except Exception as e:
        print(f"⚠️ Analysis failed for {symbol}: {e}")
        return None
        
        # === SIGNAL LOCK LOGIC ===
        global _signal_lock
        if result["signals"] and expiry:
            top_signal = result["signals"][0]
            sig_type = top_signal["type"]
            
            # Use cached strikes/prices if SAME TYPE signal was seen in the last 10 minutes
            if _signal_lock["type"] == sig_type and (now_ts - _signal_lock["timestamp"]) < 600:
                result["strike_recommendations"] = [{
                    "symbol": _signal_lock["symbol"],
                    "strike": _signal_lock["strike"],
                    "ltp": _signal_lock["buy_price"], # fallback
                    "locked_price": _signal_lock["buy_price"],
                    "sl": _signal_lock["buy_price"] - _signal_lock["sl_points"],
                    "target": _signal_lock["buy_price"] + _signal_lock["target_points"],
                    "sl_points": _signal_lock["sl_points"],
                    "target_points": _signal_lock["target_points"]
                }]
            else:
                recs = []
                if expiry and expiry.get("code"):
                    chain = await asyncio.to_thread(client.get_option_chain_strikes, spot, expiry["code"], 8)
                    # Calculate DTE (Days to Expiry)
                    dte = 5
                    try:
                        exp_date = datetime.strptime(expiry["date"], "%Y-%m-%d").date()
                        dte = (exp_date - datetime.now().date()).days
                    except: pass
                    
                    recs = get_strike_recommendations(chain, sig_type, spot, dte=dte)
                if recs:
                    best = recs[0]
                    # Calculate SL based on option chart history
                    sl_pts = 12.0
                    try:
                        opt_candles = await asyncio.to_thread(client.get_historical, best["symbol"], "5", 3)
                        if opt_candles and len(opt_candles) >= 5:
                            # Look back 15 candles for the recent lower low
                            recent_candles = opt_candles[-15:] if len(opt_candles) >= 15 else opt_candles
                            recent_low = min(c["low"] for c in recent_candles)
                            # SL distance is entry to recent low, plus a 2-point buffer
                            calculated_sl_dist = best["ltp"] - recent_low + 2.0
                            # Enforce min 5 and max 30 points
                            sl_pts = max(5.0, min(30.0, calculated_sl_dist))
                    except: pass
                    
                    # Store in Lock (Refreshes the 10 min window)
                    _signal_lock = {
                        "type": sig_type,
                        "symbol": best["symbol"],
                        "strike": best["strike"],
                        "buy_price": best["ltp"],
                        "sl_points": round(sl_pts, 1),
                        "target_points": round(sl_pts * 2.0, 1),
                        "timestamp": now_ts
                    }
                    
                    best["locked_price"] = _signal_lock["buy_price"]
                    best["sl_points"] = _signal_lock["sl_points"]
                    best["target_points"] = _signal_lock["target_points"]
                    best["sl"] = best["ltp"] - best["sl_points"]
                    best["target"] = best["ltp"] + best["target_points"]
                    result["strike_recommendations"] = [best]
                else:
                    # Fallback to market cache if API chain fetch fails
                    cached_recs = _market_cache.get("ce_strikes" if sig_type == "CALL" else "pe_strikes", [])
                    if cached_recs:
                        best = cached_recs[0]
                        best["sl_points"] = 12.0
                        best["target_points"] = 24.0
                        best["sl"] = best["ltp"] - best["sl_points"]
                        best["target"] = best["ltp"] + best["target_points"]
                        result["strike_recommendations"] = [best]
                    else:
                        result["strike_recommendations"] = []
                        result["auth_error"] = "Token expired or limit reached. Please log in again."
        else:
            # DO NOT clear the lock immediately if signals are empty. 
            # This allows the prices to "stay" if the signal flickers back in.
            # We only provide empty recommendations if there is truly no signal.
            result["strike_recommendations"] = []

        # Update cache on success
        _analysis_cache = {
            "data": result,
            "timestamp": now_ts
        }
    except Exception as e:
        print(f"🔥 Analysis Error: {e}")
        if _analysis_cache["data"]: return _analysis_cache["data"]
        raise HTTPException(500, str(e))

    # === AUTOMATION ENGINE ===
    if result["signals"]:
        top_sig = result["signals"][0]
        action_status = "⚪ SKIPPED (Advisory / Low Conf)"
        
        # Only auto-trade if high confidence and we have strike recs
        if top_sig["confidence"] >= 65 and not top_sig.get("advisory_only"):
            if not result.get("strike_recommendations"):
                action_status = "🔴 SKIPPED (No Strikes)"
            elif not state.automation_enabled:
                action_status = "🟡 SKIPPED (Auto Off)"
            else:
                can_trade, reason = state.can_trade("NSE:NIFTY")
                if not can_trade:
                    action_status = f"🔴 SKIPPED ({reason})"
                else:
                    best_strike = result["strike_recommendations"][0]
                    
                    # Read explicitly calculated SL/TGT points from the option chart
                    opt_sl_pts = best_strike.get("sl_points", 12.0)
                    opt_tgt_pts = best_strike.get("target_points", opt_sl_pts * 2.0)
                    
                    # Refresh lock with option values
                    _signal_lock = {
                        "type": top_sig["type"],
                        "symbol": best_strike["symbol"],
                        "strike": best_strike["strike"],
                        "buy_price": best_strike["ltp"], # Option Entry
                        "sl_points": opt_sl_pts,
                        "target_points": opt_tgt_pts,
                        "timestamp": datetime.now().timestamp()
                    }
                    
                    opt_symbol = _signal_lock["symbol"]
                    opt_ltp = _signal_lock["buy_price"]
                    sl_points = max(1, round(_signal_lock["sl_points"]))
                    target_points = max(1, round(_signal_lock["target_points"]))
                    
                    print(f"🤖 AUTO-TRADE: Executing {top_sig['type']} BUY on {best_strike['strike']} at STRATEGY price {opt_ltp} | Option SL {sl_points} | Option TGT {target_points}")
                    
                    try:
                        # Final safety check
                        live_pos = await asyncio.to_thread(client.get_positions)
                        if any(p.get("symbol").startswith("NSE:NIFTY") and p.get("netQty", 0) != 0 for p in live_pos):
                            action_status = "🔴 SKIPPED (Live position exists)"
                        else:
                            order_res = client.place_order(
                                symbol=opt_symbol,
                                qty=65, # Updated Nifty Lot
                                side="BUY",
                                order_type="MARKET",
                                product="INTRADAY", # Forced Intraday
                                limit_price=0, # Force 0 to auto-fetch the live option premium in fyers_client
                                sl_points=sl_points,
                                target_points=target_points
                            )
                        if order_res.get("success"):
                            action_status = "🟢 TRADED"
                            state.record_trade()
                            state.add_active_trade(
                                symbol=opt_symbol,
                                entry_price=opt_ltp,
                                sl_points=sl_points,
                                side="BUY",
                                sl_order_id=order_res.get("sl_order_id"),
                                tgt_order_id=order_res.get("tgt_order_id")
                            )
                        else:
                            action_status = f"🔴 FAILED: {order_res.get('message', 'Unknown')}"
                    except Exception as e:
                        print(f"❌ Auto-trade failed: {e}")
                        action_status = f"🔴 ERROR: {e}"
        
        # Prepare trade details for history logging
        trade_details = None
        if result.get("strike_recommendations") and len(result["strike_recommendations"]) > 0:
            best = result["strike_recommendations"][0]
            trade_details = {
                "strike": best.get("strike"),
                "entry": best.get("locked_price", best.get("ltp")),
                "sl": best.get("sl"),
                "target": best.get("target")
            }
        
        # Log the signal with its final status and trade specs
        log_signal([top_sig], spot, action_status, trade_details)

    return result


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
                _market_cache["active_positions"] = [p for p in positions if p.get("qty", 0) != 0]
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
            
            # Legacy result handlers (can be removed later, but kept for compatibility)
            # Handle Orders
            orders = results.get("orders")

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
                if now_ist.hour == 15 and now_ist.minute >= 15:
                    active_pos = _market_cache.get("active_positions", [])
                    if active_pos:
                        await broadcast_log(f"⏰ HARD EXIT (15:15 IST): Squaring off {len(active_pos)} positions.", "warning")
                        for p in active_pos:
                            try:
                                client.place_order(symbol=p["symbol"], qty=abs(p["qty"]), side="SELL" if p["qty"] > 0 else "BUY")
                            except: pass
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


@app.get("/api/login")
async def login():
    """Trigger Fyers authentication."""
    try:
        client = get_client()
        # The FyersClient uses smart authentication via MCP
        # We just need to trigger the status check which handles the redirect
        client.check_auth_status() # This will raise an exception or handle login
        return {"success": True, "message": "Login initiated"}
    except Exception as e:
        return {"success": False, "message": str(e)}




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

@app.on_event("startup")
async def startup_event():
    """Start background tasks."""
    global main_loop
    main_loop = asyncio.get_running_loop()
    
    # Start the Data Stream Thread for Real-time Prices
    print("📡 Initializing Real-time Data Stream...")
    threading.Thread(target=start_data_socket_thread, daemon=True).start()
    
    asyncio.create_task(market_data_worker())
    asyncio.create_task(trailing_monitor())

if __name__ == "__main__":
    import uvicorn
    import os
    port = int(os.environ.get("PORT", 8000))
    print(f"🚀 Starting NIFTY Trading Dashboard on http://localhost:{port}")
    uvicorn.run(app, host="0.0.0.0", port=port)
