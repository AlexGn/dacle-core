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
    def __init__(self, initial_nonce: int = 0):
        self._next_nonce = initial_nonce
        self._lock = asyncio.Lock()
        self._last_resync = 0

    async def get_next_nonce(self) -> int:
        """Atomic increment and return."""
        async with self._lock:
            nonce = self._next_nonce
            self._next_nonce += 1
            return nonce

    async def resync(self, current_onchain_nonce: int):
        """Re-align local counter with exchange truth."""
        async with self._lock:
            if current_onchain_nonce > self._next_nonce:
                logger.info(f"NonceManager resync: {self._next_nonce} -> {current_onchain_nonce}")
                self._next_nonce = current_onchain_nonce
            else:
                logger.debug("NonceManager resync ignored: local is ahead or equal.")

    def peek(self) -> int:
        return self._next_nonce
