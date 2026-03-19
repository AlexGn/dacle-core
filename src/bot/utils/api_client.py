"""
Resilient API client for DACLE Bot.
Provides request retries and consistent error handling.
Session 457: Consolidated and hardened async client using httpx.
"""

import asyncio
import os
import time
from typing import Any, Dict, Optional, Tuple

import httpx
from src.utils.logger import get_logger
from src.bot.runtime_routing import get_bot_api_base_url

logger = get_logger(__name__)

DEFAULT_RETRIES = 2
DEFAULT_RETRY_DELAY = 1.0


class BotAPIError(Exception):
    """Exception raised when the backend API returns an error or is unreachable."""
    def __init__(self, message: str, status_code: Optional[int] = None, data: Optional[Dict[str, Any]] = None):
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.data = data or {}


def _api_headers() -> dict:
    api_key = os.getenv("DACLE_API_KEY", "").strip()
    return {"X-API-Key": api_key, "Accept": "application/json"} if api_key else {"Accept": "application/json"}


def _format_error_message(status_code: int, response_json: Any, exc: Optional[Exception] = None) -> str:
    """Extract human-readable error from FastAPI details or fallback to generic message."""
    if isinstance(response_json, dict) and "detail" in response_json:
        detail = response_json["detail"]
        if isinstance(detail, list):  # Pydantic validation error lists
            try:
                return f"Validation error: {detail[0].get('msg', str(detail))}"
            except Exception:
                return str(detail)
        return str(detail)
    
    if exc:
        return f"{type(exc).__name__}: {str(exc)}"
        
    if status_code == 404:
        return "Resource not found (404)"
    if status_code == 422:
        return "Invalid request data (422)"
    if status_code >= 500:
        return f"Internal Server Error ({status_code})"
        
    return f"API Error ({status_code})"


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
    Raises BotAPIError on failure, ensuring exact error strings propagate to Discord.
    
    Returns:
        JSON response dict if successful (2xx).
    """
    url = f"{get_bot_api_base_url().rstrip('/')}/{endpoint.lstrip('/')}"
    headers = _api_headers()
    
    last_err: Optional[Exception] = None
    
    async with httpx.AsyncClient(timeout=timeout) as client:
        for attempt in range(1, retries + 2):
            try:
                resp = await client.request(
                    method=method,
                    url=url,
                    json=json,
                    params=params,
                    headers=headers,
                )
                
                resp_json = None
                try:
                    resp_json = resp.json()
                except Exception:
                    pass
                
                if 200 <= resp.status_code < 300:
                    return resp_json if isinstance(resp_json, dict) else {}
                    
                # 4xx Client Errors - Don't retry, fail fast with detail
                if 400 <= resp.status_code < 500:
                    msg = _format_error_message(resp.status_code, resp_json)
                    logger.warning(f"API {method} {endpoint} rejected ({resp.status_code}): {msg}")
                    raise BotAPIError(msg, status_code=resp.status_code, data=resp_json)
                
                # 5xx Server Errors - Maybe transient, allow retry
                msg = _format_error_message(resp.status_code, resp_json)
                logger.warning(f"API {method} {endpoint} failed ({resp.status_code}) attempt {attempt}: {msg}")
                last_err = BotAPIError(msg, status_code=resp.status_code, data=resp_json)
                
            except httpx.RequestError as e:
                logger.warning(f"API {method} {endpoint} connection error attempt {attempt}: {e}")
                last_err = BotAPIError(f"Connection failed: {type(e).__name__}")
                
            if attempt <= retries:
                await asyncio.sleep(retry_delay * attempt)  # Exponential backoff
                
    if last_err:
        raise last_err
    raise BotAPIError("API request failed (unknown reason)")

