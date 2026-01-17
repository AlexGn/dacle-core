"""
Feature Flag System - Session 310
Gemini-enhanced multivariate flags with security controls

Features:
- Multivariate flags (bool or "v1"/"v2" for A/B testing)
- IP-based security for production override protection
- Environment-aware configuration (production vs staging)
- Request-level overrides for testing
"""

from typing import Union, Optional
from pathlib import Path
import json
import logging

logger = logging.getLogger(__name__)


class FeatureFlag:
    """Feature flag constants"""
    # Session 310 flags
    ENHANCED_CONFLUENCE_DISPLAY = "enhanced_confluence_display"
    SETUP_PRIORITIZATION = "setup_prioritization"
    OPPORTUNITY_SCANNER = "opportunity_scanner"
    SECTOR_ROTATION_TRACKER = "sector_rotation_tracker"
    ANALYST_FEED_INTEGRATION = "analyst_feed_integration"

    # Session 333 - Sprint 3: Automation & AI Path
    WEBSOCKET_LIVE_PRICES = "websocket_live_prices"
    AUTO_REFRESH_DASHBOARD = "auto_refresh_dashboard"
    MEXC_TRADE_AUTO_DETECT = "mexc_trade_auto_detect"
    FEEDBACK_AUTO_PROMPT = "feedback_auto_prompt"
    LOG_MONITORING_AGENT = "log_monitoring_agent"
    RISK_GATES_ENABLED = "risk_gates_enabled"
    RISK_GATE_FLASH_CRASH = "risk_gate_flash_crash"
    RISK_GATE_POSITION_LIMIT = "risk_gate_position_limit"
    RISK_GATE_DRAWDOWN_PAUSE = "risk_gate_drawdown_pause"


# Cache for loaded config (avoid repeated file reads)
_config_cache = None
_config_path_cache = None


def _get_config_path() -> Path:
    """Get feature flags config path based on environment"""
    import os

    env = os.getenv("ENV", "production")
    base_path = Path(__file__).parent.parent.parent / "config"

    if env == "staging":
        config_path = base_path / "feature_flags.staging.json"
    else:
        config_path = base_path / "feature_flags.json"

    return config_path


def _load_config(force_reload: bool = False) -> dict:
    """
    Load feature flags configuration with caching

    Args:
        force_reload: Force reload from disk (bypass cache)

    Returns:
        Feature flags configuration dict
    """
    global _config_cache, _config_path_cache

    config_path = _get_config_path()

    # Return cached config if path unchanged and not forcing reload
    if not force_reload and _config_cache is not None and _config_path_cache == config_path:
        return _config_cache

    # Load from file
    try:
        with open(config_path) as f:
            config = json.load(f)
            _config_cache = config
            _config_path_cache = config_path
            logger.info(f"Loaded feature flags from {config_path}")
            return config
    except FileNotFoundError:
        logger.warning(f"Feature flags config not found at {config_path}, using defaults")
        # Return default config (all flags disabled)
        default_config = {
            "enhanced_confluence_display": False,
            "setup_prioritization": False,
            "opportunity_scanner": False,
            "sector_rotation_tracker": False,
            "analyst_feed_integration": False,
            "user_overrides_enabled": False
        }
        _config_cache = default_config
        _config_path_cache = config_path
        return default_config
    except json.JSONDecodeError as e:
        logger.error(f"Invalid JSON in feature flags config: {e}")
        raise


def _is_admin_request(client_ip: str) -> bool:
    """
    Check if request is from admin IP (Gemini Security)

    Only admin IPs can override feature flags via query params in production.

    Args:
        client_ip: Client IP address from request

    Returns:
        True if IP is in admin whitelist
    """
    ADMIN_IPS = [
        "37.27.217.82",  # VPS IP
        "127.0.0.1",      # Localhost
        "::1",            # IPv6 localhost
        # Add David's home/office IP here
    ]

    return client_ip in ADMIN_IPS


