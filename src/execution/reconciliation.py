import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from src.execution.blofin_bridge import BlofinExecutionBridge
from src.execution.lifecycle_shadow import append_lifecycle_shadow_event
from src.execution.state_manager import ExecutionStateManager
from src.execution.v2_models import ExecutionState

logger = logging.getLogger(__name__)


class ExecutionReconciliationWorker:
    """
    Reconcile non-terminal execution intents with exchange truth.
    """

    def __init__(
        self,
        *,
        state_mgr: Optional[ExecutionStateManager] = None,
        bridge: Optional[BlofinExecutionBridge] = None,
    ):
        self.state_mgr = state_mgr or ExecutionStateManager()
        self.bridge = bridge or BlofinExecutionBridge()

    @staticmethod
    def _normalize_state(raw: Any) -> Optional[ExecutionState]:
        if isinstance(raw, ExecutionState):
            return raw
        text = str(raw or "").strip()
        if not text:
            return None
        try:
            return ExecutionState(text)
        except ValueError:
            return None

    @staticmethod
    def _resolve_order_id(intent: Dict[str, Any]) -> Optional[str]:
        for key in ("entry_order_id", "order_id"):
            candidate = str(intent.get(key) or "").strip()
            if candidate:
                return candidate
        raw_order_ids = intent.get("order_ids")
        if isinstance(raw_order_ids, list):
            for item in raw_order_ids:
                candidate = str(item or "").strip()
                if candidate:
                    return candidate
        return None

    @staticmethod
    def _build_transition_metadata(status: Dict[str, Any], order_id: str) -> Dict[str, Any]:
        metadata: Dict[str, Any] = {
            "reconciled_at": datetime.now(timezone.utc).isoformat(),
            "reconciliation_source": "blofin_bridge",
            "reconciliation_order_id": order_id,
        }
        for key in ("filled_qty", "remaining_qty", "avg_fill_price", "updated_at"):
            if key in status:
                metadata[key] = status[key]
        return metadata

    async def reconcile_once(self, account_id: Optional[str] = None) -> Dict[str, int]:
        summary = {
            "intents_scanned": 0,
            "intents_transitioned": 0,
            "intents_unchanged": 0,
            "intents_ambiguous": 0,
            "errors": 0,
        }
        intents = await asyncio.to_thread(self.state_mgr.list_reconcilable_intents, account_id)
        summary["intents_scanned"] = len(intents)
        for intent in intents:
            try:
                idempotency_key = str(intent.get("idempotency_key") or "").strip()
                symbol = str(intent.get("symbol") or "").strip()
                if not idempotency_key or not symbol:
                    summary["intents_ambiguous"] += 1
                    continue

                order_id = self._resolve_order_id(intent)
                if not order_id:
                    logger.warning("Reconciliation missing order id for intent=%s", idempotency_key)
                    summary["intents_ambiguous"] += 1
                    continue

                intent_account_id = (
                    str(intent.get("account_id") or "").strip()
                    or ExecutionStateManager.account_id_from_scoped_key(idempotency_key)
                    or "primary"
                )
                status = await asyncio.to_thread(
                    self.bridge.get_order_status,
                    symbol,
                    order_id,
                    intent_account_id,
                )
                if not isinstance(status, dict) or not status:
                    summary["intents_ambiguous"] += 1
                    continue

                remote_state = self._normalize_state(status.get("state"))
                current_state = self._normalize_state(intent.get("state"))
                if remote_state is None or current_state is None:
                    summary["intents_ambiguous"] += 1
                    continue

                if remote_state == current_state:
                    summary["intents_unchanged"] += 1
                    continue

                transition_ok = await self.state_mgr.transition_to_async(
                    idempotency_key,
                    remote_state,
                    self._build_transition_metadata(status, order_id),
                    account_id=intent_account_id,
                )
                if transition_ok:
                    shadow_approved_raw = intent.get("execution_shadow_legacy_approved")
                    shadow_approved = shadow_approved_raw if isinstance(shadow_approved_raw, bool) else None
                    try:
                        append_lifecycle_shadow_event(
                            idempotency_key=idempotency_key,
                            account_id=intent_account_id,
                            symbol=symbol,
                            state=remote_state,
                            source="reconciliation",
                            shadow_approved=shadow_approved,
                            details={"reconciliation_order_id": order_id},
                        )
                    except Exception as e:
                        logger.warning(
                            "Failed to append reconciliation lifecycle shadow event for %s: %s",
                            idempotency_key,
                            e,
                        )
                    summary["intents_transitioned"] += 1
                else:
                    summary["intents_ambiguous"] += 1
            except Exception as e:
                logger.error("Reconciliation failed for intent=%s: %s", intent.get("idempotency_key"), e)
                summary["errors"] += 1
        return summary
