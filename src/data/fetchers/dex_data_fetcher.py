#!/usr/bin/env python3
"""
DEX Data Fetcher - Session 297 Integration

Fetches comprehensive data for DEX-only tokens from:
1. DexScreener API - Price, FDV, market cap, liquidity, chain identification
2. GeckoTerminal API - OHLCV candles for TA calculations
3. On-chain RPC - Circulating supply, total supply, burned tokens

This module fills the gap for tokens not listed on CEXs (like ALLOCA on Monad).

Usage:
    from src.data.fetchers.dex_data_fetcher import DEXDataFetcher

    fetcher = DEXDataFetcher()
    data = fetcher.fetch_complete_data("ALLOCA", contract_address="0x...")
    # Returns: price, market_cap, fdv, float_pct, chain, dex_pair, ohlcv, ta_indicators

Author: Claude Code (Session 297)
Created: 2026-01-07
"""

import json
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
import requests

logger = logging.getLogger(__name__)


@dataclass
class DEXPairData:
    """Data from a DEX pair."""
    chain: str
    dex: str
    pair_address: str
    base_token: str
    quote_token: str
    price_usd: float
    price_native: float
    fdv: Optional[float] = None
    market_cap: Optional[float] = None
    liquidity_usd: Optional[float] = None
    volume_24h: Optional[float] = None
    price_change_24h: Optional[float] = None
    created_at: Optional[str] = None


@dataclass
class OHLCVCandle:
    """Single OHLCV candle."""
    timestamp: int
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass
class TAIndicators:
    """Technical analysis indicators calculated from OHLCV."""
    rsi_14: Optional[float] = None
    rsi_4h: Optional[float] = None  # Same as rsi_14 when using 4H candles
    ath_price: Optional[float] = None
    drawdown_from_ath: Optional[float] = None
    days_since_ath: Optional[int] = None
    at_ema_200_support: Optional[bool] = None
    at_ema_50_support: Optional[bool] = None
    dump_volume_ratio: Optional[float] = None
    bottom_signals_count: Optional[int] = None
    ema_50: Optional[float] = None
    ema_200: Optional[float] = None
    current_price: Optional[float] = None


@dataclass
class DEXCompleteData:
    """Complete data package from DEX sources."""
    symbol: str
    name: Optional[str] = None
    chain: Optional[str] = None
    contract_address: Optional[str] = None
    current_price: Optional[float] = None
    market_cap: Optional[float] = None
    fdv: Optional[float] = None
    fdv_mc_ratio: Optional[float] = None
    circulating_supply: Optional[float] = None
    total_supply: Optional[float] = None
    float_pct: Optional[float] = None
    liquidity_usd: Optional[float] = None
    volume_24h: Optional[float] = None
    dex_pair: Optional[DEXPairData] = None
    ta_indicators: Optional[TAIndicators] = None
    ohlcv_candles: List[OHLCVCandle] = field(default_factory=list)
    data_source: str = "dex"
    fetch_timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    errors: List[str] = field(default_factory=list)


