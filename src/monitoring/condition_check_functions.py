"""
Condition Check Functions for Playbook Monitoring

This module contains all the individual check functions that evaluate
entry conditions for playbooks. Each function corresponds to a specific
technical or macro condition (e.g., trendline break, USDT.D bounce, BTC level).

Author: DACLE System
Created: 2025-12-02
Session: 82
"""

from typing import Tuple, Dict
from datetime import datetime, timezone
import json
from pathlib import Path

from scripts.helpers.price_action_analyzer import PriceActionAnalyzer
from scripts.helpers.indices_tracker import IndicesTracker
from src.analysis.technical_pattern_detector import (
    TrendlineBreakDetector,
    CandlestickAnalyzer,
    RetestDetector
)
from scripts.helpers.ta_decision_logger import TADecisionLogger
import ccxt
import os

# Global decision logger (tracks all TA decisions)
_decision_logger = TADecisionLogger()

# DEPRECATED: Tier 3 GPT-4o Vision analysis removed (Session 263 cost optimization)
# GPT-4o Vision cost $0.01/call and showed 70-80% accuracy (not worth the cost)
# Use Tier 1 free rule-based analysis instead
ENABLE_TIER3 = False  # Permanently disabled


def _get_btc_price() -> float:
    """Helper to get current BTC price from Binance."""
    try:
        exchange = ccxt.binance({'enableRateLimit': True})
        ticker = exchange.fetch_ticker('BTC/USDT')
        return ticker['last']
    except Exception:
        return 0


def _get_eth_price() -> float:
    """Helper to get current ETH price from Binance."""
    try:
        exchange = ccxt.binance({'enableRateLimit': True})
        ticker = exchange.fetch_ticker('ETH/USDT')
        return ticker['last']
    except Exception:
        return 0


def check_usdt_dominance(params: Dict) -> Tuple[bool, str]:
    """
    Check if USDT.D has bounced from support level.

    Condition: USDT.D rises above bounce_threshold after testing support_level

    Args:
        params: {
            'support_level': float (e.g., 4.88),
            'bounce_threshold': float (e.g., 5.0)
        }

    Returns:
        (met: bool, reason: str)

    Example:
        check_usdt_dominance({'support_level': 4.88, 'bounce_threshold': 5.0})
        → (True, "USDT.D bounced to 5.12% from 4.88% support")
    """
    try:
        tracker = IndicesTracker()
        indices_data = tracker.fetch_all_indices()

        if not indices_data or 'indices' not in indices_data:
            return (False, "Unable to fetch USDT.D data")

        usdt_d_data = indices_data['indices'].get('usdt_d', {})
        usdt_d = usdt_d_data.get('value', 0)

        support_level = params.get('support_level', 4.88)
        bounce_threshold = params.get('bounce_threshold', 5.0)

        if usdt_d >= bounce_threshold:
            return (True, f"USDT.D bounced to {usdt_d:.2f}% from {support_level}% support")
        elif usdt_d < support_level:
            return (False, f"USDT.D at {usdt_d:.2f}%, below {support_level}% support")
        else:
            return (False, f"USDT.D at {usdt_d:.2f}%, waiting for bounce above {bounce_threshold}%")

    except Exception as e:
        return (False, f"Error checking USDT.D: {str(e)}")


def check_btc_level(params: Dict) -> Tuple[bool, str]:
    """
    Check if BTC is above/below a key level.

    Condition: BTC price relative to a resistance or support level

    Args:
        params: {
            'level': float (e.g., 88000),
            'direction': str ('above' or 'below')
        }

    Returns:
        (met: bool, reason: str)

    Example:
        check_btc_level({'level': 88000, 'direction': 'below'})
        → (True, "BTC at $85,979 (below $88k resistance)")
    """
    try:
        current_price = _get_btc_price()

        level = params.get('level', 88000)
        direction = params.get('direction', 'below')

        if direction == 'below':
            if current_price < level:
                return (True, f"BTC at ${current_price:,.0f} (below ${level:,.0f} resistance)")
            else:
                return (False, f"BTC at ${current_price:,.0f} (above ${level:,.0f}, waiting for rejection)")
        else:  # direction == 'above'
            if current_price > level:
                return (True, f"BTC at ${current_price:,.0f} (above ${level:,.0f} support)")
            else:
                return (False, f"BTC at ${current_price:,.0f} (below ${level:,.0f}, waiting for breakout)")

    except Exception as e:
        return (False, f"Error checking BTC level: {str(e)}")


