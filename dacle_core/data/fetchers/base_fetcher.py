"""
Base Fetcher - Session 371 P1.5

Abstract base class for all data fetchers in DACLE.
Provides standardized error handling, logging, caching, and retry logic.

Usage:
    from dacle_core.data.fetchers.base_fetcher import BaseFetcher

    class MyExchangeFetcher(BaseFetcher):
        SOURCE_NAME = "my_exchange"
        BASE_URL = "https://api.myexchange.com"
        DEFAULT_TIMEOUT = 30
        RATE_LIMIT_PER_MINUTE = 60

        def fetch(self, **kwargs) -> FetchResult:
            response = self._make_request("/endpoint", params={...})
            if response is None:
                return self._error_result("Failed to fetch data")
            return self._success_result(self._parse_response(response))

Benefits:
    - Consistent error handling across all fetchers
    - Standardized logging with source attribution
    - Built-in retry logic with exponential backoff
    - Optional Redis caching integration
    - Rate limiting awareness
    - Metrics tracking (request count, success rate)
"""

import logging
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Any, Dict, List, Optional, TypeVar, Generic

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

logger = logging.getLogger(__name__)


class FetchStatus(Enum):
    """Status of a fetch operation."""
    SUCCESS = "success"
    ERROR = "error"
    RATE_LIMITED = "rate_limited"
    TIMEOUT = "timeout"
    NO_DATA = "no_data"
    CACHED = "cached"


@dataclass
class FetchResult:
    """
    Standardized result from any fetcher operation.

    All fetchers return this type for consistent handling upstream.
    """
    status: FetchStatus
    data: Optional[Any] = None
    error_message: Optional[str] = None
    source: str = ""
    timestamp: datetime = field(default_factory=datetime.utcnow)
    cached: bool = False
    request_time_ms: Optional[float] = None
    rate_limit_remaining: Optional[int] = None

    @property
    def is_success(self) -> bool:
        return self.status in (FetchStatus.SUCCESS, FetchStatus.CACHED)

    @property
    def is_error(self) -> bool:
        return self.status in (FetchStatus.ERROR, FetchStatus.TIMEOUT, FetchStatus.RATE_LIMITED)


@dataclass
class FetcherMetrics:
    """Metrics for tracking fetcher performance."""
    total_requests: int = 0
    successful_requests: int = 0
    failed_requests: int = 0
    cache_hits: int = 0
    total_time_ms: float = 0.0
    last_request_time: Optional[datetime] = None
    last_error: Optional[str] = None
    last_error_time: Optional[datetime] = None

    @property
    def success_rate(self) -> float:
        if self.total_requests == 0:
            return 0.0
        return self.successful_requests / self.total_requests

    @property
    def avg_request_time_ms(self) -> float:
        if self.successful_requests == 0:
            return 0.0
        return self.total_time_ms / self.successful_requests


