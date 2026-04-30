"""
Fair Value Gap (FVG) Detection Engine
Identifies bullish and bearish FVGs on 5-min chart.
"""
from typing import List, Dict


def detect_fvg(candles: List[Dict], min_gap_pct: float = 0.02) -> List[Dict]:
    """Detect Fair Value Gaps in candle data."""
    fvgs = []
    for i in range(2, len(candles)):
        c1, c2, c3 = candles[i-2], candles[i-1], candles[i]
        mid_price = (c2["high"] + c2["low"]) / 2
        min_gap = mid_price * min_gap_pct / 100

        # Bullish FVG: gap between c1 high and c3 low
        if c3["low"] > c1["high"] + min_gap:
            gap = c3["low"] - c1["high"]
            fvgs.append({
                "type": "bullish_fvg", "direction": "BULLISH",
                "top": c3["low"], "bottom": c1["high"],
                "gap_size": round(gap, 2),
                "gap_pct": round(gap / mid_price * 100, 4),
                "timestamp": c2["timestamp"],
                "filled": False, "active": True,
            })

        # Bearish FVG: gap between c1 low and c3 high
        if c1["low"] > c3["high"] + min_gap:
            gap = c1["low"] - c3["high"]
            fvgs.append({
                "type": "bearish_fvg", "direction": "BEARISH",
                "top": c1["low"], "bottom": c3["high"],
                "gap_size": round(gap, 2),
                "gap_pct": round(gap / mid_price * 100, 4),
                "timestamp": c2["timestamp"],
                "filled": False, "active": True,
            })

    _check_fvg_fill(fvgs, candles)
    return fvgs


def _check_fvg_fill(fvgs: List[Dict], candles: List[Dict]):
    """Check if FVGs have been filled by subsequent price action."""
    for fvg in fvgs:
        for candle in candles:
            if candle["timestamp"] <= fvg["timestamp"]:
                continue
            if fvg["direction"] == "BULLISH" and candle["close"] < fvg["bottom"]:
                fvg["filled"] = True
                fvg["active"] = False
                break
            elif fvg["direction"] == "BEARISH" and candle["close"] > fvg["top"]:
                fvg["filled"] = True
                fvg["active"] = False
                break


def get_active_fvg(candles: List[Dict], spot: float, proximity_pct: float = 0.5) -> List[Dict]:
    """Get active (unfilled) FVGs near current price."""
    all_fvgs = detect_fvg(candles)
    threshold = spot * proximity_pct / 100
    active = []
    for fvg in all_fvgs:
        if not fvg["active"]:
            continue
        dist = min(abs(spot - fvg["top"]), abs(spot - fvg["bottom"]))
        if dist <= threshold * 3:
            fvg["distance_from_spot"] = round(dist, 2)
            fvg["at_level"] = dist <= threshold
            active.append(fvg)
    active.sort(key=lambda x: x["distance_from_spot"])
    return active


def find_ob_fvg_confluence(order_blocks: List[Dict], fvgs: List[Dict], tolerance: float = 20) -> List[Dict]:
    """Find zones where OBs and FVGs overlap (highest probability setups)."""
    confluences = []
    for ob in order_blocks:
        if not ob.get("active", True):
            continue
        for fvg in fvgs:
            if not fvg.get("active", True) or ob["direction"] != fvg["direction"]:
                continue
            overlap_top = min(ob["top"], fvg["top"])
            overlap_bottom = max(ob["bottom"], fvg["bottom"])
            if overlap_top >= overlap_bottom - tolerance:
                confluences.append({
                    "direction": ob["direction"],
                    "zone_top": max(ob["top"], fvg["top"]),
                    "zone_bottom": min(ob["bottom"], fvg["bottom"]),
                    "ob": ob, "fvg": fvg,
                    "confluence_score": round(
                        ob.get("impulse_strength", 1) * 20 +
                        fvg.get("gap_pct", 0) * 500 + 30, 2),
                })
    confluences.sort(key=lambda x: x["confluence_score"], reverse=True)
    return confluences