def check_eth_level(params: Dict) -> Tuple[bool, str]:
    """
    Check if ETH is above/below a key level.

    Condition: ETH price relative to a resistance or support level

    Args:
        params: {
            'level': float (e.g., 3100),
            'direction': str ('above' or 'below')
        }

    Returns:
        (met: bool, reason: str)

    Example:
        check_eth_level({'level': 3100, 'direction': 'below'})
        → (True, "ETH at $3,045 (below $3,100 resistance)")
    """
    try:
        current_price = _get_eth_price()

        level = params.get('level', 3100)
        direction = params.get('direction', 'below')

        if direction == 'below':
            if current_price < level:
                return (True, f"ETH at ${current_price:,.0f} (below ${level:,.0f} resistance)")
            else:
                return (False, f"ETH at ${current_price:,.0f} (above ${level:,.0f}, waiting for rejection)")
        else:  # direction == 'above'
            if current_price > level:
                return (True, f"ETH at ${current_price:,.0f} (above ${level:,.0f} support)")
            else:
                return (False, f"ETH at ${current_price:,.0f} (below ${level:,.0f}, waiting for breakout)")

    except Exception as e:
        return (False, f"Error checking ETH level: {str(e)}")


def check_trendline_break(params: Dict) -> Tuple[bool, str]:
    """
    Check if token broke a trendline using automated pattern detection.

    Condition: Price breaks below descending trendline or above ascending trendline

    Args:
        params: {
            'direction': str ('upside' or 'downside'),
            'token_symbol': str,
            'timeframe': str (optional, default '4h')
        }

    Returns:
        (met: bool, reason: str)

    Example:
        check_trendline_break({'direction': 'downside', 'token_symbol': 'RLS'})
        → (True, "Price $0.024 broke below support $0.026 | Death cross confirmed")
    """
    try:
        direction = params.get('direction', 'downside')
        token_symbol = params.get('token_symbol', 'TOKEN')
        timeframe = params.get('timeframe', '4h')

        # Add /USDT suffix if not present
        if '/' not in token_symbol:
            token_symbol = f"{token_symbol}/USDT"

        # Use Tier 1 automated detection
        detector = TrendlineBreakDetector()
        result = detector.detect_break(
            token_symbol=token_symbol,
            direction=direction,
            timeframe=timeframe
        )

        # Extract clean symbol for logging
        clean_symbol = token_symbol.split('/')[0] if '/' in token_symbol else token_symbol

        # Escalate to Tier 3 if confidence is low and Tier 3 is enabled
        if ENABLE_TIER3 and result.confidence < 0.75:
            can_call, reason = _cost_guard.can_call(token_symbol)

            if can_call:
                try:
                    analyzer = GPT4VisionAnalyzer()
                    tier3_result = analyzer.analyze_trendline_break(
                        token_symbol=token_symbol,
                        direction=direction,
                        timeframe=timeframe
                    )
                    _cost_guard.record_call(token_symbol)

                    # Log Tier 3 decision
                    _decision_logger.log_decision(
                        token_symbol=clean_symbol,
                        condition_type='trendline_break',
                        tier_used=3,
                        confidence=tier3_result.confidence,
                        decision_met=tier3_result.met,
                        reasoning=tier3_result.reasoning,
                        cost=tier3_result.cost_estimate,
                        metadata={'direction': direction, 'timeframe': timeframe}
                    )

                    return (
                        tier3_result.met,
                        f"[Tier 3 GPT-4o {tier3_result.confidence:.0%}] {tier3_result.reasoning}"
                    )
                except Exception as e:
                    # Fall back to Tier 1 if Tier 3 fails - log as Tier 1
                    _decision_logger.log_decision(
                        token_symbol=clean_symbol,
                        condition_type='trendline_break',
                        tier_used=1,
                        confidence=result.confidence,
                        decision_met=result.met,
                        reasoning=f"Tier 3 failed: {str(e)}, fallback to Tier 1",
                        cost=0.0,
                        metadata={'direction': direction, 'timeframe': timeframe, 'tier3_error': str(e)}
                    )
                    return (result.met, f"[Tier 1 {result.confidence:.0%}, Tier 3 failed: {str(e)}] {result.reason}")
            else:
                # Cost guard blocked - log as Tier 1
                _decision_logger.log_decision(
                    token_symbol=clean_symbol,
                    condition_type='trendline_break',
                    tier_used=1,
                    confidence=result.confidence,
                    decision_met=result.met,
                    reasoning=f"Cost guard blocked: {reason}",
                    cost=0.0,
                    metadata={'direction': direction, 'timeframe': timeframe, 'cost_guard_reason': reason}
                )
                return (result.met, f"[Tier 1 {result.confidence:.0%}, Cost Guard: {reason}] {result.reason}")

        # Return Tier 1 result - log decision
        _decision_logger.log_decision(
            token_symbol=clean_symbol,
            condition_type='trendline_break',
            tier_used=1,
            confidence=result.confidence,
            decision_met=result.met,
            reasoning=result.reason,
            cost=0.0,
            metadata={'direction': direction, 'timeframe': timeframe}
        )

        if result.confidence >= 0.75:
            return (result.met, f"[Tier 1 HIGH CONFIDENCE {result.confidence:.0%}] {result.reason}")
        else:
            return (result.met, f"[Tier 1 {result.confidence:.0%}] {result.reason}")

    except Exception as e:
        return (False, f"Error detecting trendline break: {str(e)}")


