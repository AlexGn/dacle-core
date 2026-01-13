#!/usr/bin/env python3
"""
CoinGecko Tokenomics Fetcher - Session 318

Replacement for broken CryptoRank API as automated_data source.

Provides complete tokenomics for LISTED tokens:
- FDV (Fully Diluted Valuation)
- Market Cap
- Circulating/Total/Max Supply
- Float % (calculated)
- Price, Volume
- Listing exchanges

Why CoinGecko:
- ✅ FREE API (no auth required)
- ✅ Comprehensive tokenomics data
- ✅ Real-time prices
- ✅ Reliable and stable
- ✅ Already used in daily_tge_discovery.py

Limitations:
- Only works for LISTED tokens (post-TGE)
- Does NOT provide:
  - VC investors (use Perplexity for this)
  - Pre-TGE data (token not yet listed)
  - Vesting schedules (use Tokenomist/DefiLlama)

Usage:
    from src.data.fetchers.coingecko_fetcher import CoinGeckoFetcher

    fetcher = CoinGeckoFetcher()
    data = fetcher.fetch_token("bitcoin")
    # Returns dict compatible with automated_data format
"""

import logging
import requests
from datetime import datetime, timezone
from typing import Dict, Optional, Any, List

logger = logging.getLogger(__name__)


