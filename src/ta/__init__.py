"""
Computed TA Module - Real-time technical analysis from market data.

Provides computed TA as an alternative to screenshot-based GPT Vision extraction.
Uses Binance OHLCV data + existing analysis modules to produce TAExtractionResult
objects compatible with the existing scoring pipeline.

Session 353: Initial implementation (feature/computed-ta branch)
"""
from src.ta.computed_ta_builder import build_computed_ta

__all__ = ["build_computed_ta"]
