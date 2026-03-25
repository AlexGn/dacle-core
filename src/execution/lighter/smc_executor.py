"""
SMC Executor - Lighter Integration.
Bridges high-conviction SMC Intelligence signals to the Lighter execution daemon.
Session 460 Institutional Pivot.
"""
import logging
from typing import Optional
from src.scoring.futures_setup_scorer import FuturesSetup
from src.risk.institutional_risk_manager import InstitutionalRiskManager

logger = logging.getLogger(__name__)

class SMCExecutor:
    def __init__(self, daemon, risk_manager: InstitutionalRiskManager):
        self.daemon = daemon
        self.risk_manager = risk_manager

    async def execute_setup(
        self, 
        setup: FuturesSetup, 
        stop_loss: float, 
        target_price: float
    ):
        """
        Submits an institutional order to Lighter based on an SMC setup.
        """
        if setup.decision_label != "CONTINUATION_READY":
            logger.info(f"Skipping execution for {setup.symbol}: {setup.decision_label}")
            return

        # 1. Calculate risk-adjusted position size
        usd_size = self.risk_manager.calculate_position_size(
            entry_price=setup.price, 
            stop_loss=stop_loss
        )

        if usd_size <= 0:
            logger.error(f"Invalid position size calculation for {setup.symbol}")
            return

        # 2. Determine side
        side = "SELL" if setup.move_direction == "DUMP" else "BUY"
        
        # 3. Submit to Lighter Daemon
        logger.info(f"🚀 Executing SMC {setup.setup_type} on Lighter: {side} {setup.symbol} Size: ${usd_size:,.2f}")
        
        try:
            await self.daemon.submit_order(
                symbol=setup.symbol,
                side=side,
                usd_size=usd_size,
                setup_type=f"{setup.setup_type}_SMC",
                entry_price=setup.price,
                stop_loss=stop_loss,
                target_price=target_price,
                setup_score=setup.setup_score
            )
        except Exception as e:
            logger.error(f"Failed to submit SMC order to Lighter: {e}")