class BaseFetcher(ABC):
    """
    Abstract base class for all DACLE data fetchers.

    Subclasses must implement:
        - SOURCE_NAME: Unique identifier for logging/metrics
        - BASE_URL: Base URL for the API
        - fetch(): Main fetch method returning FetchResult

    Optional overrides:
        - DEFAULT_TIMEOUT: Request timeout in seconds (default: 30)
        - RATE_LIMIT_PER_MINUTE: Requests per minute limit (default: None)
        - MAX_RETRIES: Number of retries on failure (default: 3)
        - CACHE_TTL_SECONDS: Cache duration (default: None, no caching)
        - _get_headers(): Custom headers for requests
        - _parse_error(): Custom error parsing
    """

    # Subclass must define these
    SOURCE_NAME: str = "base"
    BASE_URL: str = ""

    # Optional configuration
    DEFAULT_TIMEOUT: int = 30
    RATE_LIMIT_PER_MINUTE: Optional[int] = None
    MAX_RETRIES: int = 3
    RETRY_BACKOFF_FACTOR: float = 0.5
    CACHE_TTL_SECONDS: Optional[int] = None

    # Standard headers
    DEFAULT_HEADERS = {
        'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36',
        'Accept': 'application/json',
        'Accept-Language': 'en-US,en;q=0.9',
    }

    def __init__(self, cache_client=None):
        """
        Initialize the fetcher.

        Args:
            cache_client: Optional Redis cache client for caching responses
        """
        self._session: Optional[requests.Session] = None
        self._cache = cache_client
        self._metrics = FetcherMetrics()
        self._last_request_time: Optional[float] = None
        self._logger = logging.getLogger(f"{__name__}.{self.SOURCE_NAME}")

    @property
    def session(self) -> requests.Session:
        """Lazy-initialized session with retry configuration."""
        if self._session is None:
            self._session = self._create_session()
        return self._session

    def _create_session(self) -> requests.Session:
        """Create a configured requests session with retry logic."""
        session = requests.Session()

        # Configure retry strategy
        retry_strategy = Retry(
            total=self.MAX_RETRIES,
            backoff_factor=self.RETRY_BACKOFF_FACTOR,
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=["HEAD", "GET", "OPTIONS", "POST"],
        )

        adapter = HTTPAdapter(max_retries=retry_strategy)
        session.mount("https://", adapter)
        session.mount("http://", adapter)

        # Set headers
        session.headers.update(self._get_headers())

        return session

    def _get_headers(self) -> Dict[str, str]:
        """
        Get headers for requests. Override to add authentication.

        Returns:
            Dict of headers to include in requests
        """
        return self.DEFAULT_HEADERS.copy()

    def _enforce_rate_limit(self) -> None:
        """Enforce rate limiting between requests."""
        if self.RATE_LIMIT_PER_MINUTE is None:
            return

        if self._last_request_time is None:
            self._last_request_time = time.time()
            return

        min_interval = 60.0 / self.RATE_LIMIT_PER_MINUTE
        elapsed = time.time() - self._last_request_time

        if elapsed < min_interval:
            sleep_time = min_interval - elapsed
            self._logger.debug(f"Rate limiting: sleeping {sleep_time:.2f}s")
            time.sleep(sleep_time)

        self._last_request_time = time.time()

    def _get_cache_key(self, endpoint: str, params: Optional[Dict] = None) -> str:
        """Generate cache key for a request."""
        key_parts = [self.SOURCE_NAME, endpoint]
        if params:
            sorted_params = sorted(params.items())
            key_parts.append(str(sorted_params))
        return ":".join(key_parts)

    def _get_cached(self, cache_key: str) -> Optional[Any]:
        """Get cached response if available and not expired."""
        if self._cache is None or self.CACHE_TTL_SECONDS is None:
            return None

        try:
            cached = self._cache.get(cache_key)
            if cached is not None:
                self._metrics.cache_hits += 1
                self._logger.debug(f"Cache hit for {cache_key}")
                return cached
        except Exception as e:
            self._logger.warning(f"Cache read error: {e}")

        return None

    def _set_cached(self, cache_key: str, data: Any) -> None:
        """Cache a response."""
        if self._cache is None or self.CACHE_TTL_SECONDS is None:
            return

        try:
            self._cache.set(cache_key, data, ttl=self.CACHE_TTL_SECONDS)
            self._logger.debug(f"Cached {cache_key} for {self.CACHE_TTL_SECONDS}s")
        except Exception as e:
            self._logger.warning(f"Cache write error: {e}")

    def _make_request(
        self,
        endpoint: str,
        method: str = "GET",
        params: Optional[Dict] = None,
        json_data: Optional[Dict] = None,
        timeout: Optional[int] = None,
        use_cache: bool = True,
    ) -> Optional[requests.Response]:
        """
        Make an HTTP request with error handling and optional caching.

        Args:
            endpoint: API endpoint (appended to BASE_URL)
            method: HTTP method (GET, POST, etc.)
            params: Query parameters
            json_data: JSON body for POST requests
            timeout: Request timeout (defaults to DEFAULT_TIMEOUT)
            use_cache: Whether to use caching for this request

        Returns:
            Response object or None on failure
        """
        url = f"{self.BASE_URL}{endpoint}" if endpoint.startswith("/") else endpoint

        # Check cache first
        cache_key = self._get_cache_key(endpoint, params)
        if use_cache and method == "GET":
            cached = self._get_cached(cache_key)
            if cached is not None:
                # Return a mock response with cached data
                # Caller should handle cached responses
                return cached

        # Enforce rate limiting
        self._enforce_rate_limit()

        # Track metrics
        self._metrics.total_requests += 1
        self._metrics.last_request_time = datetime.utcnow()
        start_time = time.time()

        try:
            self._logger.debug(f"{method} {url}")

            response = self.session.request(
                method=method,
                url=url,
                params=params,
                json=json_data,
                timeout=timeout or self.DEFAULT_TIMEOUT,
            )

            elapsed_ms = (time.time() - start_time) * 1000
            self._metrics.total_time_ms += elapsed_ms

            # Check for rate limiting
            if response.status_code == 429:
                self._logger.warning(f"{self.SOURCE_NAME} rate limited")
                self._metrics.failed_requests += 1
                return None

            response.raise_for_status()

            self._metrics.successful_requests += 1
            self._logger.debug(f"{self.SOURCE_NAME} request successful ({elapsed_ms:.0f}ms)")

            # Cache successful response
            if use_cache and method == "GET":
                self._set_cached(cache_key, response)

            return response

        except requests.exceptions.Timeout:
            self._logger.warning(f"{self.SOURCE_NAME} request timed out")
            self._metrics.failed_requests += 1
            self._metrics.last_error = "Timeout"
            self._metrics.last_error_time = datetime.utcnow()
            return None

        except requests.exceptions.RequestException as e:
            self._logger.warning(f"{self.SOURCE_NAME} request failed: {e}")
            self._metrics.failed_requests += 1
            self._metrics.last_error = str(e)
            self._metrics.last_error_time = datetime.utcnow()
            return None

    def _success_result(
        self,
        data: Any,
        cached: bool = False,
        request_time_ms: Optional[float] = None,
    ) -> FetchResult:
        """Create a successful FetchResult."""
        return FetchResult(
            status=FetchStatus.CACHED if cached else FetchStatus.SUCCESS,
            data=data,
            source=self.SOURCE_NAME,
            cached=cached,
            request_time_ms=request_time_ms,
        )

    def _error_result(
        self,
        message: str,
        status: FetchStatus = FetchStatus.ERROR,
    ) -> FetchResult:
        """Create an error FetchResult."""
        return FetchResult(
            status=status,
            error_message=message,
            source=self.SOURCE_NAME,
        )

    def _no_data_result(self, message: str = "No data found") -> FetchResult:
        """Create a no-data FetchResult."""
        return FetchResult(
            status=FetchStatus.NO_DATA,
            error_message=message,
            source=self.SOURCE_NAME,
        )

    @abstractmethod
    def fetch(self, **kwargs) -> FetchResult:
        """
        Main fetch method. Must be implemented by subclasses.

        Returns:
            FetchResult with status and data
        """
        pass

    def get_metrics(self) -> FetcherMetrics:
        """Get current metrics for this fetcher."""
        return self._metrics

    def reset_metrics(self) -> None:
        """Reset metrics to zero."""
        self._metrics = FetcherMetrics()

    def close(self) -> None:
        """Close the session and cleanup resources."""
        if self._session is not None:
            self._session.close()
            self._session = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()


