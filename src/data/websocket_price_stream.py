#!/usr/bin/env python3
"""
WebSocket Price Stream - Real-time Price Updates via Binance WebSocket (Session 283)

Provides real-time price updates for monitored tokens using Binance's
public WebSocket API. Much lower latency than REST API polling.

Architecture:
- Single persistent WebSocket connection to Binance
- Subscribes to multiple mini-ticker streams (one per token)
- Callbacks triggered on price updates
- Automatic reconnection on disconnect
- Thread-safe price cache

Usage:
    from src.data.websocket_price_stream import PriceStreamClient

    # Create client with callback
    def on_price(token: str, price: float, change_24h: float):
        print(f"{token}: ${price:.4f} ({change_24h:+.2f}%)")

    client = PriceStreamClient(on_price_update=on_price)

    # Subscribe to tokens
    client.subscribe(["BTC", "ETH", "POWER"])

    # Start streaming (blocking)
    client.start()

    # Or run in background thread
    import threading
    thread = threading.Thread(target=client.start, daemon=True)
    thread.start()

    # Get cached price
    price = client.get_price("BTC")

Author: Claude Code (Session 283 - WebSocket Price Alerts)
Date: 2026-01-05
"""

import json
import logging
import threading
import time
from datetime import datetime, timezone
from typing import Callable, Dict, List, Optional, Set
from dataclasses import dataclass

try:
    import websocket
    WEBSOCKET_AVAILABLE = True
except ImportError:
    WEBSOCKET_AVAILABLE = False
    websocket = None

logger = logging.getLogger(__name__)

# Binance WebSocket endpoints
BINANCE_WS_URL = "wss://stream.binance.com:9443/ws"
BINANCE_STREAM_URL = "wss://stream.binance.com:9443/stream"

# Reconnection settings
RECONNECT_DELAY_INITIAL = 1  # seconds
RECONNECT_DELAY_MAX = 60  # seconds
HEARTBEAT_INTERVAL = 30  # seconds


@dataclass
class PriceUpdate:
    """Represents a real-time price update."""
    symbol: str  # Trading symbol (e.g., "BTCUSDT")
    token: str  # Token symbol (e.g., "BTC")
    price: float  # Current price
    change_24h_pct: float  # 24h change percentage
    volume_24h: float  # 24h volume in quote currency
    timestamp: datetime  # Update timestamp
    source: str = "binance_ws"


