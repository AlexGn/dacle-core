"""
Computed TA Module - Real-time technical analysis from market data.

Provides computed TA as an alternative to screenshot-based GPT Vision extraction.
Uses Binance OHLCV data + existing analysis modules to produce TAExtractionResult
objects compatible with the existing scoring pipeline.

Session 353: Initial implementation (feature/computed-ta branch)
Session 360: Added outcome tracking for score calibration learning
"""
import logging
from typing import Optional

logger = logging.getLogger(__name__)


# Lazy wrapper: ccxt crashes on ARM Mac (x86_64 vs arm64 build)
# so we defer the import of computed_ta_builder until first actual call.
_computed_ta_builder = None


def build_computed_ta(*args, **kwargs):
    """Lazy-load computed_ta_builder and delegate to it.

    Eager-importing computed_ta_builder at module level triggers a ccxt
    import chain that crashes on ARM Mac with incompatible-architecture
    errors for _cffi_backend / cryptography native extensions.

    This wrapper defers the import until the first time the function is
    actually called, which only happens at webhook-handling time (not at
    bot startup or on other code paths that just need outcome_tracker).
    """
    global _computed_ta_builder
    if _computed_ta_builder is None:
        try:
            from src.ta.computed_ta_builder import build_computed_ta as _bt
            _computed_ta_builder = _bt
        except Exception as e:
            logger.error("Failed to lazy-import computed_ta_builder: %s", e)
            raise
    return _computed_ta_builder(*args, **kwargs)

from src.ta.outcome_tracker import (
    log_ta_score,
    record_outcome,
    get_score_accuracy_stats,
    get_calibration_suggestions,
)

__all__ = [
    "build_computed_ta",
    "log_ta_score",
    "record_outcome",
    "get_score_accuracy_stats",
    "get_calibration_suggestions",
]
