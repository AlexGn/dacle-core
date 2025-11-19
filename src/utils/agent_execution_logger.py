#!/usr/bin/env python3
"""
Agent Execution Logger

Purpose: Centralized logging of agent executions to the agent_executions table.
Integrates with TGE pipeline to automatically track which agents ran for each analysis.

Phase: Phase 2 Week 3 (Learning Loop)
Created: 2025-11-19 (Session 40+)
"""

import logging
from datetime import datetime
from typing import Any, Dict, Optional
from uuid import UUID

from supabase import Client

logger = logging.getLogger(__name__)


class AgentExecutionLogger:
    """Logs agent executions to agent_executions table for learning loop analysis"""

    def __init__(self, supabase_client: Client):
        """
        Initialize the logger.

        Args:
            supabase_client: Supabase client instance
        """
        self.client = supabase_client

    def log_agent_execution(
        self,
        analysis_id: str,
        agent_name: str,
        agent_version: str,
        execution_status: str,
        input_data: Optional[Dict[str, Any]] = None,
        output_data: Optional[Dict[str, Any]] = None,
        error_message: Optional[str] = None,
        execution_time_ms: Optional[int] = None,
        confidence_score: Optional[float] = None,
        decision: Optional[str] = None,
    ) -> Optional[str]:
        """
        Log a single agent execution to the database.

        Args:
            analysis_id: UUID of the parent analysis (from contexts table)
            agent_name: Name of the agent (e.g., 'agent_0', 'agent_2', 'agent_5')
            agent_version: Version of the agent (e.g., 'v3.0', 'v2.1')
            execution_status: 'success', 'failure', 'skipped'
            input_data: Agent input data (JSONB)
            output_data: Agent output data (JSONB)
            error_message: Error message if execution_status = 'failure'
            execution_time_ms: Execution time in milliseconds
            confidence_score: Agent confidence score (0-100)
            decision: Agent decision (e.g., 'EXECUTE_SHORT', 'SKIP', 'MONITOR')

        Returns:
            UUID of created agent_executions record, or None if failed
        """
        try:
            execution_data = {
                "analysis_id": analysis_id,
                "agent_name": agent_name,
                "agent_version": agent_version,
                "executed_at": datetime.now().isoformat(),
                "execution_status": execution_status,
                "input_data": input_data,
                "output_data": output_data,
                "error_message": error_message,
                "execution_time_ms": execution_time_ms,
                "confidence_score": confidence_score,
                "decision": decision,
            }

            result = self.client.table("agent_executions").insert(execution_data).execute()

            if result.data and len(result.data) > 0:
                execution_id = result.data[0]["id"]
                logger.debug(
                    f"✅ Logged {agent_name} execution: {execution_status} "
                    f"(analysis_id: {analysis_id[:8]}..., execution_id: {execution_id[:8]}...)"
                )
                return execution_id
            else:
                logger.warning(f"⚠️  No data returned when logging {agent_name} execution")
                return None

        except Exception as e:
            logger.error(f"❌ Failed to log {agent_name} execution: {e}", exc_info=True)
            return None

    def log_pipeline_agents(
        self,
        analysis_id: str,
        pipeline_result: Any,
        execution_times: Optional[Dict[str, int]] = None,
    ) -> Dict[str, Optional[str]]:
        """
        Log all agent executions from a complete TGE pipeline run.

        This is the main integration point for the TGE pipeline. Call this AFTER
        saving the pipeline result to the contexts table.

        Args:
            analysis_id: UUID of the analysis (contexts table)
            pipeline_result: PipelineResult object from TGE pipeline
            execution_times: Optional dict mapping agent names to execution times in ms

        Returns:
            Dictionary mapping agent names to their execution_ids (or None if failed)
        """
        execution_times = execution_times or {}
        execution_ids = {}

        # Agent 0: Data Validation
        if pipeline_result.validation_result:
            execution_ids["agent_0"] = self.log_agent_execution(
                analysis_id=analysis_id,
                agent_name="agent_0",
                agent_version="v3.0",
                execution_status="success",
                input_data={
                    "token_name": pipeline_result.token_name,
                    "token_symbol": pipeline_result.token_symbol,
                },
                output_data={
                    "data_quality_score": pipeline_result.data_quality_score,
                    "overall_confidence": pipeline_result.validation_result.overall_confidence,
                    "blockers": pipeline_result.validation_blockers,
                    "gaps": pipeline_result.validation_gaps,
                    "edge_cases": pipeline_result.edge_cases_detected,
                },
                execution_time_ms=execution_times.get("agent_0"),
                confidence_score=pipeline_result.validation_result.overall_confidence,
                decision=pipeline_result.validation_result.recommended_action.split(" - ")[0],
            )

        # Agent 0.5: Knowledge Base Lookup
        if pipeline_result.kb_confidence is not None:
            execution_ids["agent_0_5"] = self.log_agent_execution(
                analysis_id=analysis_id,
                agent_name="agent_0_5",
                agent_version="v3.0",
                execution_status="success",
                input_data={"token_name": pipeline_result.token_name},
                output_data={
                    "kb_confidence": pipeline_result.kb_confidence,
                    "vc_risk_score": pipeline_result.vc_risk_score,
                    "historical_patterns": pipeline_result.historical_patterns,
                },
                execution_time_ms=execution_times.get("agent_0_5"),
                confidence_score=pipeline_result.kb_confidence,
                decision="KB_MATCH" if pipeline_result.kb_data else "NO_MATCH",
            )

        # Agent 1: OTC Volume Analysis
        if pipeline_result.otc_available is not None:
            execution_ids["agent_1"] = self.log_agent_execution(
                analysis_id=analysis_id,
                agent_name="agent_1",
                agent_version="v3.0",
                execution_status="success",
                input_data={
                    "token_name": pipeline_result.token_name,
                    "token_symbol": pipeline_result.token_symbol,
                },
                output_data={
                    "otc_available": pipeline_result.otc_available,
                    "volume_trend": pipeline_result.volume_trend,
                    "platforms": pipeline_result.otc_platforms,
                    "volume_alert": pipeline_result.volume_alert,
                },
                execution_time_ms=execution_times.get("agent_1"),
                confidence_score=100.0 if pipeline_result.otc_available else 0.0,
                decision="OTC_FOUND" if pipeline_result.otc_available else "OTC_NOT_FOUND",
            )

        # Agent 2: Conviction Scoring
        if pipeline_result.conviction_score:
            execution_ids["agent_2"] = self.log_agent_execution(
                analysis_id=analysis_id,
                agent_name="agent_2",
                agent_version="v3.0",
                execution_status="success",
                input_data={
                    "token_name": pipeline_result.token_name,
                    "data_quality_score": pipeline_result.data_quality_score,
                    "kb_confidence": pipeline_result.kb_confidence,
                },
                output_data={
                    "final_score": pipeline_result.final_conviction,
                    "decision": pipeline_result.conviction_score.decision,
                    "component_scores": pipeline_result.conviction_score.component_scores,
                    "warnings": pipeline_result.conviction_score.warnings,
                },
                execution_time_ms=execution_times.get("agent_2"),
                confidence_score=pipeline_result.final_conviction * 10,  # Convert 0-10 to 0-100
                decision=pipeline_result.conviction_score.decision,
            )

        # Agent 5: Position Sizing
        if pipeline_result.position_sizing:
            execution_ids["agent_5"] = self.log_agent_execution(
                analysis_id=analysis_id,
                agent_name="agent_5",
                agent_version="v3.0",
                execution_status="success"
                if pipeline_result.agent_5_decision == "APPROVED"
                else "skipped",
                input_data={
                    "token_name": pipeline_result.token_name,
                    "conviction_score": pipeline_result.final_conviction,
                },
                output_data=pipeline_result.position_sizing,
                execution_time_ms=execution_times.get("agent_5"),
                decision=pipeline_result.agent_5_decision,
            )
        elif pipeline_result.agent_5_rejection_reason:
            # Agent 5 was skipped/rejected
            execution_ids["agent_5"] = self.log_agent_execution(
                analysis_id=analysis_id,
                agent_name="agent_5",
                agent_version="v3.0",
                execution_status="skipped",
                input_data={
                    "token_name": pipeline_result.token_name,
                    "conviction_score": pipeline_result.final_conviction,
                },
                output_data={"rejection_reason": pipeline_result.agent_5_rejection_reason},
                execution_time_ms=execution_times.get("agent_5"),
                decision=pipeline_result.agent_5_decision,
            )

        logger.info(
            f"✅ Logged {len([eid for eid in execution_ids.values() if eid])} agent executions "
            f"for analysis {analysis_id[:8]}..."
        )

        return execution_ids


def log_pipeline_agents_from_result(
    supabase_client: Client,
    analysis_id: str,
    pipeline_result: Any,
    execution_times: Optional[Dict[str, int]] = None,
) -> Dict[str, Optional[str]]:
    """
    Convenience function to log agent executions from a pipeline result.

    Usage:
        from utils.agent_execution_logger import log_pipeline_agents_from_result

        # After saving pipeline result to contexts table:
        execution_ids = log_pipeline_agents_from_result(
            supabase_client=kb.client,
            analysis_id=analysis_id,
            pipeline_result=result
        )

    Args:
        supabase_client: Supabase client instance
        analysis_id: UUID of the analysis
        pipeline_result: PipelineResult from TGE pipeline
        execution_times: Optional execution times dict

    Returns:
        Dictionary mapping agent names to execution_ids
    """
    logger_instance = AgentExecutionLogger(supabase_client)
    return logger_instance.log_pipeline_agents(analysis_id, pipeline_result, execution_times)
