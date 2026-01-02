#!/usr/bin/env python3
"""
MEXC Trade Sync - Session 276 P0.2

Automated trade outcome sync from MEXC exchange to close the learning loop.
Replaces manual feedback form entry with automated position tracking.

Flow:
1. Fetch closed positions from MEXC Futures API
2. Match to DACLE tokens (by symbol)
3. Calculate P&L metrics
4. Update trade_log.json automatically
5. Trigger forward_validation sync

Requirements:
- MEXC_API_KEY and MEXC_API_SECRET in .env
- ccxt library (already installed)

Usage:
    from src.data.mexc_trade_sync import MEXCTradeSync

    sync = MEXCTradeSync()
    results = sync.sync_recent_trades(days=7)
    print(f"Synced {len(results['new_trades'])} new trades")

CLI:
    python -m src.data.mexc_trade_sync              # Sync last 7 days
    python -m src.data.mexc_trade_sync --days 30    # Sync last 30 days
    python -m src.data.mexc_trade_sync --token POWER  # Sync specific token

Author: Claude Code (Session 276)
Date: 2026-01-02
"""

import json
import logging
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
import hashlib

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).parent.parent.parent
TRADE_LOG_PATH = PROJECT_ROOT / "data" / "trades" / "trade_log.json"
SYNC_STATE_PATH = PROJECT_ROOT / "data" / "trades" / "mexc_sync_state.json"
DATA_TOKENS_DIR = PROJECT_ROOT / "data" / "tokens"

# Tokens we track in DACLE (will match against these)
TRACKED_TOKENS: Optional[List[str]] = None  # Loaded dynamically


def _get_tracked_tokens() -> List[str]:
    """Get list of tokens DACLE is tracking."""
    global TRACKED_TOKENS
    if TRACKED_TOKENS is not None:
        return TRACKED_TOKENS

    tokens = []
    if DATA_TOKENS_DIR.exists():
        for token_dir in DATA_TOKENS_DIR.iterdir():
            if token_dir.is_dir() and not token_dir.name.startswith('.'):
                tokens.append(token_dir.name.upper())

    TRACKED_TOKENS = tokens
    return tokens


def _load_trade_log() -> Dict[str, Any]:
    """Load existing trade log."""
    if TRADE_LOG_PATH.exists():
        try:
            with open(TRADE_LOG_PATH) as f:
                return json.load(f)
        except json.JSONDecodeError:
            logger.warning("Corrupted trade log, starting fresh")

    return {
        "version": "1.0",
        "last_updated": datetime.now(timezone.utc).isoformat(),
        "trades": []
    }


def _save_trade_log(data: Dict[str, Any]) -> None:
    """Save trade log to disk."""
    TRADE_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    data["last_updated"] = datetime.now(timezone.utc).isoformat()
    with open(TRADE_LOG_PATH, "w") as f:
        json.dump(data, f, indent=2)


def _load_sync_state() -> Dict[str, Any]:
    """Load sync state (last sync timestamp, synced trade IDs)."""
    if SYNC_STATE_PATH.exists():
        try:
            with open(SYNC_STATE_PATH) as f:
                return json.load(f)
        except json.JSONDecodeError:
            pass

    return {
        "last_sync": None,
        "synced_trade_ids": [],
        "created_at": datetime.now(timezone.utc).isoformat()
    }


def _save_sync_state(state: Dict[str, Any]) -> None:
    """Save sync state."""
    SYNC_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    state["last_sync"] = datetime.now(timezone.utc).isoformat()
    with open(SYNC_STATE_PATH, "w") as f:
        json.dump(state, f, indent=2)


def _generate_trade_id(trade: Dict[str, Any]) -> str:
    """Generate unique trade ID from MEXC trade data."""
    # Use order ID + symbol + timestamp for uniqueness
    key = f"{trade.get('id', '')}-{trade.get('symbol', '')}-{trade.get('timestamp', '')}"
    hash_suffix = hashlib.md5(key.encode()).hexdigest()[:6]
    token = trade.get('symbol', 'UNKNOWN').replace('/USDT', '').replace(':USDT', '')
    ts = datetime.now().strftime('%Y%m%d_%H%M%S')
    return f"TRADE_{token}_{ts}_{hash_suffix}"


