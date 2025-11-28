"""
Active Token Queue Manager

Tracks which tokens to monitor for entry timing signals.
Integrates with Supabase to persist monitoring state.

States:
- MONITORING: Active monitoring (checking every 15 min)
- READY: Entry signal detected (sent alert to David)
- ENTERED: David executed trade (stop monitoring)
- CLOSED: TGE passed or David skipped (stop monitoring)

Author: DACLE System
Created: 2025-11-27
"""

import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional
from dataclasses import dataclass
import os
import sys

# Add parent directory to path for imports
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from src.knowledge.supabase_client import SupabaseKnowledgeBase

logger = logging.getLogger(__name__)


@dataclass
class MonitoredToken:
    """Represents a token being monitored for entry timing."""
    id: str
    token_symbol: str
    conviction_score: float
    tge_date: str
    exchange: str
    status: str  # MONITORING / READY / ENTERED / CLOSED
    last_check_timestamp: Optional[str]
    last_alert_sent: Optional[str]
    last_recommendation: Optional[str]  # ENTER NOW / WAIT
    created_at: str


class ActiveTokenQueue:
    """
    Manages the queue of tokens being actively monitored for entry timing.
    """

    def __init__(self):
        """Initialize with Supabase connection."""
        self.kb = SupabaseKnowledgeBase()
        self.table_name = "active_entry_monitors"

    def add_token(
        self,
        token_symbol: str,
        conviction_score: float,
        tge_date: str,
        exchange: str = "MEXC"
    ) -> str:
        """
        Add a token to the monitoring queue.

        Args:
            token_symbol: Token symbol (e.g., "MONAD")
            conviction_score: Conviction score from Agent 2 (e.g., 9.2)
            tge_date: TGE date in ISO format (e.g., "2025-11-24")
            exchange: Primary exchange (MEXC, Hyperliquid, Blofin)

        Returns:
            Token ID (UUID)
        """
        try:
            # Check if token already in queue
            existing = self._get_by_symbol(token_symbol)
            if existing:
                logger.info(f"Token {token_symbol} already in queue (ID: {existing['id']})")
                return existing['id']

            # Insert new token
            data = {
                'token_symbol': token_symbol,
                'conviction_score': conviction_score,
                'tge_date': tge_date,
                'exchange': exchange,
                'status': 'MONITORING',
                'last_check_timestamp': None,
                'last_alert_sent': None,
                'last_recommendation': None,
                'created_at': datetime.utcnow().isoformat()
            }

            result = self.kb.client.table(self.table_name).insert(data).execute()

            if result.data:
                token_id = result.data[0]['id']
                logger.info(f"✅ Added {token_symbol} to monitoring queue (ID: {token_id})")
                return token_id
            else:
                raise Exception("No data returned from insert")

        except Exception as e:
            logger.error(f"Failed to add {token_symbol} to queue: {e}", exc_info=True)
            raise

    def get_active_tokens(
        self,
        status: str = "MONITORING",
        min_conviction: float = 8.0
    ) -> List[MonitoredToken]:
        """
        Get all tokens currently being monitored.

        Args:
            status: Filter by status (default: MONITORING)
            min_conviction: Minimum conviction score (default: 8.0)

        Returns:
            List of MonitoredToken objects
        """
        try:
            query = self.kb.client.table(self.table_name)\
                .select('*')\
                .eq('status', status)\
                .gte('conviction_score', min_conviction)\
                .order('created_at', desc=True)

            result = query.execute()

            if not result.data:
                return []

            tokens = [
                MonitoredToken(
                    id=row['id'],
                    token_symbol=row['token_symbol'],
                    conviction_score=row['conviction_score'],
                    tge_date=row['tge_date'],
                    exchange=row['exchange'],
                    status=row['status'],
                    last_check_timestamp=row.get('last_check_timestamp'),
                    last_alert_sent=row.get('last_alert_sent'),
                    last_recommendation=row.get('last_recommendation'),
                    created_at=row['created_at']
                )
                for row in result.data
            ]

            logger.info(f"📋 Found {len(tokens)} active tokens (status={status}, conviction≥{min_conviction})")
            return tokens

        except Exception as e:
            logger.error(f"Failed to get active tokens: {e}", exc_info=True)
            return []

    def update_check_timestamp(
        self,
        token_id: str,
        recommendation: str,
        timestamp: Optional[str] = None
    ) -> bool:
        """
        Update the last check timestamp for a token.

        Args:
            token_id: Token ID (UUID)
            recommendation: Current recommendation (ENTER NOW / WAIT)
            timestamp: Check timestamp (default: now)

        Returns:
            True if successful
        """
        try:
            timestamp = timestamp or datetime.utcnow().isoformat()

            data = {
                'last_check_timestamp': timestamp,
                'last_recommendation': recommendation
            }

            result = self.kb.client.table(self.table_name)\
                .update(data)\
                .eq('id', token_id)\
                .execute()

            if result.data:
                logger.debug(f"Updated check timestamp for {token_id}: {recommendation}")
                return True
            else:
                logger.warning(f"No rows updated for token {token_id}")
                return False

        except Exception as e:
            logger.error(f"Failed to update check timestamp: {e}", exc_info=True)
            return False

    def update_alert_sent(
        self,
        token_id: str,
        timestamp: Optional[str] = None
    ) -> bool:
        """
        Mark that an alert was sent for this token.

        Args:
            token_id: Token ID (UUID)
            timestamp: Alert timestamp (default: now)

        Returns:
            True if successful
        """
        try:
            timestamp = timestamp or datetime.utcnow().isoformat()

            data = {'last_alert_sent': timestamp}

            result = self.kb.client.table(self.table_name)\
                .update(data)\
                .eq('id', token_id)\
                .execute()

            if result.data:
                logger.info(f"📬 Marked alert sent for {token_id} at {timestamp}")
                return True
            else:
                return False

        except Exception as e:
            logger.error(f"Failed to update alert timestamp: {e}", exc_info=True)
            return False

    def update_status(
        self,
        token_id: str,
        new_status: str
    ) -> bool:
        """
        Update the status of a monitored token.

        Args:
            token_id: Token ID (UUID)
            new_status: New status (MONITORING / READY / ENTERED / CLOSED)

        Returns:
            True if successful
        """
        valid_statuses = ['MONITORING', 'READY', 'ENTERED', 'CLOSED']
        if new_status not in valid_statuses:
            raise ValueError(f"Invalid status: {new_status}. Must be one of {valid_statuses}")

        try:
            data = {'status': new_status}

            result = self.kb.client.table(self.table_name)\
                .update(data)\
                .eq('id', token_id)\
                .execute()

            if result.data:
                logger.info(f"🔄 Updated token {token_id} status: {new_status}")
                return True
            else:
                return False

        except Exception as e:
            logger.error(f"Failed to update status: {e}", exc_info=True)
            return False

    def remove_token(self, token_id: str) -> bool:
        """
        Remove a token from the monitoring queue.

        Args:
            token_id: Token ID (UUID)

        Returns:
            True if successful
        """
        try:
            result = self.kb.client.table(self.table_name)\
                .delete()\
                .eq('id', token_id)\
                .execute()

            if result.data:
                logger.info(f"🗑️  Removed token {token_id} from queue")
                return True
            else:
                return False

        except Exception as e:
            logger.error(f"Failed to remove token: {e}", exc_info=True)
            return False

    def _get_by_symbol(self, token_symbol: str) -> Optional[Dict]:
        """Get token by symbol (for duplicate check)."""
        try:
            result = self.kb.client.table(self.table_name)\
                .select('*')\
                .eq('token_symbol', token_symbol)\
                .eq('status', 'MONITORING')\
                .execute()

            if result.data and len(result.data) > 0:
                return result.data[0]
            return None

        except Exception as e:
            logger.warning(f"Failed to check for existing token: {e}")
            return None

    def should_send_alert(
        self,
        token_id: str,
        current_recommendation: str,
        cooldown_minutes: int = 15
    ) -> bool:
        """
        Check if we should send an alert (avoid spam).

        Rules:
        - If recommendation changed (WAIT → ENTER NOW), send alert
        - If same recommendation, check cooldown period
        - If no previous alert, send

        Args:
            token_id: Token ID (UUID)
            current_recommendation: Current recommendation (ENTER NOW / WAIT)
            cooldown_minutes: Minutes to wait before re-alerting (default: 15)

        Returns:
            True if should send alert
        """
        try:
            # Get current token state
            result = self.kb.client.table(self.table_name)\
                .select('last_alert_sent, last_recommendation')\
                .eq('id', token_id)\
                .execute()

            if not result.data:
                return True  # No state = first check = send alert

            token_data = result.data[0]
            last_alert_sent = token_data.get('last_alert_sent')
            last_recommendation = token_data.get('last_recommendation')

            # Case 1: Recommendation changed
            if last_recommendation != current_recommendation:
                logger.info(f"📢 Recommendation changed: {last_recommendation} → {current_recommendation}")
                return True

            # Case 2: No previous alert
            if not last_alert_sent:
                logger.info(f"📢 First alert for token {token_id}")
                return True

            # Case 3: Check cooldown
            last_alert_time = datetime.fromisoformat(last_alert_sent)
            elapsed = datetime.utcnow() - last_alert_time
            cooldown = timedelta(minutes=cooldown_minutes)

            if elapsed >= cooldown:
                logger.info(f"📢 Cooldown expired ({elapsed.total_seconds()/60:.1f} min)")
                return True
            else:
                remaining = (cooldown.total_seconds() - elapsed.total_seconds()) / 60
                logger.debug(f"🔇 Cooldown active ({remaining:.1f} min remaining)")
                return False

        except Exception as e:
            logger.error(f"Error checking alert cooldown: {e}", exc_info=True)
            return False  # On error, don't spam

    def cleanup_old_tokens(self, days: int = 7) -> int:
        """
        Remove tokens older than N days (TGE passed).

        Args:
            days: Number of days to keep (default: 7)

        Returns:
            Number of tokens removed
        """
        try:
            cutoff_date = (datetime.utcnow() - timedelta(days=days)).isoformat()

            result = self.kb.client.table(self.table_name)\
                .delete()\
                .lt('created_at', cutoff_date)\
                .execute()

            count = len(result.data) if result.data else 0
            if count > 0:
                logger.info(f"🧹 Cleaned up {count} old tokens (>{days} days)")
            return count

        except Exception as e:
            logger.error(f"Failed to cleanup old tokens: {e}", exc_info=True)
            return 0


