"""
Order Block Detection Engine
Identifies bullish and bearish order blocks on 5-min chart.

Order Block Definition:
- Bullish OB: Last bearish (red) candle before a strong bullish impulse move
- Bearish OB: Last bullish (green) candle before a strong bearish impulse move
"""

from typing import List, Dict, Optional


def detect_order_blocks(candles: List[Dict], min_impulse_ratio: float = 1.2,
                        min_body_pct: float = 0.2) -> List[Dict]:
    """
    Detect order blocks in 5-minute candle data.

    Args:
        candles: List of OHLCV candle dicts
        min_impulse_ratio: Minimum ratio of impulse move to OB candle range
        min_body_pct: Minimum body-to-range ratio for the OB candle

    Returns:
        List of order block dicts
    """
    order_blocks = []

    for i in range(1, len(candles) - 4):
        candle = candles[i]
        c_open, c_close, c_high, c_low = candle["open"], candle["close"], candle["high"], candle["low"]
        c_range = max(0.1, c_high - c_low)

        # Look at next 4 candles for the move
        next_candles = candles[i+1 : i+5]
        is_bearish_candle = c_close < c_open
        is_bullish_candle = c_close > c_open

        # === BULLISH ORDER BLOCK ===
        if is_bearish_candle:
            impulse_high = max(c["high"] for c in next_candles)
            impulse_move = impulse_high - c_high 

            if impulse_move > c_range * 0.3: # Lowered to 30% for visibility
                if any(c["close"] > c_high for c in next_candles):
                    order_blocks.append({
                        "type": "bullish_ob", "direction": "BULLISH",
                        "top": c_high, "bottom": c_low,
                        "timestamp": candle["timestamp"],
                        "impulse_strength": round(impulse_move / c_range, 2),
                        "active": True, "mitigated": False
                    })

        # === BEARISH ORDER BLOCK ===
        elif is_bullish_candle:
            impulse_low = min(c["low"] for c in next_candles)
            impulse_move = c_low - impulse_low 

            if impulse_move > c_range * 0.3:
                if any(c["close"] < c_low for c in next_candles):
                    order_blocks.append({
                        "type": "bearish_ob", "direction": "BEARISH",
                        "top": c_high, "bottom": c_low,
                        "timestamp": candle["timestamp"],
                        "impulse_strength": round(impulse_move / c_range, 2),
                        "active": True, "mitigated": False
                    })

    # Check which OBs have been mitigated (price returned and passed through)
    _check_mitigation(order_blocks, candles)

    return order_blocks


def _check_mitigation(order_blocks: List[Dict], candles: List[Dict]):
    """Mark order blocks as mitigated if price has already swept through them."""
    # Create a mapping of timestamp to index for fast lookup
    time_to_idx = {c["timestamp"]: i for i, c in enumerate(candles)}
    
    for ob in order_blocks:
        start_idx = time_to_idx.get(ob["timestamp"], 0)
        
        # Only check candles AFTER the OB was formed
        for j in range(start_idx + 1, len(candles)):
            candle = candles[j]

            if ob["direction"] == "BULLISH":
                if candle["low"] < ob["bottom"]:
                    ob["mitigated"] = True
                    ob["active"] = False
                    break
            else:  # BEARISH
                if candle["high"] > ob["top"]:
                    ob["mitigated"] = True
                    ob["active"] = False
                    break


def get_active_order_blocks(candles: List[Dict], spot: float,
                            proximity_pct: float = 0.5) -> List[Dict]:
    """
    Get only active (unmitigated) order blocks near current price.

    Args:
        candles: 5-min candle data
        spot: Current spot price
        proximity_pct: How close (in %) price must be to OB

    Returns:
        Active OBs sorted by proximity to spot
    """
    all_obs = detect_order_blocks(candles)
    threshold = spot * proximity_pct / 100

    active_nearby = []
    for ob in all_obs:
        if not ob["active"]:
            continue

        # Check if spot is near the OB zone
        distance = min(abs(spot - ob["top"]), abs(spot - ob["bottom"]))
        if distance <= threshold:
            ob["distance_from_spot"] = round(distance, 2)
            ob["at_level"] = True
            active_nearby.append(ob)
        elif distance <= threshold * 3:
            ob["distance_from_spot"] = round(distance, 2)
            ob["at_level"] = False
            active_nearby.append(ob)

    active_nearby.sort(key=lambda x: x["distance_from_spot"])
    return active_nearby
