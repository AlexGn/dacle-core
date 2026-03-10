"""
DACLE Nonce Manager
Ensures sequential transaction safety for Lighter.xyz.
Implements resync logic from exchange REST API.
"""

import asyncio
import logging
from typing import Optional

logger = logging.getLogger(__name__)


class NonceManager:
    # Process-local nonce coordination for multi-daemon same-account contention.
    _shared_locks: dict[str, asyncio.Lock] = {}
    _shared_next_nonce: dict[str, int] = {}

    def __init__(self, initial_nonce: int = 0, namespace: Optional[str] = None):
        self._namespace = str(namespace or "").strip() or None
        if self._namespace:
            self._lock = self._shared_locks.setdefault(self._namespace, asyncio.Lock())
            self._shared_next_nonce.setdefault(self._namespace, int(initial_nonce))
            self._next_nonce = int(self._shared_next_nonce[self._namespace])
        else:
            self._next_nonce = int(initial_nonce)
            self._lock = asyncio.Lock()
        self._last_resync = 0

    def _peek_locked(self) -> int:
        if self._namespace:
            return int(self._shared_next_nonce[self._namespace])
        return int(self._next_nonce)

    def _set_locked(self, value: int) -> None:
        value = int(value)
        if self._namespace:
            self._shared_next_nonce[self._namespace] = value
        self._next_nonce = value

    async def get_next_nonce(self) -> int:
        """Atomic increment and return."""
        async with self._lock:
            nonce = self._peek_locked()
            self._set_locked(nonce + 1)
            return nonce

    async def resync(self, current_onchain_nonce: int, force: bool = False):
        """Re-align local counter with exchange truth.

        When ``force`` is true, exchange truth wins even if the local counter is ahead.
        This is reserved for explicit server-side nonce rejection paths.
        """
        current_onchain_nonce = int(current_onchain_nonce)
        async with self._lock:
            local_next = self._peek_locked()
            if force and current_onchain_nonce != local_next:
                logger.warning(
                    "NonceManager force resync: %s -> %s",
                    local_next,
                    current_onchain_nonce,
                )
                self._set_locked(current_onchain_nonce)
            elif current_onchain_nonce > local_next:
                logger.info(f"NonceManager resync: {local_next} -> {current_onchain_nonce}")
                self._set_locked(current_onchain_nonce)
            elif current_onchain_nonce < local_next:
                logger.debug(
                    "NonceManager resync ignored: local nonce %s ahead of exchange %s.",
                    local_next,
                    current_onchain_nonce,
                )
            else:
                logger.debug("NonceManager resync ignored: already in sync.")

    def peek(self) -> int:
        if self._namespace:
            return int(self._shared_next_nonce[self._namespace])
        return int(self._next_nonce)
