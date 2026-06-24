import json
import logging
import os
import base64
import time
import hashlib
import hmac
import asyncio
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any, Optional, List, Tuple
from threading import Lock
from uuid import uuid4

from dacle_core.execution.v2_models import ExecutionState, VetoReasonCode
from dacle_core.execution.context_token_store import ContextTokenStore

logger = logging.getLogger(__name__)

# Persistence paths
DATA_DIR = Path("data/execution")
INTENTS_FILE = DATA_DIR / "execution_intents.json"
EVENTS_FILE = DATA_DIR / "execution_events.json"
EVENTS_CHAIN_FILE = DATA_DIR / "execution_events.chain.jsonl"
EVENTS_CHAIN_HEAD_FILE = DATA_DIR / "execution_events.chain.head.json"
DEFAULT_CONTEXT_TOKEN_TTL_SEC = 60
DEFAULT_CONTEXT_CLOCK_SKEW_SEC = 5
DEV_ONLY_CONTEXT_SECRET = "DEV_ONLY_EXECUTION_CONTEXT_SECRET"
DEFAULT_CONTEXT_NONCE_PREFIX = "exec_ctx_nonce:"
DEFAULT_CONTEXT_NONCE_TIMEOUT_MS = 50

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
        self._nonce_lock = Lock()
        self._async_intents_lock: Optional[asyncio.Lock] = None
        self._async_events_lock: Optional[asyncio.Lock] = None
        self._consumed_context_nonces: Dict[str, int] = {}
        self._context_store: Optional[ContextTokenStore] = None
        self._context_store_config: Optional[Tuple[str, str, int]] = None

    def _get_async_intents_lock(self) -> asyncio.Lock:
        if self._async_intents_lock is None:
            self._async_intents_lock = asyncio.Lock()
        return self._async_intents_lock

    def _get_async_events_lock(self) -> asyncio.Lock:
        if self._async_events_lock is None:
            self._async_events_lock = asyncio.Lock()
        return self._async_events_lock

    def _load_intents(self) -> Dict[str, Dict[str, Any]]:
        with open(INTENTS_FILE, "r") as f:
            raw = json.load(f)
        return raw if isinstance(raw, dict) else {}

    def _save_intents(self, intents: Dict[str, Dict[str, Any]]) -> None:
        with open(INTENTS_FILE, "w") as f:
            json.dump(intents, f, indent=4)

    @staticmethod
    def _normalize_account_id(account_id: Optional[str]) -> str:
        candidate = str(account_id or "").strip()
        if candidate:
            return candidate
        fallback = str(os.getenv("EXECUTION_DEFAULT_ACCOUNT_ID", "primary") or "").strip()
        return fallback or "primary"

    def scope_idempotency_key(self, idempotency_key: str, account_id: Optional[str]) -> str:
        """
        Build canonical scoped key: {account_id}:{idempotency_key}.
        If already scoped, preserve as-is.
        """
        raw_key = str(idempotency_key or "").strip()
        if ":" in raw_key:
            return raw_key
        scoped_account = self._normalize_account_id(account_id)
        return f"{scoped_account}:{raw_key}"

    @staticmethod
    def account_id_from_scoped_key(idempotency_key: str) -> Optional[str]:
        raw_key = str(idempotency_key or "").strip()
        if ":" not in raw_key:
            return None
        account_id, _, suffix = raw_key.partition(":")
        return account_id if account_id and suffix else None

    def _resolve_effective_key(self, idempotency_key: str, account_id: Optional[str] = None) -> str:
        if account_id:
            return self.scope_idempotency_key(idempotency_key, account_id)
        return str(idempotency_key or "").strip()

    @staticmethod
    def _b64url_encode(raw: bytes) -> str:
        return base64.urlsafe_b64encode(raw).decode("utf-8").rstrip("=")

    @staticmethod
    def _b64url_decode(raw: str) -> bytes:
        padded = raw + "=" * ((4 - len(raw) % 4) % 4)
        return base64.urlsafe_b64decode(padded.encode("utf-8"))

    @staticmethod
    def _state_value(state: ExecutionState) -> str:
        return state.value if isinstance(state, ExecutionState) else str(state)

    @staticmethod
    def _env_bool(name: str, default: bool = False) -> bool:
        raw = os.getenv(name)
        if raw is None:
            return default
        return str(raw).strip().lower() in {"1", "true", "yes", "on"}

    def _is_live_mode(self) -> bool:
        return self._env_bool("BLOFIN_LIVE_ENABLED", default=False)

    @staticmethod
    def _resolve_context_secret() -> str:
        primary = str(os.getenv("EXECUTION_CONTEXT_HMAC_KEY", "") or "").strip()
        if primary:
            return primary
        
        # In live mode, we do NOT fall back to the audit key for execution context tokens.
        live_enabled = str(os.getenv("BLOFIN_LIVE_ENABLED", "false")).strip().lower() in {"1", "true", "yes", "on"}
        if live_enabled:
            return DEV_ONLY_CONTEXT_SECRET # This will trigger the UNCONFIGURED failure in issue/validate
            
        return str(os.getenv("EXECUTION_AUDIT_HMAC_KEY", "") or "").strip() or DEV_ONLY_CONTEXT_SECRET

    @staticmethod
    def _resolve_context_ttl(ttl_sec: Optional[int]) -> int:
        if ttl_sec is not None:
            ttl = int(ttl_sec)
        else:
            ttl = int(os.getenv("EXECUTION_CONTEXT_TOKEN_TTL_SEC", str(DEFAULT_CONTEXT_TOKEN_TTL_SEC)))
        return max(1, min(ttl, 300))

    @staticmethod
    def _resolve_context_clock_skew() -> int:
        skew = int(os.getenv("EXECUTION_CONTEXT_CLOCK_SKEW_SEC", str(DEFAULT_CONTEXT_CLOCK_SKEW_SEC)))
        return max(0, min(skew, 30))

    def _require_distributed_nonce_store(self) -> bool:
        raw = os.getenv("EXECUTION_CONTEXT_REQUIRE_DISTRIBUTED_NONCE")
        if raw is None:
            return self._is_live_mode()
        return self._env_bool("EXECUTION_CONTEXT_REQUIRE_DISTRIBUTED_NONCE", default=False)

    def _context_nonce_store_timeout_ms(self) -> int:
        raw = os.getenv("EXECUTION_CONTEXT_NONCE_TIMEOUT_MS")
        try:
            timeout_ms = int(raw) if raw is not None else DEFAULT_CONTEXT_NONCE_TIMEOUT_MS
        except (TypeError, ValueError):
            timeout_ms = DEFAULT_CONTEXT_NONCE_TIMEOUT_MS
        return max(10, min(timeout_ms, 100))

    def _get_context_store(self) -> Optional[ContextTokenStore]:
        redis_url = str(os.getenv("EXECUTION_CONTEXT_NONCE_REDIS_URL", "") or "").strip()
        if not redis_url:
            return None
        prefix = str(os.getenv("EXECUTION_CONTEXT_NONCE_PREFIX", DEFAULT_CONTEXT_NONCE_PREFIX) or DEFAULT_CONTEXT_NONCE_PREFIX)
        timeout_ms = self._context_nonce_store_timeout_ms()
        config = (redis_url, prefix, timeout_ms)
        if self._context_store is None or self._context_store_config != config:
            self._context_store = ContextTokenStore(
                redis_url=redis_url,
                prefix=prefix,
                timeout_ms=timeout_ms,
            )
            self._context_store_config = config
        return self._context_store

    def ensure_context_runtime_ready(self, strict: Optional[bool] = None) -> Tuple[bool, str]:
        strict_mode = self._is_live_mode() if strict is None else bool(strict)
        secret = self._resolve_context_secret()
        if strict_mode and secret == DEV_ONLY_CONTEXT_SECRET:
            return False, "TOKEN_SECRET_UNCONFIGURED"

        distributed_required = strict_mode or self._require_distributed_nonce_store()
        if not distributed_required:
            return True, "OK"

        store = self._get_context_store()
        if store is None:
            return False, "TOKEN_NONCE_STORE_UNAVAILABLE"

        ok, reason = store.ensure_ready()
        if ok:
            return True, "OK"
        if reason == "TIMEOUT":
            return False, "TOKEN_NONCE_STORE_TIMEOUT"
        return False, "TOKEN_NONCE_STORE_UNAVAILABLE"

    def issue_bridge_context_token(
        self,
        idempotency_key: str,
        required_state: ExecutionState = ExecutionState.PROTECTION_SUBMITTING,
        *,
        account_id: Optional[str] = None,
        ttl_sec: Optional[int] = None,
    ) -> str:
        scoped_key = self._resolve_effective_key(idempotency_key, account_id=account_id)
        intent = self.get_intent(scoped_key)
        if not intent:
            raise ValueError("CONTEXT_INTENT_NOT_FOUND")

        expected_state = self._state_value(required_state)
        current_state = str(intent.get("state", "") or "")
        if current_state != expected_state:
            raise ValueError(f"CONTEXT_STATE_MISMATCH:{current_state}->{expected_state}")

        secret = self._resolve_context_secret()
        if self._is_live_mode() and secret == DEV_ONLY_CONTEXT_SECRET:
            raise ValueError("CONTEXT_SECRET_UNCONFIGURED")

        now = int(time.time())
        ttl = self._resolve_context_ttl(ttl_sec)
        payload = {
            "idempotency_key": scoped_key,
            "required_state": expected_state,
            "iat": now,
            "exp": now + ttl,
            "nonce": uuid4().hex,
        }
        encoded = self._b64url_encode(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        )
        signature = hmac.new(secret.encode("utf-8"), encoded.encode("utf-8"), hashlib.sha256).hexdigest()
        return f"{encoded}.{signature}"

    def _consume_context_nonce(self, nonce: str, expiry_ts: int, now_ts: int, skew_sec: int) -> Tuple[bool, str]:
        ttl_sec = max(1, int(expiry_ts - now_ts + skew_sec))
        distributed_required = self._require_distributed_nonce_store()
        store = self._get_context_store()
        if store is not None:
            ok, reason = store.consume_once(nonce, ttl_sec=ttl_sec)
            if ok:
                return True, "OK"
            if reason == "REPLAYED":
                return False, "TOKEN_REPLAYED"
            if reason == "TIMEOUT":
                return False, "TOKEN_NONCE_STORE_TIMEOUT"
            return False, "TOKEN_NONCE_STORE_UNAVAILABLE"
        if distributed_required:
            return False, "TOKEN_NONCE_STORE_UNAVAILABLE"

        with self._nonce_lock:
            for key, expiry in list(self._consumed_context_nonces.items()):
                if int(expiry) < now_ts - skew_sec:
                    self._consumed_context_nonces.pop(key, None)
            if nonce in self._consumed_context_nonces:
                return False, "TOKEN_REPLAYED"
            self._consumed_context_nonces[nonce] = expiry_ts
        return True, "OK"

    def validate_bridge_context_token(
        self,
        token: str,
        idempotency_key: str,
        required_state: ExecutionState = ExecutionState.PROTECTION_SUBMITTING,
        *,
        account_id: Optional[str] = None,
        consume_nonce: bool = True,
    ) -> Tuple[bool, str]:
        raw_token = str(token or "").strip()
        if not raw_token:
            return False, "MISSING_TOKEN"

        encoded, sep, signature = raw_token.partition(".")
        if not sep or not encoded or not signature:
            return False, "TOKEN_FORMAT_INVALID"

        secret = self._resolve_context_secret()
        if self._is_live_mode() and secret == DEV_ONLY_CONTEXT_SECRET:
            return False, "TOKEN_SECRET_UNCONFIGURED"

        expected_sig = hmac.new(secret.encode("utf-8"), encoded.encode("utf-8"), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature, expected_sig):
            return False, "TOKEN_SIGNATURE_INVALID"

        try:
            payload = json.loads(self._b64url_decode(encoded).decode("utf-8"))
        except Exception:
            return False, "TOKEN_DECODE_FAILED"

        expected_key = self._resolve_effective_key(idempotency_key, account_id=account_id)
        if str(payload.get("idempotency_key", "")) != expected_key:
            return False, "TOKEN_KEY_MISMATCH"

        expected_state = self._state_value(required_state)
        if str(payload.get("required_state", "")) != expected_state:
            return False, "TOKEN_STATE_CLAIM_MISMATCH"

        try:
            issued_at = int(payload.get("iat"))
            expires_at = int(payload.get("exp"))
        except Exception:
            return False, "TOKEN_TIME_INVALID"

        now = int(time.time())
        skew = self._resolve_context_clock_skew()
        if now < issued_at - skew:
            return False, "TOKEN_NOT_YET_VALID"
        if now > expires_at + skew:
            return False, "TOKEN_EXPIRED"

        intent = self.get_intent(expected_key)
        if not intent:
            return False, "INTENT_NOT_FOUND"
        if str(intent.get("state", "")) != expected_state:
            return False, "INTENT_STATE_MISMATCH"

        nonce = str(payload.get("nonce", "") or "")
        if not nonce:
            return False, "TOKEN_NONCE_MISSING"

        if consume_nonce:
            ok, reason = self._consume_context_nonce(nonce, expires_at, now, skew)
            if not ok:
                return False, reason
        return True, "OK"

    # --- Sync Interface ---

    def get_intent(self, idempotency_key: str, account_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """Retrieve execution intent by idempotency key."""
        with self._intents_lock:
            try:
                intents = self._load_intents()
                return intents.get(self._resolve_effective_key(idempotency_key, account_id=account_id))
            except Exception as e:
                logger.error(f"Failed to read intents: {e}")
                return None

    def create_intent(
        self,
        idempotency_key: str,
        payload: Dict[str, Any],
        account_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Create a new execution intent if it doesn't exist."""
        with self._intents_lock:
            effective_account_id = account_id or payload.get("account_id")
            scoped_key = self._resolve_effective_key(idempotency_key, account_id=effective_account_id)
            payload_copy = dict(payload)
            if effective_account_id and not payload_copy.get("account_id"):
                payload_copy["account_id"] = str(effective_account_id)
            return self._create_intent_logic(scoped_key, payload_copy)

    def transition_to(
        self,
        idempotency_key: str,
        next_state: ExecutionState,
        metadata: Dict[str, Any] = None,
        account_id: Optional[str] = None,
    ) -> bool:
        """
        Transition an intent to a new state if valid.
        Enforces the State Transition Table.
        """
        with self._intents_lock:
            scoped_key = self._resolve_effective_key(idempotency_key, account_id=account_id)
            return self._transition_to_logic(scoped_key, next_state, metadata)

    def store_pretrade_snapshot(
        self,
        idempotency_key: str,
        snapshot: Dict[str, Any],
        *,
        symbol: Optional[str] = None,
        side: Optional[str] = None,
        setup_id: Optional[str] = None,
        account_id: Optional[str] = None,
    ) -> None:
        """Upsert pre-trade snapshot by idempotency key."""
        with self._intents_lock:
            scoped_key = self._resolve_effective_key(idempotency_key, account_id=account_id)
            self._store_pretrade_snapshot_logic(scoped_key, snapshot, symbol=symbol, side=side, setup_id=setup_id)

    def get_pretrade_snapshot(self, idempotency_key: str, account_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """Return stored pre-trade snapshot for an idempotency key."""
        with self._intents_lock:
            scoped_key = self._resolve_effective_key(idempotency_key, account_id=account_id)
            return self._get_pretrade_snapshot_logic(scoped_key)

    def list_active_intents(self, account_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        Return intents considered active for monitor workflows.
        Active intents are non-terminal, exchange-facing states.
        """
        active_states = {
            ExecutionState.PROTECTION_ARMED,
            ExecutionState.SUBMITTED,
            ExecutionState.PARTIALLY_FILLED,
        }
        return self._list_intents_by_states(active_states, account_id=account_id)

    def list_reconcilable_intents(self, account_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        Return intents that should be reconciled against exchange truth.
        """
        reconcilable_states = {
            ExecutionState.SUBMITTED,
            ExecutionState.PARTIALLY_FILLED,
        }
        return self._list_intents_by_states(reconcilable_states, account_id=account_id)

    def update_intent_metadata(
        self,
        idempotency_key: str,
        metadata: Dict[str, Any],
        account_id: Optional[str] = None,
    ) -> bool:
        """Merge metadata into an existing intent without state transition."""
        with self._intents_lock:
            scoped_key = self._resolve_effective_key(idempotency_key, account_id=account_id)
            return self._update_intent_metadata_logic(scoped_key, metadata)

    # --- Async Interface (Avoids to_thread overhead) ---

    async def get_intent_async(self, idempotency_key: str, account_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
        async with self._get_async_intents_lock():
            # I/O still happens in this thread, but lock is async-safe
            return self.get_intent(idempotency_key, account_id=account_id)

    async def create_intent_async(
        self,
        idempotency_key: str,
        payload: Dict[str, Any],
        account_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        async with self._get_async_intents_lock():
            effective_account_id = account_id or payload.get("account_id")
            scoped_key = self._resolve_effective_key(idempotency_key, account_id=effective_account_id)
            payload_copy = dict(payload)
            if effective_account_id and not payload_copy.get("account_id"):
                payload_copy["account_id"] = str(effective_account_id)
            return self._create_intent_logic(scoped_key, payload_copy)

    async def transition_to_async(
        self,
        idempotency_key: str,
        next_state: ExecutionState,
        metadata: Dict[str, Any] = None,
        account_id: Optional[str] = None,
    ) -> bool:
        async with self._get_async_intents_lock():
            scoped_key = self._resolve_effective_key(idempotency_key, account_id=account_id)
            return self._transition_to_logic(scoped_key, next_state, metadata)

    async def store_pretrade_snapshot_async(
        self,
        idempotency_key: str,
        snapshot: Dict[str, Any],
        *,
        symbol: Optional[str] = None,
        side: Optional[str] = None,
        setup_id: Optional[str] = None,
        account_id: Optional[str] = None,
    ) -> None:
        async with self._get_async_intents_lock():
            scoped_key = self._resolve_effective_key(idempotency_key, account_id=account_id)
            self._store_pretrade_snapshot_logic(scoped_key, snapshot, symbol=symbol, side=side, setup_id=setup_id)

    async def get_pretrade_snapshot_async(
        self,
        idempotency_key: str,
        account_id: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        async with self._get_async_intents_lock():
            scoped_key = self._resolve_effective_key(idempotency_key, account_id=account_id)
            return self._get_pretrade_snapshot_logic(scoped_key)

    async def list_active_intents_async(self, account_id: Optional[str] = None) -> List[Dict[str, Any]]:
        async with self._get_async_intents_lock():
            return self.list_active_intents(account_id=account_id)

    async def list_reconcilable_intents_async(self, account_id: Optional[str] = None) -> List[Dict[str, Any]]:
        async with self._get_async_intents_lock():
            return self.list_reconcilable_intents(account_id=account_id)

    async def update_intent_metadata_async(
        self,
        idempotency_key: str,
        metadata: Dict[str, Any],
        account_id: Optional[str] = None,
    ) -> bool:
        async with self._get_async_intents_lock():
            scoped_key = self._resolve_effective_key(idempotency_key, account_id=account_id)
            return self._update_intent_metadata_logic(scoped_key, metadata)

    # --- Private Logic (No Locking) ---

    def _create_intent_logic(self, idempotency_key: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        try:
            intents = self._load_intents()
            payload = dict(payload)
            parsed_account_id = self.account_id_from_scoped_key(idempotency_key)
            if parsed_account_id and not payload.get("account_id"):
                payload["account_id"] = parsed_account_id
            
            if idempotency_key in intents:
                # Check for conflict
                existing = intents[idempotency_key]
                existing_symbol = existing.get("symbol")
                existing_side = existing.get("side")
                incoming_symbol = payload.get("symbol")
                incoming_side = payload.get("side")
                if (
                    existing_symbol
                    and existing_side
                    and (existing_symbol != incoming_symbol or existing_side != incoming_side)
                ):
                    self._log_event(
                        idempotency_key,
                        "IDEMPOTENCY_CONFLICT",
                        {
                            "existing_symbol": existing_symbol,
                            "existing_side": existing_side,
                            "incoming_symbol": incoming_symbol,
                            "incoming_side": incoming_side,
                        },
                    )
                    raise ValueError("VETO_IDEMPOTENCY_CONFLICT: payload mismatch for key")

                # Pre-check snapshot stubs can exist before full approval payload.
                changed = False
                for key, value in payload.items():
                    if key not in existing or existing.get(key) is None:
                        existing[key] = value
                        changed = True
                if changed:
                    existing["updated_at"] = datetime.now(timezone.utc).isoformat()
                    intents[idempotency_key] = existing
                    self._save_intents(intents)

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
            
            self._save_intents(intents)
                
            self._log_event(idempotency_key, ExecutionState.DISCOVERED)
            return intent
        except Exception as e:
            logger.error(f"Failed to create intent: {e}")
            raise

    def _transition_to_logic(self, idempotency_key: str, next_state: ExecutionState, metadata: Dict[str, Any] = None) -> bool:
        try:
            intents = self._load_intents()
            
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
            self._save_intents(intents)
            
            self._log_event(idempotency_key, next_state, metadata)
            return True
        except Exception as e:
            logger.error(f"Transition failed: {e}")
            return False

    def _store_pretrade_snapshot_logic(
        self,
        idempotency_key: str,
        snapshot: Dict[str, Any],
        symbol: Optional[str] = None,
        side: Optional[str] = None,
        setup_id: Optional[str] = None,
    ) -> None:
        try:
            intents = self._load_intents()
            existing = intents.get(idempotency_key) or {
                "idempotency_key": idempotency_key,
                "state": ExecutionState.DISCOVERED,
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
            if symbol and not existing.get("symbol"):
                existing["symbol"] = symbol
            if side and not existing.get("side"):
                existing["side"] = side
            if setup_id and not existing.get("setup_id"):
                existing["setup_id"] = setup_id
            account_id = self.account_id_from_scoped_key(idempotency_key)
            if account_id and not existing.get("account_id"):
                existing["account_id"] = account_id
            existing["pretrade_snapshot"] = snapshot
            existing["updated_at"] = datetime.now(timezone.utc).isoformat()
            intents[idempotency_key] = existing
            self._save_intents(intents)
        except Exception as e:
            logger.error("Failed to store pre-trade snapshot: %s", e)

    def _get_pretrade_snapshot_logic(self, idempotency_key: str) -> Optional[Dict[str, Any]]:
        try:
            intents = self._load_intents()
            intent = intents.get(idempotency_key) or {}
            snapshot = intent.get("pretrade_snapshot")
            return snapshot if isinstance(snapshot, dict) else None
        except Exception as e:
            logger.error("Failed to read pre-trade snapshot: %s", e)
            return None

    @staticmethod
    def _normalize_state(raw_state: Any) -> Optional[ExecutionState]:
        if isinstance(raw_state, ExecutionState):
            return raw_state
        text = str(raw_state or "").strip()
        if not text:
            return None
        try:
            return ExecutionState(text)
        except ValueError:
            return None

    def _list_intents_by_states(
        self,
        allowed_states: set[ExecutionState],
        *,
        account_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        try:
            intents = self._load_intents()
            resolved_account = self._normalize_account_id(account_id) if account_id else None
            rows: List[Dict[str, Any]] = []
            for scoped_key, intent in intents.items():
                if not isinstance(intent, dict):
                    continue
                if resolved_account:
                    intent_account = intent.get("account_id") or self.account_id_from_scoped_key(scoped_key)
                    if str(intent_account or "").strip() != resolved_account:
                        continue
                state = self._normalize_state(intent.get("state"))
                if state is None or state not in allowed_states:
                    continue
                row = dict(intent)
                if "idempotency_key" not in row:
                    row["idempotency_key"] = scoped_key
                rows.append(row)
            rows.sort(key=lambda item: str(item.get("created_at", "")))
            return rows
        except Exception as e:
            logger.error("Failed to list intents by state: %s", e)
            return []

    def _update_intent_metadata_logic(self, idempotency_key: str, metadata: Optional[Dict[str, Any]]) -> bool:
        if not isinstance(metadata, dict):
            return False
        try:
            intents = self._load_intents()
            intent = intents.get(idempotency_key)
            if not isinstance(intent, dict):
                return False
            intent.update(metadata)
            intent["updated_at"] = datetime.now(timezone.utc).isoformat()
            intents[idempotency_key] = intent
            self._save_intents(intents)
            self._log_event(
                idempotency_key,
                "METADATA_UPDATED",
                {"updated_fields": sorted(metadata.keys())},
            )
            return True
        except Exception as e:
            logger.error("Failed to update intent metadata: %s", e)
            return False

    def _is_valid_transition(self, from_state: ExecutionState, to_state: ExecutionState) -> bool:
        """
        Enforce the State Transition Table logic (Phase 2 Expanded).
        """
        # Define allowed transitions
        allowed = {
            ExecutionState.DISCOVERED: [ExecutionState.PENDING_APPROVAL],
            ExecutionState.PENDING_APPROVAL: [ExecutionState.APPROVED_RECEIVED, ExecutionState.CANCELED],
            ExecutionState.APPROVED_RECEIVED: [ExecutionState.REVALIDATING],
            ExecutionState.REVALIDATING: [ExecutionState.READY, ExecutionState.VETOED],
            ExecutionState.READY: [ExecutionState.SUBMITTING, ExecutionState.VETOED],
            ExecutionState.SUBMITTING: [ExecutionState.PROTECTION_SUBMITTING, ExecutionState.SUBMITTED, ExecutionState.FAILED],
            ExecutionState.PROTECTION_SUBMITTING: [ExecutionState.PROTECTION_ARMED, ExecutionState.PROTECTION_FAILED],
            ExecutionState.PROTECTION_ARMED: [ExecutionState.SUBMITTED, ExecutionState.FAILED],
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
                    "account_id": self.account_id_from_scoped_key(idempotency_key),
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
