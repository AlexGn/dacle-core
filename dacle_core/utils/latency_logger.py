import json
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

LATENCY_LOG_DIR = Path("data/logs")
LATENCY_LOG_FILE = LATENCY_LOG_DIR / "latency_audit.jsonl"
SCHEMA_VERSION = "1.0.0"

class LatencyAuditLogger:
    """Standardized high-precision latency logger (Session 488)."""
    
    _instance = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(LatencyAuditLogger, cls).__new__(cls)
            cls._instance._init_storage()
        return cls._instance
        
    def _init_storage(self):
        LATENCY_LOG_DIR.mkdir(parents=True, exist_ok=True)

    def log_event(self, intent_id: str, symbol: str, side: str, 
                  prices: Dict[str, float], 
                  timestamps: Dict[str, int],
                  metadata: Optional[Dict[str, Any]] = None) -> None:
        """
        Write a structured latency event to JSONL.
        
        Timestamps should be in nanoseconds (time.monotonic_ns()).
        """
        try:
            event = {
                "version": SCHEMA_VERSION,
                "intent_id": intent_id,
                "symbol": symbol,
                "side": side,
                "wall_time": datetime.now(timezone.utc).isoformat(),
                "prices": {
                    "requested": prices.get("requested", 0.0),
                    "expected_vwap": prices.get("expected_vwap", 0.0),
                    "actual_vwap": prices.get("actual_vwap", 0.0),
                },
                "metrics": {
                    "fill_ratio": metadata.get("fill_ratio", 0.0) if metadata else 0.0,
                    "slippage_bps": self._calculate_slippage(prices),
                },
                "slices_ns": timestamps,
                "metadata": metadata or {}
            }
            
            with open(LATENCY_LOG_FILE, "a") as f:
                f.write(json.dumps(event) + "\n")
        except Exception as e:
            logger.error(f"Failed to log latency event: {e}")

    def _calculate_slippage(self, prices: Dict[str, float]) -> float:
        req = prices.get("requested", 0.0)
        actual = prices.get("actual_vwap", 0.0)
        if req <= 0 or actual <= 0:
            return 0.0
        # (actual - req) / req * 10000
        return round((actual - req) / req * 10000, 2)
