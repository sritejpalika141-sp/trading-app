"""
Key Level Detection Engine
Identifies support/resistance levels from 1-Hour chart data.
"""

from typing import List, Dict, Tuple
import math


def detect_swing_highs(candles: List[Dict], lookback: int = 3) -> List[Dict]:
    """
    Detect swing highs — local maxima in price.

    A swing high is a candle whose high is greater than the highs
    of `lookback` candles on both sides.
    """
    swings = []
    for i in range(lookback, len(candles) - lookback):
        high = candles[i]["high"]
        is_swing = True

        for j in range(1, lookback + 1):
            if candles[i - j]["high"] >= high or candles[i + j]["high"] >= high:
                is_swing = False
                break

        if is_swing:
            swings.append({
                "type": "resistance",
                "price": high,
                "timestamp": candles[i]["timestamp"],
                "strength": _calc_level_strength(candles, high, i),
            })

    return swings


def detect_swing_lows(candles: List[Dict], lookback: int = 3) -> List[Dict]:
    """
    Detect swing lows — local minima in price.

    A swing low is a candle whose low is less than the lows
    of `lookback` candles on both sides.
    """
    swings = []
    for i in range(lookback, len(candles) - lookback):
        low = candles[i]["low"]
        is_swing = True

        for j in range(1, lookback + 1):
            if candles[i - j]["low"] <= low or candles[i + j]["low"] <= low:
                is_swing = False
                break

        if is_swing:
            swings.append({
                "type": "support",
                "price": low,
                "timestamp": candles[i]["timestamp"],
                "strength": _calc_level_strength(candles, low, i),
            })

    return swings


def _calc_level_strength(candles: List[Dict], level: float, idx: int, tolerance: float = 0.002) -> int:
    """Calculate how many times price has touched this level (strength)."""
    touches = 0
    threshold = level * tolerance

    for c in candles:
        if abs(c["high"] - level) <= threshold or abs(c["low"] - level) <= threshold:
            touches += 1

    return min(touches, 10)  # cap at 10


def detect_round_numbers(spot: float, num_levels: int = 5) -> List[Dict]:
    """
    Detect round psychological levels near current price.
    NIFTY: every 100 points is significant, every 500 is major.
    """
    levels = []
    base = round(spot / 100) * 100

    for i in range(-num_levels, num_levels + 1):
        price = base + (i * 100)
        is_major = price % 500 == 0

        levels.append({
            "type": "round_number",
            "price": price,
            "strength": 8 if is_major else 4,
            "label": f"{'Major' if is_major else 'Minor'} Round: {price}",
        })

    return levels


def detect_prev_day_levels(candles_daily: List[Dict]) -> List[Dict]:
    """
    Get previous day high, low, close as key levels.
    """
    if len(candles_daily) < 2:
        return []

    prev = candles_daily[-2]  # Previous day

    return [
        {
            "type": "resistance",
            "price": prev["high"],
            "strength": 7,
            "label": "PDH",
        },
        {
            "type": "support",
            "price": prev["low"],
            "strength": 7,
            "label": "PDL",
        },
        {
            "type": "pivot",
            "price": prev["close"],
            "strength": 5,
            "label": "PDC",
        },
    ]


def cluster_levels(levels: List[Dict], tolerance_pct: float = 0.15) -> List[Dict]:
    """
    Cluster nearby levels into zones.
    If two levels are within tolerance_pct of each other, merge them.
    """
    if not levels:
        return []

    # Sort by price
    sorted_levels = sorted(levels, key=lambda x: x["price"])
    clusters = []
    current_cluster = [sorted_levels[0]]

    for i in range(1, len(sorted_levels)):
        prev_price = current_cluster[-1]["price"]
        curr_price = sorted_levels[i]["price"]

        if abs(curr_price - prev_price) / prev_price * 100 <= tolerance_pct:
            current_cluster.append(sorted_levels[i])
        else:
            clusters.append(_merge_cluster(current_cluster))
            current_cluster = [sorted_levels[i]]

    clusters.append(_merge_cluster(current_cluster))
    return clusters


def _merge_cluster(levels: List[Dict]) -> Dict:
    """Merge a cluster of nearby levels into a single zone."""
    avg_price = sum(l["price"] for l in levels) / len(levels)
    max_strength = max(l["strength"] for l in levels)
    total_strength = min(10, sum(l["strength"] for l in levels))

    # Determine type and strictly use exact high/low instead of averaging
    types = [l["type"] for l in levels]
    if "resistance" in types and "support" in types:
        level_type = "pivot"
        exact_price = avg_price
    elif "resistance" in types:
        level_type = "resistance"
        exact_price = max(l["price"] for l in levels)
    elif "support" in types:
        level_type = "support"
        exact_price = min(l["price"] for l in levels)
    else:
        level_type = levels[0].get("type", "level")
        exact_price = levels[0]["price"]

    labels = [l.get("label", "") for l in levels if l.get("label")]

    return {
        "type": level_type,
        "price": round(exact_price, 2),
        "price_low": min(l["price"] for l in levels),
        "price_high": max(l["price"] for l in levels),
        "strength": total_strength,
        "touches": len(levels),
        "label": ", ".join(labels) if labels else f"{level_type.title()}: {round(exact_price, 2)}",
    }