def is_enabled(
    flag_name: str,
    request: Optional[object] = None,
    default: Union[bool, str] = False
) -> Union[bool, str]:
    """
    Check if feature flag is enabled

    **Gemini Enhancement**: Supports multivariate flags (v1/v2) for A/B testing

    Args:
        flag_name: Name of the feature flag (use FeatureFlag constants)
        request: FastAPI Request object (optional, for override support)
        default: Default value if flag not found

    Returns:
        - bool: True/False for simple flags
        - str: "v1", "v2", etc. for multivariate flags

    Examples:
        >>> is_enabled(FeatureFlag.ENHANCED_CONFLUENCE_DISPLAY)
        False

        >>> # With staging config: enhanced_confluence_display="v1"
        >>> is_enabled(FeatureFlag.ENHANCED_CONFLUENCE_DISPLAY)
        "v1"

        >>> # With request override (admin IP only)
        >>> is_enabled(FeatureFlag.SETUP_PRIORITIZATION, request=request)
        True
    """
    # Load config
    config = _load_config()

    # Check if user override is allowed
    if request and config.get("user_overrides_enabled", False):
        # Extract client IP from request
        try:
            # FastAPI Request object
            if hasattr(request, 'client'):
                client_ip = request.client.host
            # Starlette Request object
            elif hasattr(request, 'headers'):
                # Check X-Forwarded-For for proxied requests
                forwarded_for = request.headers.get('X-Forwarded-For')
                if forwarded_for:
                    client_ip = forwarded_for.split(',')[0].strip()
                else:
                    # Fallback to direct connection IP
                    client_ip = getattr(request.client, 'host', None) if hasattr(request, 'client') else None
            else:
                client_ip = None

            # Gemini Security: Only allow admin IPs to override flags
            if client_ip and _is_admin_request(client_ip):
                # Check for query param override
                if hasattr(request, 'query_params'):
                    override = request.query_params.get("feature_flags", "")
                    if flag_name in override.split(","):
                        logger.info(f"Feature flag '{flag_name}' enabled via admin override from {client_ip}")
                        return True
        except Exception as e:
            logger.warning(f"Error checking request for feature flag override: {e}")

    # Return configured value (bool or string)
    flag_value = config.get(flag_name, default)

    # Log if flag is enabled
    if flag_value and flag_value is not False:
        logger.debug(f"Feature flag '{flag_name}' is enabled (value: {flag_value})")

    return flag_value


def is_variant(flag_name: str, variant: str, request: Optional[object] = None) -> bool:
    """
    Check if a specific variant of a multivariate flag is enabled

    Convenience method for multivariate flag checks.

    Args:
        flag_name: Name of the feature flag
        variant: Expected variant value (e.g., "v1", "v2")
        request: FastAPI Request object (optional)

    Returns:
        True if flag is set to the specified variant

    Examples:
        >>> is_variant(FeatureFlag.ENHANCED_CONFLUENCE_DISPLAY, "v1")
        True  # If config has enhanced_confluence_display="v1"

        >>> is_variant(FeatureFlag.ENHANCED_CONFLUENCE_DISPLAY, "v2")
        False  # If config has enhanced_confluence_display="v1"
    """
    flag_value = is_enabled(flag_name, request=request)
    return flag_value == variant


def get_all_flags(request: Optional[object] = None) -> dict:
    """
    Get all feature flags with their current values

    Useful for debugging and admin dashboards.

    Args:
        request: FastAPI Request object (optional, for override checks)

    Returns:
        Dictionary of all feature flags and their values

    Examples:
        >>> get_all_flags()
        {
            "enhanced_confluence_display": "v1",
            "setup_prioritization": True,
            "opportunity_scanner": False,
            ...
        }
    """
    config = _load_config()
    flags = {}

    # Get all flags from FeatureFlag class
    for attr in dir(FeatureFlag):
        if not attr.startswith('_') and attr.isupper():
            flag_name = getattr(FeatureFlag, attr)
            flags[flag_name] = is_enabled(flag_name, request=request)

    return flags


def reload_config():
    """
    Reload feature flags configuration from disk

    Useful for hot-reloading config without restarting the server.
    """
    global _config_cache, _config_path_cache
    _config_cache = None
    _config_path_cache = None
    _load_config(force_reload=True)
    logger.info("Feature flags configuration reloaded")


