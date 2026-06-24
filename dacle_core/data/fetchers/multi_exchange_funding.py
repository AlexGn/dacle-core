"""
Multi-Exchange Funding Rate Aggregator — Session 383

Wraps FundingRateFetcher (Binance) and BlofinFetcher to provide
unified funding rate data across both exchanges.

Priority logic:
- Binance rates take priority for overlapping symbols (more liquid)
- Blofin-only tokens get appended (fills L051 gap for non-Binance tokens)
- Graceful degradation: if either source fails, return what's available

Uses existing classify_funding_risk() from FundingRateFetcher for
consistent L051 risk classification across all sources.
"""

import logging
from datetime import datetime, timezone
from typing import Dict, List, Optional

from dacle_core.data.fetchers.blofin_fetcher import BlofinFetcher
from dacle_core.data.fetchers.funding_rate_fetcher import FundingRateFetcher

logger = logging.getLogger(__name__)


class MultiExchangeFunding:
    """
    Aggregates funding rates from Binance and Blofin into a single
    unified view, with Binance priority for overlapping symbols.

    Binance covers ~300 USDT perpetuals (major + mid-cap).
    Blofin lists tokens aggressively and often has funding data
    for tokens not yet on Binance futures.

    Usage:
        mef = MultiExchangeFunding()
        rates = mef.get_all_funding_rates(limit=50)
        btc = mef.get_funding_for_symbol("BTC")
        stats = mef.get_coverage_stats()
    """

    def __init__(self):
        self._binance = FundingRateFetcher()
        self._blofin = BlofinFetcher()
        # Cache for the last fetch (avoid double-fetching in same call chain)
        self._last_binance: Optional[List[Dict]] = None
        self._last_blofin: Optional[List[Dict]] = None
        self._last_merged: Optional[List[Dict]] = None

    def get_all_funding_rates(self, limit: int = 100) -> List[Dict]:
        """
        Get unified funding rates from both exchanges.

        Binance rates override Blofin for the same symbol.
        Blofin-only tokens are appended at the end.
        Result is sorted by absolute funding rate (most extreme first).

        Args:
            limit: Maximum number of results to return.

        Returns:
            Unified list of funding rate dicts sorted by |rate| descending.
            Each dict contains at minimum:
                symbol, pair, funding_rate, funding_rate_pct,
                mark_price, data_source, fetched_at
        """
        # Fetch from both sources independently
        binance_rates = self._fetch_binance()
        blofin_rates = self._fetch_blofin()

        # Index Binance rates by symbol for fast lookup
        binance_by_symbol: Dict[str, Dict] = {}
        for rate in binance_rates:
            binance_by_symbol[rate["symbol"]] = rate

        # Start with all Binance rates (they take priority)
        merged: Dict[str, Dict] = dict(binance_by_symbol)

        # Append Blofin-only tokens (skip overlapping symbols)
        blofin_only_count = 0
        for rate in blofin_rates:
            sym = rate["symbol"]
            if sym not in merged:
                merged[sym] = rate
                blofin_only_count += 1

        # Sort by absolute funding rate (most extreme first)
        result = sorted(
            merged.values(),
            key=lambda x: abs(x.get("funding_rate", 0)),
            reverse=True,
        )

        # Cache for coverage stats
        self._last_binance = binance_rates
        self._last_blofin = blofin_rates
        self._last_merged = result

        logger.info(
            f"MultiExchangeFunding: {len(binance_rates)} Binance + "
            f"{blofin_only_count} Blofin-only = {len(result)} total unique symbols"
        )

        return result[:limit]

    def get_funding_for_symbol(self, symbol: str) -> Optional[Dict]:
        """
        Get best available funding data for a specific token.

        Priority: Binance (more liquid) > Blofin (fallback).

        Args:
            symbol: Token symbol (e.g., "BTC", "MONAD").

        Returns:
            Funding rate dict with risk classification, or None.
        """
        symbol = symbol.upper()

        # Try Binance first (preferred source)
        binance_rates = self._fetch_binance()
        for rate in binance_rates:
            if rate["symbol"] == symbol:
                rate["risk"] = self._binance.classify_funding_risk(rate["funding_rate"])
                return rate

        # Fall back to Blofin
        blofin_rates = self._fetch_blofin()
        for rate in blofin_rates:
            if rate["symbol"] == symbol:
                rate["risk"] = self._binance.classify_funding_risk(rate["funding_rate"])
                return rate

        logger.debug(f"MultiExchangeFunding: No funding data for {symbol}")
        return None

    def get_coverage_stats(self) -> Dict:
        """
        Return coverage statistics across both exchanges.

        Returns:
            Dict with keys:
                binance_count: Number of symbols with Binance funding data.
                blofin_count: Number of symbols with Blofin funding data.
                blofin_only_count: Symbols on Blofin but NOT on Binance.
                overlap_count: Symbols present on both exchanges.
                total_unique: Total unique symbols across both sources.
        """
        binance_rates = self._fetch_binance()
        blofin_rates = self._fetch_blofin()

        binance_symbols = {r["symbol"] for r in binance_rates}
        blofin_symbols = {r["symbol"] for r in blofin_rates}

        overlap = binance_symbols & blofin_symbols
        blofin_only = blofin_symbols - binance_symbols
        all_symbols = binance_symbols | blofin_symbols

        return {
            "binance_count": len(binance_symbols),
            "blofin_count": len(blofin_symbols),
            "blofin_only_count": len(blofin_only),
            "overlap_count": len(overlap),
            "total_unique": len(all_symbols),
            "blofin_only_symbols": sorted(blofin_only)[:20],  # Cap for readability
            "fetched_at": datetime.now(timezone.utc).isoformat(),
        }

    def classify_funding_risk(self, funding_rate: float) -> Dict:
        """
        Proxy to FundingRateFetcher.classify_funding_risk() for convenience.

        Args:
            funding_rate: Funding rate as decimal (e.g., -0.001 = -0.1%).

        Returns:
            Dict with risk_level, position_modifier, action, reason.
        """
        return self._binance.classify_funding_risk(funding_rate)

    # --- Internal helpers ---

    def _fetch_binance(self) -> List[Dict]:
        """Fetch Binance rates with graceful failure."""
        if self._last_binance is not None:
            return self._last_binance
        try:
            rates = self._binance.get_funding_rates(limit=500)
            self._last_binance = rates
            return rates
        except Exception as e:
            logger.warning(f"Binance funding fetch failed (degraded mode): {e}")
            self._last_binance = []
            return []

    def _fetch_blofin(self) -> List[Dict]:
        """Fetch Blofin rates with graceful failure."""
        if self._last_blofin is not None:
            return self._last_blofin
        try:
            rates = self._blofin.fetch_funding_rates(limit=500)
            self._last_blofin = rates
            return rates
        except Exception as e:
            logger.warning(f"Blofin funding fetch failed (degraded mode): {e}")
            self._last_blofin = []
            return []

    def clear_cache(self):
        """Clear internal fetch cache (forces re-fetch on next call)."""
        self._last_binance = None
        self._last_blofin = None
        self._last_merged = None