def detect_trend(candles: List[Dict], period: int = 20) -> Dict:
    """
    Detect overall trend from 1H candles.
    Uses EMA crossover and higher highs / higher lows.
    """
    if len(candles) < period:
        return {"trend": "NEUTRAL", "strength": 0}

    recent = candles[-period:]

    # Simple trend: compare EMA of closes
    closes = [c["close"] for c in recent]
    ema_fast = _ema(closes, min(8, len(closes)))
    ema_slow = _ema(closes, min(20, len(closes)))

    # Higher highs / higher lows count
    hh_count = 0
    ll_count = 0
    for i in range(1, len(recent)):
        if recent[i]["high"] > recent[i - 1]["high"]:
            hh_count += 1
        if recent[i]["low"] < recent[i - 1]["low"]:
            ll_count += 1

    # Trend determination
    if ema_fast > ema_slow and hh_count > ll_count:
        trend = "BULLISH"
        strength = min(100, int((hh_count / len(recent)) * 100 + (ema_fast - ema_slow) / ema_slow * 1000))
    elif ema_fast < ema_slow and ll_count > hh_count:
        trend = "BEARISH"
        strength = min(100, int((ll_count / len(recent)) * 100 + (ema_slow - ema_fast) / ema_slow * 1000))
    else:
        trend = "NEUTRAL"
        strength = 30

    return {
        "trend": trend,
        "strength": min(100, max(0, strength)),
        "ema_fast": round(ema_fast, 2),
        "ema_slow": round(ema_slow, 2),
        "higher_highs": hh_count,
        "lower_lows": ll_count,
    }


def _ema(values: List[float], period: int) -> float:
    """Calculate Exponential Moving Average."""
    if not values:
        return 0
    if len(values) <= period:
        return sum(values) / len(values)

    multiplier = 2 / (period + 1)
    ema_val = sum(values[:period]) / period

    for v in values[period:]:
        ema_val = (v - ema_val) * multiplier + ema_val

    return ema_val


def get_all_key_levels(candles_1h: List[Dict], spot: float, candles_daily: List[Dict] = None) -> List[Dict]:
    """
    Master function: detect all key levels and return clustered, sorted list.
    """
    all_levels = []

    # Swing highs and lows (reduced lookback to 2 for more responsive levels)
    all_levels.extend(detect_swing_highs(candles_1h, lookback=2))
    all_levels.extend(detect_swing_lows(candles_1h, lookback=2))

    # Removed detect_round_numbers(spot, num_levels=5) to strictly avoid rounding

    # Previous day levels
    if candles_daily:
        all_levels.extend(detect_prev_day_levels(candles_daily))

    # Cluster nearby levels
    clustered = cluster_levels(all_levels, tolerance_pct=0.15)

    # Sort by distance from spot
    clustered.sort(key=lambda x: abs(x["price"] - spot))

    return clustered

def detect_recent_bos(candles_5m: List[Dict]) -> List[Dict]:
    """
    Detect the most recent Break of Structure (BOS).
    A BOS is formed when price closes above a recent swing high or below a recent swing low.
    """
    if len(candles_5m) < 10:
        return []
        
    swings_high = detect_swing_highs(candles_5m, lookback=2)
    swings_low = detect_swing_lows(candles_5m, lookback=2)
    
    bos_events = []
    
    # Check for Bullish BOS (Close above Swing High)
    for sh in swings_high:
        sh_time = sh["timestamp"]
        sh_price = sh["price"]
        for candle in candles_5m:
            if candle["timestamp"] > sh_time and candle["close"] > sh_price:
                bos_events.append({
                    "type": "BULLISH_BOS",
                    "price": sh_price,
                    "timestamp": sh_time,
                    "break_time": candle["timestamp"]
                })
                break # Only record the first break
                
    # Check for Bearish BOS (Close below Swing Low)
    for sl in swings_low:
        sl_time = sl["timestamp"]
        sl_price = sl["price"]
        for candle in candles_5m:
            if candle["timestamp"] > sl_time and candle["close"] < sl_price:
                bos_events.append({
                    "type": "BEARISH_BOS",
                    "price": sl_price,
                    "timestamp": sl_time,
                    "break_time": candle["timestamp"]
                })
                break

    # Sort by break time to get the most recent ones
    bos_events.sort(key=lambda x: x["break_time"], reverse=True)
    return bos_events[:2]  # Return the last 2 recent BOS events
