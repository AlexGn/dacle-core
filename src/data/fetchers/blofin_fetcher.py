"""
Blofin Exchange Fetcher - Session 383

Public-only CCXT fetcher for Blofin exchange. No auth needed.
Blofin lists tokens aggressively, often before larger exchanges,
making it a valuable discovery source.

Existing Blofin usage in codebase:
- blofin_trade_sync.py: Auth CCXT for trade sync (separate from this)
- futures_movers_fetcher.py: REST call for live SWAP instruments

This fetcher uses public CCXT for:
- New listing discovery (volume-based)
- Funding rates (L051 coverage for non-Binance tokens)
- Open interest (fills 5% OI/Order Book conviction weight)
- OHLCV candles (Quick TA fallback for non-Binance tokens)

API: Blofin public endpoints via CCXT
Rate Limit: Generous (public endpoints)
Cost: $0/month
Auth: None required
"""

import json
import logging
import urllib.request
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set

import ccxt

logger = logging.getLogger(__name__)

# Stablecoins and majors to exclude from discovery results
EXCLUDED_SYMBOLS = {
    "USDT", "USDC", "BUSD", "DAI", "TUSD", "FDUSD", "USDP",
    "BTC", "ETH", "BNB", "SOL", "XRP",
}

# Minimum 24h volume in USDT to qualify as a discovery signal
MIN_VOLUME_USD = 50_000


