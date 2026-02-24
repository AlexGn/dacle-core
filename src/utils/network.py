"""
Standardized network utilities for DACLE.
Provides browser-like headers to avoid Cloudflare/API 403s.
"""

import aiohttp
import httpx

# High-reputation browser user agent
DEFAULT_USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"

def get_standard_headers(extra_headers: dict = None) -> dict:
    """Return standard headers including a browser-like User-Agent."""
    headers = {
        "User-Agent": DEFAULT_USER_AGENT,
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "en-US,en;q=0.9",
    }
    if extra_headers:
        headers.update(extra_headers)
    return headers

def get_httpx_headers(extra_headers: dict = None) -> httpx.Headers:
    """Return standard headers as an httpx.Headers object."""
    import httpx
    return httpx.Headers(get_standard_headers(extra_headers))

def create_standard_session(timeout: aiohttp.ClientTimeout = None, **kwargs) -> aiohttp.ClientSession:
    """Create an aiohttp session with standard headers."""
    headers = get_standard_headers(kwargs.pop("headers", {}))
    return aiohttp.ClientSession(headers=headers, timeout=timeout, **kwargs)
