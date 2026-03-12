import json
import logging
import os
from pathlib import Path
from datetime import datetime, timezone
from typing import Dict, Any
from src.trading_shared.models import TradeIntent
from src.utils.atomic_write import atomic_json_write

logger = logging.getLogger(__name__)

class IntentLogger:
    """
    Persists TradeIntents to a centralized JSONL audit log.
    Used by the Master Portfolio for cross-strategy decision auditing.
    """
    def __init__(self, log_dir: str = "data/audit"):
        self.log_dir = Path(log_dir)
        self.log_path = self.log_dir / "trade_intents.jsonl"
        os.makedirs(self.log_dir, exist_ok=True)

    async def log_intent(self, intent: TradeIntent):
        """Appends a TradeIntent to the persistent audit log."""
        try:
            # We use standard append here for performance; 
            # atomic_json_write is better for state files.
            entry = intent.model_dump(mode="json")
            
            with open(self.log_path, "a") as f:
                f.write(json.dumps(entry) + "\n")
        except Exception as e:
            logger.error(f"Failed to log TradeIntent: {e}")

    async def update_intent(self, intent_id: str, updates: Dict[str, Any]):
        """Update fields of an existing intent (e.g., RELEASED lease)."""
        # This implementation appends an 'intent_update' record for log consumers to process.
        update_record = {
            "entry_type": "intent_update",
            "intent_id": str(intent_id),
            "updates": updates,
            "ts": datetime.now(timezone.utc).isoformat()
        }
        try:
            with open(self.log_path, "a") as f:
                f.write(json.dumps(update_record) + "\n")
        except Exception as e:
            logger.error(f"Failed to log intent update: {e}")