class BlofinFetcher:
    """
    Public CCXT fetcher for Blofin exchange.

    Uses unauthenticated ccxt.blofin() instance — completely separate
    from the authenticated instance in blofin_trade_sync.py.
    """

    INSTRUMENTS_URL = "https://openapi.blofin.com/api/v1/market/instruments"
    TICKERS_URL = "https://openapi.blofin.com/api/v1/market/tickers"

    def __init__(self):
        self._exchange: Optional[ccxt.blofin] = None
        self._blofin_symbols: Optional[Set[str]] = None

    @property
    def exchange(self) -> ccxt.blofin:
        """Lazy-init public CCXT exchange instance."""
        if self._exchange is None:
            self._exchange = ccxt.blofin({
                "enableRateLimit": True,
                "timeout": 15000,
            })
        return self._exchange

    def get_blofin_symbols(self) -> Set[str]:
        """
        Fetch all live Blofin SWAP (perpetual futures) base currencies.

        Reuses pattern from futures_movers_fetcher.py:83-111 (direct REST).
        Cached after first call.

        Returns:
            Set of base currency symbols, e.g. {"BTC", "ETH", "MONAD", ...}
        """
        if self._blofin_symbols is not None:
            return self._blofin_symbols

        try:
            req = urllib.request.Request(
                f"{self.INSTRUMENTS_URL}?instType=SWAP",
                headers={"User-Agent": "DACLE-Bot/1.0"},
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read())

            instruments = data.get("data", [])
            self._blofin_symbols = {
                i["baseCurrency"]
                for i in instruments
                if i.get("state") == "live"
            }
            logger.info(f"Blofin: {len(self._blofin_symbols)} live SWAP instruments")
            return self._blofin_symbols

        except Exception as e:
            logger.warning(f"Blofin instrument fetch failed: {e}")
            self._blofin_symbols = set()
            return self._blofin_symbols

    def fetch_new_listings(self, days_back: int = 7) -> List[Dict[str, Any]]:
        """
        Discover tokens listed on Blofin via volume-based filtering.

        Loads all SWAP markets via CCXT, fetches tickers, filters by
        volume threshold, excludes stablecoins/majors.

        Args:
            days_back: Not used (Blofin has no listing date API).
                       Kept for interface consistency with other fetchers.

        Returns:
            List of standardized discovery dicts.
        """
        # Prefer direct Blofin REST ticker feed in real runtime for better
        # symbol/volume consistency. Skip this path in unit tests where
        # exchange is usually injected/mocked.
        if self._exchange is None:
            rest_results = self._fetch_new_listings_from_rest()
            if rest_results:
                return rest_results

        try:
            self.exchange.load_markets()
            tickers = self.exchange.fetch_tickers()

            results = []
            for symbol, ticker in tickers.items():
                # Only SWAP perpetuals with USDT settlement
                if "/USDT" not in symbol and ":USDT" not in symbol:
                    continue

                # Extract base currency (e.g., "MONAD" from "MONAD/USDT:USDT")
                base = symbol.split("/")[0]

                if base in EXCLUDED_SYMBOLS:
                    continue

                volume_usd = self._extract_volume_usd_from_ccxt_ticker(ticker)
                if volume_usd < MIN_VOLUME_USD:
                    continue

                results.append({
                    "symbol": base,
                    "name": base,
                    "listing_date": None,
                    "trading_pairs": [f"{base}/USDT"],
                    "exchange": "blofin",
                    "announcement_url": "https://blofin.com",
                    "confidence": "MEDIUM",
                    "data_source": "blofin_ccxt",
                    "volume_24h_usd": volume_usd,
                    "price": float(ticker.get("last") or 0),
                    "change_24h_pct": self._extract_change_pct_from_ccxt_ticker(ticker),
                })

            # Sort by volume descending
            results.sort(key=lambda x: x["volume_24h_usd"], reverse=True)

            logger.info(f"Blofin: {len(results)} tokens above ${MIN_VOLUME_USD:,} volume")
            return results

        except Exception as e:
            logger.error(f"Blofin fetch_new_listings failed: {e}")
            return []

    def _fetch_new_listings_from_rest(self) -> List[Dict[str, Any]]:
        """Fetch high-volume SWAP movers from Blofin REST tickers endpoint."""
        try:
            # Build live instrument map to ensure we're only using live USDT SWAPs.
            req_inst = urllib.request.Request(
                f"{self.INSTRUMENTS_URL}?instType=SWAP",
                headers={"User-Agent": "DACLE-Bot/1.0"},
            )
            with urllib.request.urlopen(req_inst, timeout=10) as resp:
                inst_data = json.loads(resp.read())
            instruments = inst_data.get("data", [])
            live_usdt_inst_ids = {
                i.get("instId")
                for i in instruments
                if i.get("state") == "live" and i.get("quoteCurrency") == "USDT"
            }

            req_tick = urllib.request.Request(
                f"{self.TICKERS_URL}?instType=SWAP",
                headers={"User-Agent": "DACLE-Bot/1.0"},
            )
            with urllib.request.urlopen(req_tick, timeout=10) as resp:
                tick_data = json.loads(resp.read())
            rows = tick_data.get("data", [])

            results: List[Dict[str, Any]] = []
            for row in rows:
                inst_id = row.get("instId")
                if not inst_id or inst_id not in live_usdt_inst_ids:
                    continue
                base = inst_id.split("-")[0]
                if base in EXCLUDED_SYMBOLS:
                    continue

                price = float(row.get("last") or 0)
                if price <= 0:
                    continue

                # volCurrency24h is base-asset volume; convert to quote notional.
                base_vol_24h = float(row.get("volCurrency24h") or 0)
                vol_24h_usd = base_vol_24h * price
                if vol_24h_usd < MIN_VOLUME_USD:
                    continue

                open_24h = float(row.get("open24h") or 0)
                change_pct = ((price - open_24h) / open_24h * 100) if open_24h > 0 else 0.0

                results.append(
                    {
                        "symbol": base,
                        "name": base,
                        "listing_date": None,
                        "trading_pairs": [f"{base}/USDT"],
                        "exchange": "blofin",
                        "announcement_url": "https://blofin.com",
                        "confidence": "MEDIUM",
                        "data_source": "blofin_rest",
                        "volume_24h_usd": vol_24h_usd,
                        "price": price,
                        "change_24h_pct": change_pct,
                    }
                )

            results.sort(key=lambda x: x["volume_24h_usd"], reverse=True)
            logger.info(f"Blofin REST: {len(results)} USDT SWAP tokens above ${MIN_VOLUME_USD:,} volume")
            return results
        except Exception as e:
            logger.warning(f"Blofin REST listings fetch failed: {e}")
            return []

    def _extract_volume_usd_from_ccxt_ticker(self, ticker: Dict[str, Any]) -> float:
        """
        Best-effort 24h notional volume extraction across CCXT variants.
        """
        quote_volume = float(ticker.get("quoteVolume") or 0)
        if quote_volume > 0:
            return quote_volume

        last = float(ticker.get("last") or 0)
        base_volume = float(ticker.get("baseVolume") or 0)
        if last > 0 and base_volume > 0:
            return last * base_volume

        info = ticker.get("info") or {}
        if isinstance(info, dict):
            info_quote = float(info.get("vol24h") or info.get("turnover24h") or 0)
            if info_quote > 0:
                # Many exchanges expose contract volume; prefer base-volume conversion when present.
                base_24h = float(info.get("volCurrency24h") or 0)
                if last > 0 and base_24h > 0:
                    return last * base_24h
                return info_quote
            info_base = float(info.get("volCurrency24h") or 0)
            if last > 0 and info_base > 0:
                return last * info_base
        return 0.0

    def _extract_change_pct_from_ccxt_ticker(self, ticker: Dict[str, Any]) -> float:
        """Best-effort 24h percentage change extraction."""
        pct = ticker.get("percentage")
        if pct is not None:
            return float(pct)
        info = ticker.get("info") or {}
        if isinstance(info, dict):
            last = float(ticker.get("last") or info.get("last") or 0)
            open_24h = float(info.get("open24h") or 0)
            if last > 0 and open_24h > 0:
                return (last - open_24h) / open_24h * 100
        return 0.0

    def fetch_funding_rates(self, limit: int = 50) -> List[Dict[str, Any]]:
        """
        Fetch current funding rates for all Blofin perpetuals.

        Fills L051 gap for tokens not on Binance futures.

        Args:
            limit: Max results to return (sorted by absolute rate).

        Returns:
            List of funding rate dicts, sorted by |rate| descending.
        """
        try:
            rates = self.exchange.fetch_funding_rates()

            results = []
            for symbol, rate_data in rates.items():
                if ":USDT" not in symbol:
                    continue

                base = symbol.split("/")[0]
                rate = float(rate_data.get("fundingRate") or 0)

                results.append({
                    "symbol": base,
                    "pair": f"{base}USDT",
                    "funding_rate": rate,
                    "funding_rate_pct": rate * 100,
                    "mark_price": float(rate_data.get("markPrice") or 0),
                    "next_funding_time": rate_data.get("fundingTimestamp"),
                    "data_source": "blofin_funding",
                    "fetched_at": datetime.now(timezone.utc).isoformat(),
                })

            # Sort by absolute rate
            results.sort(key=lambda x: abs(x["funding_rate"]), reverse=True)

            logger.info(f"Blofin: {len(results)} funding rates fetched")
            return results[:limit]

        except Exception as e:
            logger.error(f"Blofin fetch_funding_rates failed: {e}")
            return []

    def fetch_open_interest(self, symbol: str) -> Optional[Dict[str, Any]]:
        """
        Fetch open interest for a specific symbol.

        Used as fallback when Binance/Bybit/OKX OI data is unavailable.
        Fills the OI/Order Book conviction component (5% weight).

        Args:
            symbol: Base currency symbol (e.g., "MONAD").

        Returns:
            OI dict or None if unavailable.
        """
        try:
            pair = f"{symbol.upper()}/USDT:USDT"
            oi_data = self.exchange.fetch_open_interest(pair)

            if not oi_data:
                return None

            return {
                "symbol": symbol.upper(),
                "pair": pair,
                "open_interest": float(oi_data.get("openInterestAmount") or 0),
                "open_interest_value": float(oi_data.get("openInterestValue") or 0),
                "timestamp": oi_data.get("timestamp"),
                "data_source": "blofin_oi",
                "fetched_at": datetime.now(timezone.utc).isoformat(),
            }

        except Exception as e:
            logger.debug(f"Blofin OI for {symbol} not available: {e}")
            return None

    def fetch_ohlcv(
        self,
        symbol: str,
        timeframe: str = "4h",
        limit: int = 200,
    ) -> list:
        """
        Fetch OHLCV candles for a symbol from Blofin.

        Returns CCXT standard format: [[timestamp, O, H, L, C, V], ...]
        Used as Quick TA fallback when Binance has no data.

        Args:
            symbol: Base currency symbol (e.g., "MONAD").
            timeframe: Candle timeframe (e.g., "4h", "1h", "1d").
            limit: Number of candles.

        Returns:
            List of OHLCV candles in CCXT format, or empty list.
        """
        pair = f"{symbol.upper()}/USDT:USDT"
        try:
            ohlcv = self.exchange.fetch_ohlcv(pair, timeframe, limit=limit)
            if ohlcv and len(ohlcv) > 0:
                logger.info(f"Blofin: Fetched {len(ohlcv)} {timeframe} candles for {pair}")
                return ohlcv
        except Exception as e:
            logger.debug(f"Blofin OHLCV {pair} not available: {e}")

        return []

    def validate_token(self, symbol: str) -> bool:
        """
        Check if a token has a live SWAP instrument on Blofin.

        Args:
            symbol: Token symbol (e.g., "MONAD").

        Returns:
            True if listed as a live SWAP on Blofin.
        """
        symbols = self.get_blofin_symbols()
        return symbol.upper() in symbols