class ExchangeFetcher(BaseFetcher):
    """
    Base class for exchange-specific fetchers.

    Adds exchange-specific functionality like:
    - Standardized listing announcement parsing
    - Trading pair normalization
    - Volume filtering
    """

    EXCHANGE_NAME: str = ""
    LISTING_KEYWORDS: List[str] = [
        "new listing",
        "token listing",
        "trading starts",
        "trading opens",
        "will list",
        "to list",
    ]

    def _normalize_symbol(self, symbol: str) -> str:
        """Normalize token symbol to uppercase."""
        return symbol.upper().strip()

    def _normalize_pair(self, base: str, quote: str) -> str:
        """Normalize trading pair format."""
        return f"{self._normalize_symbol(base)}/{self._normalize_symbol(quote)}"

    def _is_listing_announcement(self, title: str) -> bool:
        """Check if announcement title indicates a new listing."""
        title_lower = title.lower()
        return any(kw in title_lower for kw in self.LISTING_KEYWORDS)

    def _create_listing_result(
        self,
        symbol: str,
        name: Optional[str] = None,
        listing_date: Optional[str] = None,
        trading_pairs: Optional[List[str]] = None,
        announcement_url: Optional[str] = None,
        confidence: str = "MEDIUM",
        **extra_fields,
    ) -> Dict[str, Any]:
        """Create a standardized listing result dict."""
        return {
            "symbol": self._normalize_symbol(symbol),
            "name": name or symbol,
            "listing_date": listing_date,
            "trading_pairs": trading_pairs or [],
            "exchange": self.EXCHANGE_NAME or self.SOURCE_NAME,
            "announcement_url": announcement_url,
            "confidence": confidence,
            "data_source": "exchange_api",
            "fetched_at": datetime.utcnow().isoformat(),
            **extra_fields,
        }


