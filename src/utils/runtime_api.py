"""Shared runtime API URL helpers for non-bot callers."""

from __future__ import annotations

import os


def get_runtime_api_base_url() -> str:
    """Resolve the canonical DACLE API base URL for runtime scripts and ops helpers."""
    return str(os.getenv("DACLE_API_URL") or os.getenv("API_BASE_URL") or "http://localhost:8000").rstrip("/")


def get_runtime_health_url() -> str:
    """Resolve the canonical health endpoint URL."""
    return f"{get_runtime_api_base_url()}/health"