class PriceStreamClient:
    """
    Real-time price stream client using Binance WebSocket.

    Features:
    - Automatic reconnection with exponential backoff
    - Thread-safe price cache
    - Multiple token subscriptions
    - Callback support for price updates
    """

    def __init__(
        self,
        on_price_update: Optional[Callable[[str, float, float], None]] = None,
        on_connect: Optional[Callable[[], None]] = None,
        on_disconnect: Optional[Callable[[str], None]] = None,
    ):
        """
        Initialize the price stream client.

        Args:
            on_price_update: Callback(token, price, change_24h) on each price update
            on_connect: Callback when connected
            on_disconnect: Callback(reason) when disconnected
        """
        if not WEBSOCKET_AVAILABLE:
            raise ImportError(
                "websocket-client not installed. "
                "Install with: pip install websocket-client"
            )

        self.on_price_update = on_price_update
        self.on_connect = on_connect
        self.on_disconnect = on_disconnect

        self._subscriptions: Set[str] = set()  # Token symbols (e.g., "BTC")
        self._price_cache: Dict[str, PriceUpdate] = {}
        self._cache_lock = threading.Lock()

        self._ws: Optional[websocket.WebSocketApp] = None
        self._running = False
        self._reconnect_delay = RECONNECT_DELAY_INITIAL
        self._last_heartbeat = time.time()

    def subscribe(self, tokens: List[str]):
        """
        Subscribe to price updates for tokens.

        Args:
            tokens: List of token symbols (e.g., ["BTC", "ETH", "POWER"])
        """
        for token in tokens:
            self._subscriptions.add(token.upper())
        logger.info(f"Subscribed to {len(tokens)} tokens: {tokens}")

    def unsubscribe(self, tokens: List[str]):
        """Unsubscribe from tokens."""
        for token in tokens:
            self._subscriptions.discard(token.upper())

    def get_price(self, token: str) -> Optional[PriceUpdate]:
        """
        Get the latest cached price for a token.

        Args:
            token: Token symbol (e.g., "BTC")

        Returns:
            PriceUpdate if available, None otherwise
        """
        with self._cache_lock:
            return self._price_cache.get(token.upper())

    def get_all_prices(self) -> Dict[str, PriceUpdate]:
        """Get all cached prices."""
        with self._cache_lock:
            return dict(self._price_cache)

    def start(self, blocking: bool = True):
        """
        Start the WebSocket connection.

        Args:
            blocking: If True, blocks until stop() is called
        """
        if not self._subscriptions:
            logger.warning("No subscriptions - add tokens before starting")
            return

        self._running = True

        while self._running:
            try:
                self._connect()
            except Exception as e:
                logger.error(f"WebSocket error: {e}")

            if self._running:
                logger.info(f"Reconnecting in {self._reconnect_delay}s...")
                time.sleep(self._reconnect_delay)
                self._reconnect_delay = min(
                    self._reconnect_delay * 2,
                    RECONNECT_DELAY_MAX
                )

            if not blocking:
                break

    def stop(self):
        """Stop the WebSocket connection."""
        self._running = False
        if self._ws:
            self._ws.close()

    def _connect(self):
        """Establish WebSocket connection."""
        # Build stream URL for all subscriptions
        streams = [f"{token.lower()}usdt@miniTicker" for token in self._subscriptions]
        url = f"{BINANCE_STREAM_URL}?streams={'/'.join(streams)}"

        logger.info(f"Connecting to Binance WebSocket ({len(streams)} streams)...")

        self._ws = websocket.WebSocketApp(
            url,
            on_open=self._on_open,
            on_message=self._on_message,
            on_error=self._on_error,
            on_close=self._on_close,
        )

        self._ws.run_forever(ping_interval=HEARTBEAT_INTERVAL)

    def _on_open(self, ws):
        """Handle WebSocket connection opened."""
        logger.info("WebSocket connected")
        self._reconnect_delay = RECONNECT_DELAY_INITIAL
        self._last_heartbeat = time.time()

        if self.on_connect:
            try:
                self.on_connect()
            except Exception as e:
                logger.error(f"on_connect callback error: {e}")

    def _on_message(self, ws, message):
        """Handle incoming WebSocket message."""
        try:
            data = json.loads(message)

            # Combined stream format: {"stream": "btcusdt@miniTicker", "data": {...}}
            if "data" in data:
                ticker_data = data["data"]
            else:
                ticker_data = data

            # Mini ticker format:
            # {
            #   "e": "24hrMiniTicker",
            #   "s": "BTCUSDT",       # Symbol
            #   "c": "98500.00",      # Current price
            #   "o": "97000.00",      # Open price
            #   "h": "99000.00",      # High price
            #   "l": "96500.00",      # Low price
            #   "v": "12345.67",      # Total traded base asset volume
            #   "q": "1234567.89"     # Total traded quote asset volume
            # }

            if ticker_data.get("e") != "24hrMiniTicker":
                return

            symbol = ticker_data.get("s", "")  # e.g., "BTCUSDT"
            if not symbol.endswith("USDT"):
                return

            token = symbol[:-4]  # Remove "USDT" suffix

            try:
                current_price = float(ticker_data.get("c", 0))
                open_price = float(ticker_data.get("o", current_price))
                volume_24h = float(ticker_data.get("q", 0))

                # Calculate 24h change
                if open_price > 0:
                    change_24h_pct = ((current_price - open_price) / open_price) * 100
                else:
                    change_24h_pct = 0.0

                update = PriceUpdate(
                    symbol=symbol,
                    token=token,
                    price=current_price,
                    change_24h_pct=change_24h_pct,
                    volume_24h=volume_24h,
                    timestamp=datetime.now(timezone.utc),
                )

                # Update cache
                with self._cache_lock:
                    self._price_cache[token] = update

                # Trigger callback
                if self.on_price_update:
                    try:
                        self.on_price_update(token, current_price, change_24h_pct)
                    except Exception as e:
                        logger.error(f"on_price_update callback error: {e}")

            except (ValueError, TypeError) as e:
                logger.debug(f"Failed to parse ticker data: {e}")

        except json.JSONDecodeError as e:
            logger.debug(f"Invalid JSON message: {e}")

    def _on_error(self, ws, error):
        """Handle WebSocket error."""
        logger.error(f"WebSocket error: {error}")

    def _on_close(self, ws, close_status_code, close_msg):
        """Handle WebSocket connection closed."""
        reason = f"code={close_status_code}, msg={close_msg}"
        logger.info(f"WebSocket disconnected: {reason}")

        if self.on_disconnect:
            try:
                self.on_disconnect(reason)
            except Exception as e:
                logger.error(f"on_disconnect callback error: {e}")


