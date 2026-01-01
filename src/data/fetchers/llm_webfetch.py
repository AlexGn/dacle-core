#!/usr/bin/env python3
"""
LLM-powered WebFetch replacement for GitHub Actions

DEPRECATED: Use src.data.fetchers.llm_web.LLMWebFetch instead.
    from src.data.fetchers import LLMWebFetch

---

Supports Together AI (cheap) and Anthropic Claude (high quality)

This replaces Claude Code's WebFetch tool with direct LLM API calls,
enabling intelligent web scraping in CI/CD environments.

Cost comparison per 1M input tokens:
- Together AI (Llama 3.1 70B): ~$0.10
- OpenAI GPT-4o-mini: ~$0.15 (RECOMMENDED - best quality/cost ratio)
- Anthropic Claude 3.5 Sonnet: ~$3.00
- OpenAI GPT-4o: ~$2.50

Usage (NEW):
    from src.data.fetchers import LLMWebFetch

Usage (DEPRECATED):
    from src.data.fetchers.llm_webfetch import LLMWebFetch

Environment Variables:
    OPENAI_API_KEY - OpenAI API key (recommended)
    TOGETHER_API_KEY - Together AI API key
    ANTHROPIC_API_KEY - Anthropic API key

Deprecated: 2025-12-26 (Session 256+ Refactoring)
"""
import warnings
warnings.warn(
    "scripts.helpers.llm_webfetch is deprecated. "
    "Use src.data.fetchers.LLMWebFetch instead.",
    DeprecationWarning,
    stacklevel=2
)

import os
import json
import requests
from typing import Dict, List, Optional, Any
from datetime import datetime


