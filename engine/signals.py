"""
Signal Generator — combines key levels, OB, FVG, trend to produce trade signals.
"""
from typing import List, Dict, Optional
from .key_levels import get_all_key_levels, detect_trend
from .order_blocks import detect_order_blocks, get_active_order_blocks
from .fvg import get_active_fvg, find_ob_fvg_confluence


def generate_signals(candles_1h: List[Dict], candles_5m: List[Dict],
                     spot: float, candles_daily: List[Dict] = None, vix: float = 0.0) -> Dict:
    """
    Master signal generator.
    Returns key levels, OBs, FVGs, confluences, trend, and trade signals.
    """
    from datetime import datetime
    import pytz
    
    # Time Filter (No new trades after 14:30)
    ist = pytz.timezone('Asia/Kolkata')
    current_time = datetime.now(ist)
    past_entry_time = (current_time.hour > 14) or (current_time.hour == 14 and current_time.minute >= 30)
    
    # Gap Analysis
    gap_percent = 0.0
    gap_type = "Normal Open"
    if candles_daily and len(candles_daily) >= 2 and len(candles_5m) > 0:
        prev_close = candles_daily[-2]["close"]
        today_open = candles_daily[-1]["open"]
        if prev_close > 0:
            gap_percent = ((today_open - prev_close) / prev_close) * 100
            abs_gap = abs(gap_percent)
            if abs_gap < 0.3: gap_type = "Normal Open"
            elif abs_gap < 0.8: gap_type = "Mild Gap"
            elif abs_gap < 1.5: gap_type = "Large Gap"
            else: gap_type = "Extreme Gap"
    # 1. Trend from 1H
    trend = detect_trend(candles_1h)

    # 2. Key levels from 1H
    key_levels = get_all_key_levels(candles_1h, spot, candles_daily)

    # 3. Order blocks from 5M
    all_obs = detect_order_blocks(candles_5m)
    active_obs = get_active_order_blocks(candles_5m, spot)

    # 4. FVGs from 5M
    active_fvgs = get_active_fvg(candles_5m, spot)

    # 5. Confluences
    confluences = find_ob_fvg_confluence(active_obs, active_fvgs)

    # 6. Generate trade signals
    signals = _evaluate_signals(trend, key_levels, active_obs, active_fvgs, confluences, spot, past_entry_time, vix, gap_percent, candles_5m)

    # 7. Break of Structure (BOS) from 5M
    from .key_levels import detect_recent_bos
    bos_events = detect_recent_bos(candles_5m)

    return {
        "spot": spot,
        "trend": trend,
        "key_levels": key_levels[:12],
        "bos_events": bos_events,
        "order_blocks": all_obs,
        "active_order_blocks": active_obs,
        "fvgs": active_fvgs,
        "confluences": confluences,
        "signals": signals,
        "gap_percent": round(gap_percent, 2),
        "gap_type": gap_type
    }

def detect_retest_and_rejection(candles: List[Dict], zone: Dict, direction: str) -> Dict:
    """
    Checks if price has retested the zone and rejected out of it.
    Bullish: Dips into zone (retest), then moves up (rejection).
    Bearish: Rallies into zone (retest), then moves down (rejection).
    """
    if not candles or len(candles) < 3:
        return {"retested": False, "rejected": False}
    
    # Check last 10 candles for a retest
    recent = candles[-10:]
    retested = False
    rejection_move = False
    
    zone_top = zone.get("top")
    zone_bottom = zone.get("bottom")
    
    # 1. RETEST CHECK
    for c in recent:
        if direction == "BULLISH":
            # Candle low touched or dipped into the zone
            if c["low"] <= zone_top and c["low"] >= zone_bottom:
                retested = True
                break
        else: # BEARISH
            # Candle high touched or dipped into the zone
            if c["high"] >= zone_bottom and c["high"] <= zone_top:
                retested = True
                break
                
    # 2. REJECTION CHECK (Looking at last 3 candles)
    if retested:
        last_c = candles[-1]
        prev_c = candles[-2]
        
        if direction == "BULLISH":
            # Rejection = Moving UP from zone
            # Criteria: Last candle closed above previous candle or is green and above zone bottom
            if last_c["close"] > prev_c["close"] and last_c["close"] >= zone_bottom:
                rejection_move = True
        else: # BEARISH
            # Rejection = Moving DOWN from zone
            if last_c["close"] < prev_c["close"] and last_c["close"] <= zone_top:
                rejection_move = True
                
    return {"retested": retested, "rejected": rejection_move}

