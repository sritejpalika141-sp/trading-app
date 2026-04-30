"""
Strike Selection Engine — picks optimal option strikes based on spot, trend, premium budget.
"""
from typing import List, Dict, Optional


def select_strike(option_chain: Dict, signal_type: str, spot: float,
                  max_premium: float = 300) -> Optional[Dict]:
    """
    Select the best strike for a given signal.

    Args:
        option_chain: Dict with 'calls', 'puts', 'atm' keys
        signal_type: 'CALL' or 'PUT'
        spot: Current spot price
        max_premium: Maximum premium budget per lot

    Returns:
        Selected strike details
    """
    atm = option_chain.get("atm", round(spot / 50) * 50)

    if signal_type == "CALL":
        options = option_chain.get("calls", [])
    else:
        options = option_chain.get("puts", [])

    if not options:
        return None

    # Filter by max premium
    affordable = [o for o in options if 0 < o["ltp"] <= max_premium]
    if not affordable:
        affordable = options  # fallback to all

    # Scoring: prefer ATM for highest gamma, but consider premium budget
    best = None
    best_score = -1

    for opt in affordable:
        strike = opt["strike"]
        premium = opt["ltp"]
        dist_from_atm = abs(strike - atm)

        # Score components
        atm_score = max(0, 50 - dist_from_atm / 5)  # Closer to ATM = better
        premium_score = max(0, 30 - (premium / max_premium) * 30) if premium > 0 else 0
        volume_score = min(20, opt.get("volume", 0) / 1000000)  # Volume liquidity
        spread_score = 0
        if opt.get("bid") and opt.get("ask") and opt["ask"] > 0:
            spread_pct = (opt["ask"] - opt["bid"]) / opt["ask"] * 100
            spread_score = max(0, 10 - spread_pct * 5)

        total = atm_score + premium_score + volume_score + spread_score

        if total > best_score:
            best_score = total
            best = opt.copy()
            best["score"] = round(total, 2)
            best["distance_from_atm"] = dist_from_atm

    return best


def get_strike_recommendations(option_chain: Dict, signal_type: str, spot: float) -> List[Dict]:
    """
    Quantitative Strike Selection:
    - CALL: Always ATM CALL.
    - PUT: Premium Matching (Find PUT whose premium closest matches ATM CALL premium).
    """
    if signal_type in ["NO TRADE", "WAITING"]:
        return []
        
    atm_strike = option_chain.get("atm", round(spot / 50) * 50)
    calls = option_chain.get("calls", [])
    puts = option_chain.get("puts", [])
    
    if not calls or not puts:
        return []

    # 1. Select ATM CALL
    atm_call = next((c for c in calls if c["strike"] == atm_strike), None)
    if not atm_call and calls:
        # Fallback to nearest if exact ATM not found
        atm_call = min(calls, key=lambda c: abs(c["strike"] - spot))
        
    atm_call_premium = atm_call["ltp"] if atm_call else 0.0

    # 2. Select PREMIUM MATCHED PUT
    matched_put = None
    if puts and atm_call_premium > 0:
        matched_put = min(puts, key=lambda p: abs(p["ltp"] - atm_call_premium) if p["ltp"] > 0 else float('inf'))
    
    # Clean up and add moneyness
    if atm_call:
        atm_call["moneyness"] = "ATM"
        atm_call["score"] = 100
        atm_call["type_label"] = "ATM CALL"
    if matched_put:
        matched_put["moneyness"] = "Premium Matched"
        matched_put["score"] = 99
        matched_put["type_label"] = "PREMIUM MATCHED PUT"

    results = []
    
    if signal_type == "CALL":
        if atm_call: results.append(atm_call)
    elif signal_type == "PUT":
        if matched_put: results.append(matched_put)
    elif signal_type == "LONG STRADDLE":
        if atm_call: results.append(atm_call)
        if matched_put: results.append(matched_put)
        
    return results
