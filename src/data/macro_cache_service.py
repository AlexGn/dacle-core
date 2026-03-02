#!/usr/bin/env python3
"""
Macro Data Cache Service - Session 474 Hardening
Centralized service for fetching and caching external macro data (CoinGecko, Investing.com)

Features:
- Dual-layer caching: Redis (hot) + Local JSON (fallback)
- Rate limit (429) protection with fail-open stale data fallback
- Centralized fetch logic for L088 and Economic Calendar
"""

import json
import logging
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import httpx
import requests
from src.utils.redis_cache import get_redis_cache

logger = logging.getLogger(__name__)

# Constants
PROJECT_ROOT = Path(__file__).parent.parent.parent
MACRO_CACHE_DIR = PROJECT_ROOT / "data" / "cache" / "macro"
CG_CACHE_FILE = MACRO_CACHE_DIR / "coingecko_global.json"
ECON_CACHE_FILE = MACRO_CACHE_DIR / "economic_calendar.json"

# TTLs
CG_TTL = 15 * 60  # 15 minutes
ECON_TTL = 60 * 60  # 1 hour

class MacroDataService:
    """Service to handle external macro data with robust caching."""
    
    def __init__(self):
        MACRO_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        self.redis = get_redis_cache()

    def get_coingecko_global(self, force_refresh: bool = False) -> Optional[Dict[str, Any]]:
        """
        Get global market data from CoinGecko with fallback caching.
        
        Returns:
            Dictionary containing market_cap_percentage, total_market_cap, etc.
        """
        cache_key = "coingecko_global"
        
        # 1. Try Redis
        if not force_refresh:
            cached = self.redis.get(cache_key, namespace="macro")
            if cached:
                return cached

        # 2. Try Live API
        try:
            response = httpx.get(
                "https://api.coingecko.com/api/v3/global",
                timeout=10.0,
                headers={"User-Agent": "DACLE-Bot/1.0"}
            )
            
            if response.status_code == 200:
                data = response.json()
                if "data" in data:
                    # Save to Redis
                    self.redis.set(cache_key, data["data"], ttl_seconds=CG_TTL, namespace="macro")
                    # Save to Disk
                    self._save_to_disk(CG_CACHE_FILE, data["data"])
                    return data["data"]
            elif response.status_code == 429:
                logger.warning("CoinGecko API rate limited (429) - falling back to disk cache")
            else:
                logger.warning(f"CoinGecko API error: {response.status_code}")
                
        except Exception as e:
            logger.error(f"CoinGecko fetch exception: {e}")

        # 3. Fallback to Disk
        return self._load_from_disk(CG_CACHE_FILE, max_age_hours=24)

    def get_economic_calendar(self, force_refresh: bool = False) -> List[Dict[str, Any]]:
        """
        Get economic calendar data with fallback caching.
        """
        cache_key = "economic_calendar"
        
        # 1. Try Redis
        if not force_refresh:
            cached = self.redis.get(cache_key, namespace="macro")
            if cached:
                return cached

        # 2. Try Live API (Placeholder for full implementation from economic_calendar.py)
        # Note: In production, we'll likely move the actual fetch logic here 
        # but for now we'll focus on the caching architecture.
        return self._load_from_disk(ECON_CACHE_FILE, max_age_hours=48) or []

    def _save_to_disk(self, path: Path, data: Any):
        """Save data to local JSON cache."""
        try:
            payload = {
                "timestamp": datetime.utcnow().isoformat(),
                "data": data
            }
            with open(path, 'w') as f:
                json.dump(payload, f, indent=2)
        except Exception as e:
            logger.error(f"Failed to save macro cache to disk ({path.name}): {e}")

    def _load_from_disk(self, path: Path, max_age_hours: int) -> Optional[Any]:
        """Load data from local JSON cache if not too old."""
        if not path.exists():
            return None
            
        try:
            with open(path) as f:
                payload = json.load(f)
                
            ts = datetime.fromisoformat(payload["timestamp"])
            age = datetime.utcnow() - ts
            
            if age > timedelta(hours=max_age_hours):
                logger.warning(f"Disk cache {path.name} is too old ({age.total_seconds()/3600:.1f}h)")
                # Return even if old as last resort, but log warning
                
            return payload["data"]
        except Exception as e:
            logger.error(f"Failed to load macro cache from disk ({path.name}): {e}")
            return None

# Singleton
_service = None

def get_macro_data_service() -> MacroDataService:
    global _service
    if _service is None:
        _service = MacroDataService()
    return _service
