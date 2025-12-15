"""
TA Snapshot Logger - Session 126
================================

Captures Technical Analysis state at the moment of alert generation.

Problem this solves:
- Only 9.1% of past trades had TA data attached
- Without snapshots, Agent 7 cannot learn which TA signals work
- RSI/Volume/Exhaustion change every 15 minutes - we need frozen state

Usage:
    from src.data.validation.ta_snapshot_logger import TASnapshotLogger

    # In Agent 6 (Playbook) or Alert generation:
    snapshot_id = TASnapshotLogger.save_snapshot(
        symbol="POWER",
        ta_data=ta_analysis_result,
        conviction_score=8.5,
        stage="ALERT_GENERATION"
    )

    # Link to playbook/trade
    playbook["snapshot_id"] = snapshot_id

Future correlation:
    trade_outcomes.json: {token: "POWER", snapshot_id: "abc123", outcome: "LOSS"}
    Agent 7 can then correlate: "RSI > 80 at entry → 80% loss rate"
"""

import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

# Project root for consistent paths
PROJECT_ROOT = Path(__file__).parent.parent.parent.parent


class TASnapshotLogger:
    """
    Persists TA indicator state at critical moments (alert, entry, exit).

    Why this matters:
    - TA data is ephemeral (changes every candle)
    - Without snapshots, we can't backtest "why did we enter?"
    - Agent 7 needs historical TA context to calibrate signals
    """

    DIR_PATH = PROJECT_ROOT / "data" / "validation" / "ta_snapshots"

    @classmethod
    def save_snapshot(
        cls,
        symbol: str,
        ta_data: Dict[str, Any],
        conviction_score: float,
        stage: str = "ALERT_GENERATION",
        entry_price: Optional[float] = None,
        additional_context: Optional[Dict[str, Any]] = None
    ) -> str:
        """
        Save a frozen TA state snapshot.

        Args:
            symbol: Token symbol (e.g., "POWER")
            ta_data: TA analysis result from TADataAggregator or similar
            conviction_score: Conviction score at this moment
            stage: "ALERT_GENERATION", "ENTRY", "EXIT", "REFRESH"
            entry_price: Current price at snapshot time
            additional_context: Any extra context (macro, category, etc.)

        Returns:
            snapshot_id: Unique ID to link with trade_executions
        """
        # Ensure directory exists
        cls.DIR_PATH.mkdir(parents=True, exist_ok=True)

        # Generate unique ID
        timestamp = datetime.now(timezone.utc)
        snapshot_id = str(uuid.uuid4())[:8]
        filename = f"{symbol}_{timestamp.strftime('%Y%m%d_%H%M%S')}_{snapshot_id}.json"

        # Extract key indicators (normalize different TA data formats)
        indicators = cls._extract_indicators(ta_data)

        # Build record
        record = {
            "id": snapshot_id,
            "symbol": symbol,
            "timestamp": timestamp.isoformat(),
            "stage": stage,
            "conviction_score": conviction_score,
            "entry_price": entry_price,

            # Normalized indicators for easy querying
            "indicators": indicators,

            # Market context
            "market_context": cls._extract_market_context(ta_data),

            # Additional context (category, macro regime, etc.)
            "context": additional_context or {},

            # Full raw dump for detailed analysis
            "raw_ta_data": ta_data
        }

        # Save to file
        filepath = cls.DIR_PATH / filename
        with open(filepath, 'w') as f:
            json.dump(record, f, indent=2, default=str)

        print(f"📸 [TA SNAPSHOT] Saved {symbol} state (ID: {snapshot_id}) → {filename}")
        return snapshot_id

    @classmethod
    def _extract_indicators(cls, ta_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Extract and normalize key TA indicators from various formats.

        Supports:
        - TADataAggregator format (scripts/helpers/ta_aggregator.py)
        - Raw exchange OHLCV analysis
        - Sniper monitor exhaustion data
        """
        if not ta_data:
            return {"error": "No TA data provided"}

        indicators = {}

        # RSI (multiple timeframes)
        if "rsi" in ta_data:
            rsi = ta_data["rsi"]
            if isinstance(rsi, dict):
                indicators["rsi_15m"] = rsi.get("15m") or rsi.get("15min")
                indicators["rsi_1h"] = rsi.get("1h") or rsi.get("1hour")
                indicators["rsi_4h"] = rsi.get("4h") or rsi.get("4hour")
            else:
                indicators["rsi"] = rsi

        # Exhaustion signal (sniper monitor)
        if "exhaustion_signal" in ta_data:
            indicators["exhaustion_signal"] = ta_data["exhaustion_signal"]
        elif "exhaustion" in ta_data:
            indicators["exhaustion_signal"] = ta_data["exhaustion"]

        # Bollinger Bands
        if "bollinger" in ta_data:
            bb = ta_data["bollinger"]
            indicators["bb_upper"] = bb.get("upper")
            indicators["bb_lower"] = bb.get("lower")
            indicators["bb_position"] = bb.get("position")  # 0-1 scale

        # Volume
        if "volume" in ta_data:
            vol = ta_data["volume"]
            if isinstance(vol, dict):
                indicators["volume_24h"] = vol.get("24h")
                indicators["volume_ratio"] = vol.get("ratio")  # vs 7d avg
            else:
                indicators["volume"] = vol

        # Price action
        if "price" in ta_data:
            price = ta_data["price"]
            if isinstance(price, dict):
                indicators["current_price"] = price.get("current")
                indicators["price_change_24h"] = price.get("change_24h")
                indicators["price_vs_ath"] = price.get("vs_ath")
            else:
                indicators["current_price"] = price

        # Support/Resistance
        if "support_resistance" in ta_data:
            sr = ta_data["support_resistance"]
            indicators["nearest_support"] = sr.get("support")
            indicators["nearest_resistance"] = sr.get("resistance")

        # Trend
        if "trend" in ta_data:
            indicators["trend"] = ta_data["trend"]

        # SMC signals
        if "smc" in ta_data:
            smc = ta_data["smc"]
            indicators["choch_signal"] = smc.get("choch")
            indicators["bos_signal"] = smc.get("bos")
            indicators["fvg_present"] = smc.get("fvg")

        return indicators

    @classmethod
    def _extract_market_context(cls, ta_data: Dict[str, Any]) -> Dict[str, Any]:
        """Extract market-wide context (BTC, indices, macro)."""
        context = {}

        # BTC correlation/structure
        if "market_context" in ta_data:
            mc = ta_data["market_context"]
            context["btc_trend"] = mc.get("btc_trend") or mc.get("btc_structure")
            context["btc_correlation"] = mc.get("btc_correlation")
            context["eth_trend"] = mc.get("eth_trend")

        # Indices (USDT.D, TOTAL3)
        if "indices" in ta_data:
            idx = ta_data["indices"]
            context["usdt_dominance"] = idx.get("usdt_d") or idx.get("usdt_dominance")
            context["total3"] = idx.get("total3")
            context["fear_greed"] = idx.get("fear_greed")

        # Macro regime
        if "macro_regime" in ta_data:
            context["macro_regime"] = ta_data["macro_regime"]

        return context

    @classmethod
    def get_snapshot(cls, snapshot_id: str) -> Optional[Dict[str, Any]]:
        """
        Retrieve a snapshot by ID.

        Args:
            snapshot_id: The 8-char snapshot ID

        Returns:
            Snapshot data or None if not found
        """
        if not cls.DIR_PATH.exists():
            return None

        # Search for file with this ID
        for filepath in cls.DIR_PATH.glob(f"*_{snapshot_id}.json"):
            with open(filepath, 'r') as f:
                return json.load(f)

        return None

    @classmethod
    def get_snapshots_for_symbol(
        cls,
        symbol: str,
        limit: int = 10
    ) -> list:
        """Get recent snapshots for a symbol."""
        if not cls.DIR_PATH.exists():
            return []

        snapshots = []
        for filepath in sorted(cls.DIR_PATH.glob(f"{symbol}_*.json"), reverse=True):
            if len(snapshots) >= limit:
                break
            with open(filepath, 'r') as f:
                snapshots.append(json.load(f))

        return snapshots

    @classmethod
    def link_snapshot_to_trade(cls, snapshot_id: str, trade_id: str) -> bool:
        """
        Link a snapshot to a trade execution (for correlation later).

        This adds the trade_id to the snapshot file for bidirectional lookup.
        """
        snapshot = cls.get_snapshot(snapshot_id)
        if not snapshot:
            return False

        # Find the file
        for filepath in cls.DIR_PATH.glob(f"*_{snapshot_id}.json"):
            snapshot["linked_trade_id"] = trade_id
            snapshot["linked_at"] = datetime.now(timezone.utc).isoformat()

            with open(filepath, 'w') as f:
                json.dump(snapshot, f, indent=2, default=str)

            print(f"🔗 [TA SNAPSHOT] Linked {snapshot_id} → Trade {trade_id}")
            return True

        return False


# Convenience function for quick snapshots
def capture_ta_snapshot(
    symbol: str,
    conviction_score: float,
    ta_data: Optional[Dict[str, Any]] = None,
    stage: str = "ALERT_GENERATION"
) -> str:
    """
    Quick function to capture TA snapshot.

    If ta_data is not provided, attempts to fetch current TA.
    """
    if ta_data is None:
        # Try to get current TA
        try:
            from scripts.helpers.ta_aggregator import TADataAggregator
            aggregator = TADataAggregator()
            ta_data = aggregator.get_full_analysis(symbol)
        except Exception as e:
            print(f"⚠️ Could not fetch TA data: {e}")
            ta_data = {"error": str(e), "fetched": False}

    return TASnapshotLogger.save_snapshot(
        symbol=symbol,
        ta_data=ta_data,
        conviction_score=conviction_score,
        stage=stage
    )
