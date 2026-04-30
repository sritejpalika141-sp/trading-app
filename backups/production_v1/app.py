"""
NIFTY Options Trading Dashboard — FastAPI Backend
"""
import os
import json
import asyncio
from datetime import datetime
from typing import Optional, List, Set
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

from fyers_client import get_client
from engine.signals import generate_signals
from engine.strikes import select_strike, get_strike_recommendations
from engine.logger import log_signal, log_trade, get_signal_history
from engine.automation import TradingState

app = FastAPI(title="NIFTY Trading Dashboard")
state = TradingState()

# Performance-optimized Global Caches
_analysis_cache = {
    "data": None,
    "timestamp": 0
}

_market_cache = {
    "spot": None,
    "vix": None,
    "positions": [],
    "active_positions": [],
    "total_pnl": 0.0,
    "realized_pnl": 0.0,
    "unrealized_pnl": 0.0,
    "orders": [],
    "funds": None,
    "ce_strikes": [],
    "pe_strikes": [],
    "expiry": None,
    "last_update": 0
}

# Serve static files
app.mount("/static", StaticFiles(directory="static"), name="static")

# Track active WebSocket connections
active_connections: Set[WebSocket] = set()


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
        "market_open": _is_market_open(),
    }


@app.get("/api/quotes")
@app.get("/api/spot")
async def get_spot():
    """Get live NIFTY spot price and VIX."""
    client = get_client()
    quotes = client.get_quotes(["NSE:NIFTY50-INDEX", "NSE:INDIAVIX-INDEX"])

    nifty = quotes.get("NSE:NIFTY50-INDEX", {})
    vix = quotes.get("NSE:INDIAVIX-INDEX", {})

    if not nifty:
        raise HTTPException(500, "Could not fetch NIFTY data")

    return {
        "spot": nifty.get("lp", 0),
        "open": nifty.get("open_price", 0),
        "high": nifty.get("high_price", 0),
        "low": nifty.get("low_price", 0),
        "prev_close": nifty.get("prev_close_price", 0),
        "change": nifty.get("ch", 0),
        "change_pct": nifty.get("chp", 0),
        "vix": vix.get("lp", 0),
        "vix_change": vix.get("chp", 0),
        "timestamp": datetime.now().isoformat(),
    }


@app.get("/api/candles/{resolution}")
async def get_candles(resolution: str, days: int = 10):
    """Get historical candles. resolution: 1, 5, 15, 60, D"""
    client = get_client()
    candles = await asyncio.to_thread(client.get_historical, "NSE:NIFTY50-INDEX", resolution, days)
    return {"candles": candles, "resolution": resolution, "count": len(candles)}


@app.get("/api/login")
async def login():
    """Trigger Fyers authentication."""
    try:
        client = get_client()
        # Trigger re-init which might pick up new tokens
        await asyncio.to_thread(client.is_authenticated)
        return {"success": True, "message": "Login initiated"}
    except Exception as e:
        return {"success": False, "message": str(e)}

@app.get("/api/auth-status")
async def auth_status():
    """Check if we are authenticated."""
    try:
        client = get_client()
        status = await asyncio.to_thread(client.is_authenticated)
        return {"authenticated": status}
    except:
        return {"authenticated": False}


