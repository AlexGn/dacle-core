"""
Per-Step Timeout Manager - Session 346

Manages timeouts and retries for multi-step refresh pipeline.
Replaces single global timeout with per-step strategies.
"""

import logging
import time
import json
from pathlib import Path
from typing import Callable, Any, Dict
from functools import wraps

logger = logging.getLogger(__name__)


class TimeoutManager:
    """
    Manages per-step timeouts and retry logic.

    Features:
    - Per-step timeout configuration
    - Exponential backoff for retries
    - Graceful degradation on failure
    """

    def __init__(self, config_path: str = "config/refresh_timeouts.json"):
        """Load timeout configuration from JSON file."""
        self.config_path = Path(config_path)
        self.config = self._load_config()

    def _load_config(self) -> Dict:
        """Load timeout config from JSON file."""
        if not self.config_path.exists():
            logger.warning(f"Config not found: {self.config_path}, using defaults")
            return self._get_default_config()

        with open(self.config_path) as f:
            return json.load(f)

    def _get_default_config(self) -> Dict:
        """Default timeout configuration."""
        return {
            "steps": {
                "default": {"timeout": 30, "retries": 2}
            },
            "total_timeout": 180,
            "retry_backoff_base": 2.0
        }

    def get_step_config(self, step_name: str) -> Dict:
        """Get timeout config for specific step."""
        return self.config["steps"].get(step_name, self.config["steps"].get("default", {"timeout": 30, "retries": 2}))

    def execute_step(
        self,
        step_name: str,
        func: Callable,
        timeout: int = None,
        retries: int = None,
        **kwargs
    ) -> Any:
        """
        Execute function with timeout and retry logic.

        Args:
            step_name: Name of step (for logging)
            func: Function to execute
            timeout: Override default timeout (seconds)
            retries: Override default retry count
            **kwargs: Arguments to pass to func

        Returns:
            Function result

        Raises:
            Exception: After exhausting all retries
        """
        config = self.get_step_config(step_name)
        timeout = timeout or config["timeout"]
        retries = retries if retries is not None else config["retries"]
        backoff_base = self.config.get("retry_backoff_base", 2.0)

        for attempt in range(retries + 1):
            try:
                logger.debug(f"⚡ {step_name} (attempt {attempt + 1}/{retries + 1}, timeout: {timeout}s)")

                # Execute with timeout
                # Note: For true timeout enforcement, use concurrent.futures or signal
                # For now, we rely on underlying API timeouts
                result = func(**kwargs)

                logger.debug(f"✓ {step_name} succeeded")
                return result

            except Exception as e:
                is_last_attempt = (attempt == retries)

                if is_last_attempt:
                    logger.error(f"✗ {step_name} failed after {retries + 1} attempts: {e}")
                    raise

                # Calculate backoff delay
                delay = backoff_base ** attempt
                logger.warning(f"⚠️ {step_name} failed (attempt {attempt + 1}), retrying in {delay}s: {e}")
                time.sleep(delay)
