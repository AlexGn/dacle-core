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

# Load environment variables
from dotenv import load_dotenv
load_dotenv()

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
    from src.utils.atomic_write import atomic_json_write
    data["last_updated"] = datetime.now(timezone.utc).isoformat()
    atomic_json_write(TRADE_LOG_PATH, data)


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

            # MEXC Futures: Fetch all positions (open + recently closed)
            # This returns a list of positions without requiring a symbol
            positions = self.exchange.fetch_positions()

            logger.info(f"Fetched {len(positions)} positions from MEXC")

            # For each position with trades, fetch the trade history
            all_trades = []
            seen_symbols = set()

            # First, collect unique symbols from positions
            for pos in positions:
                symbol = pos.get('symbol')
                if symbol and symbol not in seen_symbols:
                    seen_symbols.add(symbol)

                    try:
                        # Fetch trades for this specific symbol
                        symbol_trades = self.exchange.fetch_my_trades(
                            symbol=symbol,
                            since=since_ts,
                            limit=500
                        )

                        if symbol_trades:
                            all_trades.extend(symbol_trades)
                            logger.info(f"  {symbol}: {len(symbol_trades)} trades")

                    except Exception as e:
                        # Some symbols might not have trades in the time window
                        logger.debug(f"  {symbol}: {e}")
                        continue

            # ALSO check DACLE-tracked tokens (to catch fully closed positions)
            # These won't appear in fetch_positions() if contracts = 0
            import os
            import glob
            tokens_dir = os.path.join(os.path.dirname(__file__), '..', '..', 'data', 'tokens')
            if os.path.exists(tokens_dir):
                dacle_tokens = [
                    os.path.basename(d)
                    for d in glob.glob(os.path.join(tokens_dir, '*'))
                    if os.path.isdir(d)
                ]

                logger.info(f"Checking {len(dacle_tokens)} DACLE-tracked tokens for closed positions")

                for token in dacle_tokens:
                    # Convert token to MEXC symbol format (e.g., MANA -> MANA/USDT:USDT)
                    symbol = f"{token}/USDT:USDT"

                    if symbol not in seen_symbols:
                        try:
                            symbol_trades = self.exchange.fetch_my_trades(
                                symbol=symbol,
                                since=since_ts,
                                limit=500
                            )

                            if symbol_trades:
                                all_trades.extend(symbol_trades)
                                seen_symbols.add(symbol)
                                logger.info(f"  {symbol}: {len(symbol_trades)} trades (DACLE token)")

                        except Exception as e:
                            # Expected for tokens not traded on MEXC or without trades
                            logger.debug(f"  {symbol}: {e}")
                            continue

            logger.info(f"Fetched {len(all_trades)} total trades from {len(seen_symbols)} symbols (last {days} days)")
            return all_trades

        except Exception as e:
            logger.error(f"Failed to fetch MEXC position history: {e}")
            return []

    def sync_recent_trades(
        self,
        days: int = 7,
        token_filter: Optional[str] = None,
        include_untracked: bool = True
    ) -> Dict[str, Any]:
        """
        Sync recent trades from MEXC to trade_log.json.

        Args:
            days: Number of days to look back
            token_filter: Optional specific token to sync
            include_untracked: If True, sync ALL trades including non-DACLE tokens (default: True)
                              If False, only sync tokens with data/tokens/<TOKEN> directories

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

                # Track if token is in DACLE's tracked list
                is_tracked = token in tracked_tokens

                # Skip if not tracked and include_untracked is False
                if not is_tracked and not include_untracked:
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
                    # Skip PENDING orders (entry orders without P&L)
                    if dacle_trade.get("result") == "PENDING":
                        results["skipped_existing"].append({
                            "token": token,
                            "order_id": mexc_order_id,
                            "reason": "entry_order_no_pnl"
                        })
                        continue

                    # Mark if token is tracked by DACLE (has conviction analysis)
                    dacle_trade["is_dacle_tracked"] = is_tracked

                    # Session 315 L089: Detect partial exits for TP scaling tracking
                    partial_exits = self._detect_partial_exits(mexc_trades, token)
                    if partial_exits:
                        dacle_trade["exit"]["partial_exits"] = partial_exits
                        # Calculate remaining position after partial exits
                        total_exited = sum(pe.get("percentage", 0) for pe in partial_exits)
                        dacle_trade["exit"]["remaining_position_pct"] = round(100.0 - total_exited, 1)
                        logger.info(f"  → {len(partial_exits)} partial exits detected ({total_exited:.1f}% scaled out)")

                    # Add to trade log
                    trade_log["trades"].append(dacle_trade)
                    synced_ids.add(mexc_order_id)
                    metrics = dacle_trade.get("metrics", {})
                    results["new_trades"].append({
                        "trade_id": trade_id,
                        "token": token,
                        "result": dacle_trade.get("result"),
                        "pnl_percent": metrics.get("pnl_percent"),
                        "pnl_usd": metrics.get("pnl_usd"),
                        "is_dacle_tracked": is_tracked,
                        # Session 315 L089: Include R:R data
                        "estimated_rr_ratio": metrics.get("estimated_rr_ratio"),
                        "actual_rr_ratio": metrics.get("actual_rr_ratio"),
                    })

                    tracked_label = "DACLE" if is_tracked else "non-DACLE"
                    logger.info(f"Synced trade: {trade_id} ({token}, {tracked_label}) - {dacle_trade.get('result')} ({dacle_trade.get('metrics', {}).get('pnl_percent', 0):+.2f}%)")

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

            # Calculate P&L from MEXC data
            # MEXC returns 'profit' field for close orders (reduceOnly=true)
            info = mexc_trade.get('info', {})
            profit_str = info.get('profit', '0')
            pnl_usd = float(profit_str) if profit_str else 0

            # Get leverage for P&L % calculation
            leverage = float(info.get('leverage', 1) or 1)

            # Calculate P&L percentage
            # For close orders: orderMargin is 0, so we calculate from cost/leverage
            order_margin = float(info.get('orderMargin', 0) or info.get('usedMargin', 0))
            if order_margin <= 0 and cost > 0:
                # For close orders, estimate margin from cost and leverage
                order_margin = cost / leverage if leverage > 0 else cost

            if order_margin > 0:
                pnl_percent = (pnl_usd / order_margin * 100)
            else:
                pnl_percent = 0

            # Check if this is a close order (has actual P&L)
            is_close_order = info.get('reduceOnly', False) or pnl_usd != 0

            # Determine result based on P&L
            # Use $1 USD threshold for BREAKEVEN to avoid tiny profit/loss noise
            if not is_close_order:
                # Entry orders don't have P&L yet - mark as PENDING
                result = "PENDING"
            elif abs(pnl_usd) < 1.0:  # Less than $1 USD = BREAKEVEN
                result = "BREAKEVEN"
            elif pnl_usd > 0:
                result = "WIN"
            else:
                result = "LOSS"

            # Load conviction score if available
            conviction_score = self._get_conviction_for_token(token, entry_date)

            # Session 315 L089: Get playbook R:R data
            rr_data = self._get_playbook_rr_data(token)

            trade_dict = {
                "trade_id": trade_id,
                "token": token,
                "exchange": "MEXC",  # Session 371: Multi-exchange support
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
                    "take_profit_hit": None,
                    # Session 315 L089: Partial exit tracking for TP scaling
                    "partial_exits": [],  # List of {price, date, percentage, pnl_pct}
                    "remaining_position_pct": 100.0,  # Tracks how much position is left
                },
                "position": {
                    "size_usd": cost,
                    "leverage": mexc_trade.get('info', {}).get('leverage', 1)
                },
                "metrics": {
                    "pnl_percent": round(pnl_percent, 2),
                    "pnl_usd": round(pnl_usd, 2),
                    "mae_percent": None,
                    "mfe_percent": None,
                    # Session 315 L089: Track actual R:R for profitability analysis
                    "estimated_sl_pct": rr_data.get("estimated_sl_pct"),
                    "estimated_tp_pct": rr_data.get("estimated_tp_pct"),
                    "estimated_rr_ratio": rr_data.get("estimated_rr_ratio"),
                    "actual_rr_ratio": None,  # Calculated after trade closes
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

            # Session 315 L089: Calculate actual R:R after building trade dict
            actual_rr = self._calculate_actual_rr(trade_dict)
            if actual_rr:
                trade_dict["metrics"]["actual_rr_ratio"] = actual_rr

            return trade_dict

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

    def _get_playbook_rr_data(
        self,
        token: str
    ) -> Dict[str, Optional[float]]:
        """
        Session 315 L089: Get R:R data from playbook if available.

        Returns:
            Dict with estimated_sl_pct, estimated_tp_pct, and estimated_rr_ratio
        """
        result = {
            "estimated_sl_pct": None,
            "estimated_tp_pct": None,
            "estimated_rr_ratio": None
        }

        try:
            playbook_path = DATA_TOKENS_DIR / token / "execution_playbook.json"
            if not playbook_path.exists():
                return result

            with open(playbook_path) as f:
                playbook = json.load(f)

            # Get SL data
            sl_data = playbook.get("stoploss", {})
            recommended_sl = sl_data.get("recommended", {})
            sl_pct = recommended_sl.get("percentage")

            # Get TP data - use first target (TP1 at 50%)
            tp_data = playbook.get("take_profit", {})
            tp_targets = tp_data.get("targets", [])
            tp_pct = None
            if tp_targets:
                # Get the first (most conservative) target
                tp_pct = tp_targets[0].get("percentage")

            if sl_pct:
                result["estimated_sl_pct"] = abs(sl_pct)
            if tp_pct:
                result["estimated_tp_pct"] = abs(tp_pct)

            # Calculate R:R if both available
            if result["estimated_sl_pct"] and result["estimated_tp_pct"]:
                result["estimated_rr_ratio"] = round(
                    result["estimated_tp_pct"] / result["estimated_sl_pct"], 2
                )

            return result

        except Exception as e:
            logger.debug(f"Could not get playbook R:R for {token}: {e}")
            return result

    def _calculate_actual_rr(
        self,
        trade: Dict[str, Any]
    ) -> Optional[float]:
        """
        Session 315 L089: Calculate actual R:R ratio from closed trade.

        Actual R:R = |profit%| / |loss%| for the trade
        - WIN: profit / estimated_sl (what we risked)
        - LOSS: estimated_tp / |actual_loss| (what we would have gained vs what we lost)

        For accurate comparison, we need the SL that was set when entering.
        If not available, we estimate from the result.
        """
        result = trade.get("result")
        pnl_pct = trade.get("metrics", {}).get("pnl_percent", 0)
        estimated_sl = trade.get("metrics", {}).get("estimated_sl_pct")
        estimated_tp = trade.get("metrics", {}).get("estimated_tp_pct")

        if not pnl_pct or abs(pnl_pct) < 0.1:
            # BREAKEVEN - R:R not meaningful
            return None

        if result == "WIN":
            # Actual R:R = what we gained / what we risked
            if estimated_sl and estimated_sl > 0:
                return round(abs(pnl_pct) / estimated_sl, 2)
            else:
                # Estimate SL was ~10% (typical)
                return round(abs(pnl_pct) / 10.0, 2)

        elif result == "LOSS":
            # Actual R:R = what we would have gained / what we lost
            if estimated_tp and abs(pnl_pct) > 0:
                return round(estimated_tp / abs(pnl_pct), 2)
            else:
                # Estimate TP was ~30% (typical)
                return round(30.0 / abs(pnl_pct), 2)

        return None

    def _detect_partial_exits(
        self,
        trades: List[Dict[str, Any]],
        token: str
    ) -> List[Dict[str, Any]]:
        """
        Session 315 L089: Detect partial exits from multiple close orders.

        Partial exit = reduceOnly orders for same symbol with smaller quantity.
        Groups orders by symbol and timestamp proximity to find scaling out.

        Returns:
            List of partial exit dicts {price, date, percentage, pnl_pct}
        """
        partial_exits = []

        # Filter for same token's reduce-only orders
        token_closes = [
            t for t in trades
            if token in t.get('symbol', '')
            and t.get('info', {}).get('reduceOnly', False)
        ]

        if len(token_closes) <= 1:
            return partial_exits

        # Sort by timestamp
        token_closes.sort(key=lambda x: x.get('timestamp', 0))

        # Calculate total closed amount
        total_amount = sum(float(t.get('amount', 0)) for t in token_closes)

        if total_amount <= 0:
            return partial_exits

        # Track each close as a partial exit (except last one which is "full" close)
        cumulative_pct = 0.0
        for i, close in enumerate(token_closes[:-1]):
            amount = float(close.get('amount', 0))
            close_pct = (amount / total_amount) * 100
            cumulative_pct += close_pct

            close_ts = close.get('timestamp', 0)
            close_date = datetime.fromtimestamp(close_ts / 1000, tz=timezone.utc) if close_ts else None

            info = close.get('info', {})
            pnl = float(info.get('profit', 0))
            margin = float(info.get('orderMargin', 0) or info.get('usedMargin', 0))
            pnl_pct = (pnl / margin * 100) if margin > 0 else 0

            partial_exits.append({
                "price": float(close.get('price', 0)),
                "date": close_date.isoformat() if close_date else None,
                "percentage": round(close_pct, 1),
                "cumulative_pct": round(cumulative_pct, 1),
                "pnl_usd": round(pnl, 2),
                "pnl_pct": round(pnl_pct, 2),
                "order_id": close.get('id'),
            })

            logger.debug(f"Partial exit detected: {token} {close_pct:.1f}% @ ${close.get('price', 0)}")

        return partial_exits

    def _trigger_forward_validation_sync(self) -> None:
        """Trigger forward validation sync after new trades added."""
        try:
            from src.conviction.forward_validation import sync_trade_outcomes

            result = sync_trade_outcomes()
            matched = len(result.get("matched", []))
            logger.info(f"Forward validation sync complete: {matched} trades matched")

        except Exception as e:
            logger.warning(f"Forward validation sync failed: {e}")

    # =========================================================================
    # Phase 5A + 5E: Real-Time Position & Balance Monitoring (Session 320)
    # =========================================================================

    def get_open_positions(self) -> List[Dict[str, Any]]:
        """
        Fetch current open positions from MEXC Futures API.

        Session 320 - Phase 5A: Real-time position awareness for:
        - Dashboard display
        - L066 position limit enforcement
        - Duplicate entry prevention

        Returns:
            List of open position dicts with:
            - symbol: Trading pair (e.g., 'POWER/USDT:USDT')
            - side: 'LONG' or 'SHORT'
            - size_usd: Position notional value in USD
            - entry_price: Average entry price
            - current_price: Mark price (current)
            - unrealized_pnl: Unrealized P&L in USD
            - unrealized_pnl_pct: Unrealized P&L as percentage
            - liquidation_price: Liquidation price
            - leverage: Position leverage
            - margin_used: Initial margin used
        """
        if not self.exchange:
            logger.warning("MEXC not initialized - cannot fetch positions")
            return []

        try:
            # Fetch all positions (including zero-size for available markets)
            positions = self.exchange.fetch_positions()

            # Filter for active positions only (contracts > 0)
            open_positions = []
            for p in positions:
                contracts = float(p.get('contracts', 0) or 0)
                if contracts <= 0:
                    continue

                # Extract position data with safe defaults
                position_data = {
                    'symbol': p.get('symbol', ''),
                    'side': 'LONG' if p.get('side') == 'long' else 'SHORT',
                    'size_usd': abs(float(p.get('notional') or 0)),
                    'entry_price': float(p.get('entryPrice') or 0),
                    'current_price': float(p.get('markPrice') or 0),
                    'unrealized_pnl': float(p.get('unrealizedPnl') or 0),
                    'unrealized_pnl_pct': float(p.get('percentage') or 0),
                    'liquidation_price': float(p.get('liquidationPrice') or 0),
                    'leverage': int(p.get('leverage') or 1),
                    'margin_used': float(p.get('initialMargin') or 0),
                    # Extract token symbol for easier matching
                    'token': p.get('symbol', '').replace('/USDT:USDT', '').replace('/USDT', ''),
                }
                open_positions.append(position_data)

            logger.info(f"Fetched {len(open_positions)} open positions from MEXC")
            return open_positions

        except Exception as e:
            logger.error(f"Failed to fetch open positions: {e}")
            return []

    def get_account_balance(self) -> Dict[str, Any]:
        """
        Fetch account balance and margin info from MEXC Futures.

        Session 320 - Phase 5E: Account monitoring for:
        - Dashboard balance display
        - Margin health indicator
        - Available margin for new positions

        Returns:
            Dict with:
            - total_equity: Total account value (balance + unrealized P&L)
            - available_margin: Free margin for new positions
            - used_margin: Margin locked in positions
            - unrealized_pnl: Total unrealized P&L across all positions
            - margin_ratio: Percentage of margin used (0-100)
        """
        if not self.exchange:
            logger.warning("MEXC not initialized - cannot fetch balance")
            return {
                'total_equity': 0,
                'available_margin': 0,
                'used_margin': 0,
                'unrealized_pnl': 0,
                'margin_ratio': 0,
            }

        try:
            # Fetch balance - MEXC returns different structure for futures
            balance = self.exchange.fetch_balance()

            # MEXC Futures: Look for USDT balance
            usdt = balance.get('USDT', {})

            # Also check 'info' for additional data
            info = balance.get('info', {})

            # Extract values with safe defaults
            total_equity = float(usdt.get('total', 0) or 0)
            available_margin = float(usdt.get('free', 0) or 0)
            used_margin = float(usdt.get('used', 0) or 0)

            # Unrealized PnL might be in info or calculated
            unrealized_pnl = float(info.get('unrealizedProfit', 0) or 0)

            # Calculate margin ratio
            margin_ratio = 0.0
            if total_equity > 0:
                margin_ratio = round((used_margin / total_equity) * 100, 2)

            balance_data = {
                'total_equity': round(total_equity, 2),
                'available_margin': round(available_margin, 2),
                'used_margin': round(used_margin, 2),
                'unrealized_pnl': round(unrealized_pnl, 2),
                'margin_ratio': margin_ratio,
            }

            logger.info(f"Fetched account balance: ${total_equity:.2f} equity, {margin_ratio:.1f}% margin used")
            return balance_data

        except Exception as e:
            logger.error(f"Failed to fetch account balance: {e}")
            return {
                'total_equity': 0,
                'available_margin': 0,
                'used_margin': 0,
                'unrealized_pnl': 0,
                'margin_ratio': 0,
            }

    def get_full_status(self) -> Dict[str, Any]:
        """
        Get complete MEXC account status (positions + balance + metrics).

        Session 320 - Combined endpoint for dashboard and L066 enforcement.

        Returns:
            Dict with:
            - positions: List of open positions
            - position_count: Number of open positions
            - total_exposure_usd: Sum of all position notional values
            - total_unrealized_pnl: Sum of unrealized P&L
            - balance: Account balance info
            - l066_status: 'OK' if <3 positions, 'AT_LIMIT' if =3, 'EXCEEDED' if >3
            - margin_health: 'SAFE' (<50%), 'WARNING' (50-80%), 'DANGER' (>80%)
            - can_open_position: Boolean - True if can open new position
        """
        positions = self.get_open_positions()
        balance = self.get_account_balance()

        # Calculate aggregates
        total_exposure = sum(p['size_usd'] for p in positions)
        total_unrealized = sum(p['unrealized_pnl'] for p in positions)
        position_count = len(positions)

        # L066 status
        if position_count < 3:
            l066_status = 'OK'
        elif position_count == 3:
            l066_status = 'AT_LIMIT'
        else:
            l066_status = 'EXCEEDED'

        # Margin health
        margin_ratio = balance.get('margin_ratio', 0)
        if margin_ratio < 50:
            margin_health = 'SAFE'
        elif margin_ratio < 80:
            margin_health = 'WARNING'
        else:
            margin_health = 'DANGER'

        # Can open new position?
        can_open = (
            position_count < 3 and
            margin_ratio < 80 and
            balance.get('available_margin', 0) > 50  # At least $50 available
        )

        return {
            'positions': positions,
            'position_count': position_count,
            'total_exposure_usd': round(total_exposure, 2),
            'total_unrealized_pnl': round(total_unrealized, 2),
            'balance': balance,
            'l066_status': l066_status,
            'margin_health': margin_health,
            'can_open_position': can_open,
        }


def sync_mexc_trades(
    days: int = 7,
    token: Optional[str] = None,
    include_untracked: bool = True
) -> Dict[str, Any]:
    """Convenience function for syncing trades."""
    sync = MEXCTradeSync()
    return sync.sync_recent_trades(
        days=days,
        token_filter=token,
        include_untracked=include_untracked
    )


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
    parser.add_argument(
        "--tracked-only",
        action="store_true",
        help="Only sync DACLE-tracked tokens (default: sync ALL trades)"
    )
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
        include_untracked = not args.tracked_only
        mode = "DACLE-tracked tokens only" if args.tracked_only else "ALL trades"
        print(f"\n=== Syncing MEXC Trades (last {args.days} days, {mode}) ===\n")

        results = sync.sync_recent_trades(
            days=args.days,
            token_filter=args.token,
            include_untracked=include_untracked
        )

        print(f"New trades synced: {len(results['new_trades'])}")
        for trade in results['new_trades']:
            rr_str = ""
            if trade.get('estimated_rr_ratio'):
                rr_str = f" [Est R:R: {trade['estimated_rr_ratio']:.1f}]"
            if trade.get('actual_rr_ratio'):
                rr_str = f" [Actual R:R: {trade['actual_rr_ratio']:.2f}]"
            tracked = "✓ DACLE" if trade.get('is_dacle_tracked') else "○ Manual"
            print(f"  • {trade['token']}: {trade['result']} ({trade['pnl_percent']:+.2f}%){rr_str} [{tracked}]")

        print(f"\nSkipped (already synced): {len(results['skipped_existing'])}")
        print(f"Skipped (untracked): {len(results['skipped_untracked'])}")

        if results['errors']:
            print(f"\nErrors: {len(results['errors'])}")
            for error in results['errors']:
                print(f"  • {error}")

        print(f"\nSync timestamp: {results['sync_timestamp']}")
