"""
Futures Movers Fetcher - Binance Futures API (FREE)

Session 382: Automates David's Blofin Futures "24h % change" screening workflow.
Fetches ALL USDT-M perpetual futures in a single API call, filters by move size
and volume, returns top movers sorted by absolute percentage change.

API: https://fapi.binance.com/fapi/v1/ticker/24hr
Rate Limit: 1200/minute (very generous)
Cost: $0/month
Auth: None required

Use Cases:
- Find tokens that dumped >10% (SHORT_CONTINUATION or LONG_RECOVERY candidates)
- Find tokens that pumped >10% (LONG_CONTINUATION or SHORT_REVERSAL candidates)
- Pre-filter for Discovery TA enrichment
"""

import json
import logging
import requests
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import List, Optional, Set

logger = logging.getLogger(__name__)


@dataclass
class FuturesMover:
    """A futures pair with significant 24h price movement."""
    symbol: str  # e.g., "ARCUSDT"
    clean_symbol: str  # e.g., "ARC"
    price: float
    change_24h_pct: float
    volume_24h_usd: float  # quoteVolume in USDT
    high_24h: float
    low_24h: float
    trade_count: int
    direction: str  # "DUMP" or "PUMP"
    fetched_at: str = ""

    def __post_init__(self):
        if not self.fetched_at:
            self.fetched_at = datetime.now(timezone.utc).isoformat()

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "clean_symbol": self.clean_symbol,
            "price": self.price,
            "change_24h_pct": round(self.change_24h_pct, 2),
            "volume_24h_usd": round(self.volume_24h_usd, 2),
            "high_24h": self.high_24h,
            "low_24h": self.low_24h,
            "trade_count": self.trade_count,
            "direction": self.direction,
            "fetched_at": self.fetched_at,
        }


