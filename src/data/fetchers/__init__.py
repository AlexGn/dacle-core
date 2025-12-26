"""
Unified Data Fetchers Module

Session 255+ Refactoring: Consolidates 15 helper fetchers into 3 logical modules.

This module provides a clean, unified interface for all data fetching operations:
- Token Data: CryptoRank, ICODrops, Dropstab, CoinGecko, CMC
- LLM/Web: Perplexity, Claude, OpenAI for web content extraction
- Exchange: MEXC calendar, price fetching, Twitter announcements

Usage:
    from src.data.fetchers import (
        # Token data fetchers
        fetch_cryptorank_web,
        fetch_icodrops_data,
        fetch_dropstab_data,
        fetch_from_primary_sources,

        # LLM/Web fetchers
        LLMWebFetch,
        fetch_with_perplexity,

        # Exchange fetchers
        fetch_mexc_calendar,
        fetch_token_price,
    )

Migration Status:
- Phase 1: Re-export wrappers (current)
- Phase 2: Move actual code here
- Phase 3: Deprecate old helpers
"""

# Token data fetchers
from src.data.fetchers.token_data import (
    fetch_cryptorank_web,
    fetch_icodrops_data,
    fetch_dropstab_data,
    fetch_from_primary_sources,
    fetch_coingecko,
    fetch_coinmarketcap,
    PrimarySourceFetcher,
)

# LLM/Web fetchers
from src.data.fetchers.llm_web import (
    LLMWebFetch,
    fetch_with_perplexity,
    validate_with_perplexity,
    ClaudeWebFetcher,
)

# Exchange/Price fetchers
from src.data.fetchers.exchange import (
    fetch_mexc_calendar,
    fetch_token_price,
    fetch_mexc_twitter,
    MEXCCalendarFetcher,
)

__all__ = [
    # Token data
    'fetch_cryptorank_web',
    'fetch_icodrops_data',
    'fetch_dropstab_data',
    'fetch_from_primary_sources',
    'fetch_coingecko',
    'fetch_coinmarketcap',
    'PrimarySourceFetcher',

    # LLM/Web
    'LLMWebFetch',
    'fetch_with_perplexity',
    'validate_with_perplexity',
    'ClaudeWebFetcher',

    # Exchange
    'fetch_mexc_calendar',
    'fetch_token_price',
    'fetch_mexc_twitter',
    'MEXCCalendarFetcher',
]