def _evaluate_signals(trend: Dict, key_levels: List[Dict], obs: List[Dict],
                      fvgs: List[Dict], confluences: List[Dict], spot: float,
                      past_entry_time: bool, vix: float, gap_percent: float,
                      candles_5m: List[Dict] = []) -> List[Dict]:
    """Evaluate and generate actionable trade signals with Retest & Rejection logic."""
    signals = []

    # 0. Time Filter check
    if past_entry_time:
        signals.append({
            "type": "NO TRADE", "direction": "NEUTRAL",
            "reason": "Past Entry Time (14:30 IST) - Theta decay risk",
            "confidence": 0, "advisory_only": True
        })
        return signals

    # Check if price is near a key level
    at_support = False
    at_resistance = False
    nearest_level = None

    for kl in key_levels:
        dist_pct = abs(kl["price"] - spot) / spot * 100
        if dist_pct <= 0.4:
            nearest_level = kl
            if kl["type"] in ("support", "pivot"): at_support = True
            if kl["type"] in ("resistance", "pivot"): at_resistance = True
            break

    trend_dir = trend.get("trend", "NEUTRAL")
    trend_strength = trend.get("strength", 0)

    # 1. OB & FVG signals with Retest & Rejection
    # We combine them because they follow the same logic
    all_setups = []
    for conf in confluences: all_setups.append({"dir": conf["direction"], "top": conf["zone_top"], "bottom": conf["zone_bottom"], "type": "confluence", "source": conf})
    for ob in obs: all_setups.append({"dir": ob["direction"], "top": ob["top"], "bottom": ob["bottom"], "type": "ob", "source": ob})
    for fvg in fvgs: all_setups.append({"dir": fvg["direction"], "top": fvg["top"], "bottom": fvg["bottom"], "type": "fvg", "source": fvg})

    for setup in all_setups:
        status = detect_retest_and_rejection(candles_5m, setup, setup["dir"])
        
        if status["retested"] and status["rejected"]:
            is_bull = setup["dir"] == "BULLISH"
            
            # Entry Price Calculation
            # OB/Confluence -> Outer Edge | FVG -> Midpoint
            if setup["type"] == "fvg":
                entry_price = setup["bottom"] + (setup["top"] - setup["bottom"]) / 2
            else:
                entry_price = setup["top"] if is_bull else setup["bottom"]
            
            # SL Calculation: Nearest swing low/high
            # We look at the last 15 candles to find the recent lower low or higher high
            recent_candles = candles_5m[-15:] if len(candles_5m) >= 15 else candles_5m
            if is_bull:
                recent_swing_low = min(c["low"] for c in recent_candles) if recent_candles else setup["bottom"]
                sl_price = min(recent_swing_low - 2.0, entry_price - 10.0)
            else:
                recent_swing_high = max(c["high"] for c in recent_candles) if recent_candles else setup["top"]
                sl_price = max(recent_swing_high + 2.0, entry_price + 10.0)

            confidence = min(95, 60 + (trend_strength / 5))
            if (is_bull and at_support) or (not is_bull and at_resistance):
                confidence = min(95, confidence + 15)

            # Avoid duplicate signals for same zone
            signals.append({
                "type": "CALL" if is_bull else "PUT",
                "direction": setup["dir"],
                "reason": f"{setup['dir']} {setup['type'].upper()} Retest & Rejection confirmed",
                "confidence": confidence,
                "entry_price": round(entry_price, 1),
                "sl": round(sl_price, 1),
                "target": round(entry_price + (entry_price - sl_price) * 2, 1), # 1:2 RR
                "entry_zone_top": setup["top"],
                "entry_zone_bottom": setup["bottom"]
            })

    # 2. Catch-all NO TRADE explanation
    if not signals:
        reason = "Waiting for OB/FVG Retest & Rejection..."
        if not all_setups:
            reason = "No active Order Blocks or FVGs near key levels."
            
        signals.append({
            "type": "WAITING", "direction": "NEUTRAL",
            "reason": reason,
            "confidence": 0,
            "advisory_only": True
        })

    # Deduplicate
    best_signals = {}
    for s in signals:
        dir_key = s["direction"]
        if dir_key not in best_signals or s["confidence"] > best_signals[dir_key]["confidence"]:
            best_signals[dir_key] = s

    final = sorted(list(best_signals.values()), key=lambda x: x["confidence"], reverse=True)
    return final[:3]