class PriceAlertManager:
    """
    Manages price-based alerts using WebSocket streaming.

    Features:
    - Define entry zones for tokens
    - Automatic alerts when price enters/exits zones
    - Integrates with Telegram notifications
    """

    def __init__(
        self,
        on_entry_zone: Optional[Callable[[str, float, Dict], None]] = None,
        on_invalidation: Optional[Callable[[str, float, Dict], None]] = None,
    ):
        """
        Initialize the alert manager.

        Args:
            on_entry_zone: Callback(token, price, zone_info) when price enters zone
            on_invalidation: Callback(token, price, inv_info) when price near invalidation
        """
        self.on_entry_zone = on_entry_zone
        self.on_invalidation = on_invalidation

        self._entry_zones: Dict[str, Dict] = {}  # token -> {entry_low, entry_high, ...}
        self._alerts_sent: Dict[str, datetime] = {}  # Deduplication
        self._client = PriceStreamClient(on_price_update=self._on_price_update)

    def add_zone(
        self,
        token: str,
        entry_low: float,
        entry_high: float,
        invalidation: Optional[float] = None,
        metadata: Optional[Dict] = None,
    ):
        """
        Add an entry zone to monitor.

        Args:
            token: Token symbol
            entry_low: Lower bound of entry zone
            entry_high: Upper bound of entry zone
            invalidation: Invalidation level (trade no longer valid if price above)
            metadata: Additional metadata (playbook info, etc.)
        """
        self._entry_zones[token.upper()] = {
            "entry_low": entry_low,
            "entry_high": entry_high,
            "invalidation": invalidation,
            "metadata": metadata or {},
            "last_position": "UNKNOWN",  # IN_ZONE, ABOVE_ZONE, BELOW_ZONE
        }
        self._client.subscribe([token])
        logger.info(f"Added zone for {token}: ${entry_low:.4f}-${entry_high:.4f}")

    def remove_zone(self, token: str):
        """Remove a token from monitoring."""
        self._entry_zones.pop(token.upper(), None)
        self._client.unsubscribe([token])

    def start(self, blocking: bool = True):
        """Start monitoring."""
        if not self._entry_zones:
            logger.warning("No zones configured - add zones before starting")
            return
        self._client.start(blocking=blocking)

    def stop(self):
        """Stop monitoring."""
        self._client.stop()

    def _on_price_update(self, token: str, price: float, change_24h: float):
        """Handle price update from WebSocket."""
        zone = self._entry_zones.get(token)
        if not zone:
            return

        entry_low = zone["entry_low"]
        entry_high = zone["entry_high"]
        invalidation = zone.get("invalidation")
        last_position = zone.get("last_position", "UNKNOWN")

        # Determine current position
        if entry_low <= price <= entry_high:
            current_position = "IN_ZONE"
        elif price < entry_low:
            current_position = "BELOW_ZONE"
        else:
            current_position = "ABOVE_ZONE"

        # Detect zone entry
        if current_position == "IN_ZONE" and last_position != "IN_ZONE":
            if self._can_alert(token, "entry_zone"):
                logger.info(f"🎯 {token} ENTERED ZONE at ${price:.4f}")
                self._mark_alerted(token, "entry_zone")
                if self.on_entry_zone:
                    try:
                        self.on_entry_zone(token, price, {
                            "entry_low": entry_low,
                            "entry_high": entry_high,
                            "position_pct": ((price - entry_low) / (entry_high - entry_low)) * 100,
                            "metadata": zone.get("metadata", {}),
                        })
                    except Exception as e:
                        logger.error(f"on_entry_zone callback error: {e}")

        # Detect invalidation proximity
        if invalidation and price >= invalidation * 0.98:  # Within 2% of invalidation
            if self._can_alert(token, "invalidation"):
                logger.warning(f"⚠️ {token} NEAR INVALIDATION at ${price:.4f}")
                self._mark_alerted(token, "invalidation")
                if self.on_invalidation:
                    try:
                        self.on_invalidation(token, price, {
                            "invalidation": invalidation,
                            "distance_pct": ((invalidation - price) / invalidation) * 100,
                            "metadata": zone.get("metadata", {}),
                        })
                    except Exception as e:
                        logger.error(f"on_invalidation callback error: {e}")

        # Update last position
        zone["last_position"] = current_position

    def _can_alert(self, token: str, alert_type: str) -> bool:
        """Check if we can send an alert (cooldown)."""
        key = f"{token}_{alert_type}"
        last_alert = self._alerts_sent.get(key)
        if last_alert:
            cooldown = timedelta(hours=4)  # 4 hour cooldown
            if datetime.now(timezone.utc) - last_alert < cooldown:
                return False
        return True

    def _mark_alerted(self, token: str, alert_type: str):
        """Mark that we sent an alert."""
        key = f"{token}_{alert_type}"
        self._alerts_sent[key] = datetime.now(timezone.utc)


