"""
Analysis Module - Technical Analysis Components

This module contains technical analysis tools for TGE execution timing.

Components:
- technical_patterns: Candlestick pattern detection (Learning 022)
- market_structure: CHoCH/BOS detection for Smart Money Concepts

Migration History:
- Session 256: Migrated from scripts/helpers/ for Phase 3 refactoring
"""

from src.analysis.technical_patterns import (
    CandlestickDetector,
    PatternType,
    PatternStrength,
    CandleData,
    PatternResult,
)

from src.analysis.market_structure import (
    MarketStructureAnalyzer,
    SwingPoint,
    StructureBreak,
    FairValueGap,
    TrendlineAnalysis,
    LiquiditySweep,
    OrderBlock,
    EqualLevel,
    Equilibrium,
    EntryTimingConfirmation,
)

__all__ = [
    # Technical Patterns
    'CandlestickDetector',
    'PatternType',
    'PatternStrength',
    'CandleData',
    'PatternResult',
    # Market Structure
    'MarketStructureAnalyzer',
    'SwingPoint',
    'StructureBreak',
    'FairValueGap',
    'TrendlineAnalysis',
    'LiquiditySweep',
    'OrderBlock',
    'EqualLevel',
    'Equilibrium',
    'EntryTimingConfirmation',
]
