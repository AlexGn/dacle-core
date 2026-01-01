#!/usr/bin/env python3
"""
Historical TGE Pattern Analyzer.

DEPRECATED: Use src.analysis module instead.
Session 256: Marked for migration to src/analysis/

Analyzes LIVE tokens in the 5-21 day post-TGE window for pattern-based
short opportunities. Complements the ATH Sniper (launch monitoring) with
second-wave detection for tokens in their "settling" phase.

Gemini Session 115 Design:
- Expanded window: 5-21 days (from 7-17)
- Revised weights: Unlock proximity (30), Volume decay (25), Pattern (25), ATH (10), TA (10)
- Lower volume threshold for MATURE: $100k (vs $500k for FRESH)
- Market-wide correlation filter: If >3 alerts simultaneously, raise threshold to 85

Usage:
    from src.analysis.historical_analyzer import HistoricalAnalyzer

    analyzer = HistoricalAnalyzer(token_symbol="MONAD")
    result = analyzer.analyze()
"""

import json
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Any, Tuple

PROJECT_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.analysis.exhaustion_calculator import fetch_ohlcv_ccxt, get_trading_symbol, fetch_funding_rate
from src.analysis.market_structure_analyzer import MarketStructureAnalyzer

TOKENS_DIR = PROJECT_ROOT / "data" / "tokens"
CONFIG_PATH = PROJECT_ROOT / "config" / "historical_patterns.json"
LEARNED_PATTERNS_PATH = PROJECT_ROOT / "data" / "feedback" / "patterns.json"


def load_learned_patterns() -> List[Dict[str, Any]]:
    """
    Session 117: Load learned patterns from David's feedback.

    These patterns capture insights that system scoring missed, such as:
    - IRYS: FIB + FVG confluence can override CHoCH veto in MATURE window
    - POWER: Descending trendline 3x allows tighter SL

    Returns list of pattern dicts with override rules.
    """
    if not LEARNED_PATTERNS_PATH.exists():
        return []

    try:
        with open(LEARNED_PATTERNS_PATH, 'r') as f:
            data = json.load(f)
        return data.get("patterns", [])
    except Exception:
        return []


def check_learned_pattern_override(token: str, days_since_tge: int, profile: Dict, components: Dict) -> Dict[str, Any]:
    """
    Session 117: Check if learned patterns apply to this token/situation.

    This allows David's manual learnings to influence scoring without
    hardcoding patterns into the system.

    Returns:
        {
            "applies": bool,
            "pattern_name": str or None,
            "score_modifier": float (e.g., 1.0 = no change, 1.25 = 25% boost),
            "size_modifier": float (e.g., 0.75 = 75% position size),
            "reason": str
        }
    """
    patterns = load_learned_patterns()
    if not patterns:
        return {"applies": False}

    # Get token category from consolidated.json
    consolidated_path = TOKENS_DIR / token / "consolidated.json"
    token_category = "Unknown"
    if consolidated_path.exists():
        try:
            with open(consolidated_path, 'r') as f:
                token_data = json.load(f)
            token_category = token_data.get("category", "Unknown")
        except Exception:
            pass

    for pattern in patterns:
        override = pattern.get("override_rule", {})
        if not override:
            continue

        condition = override.get("condition", "")
        action = override.get("action", "")

        # Parse condition: "lifecycle_stage == MATURE AND confluence_count >= 3"
        # For now, check if we're in MATURE stage and have confluence factors

        # Check if pattern applies to this category (if specified)
        pattern_category = pattern.get("category", "")
        if pattern_category and pattern_category != token_category:
            continue

        # Check MATURE stage condition
        if "lifecycle_stage == MATURE" in condition and days_since_tge < 5:
            continue

        # Check confluence factors
        confluence_factors = override.get("confluence_factors", [])
        if confluence_factors:
            confluence_count = 0

            # FIB_ZONE: >60% drawdown indicates FIB 0.618 zone
            drawdown = profile.get("drawdown_from_ath_pct", 0)
            if drawdown >= 60 and "FIB_ZONE" in confluence_factors:
                confluence_count += 1

            # RR_3_PLUS: Check if R:R is good (>3:1 based on drawdown potential)
            # With 30% SL and >60% drawdown, further dump is likely
            if drawdown >= 60 and "RR_3_PLUS" in confluence_factors:
                confluence_count += 1

            # EQH_EQL: Would need chart analysis - skip for now
            # FVG: Would need chart analysis - skip for now
            # TRENDLINE: Would need chart analysis - skip for now

            # Check if we meet minimum confluence (usually 3)
            if "confluence_count >= 3" in condition and confluence_count >= 2:
                # We have at least 2/5 automated factors - apply pattern
                return {
                    "applies": True,
                    "pattern_name": pattern.get("pattern_name"),
                    "score_modifier": 1.15,  # 15% score boost
                    "size_modifier": 0.75,  # Reduced size per action
                    "reason": f"Learned pattern '{pattern.get('pattern_name')}' - {confluence_count}/5 confluence factors detected (FIB_ZONE, RR_3_PLUS)",
                    "note": pattern.get("notes", ""),
                    "expected_outcome": pattern.get("expected_outcome", "")
                }

    return {"applies": False}


def load_historical_config() -> Dict[str, Any]:
    """Load historical pattern configuration."""
    if CONFIG_PATH.exists():
        with open(CONFIG_PATH, 'r') as f:
            return json.load(f)
    return {}