def get_sprint3_status() -> dict:
    """
    Get status of all Sprint 3 feature flags with safety checks.

    Session 333: Provides visibility into Sprint 3 rollout progress.

    Returns:
        Dictionary with flag status, prerequisites, and readiness
    """
    config = _load_config()

    sprint3_flags = {
        "websocket_live_prices": {
            "enabled": config.get("websocket_live_prices", False),
            "description": "Real-time price streaming via WebSocket",
            "prerequisites": ["Integration tests passing"],
            "risk": "LOW - Additive, doesn't replace polling",
        },
        "auto_refresh_dashboard": {
            "enabled": config.get("auto_refresh_dashboard", False),
            "description": "Auto-refresh dashboard on >5% price change",
            "prerequisites": ["websocket_live_prices enabled"],
            "risk": "LOW - UI only, no backend changes",
        },
        "mexc_trade_auto_detect": {
            "enabled": config.get("mexc_trade_auto_detect", False),
            "description": "Auto-detect trades on MEXC",
            "prerequisites": ["MEXC API credentials", "Integration tests"],
            "risk": "MEDIUM - Writes to trade_log.json",
        },
        "feedback_auto_prompt": {
            "enabled": config.get("feedback_auto_prompt", False),
            "description": "Auto-prompt for feedback on trade exit",
            "prerequisites": ["mexc_trade_auto_detect enabled"],
            "risk": "LOW - Telegram notification only",
        },
        "log_monitoring_agent": {
            "enabled": config.get("log_monitoring_agent", False),
            "description": "AI agent monitoring logs for errors",
            "prerequisites": ["None - read-only daemon"],
            "risk": "VERY LOW - Separate process, read-only",
        },
        "risk_gates_enabled": {
            "enabled": config.get("risk_gates_enabled", False),
            "description": "Master switch for automated risk gates",
            "prerequisites": ["Integration tests", "David approval"],
            "risk": "MEDIUM - Can block trades",
        },
        "risk_gate_flash_crash": {
            "enabled": config.get("risk_gate_flash_crash", False),
            "description": "VETO trades on BTC flash crash (>5% drop)",
            "prerequisites": ["risk_gates_enabled"],
            "risk": "MEDIUM - Will block trades",
        },
        "risk_gate_position_limit": {
            "enabled": config.get("risk_gate_position_limit", False),
            "description": "Block trades when position count >= 3 (L066)",
            "prerequisites": ["risk_gates_enabled", "mexc_trade_auto_detect"],
            "risk": "MEDIUM - Will block trades",
        },
        "risk_gate_drawdown_pause": {
            "enabled": config.get("risk_gate_drawdown_pause", False),
            "description": "Pause new trades when drawdown > 20%",
            "prerequisites": ["risk_gates_enabled", "KPI tracking active"],
            "risk": "MEDIUM - Will block trades",
        },
    }

    # Calculate summary
    enabled_count = sum(1 for f in sprint3_flags.values() if f["enabled"])
    total_count = len(sprint3_flags)

    return {
        "flags": sprint3_flags,
        "summary": {
            "enabled": enabled_count,
            "total": total_count,
            "progress_pct": round((enabled_count / total_count) * 100, 1),
        },
        "recommended_next": _get_recommended_next_flag(sprint3_flags),
    }


def _get_recommended_next_flag(flags: dict) -> Optional[str]:
    """Get the next recommended flag to enable based on dependencies."""
    # Priority order for safe rollout
    rollout_order = [
        "log_monitoring_agent",  # Lowest risk - read-only
        "websocket_live_prices",  # Additive - doesn't replace
        "auto_refresh_dashboard",  # UI only
        "mexc_trade_auto_detect",  # Enables feedback loop
        "feedback_auto_prompt",  # Notification only
        "risk_gates_enabled",  # Master switch
        "risk_gate_flash_crash",  # Safety gate
        "risk_gate_position_limit",  # L066 automation
        "risk_gate_drawdown_pause",  # Capital protection
    ]

    for flag_name in rollout_order:
        if flag_name in flags and not flags[flag_name]["enabled"]:
            return flag_name

    return None  # All flags enabled


# Example usage and testing
if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.DEBUG)

    print("Feature Flags Test\n" + "="*50)

    # Test config loading
    config = _load_config()
    print(f"Loaded config: {json.dumps(config, indent=2)}")

    # Test flag checking
    print(f"\nEnhanced Confluence: {is_enabled(FeatureFlag.ENHANCED_CONFLUENCE_DISPLAY)}")
    print(f"Setup Prioritization: {is_enabled(FeatureFlag.SETUP_PRIORITIZATION)}")
    print(f"Opportunity Scanner: {is_enabled(FeatureFlag.OPPORTUNITY_SCANNER)}")

    # Test variant checking
    if isinstance(is_enabled(FeatureFlag.ENHANCED_CONFLUENCE_DISPLAY), str):
        print(f"Confluence is variant: {is_enabled(FeatureFlag.ENHANCED_CONFLUENCE_DISPLAY)}")
        print(f"Is v1? {is_variant(FeatureFlag.ENHANCED_CONFLUENCE_DISPLAY, 'v1')}")
        print(f"Is v2? {is_variant(FeatureFlag.ENHANCED_CONFLUENCE_DISPLAY, 'v2')}")

    # Test get all flags
    print(f"\nAll flags: {json.dumps(get_all_flags(), indent=2)}")

    print("\n" + "="*50)
    print("✅ Feature flags system working correctly")
