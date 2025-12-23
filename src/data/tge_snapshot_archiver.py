"""
TGE Snapshot Archiver - Session 255 Task 7

Captures and archives token supply data snapshots for historical tracking.

Problem Solved:
- Token circulating supply changes over time due to unlocks
- Projects often misreport float % at TGE
- No way to validate actual vs reported supply without historical data
- Unlock schedules are estimates - actual unlocks may differ

Solution:
- Daily/weekly snapshots of token supply metrics
- Historical archive for drift detection
- Validation of actual vs reported values
- Unlock event tracking

Usage:
    from src.data.tge_snapshot_archiver import TGESnapshotArchiver

    # Take snapshot
    archiver = TGESnapshotArchiver()
    snapshot_id = archiver.capture_snapshot("POWER")

    # Get historical snapshots
    history = archiver.get_snapshot_history("POWER", days=30)

    # Validate supply drift
    drift = archiver.calculate_supply_drift("POWER")
"""

import json
import logging
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, asdict
import uuid

logger = logging.getLogger(__name__)

# Project root
PROJECT_ROOT = Path(__file__).parent.parent.parent


@dataclass
class SupplySnapshot:
    """Single snapshot of token supply data."""

    snapshot_id: str
    symbol: str
    timestamp: str

    # Supply metrics
    total_supply: Optional[float]
    circulating_supply: Optional[float]
    float_percent: Optional[float]
    locked_percent: Optional[float]

    # Price & market cap
    current_price: Optional[float]
    market_cap: Optional[float]
    fdv: Optional[float]

    # Days since TGE
    days_since_tge: Optional[int]

    # Source tracking
    data_source: str  # coingecko, coinmarketcap, exchange_api, manual
    data_confidence: float  # 0.0-1.0

    # Comparison vs TGE
    supply_drift_pct: Optional[float] = None  # % change vs TGE float
    unlock_detected: bool = False

    # Additional context
    notes: Optional[str] = None


@dataclass
class UnlockEvent:
    """Detected unlock event."""

    event_id: str
    symbol: str
    detected_at: str

    # Supply change
    before_snapshot_id: str
    after_snapshot_id: str
    circulating_supply_before: float
    circulating_supply_after: float
    unlock_amount: float
    unlock_pct: float  # % of total supply unlocked

    # Context
    days_since_tge: int
    expected_unlock: bool  # Was this expected per schedule?
    notes: Optional[str] = None