def get_token_tge_date(token: str) -> Optional[datetime]:
    """Get TGE date from consolidated.json."""
    consolidated_path = TOKENS_DIR / token / "consolidated.json"
    if not consolidated_path.exists():
        return None

    try:
        with open(consolidated_path, 'r') as f:
            data = json.load(f)

        tge_str = data.get("tge_date") or data.get("releaseDate")
        if not tge_str:
            return None

        # Parse ISO format
        if tge_str.endswith('Z'):
            tge_str = tge_str[:-1] + '+00:00'
        return datetime.fromisoformat(tge_str)
    except Exception:
        return None


def get_token_tge_datetime(token: str) -> Optional[datetime]:
    """
    Get TGE datetime including tge_time_utc when available.
    More precise than get_token_tge_date for hour-level calculations.
    """
    consolidated_path = TOKENS_DIR / token / "consolidated.json"
    if not consolidated_path.exists():
        return None

    try:
        with open(consolidated_path, 'r') as f:
            data = json.load(f)

        tge_str = data.get("tge_date") or data.get("releaseDate")
        if not tge_str:
            return None

        # Parse date
        if tge_str.endswith('Z'):
            tge_str = tge_str[:-1] + '+00:00'

        tge_date = datetime.fromisoformat(tge_str)

        # Add time if available (e.g., "10:00" or "14:30")
        tge_time_str = data.get("tge_time_utc")
        if tge_time_str and ":" in tge_time_str:
            try:
                hour, minute = map(int, tge_time_str.split(":"))
                tge_date = tge_date.replace(hour=hour, minute=minute)
            except ValueError:
                pass

        # Ensure timezone-aware
        if tge_date.tzinfo is None:
            tge_date = tge_date.replace(tzinfo=timezone.utc)

        return tge_date
    except Exception:
        return None


def get_hours_since_tge(token: str) -> float:
    """
    Get hours since TGE for a token.
    Returns -1 if TGE date unknown, negative hours if TGE is in the future.
    """
    tge_datetime = get_token_tge_datetime(token)
    if not tge_datetime:
        return -1

    now = datetime.now(timezone.utc)
    delta = now - tge_datetime
    return delta.total_seconds() / 3600


def get_token_lifecycle_stage(token: str, config: Dict[str, Any]) -> Tuple[str, int]:
    """
    Determine token lifecycle stage based on days since TGE.

    Returns: (stage_name, days_since_tge)
    """
    tge_date = get_token_tge_date(token)
    if not tge_date:
        return "UNKNOWN", -1

    # Ensure tge_date is timezone-aware
    if tge_date.tzinfo is None:
        tge_date = tge_date.replace(tzinfo=timezone.utc)

    days_since_tge = (datetime.now(timezone.utc) - tge_date).days

    windows = config.get("lifecycle_windows", {})

    fresh = windows.get("fresh", {})
    mature = windows.get("mature", {})
    established = windows.get("established", {})

    if days_since_tge <= fresh.get("max_days", 4):
        return "FRESH", days_since_tge
    elif days_since_tge <= mature.get("max_days", 21):
        return "MATURE", days_since_tge
    else:
        return "ESTABLISHED", days_since_tge


def get_next_unlock_date(token: str) -> Optional[Tuple[datetime, float]]:
    """
    Get next unlock date and percentage from vesting schedule.

    Returns: (unlock_datetime, unlock_percentage) or None
    """
    consolidated_path = TOKENS_DIR / token / "consolidated.json"
    if not consolidated_path.exists():
        return None

    try:
        with open(consolidated_path, 'r') as f:
            data = json.load(f)

        vesting = data.get("vesting_schedule", [])
        if isinstance(vesting, str):
            # String format - can't parse
            return None

        if not isinstance(vesting, list) or not vesting:
            return None

        now = datetime.now(timezone.utc)

        for unlock in vesting:
            if isinstance(unlock, dict):
                unlock_date_str = unlock.get("date")
                unlock_pct = unlock.get("percent", 0)

                if unlock_date_str:
                    try:
                        # Parse date (may be YYYY-MM-DD format)
                        if 'T' in unlock_date_str:
                            unlock_date = datetime.fromisoformat(unlock_date_str.replace('Z', '+00:00'))
                        else:
                            unlock_date = datetime.strptime(unlock_date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)

                        if unlock_date > now:
                            return (unlock_date, unlock_pct)
                    except Exception:
                        continue

        return None
    except Exception:
        return None