class LLMWebFetch:
    """
    Intelligent web content extraction using LLM APIs

    Replaces Claude Code's WebFetch with API-based alternatives
    that work in GitHub Actions and other CI environments.
    """

    def __init__(self, provider: str = 'together'):
        """
        Initialize LLM WebFetch

        Args:
            provider: 'together' (FREE, recommended), 'openai' (paid), or 'anthropic' (high quality)

        Session 268: Changed default from 'openai' to 'together' - OpenAI was costing $1/day
        for high-frequency scanning that produced zero useful results.
        Together.ai has $25 free credits and Llama 3.1 70B works well for extraction.
        """
        self.provider = provider

        if provider == 'openai':
            self.api_key = os.getenv('OPENAI_API_KEY')
            if not self.api_key:
                raise ValueError(
                    "OPENAI_API_KEY not found in environment. "
                    "Get API key at: https://platform.openai.com/api-keys"
                )
            self.api_url = "https://api.openai.com/v1/chat/completions"
            self.model = "gpt-4o-mini"  # Best quality/cost ratio for extraction

        elif provider == 'together':
            self.api_key = os.getenv('TOGETHER_API_KEY')
            if not self.api_key:
                raise ValueError(
                    "TOGETHER_API_KEY not found in environment. "
                    "Get free API key at: https://api.together.xyz/"
                )
            self.api_url = "https://api.together.xyz/v1/chat/completions"
            self.model = "meta-llama/Meta-Llama-3.1-70B-Instruct-Turbo"

        elif provider == 'anthropic':
            self.api_key = os.getenv('ANTHROPIC_API_KEY')
            if not self.api_key:
                raise ValueError(
                    "ANTHROPIC_API_KEY not found in environment. "
                    "Sign up at: https://console.anthropic.com/"
                )
            self.api_url = "https://api.anthropic.com/v1/messages"
            self.model = "claude-3-5-sonnet-20241022"

        else:
            raise ValueError(f"Unsupported provider: {provider}. Use 'openai', 'together', or 'anthropic'")

    def fetch(
        self,
        url: str,
        prompt: str,
        max_retries: int = 2,
        timeout: int = 30
    ) -> List[Dict[str, Any]]:
        """
        Fetch webpage and extract structured data using LLM

        Args:
            url: URL to fetch
            prompt: Extraction instructions for the LLM
            max_retries: Number of retries on failure
            timeout: Request timeout in seconds

        Returns:
            Extracted data as list of dictionaries
        """
        print(f"  🤖 LLM WebFetch ({self.provider}): {url}")

        # Step 1: Fetch HTML content
        try:
            response = requests.get(
                url,
                timeout=timeout,
                headers={
                    'User-Agent': 'Mozilla/5.0 (compatible; DACLE-TGE-Scanner/1.0)'
                }
            )
            response.raise_for_status()
            html_content = response.text

            # Debug logging
            print(f"     📄 HTTP {response.status_code} | Content: {len(html_content):,} chars")

            # Truncate if too long (LLMs have context limits)
            if self.provider == 'anthropic':
                max_chars = 100000  # Claude has large context
            elif self.provider == 'openai':
                max_chars = 80000   # GPT-4o-mini has good context
            else:  # together
                max_chars = 50000   # Llama has smaller context

            if len(html_content) > max_chars:
                print(f"     ⚠️  HTML truncated from {len(html_content):,} to {max_chars:,} chars")
                html_content = html_content[:max_chars]

        except requests.RequestException as e:
            print(f"     ❌ Failed to fetch {url}: {e}")
            return []

        # Step 2: Send to LLM for extraction
        for attempt in range(max_retries):
            try:
                if self.provider == 'openai':
                    result = self._extract_with_openai(html_content, prompt)
                elif self.provider == 'together':
                    result = self._extract_with_together(html_content, prompt)
                else:  # anthropic
                    result = self._extract_with_anthropic(html_content, prompt)

                print(f"     ✅ Extracted {len(result)} items")
                if len(result) == 0:
                    print(f"     ⚠️  LLM returned empty array - website structure may have changed")
                return result

            except Exception as e:
                error_type = type(e).__name__
                if attempt < max_retries - 1:
                    print(f"     ⚠️  Attempt {attempt + 1} failed ({error_type}), retrying...")
                else:
                    print(f"     ❌ LLM extraction failed after {max_retries} attempts: {error_type}: {e}")
                    return []

        return []

    def _extract_with_openai(self, html: str, prompt: str) -> List[Dict]:
        """Extract data using OpenAI GPT-4o-mini API"""

        system_prompt = """You are a data extraction expert. Extract structured data from HTML.
Return ONLY valid JSON array, no markdown formatting, no explanations.
If no data found, return empty array: []"""

        user_prompt = f"""{prompt}

HTML Content:
{html}

Return as JSON array of objects. Example format:
[{{"token_symbol": "ABC", "tge_date": "2025-12-15", ...}}]"""

        response = requests.post(
            self.api_url,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json"
            },
            json={
                "model": self.model,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                "temperature": 0.1,  # Low temp for consistent extraction
                "max_tokens": 4000,
                "response_format": {"type": "json_object"}  # Force JSON output
            },
            timeout=60
        )
        response.raise_for_status()

        result = response.json()
        content = result['choices'][0]['message']['content']

        # Parse JSON from response
        return self._parse_json_response(content)

    def _extract_with_together(self, html: str, prompt: str) -> List[Dict]:
        """Extract data using Together AI API"""

        system_prompt = """You are a data extraction expert. Extract structured data from HTML.
Return ONLY valid JSON array, no markdown formatting, no explanations.
If no data found, return empty array: []"""

        user_prompt = f"""{prompt}

HTML Content:
{html}

Return as JSON array of objects. Example format:
[{{"token_symbol": "ABC", "tge_date": "2025-12-15", ...}}]"""

        response = requests.post(
            self.api_url,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json"
            },
            json={
                "model": self.model,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                "temperature": 0.1,  # Low temp for consistent extraction
                "max_tokens": 4000
            },
            timeout=60
        )
        response.raise_for_status()

        result = response.json()
        content = result['choices'][0]['message']['content']

        # Parse JSON from response
        return self._parse_json_response(content)

    def _extract_with_anthropic(self, html: str, prompt: str) -> List[Dict]:
        """Extract data using Anthropic Claude API"""

        user_prompt = f"""{prompt}

HTML Content:
{html}

Return ONLY a valid JSON array. No markdown, no explanations.
If no data found, return: []"""

        response = requests.post(
            self.api_url,
            headers={
                "x-api-key": self.api_key,
                "Content-Type": "application/json",
                "anthropic-version": "2023-06-01"
            },
            json={
                "model": self.model,
                "max_tokens": 4000,
                "temperature": 0.1,
                "messages": [
                    {
                        "role": "user",
                        "content": user_prompt
                    }
                ]
            },
            timeout=60
        )
        response.raise_for_status()

        result = response.json()
        content = result['content'][0]['text']

        # Parse JSON from response
        return self._parse_json_response(content)

    def _parse_json_response(self, content: str) -> List[Dict]:
        """
        Parse JSON from LLM response, handling markdown code blocks
        """
        # Remove markdown code blocks if present
        content = content.strip()
        if content.startswith('```'):
            lines = content.split('\n')
            content = '\n'.join(lines[1:-1])  # Remove first and last line

        content = content.strip()

        # Try to parse JSON
        try:
            data = json.loads(content)

            # Ensure it's a list
            if isinstance(data, dict):
                data = [data]
            elif not isinstance(data, list):
                print(f"     ⚠️  Unexpected format, wrapping in list")
                data = [data]

            return data

        except json.JSONDecodeError as e:
            print(f"     ⚠️  JSON parse error: {e}")
            print(f"     Content preview: {content[:200]}...")
            return []


# Convenience function for quick usage
def llm_webfetch(url: str, prompt: str, provider: str = 'openai') -> List[Dict]:
    """
    Quick LLM-based WebFetch

    Usage:
        results = llm_webfetch(
            "https://cryptorank.io/upcoming-ico",
            "Extract TGEs with symbol, date, FDV",
            provider='openai'  # or 'together', 'anthropic'
        )
    """
    fetcher = LLMWebFetch(provider=provider)
    return fetcher.fetch(url, prompt)


if __name__ == "__main__":
    # Test with CryptoRank
    print("Testing LLM WebFetch with CryptoRank...")

    prompt = """Extract upcoming TGE/ICO listings.
For each listing extract:
- token_symbol (string)
- token_name (string)
- tge_date (YYYY-MM-DD format or null)
- fdv_usd (number or null)

Return as JSON array."""

    results = llm_webfetch(
        "https://cryptorank.io/upcoming-ico",
        prompt,
        provider='openai'  # Change to 'together' or 'anthropic' to test others
    )

    print(f"\n✅ Found {len(results)} TGEs:")
    for tge in results[:3]:  # Show first 3
        print(f"   - {tge.get('token_symbol')}: {tge.get('token_name')} ({tge.get('tge_date')})")
