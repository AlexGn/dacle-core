#!/usr/bin/env python3
"""
Macro S/R Signal Checker

Queries Supabase for active macro signals from TradingView S/R events.
Used by Entry Timer Bot to filter alerts (only send when macro favorable).

David's Rule:
"Only alert when BOTH conditions are met:"
1. Entry readiness score ≥ 7.0
2. Macro S/R signal = BULLISH_FOR_SHORTS

Author: DACLE System
Created: 2025-11-27
"""

import logging
from datetime import datetime, timedelta
from typing import Optional, Dict, List
import sys
import os

sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from src.knowledge.supabase_client import get_supabase_client

logger = logging.getLogger(__name__)


class MacroSRChecker:
    """
    Checks for active macro S/R signals from TradingView webhooks.

    Usage:
        checker = MacroSRChecker()
        signal = checker.get_active_signal()

        if signal and signal['type'] == 'BULLISH_FOR_SHORTS':
            # Macro is favorable for shorts
            send_alert()
        else:
            # Wait for better macro conditions
            wait()
    """

    def __init__(self, signal_expiry_hours: int = 4):
        """
        Initialize macro S/R checker.

        Args:
            signal_expiry_hours: How many hours before signals expire (default: 4)
        """
        self.signal_expiry_hours = signal_expiry_hours
        self.supabase = get_supabase_client()

    def get_active_signal(self) -> Optional[Dict]:
        """
        Get the most recent active macro signal.

        Returns:
            Dict with signal data if active signal exists, else None
            {
                'id': 123,
                'signal_type': 'BULLISH_FOR_SHORTS' or 'BEARISH_FOR_SHORTS',
                'confidence': 0.85,
                'indices_aligned': ['USDT.D', 'BTC.D'],
                'reasoning': 'USDT.D rejected at support and pumping',
                'timestamp': '2025-11-27T10:30:00',
                'expires_at': '2025-11-27T14:30:00'
            }
        """
        try:
            # Query for non-expired signals, most recent first
            now = datetime.utcnow().isoformat()

            response = self.supabase.table('macro_signals').select('*').gte(
                'expires_at', now
            ).order('timestamp', desc=True).limit(1).execute()

            if response.data and len(response.data) > 0:
                signal = response.data[0]
                logger.info(f"✅ Active macro signal: {signal['signal_type']} @ {signal['confidence']}")
                return signal
            else:
                logger.info("⚠️ No active macro signals (all expired or none exist)")
                return None

        except Exception as e:
            logger.error(f"❌ Error fetching macro signal: {e}")
            return None

    def is_favorable_for_shorts(self) -> bool:
        """
        Check if current macro conditions are favorable for TGE shorts.

        Returns:
            bool: True if BULLISH_FOR_SHORTS signal is active, False otherwise
        """
        signal = self.get_active_signal()

        if not signal:
            logger.info("❌ No active signal → NOT favorable for shorts")
            return False

        is_favorable = signal['signal_type'] == 'BULLISH_FOR_SHORTS'

        if is_favorable:
            logger.info(f"✅ Macro FAVORABLE for shorts: {signal['reasoning']}")
        else:
            logger.info(f"❌ Macro NOT favorable for shorts: {signal['signal_type']} - {signal['reasoning']}")

        return is_favorable

    def get_signal_summary(self) -> Dict:
        """
        Get a summary of current macro conditions for logging/display.

        Returns:
            Dict with summary data:
            {
                'has_active_signal': bool,
                'is_favorable': bool,
                'signal_type': str or None,
                'reasoning': str or None,
                'confidence': float or None,
                'indices_aligned': list or None,
                'time_until_expiry_minutes': int or None
            }
        """
        signal = self.get_active_signal()

        if not signal:
            return {
                'has_active_signal': False,
                'is_favorable': False,
                'signal_type': None,
                'reasoning': None,
                'confidence': None,
                'indices_aligned': None,
                'time_until_expiry_minutes': None
            }

        # Calculate time until expiry
        try:
            expires_at = datetime.fromisoformat(signal['expires_at'].replace('Z', '+00:00'))
            now = datetime.utcnow()
            time_until_expiry = (expires_at - now).total_seconds() / 60
        except:
            time_until_expiry = None

        return {
            'has_active_signal': True,
            'is_favorable': signal['signal_type'] == 'BULLISH_FOR_SHORTS',
            'signal_type': signal['signal_type'],
            'reasoning': signal['reasoning'],
            'confidence': signal['confidence'],
            'indices_aligned': signal['indices_aligned'],
            'time_until_expiry_minutes': int(time_until_expiry) if time_until_expiry else None
        }

    def get_recent_events(self, limit: int = 10) -> List[Dict]:
        """
        Get recent S/R events from TradingView webhooks.

        Useful for debugging/logging recent macro activity.

        Args:
            limit: Max number of events to return (default: 10)

        Returns:
            List of recent S/R events
        """
        try:
            response = self.supabase.table('indices_sr_events').select('*').order(
                'received_at', desc=True
            ).limit(limit).execute()

            return response.data if response.data else []

        except Exception as e:
            logger.error(f"❌ Error fetching recent events: {e}")
            return []


def main():
    """
    CLI test for macro S/R checker.

    Usage:
        python scripts/helpers/macro_sr_checker.py
    """
    logging.basicConfig(level=logging.INFO, format='%(message)s')

    print("\n" + "="*70)
    print("MACRO S/R CHECKER - TEST")
    print("="*70)

    checker = MacroSRChecker()

    # Test 1: Get active signal
    print("\n📊 Test 1: Check for active signal")
    print("-"*70)
    signal = checker.get_active_signal()

    if signal:
        print(f"✅ Active Signal Found:")
        print(f"   Type: {signal['signal_type']}")
        print(f"   Confidence: {signal['confidence']}")
        print(f"   Reasoning: {signal['reasoning']}")
        print(f"   Indices: {', '.join(signal['indices_aligned'])}")
    else:
        print("⚠️ No active signal")

    # Test 2: Check if favorable for shorts
    print("\n🎯 Test 2: Is macro favorable for shorts?")
    print("-"*70)
    is_favorable = checker.is_favorable_for_shorts()
    print(f"   Result: {'✅ YES' if is_favorable else '❌ NO'}")

    # Test 3: Get signal summary
    print("\n📋 Test 3: Get signal summary")
    print("-"*70)
    summary = checker.get_signal_summary()
    print(f"   Has Active Signal: {summary['has_active_signal']}")
    print(f"   Is Favorable: {summary['is_favorable']}")
    print(f"   Signal Type: {summary['signal_type']}")
    print(f"   Time Until Expiry: {summary['time_until_expiry_minutes']} minutes")

    # Test 4: Recent events
    print("\n📜 Test 4: Recent S/R events (last 5)")
    print("-"*70)
    events = checker.get_recent_events(limit=5)

    if events:
        for i, event in enumerate(events, 1):
            print(f"   {i}. {event['index_name']} {event['action']} {event['level_type']} at {event['price']} - Signal: {event.get('signal_type', 'none')}")
    else:
        print("   No recent events")

    print("\n" + "="*70)
    print("✅ TEST COMPLETE")
    print("="*70)


if __name__ == "__main__":
    main()