class HistoricalAnalyzer:
    """
    Analyzes MATURE tokens (5-21 days post-TGE) for pattern-based short signals.
    """

    def __init__(self, token_symbol: str):
        self.token = token_symbol
        self.config = load_historical_config()
        self.token_data = self._load_token_data()
        self.ohlcv_daily: List[Dict] = []
        self.ohlcv_4h: List[Dict] = []  # Session 115: For EMA calculation

    def _load_token_data(self) -> Dict[str, Any]:
        """Load consolidated token data."""
        consolidated_path = TOKENS_DIR / self.token / "consolidated.json"
        if consolidated_path.exists():
            with open(consolidated_path, 'r') as f:
                return json.load(f)
        return {}

    def fetch_historical_ohlcv(self, days: int = 21) -> List[Dict]:
        """
        Fetch daily OHLCV data from TGE to now.

        Session 115: Adaptive fetch - only request available days to avoid crash on young tokens.
        Gemini recommendation: Cache and only append new candles.
        """
        try:
            # Session 115: Adaptive fetch - use actual token age, not fixed 21
            tge_date = get_token_tge_date(self.token)
            if tge_date:
                if tge_date.tzinfo is None:
                    tge_date = tge_date.replace(tzinfo=timezone.utc)
                actual_days = (datetime.now(timezone.utc) - tge_date).days
                # Request only what exists + small buffer
                limit = min(actual_days + 2, days + 5)
            else:
                limit = days + 5

            # Safety: need at least 2 candles
            if limit < 2:
                print(f"  [WARN] Token too young ({limit} days), need at least 2")
                return []

            ohlcv = fetch_ohlcv_ccxt(self.token, timeframe="1d", limit=limit)
            self.ohlcv_daily = ohlcv or []
            return self.ohlcv_daily
        except Exception as e:
            print(f"  [ERROR] Failed to fetch historical OHLCV: {e}")
            return []

    def fetch_4h_ohlcv(self, limit: int = 120) -> List[Dict]:
        """
        Session 115: Fetch 4H OHLCV for EMA trend calculation.
        120 candles = 20 days of 4H data (enough for EMA20).
        """
        try:
            ohlcv = fetch_ohlcv_ccxt(self.token, timeframe="4h", limit=limit)
            self.ohlcv_4h = ohlcv or []
            return self.ohlcv_4h
        except Exception as e:
            print(f"  [ERROR] Failed to fetch 4H OHLCV: {e}")
            return []

    def calculate_ema(self, closes: List[float], period: int = 20) -> List[float]:
        """
        Session 115: Calculate EMA for trend detection.
        """
        if len(closes) < period:
            return []

        multiplier = 2 / (period + 1)
        ema = [sum(closes[:period]) / period]  # SMA for first value

        for price in closes[period:]:
            ema.append((price - ema[-1]) * multiplier + ema[-1])

        return ema

    def is_price_below_ema20_4h(self) -> Tuple[bool, float, float]:
        """
        Session 115: Check if current price is below 4H EMA20 (bearish trend).
        Returns: (is_below, current_price, ema_value)
        """
        if not self.ohlcv_4h:
            self.fetch_4h_ohlcv()

        if len(self.ohlcv_4h) < 20:
            return False, 0, 0

        closes = [c.get("close", 0) for c in self.ohlcv_4h]
        ema = self.calculate_ema(closes, 20)

        if not ema:
            return False, 0, 0

        current_price = closes[-1]
        current_ema = ema[-1]

        return current_price < current_ema, current_price, current_ema

    def get_smc_signals(self) -> Dict[str, Any]:
        """
        Session 121: Get Smart Money Concepts signals from MarketStructureAnalyzer.

        Integrates SMC signals (liquidity sweeps, order blocks, equilibrium) into
        historical pattern analysis for enhanced confluence detection.

        Returns:
            {
                "has_sweep": bool,           # Recent bearish sweep above highs
                "has_order_block": bool,     # Unmitigated bearish OB
                "in_premium_zone": bool,     # Price above equilibrium (50%)
                "confidence_boost": int,     # Total SMC confidence boost (0-19)
                "signals": List[str],        # Human-readable signal descriptions
                "raw_data": Dict             # Full SMC analysis data
            }
        """
        result = {
            "has_sweep": False,
            "has_order_block": False,
            "in_premium_zone": False,
            "confidence_boost": 0,
            "signals": [],
            "raw_data": {}
        }

        try:
            # Fetch 4H OHLCV if not already loaded
            if not self.ohlcv_4h:
                self.fetch_4h_ohlcv()

            if len(self.ohlcv_4h) < 50:
                return result

            # Run MarketStructureAnalyzer
            analyzer = MarketStructureAnalyzer()
            msa_result = analyzer.analyze(self.ohlcv_4h)
            result["raw_data"] = msa_result

            # Extract SMC signals
            current_price = self.ohlcv_4h[-1].get("close", 0)
            confidence_boost = 0

            # 1. Liquidity Sweeps - Check for recent bearish sweep
            sweeps = msa_result.get("liquidity_sweeps", [])
            recent_bearish_sweeps = [
                s for s in sweeps
                if s.get("direction") == "bearish"
                and s.get("candle_index", 0) >= len(self.ohlcv_4h) - 10  # Last 10 candles
            ]
            if recent_bearish_sweeps:
                result["has_sweep"] = True
                confidence_boost += 8  # Per SMC pattern scoring
                best_sweep = recent_bearish_sweeps[-1]
                result["signals"].append(
                    f"SWEEP_ABOVE_HIGH: ${best_sweep.get('sweep_price', 0):.4f} "
                    f"({best_sweep.get('strength', 'moderate')})"
                )

            # 2. Order Blocks - Check for unmitigated bearish OB
            order_blocks = msa_result.get("order_blocks", [])
            unmitigated_bearish_obs = [
                ob for ob in order_blocks
                if ob.get("direction") == "bearish"
                and not ob.get("mitigated", True)
            ]
            if unmitigated_bearish_obs:
                # Check if price is near any OB
                for ob in unmitigated_bearish_obs:
                    ob_top = ob.get("top", 0)
                    ob_bottom = ob.get("bottom", 0)
                    if ob_bottom <= current_price <= ob_top:
                        result["has_order_block"] = True
                        # Strong OB or preceded by sweep = more confident
                        if ob.get("strength") == "strong" or ob.get("preceded_by_sweep"):
                            confidence_boost += 6
                            result["signals"].append(
                                f"STRONG_BEARISH_OB: ${ob_bottom:.4f}-${ob_top:.4f} "
                                f"(sweep_preceded: {ob.get('preceded_by_sweep', False)})"
                            )
                        else:
                            confidence_boost += 3
                            result["signals"].append(
                                f"BEARISH_OB: ${ob_bottom:.4f}-${ob_top:.4f}"
                            )
                        break  # Only count one OB

            # 3. Equilibrium - Check if in premium zone
            equilibrium = msa_result.get("equilibrium", {})
            if equilibrium and equilibrium.get("zone") == "premium":
                result["in_premium_zone"] = True
                confidence_boost += 5
                eq_price = equilibrium.get("equilibrium_price", 0)
                result["signals"].append(
                    f"PREMIUM_ZONE: Above ${eq_price:.4f} (50% level)"
                )

            # 4. Equal Highs - Liquidity target (informational, no score boost)
            equal_levels = msa_result.get("equal_levels", [])
            eqh = [el for el in equal_levels if el.get("type") == "EQH"]
            if eqh:
                latest_eqh = eqh[-1]
                result["signals"].append(
                    f"EQH_TARGET: ${latest_eqh.get('price', 0):.4f} "
                    f"({latest_eqh.get('touch_count', 0)} touches)"
                )

            result["confidence_boost"] = confidence_boost

        except Exception as e:
            result["error"] = str(e)

        return result

    def check_bounce_filter(self) -> Dict[str, Any]:
        """
        Session 117: CRITICAL - Filter out alerts when token is bouncing/pumping.

        RAYLS False Alert Case Study:
        - Historical pattern score 92/100 (correct fundamentally)
        - BUT price was in +23% intraday pump
        - Alert sent at local highs = bad entry

        This filter checks REAL-TIME price action:
        1. 24h price change: Skip if >10% pump (momentum against short)
        2. RSI 4H: Skip if >65 (overbought = wait for pullback)
        3. Position in range: Skip if in upper 30% of 24h range (near highs)

        Returns:
            {
                "is_bouncing": bool,
                "skip_reason": str or None,
                "price_change_24h": float,
                "rsi_4h": float,
                "position_in_range": float (0-100, 100=at highs)
            }
        """
        result = {
            "is_bouncing": False,
            "skip_reason": None,
            "price_change_24h": 0,
            "rsi_4h": 50,
            "position_in_range": 50
        }

        if not self.ohlcv_4h:
            self.fetch_4h_ohlcv()

        if len(self.ohlcv_4h) < 6:  # Need at least 24h of 4H candles
            return result

        # 1. Calculate 24h price change
        # 6 candles ago = 24 hours for 4H timeframe
        closes = [c.get("close", 0) for c in self.ohlcv_4h]
        current_price = closes[-1]
        price_24h_ago = closes[-6] if len(closes) >= 6 else closes[0]

        if price_24h_ago > 0:
            price_change_24h = ((current_price - price_24h_ago) / price_24h_ago) * 100
        else:
            price_change_24h = 0

        result["price_change_24h"] = round(price_change_24h, 2)

        # Filter: Skip if pumping >10% in 24h
        if price_change_24h > 10:
            result["is_bouncing"] = True
            result["skip_reason"] = f"BOUNCE_PUMP: +{price_change_24h:.1f}% in 24h (>10% threshold)"
            return result

        # 2. Calculate RSI 4H
        rsi_4h = self._calculate_rsi(closes, period=14)
        result["rsi_4h"] = round(rsi_4h, 1)

        # Filter: Skip if overbought (RSI > 65)
        if rsi_4h > 65:
            result["is_bouncing"] = True
            result["skip_reason"] = f"BOUNCE_RSI: RSI {rsi_4h:.1f} > 65 (overbought)"
            return result

        # 3. Calculate position in 24h range
        # Get highs and lows from last 6 4H candles (24h)
        recent_candles = self.ohlcv_4h[-6:]
        highs = [c.get("high", 0) for c in recent_candles]
        lows = [c.get("low", 0) for c in recent_candles]

        high_24h = max(highs) if highs else current_price
        low_24h = min([l for l in lows if l > 0]) if lows else current_price
        range_24h = high_24h - low_24h

        if range_24h > 0:
            # 0 = at lows, 100 = at highs
            position_in_range = ((current_price - low_24h) / range_24h) * 100
        else:
            position_in_range = 50

        result["position_in_range"] = round(position_in_range, 1)

        # Filter: Skip if in upper 30% of range (near local highs)
        if position_in_range > 70:
            result["is_bouncing"] = True
            result["skip_reason"] = f"BOUNCE_RANGE: Price at {position_in_range:.0f}% of 24h range (near highs)"
            return result

        return result

    def _check_playbook_veto(self) -> Dict[str, Any]:
        """
        Session 118: Read execution state to check for structural blocks.

        Aligns historical analyzer with sniper's playbook veto logic,
        ensuring both systems respect the same strategic signals.

        FAIL OPEN: If no playbook exists, returns no veto.

        Returns:
            {"has_veto": bool, "reason": str or None}
        """
        try:
            path = TOKENS_DIR / self.token / "playbooks" / f"{self.token}_execution_state.json"
            if not path.exists():
                return {"has_veto": False}

            data = json.loads(path.read_text())

            # Check CHoCH (Change of Character) - Critical bearish structure
            if not data.get("structure", {}).get("choch_detected", True):
                return {"has_veto": True, "reason": "NO_CHOCH"}

            # Check Confidence Score
            if data.get("trade_confidence", {}).get("score", 10) < 6.0:
                return {"has_veto": True, "reason": "LOW_CONFIDENCE"}

            # Check explicit SKIP recommendation
            if data.get("recommendation", "").upper() == "SKIP":
                return {"has_veto": True, "reason": "PLAYBOOK_SKIP"}

            return {"has_veto": False}
        except Exception:
            return {"has_veto": False}

    def calculate_historical_profile(self) -> Dict[str, Any]:
        """
        Calculate historical profile metrics from OHLCV data.

        Returns:
            {
                "ath_price": float,
                "ath_day": int,
                "current_price": float,
                "drawdown_from_ath_pct": float,
                "day1_volume": float,
                "current_volume": float,
                "volume_decay_pct": float,
                "first_dump_completed": bool,
                ...
            }
        """
        if not self.ohlcv_daily:
            self.fetch_historical_ohlcv()

        if not self.ohlcv_daily or len(self.ohlcv_daily) < 2:
            return {"error": "Insufficient OHLCV data"}

        # Calculate metrics
        highs = [c.get("high", 0) for c in self.ohlcv_daily]
        closes = [c.get("close", 0) for c in self.ohlcv_daily]
        volumes = [c.get("volume", 0) for c in self.ohlcv_daily]

        ath_price = max(highs) if highs else 0
        ath_day = highs.index(ath_price) + 1 if ath_price > 0 else 0

        current_price = closes[-1] if closes else 0
        drawdown_pct = ((ath_price - current_price) / ath_price * 100) if ath_price > 0 else 0

        day1_volume = volumes[0] if volumes else 0
        current_volume = volumes[-1] if volumes else 0
        volume_decay_pct = ((day1_volume - current_volume) / day1_volume * 100) if day1_volume > 0 else 0

        # First dump completed = >50% drop from ATH
        first_dump_completed = drawdown_pct >= 50

        # Find lowest point
        lows = [c.get("low", 0) for c in self.ohlcv_daily]
        atl_price = min([l for l in lows if l > 0]) if lows else 0

        # Bounce from low
        bounce_pct = ((current_price - atl_price) / atl_price * 100) if atl_price > 0 else 0

        return {
            "ath_price": ath_price,
            "ath_day": ath_day,
            "atl_price": atl_price,
            "current_price": current_price,
            "drawdown_from_ath_pct": round(drawdown_pct, 2),
            "day1_volume": day1_volume,
            "current_volume": current_volume,
            "volume_decay_pct": round(volume_decay_pct, 2),
            "first_dump_completed": first_dump_completed,
            "bounce_from_low_pct": round(bounce_pct, 2),
            "total_candles": len(self.ohlcv_daily)
        }

    def match_vc_dump_pattern(self, profile: Dict, days_since_tge: int) -> Tuple[bool, str]:
        """
        Check if token matches VC Dump pattern.

        Pattern: ATH day 1-2 -> first dump day 3-5 -> dead cat bounce day 6-10 -> second dump day 11-14
        """
        patterns = self.config.get("patterns", {}).get("vc_dump", {})
        entry_window = patterns.get("entry_window", {})

        start_day = entry_window.get("start_day", 11)
        end_day = entry_window.get("end_day", 14)

        ath_day = profile.get("ath_day", 0)
        drawdown = profile.get("drawdown_from_ath_pct", 0)
        bounce = profile.get("bounce_from_low_pct", 0)
        first_dump = profile.get("first_dump_completed", False)

        # Check if in entry window
        in_window = start_day <= days_since_tge <= end_day

        # Check criteria
        ath_early = ath_day <= 3
        significant_dump = drawdown >= 40
        has_bounced = 10 <= bounce <= 40

        if in_window and ath_early and significant_dump and has_bounced and first_dump:
            return True, f"VC_DUMP phase 2 (Day {days_since_tge}, ATH day {ath_day}, -{drawdown:.0f}% dump, +{bounce:.0f}% bounce)"

        return False, ""

    def match_unlock_cliff_pattern(self, days_since_tge: int) -> Tuple[bool, str, int]:
        """
        Check if token is approaching an unlock cliff.

        Returns: (matched, reason, hours_to_unlock)
        """
        next_unlock = get_next_unlock_date(self.token)
        if not next_unlock:
            return False, "", 0

        unlock_date, unlock_pct = next_unlock
        hours_to_unlock = (unlock_date - datetime.now(timezone.utc)).total_seconds() / 3600

        scoring = self.config.get("scoring", {}).get("components", {}).get("unlock_proximity", {})
        max_hours = scoring.get("max_hours", 48)

        if hours_to_unlock <= max_hours:
            return True, f"UNLOCK_CLIFF in {hours_to_unlock:.0f}h ({unlock_pct:.1f}% unlocking)", int(hours_to_unlock)

        return False, "", int(hours_to_unlock)

    def match_volume_exhaustion_pattern(self, profile: Dict) -> Tuple[bool, str]:
        """
        Check if token shows volume exhaustion pattern.
        """
        volume_decay = profile.get("volume_decay_pct", 0)
        threshold = 80

        if volume_decay >= 90:
            return True, f"VOLUME_EXHAUSTION extreme ({volume_decay:.0f}% decay)"
        elif volume_decay >= threshold:
            return True, f"VOLUME_EXHAUSTION ({volume_decay:.0f}% decay)"

        return False, ""

    def match_airdrop_fatigue_pattern(self, profile: Dict, days_since_tge: int) -> Tuple[bool, str]:
        """
        Session 115: Check if token matches AIRDROP_FATIGUE pattern.

        This is the PRIMARY pattern for Month 1 tokens (Day 5-21).
        Selling pressure comes from airdrop recipients, not VCs (who have 6-12mo cliffs).

        Criteria:
        - Days 5-21
        - Drawdown > 40%
        - Volume decay > 60%
        - Price below 4H EMA20 (bearish trend)
        """
        patterns = self.config.get("patterns", {}).get("airdrop_fatigue", {})
        criteria = patterns.get("criteria", {})

        min_day = criteria.get("days_since_tge_min", 5)
        max_day = criteria.get("days_since_tge_max", 21)
        min_drawdown = criteria.get("drawdown_from_ath_min", 40)
        min_vol_decay = criteria.get("volume_decay_min", 60)

        drawdown = profile.get("drawdown_from_ath_pct", 0)
        volume_decay = profile.get("volume_decay_pct", 0)

        # Check day window
        in_window = min_day <= days_since_tge <= max_day

        # Check metrics
        has_drawdown = drawdown >= min_drawdown
        has_vol_decay = volume_decay >= min_vol_decay

        # Check 4H EMA trend
        is_below_ema, current_price, ema_value = self.is_price_below_ema20_4h()

        if in_window and has_drawdown and has_vol_decay and is_below_ema:
            return True, f"AIRDROP_FATIGUE (Day {days_since_tge}, -{drawdown:.0f}%, vol -{volume_decay:.0f}%, below EMA20)"

        # Partial match for debugging
        if in_window and has_drawdown and has_vol_decay:
            # Pattern almost matches but EMA not confirmed
            return False, f"AIRDROP_FATIGUE partial (needs EMA confirmation, price ${current_price:.4f} vs EMA ${ema_value:.4f})"

        return False, ""

    def calculate_pattern_score(self, profile: Dict, days_since_tge: int) -> Dict[str, Any]:
        """
        Calculate Historical Pattern Score.

        Session 103 Gemini Updated Weights:
        - Volume Decay: 45 pts (PRIMARY signal - per Gemini optimization)
        - Pattern Match: 25 pts (AIRDROP_FATIGUE/VC_DUMP)
        - ATH Distance: 10 pts (>60% drawdown)
        - Technical Setup: 10 pts (4H EMA trend + funding - reduced per Gemini)
        - Unlock Proximity: 10 pts (for tokens with unlock data - Month 2+)

        Total: 100 pts (threshold 70 for HISTORICAL_SHORT signal)
        """
        score = 0
        signals = []
        components = {}

        scoring_config = self.config.get("scoring", {}).get("components", {})

        # 1. Unlock Proximity (10 pts for tokens with unlock data - Month 2+)
        # Session 103 Gemini: Re-enable at 10 pts for tokens with actual unlock schedules
        unlock_matched, unlock_reason, hours_to_unlock = self.match_unlock_cliff_pattern(days_since_tge)
        unlock_config = scoring_config.get("unlock_proximity", {})
        unlock_weight = unlock_config.get("weight", 10)  # Session 103: 10 pts (was 0)

        if unlock_matched and unlock_weight > 0:
            if hours_to_unlock <= 24:
                unlock_score = unlock_weight
            elif hours_to_unlock <= 48:
                unlock_score = unlock_weight * 0.83
            else:
                unlock_score = unlock_weight * 0.5
            score += unlock_score
            signals.append(unlock_reason)
            components["unlock_proximity"] = {"score": unlock_score, "reason": unlock_reason, "hours": hours_to_unlock}
        else:
            components["unlock_proximity"] = {"score": 0, "reason": "Disabled for Month 1 (no VC unlocks)", "hours": hours_to_unlock}

        # 2. Volume Decay (45 pts max - Session 103 Gemini PRIMARY signal)
        volume_decay = profile.get("volume_decay_pct", 0)
        vol_config = scoring_config.get("volume_decay", {})
        vol_weight = vol_config.get("weight", 45)  # Session 103: 45 pts (was 35)

        if volume_decay >= 90:
            vol_score = vol_weight
            vol_reason = f"VOLUME_EXHAUSTION extreme ({volume_decay:.0f}%)"
        elif volume_decay >= 80:
            vol_score = vol_weight * 0.8
            vol_reason = f"VOLUME_EXHAUSTION ({volume_decay:.0f}%)"
        elif volume_decay >= 60:
            vol_score = vol_weight * 0.4
            vol_reason = f"Volume declining ({volume_decay:.0f}%)"
        else:
            vol_score = 0
            vol_reason = f"Volume stable ({volume_decay:.0f}%)"

        score += vol_score
        if vol_score > 0:
            signals.append(vol_reason)
        components["volume_decay"] = {"score": vol_score, "reason": vol_reason, "decay_pct": volume_decay}

        # 3. Pattern Match (25 pts max - Session 103 Gemini reduced from 35)
        # Check AIRDROP_FATIGUE first (primary for Month 1), then VC_DUMP
        pattern_config = scoring_config.get("pattern_match", {})
        pattern_weight = pattern_config.get("weight", 25)  # Session 103: 25 pts (was 35)
        pattern_score = 0
        pattern_matched = None
        pattern_reason = ""

        # Try AIRDROP_FATIGUE first (Month 1 primary pattern)
        airdrop_matched, airdrop_reason = self.match_airdrop_fatigue_pattern(profile, days_since_tge)
        if airdrop_matched:
            pattern_score = pattern_weight
            pattern_matched = "AIRDROP_FATIGUE"
            pattern_reason = airdrop_reason
            signals.append(airdrop_reason)
        else:
            # Fallback to VC_DUMP
            vc_matched, vc_reason = self.match_vc_dump_pattern(profile, days_since_tge)
            if vc_matched:
                pattern_score = pattern_weight
                pattern_matched = "VC_DUMP"
                pattern_reason = vc_reason
                signals.append(vc_reason)
            else:
                # Check for partial AIRDROP_FATIGUE match (for debugging)
                if "partial" in airdrop_reason.lower():
                    pattern_reason = airdrop_reason

        if pattern_matched:
            components["pattern_match"] = {"score": pattern_score, "reason": pattern_reason, "pattern": pattern_matched}
        else:
            components["pattern_match"] = {"score": 0, "reason": pattern_reason or "No pattern match", "pattern": None}

        score += pattern_score

        # 4. ATH Distance (10 pts max) - Gemini reduced from 25
        drawdown = profile.get("drawdown_from_ath_pct", 0)
        ath_config = scoring_config.get("ath_distance", {})
        ath_weight = ath_config.get("weight", 10)

        if drawdown >= 80:
            ath_score = ath_weight
            ath_reason = f"ATH_DISTANCE extreme (-{drawdown:.0f}%)"
        elif drawdown >= 60:
            ath_score = ath_weight * 0.7
            ath_reason = f"ATH_DISTANCE favorable (-{drawdown:.0f}%)"
        elif drawdown >= 40:
            ath_score = ath_weight * 0.3
            ath_reason = f"ATH_DISTANCE moderate (-{drawdown:.0f}%)"
        else:
            ath_score = 0
            ath_reason = f"ATH_DISTANCE low (-{drawdown:.0f}%)"

        score += ath_score
        if ath_score > 0:
            signals.append(ath_reason)
        components["ath_distance"] = {"score": ath_score, "reason": ath_reason, "drawdown_pct": drawdown}

        # 5. Technical Setup (10 pts max) - Session 103 Gemini: Reduced from 20 pts
        # Use 4H EMA trend + funding (secondary confirmation, not primary driver)
        ta_config = scoring_config.get("technical_setup", {})
        ta_weight = ta_config.get("weight", 10)  # Session 103: 10 pts (was 20)

        # Fetch funding rate
        funding_rate = fetch_funding_rate(self.token)

        ta_score = 0
        ta_reasons = []

        # 4H EMA20 trend (primary signal - 60% of weight)
        is_below_ema, current_price, ema_value = self.is_price_below_ema20_4h()
        if is_below_ema:
            ta_score += ta_weight * 0.6
            ta_reasons.append(f"Below 4H EMA20 ({current_price:.4f} < {ema_value:.4f})")

        # Funding rate (secondary signal - 40% of weight)
        if funding_rate is not None and funding_rate > 0.05:
            ta_score += ta_weight * 0.4
            ta_reasons.append(f"Funding positive ({funding_rate:.3f}%)")

        score += ta_score
        if ta_reasons:
            signals.extend(ta_reasons)
        components["technical_setup"] = {"score": ta_score, "reasons": ta_reasons, "funding_rate": funding_rate}

        # 6. Session 121: SMC Signals (BONUS - up to 19 pts, does not reduce below 100)
        # Smart Money Concepts: Liquidity sweeps, Order Blocks, Equilibrium
        # These are confluence factors that can boost score above threshold
        smc_signals = self.get_smc_signals()
        smc_boost = smc_signals.get("confidence_boost", 0)

        if smc_boost > 0:
            score += smc_boost
            # Add SMC signals to output
            for smc_sig in smc_signals.get("signals", []):
                signals.append(f"🎯 SMC: {smc_sig}")

        components["smc_signals"] = {
            "score": smc_boost,
            "has_sweep": smc_signals.get("has_sweep", False),
            "has_order_block": smc_signals.get("has_order_block", False),
            "in_premium_zone": smc_signals.get("in_premium_zone", False),
            "signals": smc_signals.get("signals", [])
        }

        # Determine recommendation
        threshold = self.config.get("scoring", {}).get("threshold", 70)

        if score >= threshold:
            recommendation = "HISTORICAL_SHORT"
        elif score >= threshold * 0.7:
            recommendation = "WATCH"
        else:
            recommendation = "SKIP"

        return {
            "score": round(score, 1),
            "max_score": 100,
            "threshold": threshold,
            "signals": signals,
            "components": components,
            "recommendation": recommendation
        }

    def _calculate_rsi(self, closes: List[float], period: int = 14) -> float:
        """Calculate RSI from close prices."""
        if len(closes) < period + 1:
            return 50.0

        gains = []
        losses = []

        for i in range(1, len(closes)):
            change = closes[i] - closes[i-1]
            if change > 0:
                gains.append(change)
                losses.append(0)
            else:
                gains.append(0)
                losses.append(abs(change))

        if len(gains) < period:
            return 50.0

        avg_gain = sum(gains[-period:]) / period
        avg_loss = sum(losses[-period:]) / period

        if avg_loss == 0:
            return 100.0

        rs = avg_gain / avg_loss
        return 100 - (100 / (1 + rs))

    def analyze(self) -> Dict[str, Any]:
        """
        Full historical analysis pipeline.

        Returns comprehensive analysis result.
        """
        result = {
            "token": self.token,
            "analysis_type": "HISTORICAL_PATTERN",
            "timestamp": datetime.now(timezone.utc).isoformat()
        }

        # 1. Check lifecycle stage
        stage, days_since_tge = get_token_lifecycle_stage(self.token, self.config)
        result["lifecycle_stage"] = stage
        result["days_since_tge"] = days_since_tge

        if stage != "MATURE":
            result["skip_reason"] = f"Not in MATURE window (stage: {stage}, days: {days_since_tge})"
            result["recommendation"] = "SKIP"
            return result

        # 2. Fetch historical data
        self.fetch_historical_ohlcv(days=days_since_tge + 5)

        if not self.ohlcv_daily or len(self.ohlcv_daily) < 3:
            result["error"] = "Insufficient historical data"
            result["recommendation"] = "SKIP"
            return result

        # 3. Calculate historical profile
        profile = self.calculate_historical_profile()
        result["profile"] = profile

        if profile.get("error"):
            result["error"] = profile["error"]
            result["recommendation"] = "SKIP"
            return result

        # 4. Calculate pattern score
        scoring = self.calculate_pattern_score(profile, days_since_tge)

        # 4.5 Session 117 CRITICAL: Bounce Filter Check
        # Historical score may be high, but if token is bouncing/pumping RIGHT NOW
        # it's a bad entry. Wait for pullback.
        bounce_check = self.check_bounce_filter()
        result["bounce_filter"] = bounce_check

        if bounce_check["is_bouncing"]:
            # Override recommendation even if score is high
            original_recommendation = scoring["recommendation"]
            scoring["recommendation"] = "SKIP"
            scoring["signals"].append(f"⚠️ {bounce_check['skip_reason']}")
            result["skip_reason"] = bounce_check["skip_reason"]
            result["original_recommendation"] = original_recommendation
            # Still return full analysis but with SKIP recommendation

        # 4.6 Session 118: Playbook Veto Gate - Strategic overrides Tactical
        playbook_veto = self._check_playbook_veto()
        result["playbook_veto"] = playbook_veto

        if playbook_veto.get("has_veto"):
            # Downgrade recommendation to HOLD (not SKIP - we want to track it)
            if scoring["recommendation"] == "HISTORICAL_SHORT":
                result["original_recommendation"] = scoring["recommendation"]
            scoring["recommendation"] = "HOLD"
            result["veto_active"] = True
            result["veto_reason"] = playbook_veto["reason"]
            scoring["signals"].append(f"🛑 VETO: {playbook_veto['reason']} (playbook structure not confirmed)")
        else:
            result["veto_active"] = False

        # 5. Session 117: Check for learned pattern overrides
        learned_override = check_learned_pattern_override(
            self.token, days_since_tge, profile, scoring.get("components", {})
        )
        result["learned_pattern_override"] = learned_override

        # Apply score modifier if learned pattern applies
        original_score = scoring["score"]
        if learned_override.get("applies"):
            modifier = learned_override.get("score_modifier", 1.0)
            scoring["score"] = min(100, round(original_score * modifier, 1))
            scoring["signals"].append(
                f"📚 LEARNED: {learned_override.get('pattern_name')} (+{int((modifier-1)*100)}% score boost)"
            )

            # If original was below threshold but modified is above, update recommendation
            threshold = scoring.get("threshold", 70)
            if original_score < threshold and scoring["score"] >= threshold:
                scoring["recommendation"] = "HISTORICAL_SHORT"
                scoring["signals"].append("🔓 Override: CHoCH veto bypassed by confluence")

        result.update(scoring)

        # 6. Build alert data if above threshold
        if scoring["recommendation"] == "HISTORICAL_SHORT":
            current_price = profile.get("current_price", 0)

            # Session 121: Extract SMC data for alert
            smc_component = scoring.get("components", {}).get("smc_signals", {})

            result["alert_data"] = {
                "alert_type": "HISTORICAL_PATTERN_ALERT",
                "token": self.token,
                "days_since_tge": days_since_tge,
                "pattern_score": scoring["score"],
                "signals": scoring["signals"],
                "entry_price": current_price,
                "stop_loss": current_price * 1.30,  # 30% SL
                "take_profit": current_price * 0.75,  # -25% target
                "ath_price": profile.get("ath_price"),
                "drawdown_pct": profile.get("drawdown_from_ath_pct"),
                # Session 121: SMC signals for alert display
                "smc_boost": smc_component.get("score", 0),
                "smc_sweep": smc_component.get("has_sweep", False),
                "smc_order_block": smc_component.get("has_order_block", False),
                "smc_premium_zone": smc_component.get("in_premium_zone", False),
                "smc_signals": smc_component.get("signals", [])
            }

        return result