def check_rejection_candle(params: Dict) -> Tuple[bool, str]:
    """
    Check if token printed a rejection candle using automated pattern detection.

    Condition: Strong rejection wick/candle at resistance

    Args:
        params: {
            'token_symbol': str,
            'level': float,
            'direction': str (optional, 'downside' for bearish rejection, 'upside' for bullish),
            'timeframe': str (optional, default '4h'),
            'lookback': int (optional, default 3 candles)
        }

    Returns:
        (met: bool, reason: str)

    Example:
        check_rejection_candle({'token_symbol': 'RLS', 'level': 0.0264})
        → (True, "At $0.0264: Strong upper wick (3.2x body) | Close in lower 25% of range")
    """
    try:
        token_symbol = params.get('token_symbol', 'TOKEN')
        level = params.get('level')
        direction = params.get('direction', 'downside')
        timeframe = params.get('timeframe', '4h')
        lookback = params.get('lookback', 3)

        if not level:
            return (False, f"Missing 'level' parameter for {token_symbol} rejection check")

        # Add /USDT suffix if not present
        if '/' not in token_symbol:
            token_symbol = f"{token_symbol}/USDT"

        # Use Tier 1 automated detection
        analyzer = CandlestickAnalyzer()
        result = analyzer.detect_rejection(
            token_symbol=token_symbol,
            level=level,
            direction=direction,
            timeframe=timeframe,
            lookback=lookback
        )

        # Escalate to Tier 3 if confidence is low and Tier 3 is enabled
        if ENABLE_TIER3 and result.confidence < 0.75:
            can_call, reason = _cost_guard.can_call(token_symbol)

            if can_call:
                try:
                    vision_analyzer = GPT4VisionAnalyzer()
                    tier3_result = vision_analyzer.analyze_rejection_candle(
                        token_symbol=token_symbol,
                        level=level,
                        direction=direction,
                        timeframe=timeframe
                    )
                    _cost_guard.record_call(token_symbol)

                    return (
                        tier3_result.met,
                        f"[Tier 3 GPT-4o {tier3_result.confidence:.0%}] {tier3_result.reasoning}"
                    )
                except Exception as e:
                    # Fall back to Tier 1 if Tier 3 fails
                    return (result.met, f"[Tier 1 {result.confidence:.0%}, Tier 3 failed: {str(e)}] {result.reason}")
            else:
                # Cost guard blocked - return Tier 1 with note
                return (result.met, f"[Tier 1 {result.confidence:.0%}, Cost Guard: {reason}] {result.reason}")

        # Return Tier 1 result
        if result.confidence >= 0.75:
            return (result.met, f"[Tier 1 HIGH CONFIDENCE {result.confidence:.0%}] {result.reason}")
        else:
            return (result.met, f"[Tier 1 {result.confidence:.0%}] {result.reason}")

    except Exception as e:
        return (False, f"Error detecting rejection candle: {str(e)}")