if __name__ == "__main__":
    # Test queue manager
    logging.basicConfig(level=logging.INFO)

    queue = ActiveTokenQueue()

    # Test: Add token
    print("\n" + "="*60)
    print("TESTING ACTIVE TOKEN QUEUE")
    print("="*60)

    try:
        token_id = queue.add_token(
            token_symbol="TEST_MONAD",
            conviction_score=10.0,
            tge_date="2025-11-24",
            exchange="MEXC"
        )
        print(f"\n✅ Added token: {token_id}")

        # Test: Get active tokens
        tokens = queue.get_active_tokens()
        print(f"\n📋 Active tokens: {len(tokens)}")
        for token in tokens:
            print(f"  - {token.token_symbol}: {token.conviction_score}/10 (status: {token.status})")

        # Test: Update check timestamp
        queue.update_check_timestamp(token_id, "WAIT")
        print(f"\n✅ Updated check timestamp")

        # Test: Check if should send alert
        should_alert = queue.should_send_alert(token_id, "ENTER NOW")
        print(f"\n📢 Should send alert: {should_alert}")

        # Test: Update status
        queue.update_status(token_id, "READY")
        print(f"\n✅ Updated status to READY")

        # Test: Cleanup
        # queue.remove_token(token_id)
        # print(f"\n🗑️  Removed token")

    except Exception as e:
        print(f"\n❌ Test failed: {e}")