def analyze_token(token: str) -> Dict[str, Any]:
    """Convenience function to analyze a single token."""
    analyzer = HistoricalAnalyzer(token)
    return analyzer.analyze()


def main():
    """CLI for testing historical analyzer."""
    import argparse

    parser = argparse.ArgumentParser(description="Historical TGE Pattern Analyzer")
    parser.add_argument("token", help="Token symbol (e.g., MONAD)")
    parser.add_argument("--json", "-j", action="store_true", help="Output as JSON")

    args = parser.parse_args()

    result = analyze_token(args.token.upper())

    if args.json:
        print(json.dumps(result, indent=2, default=str))
    else:
        print(f"\n{'='*60}")
        print(f"  HISTORICAL PATTERN ANALYSIS: {args.token.upper()}")
        print(f"{'='*60}")

        print(f"\n  Lifecycle: {result.get('lifecycle_stage')} (Day {result.get('days_since_tge')})")

        if result.get("skip_reason"):
            print(f"  Skip: {result['skip_reason']}")
        elif result.get("error"):
            print(f"  Error: {result['error']}")
        else:
            profile = result.get("profile", {})
            print(f"\n  Profile:")
            print(f"    ATH: ${profile.get('ath_price', 0):.4f} (Day {profile.get('ath_day', 0)})")
            print(f"    Current: ${profile.get('current_price', 0):.4f}")
            print(f"    Drawdown: {profile.get('drawdown_from_ath_pct', 0):.1f}%")
            print(f"    Volume Decay: {profile.get('volume_decay_pct', 0):.1f}%")

            print(f"\n  Score: {result.get('score', 0)}/100 (threshold: {result.get('threshold', 70)})")
            print(f"  Recommendation: {result.get('recommendation')}")

            if result.get("signals"):
                print(f"\n  Signals:")
                for s in result["signals"]:
                    print(f"    - {s}")

            components = result.get("components", {})
            if components:
                print(f"\n  Components:")
                for name, data in components.items():
                    print(f"    {name}: {data.get('score', 0):.1f} - {data.get('reason', '')}")

        print(f"\n{'='*60}\n")


if __name__ == "__main__":
    main()
