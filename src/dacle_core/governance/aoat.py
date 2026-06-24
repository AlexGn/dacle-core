"""Append-Only Audit Trail with sha256 hash chain.

Each line links to the previous via prev_hash + event_hash. Tampering with
any line breaks the chain on subsequent verify_chain() runs. This is not
true WORM (the file is mutable on disk), but it provides tamper detection.
"""

from __future__ import annotations

import hashlib
import json
import logging
import threading
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional, Tuple, Union

from src.governance.contracts import SovereignDecision
from src.utils.atomic_write import atomic_jsonl_append

logger = logging.getLogger(__name__)

GENESIS_HASH = "0" * 64


def _canonical_json(obj: Dict[str, Any]) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str)


def _sha256_hex(data: str) -> str:
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


def _hash_event(prev_hash: str, event_body: Dict[str, Any]) -> str:
    payload = {"prev_hash": prev_hash, "body": event_body}
    return _sha256_hex(_canonical_json(payload))


@dataclass
class _ChainState:
    last_hash: str = GENESIS_HASH
    line_count: int = 0


class AuditTrail:
    """Append-only audit log with sha256 hash chain."""

    def __init__(self, path: Union[str, Path]):
        self.path = Path(path)
        self._state = _ChainState()
        self._lock = threading.Lock()
        self._initialized = False

    def _initialize_from_disk(self) -> None:
        if self._initialized:
            return
        if self.path.exists():
            try:
                with open(self.path, "r") as f:
                    last = None
                    count = 0
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        last = line
                        count += 1
                if last is not None:
                    parsed = json.loads(last)
                    self._state.last_hash = parsed.get("event_hash", GENESIS_HASH)
                self._state.line_count = count
            except Exception as e:
                logger.error("AOAT init failed reading %s: %s", self.path, e)
                self._state.last_hash = GENESIS_HASH
        self._initialized = True

    def record_decision(self, decision: SovereignDecision) -> Optional[str]:
        """Append a decision to the chain. Returns event_id, or None on failure (fail-closed signal)."""
        with self._lock:
            self._initialize_from_disk()
            event_id = str(uuid.uuid4())
            body = {
                "event_id": event_id,
                "event_type": "GOVERNANCE_DECISION",
                "ts": datetime.now(timezone.utc).isoformat(),
                **decision.to_dict(),
            }
            event_hash = _hash_event(self._state.last_hash, body)
            line = {
                **body,
                "prev_hash": self._state.last_hash,
                "event_hash": event_hash,
            }
            try:
                atomic_jsonl_append(self.path, line)
            except Exception as e:
                logger.critical("AOAT write FAILED for event_id=%s: %s", event_id, e)
                return None
            self._state.last_hash = event_hash
            self._state.line_count += 1
            return event_id

    def verify_chain(self) -> Tuple[bool, Optional[str]]:
        """Walk the file, verify every prev_hash+event_hash link. Returns (ok, error_or_None)."""
        if not self.path.exists():
            return True, None
        prev = GENESIS_HASH
        line_no = 0
        try:
            with open(self.path, "r") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    line_no += 1
                    record = json.loads(line)
                    if record.get("prev_hash") != prev:
                        return False, f"line {line_no}: prev_hash mismatch"
                    body = {k: v for k, v in record.items() if k not in ("prev_hash", "event_hash")}
                    expected = _hash_event(prev, body)
                    if record.get("event_hash") != expected:
                        return False, f"line {line_no}: event_hash mismatch"
                    prev = record["event_hash"]
            return True, None
        except Exception as e:
            return False, f"line {line_no}: parse error: {e}"

    @property
    def line_count(self) -> int:
        with self._lock:
            self._initialize_from_disk()
            return self._state.line_count