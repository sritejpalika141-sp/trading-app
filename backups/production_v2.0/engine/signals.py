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
    
    # Time Filter (No new trades after 14:00)
    ist = pytz.timezone('Asia/Kolkata')
    current_time = datetime.now(ist)
    past_entry_time = current_time.hour >= 14
    
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
    signals = _evaluate_signals(trend, key_levels, active_obs, active_fvgs, confluences, spot, past_entry_time, vix, gap_percent)

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

def _evaluate_signals(trend: Dict, key_levels: List[Dict], obs: List[Dict],
                      fvgs: List[Dict], confluences: List[Dict], spot: float,
                      past_entry_time: bool, vix: float, gap_percent: float) -> List[Dict]:
    """Evaluate and generate actionable trade signals."""
    signals = []

    # 0. Time Filter check
    if past_entry_time:
        signals.append({
            "type": "NO TRADE", "direction": "NEUTRAL",
            "reason": "Past Entry Time (14:00 IST) - Theta decay risk",
            "confidence": 0, "advisory_only": True
        })
        return signals

    # Check if price is near a key level (widened to 0.4%)
    at_support = False
    at_resistance = False
    nearest_level = None

    for kl in key_levels:
        dist_pct = abs(kl["price"] - spot) / spot * 100
        if dist_pct <= 0.4:
            nearest_level = kl
            if kl["type"] in ("support", "pivot"):
                at_support = True
            if kl["type"] in ("resistance", "pivot"):
                at_resistance = True
            break

    trend_dir = trend.get("trend", "NEUTRAL")
    trend_strength = trend.get("strength", 0)

    # 1. Confluence-based signals (highest priority)
    for conf in confluences:
        conf_dir = conf["direction"]
        score = conf["confluence_score"]

        if conf_dir == "BULLISH" and trend_dir != "BEARISH":
            confidence = min(95, int(45 + score / 2 + trend_strength / 5))
            if at_support:
                confidence = min(95, confidence + 15)
            signals.append({
                "type": "CALL", "direction": "BULLISH",
                "reason": "OB + FVG confluence" + (" at support" if at_support else ""),
                "confidence": confidence,
                "entry_zone_top": conf["zone_top"],
                "entry_zone_bottom": conf["zone_bottom"],
                "key_level": nearest_level, "ob": conf["ob"], "fvg": conf["fvg"],
            })

        elif conf_dir == "BEARISH" and trend_dir != "BULLISH":
            confidence = min(95, int(45 + score / 2 + trend_strength / 5))
            if at_resistance:
                confidence = min(95, confidence + 15)
            signals.append({
                "type": "PUT", "direction": "BEARISH",
                "reason": "OB + FVG confluence" + (" at resistance" if at_resistance else ""),
                "confidence": confidence,
                "entry_zone_top": conf["zone_top"],
                "entry_zone_bottom": conf["zone_bottom"],
                "key_level": nearest_level, "ob": conf["ob"], "fvg": conf["fvg"],
            })

    # 2. OB-based signals (only when OB is AT a key level or very close)
    for ob in obs:
        if any(s.get("ob", {}).get("timestamp") == ob["timestamp"] for s in signals):
            continue

        # Require OB to be near a key level (at_level flag or at_support/resistance)
        ob_at_key = ob.get("at_level", False) or at_support or at_resistance
        if not ob_at_key:
            continue  # Skip OBs that aren't at key levels

        if ob["direction"] == "BULLISH" and trend_dir != "BEARISH":
            confidence = min(80, int(30 + ob.get("impulse_strength", 1) * 8 + trend_strength / 5))
            if at_support:
                confidence = min(85, confidence + 12)
            if ob.get("at_level"):
                confidence = min(85, confidence + 8)
            signals.append({
                "type": "CALL", "direction": "BULLISH",
                "reason": "Bullish Order Block at " + (nearest_level["label"] if nearest_level else "key level"),
                "confidence": confidence,
                "entry_zone_top": ob["top"], "entry_zone_bottom": ob["bottom"],
                "key_level": nearest_level, "ob": ob, "fvg": None,
            })

        elif ob["direction"] == "BEARISH" and trend_dir != "BULLISH":
            confidence = min(80, int(30 + ob.get("impulse_strength", 1) * 8 + trend_strength / 5))
            if at_resistance:
                confidence = min(85, confidence + 12)
            if ob.get("at_level"):
                confidence = min(85, confidence + 8)
            signals.append({
                "type": "PUT", "direction": "BEARISH",
                "reason": "Bearish Order Block at " + (nearest_level["label"] if nearest_level else "key level"),
                "confidence": confidence,
                "entry_zone_top": ob["top"], "entry_zone_bottom": ob["bottom"],
                "key_level": nearest_level, "ob": ob, "fvg": None,
            })

    # 3. Trend-only advisory (NO BUY button — just informational)
    # These are NOT actionable signals, just market context
    # They will be shown differently in the UI (no BUY button)
    if not signals and trend_strength >= 65:
        if trend_dir == "BULLISH":
            signals.append({
                "type": "CALL", "direction": "BULLISH",
                "reason": f"Strong bullish trend ({trend_strength}%) — waiting for OB/FVG setup",
                "confidence": min(40, int(15 + trend_strength / 4)),
                "entry_zone_top": spot + 20, "entry_zone_bottom": spot - 30,
                "key_level": nearest_level, "ob": None, "fvg": None,
                "advisory_only": True,
            })
        elif trend_dir == "BEARISH":
            signals.append({
                "type": "PUT", "direction": "BEARISH",
                "reason": f"Strong bearish trend ({trend_strength}%) — waiting for OB/FVG setup",
                "confidence": min(40, int(15 + trend_strength / 4)),
                "entry_zone_top": spot + 30, "entry_zone_bottom": spot - 20,
                "key_level": nearest_level, "ob": None, "fvg": None,
                "advisory_only": True,
            })
    # 4. Long Straddle Edge (Volatility Breakout Setup)
    # If market is strictly neutral but sitting right at a major level, and VIX is expanding
    if trend_dir == "NEUTRAL" and (at_support or at_resistance) and vix > 14.0 and abs(gap_percent) < 0.8:
        signals.append({
            "type": "LONG STRADDLE", "direction": "VOLATILITY",
            "reason": "Price stalled at Key Level with rising VIX - Delta neutral breakout",
            "confidence": 70,
            "advisory_only": False,
            "key_level": nearest_level,
        })
    # 5. Catch-all NO TRADE explanation
    if not signals:
        reason = "Market is choppy / no clear trend."
        if trend_dir != "NEUTRAL" and trend_strength < 65:
            reason = f"Trend is {trend_dir} but too weak ({trend_strength}%) to enter blindly."
        elif not active_obs and not active_fvgs:
            reason = "No active Order Blocks or FVGs near current price."
            
        signals.append({
            "type": "WAITING", "direction": "NEUTRAL",
            "reason": reason,
            "confidence": 0,
            "advisory_only": True,
            "key_level": None,
        })

    # Deduplicate: Only keep the best signal per direction (BULLISH/BEARISH/VOLATILITY)
    # This prevents showing multiple CALL BUY signals for the same move.
    best_signals = {}
    for s in signals:
        dir_key = s["direction"]
        if dir_key not in best_signals or s["confidence"] > best_signals[dir_key]["confidence"]:
            best_signals[dir_key] = s

    final_signals = list(best_signals.values())
    final_signals.sort(key=lambda x: x["confidence"], reverse=True)
    return final_signals[:3] # Show top 3 unique directions
