import os
import json
import csv
from datetime import datetime

LOG_DIR = "logs"
SIGNAL_LOG = os.path.join(LOG_DIR, "signals.log")
TRADE_LOG = os.path.join(LOG_DIR, "trades.csv")

def init_logs():
    """Initialize log directory and files."""
    if not os.path.exists(LOG_DIR):
        os.makedirs(LOG_DIR)
    
    # Initialize trades CSV with header if it doesn't exist
    if not os.path.exists(TRADE_LOG):
        with open(TRADE_LOG, 'w', newline='') as f:
            writer = csv.writer(f)
            writer.writerow(["Timestamp", "Symbol", "Side", "Qty", "Price", "Signal_Type", "Status"])

def log_signal(signals, spot, action_status="SKIPPED (No Action)"):
    """Log generated high-confidence signals to a file."""
    if not signals:
        return

    init_logs()
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    with open(SIGNAL_LOG, "a") as f:
        for s in signals:
            # Only log actionable or high-confidence signals
            if s.get("advisory_only") and s.get("confidence", 0) < 50:
                continue
                
            log_entry = {
                "time": timestamp,
                "spot": spot,
                "type": s.get("type"),
                "reason": s.get("reason"),
                "confidence": s.get("confidence"),
                "zone": f"{s.get('entry_zone_bottom')} - {s.get('entry_zone_top')}",
                "action": action_status
            }
            f.write(json.dumps(log_entry) + "\n")

def get_signal_history(limit=20):
    """Retrieve the most recent logged signals."""
    if not os.path.exists(SIGNAL_LOG):
        return []
    
    try:
        with open(SIGNAL_LOG, "r") as f:
            lines = f.readlines()
            
        history = []
        # Return newest first
        for line in reversed(lines[-limit:]):
            if line.strip():
                try:
                    history.append(json.loads(line))
                except:
                    pass
        return history
    except Exception as e:
        print(f"Error reading signal history: {e}")
        return []

def log_trade(trade_data):
    """Log actual order placements."""
    init_logs()
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    with open(TRADE_LOG, "a", newline='') as f:
        writer = csv.writer(f)
        writer.writerow([
            timestamp,
            trade_data.get("symbol"),
            trade_data.get("side"),
            trade_data.get("qty"),
            trade_data.get("price", "MARKET"),
            trade_data.get("signal_type", "MANUAL"),
            trade_data.get("status", "PLACED")
        ])