# CLI Testing
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    fetcher = BlofinFetcher()

    print("\n" + "=" * 60)
    print("Blofin Exchange Fetcher - Session 383")
    print("=" * 60)

    # Test symbols
    symbols = fetcher.get_blofin_symbols()
    print(f"\nBlofin live SWAP instruments: {len(symbols)}")

    # Test new listings
    print("\nFetching high-volume tokens...")
    listings = fetcher.fetch_new_listings()
    print(f"Found {len(listings)} tokens above ${MIN_VOLUME_USD:,} volume")
    for t in listings[:10]:
        print(f"  {t['symbol']:10s} Vol: ${t['volume_24h_usd']:>12,.0f}  {t['change_24h_pct']:+.1f}%")

    # Test funding rates
    print("\nFetching funding rates...")
    funding = fetcher.fetch_funding_rates(limit=10)
    for f in funding[:5]:
        sign = "+" if f["funding_rate_pct"] >= 0 else ""
        print(f"  {f['symbol']:10s} {sign}{f['funding_rate_pct']:.4f}%")

    # Test OI
    if listings:
        test_sym = listings[0]["symbol"]
        print(f"\nFetching OI for {test_sym}...")
        oi = fetcher.fetch_open_interest(test_sym)
        if oi:
            print(f"  OI value: ${oi['open_interest_value']:,.0f}")
        else:
            print(f"  No OI data for {test_sym}")