class CoinGeckoFetcher:
    """
    Fetches tokenomics data from CoinGecko API for listed tokens.

    Replaces CryptoRank as the automated_data source in data consolidation.
    """

    BASE_URL = "https://api.coingecko.com/api/v3"
    TIMEOUT = 30

    def __init__(self):
        """Initialize fetcher."""
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36',
            'Accept': 'application/json'
        })

    def fetch_token(self, symbol_or_id: str) -> Optional[Dict[str, Any]]:
        """
        Fetch tokenomics for a token.

        Args:
            symbol_or_id: Token symbol (BTC) or CoinGecko ID (bitcoin)

        Returns:
            Dict in automated_data format:
            {
                "symbol": "BTC",
                "name": "Bitcoin",
                "fdv": 1830191431704.0,
                "market_cap": 1830191431704.0,
                "circulating_supply": 19975018.0,
                "total_supply": 19975018.0,
                "max_supply": 21000000.0,
                "float_pct": 95.1,
                "price_usd": 91625.0,
                "volume_24h": 50000000000.0,
                "listing_exchanges": ["binance", "coinbase", ...],
                "data_source": "coingecko",
                "fetched_at": "2026-01-12T..."
            }
        """
        # Step 1: Find CoinGecko ID from symbol
        coin_id = self._find_coin_id(symbol_or_id)
        if not coin_id:
            logger.warning(f"Token {symbol_or_id} not found on CoinGecko")
            return None

        # Step 2: Fetch detailed data
        try:
            url = f"{self.BASE_URL}/coins/{coin_id}"
            params = {
                'localization': 'false',
                'tickers': 'true',   # Get exchange listings
                'market_data': 'true',
                'community_data': 'false',
                'developer_data': 'false'
            }

            response = self.session.get(url, params=params, timeout=self.TIMEOUT)
            response.raise_for_status()
            data = response.json()

            return self._parse_token_data(data)

        except Exception as e:
            logger.error(f"Failed to fetch {symbol_or_id} from CoinGecko: {e}")
            return None

    def fetch_tokens_batch(self, symbols: List[str]) -> Dict[str, Optional[Dict]]:
        """
        Fetch multiple tokens (sequential to avoid rate limits).

        Args:
            symbols: List of token symbols

        Returns:
            Dict mapping symbol to tokenomics data (or None if not found)
        """
        results = {}
        for symbol in symbols:
            results[symbol] = self.fetch_token(symbol)
        return results

    def _find_coin_id(self, symbol_or_id: str) -> Optional[str]:
        """
        Find CoinGecko coin ID from symbol or ID.

        Args:
            symbol_or_id: Token symbol (BTC) or CoinGecko ID (bitcoin)

        Returns:
            CoinGecko coin ID (e.g., "bitcoin") or None
        """
        # If it looks like an ID (lowercase, no special chars), try it directly
        if symbol_or_id.islower() and symbol_or_id.isalnum():
            return symbol_or_id

        # Otherwise, search by symbol
        try:
            # Use /coins/list to get all coins (cached by CoinGecko)
            url = f"{self.BASE_URL}/coins/list"
            response = self.session.get(url, timeout=self.TIMEOUT)
            response.raise_for_status()
            coins = response.json()

            # Find coin matching symbol (case-insensitive)
            # Prefer exact match with highest market cap
            symbol_upper = symbol_or_id.upper()
            matches = [coin for coin in coins if coin['symbol'].upper() == symbol_upper]

            if not matches:
                logger.warning(f"  Symbol {symbol_or_id} not found in CoinGecko list")
                return None

            # If multiple matches, prefer the one with most well-known ID
            # (e.g., "bitcoin" for BTC, not "batcat")
            well_known = {
                'BTC': 'bitcoin',
                'ETH': 'ethereum',
                'BNB': 'binancecoin',
                'SOL': 'solana',
                'ADA': 'cardano',
                'DOT': 'polkadot',
                'MATIC': 'matic-network',
                'AVAX': 'avalanche-2',
            }

            if symbol_upper in well_known:
                for coin in matches:
                    if coin['id'] == well_known[symbol_upper]:
                        logger.debug(f"  Found {symbol_or_id} → {coin['id']} (well-known)")
                        return coin['id']

            # Otherwise return first match
            logger.debug(f"  Found {symbol_or_id} → {matches[0]['id']} ({len(matches)} matches)")
            return matches[0]['id']

        except Exception as e:
            logger.error(f"Failed to search for {symbol_or_id}: {e}")
            return None

    def _parse_token_data(self, data: Dict) -> Dict[str, Any]:
        """
        Parse CoinGecko response into automated_data format.

        Session 318 P1.4: Enhanced to extract TGE-specific fields:
        - listing_exchanges: Extracted from tickers with tier classification
        - tge_date: ATH date as proxy (±2-7 days accuracy)
        - listing_price_low: ATL as proxy (can be 30%+ off for dumped tokens)
        """
        market_data = data.get('market_data', {})

        # Extract supplies
        circulating = market_data.get('circulating_supply')
        total = market_data.get('total_supply')
        max_supply = market_data.get('max_supply')

        # Calculate float %
        float_pct = None
        if circulating and max_supply and max_supply > 0:
            float_pct = round((circulating / max_supply) * 100, 2)

        # Session 318 P1.4: Extract exchanges with tier classification
        tickers = data.get('tickers', [])
        exchange_data = self._extract_exchanges_with_tiers(tickers)

        # Session 318 P1.4: TGE-specific fields
        # Use ATH date as TGE date proxy (typical accuracy: ±2-7 days)
        ath_date_dict = market_data.get('ath_date', {})
        tge_date = ath_date_dict.get('usd') if isinstance(ath_date_dict, dict) else ath_date_dict

        # Use ATL as listing price proxy (can be 30%+ off if token dumped)
        atl_dict = market_data.get('atl', {})
        listing_price_low = atl_dict.get('usd') if isinstance(atl_dict, dict) else atl_dict

        # FDV
        fdv_dict = market_data.get('fully_diluted_valuation', {})
        fdv = fdv_dict.get('usd') if isinstance(fdv_dict, dict) else fdv_dict

        # Build automated_data dict
        return {
            "symbol": data.get('symbol', '').upper(),
            "name": data.get('name'),
            "fdv": fdv,
            "fully_diluted_valuation": fdv,  # Alias for compatibility
            "market_cap": market_data.get('market_cap', {}).get('usd'),
            "mc": market_data.get('market_cap', {}).get('usd'),  # Alias
            "circulating_supply": circulating,
            "total_supply": total,
            "max_supply": max_supply,
            "float_pct": float_pct,
            "price_usd": market_data.get('current_price', {}).get('usd'),
            "volume_24h": market_data.get('total_volume', {}).get('usd'),
            "price_change_24h": market_data.get('price_change_percentage_24h'),
            "ath": market_data.get('ath', {}).get('usd'),
            "ath_date": market_data.get('ath_date', {}).get('usd'),
            # Session 318 P1.4: TGE-specific fields
            "tge_date": tge_date,  # ATH date as proxy
            "listing_price_low": listing_price_low,  # ATL as proxy
            "listing_exchanges": exchange_data['exchange_names'],  # Human-readable names
            "exchange_tier": exchange_data['highest_tier'],  # Tier 1/2/3
            "contract_address": data.get('contract_address'),  # If available
            "data_source": "coingecko",
            "coingecko_id": data.get('id'),
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "_raw_coingecko": {
                "rank": data.get('market_cap_rank'),
                "categories": data.get('categories', []),
                "platforms": data.get('platforms', {}),
            }
        }

    def _extract_exchanges_with_tiers(self, tickers: List[Dict]) -> Dict[str, Any]:
        """
        Extract exchange names and classify into tiers.

        Session 318 P1.4: Added tier classification based on data/exchange_tiers.json

        Args:
            tickers: List of ticker dicts from CoinGecko API

        Returns:
            Dict with exchange_names (list of str) and highest_tier (str)
        """
        # Exchange tier classification (from data/exchange_tiers.json)
        TIER_1 = {'binance', 'coinbase', 'bybit', 'okx', 'kraken'}
        TIER_2 = {'gate.io', 'gate', 'kucoin', 'bitget', 'upbit', 'bithumb'}
        TIER_3 = {'mexc', 'htx', 'bitmart', 'poloniex', 'bingx'}

        # Extract unique exchange names (case-insensitive)
        exchange_names_set = set()
        tier_found = None

        for ticker in tickers:
            market_name = ticker.get('market', {}).get('name', '')
            if not market_name:
                continue

            # Normalize name for tier classification
            name_lower = market_name.lower()

            # Check tier (prioritize higher tiers)
            if any(t1 in name_lower for t1 in TIER_1):
                exchange_names_set.add(market_name)
                if tier_found is None or tier_found > 1:
                    tier_found = 1
            elif any(t2 in name_lower for t2 in TIER_2):
                exchange_names_set.add(market_name)
                if tier_found is None or tier_found > 2:
                    tier_found = 2
            elif any(t3 in name_lower for t3 in TIER_3):
                exchange_names_set.add(market_name)
                if tier_found is None or tier_found > 3:
                    tier_found = 3
            else:
                # Unknown exchange - add but don't classify tier
                exchange_names_set.add(market_name)

        # Convert tier number to string label
        tier_label = None
        if tier_found == 1:
            tier_label = "Tier 1"
        elif tier_found == 2:
            tier_label = "Tier 2"
        elif tier_found == 3:
            tier_label = "Tier 3"

        return {
            'exchange_names': sorted(list(exchange_names_set))[:20],  # Top 20 exchanges
            'highest_tier': tier_label
        }


