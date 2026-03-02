import logging
import asyncio
from datetime import datetime, timezone
from typing import Dict, List, Any, Optional

from src.execution.state_manager import ExecutionStateManager
from src.execution.v2_models import ExecutionState, WarningCode
from src.execution.blofin_bridge import BlofinExecutionBridge

logger = logging.getLogger(__name__)

class ActiveConvictionMonitor:
    """
    Active trade management system (Phase 3).
    Monitors open positions for conviction drift and macro shifts.
    """

    SURVIVAL_THRESHOLD = 5.0
    DRIFT_ALERT_THRESHOLD = 2.0 # Drop of 2.0 in score

    def __init__(self):
        self.state_mgr = ExecutionStateManager()
        self.bridge = BlofinExecutionBridge()
        self._last_run = None

    async def monitor_step(self):
        """Perform a single monitoring pass over all active trades."""
        active_intents = self.state_mgr.list_active_intents()
        if not active_intents:
            logger.debug("No active intents to monitor")
            return

        logger.info(f"Monitoring {len(active_intents)} active trades for drift...")
        
        for intent in active_intents:
            try:
                await self._process_intent(intent)
            except Exception as e:
                logger.error(f"Failed to monitor {intent['symbol']}: {e}")

        self._last_run = datetime.now(timezone.utc)

    async def _process_intent(self, intent: Dict[str, Any]):
        symbol = intent["symbol"]
        id_key = intent["idempotency_key"]
        entry_score = intent.get("final_score", 0.0)
        side = intent["side"].upper()

        # 1. Fetch Fresh Conviction
        # We use the internal scoring flow
        current_result = await self._re_score_token(symbol, side, intent)
        if not current_result:
            return

        current_score = current_result.final_score
        
        # 2. Check for Survival Threshold
        if current_score < self.SURVIVAL_THRESHOLD:
            logger.warning(f"🚨 CRITICAL DRIFT: {symbol} score dropped to {current_score:.1f} (Threshold: {self.SURVIVAL_THRESHOLD})")
            await self._trigger_drift_alert(intent, current_result, "SURVIVAL_THRESHOLD_VIOLATION")
            # Update intent metadata without changing state
            self.state_mgr.update_intent_metadata(id_key, {
                "warnings": list(set(intent.get("warnings", []) + [WarningCode.WARN_CONVICTION_DRIFT])),
                "current_score": current_score
            })
            return

        # 3. Check for Relative Drift
        drift = entry_score - current_score
        if drift >= self.DRIFT_ALERT_THRESHOLD:
            logger.info(f"⚠️ Conviction Drift: {symbol} dropped {drift:.1f} points since entry")
            await self._trigger_drift_alert(intent, current_result, "RELATIVE_DRIFT")

    async def _re_score_token(self, symbol: str, side: str, intent: Dict[str, Any]) -> Optional[Any]:
        """Trigger a fresh scoring run for the token."""
        try:
            from src.conviction.tge_scorer import TGEConvictionScorer
            from src.conviction.long_scorer import LONGConvictionScorer
            from src.ta.computed_ta_builder import build_computed_ta
            
            logger.debug(f"Re-fetching data for {symbol}...")
            # Fetch fresh TA (using to_thread for safety)
            # Computed TA requires explicit levels; skip re-score if missing.
            entry = intent.get("entry")
            sl = intent.get("stop_loss")
            tp = intent.get("take_profit")
            if entry is None or sl is None or tp is None:
                logger.warning(f"Missing execution levels for monitor re-score: {symbol} {side}")
                return None

            ta_data = await asyncio.to_thread(
                build_computed_ta,
                symbol,
                side,
                float(entry),
                float(sl),
                float(tp),
            )
            if not ta_data:
                logger.warning(f"Could not fetch fresh TA for {symbol}")
                return None
                
            # Scorer selection
            if side == "SHORT": 
                scorer = TGEConvictionScorer()
            else:
                scorer = LONGConvictionScorer()
                
            # project_data needs to be enriched for base_scorer
            project_data = {
                "symbol": symbol,
                "name": symbol,
                "extraction_confidence": ta_data.get("extraction_confidence", 1.0),
                "ta_data": ta_data,
                **ta_data 
            }
            
            # Execute unified scoring flow
            return await asyncio.to_thread(scorer.score_project, project_data)
        except Exception as e:
            logger.error(f"Re-scoring failed for {symbol}: {e}")
            return None

    async def _trigger_drift_alert(self, intent: Dict[str, Any], current_result: Any, reason: str):
        """Trigger a Discord alert for David."""
        symbol = intent["symbol"]
        entry_score = intent.get("final_score", 0.0)
        current_score = current_result.final_score
        side = intent["side"].upper()
        
        status_emoji = "🚨" if reason == "SURVIVAL_THRESHOLD_VIOLATION" else "⚠️"
        
        message = (
            f"{status_emoji} **CONVICTION DRIFT DETECTED: {symbol}**\n"
            f"**Direction**: {side}\n"
            f"**Entry Score**: {entry_score:.1f}/10\n"
            f"**Current Score**: {current_score:.1f}/10\n"
            f"**Drift**: {current_score - entry_score:+.1f}\n"
            f"**Reason Code**: `{reason}`\n"
            f"**Top Opposing Flags**:\n" + "\n".join([f"• {f}" for f in current_result.opposing_flags[:3]]) + "\n\n"
            f"**Action Recommended**: Check chart for setup invalidation. Consider manual exit."
        )
        
        logger.info(f"Monitor Alert for {symbol}:\n{message}")
        
        # Integration with system-wide alert dispatcher if available
        try:
            from src.orchestration.action_dispatcher import dispatch_alert
            await asyncio.to_thread(dispatch_alert, "drift_monitor", message)
        except ImportError:
            pass
