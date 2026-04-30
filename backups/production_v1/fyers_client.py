"""
Fyers API Client Wrapper
Handles authentication, historical data, quotes, option chain, and order management.
"""

import os
import json
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any
from dotenv import load_dotenv

# Load credentials from fyers-mcp-server .env
ENV_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "fyers-mcp-server", ".env")
load_dotenv(ENV_PATH)


class FyersClient:
    """Wrapper around Fyers API v3 for trading operations."""

    def __init__(self):
        self.client = None
        self._init_client()

    def _init_client(self):
        """Initialize Fyers client with stored credentials."""
        try:
            from fyers_apiv3 import fyersModel
            load_dotenv(ENV_PATH, override=True)

            client_id = os.getenv("FYERS_CLIENT_ID")
            access_token = os.getenv("FYERS_ACCESS_TOKEN")

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

    def is_authenticated(self) -> bool:
        """Check if client is authenticated and working."""
        if not self.client:
            self._init_client()
        if not self.client:
            return False
        try:
            resp = self.client.get_profile()
            return resp.get("code") == 200
        except:
            return False

    def get_quote(self, symbol: str) -> Optional[Dict]:
        """Get live quote for a single symbol."""
        if not self.client:
            return None
        try:
            resp = self.client.quotes({"symbols": symbol})
            if resp.get("code") == 200:
                data = resp.get("d", [])
                if data and isinstance(data, list):
                    v = data[0].get("v", {})
                    if v.get("lp", 0) > 0:
                        return v
            return None
        except Exception as e:
            print(f"Quote error: {e}")
            return None

    def get_quotes(self, symbols: List[str]) -> Dict[str, Dict]:
        """Get live quotes for multiple symbols."""
        if not self.client:
            return {}
        try:
            symbols_str = ",".join(symbols)
            resp = self.client.quotes({"symbols": symbols_str})
            results = {}
            if resp.get("code") == 200:
                for item in resp.get("d", []):
                    v = item.get("v", {})
                    if v.get("lp", 0) > 0:
                        results[item["n"]] = v
            return results
        except Exception as e:
            print(f"Quotes error: {e}")
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
        if not self.client:
            return []
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

                if ce_data.get("lp", 0) > 0:
                    calls.append({
                        "strike": s,
                        "symbol": ce_sym,
                        "ltp": ce_data.get("lp", 0),
                        "bid": ce_data.get("bid", 0),
                        "ask": ce_data.get("ask", 0),
                        "volume": ce_data.get("volume", 0),
                        "oi": ce_data.get("oi", 0),
                        "prev_close": ce_data.get("prev_close_price", 0),
                        "change_pct": ce_data.get("chp", 0),
                    })

                if pe_data.get("lp", 0) > 0:
                    puts.append({
                        "strike": s,
                        "symbol": pe_sym,
                        "ltp": pe_data.get("lp", 0),
                        "bid": pe_data.get("bid", 0),
                        "ask": pe_data.get("ask", 0),
                        "volume": pe_data.get("volume", 0),
                        "oi": pe_data.get("oi", 0),
                        "prev_close": pe_data.get("prev_close_price", 0),
                        "change_pct": pe_data.get("chp", 0),
                    })

        return {"calls": calls, "puts": puts, "atm": atm}

    def find_nearest_expiry(self, spot: float) -> Optional[Dict]:
        """Find the nearest valid NIFTY weekly expiry."""
        atm = round(spot / 50) * 50
        today = datetime.now()

        for delta in range(0, 14):
            d = today + timedelta(days=delta)
            if d.date() == today.date() and today.hour >= 15:
                continue  # Skip today if market closed

            yy = d.strftime("%y")
            m = str(d.month)
            dd = str(d.day)

            ce_sym = f"NSE:NIFTY{yy}{m}{dd}{atm}CE"
            quote = self.get_quote(ce_sym)

            if quote and quote.get("lp", 0) > 0:
                dte = (d.date() - today.date()).days
                return {
                    "date": d.strftime("%Y-%m-%d"),
                    "day": d.strftime("%A"),
                    "code": f"{yy}{m}{dd}",
                    "dte": dte
                }

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
        try:
            resp = self.client.funds()
            if resp.get("code") == 200:
                fund_limit = resp.get("fund_limit", [])
                if isinstance(fund_limit, list) and fund_limit:
                    return fund_limit[0]
                return fund_limit if isinstance(fund_limit, dict) else {}
            return {}
        except:
            return {}

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

        # For MARKET orders, fetch live price and use LIMIT with buffer
        if order_type.upper() == "MARKET" and limit_price <= 0:
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
            print(f"📊 MARKET→LIMIT: {symbol} LTP={ltp} Ask={ask} Bid={bid} → Limit={limit_price}")
        else:
            type_map = {"MARKET": 2, "LIMIT": 1, "STOP": 3, "STOPLIMIT": 4}
            actual_type = type_map.get(order_type.upper(), 1)
            # Round limit price to tick
            if limit_price > 0:
                limit_price = round(round(limit_price / 0.05) * 0.05, 2)

        # Map product types (Fyers v3 dropped NRML, uses MARGIN for F&O)
        product_map = {"NRML": "MARGIN", "MIS": "INTRADAY"}
        mapped_product = product_map.get(product.upper(), product.upper())

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

        try:
            print(f"📤 Placing order: {order_data}")
            resp = self.client.place_order(order_data)
            print(f"📥 Response: {resp}")

            if resp.get("code") not in [200, 201, 1101]:
                return {"success": False, "message": resp.get("message", "Order failed")}

            main_order_id = resp.get("id", "")
            entry_price = limit_price

            # === AUTO STOP LOSS ===
            sl_result = self._place_stop_loss(
                symbol=symbol,
                qty=qty,
                entry_side=side.upper(),
                entry_price=entry_price,
                sl_points=sl_points,
                product=mapped_product,
            )

            sl_msg = ""
            if sl_result.get("success"):
                sl_msg = f" | SL placed at ₹{sl_result.get('sl_price', '?')} (ID: {sl_result.get('order_id', '?')})"
            else:
                sl_msg = f" | ⚠️ SL failed: {sl_result.get('message', 'unknown')}"

            # === AUTO TARGET ===
            target_msg = ""
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
