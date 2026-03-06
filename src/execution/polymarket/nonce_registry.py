from __future__ import annotations

import asyncio
from typing import Awaitable, Callable, Dict


class NonceRegistry:
    """Process-wide nonce allocator keyed by signer identity."""

    _locks: Dict[str, asyncio.Lock] = {}
    _pending: Dict[str, int] = {}

    @classmethod
    async def next_nonce(
        cls,
        key: str,
        fetch_chain_nonce: Callable[[], Awaitable[int]],
    ) -> int:
        k = str(key or "").strip().lower()
        if not k:
            raise ValueError("NonceRegistry key is required")

        lock = cls._locks.get(k)
        if lock is None:
            lock = asyncio.Lock()
            cls._locks[k] = lock

        async with lock:
            chain_nonce = int(await fetch_chain_nonce())
            pending = cls._pending.get(k)
            if pending is None or chain_nonce > pending:
                cls._pending[k] = chain_nonce
            nonce = int(cls._pending[k])
            cls._pending[k] = nonce + 1
            return nonce

    @classmethod
    def reset_for_tests(cls) -> None:
        cls._locks.clear()
        cls._pending.clear()