# CLI interface for testing
def main():
    """CLI entry point."""
    import argparse

    parser = argparse.ArgumentParser(description="CoinGecko Tokenomics Fetcher")
    parser.add_argument("tokens", nargs="+", help="Token symbols or IDs (e.g., BTC ETH MONAD)")
    parser.add_argument("--verbose", "-v", action="store_true", help="Verbose output")

    args = parser.parse_args()

    if args.verbose:
        logging.basicConfig(level=logging.DEBUG)
    else:
        logging.basicConfig(level=logging.INFO)

    fetcher = CoinGeckoFetcher()

    if len(args.tokens) == 1:
        # Single token
        data = fetcher.fetch_token(args.tokens[0])
        if data:
            print(f"\n{'='*60}")
            print(f"CoinGecko Data: {data['symbol']} ({data['name']})")
            print("="*60)
            print(f"  Price:              ${data.get('price_usd', 'N/A')}")
            print(f"  Market Cap:         ${data.get('market_cap', 0):,.0f}")
            print(f"  FDV:                ${data.get('fdv', 0):,.0f}")
            print(f"  Circulating Supply: {data.get('circulating_supply', 0):,.0f}")
            print(f"  Max Supply:         {data.get('max_supply', 0):,.0f}")
            print(f"  Float %:            {data.get('float_pct', 0):.1f}%")
            print(f"  Volume 24h:         ${data.get('volume_24h', 0):,.0f}")
            print(f"  Exchanges ({len(data.get('listing_exchanges', []))}): {', '.join(data.get('listing_exchanges', [])[:10])}")
            print(f"  Data Source:        {data.get('data_source')}")
            print(f"  Fetched:            {data.get('fetched_at', 'N/A')[:19]}")
            print("="*60 + "\n")
        else:
            print(f"❌ Failed to fetch {args.tokens[0]}")
    else:
        # Batch fetch
        results = fetcher.fetch_tokens_batch(args.tokens)
        print(f"\n{'='*60}")
        print(f"Batch Results: {len([r for r in results.values() if r])}/{len(args.tokens)} successful")
        print("="*60)
        for symbol, data in results.items():
            if data:
                print(f"\n  ✅ {data['symbol']}: ${data.get('price_usd', 0)} | FDV=${data.get('fdv', 0):,.0f} | Float={data.get('float_pct', 0):.1f}%")
            else:
                print(f"\n  ❌ {symbol}: Not found")
        print()


if __name__ == "__main__":
    main()
