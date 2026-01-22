"""
Unified Tokenomics Fetcher - Session 370

Multi-source fetcher with waterfall strategy and quality scoring.
Replaces CoinGecko with: CryptoRank → Binance → DexScreener.

Priority order:
1. CryptoRank (most comprehensive)
2. Binance (price + basic tokenomics)
3. DexScreener (DEX-listed tokens)
4. Manual overrides (user-provided data)
"""

import logging
from typing import Dict, List, Optional, Any
from pathlib import Path
import json

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
            ("binance", self._fetch_binance, 2, 10),
            ("dexscreener", self._fetch_dexscreener, 3, 20),
        ]

        results = []
        for name, fetcher_func, priority, source_timeout in sources:
            try:
                data = fetcher_func(symbol, timeout=source_timeout or timeout)
                if data:  # Skip None results
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

        return {
            "data": merged,
            "primary_source": best["source"],
            "quality_score": self.calculate_quality_score(merged),
            "sources_used": [r["source"] for r in results],
        }

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
