"""
Kalshi API Client for Cross-Platform Arbitrage

Official API wrapper for Kalshi (CFTC-regulated prediction market).
Used for cross-platform arbitrage against Polymarket prices.

API Documentation:
- REST API: https://api.kalshi.com/docs
- Historical data: Requires API key + RSA signature
- WebSocket: Real-time orderbook updates

Authentication:
- API Key: From Kalshi dashboard (https://kalshi.com/settings/api)
- RSA Signature: Required for historical data endpoints
- Rate limits: 100 req/min for public, 1000 req/min for authenticated

Usage:
    client = KalshiClient(api_key="...", api_secret="...")
    await client.connect()

    # Get market price
    price = await client.get_price("TRUMP-2026", OrderSide.BUY)

    # Get orderbook
    book = await client.get_orderbook("TRUMP-2026")

    # Place order
    result = await client.place_order("TRUMP-2026", OrderSide.BUY, size=100, price=0.52)
"""

import asyncio
import base64
import hashlib
import hmac
import logging
import os
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple
from enum import Enum

import aiohttp
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.backends import default_backend

from src.execution.venue_adapter import (
    ExecutionVenue,
    VenueType,
    OrderSide,
    OrderStatus,
    OrderResult,
    OrderBook,
    PriceLevel,
    MarketInfo,
)

logger = logging.getLogger(__name__)


# Kalshi API endpoints
KALSHI_BASE_URL = "https://api.kalshi.com"
KALSHI_PUBLIC_URL = "https://public.kalshi.com"
KALSHI_HISTORICAL_URL = "https://www.kalshi.com/historical"


class KalshiOrderType(Enum):
    """Kalshi order types."""
    MARKET = "market"
    LIMIT = "limit"
    STOP = "stop"


@dataclass
class KalshiOrder:
    """Kalshi order representation."""
    order_id: str
    user_id: str
    ticker: str
    side: str  # "yes" or "no"
    action: str  # "buy" or "sell"
    type: str  # "market" or "limit"
    yes_price: int  # In cents (0-100)
    count: int  # Number of contracts
    status: str  # "pending", "filled", "cancelled"
    created_at: str
    updated_at: str
    expiration_time: Optional[str] = None
    close_cancel_count: int = 0
    queue_position: Optional[int] = None