@app.get("/api/analysis")
async def get_analysis():
    """Run full analysis: key levels, OBs, FVGs, signals."""
    global _analysis_cache
    now = datetime.now().timestamp()
       # Return cached analysis if less than 30s old
    if _analysis_cache["data"] and (now - _analysis_cache["timestamp"] < 30):
        return _analysis_cache["data"]

    client = get_client()

    try:
        # Parallelize spot and VIX fetches (with 10s timeout)
        spot_task = asyncio.to_thread(client.get_quote, "NSE:NIFTY50-INDEX")
        vix_task = asyncio.to_thread(client.get_quote, "NSE:INDIAVIX-INDEX")
        
        try:
            spot_data, vix_data = await asyncio.wait_for(
                asyncio.gather(spot_task, vix_task), timeout=10
            )
        except asyncio.TimeoutError:
            print("⏱️ Spot/VIX fetch timed out (10s)")
            if _analysis_cache["data"]: return _analysis_cache["data"]
            raise HTTPException(504, "Fyers API timeout fetching spot data")
        
        if not spot_data:
            # Fallback to cache if possible
            if _analysis_cache["data"]: return _analysis_cache["data"]
            raise HTTPException(500, "Could not fetch spot price")
            
        spot = spot_data.get("lp", 0)
        vix = vix_data.get("lp", 0)

        # Parallelize all historical candle fetches (with 15s timeout)
        h1_task = asyncio.to_thread(client.get_historical, "NSE:NIFTY50-INDEX", "60", 15)
        m5_task = asyncio.to_thread(client.get_historical, "NSE:NIFTY50-INDEX", "5", 3)
        daily_task = asyncio.to_thread(client.get_historical, "NSE:NIFTY50-INDEX", "D", 5)
        
        try:
            candles_1h, candles_5m, candles_daily = await asyncio.wait_for(
                asyncio.gather(h1_task, m5_task, daily_task), timeout=15
            )
        except asyncio.TimeoutError:
            print("⏱️ Candle fetch timed out (15s)")
            if _analysis_cache["data"]:
                print("⚠️ Serving stale cache due to timeout.")
                return _analysis_cache["data"]
            raise HTTPException(504, "Fyers API timeout fetching candle data")

        if not candles_1h or not candles_5m:
            # If rate limited, return last good cache but older
            if _analysis_cache["data"]:
                print("⚠️ Rate limited. Serving stale cache.")
                return _analysis_cache["data"]
            raise HTTPException(500, "Could not fetch candle data (Rate Limited)")

        # Run signal engine
        result = generate_signals(candles_1h, candles_5m, spot, candles_daily, vix)

        # Find nearest expiry (with 10s timeout)
        try:
            expiry = await asyncio.wait_for(
                asyncio.to_thread(client.find_nearest_expiry, spot), timeout=10
            )
        except asyncio.TimeoutError:
            print("⏱️ Expiry fetch timed out")
            expiry = None
        result["expiry"] = expiry

        # Include 5m candles in the result so the frontend doesn't have to fetch them separately
        result["candles_5m"] = candles_5m
        
        # If we have signals, get strike recommendations
        if result["signals"] and expiry:
            top_signal = result["signals"][0]
            chain = await asyncio.to_thread(client.get_option_chain_strikes, spot, expiry["code"], 8)
            recs = get_strike_recommendations(chain, top_signal["type"], spot)
            result["strike_recommendations"] = recs
            result["option_chain_summary"] = {
                "atm": chain["atm"],
                "call_volume": sum(c["volume"] for c in chain["calls"]),
                "put_volume": sum(p["volume"] for p in chain["puts"])
            }
        else:
            result["strike_recommendations"] = []

        # Update cache on success
        _analysis_cache = {
            "data": result,
            "timestamp": now
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
        if top_sig["confidence"] >= 85 and not top_sig.get("advisory_only"):
            if not result.get("strike_recommendations"):
                action_status = "🔴 SKIPPED (No Strikes)"
            elif not state.automation_enabled:
                action_status = "🟡 SKIPPED (Auto Off)"
            else:
                can_trade, reason = state.can_trade()
                if not can_trade:
                    action_status = f"🔴 SKIPPED ({reason})"
                else:
                    best_strike = result["strike_recommendations"][0]
                    opt_symbol = best_strike["symbol"]
                    opt_ltp = best_strike.get("ltp", 0)
                    
                    print(f"🤖 AUTO-TRADE: Executing {top_sig['type']} BUY on {best_strike['strike']}...")
                    
                    try:
                        # 1. Fetch Option Chart to get dynamic SL
                        sl_points = 12.0 # default
                        try:
                            # Fetch recent 5m candles to find the "first lower low" from the left
                            opt_candles = await asyncio.to_thread(client.get_historical, opt_symbol, "5", 3)
                            if opt_candles and len(opt_candles) >= 5:
                                # Look back at the last 5 candles to find the lowest point (the "lower low")
                                recent_low = min(c["low"] for c in opt_candles[-5:])
                                if opt_ltp > 0 and recent_low > 0:
                                    calc_sl = opt_ltp - recent_low
                                    # Ensure a minimum 5 pt and maximum 20 pt SL for safety
                                    sl_points = max(5.0, min(20.0, calc_sl))
                                    sl_points = round(sl_points, 1)
                        except Exception as ce:
                            print(f"⚠️ Could not fetch option history for SL: {ce}")
                        
                        target_points = round(sl_points * 2.0, 1)
                        print(f"🎯 Dynamic Risk: SL={sl_points} pts, Target={target_points} pts on {opt_symbol}")

                        order_res = client.place_order(
                            symbol=opt_symbol,
                            qty=65, # Updated Nifty Lot
                            side="BUY",
                            order_type="MARKET",
                            product="NRML",
                            sl_points=sl_points,
                            target_points=target_points
                        )
                        if order_res.get("success"):
                            action_status = "🟢 TRADED"
                            state.record_trade()
                            log_trade({
                                "symbol": opt_symbol, "qty": 65, "side": "BUY",
                                "price": opt_ltp, "status": "SUCCESS", "signal_type": "AUTO"
                            })
                            # 2. Add to active trades for trailing logic
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
        
        # Log the signal with its final status
        log_signal([top_sig], spot, action_status)

    return result


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
async def get_positions():
    """Get current positions."""
    client = get_client()
    positions = client.get_positions()
    return {"positions": positions}


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


class OrderRequest(BaseModel):
    symbol: str
    qty: int
    side: str  # BUY or SELL
    order_type: str = "MARKET"
    product: str = "NRML"
    limit_price: float = 0
    stop_price: float = 0
    sl_points: float = 12.0
    target_points: float = 0.0


@app.post("/api/order")
async def place_order(order: OrderRequest):
    """Place a trade order."""
    client = get_client()
    result = client.place_order(
        symbol=order.symbol,
        qty=order.qty,
        side=order.side,
        order_type=order.order_type,
        product=order.product,
        limit_price=order.limit_price,
        stop_price=order.stop_price,
        sl_points=order.sl_points,
        target_points=order.target_points,
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
    print("🚀 Market Data Worker started.")
    tick = 0
    while True:
        try:
            client = get_client()
            if not await asyncio.to_thread(client.is_authenticated):
                await asyncio.sleep(5)
                continue

            # Fetch Spot and VIX every loop (~1s)
            quotes = await asyncio.to_thread(client.get_quotes, ["NSE:NIFTY50-INDEX", "NSE:INDIAVIX-INDEX"])
            nifty = quotes.get("NSE:NIFTY50-INDEX", {})
            vix_data = quotes.get("NSE:INDIAVIX-INDEX", {})
            
            if nifty.get("lp", 0) > 0:
                _market_cache["spot"] = nifty
                _market_cache["vix"] = vix_data
                _market_cache["last_update"] = datetime.now().timestamp()

            # Fetch Positions and Orders every 3 seconds
            if tick % 3 == 0:
                positions = await asyncio.to_thread(client.get_positions)
                total_pnl = sum(p.get("pl", 0) for p in positions)
                state.update_pnl(total_pnl) # Update automation state
                
                realized_pnl = sum(p.get("realized_profit", p.get("pl", 0)) for p in positions if p.get("qty", 0) == 0)
                unrealized_pnl = sum(p.get("pl", 0) for p in positions if p.get("qty", 0) != 0)
                active_pos = [p for p in positions if p.get("qty", 0) != 0]
                
                _market_cache["positions"] = positions
                _market_cache["active_positions"] = active_pos
                _market_cache["total_pnl"] = round(total_pnl, 2)
                _market_cache["realized_pnl"] = round(realized_pnl, 2)
                _market_cache["unrealized_pnl"] = round(unrealized_pnl, 2)

                orders = await asyncio.to_thread(client.get_orders)
                _market_cache["orders"] = orders[-10:] if orders else []

            # Fetch Funds and Strikes every 10 seconds
            if tick % 10 == 0:
                _market_cache["funds"] = await asyncio.to_thread(client.get_funds)
                
                spot = nifty.get("lp", 0)
                if spot > 0:
                    expiry = await asyncio.to_thread(client.find_nearest_expiry, spot)
                    if expiry:
                        chain = await asyncio.to_thread(client.get_option_chain_strikes, spot, expiry["code"], 8)
                        _market_cache["ce_strikes"] = get_strike_recommendations(chain, "CALL", spot)
                        _market_cache["pe_strikes"] = get_strike_recommendations(chain, "PUT", spot)
                        _market_cache["expiry"] = expiry

            tick += 1
            await asyncio.sleep(1)
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
    try:
        while True:
            # Check if cache has new data
            if _market_cache["last_update"] > last_spot_update:
                last_spot_update = _market_cache["last_update"]
                
                # Send Spot update
                if _market_cache["spot"]:
                    nifty = _market_cache["spot"]
                    vix = _market_cache["vix"] or {}
                    await ws.send_json({
                        "type": "spot",
                        "spot": nifty.get("lp", 0),
                        "open": nifty.get("open_price", 0),
                        "high": nifty.get("high_price", 0),
                        "low": nifty.get("low_price", 0),
                        "prev_close": nifty.get("prev_close_price", 0),
                        "change": nifty.get("ch", 0),
                        "change_pct": nifty.get("chp", 0),
                        "vix": vix.get("lp", 0),
                        "vix_change": vix.get("chp", 0),
                        "timestamp": datetime.now().isoformat(),
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

                # Send Strike Updates
                if _market_cache["ce_strikes"]:
                    await ws.send_json({
                        "type": "strike_update",
                        "ce_strikes": _market_cache["ce_strikes"],
                        "pe_strikes": _market_cache["pe_strikes"],
                        "expiry": _market_cache["expiry"],
                        "spot": _market_cache["spot"].get("lp", 0),
                    })

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


@app.get("/api/auth-status")
async def auth_status():
    """Check if we are authenticated."""
    try:
        client = get_client()
        is_auth = client.check_auth_status()
        return {"authenticated": is_auth}
    except:
        return {"authenticated": False}


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
    asyncio.create_task(market_data_worker())
    asyncio.create_task(trailing_monitor())

if __name__ == "__main__":
    import uvicorn
    print("🚀 Starting NIFTY Trading Dashboard on http://localhost:8000")
    uvicorn.run(app, host="0.0.0.0", port=8000)
