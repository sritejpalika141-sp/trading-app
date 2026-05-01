"""
Fyers API Client Wrapper
Handles authentication, historical data, quotes, option chain, and order management.
"""

import os
import json
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any
from dotenv import load_dotenv
from pathlib import Path

# Load credentials from fyers-mcp-server .env
BASE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = BASE_DIR.parent
ENV_PATH = PROJECT_ROOT / "fyers-mcp-server" / ".env"
load_dotenv(ENV_PATH)


class FyersClient:
    """Wrapper around Fyers API v3 for trading operations."""

    def __init__(self):
        self.client = None
        self._cooldown_until = 0
        self._cache = {
            "funds": {"data": None, "ts": 0},
            "positions": {"data": None, "ts": 0},
            "orders": {"data": None, "ts": 0}
        }
        self._init_client()

    def _init_client(self):
        """Initialize Fyers client with stored credentials."""
        try:
            from fyers_apiv3 import fyersModel
            load_dotenv(ENV_PATH, override=True)

            client_id = os.getenv("FYERS_CLIENT_ID")
            access_token = os.getenv("FYERS_ACCESS_TOKEN")
            if access_token:
                print(f"🔑 Initializing FyersClient with token: {access_token[:10]}...", flush=True)
            else:
                print("❌ No FYERS_ACCESS_TOKEN found in environment.", flush=True)

            if not client_id or not access_token:
                raise ValueError("Missing FYERS_CLIENT_ID or FYERS_ACCESS_TOKEN")

            self.client = fyersModel.FyersModel(
                client_id=client_id,
                is_async=False,
                token=access_token,
                log_path=""
            )
        except Exception as e:
            print(f"❌ Fyers client init error: {e}")
            self.client = None

    def _check_cooldown(self) -> bool:
        """Check if we are in a rate-limit cooldown period."""
        import time
        if time.time() < self._cooldown_until:
            return True
        return False

    def _trigger_cooldown(self, duration=60):
        """Set a cooldown period (e.g., after a 429 error)."""
        import time
        self._cooldown_until = time.time() + duration
        print(f"⏳ API Global Cooldown triggered for {duration} seconds.")

    def get_synced_data(self) -> Dict:
        """Fetch funds, positions, and orders in one batch with smart caching."""
        import time
        now = time.time()
        
        # If in cooldown, return whatever we have in cache
        if self._check_cooldown():
            return {
                "funds": self._cache["funds"]["data"],
                "positions": self._cache["positions"]["data"],
                "orders": self._cache["orders"]["data"],
                "cooldown": True
            }

        # Decide what to fetch based on age
        results = {}
        
        # Positions & Orders (15s cache)
        if now - self._cache["positions"]["ts"] > 15:
            pos = self.get_positions()
            if isinstance(pos, dict) and pos.get("code") == -429:
                self._trigger_cooldown() # Stop everything if one fails
            else:
                self._cache["positions"] = {"data": pos, "ts": now}
                self._cache["orders"] = {"data": self.get_orders(), "ts": now}
        
        # Funds (60s cache)
        if now - self._cache["funds"]["ts"] > 60:
            funds = self.get_funds()
            if isinstance(funds, dict) and funds.get("code") == -429:
                self._trigger_cooldown()
            else:
                self._cache["funds"] = {"data": funds, "ts": now}

        return {
            "funds": self._cache["funds"]["data"],
            "positions": self._cache["positions"]["data"],
            "orders": self._cache["orders"]["data"],
            "cooldown": False
        }

    def get_access_token_for_ws(self):
        """Returns the token in 'appid:token' format required for websockets."""
        client_id = os.getenv("FYERS_CLIENT_ID")
        access_token = os.getenv("FYERS_ACCESS_TOKEN")
        if not client_id or not access_token:
            return None
        # Fyers v3 WS requires app_id (without -100 etc if any) or just the client_id
        # Usually it's just client_id:access_token
        return f"{client_id}:{access_token}"

    def start_data_socket(self, on_message=None, on_error=None, on_close=None, on_open=None):
        """Initializes and connects the Fyers Data WebSocket."""
        try:
            from fyers_apiv3.FyersWebsocket import data_ws
            token = self.get_access_token_for_ws()
            if not token:
                print("❌ Cannot start WebSocket: Missing token.")
                return None

            fyers_socket = data_ws.FyersDataSocket(
                access_token=token,
                log_path="",
                litemode=False,
                reconnect=False, # We handle reconnections explicitly in app.py
                on_connect=on_open,
                on_close=on_close,
                on_error=on_error,
                on_message=on_message
            )
            fyers_socket.connect()
            return fyers_socket
        except Exception as e:
            print(f"❌ WebSocket error: {e}")
            return None

    def is_authenticated(self) -> bool:
        """Check if client is authenticated and working, with local caching."""
        now = datetime.now()
        # Increased cache to 5 minutes to avoid rate limiting on profile check
        cache_duration = 300 if getattr(self, '_cached_auth_status', False) else 15
        
        if hasattr(self, '_last_auth_check') and (now - self._last_auth_check).total_seconds() < cache_duration:
            return getattr(self, '_cached_auth_status', False)

        if not self.client:
            self._init_client()
        if not self.client:
            return False
            
        try:
            resp = self.client.get_profile()
            print(f"🔍 Profile Check Response: {resp}", flush=True) # DEBUG LOG
            self._last_auth_check = now
            
            if resp.get("code") == 200:
                self._cached_auth_status = True
                return True
                
            # Fyers code -353 is "API Limit exceeded per day"
            # This means the token is likely valid, but we are being throttled.
            if resp.get("code") == -353:
                print("⚠️ Fyers API Daily Limit Reached. Treating as Authenticated.", flush=True)
                self._cached_auth_status = True
                return True

            if resp.get("code") == -8:
                print("🔐 Fyers token expired.", flush=True)
                self._cached_auth_status = False
                return False
                
            self._cached_auth_status = False
            return False
        except Exception as e:
            print(f"🔍 Profile Check Exception: {e}", flush=True) # DEBUG LOG
            self._cached_auth_status = False
            return False

    def check_auth_status(self) -> bool:
        """Alias for is_authenticated used by app.py."""
        return self.is_authenticated()

    def get_login_url(self) -> str:
        """Generate the Fyers authorization URL."""
        try:
            from fyers_apiv3 import fyersModel
            client_id = os.getenv("FYERS_CLIENT_ID")
            secret_key = os.getenv("FYERS_SECRET_KEY")
            redirect_uri = os.getenv("FYERS_REDIRECT_URI", "https://127.0.0.1:8080")
            
            session = fyersModel.SessionModel(
                client_id=client_id,
                secret_key=secret_key,
                redirect_uri=redirect_uri,
                response_type="code",
                grant_type="authorization_code"
            )
            return session.generate_authcode()
        except Exception as e:
            print(f"Error generating login URL: {e}", flush=True)
            return ""

    def set_auth_code(self, code: str) -> Dict[str, Any]:
        """Exchange auth code for access token and save to .env."""
        try:
            from fyers_apiv3 import fyersModel
            client_id = os.getenv("FYERS_CLIENT_ID")
            secret_key = os.getenv("FYERS_SECRET_KEY")
            redirect_uri = os.getenv("FYERS_REDIRECT_URI", "https://127.0.0.1:8080")
            
            print(f"🔄 Exchanging code for token... Code: {code[:10]}... | Redirect: {redirect_uri}", flush=True)
            
            session = fyersModel.SessionModel(
                client_id=client_id,
                secret_key=secret_key,
                redirect_uri=redirect_uri,
                response_type="code",
                grant_type="authorization_code"
            )
            session.set_token(code)
            response = session.generate_token()
            print(f"📥 Token Generation Response: {response}", flush=True) # DEBUG LOG
            
            if response.get("code") == 200:
                access_token = response.get("access_token")
                # Update .env
                self._save_token(access_token)
                # Clear local cache to force immediate re-validation
                if hasattr(self, '_last_auth_check'):
                    delattr(self, '_last_auth_check')
                # Re-init client
                self._init_client()
                return {"success": True, "message": "Token generated successfully"}
            else:
                return {"success": False, "message": response.get("message", "Token generation failed")}
        except Exception as e:
            print(f"❌ Token Exchange Exception: {e}", flush=True)
            return {"success": False, "message": str(e)}

    def _save_token(self, token: str):
        """Save access token to the .env file and update environment."""
        import os
        os.environ["FYERS_ACCESS_TOKEN"] = token # Forcibly update running process
        
        if os.path.exists(ENV_PATH):
            with open(ENV_PATH, 'r') as f:
                lines = f.readlines()
            
            new_lines = []
            found = False
            for line in lines:
                if line.strip().startswith("FYERS_ACCESS_TOKEN="):
                    new_lines.append(f"FYERS_ACCESS_TOKEN={token}\n")
                    found = True
                else:
                    new_lines.append(line)
            
            if not found:
                new_lines.append(f"FYERS_ACCESS_TOKEN={token}\n")
                
            with open(ENV_PATH, 'w') as f:
                f.writelines(new_lines)
            print(f"✅ Token saved to {ENV_PATH}")

    def get_quote(self, symbol: str) -> Optional[Dict]:
        """Get live quote for a single symbol."""
        if self._check_cooldown(): return {}
        try:
            resp = self.client.quotes({"symbols": symbol})
            if resp.get("code") == 200:
                data = resp.get("d", [])
                if data and isinstance(data, list):
                    v = data[0].get("v", {})
                    if v.get("lp", 0) > 0:
                        return v
            elif resp.get("code") == -429:
                self._trigger_cooldown()
            return None
        except Exception as e:
            print(f"Quote error for {symbol}: {e}")
            return {}

    def get_quotes(self, symbols: List[str]) -> Dict[str, Dict]:
        """Get live quotes for multiple symbols."""
        if self._check_cooldown(): return {}
        try:
            symbols_str = ",".join(symbols)
            resp = self.client.quotes({"symbols": symbols_str})
            results = {}
            if resp.get("code") == 200:
                for item in resp.get("d", []):
                    v = item.get("v", {})
                    if v.get("lp", 0) > 0:
                        results[item["n"]] = v
            elif resp.get("code") == -429:
                self._trigger_cooldown()
            return results
        except Exception as e:
            print(f"Quotes error for {symbols}: {e}")
            return {}

    def get_historical(self, symbol: str, resolution: str, days_back: int = 10) -> List[Dict]:
        """
        Get historical candle data.

        Args:
            symbol: e.g., 'NSE:NIFTY50-INDEX'
            resolution: '1', '5', '15', '60', 'D' (minutes or day)
            days_back: number of days of history

        Returns:
            List of candle dicts with keys: timestamp, open, high, low, close, volume
        """
        if self._check_cooldown(): return []
        try:
            end_date = datetime.now()
            start_date = end_date - timedelta(days=days_back)

            data = {
                "symbol": symbol,
                "resolution": resolution,
                "date_format": "1",
                "range_from": start_date.strftime("%Y-%m-%d"),
                "range_to": end_date.strftime("%Y-%m-%d"),
                "cont_flag": "1"
            }

            resp = self.client.history(data)

            if resp.get("code") == 200:
                candles = resp.get("candles", [])
                result = []
                for c in candles:
                    result.append({
                        "timestamp": int(c[0]),
                        "open": float(c[1]),
                        "high": float(c[2]),
                        "low": float(c[3]),
                        "close": float(c[4]),
                        "volume": int(c[5])
                    })
                return result
            elif resp.get("code") == -429:
                self._trigger_cooldown()
                print("History error: Request Limit reached")
                return []
            else:
                print(f"History error: {resp.get('message', 'Unknown')}")
                return []
        except Exception as e:
            print(f"Historical data error: {e}")
            return []

    def get_option_chain_strikes(self, spot: float, expiry_code: str, num_strikes: int = 10) -> Dict:
        """
        Fetch option premiums around ATM.

        Args:
            spot: Current spot price
            expiry_code: e.g., '26421' for April 21, 2026
            num_strikes: Number of strikes on each side of ATM

        Returns:
            Dict with 'calls' and 'puts' lists
        """
        atm = round(spot / 50) * 50
        strikes = [atm + (i * 50) for i in range(-num_strikes, num_strikes + 1)]

        calls = []
        puts = []

        # Fetch in chunks of 10
        for i in range(0, len(strikes), 5):
            chunk = strikes[i:i + 5]
            symbols = []
            for s in chunk:
                symbols.append(f"NSE:NIFTY{expiry_code}{s}CE")
                symbols.append(f"NSE:NIFTY{expiry_code}{s}PE")

            quotes = self.get_quotes(symbols)

            for s in chunk:
                ce_sym = f"NSE:NIFTY{expiry_code}{s}CE"
                pe_sym = f"NSE:NIFTY{expiry_code}{s}PE"

                ce_data = quotes.get(ce_sym, {})
                pe_data = quotes.get(pe_sym, {})

                ce_entry = {
                    "strike": s,
                    "symbol": ce_sym,
                    "ltp": ce_data.get("lp", 0),
                    "bid": ce_data.get("bid", 0),
                    "ask": ce_data.get("ask", 0),
                    "volume": ce_data.get("volume", 0),
                    "oi": ce_data.get("oi", 0),
                    "prev_close": ce_data.get("prev_close_price", 0),
                    "change_pct": ce_data.get("chp", 0),
                }
                pe_entry = {
                    "strike": s,
                    "symbol": pe_sym,
                    "ltp": pe_data.get("lp", 0),
                    "bid": pe_data.get("bid", 0),
                    "ask": pe_data.get("ask", 0),
                    "volume": pe_data.get("volume", 0),
                    "oi": pe_data.get("oi", 0),
                    "prev_close": pe_data.get("prev_close_price", 0),
                    "change_pct": pe_data.get("chp", 0),
                }
                calls.append(ce_entry)
                puts.append(pe_entry)

        return {"calls": calls, "puts": puts, "atm": atm}

    def find_nearest_expiry(self, spot: float) -> Optional[Dict]:
        """
        Find the nearest valid NIFTY weekly or monthly expiry.
        Handles both weekly (YYMDD) and monthly (YYMMM) formats.
        """
        atm = round(spot / 50) * 50
        today = datetime.now()
        
        # Optimized probe: only check today and next 7 days, and only on Tuesdays
        for delta in range(0, 10):
            d = today + timedelta(days=delta)
            
            # Skip if market closed and it's today
            if d.date() == today.date() and today.hour >= 16:
                continue
                
            # Only probe on Tuesdays (Nifty Expiry)
            if d.weekday() != 1:
                continue

            yy = d.strftime("%y")
            month_map = {10: "O", 11: "N", 12: "D"}
            m_weekly = month_map.get(d.month, str(d.month))
            dd = d.strftime("%d")
            
            # 1. Try Weekly Format
            weekly_code = f"{yy}{m_weekly}{dd}"
            ce_sym_weekly = f"NSE:NIFTY{weekly_code}{atm}CE"
            
            quote = self.get_quote(ce_sym_weekly)
            
            # CRITICAL: Detect Rate Limit. If we are rate limited, DO NOT skip this date!
            if isinstance(quote, dict) and (quote.get("code") == 429 or "limit reached" in str(quote).lower()):
                print(f"⏳ Rate limited while checking expiry {d.date()}. Assuming this is the valid expiry.")
                return {
                    "date": d.strftime("%Y-%m-%d"),
                    "day": d.strftime("%A"),
                    "code": weekly_code, # Assume weekly by default on 0DTE
                    "dte": (d.date() - today.date()).days,
                    "type": "weekly_throttled"
                }

            if quote and quote.get("lp", 0) > 0:
                return {
                    "date": d.strftime("%Y-%m-%d"),
                    "day": d.strftime("%A"),
                    "code": weekly_code,
                    "dte": (d.date() - today.date()).days,
                    "type": "weekly"
                }
            
            # 2. Try Monthly Format
            mmm = d.strftime("%b").upper()
            monthly_code = f"{yy}{mmm}"
            ce_sym_monthly = f"NSE:NIFTY{monthly_code}{atm}CE"
            
            quote = self.get_quote(ce_sym_monthly)
            if quote and quote.get("lp", 0) > 0:
                return {
                    "date": d.strftime("%Y-%m-%d"),
                    "day": d.strftime("%A"),
                    "code": monthly_code,
                    "dte": (d.date() - today.date()).days,
                    "type": "monthly"
                }
            
            # If it's today and we didn't get a clear "No" (lp=0), but rather a failure or error,
            # we should be very hesitant to skip it.
            if d.date() == today.date():
                print(f"⚠️ Today ({d.date()}) probe returned no data. Forcing weekly code as fallback.")
                return {
                    "date": d.strftime("%Y-%m-%d"),
                    "day": d.strftime("%A"),
                    "code": weekly_code,
                    "dte": 0,
                    "type": "weekly_forced"
                }

            # Small delay
            import time
            time.sleep(0.3)

        return None

    def get_positions(self) -> List[Dict]:
        """Get current trading positions."""
        if not self.client:
            return []
        try:
            resp = self.client.positions()
            if resp.get("code") == 200:
                return resp.get("netPositions", [])
            return []
        except:
            return []

    def get_orders(self) -> List[Dict]:
        """Get order book."""
        if not self.client:
            return []
        try:
            resp = self.client.orderbook()
            if resp.get("code") == 200:
                return resp.get("orderBook", [])
            return []
        except:
            return []

    def get_funds(self) -> Dict:
        """Get account funds."""
        if not self.client:
            return {}
        if self._check_cooldown(): return {"s": "error", "message": "Rate Limited (Cooldown)", "code": -429}
        try:
            resp = self.client.funds()
            if resp.get("code") == 200:
                fund_limit = resp.get("fund_limit", [])
                if isinstance(fund_limit, list) and fund_limit:
                    return fund_limit[0]
                return fund_limit if isinstance(fund_limit, dict) else {}
            elif resp.get("code") == -429:
                self._trigger_cooldown()
            return resp # Return full error dict (including code/message)
        except Exception as e:
            return {"s": "error", "message": str(e), "code": 500}

    def place_order(self, symbol: str, qty: int, side: str,
                    order_type: str = "MARKET", product: str = "NRML",
                    limit_price: float = 0, stop_price: float = 0,
                    sl_points: float = 12.0, target_points: float = 0.0) -> Dict:
        """
        Place an order.
        For MARKET orders: auto-fetches LTP and places as LIMIT with buffer
        (Fyers Algo apps require Market Price Protection — no true market orders).
        """
        if not self.client:
            return {"success": False, "message": "Not authenticated"}

        side_int = 1 if side.upper() == "BUY" else -1

        # If limit_price <= 0, auto-fetch live price and use LIMIT with buffer
        if limit_price <= 0:
            quote = self.get_quote(symbol)
            if not quote:
                return {"success": False, "message": "Could not fetch live price for " + symbol}

            ltp = quote.get("lp", 0)
            ask = quote.get("ask", ltp)
            bid = quote.get("bid", ltp)

            if side_int == 1:  # BUY — use ask price + 0.5% buffer
                raw = (ask if ask > 0 else ltp) * 1.005
            else:  # SELL — use bid price - 0.5% buffer
                raw = (bid if bid > 0 else ltp) * 0.995

            # Round to tick size 0.05
            limit_price = round(round(raw / 0.05) * 0.05, 2)

            # Force LIMIT type (type=1) since algo apps don't allow true MARKET
            actual_type = 1  # LIMIT order
            print(f"📊 AUTOLIMIT: {symbol} LTP={ltp} Ask={ask} Bid={bid} → Limit={limit_price}")
        else:
            type_map = {"MARKET": 2, "LIMIT": 1, "STOP": 3, "STOPLIMIT": 4}
            actual_type = type_map.get(order_type.upper(), 1)
            # Round limit price to tick
            if limit_price > 0:
                limit_price = round(round(limit_price / 0.05) * 0.05, 2)

        # Map product types (Fyers v3 dropped NRML, uses MARGIN for F&O)
        product_map = {"NRML": "MARGIN", "MIS": "INTRADAY"}
        mapped_product = product_map.get(product.upper(), product.upper())

        is_bo = False
        if sl_points > 0 and target_points > 0:
            is_bo = True
            mapped_product = "BO"

        order_data = {
            "symbol": symbol,
            "qty": qty,
            "type": actual_type,
            "side": side_int,
            "productType": mapped_product,
            "limitPrice": limit_price,
            "stopPrice": 0,
            "validity": "DAY",
            "disclosedQty": 0,
            "offlineOrder": False,
        }

        if is_bo:
            order_data["stopLoss"] = sl_points
            order_data["takeProfit"] = target_points

        try:
            print(f"📤 Placing order: {order_data}")
            resp = self.client.place_order(order_data)
            print(f"📥 Response: {resp}")

            # Fallback if BO fails or is rejected
            if resp.get("code") not in [200, 201, 1101] and is_bo:
                print(f"⚠️ BO Rejected. Falling back to INTRADAY with separate SL.")
                is_bo = False
                mapped_product = "INTRADAY"
                order_data["productType"] = mapped_product
                order_data.pop("stopLoss", None)
                order_data.pop("takeProfit", None)
                resp = self.client.place_order(order_data)
                print(f"📥 Fallback Response: {resp}")

            if resp.get("code") not in [200, 201, 1101]:
                return {"success": False, "message": resp.get("message", "Order failed")}

            main_order_id = resp.get("id", "")
            entry_price = limit_price

            sl_result = {"success": False}
            sl_msg = ""
            target_msg = ""

            if is_bo:
                sl_msg = f" | BO SL: {sl_points} pts"
                target_msg = f" | BO TGT: {target_points} pts"
            else:
                # === AUTO STOP LOSS ===
                if sl_points > 0:
                    sl_result = self._place_stop_loss(
                        symbol=symbol,
                        qty=qty,
                        entry_side=side.upper(),
                        entry_price=entry_price,
                        sl_points=sl_points,
                        product=mapped_product,
                    )

                if sl_result.get("success"):
                    sl_msg = f" | SL placed at ₹{sl_result.get('sl_price', '?')}"
                elif sl_points > 0:
                    sl_msg = f" | ⚠️ SL failed: {sl_result.get('message', 'unknown')}"

                # === AUTO TARGET ===
                if target_points > 0:
                    tgt_result = self._place_target(
                        symbol=symbol,
                        qty=qty,
                        entry_side=side.upper(),
                        entry_price=entry_price,
                        target_points=target_points,
                        product=mapped_product,
                    )
                    if tgt_result.get("success"):
                        target_msg = f" | TGT placed at ₹{tgt_result.get('target_price', '?')}"
                    else:
                        target_msg = f" | ⚠️ TGT failed: {tgt_result.get('message', 'unknown')}"

            return {
                "success": True,
                "order_id": main_order_id,
                "sl_order_id": sl_result.get("order_id", ""),
                "message": f"Order placed at ₹{entry_price}{sl_msg}{target_msg}",
            }

        except Exception as e:
            print(f"❌ Place order exception: {e}")
            return {"success": False, "message": str(e)}

    def _place_stop_loss(self, symbol: str, qty: int, entry_side: str,
                         entry_price: float, sl_points: float = 12,
                         product: str = "MARGIN") -> Dict:
        """
        Place a stop loss order (SL-Limit, type=4).
        For BUY entry → SL is SELL at entry - sl_points
        For SELL entry → SL is BUY at entry + sl_points
        """
        if not self.client:
            return {"success": False, "message": "Not authenticated"}

        if entry_side == "BUY":
            sl_trigger = round(round((entry_price - sl_points) / 0.05) * 0.05, 2)
            sl_limit = round(round((entry_price - sl_points - 1) / 0.05) * 0.05, 2)
            sl_side = -1  # SELL
        else:
            sl_trigger = round(round((entry_price + sl_points) / 0.05) * 0.05, 2)
            sl_limit = round(round((entry_price + sl_points + 1) / 0.05) * 0.05, 2)
            sl_side = 1  # BUY

        sl_order = {
            "symbol": symbol,
            "qty": qty,
            "type": 4,  # Stop Limit (SL)
            "side": sl_side,
            "productType": product,
            "limitPrice": sl_limit,
            "stopPrice": sl_trigger,
            "validity": "DAY",
            "disclosedQty": 0,
            "offlineOrder": False,
        }

        try:
            print(f"🛡️ Placing SL: trigger=₹{sl_trigger} limit=₹{sl_limit} | {sl_order}")
            resp = self.client.place_order(sl_order)
            print(f"🛡️ SL Response: {resp}")

            if resp.get("code") in [200, 201, 1101]:
                return {
                    "success": True,
                    "order_id": resp.get("id", ""),
                    "sl_price": sl_trigger,
                    "message": f"SL at ₹{sl_trigger}",
                }
            else:
                return {"success": False, "message": resp.get("message", "SL failed")}
        except Exception as e:
            return {"success": False, "message": str(e)}

    def _place_target(self, symbol: str, qty: int, entry_side: str,
                      entry_price: float, target_points: float = 20.0,
                      product: str = "MARGIN") -> Dict:
        """
        Place a target order (LIMIT, type=1).
        """
        if not self.client:
            return {"success": False, "message": "Not authenticated"}

        if entry_side == "BUY":
            target_price = round(round((entry_price + target_points) / 0.05) * 0.05, 2)
            target_side = -1  # SELL
        else:
            target_price = round(round((entry_price - target_points) / 0.05) * 0.05, 2)
            target_side = 1  # BUY

        target_order = {
            "symbol": symbol,
            "qty": qty,
            "type": 1,  # LIMIT
            "side": target_side,
            "productType": product,
            "limitPrice": target_price,
            "stopPrice": 0,
            "validity": "DAY",
            "disclosedQty": 0,
            "offlineOrder": False,
        }

        try:
            print(f"🎯 Placing TGT: limit=₹{target_price} | {target_order}")
            resp = self.client.place_order(target_order)
            print(f"🎯 TGT Response: {resp}")

            if resp.get("code") in [200, 201, 1101]:
                return {
                    "success": True,
                    "order_id": resp.get("id", ""),
                    "target_price": target_price,
                    "message": f"TGT at ₹{target_price}",
                }
            else:
                return {"success": False, "message": resp.get("message", "TGT failed")}
        except Exception as e:
            return {"success": False, "message": str(e)}

    def modify_order(self, order_id: str, order_type: int, limit_price: float = 0, stop_price: float = 0, qty: int = 0) -> Dict:
        """
        Modify an existing order.
        order_type: 1 for LIMIT (Target), 4 for SL-LIMIT (Stop Loss)
        """
        if not self.client:
            return {"success": False, "message": "Not authenticated"}

        data = {"id": order_id, "type": order_type}
        
        # Format prices to valid tick sizes if provided
        if limit_price > 0:
            data["limitPrice"] = round(round(limit_price / 0.05) * 0.05, 2)
        if stop_price > 0:
            data["stopPrice"] = round(round(stop_price / 0.05) * 0.05, 2)
        if qty > 0:
            data["qty"] = qty

        try:
            print(f"🔄 Modifying Order {order_id}: {data}")
            resp = self.client.modify_order(data)
            print(f"🔄 Modify Response: {resp}")

            if resp.get("code") in [200, 201, 1101]:
                return {"success": True, "message": f"Order {order_id} modified successfully"}
            else:
                return {"success": False, "message": resp.get("message", "Modify failed")}
        except Exception as e:
            return {"success": False, "message": str(e)}


# Singleton instance
_client = None

def get_client() -> FyersClient:
    """Get or create Fyers client singleton."""
    global _client
    if _client is None:
        _client = FyersClient()
    return _client