class KalshiClient:
    """
    Low-level Kalshi API client.

    Handles authentication, request signing, and rate limiting.
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        api_secret: Optional[str] = None,
        rsa_private_key: Optional[str] = None,
        use_sandbox: bool = False,
    ):
        self.api_key = api_key or os.getenv("KALSHI_API_KEY")
        self.api_secret = api_secret or os.getenv("KALSHI_API_SECRET")
        self.rsa_private_key = rsa_private_key or os.getenv("KALSHI_RSA_PRIVATE_KEY")

        self.base_url = KALSHI_PUBLIC_URL if use_sandbox else KALSHI_BASE_URL
        self.historical_url = KALSHI_HISTORICAL_URL
        self.use_sandbox = use_sandbox

        self._session: Optional[aiohttp.ClientSession] = None
        self._access_token: Optional[str] = None
        self._token_expiry: float = 0

        # Rate limiting
        self._rate_limit = 100  # requests per minute
        self._request_times: List[float] = []

    async def connect(self):
        """Establish HTTP session and authenticate."""
        self._session = aiohttp.ClientSession(
            headers={
                "Accept": "application/json",
                "User-Agent": "DACLE-Bot/1.0",
            }
        )

        if self.api_key and self.api_secret:
            await self._authenticate()

    async def disconnect(self):
        """Close HTTP session."""
        if self._session:
            await self._session.close()
            self._session = None

    async def _authenticate(self):
        """
        Authenticate with Kalshi API.

        Uses API key/secret for JWT token exchange.
        Token valid for 1 hour.
        """
        url = f"{self.base_url}/auth/login"
        payload = {
            "api_key": self.api_key,
            "secret": self.api_secret,
        }

        async with self._session.post(url, json=payload) as resp:
            if resp.status == 200:
                data = await resp.json()
                self._access_token = data.get("token")
                self._token_expiry = time.time() + 3600  # 1 hour
                logger.info("Kalshi authentication successful")
            else:
                error = await resp.text()
                logger.error(f"Kalshi auth failed: {resp.status} {error}")
                raise ValueError(f"Authentication failed: {error}")

    def _get_auth_headers(self) -> Dict[str, str]:
        """Get authentication headers for API requests."""
        headers = {}

        if self._access_token and time.time() < self._token_expiry:
            headers["Authorization"] = f"Bearer {self._access_token}"
        elif self.api_key:
            # Fallback to API key auth
            headers["X-KALSHI-API-KEY"] = self.api_key

        return headers

    def _sign_request(self, method: str, path: str, body: str = "") -> str:
        """
        Sign request with RSA private key for historical data access.

        Required for:
        - Historical market data
        - Historical trades
        - Candlestick data

        Signature format:
        base64(RSA-SHA256(method + path + timestamp + body))
        """
        if not self.rsa_private_key:
            raise ValueError("RSA private key required for signed requests")

        timestamp = str(int(time.time()))
        message = f"{method}{path}{timestamp}{body}"

        # Load private key
        key = serialization.load_pem_private_key(
            self.rsa_private_key.encode(),
            password=None,
            backend=default_backend(),
        )

        # Sign message
        signature = key.sign(
            message.encode(),
            padding.PKCS1v15(),
            hashes.SHA256(),
        )

        # Return base64-encoded signature
        return base64.b64encode(signature).decode()

    def _get_signed_headers(self, method: str, path: str, body: str = "") -> Dict[str, str]:
        """Get headers for signed API requests."""
        timestamp = str(int(time.time()))
        signature = self._sign_request(method, path, body)

        return {
            "X-KALSHI-API-KEY": self.api_key,
            "X-KALSHI-SIGNATURE": signature,
            "X-KALSHI-TIMESTAMP": timestamp,
        }

    async def _request(
        self,
        method: str,
        endpoint: str,
        params: Optional[Dict] = None,
        json: Optional[Dict] = None,
        signed: bool = False,
    ) -> Any:
        """
        Make authenticated API request with rate limiting.

        Args:
            method: HTTP method
            endpoint: API endpoint path
            params: Query parameters
            json: Request body
            signed: Whether to use RSA signature

        Returns:
            Parsed JSON response
        """
        if not self._session:
            await self.connect()

        # Rate limiting
        now = time.time()
        self._request_times = [t for t in self._request_times if now - t < 60]
        if len(self._request_times) >= self._rate_limit:
            sleep_time = 60 - (now - self._request_times[0])
            if sleep_time > 0:
                logger.warning(f"Rate limit hit, sleeping for {sleep_time:.1f}s")
                await asyncio.sleep(sleep_time)
        self._request_times.append(now)

        url = f"{self.base_url}{endpoint}"
        headers = self._get_signed_headers(method, endpoint, str(json or "")) if signed else self._get_auth_headers()

        async with self._session.request(
            method,
            url,
            params=params,
            json=json,
            headers=headers,
        ) as resp:
            if resp.status == 401:
                # Token expired, re-authenticate
                await self._authenticate()
                headers = self._get_auth_headers()
                async with self._session.request(
                    method, url, params=params, json=json, headers=headers
                ) as retry_resp:
                    return await retry_resp.json()

            if resp.status >= 400:
                error = await resp.text()
                logger.error(f"Kalshi API error: {resp.status} {error}")
                raise ValueError(f"API error {resp.status}: {error}")

            return await resp.json()

    # Public endpoints

    async def get_markets(
        self,
        series_ticker: Optional[str] = None,
        status: Optional[str] = None,
        limit: int = 100,
        cursor: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Get list of markets.

        Args:
            series_ticker: Filter by series (e.g., "TRUMP")
            status: Filter by status ("open", "closed", "settled")
            limit: Max results (default 100)
            cursor: Pagination cursor

        Returns:
            {"markets": [...], "cursor": "..."}
        """
        params = {"limit": limit}
        if series_ticker:
            params["series_ticker"] = series_ticker
        if status:
            params["status"] = status
        if cursor:
            params["cursor"] = cursor

        return await self._request("GET", "/markets", params=params)

    async def get_market(self, ticker: str) -> Optional[Dict[str, Any]]:
        """
        Get single market by ticker.

        Args:
            ticker: Market ticker (e.g., "TRUMP-2026")

        Returns:
            Market dict or None
        """
        try:
            return await self._request("GET", f"/markets/{ticker}")
        except ValueError:
            return None

    async def get_orderbook(self, ticker: str) -> Optional[Dict[str, Any]]:
        """
        Get order book for a market.

        Args:
            ticker: Market ticker

        Returns:
            {"yes": {"bids": [...], "asks": [...]}, "no": {...}}
        """
        try:
            return await self._request("GET", f"/markets/{ticker}/orderbook")
        except ValueError:
            return None

    async def get_trades(
        self,
        ticker: str,
        limit: int = 100,
        cursor: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Get recent trades for a market.

        Args:
            ticker: Market ticker
            limit: Max results

        Returns:
            {"trades": [...], "cursor": "..."}
        """
        params = {"limit": limit}
        if cursor:
            params["cursor"] = cursor

        return await self._request("GET", f"/markets/{ticker}/trades", params=params)

    async def get_account(self) -> Dict[str, Any]:
        """Get current account info."""
        return await self._request("GET", "/user")

    async def get_balance(self) -> float:
        """Get available USD balance."""
        account = await self.get_account()
        return float(account.get("balance", 0))

    # Order endpoints

    async def create_order(
        self,
        ticker: str,
        action: str,  # "buy" or "sell"
        side: str,  # "yes" or "no"
        count: int,
        yes_price: Optional[int] = None,
        order_type: str = "limit",
        expiration_time: Optional[str] = None,
    ) -> KalshiOrder:
        """
        Create an order.

        Args:
            ticker: Market ticker
            action: "buy" or "sell"
            side: "yes" or "no"
            count: Number of contracts
            yes_price: Price in cents (required for limit orders)
            order_type: "market" or "limit"
            expiration_time: ISO 8601 expiry

        Returns:
            KalshiOrder object
        """
        payload = {
            "ticker": ticker,
            "action": action,
            "side": side,
            "count": count,
            "type": order_type,
        }

        if yes_price:
            payload["yes_price"] = yes_price
        if expiration_time:
            payload["expiration_time"] = expiration_time

        result = await self._request("POST", "/orders", json=payload)
        return KalshiOrder(
            order_id=result["order"]["order_id"],
            user_id=result["order"]["user_id"],
            ticker=result["order"]["ticker"],
            side=result["order"]["side"],
            action=result["order"]["action"],
            type=result["order"]["type"],
            yes_price=result["order"]["yes_price"],
            count=result["order"]["count"],
            status=result["order"]["status"],
            created_at=result["order"]["created_at"],
            updated_at=result["order"]["updated_at"],
        )

    async def cancel_order(self, order_id: str) -> bool:
        """Cancel an order."""
        try:
            await self._request("POST", f"/orders/{order_id}/cancel")
            return True
        except ValueError:
            return False

    async def get_orders(
        self,
        ticker: Optional[str] = None,
        status: Optional[str] = None,
    ) -> List[KalshiOrder]:
        """Get user's orders with optional filters."""
        params = {}
        if ticker:
            params["ticker"] = ticker
        if status:
            params["status"] = status

        result = await self._request("GET", "/orders", params=params)
        return [
            KalshiOrder(
                order_id=o["order_id"],
                user_id=o["user_id"],
                ticker=o["ticker"],
                side=o["side"],
                action=o["action"],
                type=o["type"],
                yes_price=o["yes_price"],
                count=o["count"],
                status=o["status"],
                created_at=o["created_at"],
                updated_at=o["updated_at"],
            )
            for o in result.get("orders", [])
        ]

    # Historical data endpoints (require RSA signature)

    async def get_historical_markets(
        self,
        start_date: str,
        end_date: str,
        series: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        Get historical market data.

        Requires RSA signature authentication.

        Args:
            start_date: YYYY-MM-DD
            end_date: YYYY-MM-DD
            series: Optional series filter

        Returns:
            List of market snapshots
        """
        endpoint = "/markets"
        params = {"start_date": start_date, "end_date": end_date}
        if series:
            params["series"] = series

        # Build query string for signing
        query = "&".join(f"{k}={v}" for k, v in sorted(params.items()))
        full_path = f"{endpoint}?{query}"

        headers = self._get_signed_headers("GET", full_path)

        url = f"{self.historical_url}{full_path}"
        async with self._session.get(url, headers=headers) as resp:
            if resp.status >= 400:
                error = await resp.text()
                logger.error(f"Historical API error: {resp.status} {error}")
                return []
            return await resp.json()

    async def get_candlesticks(
        self,
        ticker: str,
        resolution: str = "1m",  # 1m, 5m, 15m, 1h, 4h, 1d
        start_ts: Optional[int] = None,
        end_ts: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """
        Get candlestick (OHLCV) data.

        Requires RSA signature authentication.

        Args:
            ticker: Market ticker
            resolution: Candlestick resolution
            start_ts: Start timestamp (unix seconds)
            end_ts: End timestamp

        Returns:
            List of candles: [{timestamp, open, high, low, close, volume}, ...]
        """
        endpoint = f"/markets/{ticker}/candlesticks"
        params = {"resolution": resolution}
        if start_ts:
            params["start_ts"] = start_ts
        if end_ts:
            params["end_ts"] = end_ts

        query = "&".join(f"{k}={v}" for k, v in sorted(params.items()))
        full_path = f"{endpoint}?{query}"

        headers = self._get_signed_headers("GET", full_path)

        url = f"{self.historical_url}{full_path}"
        async with self._session.get(url, headers=headers) as resp:
            if resp.status >= 400:
                logger.error(f"Candlestick API error: {resp.status}")
                return []

            data = await resp.json()
            return data.get("candlesticks", [])


class KalshiVenue(ExecutionVenue):
    """
    Kalshi venue implementation for cross-platform arbitrage.

    Wraps KalshiClient to implement ExecutionVenue interface.
    """

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self.client = KalshiClient(
            api_key=config.get("api_key"),
            api_secret=config.get("api_secret"),
            rsa_private_key=config.get("rsa_private_key"),
            use_sandbox=config.get("use_sandbox", False),
        )
        self._fee_schedule = {
            "maker_fee": 0.0,  # Zero maker fees
            "taker_fee": 0.05,  # 5% on winnings
            "withdrawal_fee": 0.0,
        }

    @property
    def venue_type(self) -> VenueType:
        return VenueType.KALSHI

    async def connect(self) -> bool:
        try:
            await self.client.connect()
            self._connected = True
            logger.info("Kalshi venue connected")
            return True
        except Exception as e:
            logger.error(f"Kalshi connection failed: {e}")
            return False

    async def disconnect(self):
        await self.client.disconnect()
        self._connected = False

    async def get_market_info(self, market_id: str) -> Optional[MarketInfo]:
        """Fetch market metadata."""
        market_data = await self.client.get_market(market_id)
        if not market_data:
            return None

        return MarketInfo(
            market_id=market_id,
            venue=VenueType.KALSHI,
            title=market_data.get("title", ""),
            outcome="YES",  # Kalshi uses yes/no binary
            expiry_date=None,  # Would need to parse from market_data
            status=market_data.get("status", "open"),
            tick_size=0.01,
            min_size=1.0,
        )

    async def get_orderbook(self, market_id: str) -> Optional[OrderBook]:
        """Fetch order book snapshot."""
        book_data = await self.client.get_orderbook(market_id)
        if not book_data:
            return None

        # Parse yes book
        yes_book = book_data.get("yes", {})
        bids = [
            PriceLevel(price=b["yes_price"] / 100, size=b["count"])
            for b in yes_book.get("bids", [])
        ]
        asks = [
            PriceLevel(price=a["yes_price"] / 100, size=a["count"])
            for a in yes_book.get("asks", [])
        ]

        bids.sort(key=lambda x: x.price, reverse=True)
        asks.sort(key=lambda x: x.price)

        return OrderBook(
            market_id=market_id,
            venue=VenueType.KALSHI,
            timestamp=datetime.now(timezone.utc),
            bids=bids[:10],  # Top 10 levels
            asks=asks[:10],
        )

    async def get_price(self, market_id: str, side: OrderSide) -> Optional[float]:
        """Get current executable price."""
        book = await self.get_orderbook(market_id)
        if not book:
            return None

        if side == OrderSide.BUY:
            # We hit the asks (buy at ask price)
            return book.asks[0].price if book.asks else None
        else:
            # We hit the bids (sell at bid price)
            return book.bids[0].price if book.bids else None

    async def place_order(
        self,
        market_id: str,
        side: OrderSide,
        size: float,
        price: Optional[float] = None,
        order_type: str = "limit",
    ) -> OrderResult:
        """Place an order on Kalshi."""
        try:
            # Convert to Kalshi terminology
            action = "buy" if side == OrderSide.BUY else "sell"
            kalshi_side = "yes"  # Simplified - would need to support "no" orders
            count = int(size)  # Number of contracts
            yes_price = int(price * 100) if price else None

            order = await self.client.create_order(
                ticker=market_id,
                action=action,
                side=kalshi_side,
                count=count,
                yes_price=yes_price,
                order_type=order_type,
            )

            return OrderResult(
                order_id=order.order_id,
                venue=VenueType.KALSHI,
                market_id=market_id,
                side=side,
                status=OrderStatus(order.status),
                filled_size=order.count if order.status == "filled" else 0,
                filled_price=order.yes_price / 100 if order.yes_price else 0,
            )

        except Exception as e:
            logger.error(f"Kalshi order failed: {e}")
            return OrderResult(
                order_id="",
                venue=VenueType.KALSHI,
                market_id=market_id,
                side=side,
                status=OrderStatus.REJECTED,
                error=str(e),
            )

    async def cancel_order(self, order_id: str) -> bool:
        """Cancel an order."""
        return await self.client.cancel_order(order_id)

    async def get_balance(self, asset: str) -> float:
        """Get account balance."""
        return await self.client.get_balance()

    def get_fee_schedule(self) -> Dict[str, float]:
        """Return fee structure."""
        return self._fee_schedule
