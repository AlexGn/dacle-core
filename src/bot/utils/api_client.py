"""
Resilient API client for DACLE Bot.
Provides request retries and consistent error handling.
"""

import asyncio
import os
from typing import Any, Dict, Optional, Union

import aiohttp
from src.utils.logger import get_logger
from src.bot.runtime_routing import get_bot_api_base_url

logger = get_logger(__name__)

DEFAULT_RETRIES = 3
DEFAULT_RETRY_DELAY = 2.0

def _api_headers() -> dict:
    api_key = os.getenv("DACLE_API_KEY", "").strip()
    return {"X-API-Key": api_key} if api_key else {}

async def api_request(
    method: str,
    endpoint: str,
    *,
    json: Optional[Dict[str, Any]] = None,
    params: Optional[Dict[str, Any]] = None,
    retries: int = DEFAULT_RETRIES,
    retry_delay: float = DEFAULT_RETRY_DELAY,
    timeout: int = 30,
) -> Optional[Dict[str, Any]]:
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
        JSON response dict or None if all attempts failed.
    """
    url = f"{get_bot_api_base_url().rstrip('/')}{endpoint}"
    headers = _api_headers()
    
    attempt = 0
    while attempt <= retries:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.request(
                    method=method,
                    url=url,
                    json=json,
                    params=params,
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=timeout),
                ) as resp:
                    if resp.status == 200:
                        return await resp.json()
                    
                    if resp.status == 422:
                        # Validation error - don't retry, but return data for caller to handle
                        return {"_status": 422, "_data": await resp.json()}
                    
                    logger.warning(
                        f"API request failed: {method} {endpoint} status={resp.status} (attempt {attempt+1}/{retries+1})"
                    )
                    if resp.status >= 500:
                        # Server error, might be worth retrying
                        pass
                    else:
                        # 4xx error (other than 422), probably not worth retrying
                        return {"_status": resp.status}
                        
        except (aiohttp.ClientConnectorError, aiohttp.ServerDisconnectedError, asyncio.TimeoutError) as e:
            logger.warning(
                f"API connection error: {method} {endpoint} err={e} (attempt {attempt+1}/{retries+1})"
            )
        except Exception as e:
            logger.error(f"Unexpected API request error: {method} {endpoint} err={e}")
            return None
            
        attempt += 1
        if attempt <= retries:
            await asyncio.sleep(retry_delay)
            
    logger.error(f"API request failed after {retries+1} attempts: {method} {endpoint}")
    return None
