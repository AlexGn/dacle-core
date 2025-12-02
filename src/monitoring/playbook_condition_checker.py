"""
Playbook Condition Checker - Core Evaluation Engine

This module checks all conditions in a playbook, calculates readiness scores,
and determines if alerts should be sent.

Author: DACLE System
Created: 2025-12-02
Session: 82
"""

from datetime import datetime, timezone
from typing import List, Dict, Tuple
import json

from src.models.playbook_models import (
    TokenPlaybook,
    PlaybookCondition,
    ConditionCheckResult,
    ConditionPriority,
    MarketSnapshot
)
from src.monitoring.condition_check_functions import execute_check_function
from src.monitoring.market_regime_classifier import MarketRegimeClassifier
from scripts.helpers.indices_tracker import IndicesTracker
import ccxt


class PlaybookConditionChecker:
    """
    Core engine for checking playbook entry conditions.

    Responsibilities:
    - Check all conditions in a playbook
    - Calculate weighted readiness score
    - Capture market context snapshot
    - Determine if alerts should be sent
    """

    # Priority weights for readiness calculation
    PRIORITY_WEIGHTS = {
        ConditionPriority.CRITICAL: 40.0,  # Critical conditions = 40% weight each
        ConditionPriority.HIGH: 20.0,      # High conditions = 20% weight each
        ConditionPriority.MEDIUM: 10.0     # Medium conditions = 10% weight each
    }

    def __init__(self):
        """Initialize checker with indices tracker and market regime classifier."""
        self.indices_tracker = IndicesTracker()
        self.regime_classifier = MarketRegimeClassifier()

    def check_all_conditions(
        self,
        playbook: TokenPlaybook,
        previous_check: ConditionCheckResult = None
    ) -> ConditionCheckResult:
        """
        Check all conditions in a playbook and calculate readiness score.

        Args:
            playbook: TokenPlaybook instance to check
            previous_check: Previous ConditionCheckResult for comparison (optional)

        Returns:
            ConditionCheckResult with current state
        """
        print(f"\n🔍 Checking conditions for {playbook.token_symbol}...")

        checked_at = datetime.now(timezone.utc)
        condition_states = []
        newly_met = []
        newly_lost = []

        # Check each condition
        for condition in playbook.entry_conditions:
            try:
                # Execute check function
                met, reason = execute_check_function(
                    condition.check_function,
                    condition.parameters
                )

                # Track state changes
                if previous_check:
                    prev_state = self._get_previous_condition_state(
                        previous_check,
                        condition.id
                    )
                    if met and not prev_state:
                        newly_met.append(condition.id)
                    elif not met and prev_state:
                        newly_lost.append(condition.id)

                # Update condition
                condition.met = met
                condition.reason = reason
                condition.last_checked = checked_at

                if met and not condition.last_met_at:
                    condition.last_met_at = checked_at

                # Add to states
                condition_states.append({
                    'condition_id': condition.id,
                    'condition': condition.condition,
                    'met': met,
                    'reason': reason,
                    'priority': condition.priority.value if isinstance(condition.priority, ConditionPriority) else condition.priority,
                    'checked_at': checked_at.isoformat()
                })

                # Log result
                status_icon = "✅" if met else "❌"
                print(f"  {status_icon} [{condition.priority.value}] {condition.condition}")
                print(f"      → {reason}")

            except Exception as e:
                print(f"  ⚠️  Error checking {condition.id}: {str(e)}")
                condition_states.append({
                    'condition_id': condition.id,
                    'condition': condition.condition,
                    'met': False,
                    'reason': f"Check failed: {str(e)}",
                    'priority': condition.priority.value if isinstance(condition.priority, ConditionPriority) else condition.priority,
                    'checked_at': checked_at.isoformat()
                })

        # Calculate readiness score
        readiness_score = self._calculate_readiness(playbook.entry_conditions)
        conditions_met = sum(1 for c in playbook.entry_conditions if c.met)

        # Capture market snapshot
        market_snapshot = self._capture_market_snapshot()

        # Classify market regime (Gemini feedback: critical for regime-aware learning)
        try:
            regime_result = self.regime_classifier.classify()
            market_regime = regime_result['regime']
            print(f"🌍 Market Regime: {market_regime} (Confidence: {regime_result['confidence']:.0%})")
        except Exception as e:
            print(f"⚠️  Market regime classification failed: {e}, defaulting to CHOP")
            market_regime = 'CHOP'

        # Determine if alert should be sent
        should_alert = self._should_send_alert(
            playbook,
            newly_met,
            newly_lost,
            readiness_score,
            previous_check
        )

        print(f"\n📊 Readiness: {readiness_score:.1f}% ({conditions_met}/{playbook.conditions_total} conditions met)")

        return ConditionCheckResult(
            playbook_id=playbook.id,
            checked_at=checked_at,
            conditions_met=conditions_met,
            conditions_total=playbook.conditions_total,
            readiness_score=readiness_score,
            condition_states=condition_states,
            market_snapshot=market_snapshot,
            market_regime=market_regime,
            should_alert=should_alert,
            newly_met_conditions=newly_met,
            newly_lost_conditions=newly_lost
        )

    def _calculate_readiness(self, conditions: List[PlaybookCondition]) -> float:
        """
        Calculate weighted readiness score (0-100).

        Formula:
        - CRITICAL conditions: 40% weight each
        - HIGH conditions: 20% weight each
        - MEDIUM conditions: 10% weight each

        Example:
        - 2 CRITICAL (both met) = 80%
        - 2 HIGH (1 met) = 20%
        - 2 MEDIUM (both met) = 20%
        Total: 120% possible, normalized to 100%

        Args:
            conditions: List of PlaybookCondition instances

        Returns:
            Readiness score (0-100)
        """
        total_weight = 0.0
        met_weight = 0.0

        for condition in conditions:
            priority = condition.priority if isinstance(condition.priority, ConditionPriority) else ConditionPriority(condition.priority)
            weight = self.PRIORITY_WEIGHTS.get(priority, 10.0)

            total_weight += weight
            if condition.met:
                met_weight += weight

        # Normalize to 0-100 scale
        if total_weight == 0:
            return 0.0

        readiness = (met_weight / total_weight) * 100.0
        return min(100.0, readiness)  # Cap at 100%

    def _capture_market_snapshot(self) -> Dict:
        """
        Capture current market context (BTC, ETH, USDT.D).

        Returns:
            Dictionary with market data
        """
        try:
            # Get prices from exchange
            exchange = ccxt.binance({'enableRateLimit': True})
            btc_ticker = exchange.fetch_ticker('BTC/USDT')
            eth_ticker = exchange.fetch_ticker('ETH/USDT')

            # Get USDT.D from indices
            indices_data = self.indices_tracker.fetch_all_indices()
            usdt_d = 0
            if indices_data and 'indices' in indices_data:
                usdt_d_data = indices_data['indices'].get('usdt_d', {})
                usdt_d = usdt_d_data.get('value', 0)

            return {
                'timestamp': datetime.now(timezone.utc).isoformat(),
                'btc': {
                    'price': btc_ticker['last'],
                    'rsi_14d': 0,  # Placeholder
                    'trend': 'UNKNOWN'
                },
                'eth': {
                    'price': eth_ticker['last'],
                    'rsi_14d': 0,  # Placeholder
                    'trend': 'UNKNOWN'
                },
                'usdt_dominance': {
                    'value': usdt_d,
                    'trend': 'NEUTRAL'  # TODO: Add trend calculation
                }
            }
        except Exception as e:
            print(f"⚠️  Error capturing market snapshot: {str(e)}")
            return {
                'timestamp': datetime.now(timezone.utc).isoformat(),
                'error': str(e)
            }

    def _should_send_alert(
        self,
        playbook: TokenPlaybook,
        newly_met: List[str],
        newly_lost: List[str],
        readiness_score: float,
        previous_check: ConditionCheckResult = None
    ) -> bool:
        """
        Determine if an alert should be sent.

        Alert triggers:
        1. New condition met (False → True)
        2. Readiness crosses 80% threshold (upward)
        3. All CRITICAL conditions met
        4. Critical condition lost (with 1-hour cooldown to prevent spam)

        Args:
            playbook: TokenPlaybook being checked
            newly_met: List of newly met condition IDs
            newly_lost: List of newly lost condition IDs
            readiness_score: Current readiness score
            previous_check: Previous check result for comparison

        Returns:
            True if alert should be sent
        """
        # Trigger 1: New condition met
        if newly_met:
            print(f"  🚨 Alert trigger: {len(newly_met)} new condition(s) met")
            return True

        # Trigger 2: Readiness crossed 80% threshold
        if previous_check:
            prev_readiness = previous_check.readiness_score
            if prev_readiness < 80.0 <= readiness_score:
                print(f"  🚨 Alert trigger: Readiness crossed 80% ({prev_readiness:.1f}% → {readiness_score:.1f}%)")
                return True

        # Trigger 3: All CRITICAL conditions met
        critical_conditions = [
            c for c in playbook.entry_conditions
            if c.priority == ConditionPriority.CRITICAL
        ]
        if critical_conditions and all(c.met for c in critical_conditions):
            # Check if this is a new state
            if previous_check:
                prev_all_critical_met = self._were_all_critical_met(
                    previous_check,
                    [c.id for c in critical_conditions]
                )
                if not prev_all_critical_met:
                    print(f"  🚨 Alert trigger: All CRITICAL conditions now met")
                    return True
            else:
                print(f"  🚨 Alert trigger: All CRITICAL conditions met")
                return True

        # Trigger 4: Critical condition lost (with cooldown check)
        if newly_lost:
            lost_critical = [
                cid for cid in newly_lost
                if any(c.id == cid and c.priority == ConditionPriority.CRITICAL
                       for c in playbook.entry_conditions)
            ]
            if lost_critical:
                # TODO: Implement 1-hour cooldown check via database
                print(f"  ⚠️  Critical condition lost: {lost_critical}")
                return True

        return False

    def _get_previous_condition_state(
        self,
        previous_check: ConditionCheckResult,
        condition_id: str
    ) -> bool:
        """
        Get the 'met' state of a condition from previous check.

        Args:
            previous_check: Previous ConditionCheckResult
            condition_id: ID of condition to look up

        Returns:
            True if condition was met in previous check, False otherwise
        """
        for state in previous_check.condition_states:
            if state.get('condition_id') == condition_id:
                return state.get('met', False)
        return False

    def _were_all_critical_met(
        self,
        previous_check: ConditionCheckResult,
        critical_ids: List[str]
    ) -> bool:
        """
        Check if all CRITICAL conditions were met in previous check.

        Args:
            previous_check: Previous ConditionCheckResult
            critical_ids: List of CRITICAL condition IDs

        Returns:
            True if all were met in previous check
        """
        for cid in critical_ids:
            if not self._get_previous_condition_state(previous_check, cid):
                return False
        return True
