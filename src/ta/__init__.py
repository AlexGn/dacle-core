"""
Computed TA Module - Real-time technical analysis from market data.

Provides computed TA as an alternative to screenshot-based GPT Vision extraction.
Uses Binance OHLCV data + existing analysis modules to produce TAExtractionResult
objects compatible with the existing scoring pipeline.

Session 353: Initial implementation (feature/computed-ta branch)
Session 360: Added outcome tracking for score calibration learning
"""
from src.ta.computed_ta_builder import build_computed_ta
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