def check_retest_confirmation(params: Dict) -> Tuple[bool, str]:
    """
    Check if token confirmed a retest using automated pattern detection.

    Condition: Price retests broken level and shows rejection

    Args:
        params: {
            'token_symbol': str,
            'level': float,
            'direction': str (optional, 'downside' for resistance retest, 'upside' for support retest),
            'timeframe': str (optional, default '4h')
        }

    Returns:
        (met: bool, reason: str)

    Example:
        check_retest_confirmation({'token_symbol': 'RLS', 'level': 0.0264})
        → (True, "Price returned to $0.0264 | Failed to reclaim level | Lower high formed")
    """
    try:
        token_symbol = params.get('token_symbol', 'TOKEN')
        level = params.get('level')
        direction = params.get('direction', 'downside')
        timeframe = params.get('timeframe', '4h')

        if not level:
            return (False, f"Missing 'level' parameter for {token_symbol} retest check")

        # Add /USDT suffix if not present
        if '/' not in token_symbol:
            token_symbol = f"{token_symbol}/USDT"

        # Use Tier 1 automated detection
        detector = RetestDetector()
        result = detector.detect_retest(
            token_symbol=token_symbol,
            level=level,
            direction=direction,
            timeframe=timeframe
        )

        # Escalate to Tier 3 if confidence is low and Tier 3 is enabled
        if ENABLE_TIER3 and result.confidence < 0.75:
            can_call, reason = _cost_guard.can_call(token_symbol)

            if can_call:
                try:
                    vision_analyzer = GPT4VisionAnalyzer()
                    tier3_result = vision_analyzer.analyze_retest_confirmation(
                        token_symbol=token_symbol,
                        level=level,
                        direction=direction,
                        timeframe=timeframe
                    )
                    _cost_guard.record_call(token_symbol)

                    return (
                        tier3_result.met,
                        f"[Tier 3 GPT-4o {tier3_result.confidence:.0%}] {tier3_result.reasoning}"
                    )
                except Exception as e:
                    # Fall back to Tier 1 if Tier 3 fails
                    return (result.met, f"[Tier 1 {result.confidence:.0%}, Tier 3 failed: {str(e)}] {result.reason}")
            else:
                # Cost guard blocked - return Tier 1 with note
                return (result.met, f"[Tier 1 {result.confidence:.0%}, Cost Guard: {reason}] {result.reason}")

        # Return Tier 1 result
        if result.confidence >= 0.75:
            return (result.met, f"[Tier 1 HIGH CONFIDENCE {result.confidence:.0%}] {result.reason}")
        else:
            return (result.met, f"[Tier 1 {result.confidence:.0%}] {result.reason}")

    except Exception as e:
        return (False, f"Error detecting retest confirmation: {str(e)}")


def check_total3_weakness(params: Dict) -> Tuple[bool, str]:
    """
    Check if Total3 (altcoin market cap) shows weakness.

    Condition: Total3 declining or breaking key support

    Args:
        params: {
            'direction': str ('declining', 'support_break'),
            'support_level': float (optional, in billions)
        }

    Returns:
        (met: bool, reason: str)

    Example:
        check_total3_weakness({'direction': 'declining'})
        → (True, "Total3 declining (signal: BEARISH_FOR_ALTS)")
    """
    try:
        tracker = IndicesTracker()
        indices_data = tracker.fetch_all_indices()

        if not indices_data or 'indices' not in indices_data:
            return (False, "Total3 data unavailable")

        total3_data = indices_data['indices'].get('total3', {})
        total3_value = total3_data.get('value', 0)  # In USD
        total3_signal = total3_data.get('signal', 'INFO')
        total3_billions = total3_value / 1e9  # Convert to billions

        direction = params.get('direction', 'declining')

        if direction == 'declining':
            # Check macro signal for weakness indication
            macro_signal = indices_data.get('macro_signal', 'NEUTRAL')
            if 'BEARISH' in macro_signal:
                return (True, f"Total3 at ${total3_billions:.0f}B (macro signal: {macro_signal})")
            else:
                return (False, f"Total3 at ${total3_billions:.0f}B (macro signal: {macro_signal}, waiting for weakness)")
        elif direction == 'support_break':
            support_level = params.get('support_level')  # In billions
            if support_level and total3_billions < support_level:
                return (True, f"Total3 broke ${support_level:.0f}B support (now ${total3_billions:.0f}B)")
            else:
                return (False, f"Total3 at ${total3_billions:.0f}B (waiting for support break below ${support_level:.0f}B)")
        else:
            return (False, f"Unknown direction: {direction}")

    except Exception as e:
        return (False, f"Error checking Total3: {str(e)}")