class TokenDataFetcher(BaseFetcher):
    """
    Base class for token data fetchers (price, volume, tokenomics).

    Adds token-specific functionality like:
    - Price normalization
    - Market cap calculations
    - Supply parsing
    """

    def _parse_price(self, value: Any) -> Optional[float]:
        """Parse price value to float."""
        if value is None:
            return None
        try:
            return float(value)
        except (ValueError, TypeError):
            return None

    def _parse_market_cap(self, value: Any) -> Optional[float]:
        """Parse market cap value to float."""
        return self._parse_price(value)

    def _parse_supply(self, value: Any) -> Optional[float]:
        """Parse supply value to float."""
        return self._parse_price(value)

    def _calculate_fdv(
        self,
        price: Optional[float],
        max_supply: Optional[float],
        total_supply: Optional[float] = None,
    ) -> Optional[float]:
        """Calculate fully diluted valuation."""
        if price is None:
            return None

        supply = max_supply or total_supply
        if supply is None or supply <= 0:
            return None

        return price * supply

    def _calculate_float_percent(
        self,
        circulating_supply: Optional[float],
        total_supply: Optional[float],
    ) -> Optional[float]:
        """Calculate float percentage (circulating / total)."""
        if circulating_supply is None or total_supply is None:
            return None
        if total_supply <= 0:
            return None

        return (circulating_supply / total_supply) * 100

    def _create_token_result(
        self,
        symbol: str,
        price_usd: Optional[float] = None,
        market_cap: Optional[float] = None,
        fdv: Optional[float] = None,
        volume_24h: Optional[float] = None,
        circulating_supply: Optional[float] = None,
        total_supply: Optional[float] = None,
        max_supply: Optional[float] = None,
        **extra_fields,
    ) -> Dict[str, Any]:
        """Create a standardized token data result dict."""
        return {
            "symbol": symbol.upper(),
            "price_usd": price_usd,
            "market_cap": market_cap,
            "fdv": fdv,
            "volume_24h": volume_24h,
            "circulating_supply": circulating_supply,
            "total_supply": total_supply,
            "max_supply": max_supply,
            "float_percent": self._calculate_float_percent(circulating_supply, total_supply),
            "fdv_mc_ratio": (fdv / market_cap) if fdv and market_cap and market_cap > 0 else None,
            "fetched_at": datetime.utcnow().isoformat(),
            **extra_fields,
        }