class TGESnapshotArchiver:
    """
    Archives historical token supply data for drift detection.

    Features:
    - Daily/weekly supply snapshots
    - Unlock event detection
    - Supply drift validation
    - Historical comparison tools
    """

    SNAPSHOTS_DIR = PROJECT_ROOT / "data" / "snapshots"
    TOKENS_DIR = PROJECT_ROOT / "data" / "tokens"

    def __init__(self):
        """Initialize archiver and create directories."""
        self.SNAPSHOTS_DIR.mkdir(parents=True, exist_ok=True)
        logger.info(f"TGE Snapshot Archiver initialized: {self.SNAPSHOTS_DIR}")

    def capture_snapshot(
        self,
        symbol: str,
        data_source: str = "coingecko",
        force_fetch: bool = False
    ) -> str:
        """
        Capture current supply snapshot for a token.

        Args:
            symbol: Token symbol (e.g., "POWER")
            data_source: Where data was fetched from
            force_fetch: Force fetch even if recent snapshot exists

        Returns:
            snapshot_id: Unique ID for this snapshot
        """
        # Check if recent snapshot exists (within 6 hours)
        if not force_fetch:
            recent = self._get_most_recent_snapshot(symbol)
            if recent:
                snapshot_time = datetime.fromisoformat(recent.timestamp.replace('Z', '+00:00'))
                age_hours = (datetime.now(timezone.utc) - snapshot_time).seconds / 3600
                if age_hours < 6:
                    logger.info(f"Recent snapshot exists for {symbol} ({age_hours:.1f}h old), skipping")
                    return recent.snapshot_id

        # Fetch current supply data
        supply_data = self._fetch_supply_data(symbol, data_source)

        if not supply_data:
            logger.warning(f"Could not fetch supply data for {symbol}")
            return ""

        # Get TGE data for comparison
        tge_data = self._load_tge_data(symbol)

        # Calculate drift vs TGE
        supply_drift = None
        if tge_data and supply_data.get("circulating_supply") and tge_data.get("circulating_supply_at_tge"):
            current_float = (supply_data["circulating_supply"] / supply_data.get("total_supply", 1)) * 100
            tge_float = tge_data.get("float_percent", 0)
            supply_drift = current_float - tge_float

        # Calculate days since TGE
        days_since_tge = None
        if tge_data and tge_data.get("tge_date"):
            tge_date = datetime.fromisoformat(tge_data["tge_date"].replace('Z', '+00:00'))
            days_since_tge = (datetime.now(timezone.utc) - tge_date).days

        # Create snapshot
        snapshot = SupplySnapshot(
            snapshot_id=str(uuid.uuid4())[:8],
            symbol=symbol,
            timestamp=datetime.now(timezone.utc).isoformat(),
            total_supply=supply_data.get("total_supply"),
            circulating_supply=supply_data.get("circulating_supply"),
            float_percent=supply_data.get("float_percent"),
            locked_percent=100 - supply_data.get("float_percent", 0) if supply_data.get("float_percent") else None,
            current_price=supply_data.get("current_price"),
            market_cap=supply_data.get("market_cap"),
            fdv=supply_data.get("fdv"),
            days_since_tge=days_since_tge,
            data_source=data_source,
            data_confidence=supply_data.get("confidence", 0.8),
            supply_drift_pct=supply_drift
        )

        # Detect unlock event
        recent_snapshot = self._get_most_recent_snapshot(symbol)
        if recent_snapshot and recent_snapshot.circulating_supply and snapshot.circulating_supply:
            supply_increase_pct = ((snapshot.circulating_supply - recent_snapshot.circulating_supply) /
                                   recent_snapshot.circulating_supply) * 100
            if supply_increase_pct > 1.0:  # >1% increase = likely unlock
                snapshot.unlock_detected = True
                self._log_unlock_event(recent_snapshot, snapshot)

        # Save snapshot
        self._save_snapshot(snapshot)

        logger.info(f"📸 Snapshot captured: {symbol} (ID: {snapshot.snapshot_id}, drift: {supply_drift:+.1f}% vs TGE)" if supply_drift else f"📸 Snapshot captured: {symbol} (ID: {snapshot.snapshot_id})")

        return snapshot.snapshot_id

    def _fetch_supply_data(self, symbol: str, source: str) -> Optional[Dict[str, Any]]:
        """
        Fetch current supply data from various sources.

        Args:
            symbol: Token symbol
            source: Data source (coingecko, coinmarketcap, etc.)

        Returns:
            Supply data dict or None
        """
        try:
            if source == "coingecko":
                return self._fetch_from_coingecko(symbol)
            elif source == "coinmarketcap":
                return self._fetch_from_coinmarketcap(symbol)
            elif source == "local":
                # Use data from tokens/{SYMBOL}/consolidated.json
                return self._fetch_from_local(symbol)
            else:
                logger.warning(f"Unknown data source: {source}")
                return None
        except Exception as e:
            logger.error(f"Error fetching supply data for {symbol}: {e}")
            return None

    def _fetch_from_local(self, symbol: str) -> Optional[Dict[str, Any]]:
        """Fetch from local tokens directory (fallback)."""
        token_file = self.TOKENS_DIR / symbol / "consolidated.json"
        if not token_file.exists():
            return None

        with open(token_file, 'r') as f:
            data = json.load(f)

        return {
            "total_supply": data.get("total_supply"),
            "circulating_supply": data.get("circulating_supply_at_tge"),  # This is TGE data, not current
            "float_percent": data.get("float_percent"),
            "current_price": data.get("listing_price_low"),  # Approximation
            "fdv": data.get("fdv"),
            "confidence": 0.5  # Low confidence - this is TGE data, not current
        }

    def _fetch_from_coingecko(self, symbol: str) -> Optional[Dict[str, Any]]:
        """Fetch from CoinGecko API."""
        try:
            from pycoingecko import CoinGeckoAPI
            cg = CoinGeckoAPI()

            # Search for coin ID
            search = cg.search(query=symbol)
            if not search or 'coins' not in search or not search['coins']:
                logger.warning(f"Token {symbol} not found on CoinGecko")
                return None

            coin_id = search['coins'][0]['id']

            # Get market data
            coin_data = cg.get_coin_by_id(coin_id)

            market = coin_data.get('market_data', {})
            total_supply = market.get('total_supply')
            circulating_supply = market.get('circulating_supply')
            current_price = market.get('current_price', {}).get('usd')
            market_cap = market.get('market_cap', {}).get('usd')
            fdv = market.get('fully_diluted_valuation', {}).get('usd')

            float_pct = None
            if total_supply and circulating_supply:
                float_pct = (circulating_supply / total_supply) * 100

            return {
                "total_supply": total_supply,
                "circulating_supply": circulating_supply,
                "float_percent": float_pct,
                "current_price": current_price,
                "market_cap": market_cap,
                "fdv": fdv,
                "confidence": 0.9  # CoinGecko is reliable
            }

        except Exception as e:
            logger.error(f"CoinGecko fetch error for {symbol}: {e}")
            return None

    def _fetch_from_coinmarketcap(self, symbol: str) -> Optional[Dict[str, Any]]:
        """Fetch from CoinMarketCap API (requires API key)."""
        # Placeholder - would need CMC API integration
        logger.warning("CoinMarketCap integration not yet implemented")
        return None

    def _load_tge_data(self, symbol: str) -> Optional[Dict[str, Any]]:
        """Load original TGE data for comparison."""
        token_file = self.TOKENS_DIR / symbol / "consolidated.json"
        if not token_file.exists():
            return None

        with open(token_file, 'r') as f:
            return json.load(f)

    def _save_snapshot(self, snapshot: SupplySnapshot):
        """Save snapshot to file."""
        # Organize by symbol and date
        symbol_dir = self.SNAPSHOTS_DIR / snapshot.symbol
        symbol_dir.mkdir(parents=True, exist_ok=True)

        # Filename: SYMBOL_YYYYMMDD_HHMMSS_ID.json
        timestamp = datetime.fromisoformat(snapshot.timestamp.replace('Z', '+00:00'))
        filename = f"{snapshot.symbol}_{timestamp.strftime('%Y%m%d_%H%M%S')}_{snapshot.snapshot_id}.json"
        filepath = symbol_dir / filename

        with open(filepath, 'w') as f:
            json.dump(asdict(snapshot), f, indent=2)

        logger.debug(f"Snapshot saved: {filepath}")

    def _get_most_recent_snapshot(self, symbol: str) -> Optional[SupplySnapshot]:
        """Get the most recent snapshot for a symbol."""
        symbol_dir = self.SNAPSHOTS_DIR / symbol
        if not symbol_dir.exists():
            return None

        # Get all snapshot files, sorted by modification time
        snapshots = sorted(symbol_dir.glob(f"{symbol}_*.json"), key=lambda p: p.stat().st_mtime, reverse=True)

        if not snapshots:
            return None

        with open(snapshots[0], 'r') as f:
            data = json.load(f)

        return SupplySnapshot(**data)

    def _log_unlock_event(self, before: SupplySnapshot, after: SupplySnapshot):
        """Log detected unlock event."""
        unlock_amount = after.circulating_supply - before.circulating_supply
        unlock_pct = (unlock_amount / after.total_supply) * 100 if after.total_supply else 0

        event = UnlockEvent(
            event_id=str(uuid.uuid4())[:8],
            symbol=after.symbol,
            detected_at=after.timestamp,
            before_snapshot_id=before.snapshot_id,
            after_snapshot_id=after.snapshot_id,
            circulating_supply_before=before.circulating_supply,
            circulating_supply_after=after.circulating_supply,
            unlock_amount=unlock_amount,
            unlock_pct=unlock_pct,
            days_since_tge=after.days_since_tge,
            expected_unlock=False,  # TODO: Check against schedule
            notes=f"Unlock detected: +{unlock_amount:,.0f} tokens (+{unlock_pct:.1f}%)"
        )

        # Save unlock event
        events_dir = self.SNAPSHOTS_DIR / "unlock_events"
        events_dir.mkdir(parents=True, exist_ok=True)

        filepath = events_dir / f"{event.symbol}_{event.event_id}.json"
        with open(filepath, 'w') as f:
            json.dump(asdict(event), f, indent=2)

        logger.warning(f"🔓 UNLOCK DETECTED: {event.symbol} +{unlock_amount:,.0f} tokens (+{unlock_pct:.1f}%) on day {event.days_since_tge}")

    def get_snapshot_history(
        self,
        symbol: str,
        days: int = 30,
        limit: Optional[int] = None
    ) -> List[SupplySnapshot]:
        """
        Get historical snapshots for a symbol.

        Args:
            symbol: Token symbol
            days: Number of days back to look
            limit: Max number of snapshots to return

        Returns:
            List of snapshots, newest first
        """
        symbol_dir = self.SNAPSHOTS_DIR / symbol
        if not symbol_dir.exists():
            return []

        # Get snapshots within date range
        cutoff_date = datetime.now(timezone.utc) - timedelta(days=days)
        snapshots = []

        for filepath in sorted(symbol_dir.glob(f"{symbol}_*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
            if limit and len(snapshots) >= limit:
                break

            with open(filepath, 'r') as f:
                data = json.load(f)

            snapshot = SupplySnapshot(**data)
            snapshot_time = datetime.fromisoformat(snapshot.timestamp.replace('Z', '+00:00'))

            if snapshot_time >= cutoff_date:
                snapshots.append(snapshot)

        return snapshots

    def calculate_supply_drift(self, symbol: str) -> Optional[Dict[str, Any]]:
        """
        Calculate supply drift vs TGE baseline.

        Returns:
            Drift analysis dict with current vs TGE comparison
        """
        # Get most recent snapshot
        current = self._get_most_recent_snapshot(symbol)
        if not current:
            logger.warning(f"No snapshots found for {symbol}")
            return None

        # Get TGE data
        tge_data = self._load_tge_data(symbol)
        if not tge_data:
            logger.warning(f"No TGE data found for {symbol}")
            return None

        # Calculate drift
        tge_circulating = tge_data.get("circulating_supply_at_tge")
        tge_float = tge_data.get("float_percent", 0)

        current_circulating = current.circulating_supply
        current_float = current.float_percent

        if not tge_circulating or not current_circulating:
            return None

        absolute_drift = current_circulating - tge_circulating
        relative_drift_pct = ((current_circulating - tge_circulating) / tge_circulating) * 100
        float_drift_pct = current_float - tge_float if current_float else None

        return {
            "symbol": symbol,
            "tge_circulating": tge_circulating,
            "current_circulating": current_circulating,
            "absolute_drift": absolute_drift,
            "relative_drift_pct": relative_drift_pct,
            "tge_float_pct": tge_float,
            "current_float_pct": current_float,
            "float_drift_pct": float_drift_pct,
            "days_since_tge": current.days_since_tge,
            "last_snapshot_date": current.timestamp
        }
