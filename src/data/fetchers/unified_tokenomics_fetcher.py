"""
Unified Tokenomics Fetcher - Session 370

Multi-source fetcher with waterfall strategy and quality scoring.
Replaces CoinGecko with: CryptoRank → Binance → DexScreener.

Priority order:
1. CryptoRank (most comprehensive)
2. CoinMarketCap (excellent supply data)
3. Binance (price + basic tokenomics)
4. DexScreener (DEX-listed tokens)
5. ICODrops (specialized TGE float data)
6. Manual overrides (user-provided data)
"""

import logging
from typing import Dict, List, Optional, Any
from pathlib import Path
import json

from src.integrations.coinmarketcap.cmc_client import CoinMarketCapClient
from src.integrations.icodrops.tokenomics_scanner import ICODropsScanner

logger = logging.getLogger(__name__)

# Field importance weights for quality scoring (must sum to 100)
FIELD_WEIGHTS = {
    "tge_date": 15,
    "float_pct": 15,
    "fdv": 12,
    "market_cap": 12,
    "investors": 10,
    "exchanges": 8,
    "locked_pct": 8,
    "circulating_supply": 6,
    "total_supply": 5,
    "category": 5,
    "funding_rounds": 4,
    "twitter": 0,
}


class UnifiedTokenomicsFetcher:
    """
    Fetches tokenomics from multiple sources with waterfall fallback.

    Features:
    - Waterfall fetching (try sources in priority order)
    - Quality scoring (0-100% based on field completeness)
    - Gap-filling merge (take best data from each source)
    - Manual override support (highest priority)
    """

    def __init__(self, project_root: Optional[Path] = None):
        """Initialize fetcher with optional project root for manual overrides."""
        self.project_root = project_root or Path(__file__).parent.parent.parent.parent
        self.manual_overrides_dir = self.project_root / "data" / "manual_overrides"

    def fetch(self, symbol: str, timeout: int = 30) -> Dict[str, Any]:
        """
        Fetch tokenomics from multiple sources.

        Args:
            symbol: Token symbol (e.g., "BTC")
            timeout: Max timeout per source (default: 30s)

        Returns:
            {
                "data": {merged tokenomics dict},
                "primary_source": "cryptorank",
                "quality_score": 85,
                "sources_used": ["cryptorank", "binance"],
            }
        """
        # Source priority order (1=highest)
        sources = [
            ("manual", self._fetch_manual_overrides, 0, 0),  # Always first if exists
            ("cryptorank", self._fetch_cryptorank, 1, 15),
            ("coinmarketcap", self._fetch_coinmarketcap, 2, 10),
            ("binance", self._fetch_binance, 3, 10),
            ("dexscreener", self._fetch_dexscreener, 4, 20),
            ("icodrops", self._fetch_icodrops, 5, 15),
        ]

        results = []
        for name, fetcher_func, priority, source_timeout in sources:
            try:
                data = fetcher_func(symbol, timeout=source_timeout or timeout)
                if data:  # Skip None results
                    # Auto-calculate float_pct if possible (Session 495 improvement)
                    if not data.get("float_pct"):
                        circ = data.get("circulating_supply")
                        total = data.get("total_supply")
                        if circ and total and total > 0:
                            data["float_pct"] = (circ / total) * 100

                    quality = self.calculate_quality_score(data)
                    results.append({
                        "source": name,
                        "data": data,
                        "quality_score": quality,
                        "priority": priority,
                    })
                    logger.debug(f"✓ {name}: {quality}% quality for {symbol}")
            except Exception as e:
                logger.warning(f"✗ {name} failed for {symbol}: {e}")

        if not results:
            raise ValueError(f"All sources failed for {symbol}")

        # Choose best: highest quality, then highest priority (lowest number)
        best = max(results, key=lambda x: (x["quality_score"], -x["priority"]))

        # Merge missing fields from other sources
        merged = self.merge_sources(results, strategy="fill_gaps")

        # Final pass: Ensure float_pct is calculated on the merged result
        if not merged.get("float_pct"):
            circ = merged.get("circulating_supply")
            total = merged.get("total_supply")
            if circ and total and total > 0:
                merged["float_pct"] = (circ / total) * 100

        return {
            "data": merged,
            "primary_source": best["source"],
            "quality_score": self.calculate_quality_score(merged),
            "sources_used": [r["source"] for r in results],
        }

    def _fetch_coinmarketcap(self, symbol: str, timeout: int = 10) -> Optional[Dict]:
        """Fetch data from CoinMarketCap."""
        try:
            client = CoinMarketCapClient()
            if not client.enabled:
                return None
            data = client.get_token_data(symbol)
            if not data:
                return None
            
            # Map CMC fields to our internal format
            return {
                "symbol": symbol,
                "circulating_supply": data.get("circulating_supply"),
                "total_supply": data.get("total_supply"),
                "float_pct": data.get("float_percent"),
                "price": data.get("price_usd"),
                "market_cap": data.get("market_cap"),
                "fdv": data.get("fdv"),
                "last_updated": data.get("last_updated"),
            }
        except Exception as e:
            logger.debug(f"CMC fetch error for {symbol}: {e}")
            return None

    def _fetch_icodrops(self, symbol: str, timeout: int = 15) -> Optional[Dict]:
        """Fetch data from ICODrops scraper."""
        try:
            scanner = ICODropsScanner()
            # Search requires project name, but we can try with symbol first
            url = scanner.search_project(symbol, symbol)
            if not url:
                return None
            
            data = scanner.extract_tokenomics(url)
            if not data:
                return None
            
            return {
                "float_pct": data.get("float_percent"),
                "total_supply": data.get("total_supply"),
                "circulating_supply": data.get("initial_circulating"),
                "vesting_schedule": data.get("vesting_schedule"),
            }
        except Exception as e:
            logger.debug(f"ICODrops fetch error for {symbol}: {e}")
            return None

    def calculate_quality_score(self, data: Dict[str, Any]) -> int:
        """
        Calculate data quality score (0-100) based on field completeness.

        Uses FIELD_WEIGHTS to weight critical fields more heavily.
        """
        if not data:
            return 0

        total_weight = 0
        present_weight = 0

        for field, weight in FIELD_WEIGHTS.items():
            total_weight += weight
            value = data.get(field)

            # Check if field is present and non-empty
            is_present = (
                value is not None and
                value != "" and
                (not isinstance(value, list) or len(value) > 0)
            )

            if is_present:
                present_weight += weight

        return int((present_weight / total_weight) * 100)

    def merge_sources(self, results: List[Dict], strategy: str = "fill_gaps") -> Dict[str, Any]:
        """
        Merge data from multiple sources.

        Strategy 'fill_gaps':
        - Start with highest quality source
        - Fill missing fields from lower quality sources
        - Never overwrite existing fields
        """
        if not results:
            return {}

        # Sort by quality score (highest first)
        sorted_results = sorted(results, key=lambda x: x["quality_score"], reverse=True)

        merged = {}
        for result in sorted_results:
            data = result["data"]
            for key, value in data.items():
                # Only add if not already present (fill gaps only)
                if key not in merged:
                    merged[key] = value

        return merged

    def _fetch_manual_overrides(self, symbol: str, timeout: int) -> Optional[Dict]:
        """Load manual overrides from data/manual_overrides/{symbol}.json."""
        override_path = self.manual_overrides_dir / f"{symbol}.json"
        if not override_path.exists():
            return None

        with open(override_path) as f:
            return json.load(f)

    def _fetch_cryptorank(self, symbol: str, timeout: int) -> Dict:
        """Fetch from CryptoRank API."""
        # Import here to avoid circular dependency
        from src.data.fetchers.cryptorank_web_fetcher import fetch_cryptorank_web
        return fetch_cryptorank_web(symbol)

    def _fetch_binance(self, symbol: str, timeout: int) -> Dict:
        """Fetch from Binance API."""
        from src.data.fetchers.exchange import fetch_token_price
        result = fetch_token_price(symbol)
        # Convert to tokenomics format if needed
        if result and "price" in result:
            return {
                "price": result.get("price"),
                "market_cap": result.get("market_cap"),
                "volume_24h": result.get("volume_24h"),
            }
        return result or {}

    def _fetch_dexscreener(self, symbol: str, timeout: int) -> Dict:
        """Fetch from DexScreener API."""
        from src.data.fetchers.dex_data_fetcher import fetch_dex_token_data
        return fetch_dex_token_data(symbol)
