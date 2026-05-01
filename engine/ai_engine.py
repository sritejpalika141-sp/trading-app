"""
AI Engine Module — Interfaces with Google Gemini for signal confirmation.
"""
import os
import json
import logging
import google.generativeai as genai
from typing import Dict, List, Optional
from dotenv import load_dotenv
from pathlib import Path

# Setup logging
logger = logging.getLogger("AI_ENGINE")

# Load environment
BASE_DIR = Path(__file__).resolve().parent.parent
ENV_PATH = BASE_DIR.parent / "fyers-mcp-server" / ".env"
load_dotenv(ENV_PATH)

API_KEY = os.getenv("GOOGLE_API_KEY")

class AIEngine:
    def __init__(self):
        self.enabled = False
        if API_KEY:
            try:
                genai.configure(api_key=API_KEY)
                self.model = genai.GenerativeModel('gemini-1.5-flash')
                self.enabled = True
                logger.info("✅ Gemini AI Engine initialized.")
            except Exception as e:
                logger.error(f"❌ Failed to initialize Gemini: {e}")
        else:
            logger.warning("⚠️ GOOGLE_API_KEY not found. AI confirmation will be mocked.")

    async def confirm_signal(self, symbol: str, signal: Dict, context: Dict) -> Dict:
        """
        Takes a technical signal and market context, returns AI confirmation.
        """
        if not self.enabled:
            return {
                "ai_confidence": signal.get("confidence", 70),
                "ai_rationale": "AI Confirmation (Mocked): Signal aligns with local technical structure.",
                "ai_status": "mocked"
            }

        try:
            prompt = self._build_prompt(symbol, signal, context)
            
            # Run in a thread if the library is synchronous, but genai supports async? 
            # Actually, the python SDK has a generate_content_async.
            response = await self.model.generate_content_async(
                prompt,
                generation_config=genai.types.GenerationConfig(
                    temperature=0.2,
                    response_mime_type="application/json"
                )
            )
            
            result = json.loads(response.text)
            return {
                "ai_confidence": result.get("confidence", 50),
                "ai_rationale": result.get("rationale", "No rationale provided."),
                "ai_status": "confirmed"
            }
        except Exception as e:
            logger.error(f"AI Confirmation Error: {e}")
            return {
                "ai_confidence": signal.get("confidence", 50),
                "ai_rationale": f"AI Error: {str(e)[:100]}",
                "ai_status": "error"
            }

    def _build_prompt(self, symbol: str, signal: Dict, context: Dict) -> str:
        """Constructs the prompt for the AI."""
        trend = context.get("trend", {})
        vix = context.get("vix", "Unknown")
        gap = context.get("gap_type", "Normal")
        levels = [f"{l['label']}: {l['price']}" for l in context.get("key_levels", [])[:5]]
        
        # Option Chain Analysis
        oc = context.get("option_chain", {})
        oc_summary = "Not Available"
        if oc:
            total_ce_oi = sum(c.get('oi', 0) for c in oc.get('calls', []))
            total_pe_oi = sum(p.get('oi', 0) for p in oc.get('puts', []))
            pcr = round(total_pe_oi / total_ce_oi, 2) if total_ce_oi > 0 else 0
            oc_summary = f"PCR: {pcr} | Total CE OI: {total_ce_oi} | Total PE OI: {total_pe_oi}"

        # Correlation Analysis (v3.2.0)
        bnf_spot = context.get("bnf_spot", "Unknown")
        bnf_trend = context.get("bnf_trend", "Unknown")
        
        # Market Breath Analysis (v3.3.0)
        heavyweights = context.get("heavyweights", [])
        breath_summary = "Not Available"
        if heavyweights:
            breath_summary = ", ".join([f"{h['symbol'].split(':')[-1]}: {h['trend']}" for h in heavyweights])

        pnl = context.get("pnl_today", 0)
        target_met = context.get("profit_target_met", False)

        prompt = f"""
        Analyze this NIFTY Intraday Options trade signal and provide a confirmation.
        
        SYMBOL: {symbol}
        SIGNAL TYPE: {signal.get('type')}
        DIRECTION: {signal.get('direction')}
        REASON: {signal.get('reason')}
        TECHNICAL CONFIDENCE: {signal.get('confidence')}%
        
        MARKET CONTEXT:
        - Trend: {trend.get('trend')} (Strength: {trend.get('strength')}/10)
        - VIX: {vix}
        - Gap Type: {gap}
        - Key Levels Near Price: {', '.join(levels)}
        - Option Chain Sentiment: {oc_summary}
        - BANKNIFTY Sync: Spot {bnf_spot}, Trend {bnf_trend}
        - MARKET BREATH (Heavyweights): {breath_summary}
        
        TRADING ACCOUNT STATUS:
        - PnL Today: ₹{pnl}
        - Target Met: {"YES (Switch to CONSERVATIVE mode)" if target_met else "NO (Standard mode)"}
        
        TASK:
        1. Evaluate if the signal aligns with the broader market structure.
        2. Identify Market Regime: Is the market "Trending" (fast moves) or "Sideways" (mean reverting)?
        3. Check Correlation: NIFTY and BANKNIFTY should ideally move together. 
        4. Analyze Market Breath: RELIANCE and HDFC BANK are the drivers. If NIFTY is Buy but heavyweights are Sell, lower confidence score.
        5. If Target Met is YES: Be extremely picky. Only confirm signals that have clear institutional backing and high confluence.
        6. Use Option Chain PCR: PCR > 1.0 (Bullish), PCR < 0.7 (Bearish).
        7. Assign a final confidence score (0-100).
        8. Provide a 1-sentence professional rationale.
        
        OUTPUT FORMAT (JSON):
        {{
            "confidence": <int>,
            "rationale": "<string>"
        }}
        """
        return prompt

# Singleton instance
ai_engine = AIEngine()
