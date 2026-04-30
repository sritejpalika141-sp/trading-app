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


def get_strike_recommendations(option_chain: Dict, signal_type: str, spot: float, dte: int = 5) -> List[Dict]:
    """
    Quantitative Strike Selection (OI & DTE Optimized):
    - DTE <= 1 (Expiry): Prefers ITM strikes to avoid theta decay.
    - DTE > 1: Prefers high OI strikes around ATM for liquidity.
    """
    if signal_type in ["NO TRADE", "WAITING"]:
        return []
        
    atm_strike = option_chain.get("atm", round(spot / 50) * 50)
    calls = option_chain.get("calls", [])
    puts = option_chain.get("puts", [])
    
    if not calls or not puts:
        return []

    # Function to score strikes based on OI and distance from ATM
    def score_options(options: List[Dict], is_call: bool):
        scored = []
        # Determine target moneyness based on DTE
        # If DTE <= 1 (Expiry), we want slightly ITM (dist -50 for CE, +50 for PE)
        target_offset = 0
        if dte <= 1:
            target_offset = -50 if is_call else 50
        
        target_price = atm_strike + target_offset
        
        max_oi = max((o.get("oi", 1) for o in options), default=1)
        
        for opt in options:
            o = opt.copy()
            strike = o["strike"]
            dist_from_target = abs(strike - target_price)
            
            # Score Components
            # 1. Proximity Score (Higher is better, max 50)
            prox_score = max(0, 50 - (dist_from_target / 5))
            
            # 2. OI Score (Higher is better, max 50)
            oi_val = o.get("oi", 0)
            oi_score = (oi_val / max_oi) * 50 if max_oi > 0 else 0
            
            o["score"] = round(prox_score + oi_score, 1)
            o["moneyness"] = "ITM" if (is_call and strike < spot) or (not is_call and strike > spot) else ("ATM" if strike == atm_strike else "OTM")
            scored.append(o)
            
        return sorted(scored, key=lambda x: x["score"], reverse=True)

    # Get ranked options
    ranked_calls = score_options(calls, True)
    ranked_puts = score_options(puts, False)

    results = []
    
    if signal_type == "CALL" and ranked_calls:
        best = ranked_calls[0]
        best["type_label"] = f"{best['moneyness']} CALL (OI Optimized)"
        results.append(best)
    elif signal_type == "PUT" and ranked_puts:
        best = ranked_puts[0]
        best["type_label"] = f"{best['moneyness']} PUT (OI Optimized)"
        results.append(best)
        
    return results
