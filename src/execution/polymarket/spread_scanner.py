"""
Polymarket Yes/No Spread Scanner

Detects same-market arbitrage opportunities where YES + NO < $1.00.
This is market-neutral, guaranteed profit (before fees).

Strategy:
- Scan all active Polymarket markets
- Find markets where YES bid + NO bid < $1.00
- Calculate net edge after 3.15% taker fee
- Execute when net edge > threshold (default 2%)

Example:
    YES bid: $0.46, NO bid: $0.47
    Combined: $0.93
    Gross edge: 7.5%
    Net edge: ~4% (after 3.15% fee on winning side)

Usage:
    scanner = SpreadScanner(config)
    opportunities = await scanner.scan(min_net_edge=0.02)

    if opportunities:
        result = await scanner.execute_spread_arb(opportunities[0], size=50)
"""

import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from pathlib import Path

from src.execution.polymarket.client_wrapper import PolymarketClientWrapper as PolymarketClient
from src.execution.polymarket.ctf_executor import PolymarketCTFExecutor

logger = logging.getLogger(__name__)


@dataclass
class SpreadOpportunity:
    """Yes/No spread arbitrage opportunity."""
    market_id: str
    condition_id: str
    title: str
    yes_bid: float
    no_bid: float
    combined_cost: float
    gross_edge: float  # 1.0 - combined_cost
    net_edge: float  # After fees
    yes_liquidity: float  # Available at best bid
    no_liquidity: float
    max_executable: float  # Min of both sides
    expiry_time: Optional[datetime] = None
    category: str = ""


@dataclass
class SpreadScannerConfig:
    """Configuration for spread scanner."""
    min_net_edge: float = 0.02  # Minimum 2% net edge
    min_liquidity: float = 50.0  # Minimum $ per side
    max_slippage_bps: float = 50  # 0.5% slippage tolerance
    scan_limit: int = 100  # Max markets to scan
    categories: List[str] = field(default_factory=list)  # Filter by category


@dataclass
class ScanResult:
    """Result of a spread scan."""
    timestamp: datetime
    markets_scanned: int
    opportunities_found: int
    opportunities: List[SpreadOpportunity]
    best_opportunity: Optional[SpreadOpportunity]
    scan_duration_ms: float


