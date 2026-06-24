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

from dacle_core.data.fetchers.blofin_fetcher import BlofinFetcher

logger = logging.getLogger(__name__)

# Blofin notional sizing is structurally lower than Binance on many alt pairs.
# When native-first returns empty at high volume thresholds, retry once with a
# safer floor before proxy fallback.
BLOFIN_NATIVE_RELAXED_MIN_VOLUME_USD = 1_000_000
BLOFIN_NATIVE_MIN_BOARD_SIZE = 5
BLOFIN_NATIVE_RELAXED_VOLUME_STEPS = (2_000_000, 1_000_000, 500_000)

# Session 442: Gem Exception Logic
# Allows high-momentum tokens to bypass the $5M absolute volume floor
MIN_GEM_VOLUME_USD = 100_000
GEM_RVOL_THRESHOLD = 5.0  # 5x volume spike
GEM_MIN_CHANGE_PCT = 15.0  # Must be a significant move


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
    volume_ratio: float = 1.0  # RVOL (recent/avg)
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
            "volume_ratio": round(self.volume_ratio, 2),
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
        self._blofin_fetcher: Optional[BlofinFetcher] = None

    @property
    def blofin_fetcher(self) -> BlofinFetcher:
        if self._blofin_fetcher is None:
            self._blofin_fetcher = BlofinFetcher()
        return self._blofin_fetcher

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
        min_trade_count: int = 0,
        limit: int = 30,
        source_mode: str = "blofin_native_first",
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
        if source_mode in ("blofin_native", "blofin_native_first"):
            target_count = min(BLOFIN_NATIVE_MIN_BOARD_SIZE, max(limit, 1))
            blofin_movers = self._get_top_movers_blofin_native_with_expansion(
                min_change_pct=min_change_pct,
                min_volume_usd=min_volume_usd,
                min_trade_count=min_trade_count,
                limit=limit,
                target_count=target_count,
            )
            if blofin_movers:
                return blofin_movers
            if source_mode == "blofin_native":
                logger.warning("Blofin-native mode produced no movers.")
                return []

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

            # Apply filters (with Gem Exception fallback)
            is_gem_candidate = (abs(change_pct) >= GEM_MIN_CHANGE_PCT and volume >= MIN_GEM_VOLUME_USD)
            
            if abs(change_pct) < min_change_pct and not is_gem_candidate:
                continue
            
            # Standard volume filter
            if volume < min_volume_usd and not is_gem_candidate:
                continue
                
            if min_trade_count and int(ticker.get("count") or 0) < min_trade_count:
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

        # Session 442: Enrich candidates with RVOL to find hidden gems
        if movers:
            movers = self._enrich_movers_with_rvol(movers)
            
            # Final filter: If volume < min_volume_usd, MUST have RVOL >= GEM_RVOL_THRESHOLD
            movers = [
                m for m in movers 
                if m.volume_24h_usd >= min_volume_usd or m.volume_ratio >= GEM_RVOL_THRESHOLD
            ]

        # Sort by absolute change descending
        movers.sort(key=lambda m: abs(m.change_24h_pct), reverse=True)

        blofin_msg = f", {skipped_not_on_blofin} skipped (not on Blofin)" if blofin_symbols else ""
        logger.info(
            f"Futures movers: {len(movers)} pairs with |change| >= {min_change_pct}% "
            f"and volume >= ${min_volume_usd/1e6:.0f}M (from {len(raw_tickers)} total{blofin_msg})"
        )

        return movers[:limit]

    def _get_top_movers_blofin_native_with_expansion(
        self,
        min_change_pct: float,
        min_volume_usd: float,
        min_trade_count: int,
        limit: int,
        target_count: int,
    ) -> List[FuturesMover]:
        """Fetch Blofin-native movers and relax volume floor to fill a minimum board."""
        by_symbol = {}

        def _merge(candidates: List[FuturesMover]) -> None:
            for mover in candidates:
                existing = by_symbol.get(mover.symbol)
                if existing is None or abs(mover.change_24h_pct) > abs(existing.change_24h_pct):
                    by_symbol[mover.symbol] = mover

        strict = self._get_top_movers_blofin_native(
            min_change_pct=min_change_pct,
            min_volume_usd=min_volume_usd,
            min_trade_count=min_trade_count,
            limit=limit,
        )
        _merge(strict)
        if len(by_symbol) >= target_count:
            merged = list(by_symbol.values())
            merged.sort(key=lambda m: abs(m.change_24h_pct), reverse=True)
            return merged[:limit]

        for floor in BLOFIN_NATIVE_RELAXED_VOLUME_STEPS:
            if floor >= min_volume_usd:
                continue
            if len(by_symbol) >= target_count:
                break

            logger.info(
                "Blofin-native board fill: retry with volume floor $%.1fM (have %s, target %s)",
                floor / 1e6,
                len(by_symbol),
                target_count,
            )
            expanded = self._get_top_movers_blofin_native(
                min_change_pct=min_change_pct,
                min_volume_usd=floor,
                min_trade_count=min_trade_count,
                limit=limit,
            )
            _merge(expanded)

        merged = list(by_symbol.values())
        merged.sort(key=lambda m: abs(m.change_24h_pct), reverse=True)
        if merged and len(merged) < target_count:
            logger.info(
                "Blofin-native remained below target board size (%s/%s) after relaxed floors; returning native-only set",
                len(merged),
                target_count,
            )
        return merged[:limit]

    def _get_top_movers_blofin_native(
        self,
        min_change_pct: float = 10.0,
        min_volume_usd: float = 5_000_000,
        min_trade_count: int = 0,
        limit: int = 30,
    ) -> List[FuturesMover]:
        """
        Blofin-native mover extraction.

        Uses public Blofin data (via BlofinFetcher) as primary source and keeps the
        same FuturesMover contract used by downstream scoring.
        """
        try:
            listings = self.blofin_fetcher.fetch_new_listings()
        except Exception as e:
            logger.warning(f"Blofin-native mover fetch failed: {e}")
            return []

        now = datetime.now(timezone.utc).isoformat()
        movers: List[FuturesMover] = []
        for row in listings:
            symbol = str(row.get("symbol") or "").upper().strip()
            if not symbol:
                continue
            change_pct = float(row.get("change_24h_pct") or 0.0)
            volume = float(row.get("volume_24h_usd") or 0.0)
            trade_count = int(row.get("trade_count") or 0)
            if abs(change_pct) < min_change_pct:
                continue
            if volume < min_volume_usd:
                continue
            if min_trade_count and trade_count < min_trade_count:
                continue

            price = float(row.get("price") or 0.0)
            movers.append(
                FuturesMover(
                    symbol=f"{symbol}USDT",
                    clean_symbol=symbol,
                    price=price,
                    change_24h_pct=change_pct,
                    volume_24h_usd=volume,
                    high_24h=price,
                    low_24h=price,
                    trade_count=trade_count,
                    direction="DUMP" if change_pct < 0 else "PUMP",
                    fetched_at=now,
                )
            )

        movers.sort(key=lambda m: abs(m.change_24h_pct), reverse=True)
        logger.info(
            "Blofin-native futures movers: %s pairs with |change| >= %.1f%% and volume >= $%.1fM",
            len(movers),
            min_change_pct,
            min_volume_usd / 1e6,
        )
        return movers[:limit]

    def _enrich_movers_with_rvol(self, movers: List[FuturesMover]) -> List[FuturesMover]:
        """Fetch 4h OHLCV for candidates and calculate RVOL to find 'Gems'."""
        if not movers:
            return []

        enriched = []
        # Limit enrichment to top 15 candidates to avoid rate limits
        for mover in movers[:15]:
            try:
                # Use Blofin for OHLCV as many gems are there first
                ohlcv = self.blofin_fetcher.fetch_ohlcv(mover.clean_symbol, timeframe="4h", limit=30)
                if not ohlcv or len(ohlcv) < 10:
                    enriched.append(mover)
                    continue

                # Calculate RVOL: recent volume / avg of last 20
                volumes = [c[5] for c in ohlcv]
                avg_vol = sum(volumes[:-1][-20:]) / min(len(volumes) - 1, 20)
                recent_vol = volumes[-1]
                rvol = recent_vol / avg_vol if avg_vol > 0 else 1.0
                
                mover.volume_ratio = rvol
                enriched.append(mover)
                if rvol >= GEM_RVOL_THRESHOLD:
                    logger.info(f"💎 GEM DETECTED: {mover.clean_symbol} RVOL={rvol:.1f}x")
            except Exception as e:
                logger.debug(f"RVOL enrichment failed for {mover.clean_symbol}: {e}")
                enriched.append(mover)
        
        # Add remaining unenriched
        if len(movers) > 15:
            enriched.extend(movers[15:])
            
        return enriched

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
