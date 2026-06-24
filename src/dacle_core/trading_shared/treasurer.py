import logging
import asyncio
from typing import Dict, List, Any, Optional
from datetime import datetime, timezone
from dataclasses import dataclass

logger = logging.getLogger(__name__)

@dataclass
class VenueBalance:
    venue_id: str
    asset: str
    free: float
    total: float
    last_updated: datetime

class GlobalTreasurer:
    """
    Monitors capital efficiency across all trading venues.
    Calculates rebalancing recommendations based on strategy demand and idle funds.
    """
    def __init__(self, target_allocations: Dict[str, float]):
        self.target_allocations = target_allocations # venue -> pct
        self.balances: Dict[str, VenueBalance] = {}
        self.rebalance_threshold_usd = 50.0

    async def update_balance(self, venue_id: str, asset: str, free: float, total: float):
        """Updates the internal state for a venue balance."""
        self.balances[venue_id] = VenueBalance(
            venue_id=venue_id,
            asset=asset,
            free=free,
            total=total,
            last_updated=datetime.now(timezone.utc)
        )

    def analyze_efficiency(self) -> List[Dict[str, Any]]:
        """
        Analyzes current balance distribution vs targets.
        Returns a list of rebalancing recommendations.
        """
        if not self.balances:
            return []

        total_equity = sum(b.total for b in self.balances.values())
        recommendations = []

        if total_equity <= 0:
            return []

        for venue_id, target_pct in self.target_allocations.items():
            current_bal = self.balances.get(venue_id)
            if not current_bal:
                continue

            target_val = total_equity * target_pct
            diff = current_bal.total - target_val

            if abs(diff) > self.rebalance_threshold_usd:
                recommendations.append({
                    "venue_id": venue_id,
                    "action": "WITHDRAW" if diff > 0 else "DEPOSIT",
                    "amount_usd": abs(diff),
                    "reason": f"{'Surplus' if diff > 0 else 'Deficit'} relative to {target_pct*100}% target"
                })

        return recommendations

    async def run_rebalance_monitor(self, interval_sec: int = 3600):
        """Background task to periodically log capital efficiency."""
        while True:
            recs = self.analyze_efficiency()
            if recs:
                for rec in recs:
                    logger.info(f"💰 TREASURY RECOMENDATION: {rec['action']} {rec['amount_usd']:.2f} USD from {rec['venue_id']}")
            await asyncio.sleep(interval_sec)
