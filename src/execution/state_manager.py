import json
import logging
import os
import hashlib
import hmac
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any, Optional, List
from threading import Lock

from src.execution.v2_models import ExecutionState, VetoReasonCode

logger = logging.getLogger(__name__)

# Persistence paths
DATA_DIR = Path("data/execution")
INTENTS_FILE = DATA_DIR / "execution_intents.json"
EVENTS_FILE = DATA_DIR / "execution_events.json"
EVENTS_CHAIN_FILE = DATA_DIR / "execution_events.chain.jsonl"
EVENTS_CHAIN_HEAD_FILE = DATA_DIR / "execution_events.chain.head.json"

class ExecutionStateManager:
    """
    Idempotent state manager for execution setups (PH2-04).
    Enforces the state machine and prevents duplicate submissions.
    """
    
    _instance = None
    _lock = Lock()
    
    def __new__(cls):
        with cls._lock:
            if cls._instance is None:
                cls._instance = super(ExecutionStateManager, cls).__new__(cls)
                cls._instance._init_storage()
        return cls._instance
        
    def _init_storage(self):
        """Ensure data directory and persistence files exist."""
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        if not INTENTS_FILE.exists():
            INTENTS_FILE.write_text("{}")
        if not EVENTS_FILE.exists():
            EVENTS_FILE.write_text("[]")
        if not EVENTS_CHAIN_FILE.exists():
            EVENTS_CHAIN_FILE.write_text("")
        if not EVENTS_CHAIN_HEAD_FILE.exists():
            EVENTS_CHAIN_HEAD_FILE.write_text(json.dumps({
                "seq": 0,
                "last_hash": "GENESIS",
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }, indent=2))
            
        self._intents_lock = Lock()
        self._events_lock = Lock()

    def get_intent(self, idempotency_key: str) -> Optional[Dict[str, Any]]:
        """Retrieve execution intent by idempotency key."""
        with self._intents_lock:
            try:
                with open(INTENTS_FILE, "r") as f:
                    intents = json.load(f)
                return intents.get(idempotency_key)
            except Exception as e:
                logger.error(f"Failed to read intents: {e}")
                return None

    def create_intent(self, idempotency_key: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Create a new execution intent if it doesn't exist."""
        with self._intents_lock:
            try:
                with open(INTENTS_FILE, "r") as f:
                    intents = json.load(f)
                
                if idempotency_key in intents:
                    # Check for conflict
                    existing = intents[idempotency_key]
                    if existing["symbol"] != payload["symbol"] or existing["side"] != payload["side"]:
                        self._log_event(
                            idempotency_key,
                            "IDEMPOTENCY_CONFLICT",
                            {
                                "existing_symbol": existing.get("symbol"),
                                "existing_side": existing.get("side"),
                                "incoming_symbol": payload.get("symbol"),
                                "incoming_side": payload.get("side"),
                            },
                        )
                        raise ValueError("VETO_IDEMPOTENCY_CONFLICT: payload mismatch for key")
                    self._log_event(
                        idempotency_key,
                        "IDEMPOTENCY_REPLAY",
                        {"idempotency_hit": True, "replay_state": existing.get("state")},
                    )
                    return existing
                
                # New intent
                intent = {
                    "idempotency_key": idempotency_key,
                    "state": ExecutionState.DISCOVERED,
                    "created_at": datetime.now(timezone.utc).isoformat(),
                    **payload
                }
                intents[idempotency_key] = intent
                
                with open(INTENTS_FILE, "w") as f:
                    json.dump(intents, f, indent=4)
                    
                self._log_event(idempotency_key, ExecutionState.DISCOVERED)
                return intent
            except Exception as e:
                logger.error(f"Failed to create intent: {e}")
                raise

    def transition_to(self, idempotency_key: str, next_state: ExecutionState, metadata: Dict[str, Any] = None) -> bool:
        """
        Transition an intent to a new state if valid.
        Enforces the State Transition Table.
        """
        with self._intents_lock:
            try:
                with open(INTENTS_FILE, "r") as f:
                    intents = json.load(f)
                
                if idempotency_key not in intents:
                    return False
                    
                intent = intents[idempotency_key]
                current_state = ExecutionState(intent["state"])
                
                if not self._is_valid_transition(current_state, next_state):
                    logger.warning(f"Invalid transition: {current_state} -> {next_state}")
                    self._log_event(
                        idempotency_key,
                        "TRANSITION_REJECTED",
                        {
                            "from_state": current_state,
                            "to_state": next_state,
                            "transition_rejected": True,
                        },
                    )
                    return False
                
                # Update intent
                intent["state"] = next_state
                intent["updated_at"] = datetime.now(timezone.utc).isoformat()
                if metadata:
                    intent.update(metadata)
                    
                intents[idempotency_key] = intent
                with open(INTENTS_FILE, "w") as f:
                    json.dump(intents, f, indent=4)
                
                self._log_event(idempotency_key, next_state, metadata)
                return True
            except Exception as e:
                logger.error(f"Transition failed: {e}")
                return False

    def list_active_intents(self) -> List[Dict[str, Any]]:
        """List all intents in an active execution state."""
        with self._intents_lock:
            try:
                with open(INTENTS_FILE, "r") as f:
                    intents = json.load(f)
                
                active_states = [
                    ExecutionState.SUBMITTED,
                    ExecutionState.PARTIALLY_FILLED,
                ]
                
                return [i for i in intents.values() if i["state"] in active_states]
            except Exception as e:
                logger.error(f"Failed to list active intents: {e}")
                return []

    def update_intent_metadata(self, idempotency_key: str, metadata: Dict[str, Any]) -> bool:
        """Update non-state metadata for an intent without forcing a state transition."""
        if not metadata:
            return True
        with self._intents_lock:
            try:
                with open(INTENTS_FILE, "r") as f:
                    intents = json.load(f)
                if idempotency_key not in intents:
                    return False

                intent = intents[idempotency_key]
                intent.update(metadata)
                intent["updated_at"] = datetime.now(timezone.utc).isoformat()
                intents[idempotency_key] = intent

                with open(INTENTS_FILE, "w") as f:
                    json.dump(intents, f, indent=4)

                self._log_event(idempotency_key, ExecutionState(intent["state"]), {"metadata_update": metadata})
                return True
            except Exception as e:
                logger.error(f"Metadata update failed: {e}")
                return False

    def _is_valid_transition(self, from_state: ExecutionState, to_state: ExecutionState) -> bool:
        """
        Enforce the State Transition Table logic.
        """
        # Define allowed transitions
        allowed = {
            ExecutionState.DISCOVERED: [ExecutionState.PENDING_APPROVAL],
            ExecutionState.PENDING_APPROVAL: [ExecutionState.APPROVED_RECEIVED, ExecutionState.CANCELED],
            ExecutionState.APPROVED_RECEIVED: [ExecutionState.REVALIDATING],
            ExecutionState.REVALIDATING: [ExecutionState.READY, ExecutionState.VETOED],
            ExecutionState.READY: [ExecutionState.SUBMITTING, ExecutionState.VETOED],
            ExecutionState.SUBMITTING: [ExecutionState.SUBMITTED, ExecutionState.FAILED],
            ExecutionState.SUBMITTED: [ExecutionState.PARTIALLY_FILLED, ExecutionState.FILLED, ExecutionState.CANCELED, ExecutionState.EXPIRED, ExecutionState.FAILED],
            ExecutionState.PARTIALLY_FILLED: [ExecutionState.FILLED, ExecutionState.CANCELED, ExecutionState.EXPIRED, ExecutionState.FAILED],
        }
        
        return to_state in allowed.get(from_state, [])

    def _read_chain_head(self) -> Dict[str, Any]:
        try:
            with open(EVENTS_CHAIN_HEAD_FILE, "r") as f:
                head = json.load(f)
            if not isinstance(head, dict):
                return {"seq": 0, "last_hash": "GENESIS"}
            return {
                "seq": int(head.get("seq", 0) or 0),
                "last_hash": str(head.get("last_hash", "GENESIS")),
            }
        except Exception:
            return {"seq": 0, "last_hash": "GENESIS"}

    def _append_chain_event(self, event: Dict[str, Any]) -> None:
        head = self._read_chain_head()
        seq = int(head["seq"]) + 1
        prev_hash = str(head["last_hash"])

        hash_payload = {
            "seq": seq,
            "prev_hash": prev_hash,
            "event": event,
        }
        encoded_payload = json.dumps(hash_payload, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
        event_hash = hashlib.sha256(encoded_payload).hexdigest()
        signing_key = os.getenv("EXECUTION_AUDIT_HMAC_KEY", "").strip()
        signature = None
        if signing_key:
            signature = hmac.new(signing_key.encode("utf-8"), event_hash.encode("utf-8"), hashlib.sha256).hexdigest()

        chain_record = {
            "seq": seq,
            "prev_hash": prev_hash,
            "hash": event_hash,
            "signature": signature,
            "event": event,
        }
        with open(EVENTS_CHAIN_FILE, "a") as f:
            f.write(json.dumps(chain_record, sort_keys=True) + "\n")

        with open(EVENTS_CHAIN_HEAD_FILE, "w") as f:
            json.dump(
                {
                    "seq": seq,
                    "last_hash": event_hash,
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                },
                f,
                indent=2,
            )

    def _log_event(self, idempotency_key: str, state: Any, metadata: Dict[str, Any] = None):
        """Append an event to the audit trail."""
        with self._events_lock:
            try:
                with open(EVENTS_FILE, "r") as f:
                    events = json.load(f)

                event = {
                    "idempotency_key": idempotency_key,
                    "state": state,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "metadata": metadata
                }
                events.append(event)

                with open(EVENTS_FILE, "w") as f:
                    json.dump(events, f, indent=4)
                self._append_chain_event(event)
            except Exception as e:
                logger.error(f"Event logging failed: {e}")
