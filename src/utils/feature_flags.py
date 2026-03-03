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
    ENHANCED_CONFLUENCE_DISPLAY = "enhanced_confluence_display"
    POLYMARKET_UNIVERSE_SCANNER = "polymarket_universe_scanner"
    POLYMARKET_COMBINATORIAL = "polymarket_combinatorial"
    POLYMARKET_MAKER = "polymarket_maker"
    POLYMARKET_STRICT_DUAL_LEG_STALENESS = "polymarket_strict_dual_leg_staleness"
    POLYMARKET_LEASE_QTY_CAP = "polymarket_lease_qty_cap"
    POLYMARKET_DYNAMIC_PAIR_WIRING = "polymarket_dynamic_pair_wiring"
    DISCOVERY_ENSEMBLE_FUNNEL = "discovery_ensemble_funnel"
    DISCOVERY_RANK_DRIFT_TELEMETRY = "discovery_rank_drift_telemetry"
    CAPITAL_UNIFIED_PRIORITY = "capital_unified_priority"



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

    # Test variant checking
    if isinstance(is_enabled(FeatureFlag.ENHANCED_CONFLUENCE_DISPLAY), str):
        print(f"Confluence is variant: {is_enabled(FeatureFlag.ENHANCED_CONFLUENCE_DISPLAY)}")
        print(f"Is v1? {is_variant(FeatureFlag.ENHANCED_CONFLUENCE_DISPLAY, 'v1')}")
        print(f"Is v2? {is_variant(FeatureFlag.ENHANCED_CONFLUENCE_DISPLAY, 'v2')}")

    # Test get all flags
    print(f"\nAll flags: {json.dumps(get_all_flags(), indent=2)}")

    print("\n" + "="*50)
    print("✅ Feature flags system working correctly")