def check_volume_confirmation(params: Dict) -> Tuple[bool, str]:
    """
    Check if token has volume confirmation for breakdown.

    Condition: Volume spike on breakdown move

    Args:
        params: {
            'token_symbol': str,
            'exchange': str,
            'volume_threshold': float (optional, multiplier like 1.5x avg)
        }

    Returns:
        (met: bool, reason: str)

    Note: Requires integration with exchange volume data.

    Example:
        check_volume_confirmation({'token_symbol': 'RLS', 'exchange': 'gate'})
        → (False, "Awaiting volume confirmation on breakdown")
    """
    token_symbol = params.get('token_symbol', 'TOKEN')

    # TODO: Integrate with exchange API for real-time volume analysis

    return (False, f"Awaiting volume confirmation for {token_symbol} breakdown")


def check_macro_alignment(params: Dict) -> Tuple[bool, str]:
    """
    Check if macro conditions align (BTC + ETH + USDT.D all bearish).

    Condition: All three macro indicators showing weakness

    Args:
        params: {
            'btc_below': float,
            'usdt_d_above': float
        }

    Returns:
        (met: bool, reason: str)

    Example:
        check_macro_alignment({'btc_below': 88000, 'usdt_d_above': 5.0})
        → (True, "Macro aligned: BTC <$88k, USDT.D >5%")
    """
    try:
        btc_price = _get_btc_price()
        btc_below = params.get('btc_below', 88000)

        tracker = IndicesTracker()
        indices_data = tracker.fetch_all_indices()

        if not indices_data or 'indices' not in indices_data:
            return (False, "Unable to fetch macro data")

        usdt_d_data = indices_data['indices'].get('usdt_d', {})
        usdt_d = usdt_d_data.get('value', 0)
        usdt_d_above = params.get('usdt_d_above', 5.0)

        btc_bearish = btc_price < btc_below
        usdt_d_bullish = usdt_d >= usdt_d_above

        if btc_bearish and usdt_d_bullish:
            return (True, f"Macro aligned: BTC at ${btc_price:,.0f} (< ${btc_below:,.0f}), USDT.D at {usdt_d:.2f}% (≥ {usdt_d_above}%)")
        elif not btc_bearish:
            return (False, f"BTC at ${btc_price:,.0f} (waiting for <${btc_below:,.0f})")
        else:  # not usdt_d_bullish
            return (False, f"USDT.D at {usdt_d:.2f}% (waiting for ≥{usdt_d_above}%)")

    except Exception as e:
        return (False, f"Error checking macro alignment: {str(e)}")


# =====================================================================
# Function Registry
# =====================================================================

CONDITION_CHECK_FUNCTIONS = {
    'check_usdt_dominance': check_usdt_dominance,
    'check_btc_level': check_btc_level,
    'check_eth_level': check_eth_level,
    'check_trendline_break': check_trendline_break,
    'check_rejection_candle': check_rejection_candle,
    'check_retest_confirmation': check_retest_confirmation,
    'check_total3_weakness': check_total3_weakness,
    'check_volume_confirmation': check_volume_confirmation,
    'check_macro_alignment': check_macro_alignment
}


def execute_check_function(function_name: str, parameters: Dict) -> Tuple[bool, str]:
    """
    Execute a condition check function by name.

    Args:
        function_name: Name of the check function (e.g., 'check_usdt_dominance')
        parameters: Parameters dict for the function

    Returns:
        (met: bool, reason: str)

    Raises:
        ValueError: If function_name not found in registry
    """
    if function_name not in CONDITION_CHECK_FUNCTIONS:
        raise ValueError(f"Unknown check function: {function_name}")

    check_function = CONDITION_CHECK_FUNCTIONS[function_name]
    return check_function(parameters)