class DEXDataFetcher:
    """
    Fetches comprehensive data for DEX-only tokens.

    Combines multiple data sources to provide complete token data
    similar to what CoinGecko provides for CEX-listed tokens.
    """

    # DexScreener API endpoints
    DEXSCREENER_SEARCH = "https://api.dexscreener.com/latest/dex/search"
    DEXSCREENER_TOKENS = "https://api.dexscreener.com/latest/dex/tokens"
    DEXSCREENER_PAIRS = "https://api.dexscreener.com/latest/dex/pairs"

    # GeckoTerminal API endpoints
    GECKOTERMINAL_SEARCH = "https://api.geckoterminal.com/api/v2/search/pools"
    GECKOTERMINAL_OHLCV = "https://api.geckoterminal.com/api/v2/networks/{network}/pools/{pool}/ohlcv/{timeframe}"
    GECKOTERMINAL_TOKEN_POOLS = "https://api.geckoterminal.com/api/v2/networks/{network}/tokens/{address}/pools"

    # Chain name mappings (DexScreener -> GeckoTerminal)
    CHAIN_MAPPING = {
        "monad": "monad",
        "ethereum": "eth",
        "bsc": "bsc",
        "polygon": "polygon_pos",
        "arbitrum": "arbitrum",
        "optimism": "optimism",
        "base": "base",
        "avalanche": "avax",
        "solana": "solana",
        "sui": "sui",
    }

    def __init__(self, timeout: int = 30):
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "DACLE/1.0 (DEX Data Fetcher)",
            "Accept": "application/json",
        })

    def fetch_complete_data(
        self,
        symbol: str,
        contract_address: Optional[str] = None,
        chain: Optional[str] = None,
    ) -> DEXCompleteData:
        """
        Fetch complete data for a DEX token.

        Args:
            symbol: Token symbol (e.g., "ALLOCA")
            contract_address: Optional contract address for precise lookup
            chain: Optional chain hint (e.g., "monad", "ethereum")

        Returns:
            DEXCompleteData with all available information
        """
        result = DEXCompleteData(symbol=symbol.upper())

        # Step 1: Find DEX pair via DexScreener
        pair_data = self._fetch_dexscreener_data(symbol, contract_address, chain)
        if pair_data:
            result.dex_pair = pair_data
            result.chain = pair_data.chain
            result.current_price = pair_data.price_usd
            result.fdv = pair_data.fdv
            result.market_cap = pair_data.market_cap
            result.liquidity_usd = pair_data.liquidity_usd
            result.volume_24h = pair_data.volume_24h
            result.contract_address = contract_address

            # Calculate FDV/MC ratio if both available
            if result.fdv and result.market_cap and result.market_cap > 0:
                result.fdv_mc_ratio = result.fdv / result.market_cap
        else:
            result.errors.append("DexScreener: No pair found")

        # Step 2: Fetch OHLCV from GeckoTerminal for TA calculations
        if result.chain and (contract_address or result.dex_pair):
            ohlcv = self._fetch_geckoterminal_ohlcv(
                result.chain,
                contract_address or (result.dex_pair.pair_address if result.dex_pair else None),
                timeframe="hour",
                aggregate=4,  # 4-hour candles
                limit=100,
            )
            if ohlcv:
                result.ohlcv_candles = ohlcv
                result.ta_indicators = self._calculate_ta_indicators(ohlcv, result.current_price)
            else:
                result.errors.append("GeckoTerminal: No OHLCV data")

        # Step 3: Fetch on-chain supply data if we have contract address
        if contract_address and result.chain:
            supply_data = self._fetch_onchain_supply(contract_address, result.chain)
            if supply_data:
                result.circulating_supply = supply_data.get("circulating_supply")
                result.total_supply = supply_data.get("total_supply")
                result.float_pct = supply_data.get("float_pct")

                # Recalculate market cap if we have better supply data
                if result.current_price and result.circulating_supply:
                    result.market_cap = result.current_price * result.circulating_supply
                    if result.fdv and result.market_cap > 0:
                        result.fdv_mc_ratio = result.fdv / result.market_cap

        return result

    def _fetch_dexscreener_data(
        self,
        symbol: str,
        contract_address: Optional[str] = None,
        chain: Optional[str] = None,
    ) -> Optional[DEXPairData]:
        """Fetch pair data from DexScreener API."""
        try:
            # Try by contract address first (most accurate)
            if contract_address:
                url = f"{self.DEXSCREENER_TOKENS}/{contract_address}"
                response = self.session.get(url, timeout=self.timeout)
                if response.status_code == 200:
                    data = response.json()
                    pairs = data.get("pairs", [])
                    if pairs:
                        return self._parse_dexscreener_pair(pairs[0])

            # Fallback to search by symbol
            url = f"{self.DEXSCREENER_SEARCH}?q={symbol}"
            response = self.session.get(url, timeout=self.timeout)
            if response.status_code == 200:
                data = response.json()
                pairs = data.get("pairs", [])

                # Filter by chain if specified
                if chain and pairs:
                    chain_lower = chain.lower()
                    pairs = [p for p in pairs if p.get("chainId", "").lower() == chain_lower]

                # Sort by liquidity to get the main pair
                if pairs:
                    pairs.sort(key=lambda p: p.get("liquidity", {}).get("usd", 0) or 0, reverse=True)
                    return self._parse_dexscreener_pair(pairs[0])

            return None

        except Exception as e:
            logger.warning(f"DexScreener fetch failed for {symbol}: {e}")
            return None

    def _parse_dexscreener_pair(self, pair: Dict) -> DEXPairData:
        """Parse DexScreener pair response into DEXPairData."""
        return DEXPairData(
            chain=pair.get("chainId", "unknown"),
            dex=pair.get("dexId", "unknown"),
            pair_address=pair.get("pairAddress", ""),
            base_token=pair.get("baseToken", {}).get("symbol", ""),
            quote_token=pair.get("quoteToken", {}).get("symbol", ""),
            price_usd=float(pair.get("priceUsd", 0) or 0),
            price_native=float(pair.get("priceNative", 0) or 0),
            fdv=float(pair.get("fdv", 0) or 0) if pair.get("fdv") else None,
            market_cap=float(pair.get("marketCap", 0) or 0) if pair.get("marketCap") else None,
            liquidity_usd=pair.get("liquidity", {}).get("usd"),
            volume_24h=pair.get("volume", {}).get("h24"),
            price_change_24h=pair.get("priceChange", {}).get("h24"),
            created_at=pair.get("pairCreatedAt"),
        )

    def _fetch_geckoterminal_ohlcv(
        self,
        chain: str,
        pool_or_token_address: str,
        timeframe: str = "hour",
        aggregate: int = 4,
        limit: int = 100,
    ) -> List[OHLCVCandle]:
        """Fetch OHLCV data from GeckoTerminal API."""
        try:
            # Map chain name to GeckoTerminal network ID
            network = self.CHAIN_MAPPING.get(chain.lower(), chain.lower())

            # First try to get pools for the token
            pools_url = self.GECKOTERMINAL_TOKEN_POOLS.format(
                network=network,
                address=pool_or_token_address,
            )

            response = self.session.get(pools_url, timeout=self.timeout)
            if response.status_code == 200:
                pools_data = response.json()
                pools = pools_data.get("data", [])
                if pools:
                    # Get the first (most liquid) pool
                    pool_address = pools[0].get("id", "").split("_")[-1]  # Format: network_address
                    if not pool_address:
                        pool_address = pools[0].get("attributes", {}).get("address", "")
                else:
                    pool_address = pool_or_token_address
            else:
                pool_address = pool_or_token_address

            # Now fetch OHLCV
            ohlcv_url = self.GECKOTERMINAL_OHLCV.format(
                network=network,
                pool=pool_address,
                timeframe=timeframe,
            )
            params = {
                "aggregate": aggregate,
                "limit": limit,
            }

            response = self.session.get(ohlcv_url, params=params, timeout=self.timeout)
            if response.status_code == 200:
                data = response.json()
                ohlcv_list = data.get("data", {}).get("attributes", {}).get("ohlcv_list", [])

                candles = []
                for candle in ohlcv_list:
                    if len(candle) >= 6:
                        candles.append(OHLCVCandle(
                            timestamp=candle[0],
                            open=float(candle[1]),
                            high=float(candle[2]),
                            low=float(candle[3]),
                            close=float(candle[4]),
                            volume=float(candle[5]),
                        ))

                # Sort by timestamp (oldest first for TA calculations)
                candles.sort(key=lambda c: c.timestamp)
                return candles

            return []

        except Exception as e:
            logger.warning(f"GeckoTerminal OHLCV fetch failed: {e}")
            return []

    def _calculate_ta_indicators(
        self,
        candles: List[OHLCVCandle],
        current_price: Optional[float] = None,
    ) -> TAIndicators:
        """Calculate TA indicators from OHLCV candles."""
        if not candles:
            return TAIndicators()

        closes = [c.close for c in candles]
        volumes = [c.volume for c in candles]
        highs = [c.high for c in candles]

        indicators = TAIndicators()
        indicators.current_price = current_price or closes[-1] if closes else None

        # RSI-14
        if len(closes) >= 15:
            indicators.rsi_14 = self._calculate_rsi(closes, period=14)
            indicators.rsi_4h = indicators.rsi_14  # Same when using 4H candles

        # ATH and drawdown
        if highs:
            indicators.ath_price = max(highs)
            if indicators.current_price and indicators.ath_price > 0:
                indicators.drawdown_from_ath = (
                    (indicators.current_price - indicators.ath_price) / indicators.ath_price
                ) * 100

            # Days since ATH
            ath_idx = highs.index(indicators.ath_price)
            if ath_idx < len(candles):
                ath_timestamp = candles[ath_idx].timestamp
                now = int(time.time())
                indicators.days_since_ath = (now - ath_timestamp) // 86400

        # EMA calculations
        if len(closes) >= 50:
            indicators.ema_50 = self._calculate_ema(closes, period=50)
            indicators.at_ema_50_support = (
                indicators.current_price and
                indicators.ema_50 and
                indicators.current_price <= indicators.ema_50 * 1.02  # Within 2%
            )

        if len(closes) >= 200:
            indicators.ema_200 = self._calculate_ema(closes, period=200)
            indicators.at_ema_200_support = (
                indicators.current_price and
                indicators.ema_200 and
                indicators.current_price <= indicators.ema_200 * 1.02
            )
        else:
            # Use EMA-50 as proxy if not enough data for EMA-200
            indicators.at_ema_200_support = indicators.at_ema_50_support

        # Volume ratio (current vs average)
        if len(volumes) >= 20:
            avg_volume = sum(volumes[-20:]) / 20
            current_volume = volumes[-1] if volumes else 0
            if avg_volume > 0:
                indicators.dump_volume_ratio = current_volume / avg_volume

        # Bottom signals count
        bottom_signals = 0
        if indicators.rsi_14 and indicators.rsi_14 < 30:
            bottom_signals += 1  # RSI oversold
        if indicators.drawdown_from_ath and indicators.drawdown_from_ath < -50:
            bottom_signals += 1  # Deep drawdown
        if indicators.dump_volume_ratio and indicators.dump_volume_ratio > 2.0:
            bottom_signals += 1  # Volume spike
        if indicators.at_ema_200_support or indicators.at_ema_50_support:
            bottom_signals += 1  # At EMA support
        indicators.bottom_signals_count = bottom_signals

        return indicators

    def _calculate_rsi(self, closes: List[float], period: int = 14) -> float:
        """Calculate RSI from close prices."""
        if len(closes) < period + 1:
            return 50.0  # Default neutral

        deltas = [closes[i] - closes[i-1] for i in range(1, len(closes))]
        gains = [d if d > 0 else 0 for d in deltas]
        losses = [-d if d < 0 else 0 for d in deltas]

        # Use SMA for first average, then EMA
        avg_gain = sum(gains[:period]) / period
        avg_loss = sum(losses[:period]) / period

        for i in range(period, len(gains)):
            avg_gain = (avg_gain * (period - 1) + gains[i]) / period
            avg_loss = (avg_loss * (period - 1) + losses[i]) / period

        if avg_loss == 0:
            return 100.0

        rs = avg_gain / avg_loss
        rsi = 100 - (100 / (1 + rs))
        return round(rsi, 2)

    def _calculate_ema(self, values: List[float], period: int) -> float:
        """Calculate EMA from values."""
        if len(values) < period:
            return sum(values) / len(values) if values else 0

        multiplier = 2 / (period + 1)
        ema = sum(values[:period]) / period  # SMA as seed

        for value in values[period:]:
            ema = (value * multiplier) + (ema * (1 - multiplier))

        return round(ema, 6)

    def _fetch_onchain_supply(
        self,
        contract_address: str,
        chain: str,
    ) -> Optional[Dict[str, Any]]:
        """Fetch on-chain supply data using the onchain_supply_fetcher."""
        try:
            # Import the existing on-chain fetcher
            from src.data.fetchers.onchain_supply_fetcher import OnChainSupplyFetcher

            fetcher = OnChainSupplyFetcher()
            result = fetcher.fetch_for_token(
                token_symbol="",  # Not needed for direct address lookup
                contract_address=contract_address,
                chain=chain
            )

            if result and result.get("circulating_supply"):
                return {
                    "circulating_supply": result.get("circulating_supply"),
                    "total_supply": result.get("total_supply"),
                    "float_pct": result.get("float_percent"),
                }

            return None

        except Exception as e:
            logger.warning(f"On-chain supply fetch failed: {e}")
            return None

    def to_consolidated_format(self, data: DEXCompleteData) -> Dict[str, Any]:
        """Convert DEXCompleteData to consolidated.json format."""
        result = {
            "symbol": data.symbol,
            "name": data.name or data.symbol,
            "chain": data.chain,
            "contract_address": data.contract_address,
            "current_price": data.current_price,
            "market_cap": data.market_cap,
            "fdv": data.fdv,
            "fdv_mc_ratio": data.fdv_mc_ratio,
            "circulating_supply": data.circulating_supply,
            "total_supply": data.total_supply,
            "float_pct": data.float_pct,
            "liquidity_usd": data.liquidity_usd,
            "volume_24h": data.volume_24h,
            "data_source": data.data_source,
            "dex_fetch_timestamp": data.fetch_timestamp,
            # Exchange tier for DEX-only tokens
            "exchange_tier": "DEX_ONLY",
            "binance_listing": False,
        }

        # Add TA indicators
        if data.ta_indicators:
            ta = data.ta_indicators
            result.update({
                "rsi_4h": ta.rsi_4h,
                "rsi_14": ta.rsi_14,
                "ath_price": ta.ath_price,
                "drawdown_from_ath": ta.drawdown_from_ath,
                "days_since_ath": ta.days_since_ath,
                "at_ema_200_support": ta.at_ema_200_support,
                "dump_volume_ratio": ta.dump_volume_ratio,
                "bottom_signals_count": ta.bottom_signals_count,
            })

        # Add DEX pair info
        if data.dex_pair:
            result["dex_info"] = {
                "dex": data.dex_pair.dex,
                "pair_address": data.dex_pair.pair_address,
                "quote_token": data.dex_pair.quote_token,
                "price_change_24h": data.dex_pair.price_change_24h,
            }

        # Remove None values
        result = {k: v for k, v in result.items() if v is not None}

        return result