class FuturesMoversFetcher:
    """
    Fetches top movers from Binance Futures API.

    Uses the same base URL and pattern as FundingRateFetcher.
    Single API call returns all 400+ USDT-M perpetual futures with 24h stats.
    """

    BASE_URL = "https://fapi.binance.com"
    BLOFIN_API = "https://openapi.blofin.com/api/v1/market/instruments"
    TIMEOUT = 30

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            "Accept": "application/json",
            "User-Agent": "DACLE-Futures-Movers/1.0",
        })
        self._blofin_symbols: Optional[Set[str]] = None

    def _get_blofin_symbols(self) -> Set[str]:
        """
        Fetch all live Blofin SWAP (perpetual futures) symbols.

        Cached after first call. Returns set of base currencies (e.g., {"BTC", "ETH", ...}).
        Falls back to empty set on error (disables filter, returns all Binance movers).
        """
        if self._blofin_symbols is not None:
            return self._blofin_symbols

        try:
            req = urllib.request.Request(
                f"{self.BLOFIN_API}?instType=SWAP",
                headers={"User-Agent": "DACLE-Bot/1.0"},
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read())

            instruments = data.get("data", [])
            self._blofin_symbols = {
                i["baseCurrency"] for i in instruments if i.get("state") == "live"
            }
            logger.info(f"Blofin filter: {len(self._blofin_symbols)} live SWAP instruments")
            return self._blofin_symbols

        except Exception as e:
            logger.warning(f"Blofin instrument fetch failed ({e}), filter disabled")
            self._blofin_symbols = set()
            return self._blofin_symbols

    def get_all_tickers(self) -> list:
        """
        Fetch all USDT-M futures 24h ticker data.

        Returns raw API response list (400+ entries).
        """
        try:
            url = f"{self.BASE_URL}/fapi/v1/ticker/24hr"
            response = self.session.get(url, timeout=self.TIMEOUT)
            response.raise_for_status()
            data = response.json()
            logger.info(f"Binance Futures: fetched {len(data)} tickers")
            return data
        except requests.RequestException as e:
            logger.error(f"Binance Futures ticker fetch failed: {e}")
            return []

    def get_top_movers(
        self,
        min_change_pct: float = 10.0,
        min_volume_usd: float = 5_000_000,
        limit: int = 30,
    ) -> List[FuturesMover]:
        """
        Get top movers filtered by absolute 24h change and volume.

        Args:
            min_change_pct: Minimum absolute 24h change percentage (default 10%)
            min_volume_usd: Minimum 24h quote volume in USD (default $5M)
            limit: Maximum number of movers to return (default 30)

        Returns:
            List of FuturesMover sorted by absolute change descending
        """
        raw_tickers = self.get_all_tickers()
        if not raw_tickers:
            return []

        # Fetch Blofin-listed symbols to filter (David trades on Blofin)
        blofin_symbols = self._get_blofin_symbols()

        now = datetime.now(timezone.utc).isoformat()
        movers = []
        skipped_not_on_blofin = 0

        for ticker in raw_tickers:
            symbol = ticker.get("symbol", "")

            # Only USDT perpetuals
            if not symbol.endswith("USDT"):
                continue

            # Skip stablecoin pairs
            clean = symbol.replace("USDT", "")
            if clean in ("USDC", "BUSD", "DAI", "TUSD", "FDUSD", "USDD"):
                continue

            # Only include tokens available on Blofin (where David trades)
            if blofin_symbols and clean not in blofin_symbols:
                skipped_not_on_blofin += 1
                continue

            try:
                change_pct = float(ticker.get("priceChangePercent") or 0)
                volume = float(ticker.get("quoteVolume") or 0)
            except (TypeError, ValueError):
                continue

            # Apply filters
            if abs(change_pct) < min_change_pct:
                continue
            if volume < min_volume_usd:
                continue

            try:
                mover = FuturesMover(
                    symbol=symbol,
                    clean_symbol=clean,
                    price=float(ticker.get("lastPrice") or 0),
                    change_24h_pct=change_pct,
                    volume_24h_usd=volume,
                    high_24h=float(ticker.get("highPrice") or 0),
                    low_24h=float(ticker.get("lowPrice") or 0),
                    trade_count=int(ticker.get("count") or 0),
                    direction="DUMP" if change_pct < 0 else "PUMP",
                    fetched_at=now,
                )
                movers.append(mover)
            except (TypeError, ValueError) as e:
                logger.warning(f"Failed to parse ticker {symbol}: {e}")
                continue

        # Sort by absolute change descending
        movers.sort(key=lambda m: abs(m.change_24h_pct), reverse=True)

        blofin_msg = f", {skipped_not_on_blofin} skipped (not on Blofin)" if blofin_symbols else ""
        logger.info(
            f"Futures movers: {len(movers)} pairs with |change| >= {min_change_pct}% "
            f"and volume >= ${min_volume_usd/1e6:.0f}M (from {len(raw_tickers)} total{blofin_msg})"
        )

        return movers[:limit]

    def get_dumpers(self, min_dump_pct: float = 10.0, **kwargs) -> List[FuturesMover]:
        """Get tokens that dumped significantly (negative change)."""
        movers = self.get_top_movers(min_change_pct=min_dump_pct, **kwargs)
        return [m for m in movers if m.direction == "DUMP"]

    def get_pumpers(self, min_pump_pct: float = 10.0, **kwargs) -> List[FuturesMover]:
        """Get tokens that pumped significantly (positive change)."""
        movers = self.get_top_movers(min_change_pct=min_pump_pct, **kwargs)
        return [m for m in movers if m.direction == "PUMP"]


def main():
    """Test the Futures Movers fetcher."""
    fetcher = FuturesMoversFetcher()

    print("=" * 70)
    print("Binance Futures Movers Scanner")
    print("=" * 70)

    movers = fetcher.get_top_movers(min_change_pct=8.0, min_volume_usd=2_000_000, limit=20)

    if not movers:
        print("\nNo significant movers found (|change| >= 8%, vol >= $2M)")
        return

    dumpers = [m for m in movers if m.direction == "DUMP"]
    pumpers = [m for m in movers if m.direction == "PUMP"]

    if dumpers:
        print(f"\n📉 TOP DUMPERS ({len(dumpers)}):")
        for m in dumpers[:10]:
            print(
                f"  {m.clean_symbol:10s} {m.change_24h_pct:+7.2f}%  "
                f"${m.price:<12.6f}  Vol: ${m.volume_24h_usd/1e6:>8.1f}M  "
                f"Trades: {m.trade_count:,}"
            )

    if pumpers:
        print(f"\n📈 TOP PUMPERS ({len(pumpers)}):")
        for m in pumpers[:10]:
            print(
                f"  {m.clean_symbol:10s} {m.change_24h_pct:+7.2f}%  "
                f"${m.price:<12.6f}  Vol: ${m.volume_24h_usd/1e6:>8.1f}M  "
                f"Trades: {m.trade_count:,}"
            )


if __name__ == "__main__":
    main()