# Convenience function for quick price checks
def get_realtime_price(token: str, timeout: float = 5.0) -> Optional[float]:
    """
    Get a single real-time price via WebSocket.

    This is a convenience function for one-off price checks.
    For continuous monitoring, use PriceStreamClient.

    Args:
        token: Token symbol (e.g., "BTC")
        timeout: How long to wait for price (seconds)

    Returns:
        Current price if available within timeout, None otherwise
    """
    if not WEBSOCKET_AVAILABLE:
        logger.warning("websocket-client not installed")
        return None

    result = {"price": None}
    event = threading.Event()

    def on_price(t: str, price: float, change: float):
        if t == token.upper():
            result["price"] = price
            event.set()

    client = PriceStreamClient(on_price_update=on_price)
    client.subscribe([token])

    thread = threading.Thread(target=client.start, daemon=True)
    thread.start()

    event.wait(timeout=timeout)
    client.stop()

    return result["price"]


if __name__ == "__main__":
    import sys

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(levelname)s - %(message)s"
    )

    # Test mode
    if len(sys.argv) > 1:
        token = sys.argv[1].upper()
        print(f"Getting real-time price for {token}...")
        price = get_realtime_price(token)
        if price:
            print(f"{token}: ${price:.4f}")
        else:
            print(f"Could not get price for {token}")
        sys.exit(0)

    # Demo mode - stream BTC prices
    def on_price(token: str, price: float, change: float):
        print(f"{datetime.now().strftime('%H:%M:%S')} | {token}: ${price:,.2f} ({change:+.2f}%)")

    def on_connect():
        print("Connected to Binance WebSocket")

    def on_disconnect(reason):
        print(f"Disconnected: {reason}")

    print("Starting price stream (Ctrl+C to stop)...")
    client = PriceStreamClient(
        on_price_update=on_price,
        on_connect=on_connect,
        on_disconnect=on_disconnect,
    )
    client.subscribe(["BTC", "ETH"])

    try:
        client.start()
    except KeyboardInterrupt:
        print("\nStopping...")
        client.stop()