class MEXCTradeSync:
    """
    Syncs closed trades from MEXC to DACLE's trade log.

    Automatically:
    1. Fetches closed positions from MEXC Futures
    2. Filters for DACLE-tracked tokens
    3. Calculates P&L and trade metrics
    4. Creates trade entries (without feedback - auto-filled later)
    5. Triggers forward_validation sync
    """

    def __init__(self):
        """Initialize MEXC connection."""
        self.exchange = None
        self._init_exchange()

    def _init_exchange(self) -> None:
        """Initialize ccxt MEXC exchange."""
        try:
            import ccxt

            api_key = os.getenv("MEXC_API_KEY")
            api_secret = os.getenv("MEXC_API_SECRET")

            if not api_key or not api_secret:
                logger.warning("MEXC API credentials not configured")
                return

            self.exchange = ccxt.mexc({
                'apiKey': api_key,
                'secret': api_secret,
                'enableRateLimit': True,
                'options': {
                    'defaultType': 'swap',  # Futures trading
                }
            })

            logger.info("MEXC exchange initialized successfully")

        except ImportError:
            logger.error("ccxt not installed")
        except Exception as e:
            logger.error(f"Failed to initialize MEXC: {e}")

    def is_available(self) -> bool:
        """Check if MEXC sync is available."""
        return self.exchange is not None

    def fetch_closed_positions(
        self,
        since: Optional[datetime] = None,
        symbol: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """
        Fetch closed positions from MEXC.

        Args:
            since: Start date for fetching trades
            symbol: Optional specific symbol (e.g., 'POWER/USDT:USDT')

        Returns:
            List of closed position dictionaries
        """
        if not self.exchange:
            logger.warning("MEXC not initialized")
            return []

        try:
            # Fetch closed orders (completed trades)
            since_ts = int(since.timestamp() * 1000) if since else None

            params = {}
            if symbol:
                params['symbol'] = symbol

            # Fetch my trades (actual filled orders)
            trades = self.exchange.fetch_my_trades(
                symbol=symbol,
                since=since_ts,
                limit=500,  # Max per request
                params=params
            )

            logger.info(f"Fetched {len(trades)} trades from MEXC")
            return trades

        except Exception as e:
            logger.error(f"Failed to fetch MEXC trades: {e}")
            return []

    def fetch_position_history(
        self,
        days: int = 7
    ) -> List[Dict[str, Any]]:
        """
        Fetch position history (closed positions with P&L).

        Args:
            days: Number of days to look back

        Returns:
            List of closed positions with P&L data
        """
        if not self.exchange:
            return []

        try:
            since = datetime.now(timezone.utc) - timedelta(days=days)
            since_ts = int(since.timestamp() * 1000)

            # MEXC Futures: Fetch closed positions
            # This endpoint returns positions that have been closed
            positions = self.exchange.fetch_positions()

            # Also fetch recent orders to get completed trades
            orders = self.exchange.fetch_closed_orders(
                since=since_ts,
                limit=500
            )

            # Combine and deduplicate
            all_trades = []
            seen_ids = set()

            for order in orders:
                order_id = order.get('id')
                if order_id and order_id not in seen_ids:
                    seen_ids.add(order_id)
                    all_trades.append(order)

            logger.info(f"Fetched {len(all_trades)} closed orders from MEXC (last {days} days)")
            return all_trades

        except Exception as e:
            logger.error(f"Failed to fetch MEXC position history: {e}")
            return []

    def sync_recent_trades(
        self,
        days: int = 7,
        token_filter: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Sync recent trades from MEXC to trade_log.json.

        Args:
            days: Number of days to look back
            token_filter: Optional specific token to sync

        Returns:
            Dict with sync results
        """
        results = {
            "new_trades": [],
            "skipped_existing": [],
            "skipped_untracked": [],
            "errors": [],
            "sync_timestamp": datetime.now(timezone.utc).isoformat()
        }

        if not self.exchange:
            results["errors"].append("MEXC not initialized - check API credentials")
            return results

        # Load current state
        trade_log = _load_trade_log()
        sync_state = _load_sync_state()
        synced_ids = set(sync_state.get("synced_trade_ids", []))
        tracked_tokens = _get_tracked_tokens()

        # Fetch trades from MEXC
        mexc_trades = self.fetch_position_history(days=days)

        for mexc_trade in mexc_trades:
            try:
                # Extract token symbol
                symbol = mexc_trade.get('symbol', '')
                # MEXC format: 'POWER/USDT:USDT' or 'POWERUSDT'
                token = symbol.replace('/USDT', '').replace(':USDT', '').replace('USDT', '').upper()

                # Apply token filter if specified
                if token_filter and token != token_filter.upper():
                    continue

                # Skip if not tracked by DACLE
                if token not in tracked_tokens:
                    results["skipped_untracked"].append({
                        "token": token,
                        "symbol": symbol
                    })
                    continue

                # Generate trade ID
                trade_id = _generate_trade_id(mexc_trade)

                # Check if already synced (by MEXC order ID)
                mexc_order_id = str(mexc_trade.get('id', ''))
                if mexc_order_id in synced_ids:
                    results["skipped_existing"].append({
                        "token": token,
                        "order_id": mexc_order_id
                    })
                    continue

                # Parse trade data
                dacle_trade = self._convert_to_dacle_format(mexc_trade, trade_id, token)

                if dacle_trade:
                    # Add to trade log
                    trade_log["trades"].append(dacle_trade)
                    synced_ids.add(mexc_order_id)
                    results["new_trades"].append({
                        "trade_id": trade_id,
                        "token": token,
                        "result": dacle_trade.get("result"),
                        "pnl_percent": dacle_trade.get("metrics", {}).get("pnl_percent")
                    })

                    logger.info(f"Synced trade: {trade_id} ({token}) - {dacle_trade.get('result')}")

            except Exception as e:
                results["errors"].append({
                    "trade": str(mexc_trade.get('id', 'unknown')),
                    "error": str(e)
                })

        # Save updated data
        if results["new_trades"]:
            _save_trade_log(trade_log)
            sync_state["synced_trade_ids"] = list(synced_ids)
            _save_sync_state(sync_state)

            # Trigger forward validation sync
            self._trigger_forward_validation_sync()

        return results

    def _convert_to_dacle_format(
        self,
        mexc_trade: Dict[str, Any],
        trade_id: str,
        token: str
    ) -> Optional[Dict[str, Any]]:
        """
        Convert MEXC trade to DACLE trade_log format.

        Args:
            mexc_trade: Raw MEXC trade data
            trade_id: Generated trade ID
            token: Token symbol

        Returns:
            DACLE-formatted trade dict or None
        """
        try:
            # Determine trade type from side
            side = mexc_trade.get('side', '').upper()
            trade_type = "SHORT" if side == "SELL" else "LONG"

            # Parse timestamps
            entry_ts = mexc_trade.get('timestamp', 0)
            entry_date = datetime.fromtimestamp(entry_ts / 1000, tz=timezone.utc)

            # Get price data
            entry_price = float(mexc_trade.get('price', 0))
            amount = float(mexc_trade.get('amount', 0))
            cost = float(mexc_trade.get('cost', 0))

            # Calculate P&L (if available in MEXC data)
            pnl = mexc_trade.get('info', {}).get('realizedPnl', 0)
            pnl_usd = float(pnl) if pnl else 0
            pnl_percent = (pnl_usd / cost * 100) if cost > 0 else 0

            # Determine result
            if abs(pnl_percent) < 1.0:  # Less than 1% = BREAKEVEN
                result = "BREAKEVEN"
            elif pnl_usd > 0:
                result = "WIN"
            else:
                result = "LOSS"

            # Load conviction score if available
            conviction_score = self._get_conviction_for_token(token, entry_date)

            return {
                "trade_id": trade_id,
                "token": token,
                "trade_type": trade_type,
                "result": result,
                "submitted_at": datetime.now(timezone.utc).isoformat(),
                "entry": {
                    "price": entry_price,
                    "date": entry_date.isoformat(),
                    "conviction_score": conviction_score
                },
                "exit": {
                    "price": None,  # Need to fetch separately for position close
                    "date": None,
                    "stop_loss_hit": None,
                    "take_profit_hit": None
                },
                "position": {
                    "size_usd": cost,
                    "leverage": mexc_trade.get('info', {}).get('leverage', 1)
                },
                "metrics": {
                    "pnl_percent": round(pnl_percent, 2),
                    "pnl_usd": round(pnl_usd, 2),
                    "mae_percent": None,
                    "mfe_percent": None
                },
                "feedback": {
                    # Auto-sync trades have no manual feedback
                    "auto_synced": True,
                    "sync_source": "MEXC",
                    "what_went_well": None,
                    "what_went_wrong": None,
                    "lessons_learned": None,
                    "market_conditions": None
                },
                "screenshots": [],
                "pattern_validation": {
                    "matched_pattern": None,
                    "pattern_accuracy": None
                },
                "mexc_data": {
                    "order_id": mexc_trade.get('id'),
                    "symbol": mexc_trade.get('symbol'),
                    "raw": mexc_trade.get('info', {})
                }
            }

        except Exception as e:
            logger.error(f"Failed to convert MEXC trade: {e}")
            return None

    def _get_conviction_for_token(
        self,
        token: str,
        entry_date: datetime
    ) -> Optional[float]:
        """Get DACLE conviction score for token at entry time."""
        try:
            # Check consolidated.json for the token
            consolidated_path = DATA_TOKENS_DIR / token / "consolidated.json"
            if not consolidated_path.exists():
                return None

            with open(consolidated_path) as f:
                data = json.load(f)

            # Get final conviction score
            return data.get("conviction", {}).get("final_score")

        except Exception as e:
            logger.debug(f"Could not get conviction for {token}: {e}")
            return None

    def _trigger_forward_validation_sync(self) -> None:
        """Trigger forward validation sync after new trades added."""
        try:
            from src.conviction.forward_validation import sync_trade_outcomes

            result = sync_trade_outcomes()
            matched = len(result.get("matched", []))
            logger.info(f"Forward validation sync complete: {matched} trades matched")

        except Exception as e:
            logger.warning(f"Forward validation sync failed: {e}")


def sync_mexc_trades(days: int = 7, token: Optional[str] = None) -> Dict[str, Any]:
    """Convenience function for syncing trades."""
    sync = MEXCTradeSync()
    return sync.sync_recent_trades(days=days, token_filter=token)


if __name__ == "__main__":
    import argparse

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(levelname)s - %(message)s"
    )

    parser = argparse.ArgumentParser(description="Sync trades from MEXC")
    parser.add_argument("--days", type=int, default=7, help="Days to look back")
    parser.add_argument("--token", type=str, help="Specific token to sync")
    parser.add_argument("--check", action="store_true", help="Check connection only")
    args = parser.parse_args()

    # Load environment
    from dotenv import load_dotenv
    load_dotenv(PROJECT_ROOT / ".env")

    sync = MEXCTradeSync()

    if args.check:
        print(f"MEXC Available: {sync.is_available()}")
        if sync.is_available():
            print("✅ MEXC connection successful")
        else:
            print("❌ MEXC not configured - add MEXC_API_KEY and MEXC_API_SECRET to .env")
    else:
        print(f"\n=== Syncing MEXC Trades (last {args.days} days) ===\n")

        results = sync.sync_recent_trades(days=args.days, token_filter=args.token)

        print(f"New trades synced: {len(results['new_trades'])}")
        for trade in results['new_trades']:
            print(f"  • {trade['token']}: {trade['result']} ({trade['pnl_percent']:+.2f}%)")

        print(f"\nSkipped (already synced): {len(results['skipped_existing'])}")
        print(f"Skipped (untracked): {len(results['skipped_untracked'])}")

        if results['errors']:
            print(f"\nErrors: {len(results['errors'])}")
            for error in results['errors']:
                print(f"  • {error}")

        print(f"\nSync timestamp: {results['sync_timestamp']}")
