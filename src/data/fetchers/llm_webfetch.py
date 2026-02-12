#!/usr/bin/env python3
"""
LLM-powered WebFetch replacement for GitHub Actions

DEPRECATED: Use src.data.fetchers.llm_web.LLMWebFetch instead.
    from src.data.fetchers import LLMWebFetch

---

Supports OpenAI (recommended) and Anthropic Claude (high quality)

Environment Variables:
    OPENAI_API_KEY - OpenAI API key (recommended)
    ANTHROPIC_API_KEY - Anthropic API key
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
    """

    def __init__(self, provider: str = 'openai'):
        """
        Initialize LLM WebFetch

        Args:
            provider: 'openai' (paid, recommended) or 'anthropic' (high quality)
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
            self.model = "gpt-4o-mini"

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
            raise ValueError(f"Unsupported provider: {provider}. Use 'openai' or 'anthropic'")

    def fetch(
        self,
        url: str,
        prompt: str,
        max_retries: int = 2,
        timeout: int = 30
    ) -> List[Dict[str, Any]]:
        """
        Fetch webpage and extract structured data using LLM
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

            # Truncate if too long
            if self.provider == 'anthropic':
                max_chars = 100000
            else:  # openai
                max_chars = 80000

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
                else:  # anthropic
                    result = self._extract_with_anthropic(html_content, prompt)

                print(f"     ✅ Extracted {len(result)} items")
                return result

            except Exception as e:
                if attempt < max_retries - 1:
                    print(f"     ⚠️  Attempt {attempt + 1} failed, retrying...")
                else:
                    print(f"     ❌ LLM extraction failed after {max_retries} attempts: {e}")
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
                "temperature": 0.1,
                "max_tokens": 4000,
                "response_format": {"type": "json_object"}
            },
            timeout=60
        )
        response.raise_for_status()

        result = response.json()
        content = result['choices'][0]['message']['content']

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

        return self._parse_json_response(content)

    def _parse_json_response(self, content: str) -> List[Dict]:
        """Parse JSON from LLM response"""
        content = content.strip()
        if content.startswith('```'):
            lines = content.split('\n')
            content = '\n'.join(lines[1:-1])

        content = content.strip()

        try:
            data = json.loads(content)
            if isinstance(data, dict):
                data = [data]
            return data
        except json.JSONDecodeError:
            return []


def llm_webfetch(url: str, prompt: str, provider: str = 'openai') -> List[Dict]:
    """Quick LLM-based WebFetch"""
    fetcher = LLMWebFetch(provider=provider)
    return fetcher.fetch(url, prompt)