def fetch_dex_token_data(
    symbol: str,
    contract_address: Optional[str] = None,
    chain: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Convenience function to fetch DEX token data.

    Args:
        symbol: Token symbol
        contract_address: Optional contract address
        chain: Optional chain hint

    Returns:
        Dict in consolidated.json format
    """
    fetcher = DEXDataFetcher()
    data = fetcher.fetch_complete_data(symbol, contract_address, chain)
    return fetcher.to_consolidated_format(data)


# CLI support
if __name__ == "__main__":
    import sys

    logging.basicConfig(level=logging.INFO)

    if len(sys.argv) < 2:
        print("Usage: python3 dex_data_fetcher.py <SYMBOL> [contract_address] [chain]")
        print("Example: python3 dex_data_fetcher.py ALLOCA 0x123... monad")
        sys.exit(1)

    symbol = sys.argv[1].upper()
    contract = sys.argv[2] if len(sys.argv) > 2 else None
    chain = sys.argv[3] if len(sys.argv) > 3 else None

    print(f"\nFetching DEX data for {symbol}...")

    fetcher = DEXDataFetcher()
    data = fetcher.fetch_complete_data(symbol, contract, chain)
    consolidated = fetcher.to_consolidated_format(data)

    print(f"\n{'='*60}")
    print(f"  DEX DATA FOR {symbol}")
    print(f"{'='*60}")
    print(json.dumps(consolidated, indent=2, default=str))

    if data.errors:
        print(f"\n⚠️  Errors: {', '.join(data.errors)}")
