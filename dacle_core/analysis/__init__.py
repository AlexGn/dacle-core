"""
Analysis Module - Technical Analysis Components

This module contains technical analysis tools for TGE execution timing.

Components:
- technical_patterns: Candlestick pattern detection (Learning 022)
- market_structure: CHoCH/BOS detection for Smart Money Concepts
- support_resistance_detector: S/R and trendline detection (Learning 023)
- technical_pattern_detector: Tier 1 rule-based TA automation

Migration History:
- Session 256: Migrated from scripts/helpers/ for Phase 3 refactoring
- Session 267: Added support_resistance_detector from scripts/helpers/
- Session 267: Added technical_pattern_detector from scripts/helpers/
"""

from dacle_core.analysis.technical_patterns import (
    CandlestickDetector,
    PatternType,
    PatternStrength,
    CandleData,
    PatternResult,
)

# Lazy imports: market_structure imports ccxt which crashes on ARM Mac
# These are exposed as module-level functions that defer import until first call.
_market_structure_cache = None

def _get_market_structure():
    global _market_structure_cache
    if _market_structure_cache is None:
        from dacle_core.analysis.market_structure import (
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
        _market_structure_cache = {
            'MarketStructureAnalyzer': MarketStructureAnalyzer,
            'SwingPoint': SwingPoint,
            'StructureBreak': StructureBreak,
            'FairValueGap': FairValueGap,
            'TrendlineAnalysis': TrendlineAnalysis,
            'LiquiditySweep': LiquiditySweep,
            'OrderBlock': OrderBlock,
            'EqualLevel': EqualLevel,
            'Equilibrium': Equilibrium,
            'EntryTimingConfirmation': EntryTimingConfirmation,
        }
    return _market_structure_cache

def __getattr__(name):
    cache = _get_market_structure()
    if name in cache:
        return cache[name]
    raise AttributeError(f"module 'src.analysis' has no attribute '{name}'")

from dacle_core.analysis.support_resistance_detector import (
    SupportResistanceDetector,
    format_sr_summary,
)

from dacle_core.analysis.technical_pattern_detector import (
    TrendlineBreakDetector,
    CandlestickAnalyzer,
    RetestDetector,
    PatternResult as TechnicalPatternResult,
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
    # Support/Resistance
    'SupportResistanceDetector',
    'format_sr_summary',
    # Technical Pattern Detection
    'TrendlineBreakDetector',
    'CandlestickAnalyzer',
    'RetestDetector',
    'TechnicalPatternResult',
]
