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
import ccxt


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
        usdt_d = tracker.get_usdt_dominance()

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
    Check if token broke a trendline (requires manual verification).

    Condition: Price breaks below descending trendline or above ascending trendline

    Args:
        params: {
            'direction': str ('upside' or 'downside'),
            'token_symbol': str
        }

    Returns:
        (met: bool, reason: str)

    Note: This function requires manual chart analysis. In automated mode,
    it returns False until manually updated via database.

    Example:
        check_trendline_break({'direction': 'downside', 'token_symbol': 'RLS'})
        → (False, "Awaiting manual confirmation of downside trendline break")
    """
    direction = params.get('direction', 'downside')
    token_symbol = params.get('token_symbol', 'TOKEN')

    # TODO: Integrate with chart pattern detection system (future enhancement)
    # For now, this requires manual verification and database update

    return (False, f"Awaiting manual confirmation of {direction} trendline break for {token_symbol}")


def check_rejection_candle(params: Dict) -> Tuple[bool, str]:
    """
    Check if token printed a rejection candle (requires manual verification).

    Condition: Strong rejection wick/candle at resistance

    Args:
        params: {
            'token_symbol': str,
            'level': float (optional)
        }

    Returns:
        (met: bool, reason: str)

    Note: Requires manual chart analysis or integration with candlestick pattern detection.

    Example:
        check_rejection_candle({'token_symbol': 'RLS', 'level': 0.015})
        → (False, "Awaiting rejection candle confirmation at $0.015")
    """
    token_symbol = params.get('token_symbol', 'TOKEN')
    level = params.get('level')

    # TODO: Integrate with candlestick pattern detection system (future enhancement)

    if level:
        return (False, f"Awaiting rejection candle confirmation for {token_symbol} at ${level}")
    else:
        return (False, f"Awaiting rejection candle confirmation for {token_symbol}")


def check_retest_confirmation(params: Dict) -> Tuple[bool, str]:
    """
    Check if token confirmed a retest (requires manual verification).

    Condition: Price retests broken level and shows rejection

    Args:
        params: {
            'token_symbol': str,
            'level': float (optional)
        }

    Returns:
        (met: bool, reason: str)

    Note: Requires manual chart analysis.

    Example:
        check_retest_confirmation({'token_symbol': 'RLS', 'level': 0.015})
        → (False, "Awaiting retest confirmation at $0.015")
    """
    token_symbol = params.get('token_symbol', 'TOKEN')
    level = params.get('level')

    # TODO: Integrate with price action detection system (future enhancement)

    if level:
        return (False, f"Awaiting retest confirmation for {token_symbol} at ${level}")
    else:
        return (False, f"Awaiting retest confirmation for {token_symbol}")


def check_total3_weakness(params: Dict) -> Tuple[bool, str]:
    """
    Check if Total3 (altcoin market cap) shows weakness.

    Condition: Total3 declining or breaking key support

    Args:
        params: {
            'direction': str ('declining', 'support_break'),
            'support_level': float (optional)
        }

    Returns:
        (met: bool, reason: str)

    Example:
        check_total3_weakness({'direction': 'declining'})
        → (True, "Total3 declining -2.3% in last 24h")
    """
    try:
        tracker = IndicesTracker()
        total3_data = tracker.get_index_data('TOTAL3')

        if not total3_data:
            return (False, "Total3 data unavailable")

        direction = params.get('direction', 'declining')
        current_price = total3_data.get('current_price', 0)
        change_24h = total3_data.get('change_24h', 0)

        if direction == 'declining':
            if change_24h < -1.0:  # More than 1% decline
                return (True, f"Total3 declining {change_24h:.1f}% in last 24h")
            else:
                return (False, f"Total3 at {change_24h:.1f}% (waiting for decline)")
        elif direction == 'support_break':
            support_level = params.get('support_level')
            if support_level and current_price < support_level:
                return (True, f"Total3 broke ${support_level:,.0f}B support (now ${current_price:,.0f}B)")
            else:
                return (False, f"Total3 at ${current_price:,.0f}B (waiting for support break)")
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
        btc_context = get_btc_context()
        btc_price = btc_context.get('current_price', 0)
        btc_below = params.get('btc_below', 88000)

        tracker = IndicesTracker()
        usdt_d = tracker.get_usdt_dominance()
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
