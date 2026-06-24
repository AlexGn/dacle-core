"""Canonical strategy interface contract for all DACLE pillars.

Moved from src/swing/base.py during Phase 1 pillar decoupling. Swing was
owning a contract that polymarket and lighter depended on (pillar -> pillar
violation). The canonical home is now the shared trading layer, alongside
src/trading_shared.models. src/swing/base.py re-exports this for backward
compatibility.

Move-as-is: no behavioral change to the abstract contract.
"""

from abc import ABC, abstractmethod
from typing import Any, Dict

from dacle_core.trading_shared.models import TerminalReason, TradeIntent


class StrategyInterface(ABC):
    """
    The abstract contract for all DACLE trading strategies.
    Ensures that every strategy supports:
    1. Standard config validation
    2. Atomic pre-trade gate checks
    3. Push-based halt/unwind interrupts
    4. Explicit state reconciliation
    """

    @property
    @abstractmethod
    def strategy_id(self) -> str:
        """A unique, stable identifier for this strategy (e.g. 'lighter-15m-scalp')."""
        pass

    @abstractmethod
    def validate_config(self, config: Dict[str, Any]) -> bool:
        """
        Validates the strategy-specific configuration.
        Must return True if valid, or raise an exception/return False if not.
        """
        pass

    @abstractmethod
    async def pre_trade_check(self, intent: TradeIntent) -> TerminalReason:
        """
        Performs the final pre-trade gate checks.
        Should verify freshness, local risk, and external managers (Allocator/Rate-Limit).
        Returns a TerminalReason indicating if the intent should proceed.
        """
        pass

    @abstractmethod
    async def on_halt(self, reason: str):
        """
        Push-based interrupt handler for emergency stops.
        Must immediately stop all new entry intents and initiate emergency exit
        if needed.
        """
        pass

    @abstractmethod
    def get_reconciliation_delta(self) -> Dict[str, Any]:
        """
        Returns the delta between local strategy state and actual venue state.
        Used by the Master Portfolio for autonomous error detection.
        """
        pass