class SpreadScanner:
    """
    Polymarket Yes/No spread scanner.

    Scans for market-neutral arbitrage where YES + NO < $1.00.
    Guaranteed $1.00 payout per share pair = risk-free profit.
    """

    TAKER_FEE_RATE = 0.0315  # 3.15% Polymarket fee

    def __init__(self, config: SpreadScannerConfig):
        self.config = config
        self.client: Optional[PolymarketClient] = None
        self.executor: Optional[PolymarketCTFExecutor] = None
        self._last_scan: Optional[ScanResult] = None

    async def connect(self):
        """Initialize client and executor."""
        import os

        mode = os.getenv("POLY_MODE", "SHADOW").upper()

        # Initialize PolymarketClient wrapper (uses httpx for public endpoints)
        self.client = PolymarketClient(
            config={"mode": mode},
            client=None,  # Not needed for public endpoints
        )

        self.executor = PolymarketCTFExecutor({"mode": mode})
        logger.info(f"SpreadScanner connected (mode={mode})")

    async def disconnect(self):
        """Cleanup connections."""
        if self.client:
            await self.client.disconnect()

    async def scan(self, config_override: Optional[SpreadScannerConfig] = None) -> ScanResult:
        """
        Scan all active markets for Yes/No spread opportunities.

        Args:
            config_override: Optional config overrides for this scan

        Returns:
            ScanResult with all opportunities found
        """
        start_ts = datetime.now(timezone.utc)
        cfg = config_override or self.config

        if not self.client:
            await self.connect()

        opportunities = []
        markets_scanned = 0

        # Fetch active markets
        try:
            markets_response = await self.client.get_markets(
                status="active",
                limit=cfg.scan_limit,
            )
        except Exception as e:
            logger.error(f"Failed to fetch markets: {e}")
            return ScanResult(
                timestamp=start_ts,
                markets_scanned=0,
                opportunities_found=0,
                opportunities=[],
                best_opportunity=None,
                scan_duration_ms=0,
            )

        markets = markets_response.get("markets", [])
        logger.info(f"Scanning {len(markets)} markets for spread arb...")

        for market in markets:
            markets_scanned += 1

            # Gamma API uses conditionId, clob API uses condition_id
            market_id = market.get("conditionId") or market.get("condition_id") or market.get("id")

            if not market_id:
                continue

            # Only scan active, non-closed markets
            if market.get("closed") or market.get("archived") or not market.get("active"):
                continue

            # Filter by category if specified
            if cfg.categories:
                market_category = market.get("category", "")
                if market_category not in cfg.categories:
                    continue

            # Gamma API provides outcomePrices array directly: ["YES_price", "NO_price"]
            outcome_prices = market.get("outcomePrices") or market.get("outcome_prices") or []

            if not outcome_prices or len(outcome_prices) < 2:
                continue

            try:
                yes_bid = float(outcome_prices[0]) if outcome_prices[0] else 0
                no_bid = float(outcome_prices[1]) if outcome_prices[1] else 0
            except (ValueError, TypeError):
                continue

            if not yes_bid or not no_bid:
                continue

            # Estimate liquidity from market liquidity field
            liquidity = float(market.get("liquidity", 0) or market.get("liquidityNum", 0))
            yes_size = liquidity / 2  # Estimate split between outcomes
            no_size = liquidity / 2

            # Calculate edge
            combined_cost = yes_bid + no_bid

            if combined_cost >= 1.0:
                continue  # No arb opportunity

            gross_edge = 1.0 - combined_cost

            # Fee on winning side only (3.15% of $1.00)
            fee = self.TAKER_FEE_RATE * 1.0

            # Slippage estimate
            slippage = (yes_bid * cfg.max_slippage_bps / 10000) + \
                       (no_bid * cfg.max_slippage_bps / 10000)

            net_edge = gross_edge - fee - slippage

            if net_edge < cfg.min_net_edge:
                continue  # Below threshold

            # Calculate max executable size
            max_executable = min(yes_size, no_size, cfg.min_liquidity)

            if max_executable < cfg.min_liquidity:
                continue  # Insufficient liquidity

            opp = SpreadOpportunity(
                market_id=market_id,
                condition_id=market.get("conditionId") or market.get("condition_id", market_id),
                title=market.get("question", market.get("title", market.get("eventTitle", ""))),
                yes_bid=yes_bid,
                no_bid=no_bid,
                combined_cost=combined_cost,
                gross_edge=gross_edge,
                net_edge=net_edge,
                yes_liquidity=yes_size,
                no_liquidity=no_size,
                max_executable=max_executable,
                category=market.get("category", ""),
            )

            opportunities.append(opp)

        # Sort by net edge descending
        opportunities.sort(key=lambda x: x.net_edge, reverse=True)

        best = opportunities[0] if opportunities else None

        end_ts = datetime.now(timezone.utc)
        scan_duration = (end_ts - start_ts).total_seconds() * 1000

        result = ScanResult(
            timestamp=start_ts,
            markets_scanned=markets_scanned,
            opportunities_found=len(opportunities),
            opportunities=opportunities,
            best_opportunity=best,
            scan_duration_ms=scan_duration,
        )

        self._last_scan = result

        logger.info(
            f"Scan complete: {markets_scanned} markets, "
            f"{len(opportunities)} opportunities, "
            f"best edge={best.net_edge:.2%}" if best else "best edge=N/A"
        )

        return result

    async def execute_spread_arb(
        self,
        opportunity: SpreadOpportunity,
        size_usd: float,
        dry_run: bool = True,
    ) -> Dict[str, Any]:
        """
        Execute Yes/No spread arbitrage.

        Atomic execution:
        1. Call splitPosition() to mint YES + NO shares from USDC
        2. Both legs execute simultaneously (no leg risk)
        3. Hold until resolution (guaranteed $1.00 payout per share)

        Args:
            opportunity: SpreadOpportunity to execute
            size_usd: Size in USD (per side)
            dry_run: If True, simulate without executing

        Returns:
            Execution result dict
        """
        if not self.executor:
            await self.connect()

        logger.info(
            f"[spread-arb] Executing: {opportunity.title[:40]}... "
            f"size=${size_usd}, edge={opportunity.net_edge:.2%}, dry_run={dry_run}"
        )

        if dry_run:
            expected_profit = size_usd * 2 * opportunity.net_edge
            return {
                "status": "success",
                "dry_run": True,
                "market_id": opportunity.market_id,
                "size_usd": size_usd,
                "yes_bid": opportunity.yes_bid,
                "no_bid": opportunity.no_bid,
                "combined_cost": opportunity.combined_cost,
                "net_edge": opportunity.net_edge,
                "expected_profit": expected_profit,
                "message": f"Would execute splitPosition({size_usd * 2} USDC)",
            }

        # LIVE execution: split USDC into YES + NO shares
        result = await self.executor.split_position(
            condition_id=opportunity.condition_id,
            amount_usdc=size_usd * 2,  # Total for both sides
        )

        if result.get("status") == "success":
            result["opportunity"] = {
                "market_id": opportunity.market_id,
                "yes_bid": opportunity.yes_bid,
                "no_bid": opportunity.no_bid,
                "net_edge": opportunity.net_edge,
            }
            result["expected_profit"] = size_usd * 2 * opportunity.net_edge
            return result
        else:
            logger.error(f"[spread-arb] Execution failed: {result.get('error')}")
            return result

    def get_last_scan(self) -> Optional[ScanResult]:
        """Return result of most recent scan."""
        return self._last_scan

    def generate_report(self, result: Optional[ScanResult] = None) -> str:
        """Generate human-readable scan report."""
        if result is None:
            result = self._last_scan
            if result is None:
                return "No scan results available."

        r = result

        report = f"""
================================================================================
                    POLYMARKET SPREAD ARB SCAN REPORT
================================================================================

SCAN SUMMARY
------------
Timestamp:           {r.timestamp.isoformat()}
Markets Scanned:     {r.markets_scanned}
Opportunities Found: {r.opportunities_found}
Scan Duration:       {r.scan_duration_ms:.0f}ms

{'TOP OPPORTUNITIES' if r.opportunities else 'NO OPPORTUNITIES FOUND'}
-----------------
"""

        if r.opportunities:
            for i, opp in enumerate(r.opportunities[:5], 1):
                expected_profit = opp.max_executable * 2 * opp.net_edge
                report += f"""
{i}. {opp.title[:50]}...
   Market ID: {opp.market_id[:20]}...
   YES: ${opp.yes_bid:.3f} (${opp.yes_liquidity:.0f} available)
   NO:  ${opp.no_bid:.3f} (${opp.no_liquidity:.0f} available)
   Combined: ${opp.combined_cost:.3f}
   Gross Edge: {opp.gross_edge:.2%}
   Net Edge:   {opp.net_edge:.2%}
   Max Executable: ${opp.max_executable:.0f}
   Expected Profit: ${expected_profit:.2f}
"""
        else:
            report += "\n   No spread opportunities found matching criteria.\n"

        report += f"""
================================================================================
Report generated: {datetime.now(timezone.utc).isoformat()}
================================================================================
"""

        return report


async def main():
    """Run a single scan and print results."""
    import json

    config = SpreadScannerConfig(
        min_net_edge=0.02,
        min_liquidity=50.0,
        scan_limit=100,
    )

    scanner = SpreadScanner(config)
    await scanner.connect()

    result = await scanner.scan()
    print(scanner.generate_report(result))

    # Save to file
    output_path = Path("data/polymarket/spread_scan_latest.json")
    output_path.parent.mkdir(parents=True, exist_ok=True)

    output = {
        "timestamp": result.timestamp.isoformat(),
        "markets_scanned": result.markets_scanned,
        "opportunities_found": result.opportunities_found,
        "scan_duration_ms": result.scan_duration_ms,
        "opportunities": [
            {
                "market_id": o.market_id,
                "title": o.title,
                "yes_bid": o.yes_bid,
                "no_bid": o.no_bid,
                "net_edge": o.net_edge,
                "max_executable": o.max_executable,
            }
            for o in result.opportunities[:10]
        ],
    }

    with open(output_path, "w") as f:
        json.dump(output, f, indent=2)

    logger.info(f"Scan results saved to {output_path}")

    await scanner.disconnect()


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
