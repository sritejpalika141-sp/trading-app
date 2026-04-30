import json
import os
from datetime import datetime

STATE_FILE = "logs/trading_state.json"

class TradingState:
    def __init__(self):
        self.automation_enabled = False
        self.max_trades_per_day = 2
        self.max_loss_per_day = 2500.0
        self.trades_today = 0
        self.pnl_today = 0.0
        self.last_reset_date = datetime.now().date().isoformat()
        self.active_auto_trades = []
        self.skipped_signals = [] # List of sig_id strings
        self.load()

    def load(self):
        if os.path.exists(STATE_FILE):
            try:
                with open(STATE_FILE, 'r') as f:
                    data = json.load(f)
                    # Check if we need to reset for a new day
                    if data.get("last_reset_date") != datetime.now().date().isoformat():
                        self.reset_day()
                    else:
                        self.automation_enabled = data.get("automation_enabled", False)
                        self.trades_today = data.get("trades_today", 0)
                        self.pnl_today = data.get("pnl_today", 0.0)
                        self.active_auto_trades = data.get("active_auto_trades", [])
                        self.skipped_signals = data.get("skipped_signals", [])
            except:
                self.reset_day()
        else:
            self.reset_day()

    def save(self):
        if not os.path.exists("logs"):
            os.makedirs("logs")
        with open(STATE_FILE, 'w') as f:
            json.dump({
                "automation_enabled": self.automation_enabled,
                "trades_today": self.trades_today,
                "pnl_today": self.pnl_today,
                "last_reset_date": self.last_reset_date,
                "max_trades_per_day": self.max_trades_per_day,
                "max_loss_per_day": self.max_loss_per_day,
                "active_auto_trades": self.active_auto_trades,
                "skipped_signals": self.skipped_signals
            }, f)

    def reset_day(self):
        self.trades_today = 0
        self.pnl_today = 0.0
        self.active_auto_trades = []
        self.skipped_signals = []
        self.last_reset_date = datetime.now().date().isoformat()
        self.save()

    def add_skipped_signal(self, sig_id):
        if sig_id not in self.skipped_signals:
            self.skipped_signals.append(sig_id)
            self.save()

    def can_trade(self, symbol_prefix="NSE:NIFTY"):
        if not self.automation_enabled:
            return False, "Automation disabled"
        if self.trades_today >= self.max_trades_per_day:
            return False, f"Daily trade limit reached ({self.max_trades_per_day})"
        if self.pnl_today <= -self.max_loss_per_day:
            return False, f"Daily loss limit reached (₹{self.max_loss_per_day})"
        
        # Prevent overlapping trades for the same instrument
        if any(t["symbol"].startswith(symbol_prefix) for t in self.active_auto_trades):
            return False, "Active trade in progress"
            
        return True, "OK"

    def record_trade(self):
        self.trades_today += 1
        self.save()

    def update_pnl(self, current_pnl):
        # We track realized + unrealized for the day
        self.pnl_today = current_pnl
        self.save()

    def add_active_trade(self, symbol, entry_price, sl_points, side, sl_order_id, tgt_order_id):
        self.active_auto_trades.append({
            "symbol": symbol,
            "entry_price": entry_price,
            "sl_points": sl_points,
            "side": side,
            "sl_order_id": sl_order_id,
            "tgt_order_id": tgt_order_id,
            "trailed": False
        })
        self.save()

    def mark_trade_trailed(self, sl_order_id):
        for t in self.active_auto_trades:
            if t["sl_order_id"] == sl_order_id:
                t["trailed"] = True
                break
        self.save()

    def remove_active_trade(self, symbol):
        self.active_auto_trades = [t for t in self.active_auto_trades if t["symbol"] != symbol]
        self.save()