# ---------------------------------------------------------------------------
# CLI Testing
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    mef = MultiExchangeFunding()

    print("\n" + "=" * 65)
    print("Multi-Exchange Funding Rate Aggregator — Session 383")
    print("=" * 65)

    # Coverage stats
    stats = mef.get_coverage_stats()
    print(f"\nCoverage:")
    print(f"  Binance symbols:    {stats['binance_count']}")
    print(f"  Blofin symbols:     {stats['blofin_count']}")
    print(f"  Overlap:            {stats['overlap_count']}")
    print(f"  Blofin-only:        {stats['blofin_only_count']}")
    print(f"  Total unique:       {stats['total_unique']}")

    if stats["blofin_only_symbols"]:
        print(f"\n  Blofin-only tokens (first 20):")
        for sym in stats["blofin_only_symbols"]:
            print(f"    - {sym}")

    # Clear cache so get_all_funding_rates does a fresh fetch
    mef.clear_cache()

    # Top funding rates
    print(f"\nTop 20 Funding Rates (sorted by |rate|):")
    rates = mef.get_all_funding_rates(limit=20)
    for r in rates:
        sign = "+" if r["funding_rate_pct"] >= 0 else ""
        risk = mef.classify_funding_risk(r["funding_rate"])
        src = r["data_source"].replace("_funding", "").upper()
        print(
            f"  {r['symbol']:10s} {sign}{r['funding_rate_pct']:8.4f}%  "
            f"{risk['risk_level']:25s} [{src}]"
        )

    # Single symbol lookup
    for test_sym in ["BTC", "MONAD", "ETH"]:
        print(f"\nLookup: {test_sym}")
        result = mef.get_funding_for_symbol(test_sym)
        if result:
            sign = "+" if result["funding_rate_pct"] >= 0 else ""
            print(
                f"  Rate: {sign}{result['funding_rate_pct']:.4f}%  "
                f"Source: {result['data_source']}  "
                f"Risk: {result['risk']['risk_level']}"
            )
        else:
            print(f"  No funding data available")
