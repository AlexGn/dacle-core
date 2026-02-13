"""
Safe asyncio task wrapper — Session 427

Replaces bare asyncio.create_task() calls that silently swallow exceptions.
"""

import asyncio
import logging
from typing import Any, Coroutine, Optional


def safe_create_task(
    coro: Coroutine[Any, Any, Any],
    *,
    logger: logging.Logger,
    error_channel: Optional[Any] = None,
    name: Optional[str] = None,
) -> asyncio.Task:
    """Wrap a coroutine in a task with error logging.

    Args:
        coro: The coroutine to run.
        logger: Logger instance for error reporting.
        error_channel: Optional Discord channel/messageable to send error feedback.
        name: Optional task name for debugging.

    Returns:
        The created asyncio.Task.
    """

    async def _wrapper():
        try:
            await coro
        except Exception as exc:
            logger.error(f"Background task failed: {exc}", exc_info=True)
            if error_channel is not None:
                try:
                    await error_channel.send(
                        content=f"An unexpected error occurred: {exc}"
                    )
                except Exception:
                    pass

    return asyncio.create_task(_wrapper(), name=name)
