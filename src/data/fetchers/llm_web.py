"""
LLM/Web Fetchers Module

Consolidates LLM-powered web content extraction:
- LLMWebFetch: Multi-provider (OpenAI, Together, Anthropic)
- Perplexity: Real-time web search with citations
- Claude: High-quality web content analysis

This module provides intelligent web scraping using LLM APIs,
enabling extraction of structured data from unstructured web content.

Usage:
    from src.data.fetchers.llm_web import (
        LLMWebFetch,
        fetch_with_perplexity,
        ClaudeWebFetcher,
    )

    # OpenAI-powered web fetch (recommended for cost)
    fetcher = LLMWebFetch(provider='openai')
    result = fetcher.fetch(
        url="https://cryptorank.io/upcoming-ico",
        prompt="Extract upcoming TGEs with symbol, date, FDV..."
    )

    # Perplexity for real-time web search
    result = fetch_with_perplexity(
        query="MONAD token TGE date FDV",
        system_prompt="Extract token data as JSON"
    )
"""

import logging
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

# Ensure scripts.helpers is importable
_project_root = Path(__file__).parent.parent.parent.parent
if str(_project_root) not in sys.path:  # pragma: no cover
    sys.path.insert(0, str(_project_root))

logger = logging.getLogger(__name__)


# =============================================================================
# LLMWebFetch - Multi-provider web content extraction
# =============================================================================

class LLMWebFetch:
    """
    Intelligent web content extraction using LLM APIs.

    Replaces Claude Code's WebFetch with API-based alternatives
    that work in GitHub Actions and other CI environments.

    Providers:
    - openai: GPT-4o-mini (recommended, best cost/quality)
    - together: Llama 3.1 70B (cheapest)
    - anthropic: Claude 3.5 Sonnet (highest quality)

    Usage:
        fetcher = LLMWebFetch(provider='openai')
        result = fetcher.fetch(
            url="https://example.com",
            prompt="Extract all product names and prices"
        )
    """

    def __init__(self, provider: str = 'openai'):
        """
        Initialize LLM WebFetch.

        Args:
            provider: 'openai' (recommended), 'together' (cheap), or 'anthropic'
        """
        from scripts.helpers.llm_webfetch import LLMWebFetch as _LLMWebFetch
        self._impl = _LLMWebFetch(provider=provider)

    def fetch(
        self,
        url: str,
        prompt: str,
        max_content_length: int = 50000,
        timeout: int = 30
    ) -> Optional[Dict[str, Any]]:
        """
        Fetch URL and extract structured data using LLM.

        Args:
            url: URL to fetch
            prompt: Extraction instructions for the LLM
            max_content_length: Max chars to send to LLM
            timeout: HTTP timeout in seconds

        Returns:
            Dict with extracted data, or None on error
        """
        return self._impl.fetch(
            url=url,
            prompt=prompt,
            max_content_length=max_content_length,
            timeout=timeout
        )


# =============================================================================
# Perplexity Fetcher - Real-time web search
# =============================================================================

def fetch_with_perplexity(
    query: str,
    system_prompt: Optional[str] = None,
    model: str = "llama-3.1-sonar-small-128k-online",
    max_tokens: int = 4000,
) -> Optional[Dict[str, Any]]:
    """
    Fetch real-time web data using Perplexity's search API.

    Perplexity combines web search with LLM synthesis, providing
    up-to-date information with source citations.

    Args:
        query: Search query (e.g., "MONAD token TGE date FDV")
        system_prompt: Optional system prompt for extraction
        model: Perplexity model to use
        max_tokens: Maximum response tokens

    Returns:
        Dict with extracted data and citations
    """
    from scripts.helpers.auto_perplexity_trigger import fetch_with_perplexity as _fetch
    return _fetch(
        query=query,
        system_prompt=system_prompt,
        model=model,
        max_tokens=max_tokens
    )


def validate_with_perplexity(
    token: str,
    token_name: Optional[str] = None,
    claimed_data: Optional[Dict] = None,
) -> Dict[str, Any]:
    """
    Validate token data using Perplexity's real-time search.

    Cross-references claimed data against live web sources.

    Args:
        token: Token symbol
        token_name: Token name
        claimed_data: Data to validate

    Returns:
        Validation result with confidence scores
    """
    from scripts.helpers.perplexity_validator import validate_token_data as _validate
    return _validate(
        token=token,
        token_name=token_name,
        claimed_data=claimed_data
    )


# =============================================================================
# Claude Web Fetcher - High-quality extraction
# =============================================================================

class ClaudeWebFetcher:
    """
    High-quality web content extraction using Claude API.

    Best for complex extraction tasks requiring nuanced understanding.
    Higher cost but superior quality for ambiguous content.

    Usage:
        fetcher = ClaudeWebFetcher()
        result = fetcher.fetch(
            url="https://example.com/whitepaper",
            prompt="Extract tokenomics and vesting schedule"
        )
    """

    def __init__(self, model: str = "claude-3-5-sonnet-20241022"):
        """
        Initialize Claude Web Fetcher.

        Args:
            model: Claude model to use
        """
        self.model = model
        # Lazy import to avoid circular dependencies
        self._impl = None

    def _get_impl(self):
        """Lazy initialization of implementation."""
        if self._impl is None:
            from scripts.helpers.claude_web_fetcher import ClaudeWebFetcher as _Impl
            self._impl = _Impl(model=self.model)
        return self._impl

    def fetch(
        self,
        url: str,
        prompt: str,
        max_content_length: int = 100000,
    ) -> Optional[Dict[str, Any]]:
        """
        Fetch URL and extract data using Claude.

        Args:
            url: URL to fetch
            prompt: Extraction instructions
            max_content_length: Max chars to process

        Returns:
            Extracted data dict
        """
        return self._get_impl().fetch(
            url=url,
            prompt=prompt,
            max_content_length=max_content_length
        )


# =============================================================================
# WebFetch Helpers
# =============================================================================

def extract_json_from_response(response: str) -> Optional[Dict]:
    """
    Extract JSON from LLM response text.

    Handles various formats:
    - Direct JSON
    - Markdown code blocks
    - JSON embedded in text

    Args:
        response: LLM response text

    Returns:
        Extracted JSON dict or None
    """
    from scripts.helpers.webfetch_helpers import extract_json_from_response as _extract
    return _extract(response)


def clean_html_for_llm(html: str, max_length: int = 50000) -> str:
    """
    Clean HTML content for LLM processing.

    Removes scripts, styles, and unnecessary elements.
    Converts to clean text while preserving structure.

    Args:
        html: Raw HTML content
        max_length: Maximum output length

    Returns:
        Cleaned text content
    """
    from scripts.helpers.webfetch_helpers import clean_html_for_llm as _clean
    return _clean(html, max_length)


# =============================================================================
# Module exports
# =============================================================================

__all__ = [
    'LLMWebFetch',
    'fetch_with_perplexity',
    'validate_with_perplexity',
    'ClaudeWebFetcher',
    'extract_json_from_response',
    'clean_html_for_llm',
]
