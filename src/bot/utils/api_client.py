"""Resilient API client for DACLE Bot."""

import asyncio
import os
from dataclasses import dataclass
from typing import Any, Dict, Optional

import httpx

from src.bot.runtime_routing import get_bot_api_base_url
from src.utils.logger import get_logger

logger = get_logger(__name__)

DEFAULT_RETRIES = 3
DEFAULT_RETRY_DELAY = 2.0


@dataclass
class BotAPIError(Exception):
    message: str
    status_code: Optional[int] = None

    def __str__(self) -> str:
        return self.message

def _api_headers() -> dict:
    api_key = os.getenv("DACLE_API_KEY", "").strip()
    return {"X-API-Key": api_key} if api_key else {}


def _extract_error_message(response: httpx.Response, payload: Any) -> str:
    if isinstance(payload, dict):
        detail = payload.get("detail")
        if isinstance(detail, str) and detail.strip():
            return detail.strip()
        if isinstance(detail, list) and detail:
            first = detail[0]
            if isinstance(first, dict):
                msg = str(first.get("msg") or "").strip()
                if msg:
                    return f"Validation error: {msg}"
    return f"HTTP {response.status_code}"

async def api_request(
    method: str,
    endpoint: str,
    *,
    json: Optional[Dict[str, Any]] = None,
    params: Optional[Dict[str, Any]] = None,
    retries: int = DEFAULT_RETRIES,
    retry_delay: float = DEFAULT_RETRY_DELAY,
    timeout: int = 30,
) -> Dict[str, Any]:
    """
    Perform a resilient API request with retries.
    
    Args:
        method: HTTP method (GET, POST, etc.)
        endpoint: API endpoint (e.g., "/api/execution/levels")
        json: JSON payload for POST/PATCH
        params: Query parameters
        retries: Number of retry attempts for connection errors
        retry_delay: Delay between retries in seconds
        timeout: Request timeout in seconds
        
    Returns:
        JSON response dict.
    """
    url = f"{get_bot_api_base_url().rstrip('/')}{endpoint}"
    headers = _api_headers()
    
    attempt = 0
    while attempt <= retries:
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.request(
                    method=method,
                    url=url,
                    json=json,
                    params=params,
                    headers=headers,
                )
                payload: Any = None
                try:
                    payload = response.json()
                except Exception:
                    payload = None

                if response.status_code < 400:
                    if isinstance(payload, dict):
                        return payload
                    return {}

                message = _extract_error_message(response, payload)
                logger.warning(
                    "API request failed: %s %s status=%s (attempt %s/%s)",
                    method,
                    endpoint,
                    response.status_code,
                    attempt + 1,
                    retries + 1,
                )
                if response.status_code >= 500 and attempt < retries:
                    attempt += 1
                    await asyncio.sleep(retry_delay)
                    continue
                raise BotAPIError(message=message, status_code=response.status_code)

        except (httpx.TimeoutException, httpx.RequestError) as e:
            logger.warning(
                "API connection error: %s %s err=%s (attempt %s/%s)",
                method,
                endpoint,
                e,
                attempt + 1,
                retries + 1,
            )
            if attempt >= retries:
                raise BotAPIError(message=f"Connection failed: {type(e).__name__}", status_code=None) from e
        except Exception as e:
            if isinstance(e, BotAPIError):
                raise
            logger.error(f"Unexpected API request error: {method} {endpoint} err={e}")
            raise BotAPIError(message=f"Unexpected error: {type(e).__name__}", status_code=None) from e
            
        attempt += 1
        if attempt <= retries:
            await asyncio.sleep(retry_delay)

    raise BotAPIError(message=f"API request failed after {retries+1} attempts", status_code=None)
