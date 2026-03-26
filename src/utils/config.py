"""
Configuration management for DACLE
Loads environment variables and provides typed access

Configuration is loaded explicitly at application startup via load_config().
This avoids import-time side effects and makes the config loading predictable.
"""

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Dict, Any, Tuple

from dotenv import load_dotenv


def _detect_root_path(root_path: Optional[Path] = None) -> Path:
    """Resolve project root for runtime env/config loading."""
    if root_path is not None:
        return root_path

    current = Path(__file__).resolve().parent
    while current != current.parent:
        if (current / "pyproject.toml").exists() or (current / ".env").exists():
            return current
        current = current.parent

    return Path(__file__).parent.parent.parent


def load_runtime_env_files(
    root_path: Optional[Path] = None,
    shared_env_file: Optional[Path] = None,
    override: bool = True,
) -> Tuple[Path, ...]:
    """
    Load runtime env files with the same precedence used by cron_wrapper.sh.

    Order:
    1. external shared env file (`DACLE_SHARED_ENV_FILE` or `/home/clawd/.env.shared`)
    2. project-local `.env.shared`
    3. project-local `.env`
    4. project-local `.env.secret`
    """
    resolved_root = _detect_root_path(root_path)
    external_shared = shared_env_file
    if external_shared is None:
        external_shared = Path(
            os.getenv("DACLE_SHARED_ENV_FILE", "/home/clawd/.env.shared")
        )

    loaded: list[Path] = []
    for path in (
        external_shared,
        resolved_root / ".env.shared",
        resolved_root / ".env",
        resolved_root / ".env.secret",
    ):
        if path.exists() and path not in loaded:
            load_dotenv(path, override=override)
            loaded.append(path)

    return tuple(loaded)


# =============================================================================
# Timeout Configuration (Session 302)
# =============================================================================
# Centralized timeout values to avoid hardcoding throughout the codebase.
# Grouped by category for easier management and consistent behavior.
# =============================================================================

@dataclass
class TimeoutConfig:
    """
    Centralized timeout configuration for all external calls.

    Session 302: Created to eliminate ~150+ hardcoded timeout values.

    Categories:
    - API calls (external services like CoinGecko, CryptoRank, Binance)
    - LLM calls (OpenAI, Perplexity - longer due to inference time)
    - Subprocess calls (playbook generation, analysis pipelines)
    - Redis/cache calls (fast, local operations)
    - Telegram/webhook calls (notification delivery)
    """

    # === API Timeouts (External Services) ===
    # Standard API calls - most common, used for price/market data
    api_standard: float = 10.0  # Binance, most REST APIs
    api_fast: float = 5.0       # Quick lookups, health checks
    api_extended: float = 15.0  # Slower APIs, search endpoints
    api_slow: float = 30.0      # Heavy operations (backfill, bulk fetch)

    # === LLM/AI Timeouts ===
    llm_standard: float = 60.0  # OpenAI, Perplexity inference
    llm_fast: float = 30.0      # Simple completions
    llm_extended: float = 120.0 # Complex analysis, long prompts

    # === Subprocess/Pipeline Timeouts ===
    subprocess_fast: float = 30.0    # Quick scripts (validation)
    subprocess_standard: float = 60.0  # Analysis scripts
    subprocess_extended: float = 120.0 # Watchtower, batch operations
    subprocess_playbook: float = 180.0 # Playbook generation (can take ~80s)
    subprocess_pipeline: float = 300.0 # Full pipeline (5 min max)

    # === Cache/Redis Timeouts ===
    redis_connect: float = 2.0  # Connection timeout
    redis_socket: float = 5.0   # Socket operations
    cache_fast: float = 1.0     # In-memory cache checks

    # === Notification/Webhook Timeouts ===
    telegram: float = 10.0      # Telegram API calls
    webhook: float = 30.0       # Webhook delivery (keepalive 30s)
    sentry: float = 2.0         # Error reporting (non-blocking)

    # === Health Check Timeouts ===
    health_check: float = 5.0   # Service health checks
    health_redis: float = 2.0   # Redis health check

    @classmethod
    def from_env(cls) -> "TimeoutConfig":
        """
        Load timeout config with optional environment variable overrides.

        Environment variables follow pattern: TIMEOUT_{CATEGORY}_{TYPE}
        Example: TIMEOUT_API_STANDARD=15 (overrides api_standard to 15s)
        """
        def get_timeout(name: str, default: float) -> float:
            env_key = f"TIMEOUT_{name.upper()}"
            env_val = os.getenv(env_key)
            if env_val:
                try:
                    return float(env_val)
                except ValueError:
                    pass
            return default

        return cls(
            api_standard=get_timeout("API_STANDARD", 10.0),
            api_fast=get_timeout("API_FAST", 5.0),
            api_extended=get_timeout("API_EXTENDED", 15.0),
            api_slow=get_timeout("API_SLOW", 30.0),
            llm_standard=get_timeout("LLM_STANDARD", 60.0),
            llm_fast=get_timeout("LLM_FAST", 30.0),
            llm_extended=get_timeout("LLM_EXTENDED", 120.0),
            subprocess_fast=get_timeout("SUBPROCESS_FAST", 30.0),
            subprocess_standard=get_timeout("SUBPROCESS_STANDARD", 60.0),
            subprocess_extended=get_timeout("SUBPROCESS_EXTENDED", 120.0),
            subprocess_playbook=get_timeout("SUBPROCESS_PLAYBOOK", 180.0),
            subprocess_pipeline=get_timeout("SUBPROCESS_PIPELINE", 300.0),
            redis_connect=get_timeout("REDIS_CONNECT", 2.0),
            redis_socket=get_timeout("REDIS_SOCKET", 5.0),
            cache_fast=get_timeout("CACHE_FAST", 1.0),
            telegram=get_timeout("TELEGRAM", 10.0),
            webhook=get_timeout("WEBHOOK", 30.0),
            sentry=get_timeout("SENTRY", 2.0),
            health_check=get_timeout("HEALTH_CHECK", 5.0),
            health_redis=get_timeout("HEALTH_REDIS", 2.0),
        )


# Singleton timeout config (available without full app config load)
_timeout_config: Optional[TimeoutConfig] = None


def get_timeout_config() -> TimeoutConfig:
    """
    Get timeout configuration singleton.

    Unlike other configs, TimeoutConfig can be used without calling load_config()
    first - it will use defaults if not explicitly loaded.
    """
    global _timeout_config
    if _timeout_config is None:
        _timeout_config = TimeoutConfig.from_env()
    return _timeout_config


# Convenience aliases for common timeout values
def timeout_api(variant: str = "standard") -> float:
    """Get API timeout by variant: 'fast', 'standard', 'extended', 'slow'"""
    cfg = get_timeout_config()
    return {
        "fast": cfg.api_fast,
        "standard": cfg.api_standard,
        "extended": cfg.api_extended,
        "slow": cfg.api_slow,
    }.get(variant, cfg.api_standard)


def timeout_llm(variant: str = "standard") -> float:
    """Get LLM timeout by variant: 'fast', 'standard', 'extended'"""
    cfg = get_timeout_config()
    return {
        "fast": cfg.llm_fast,
        "standard": cfg.llm_standard,
        "extended": cfg.llm_extended,
    }.get(variant, cfg.llm_standard)


def timeout_subprocess(variant: str = "standard") -> float:
    """Get subprocess timeout by variant: 'fast', 'standard', 'extended', 'playbook', 'pipeline'"""
    cfg = get_timeout_config()
    return {
        "fast": cfg.subprocess_fast,
        "standard": cfg.subprocess_standard,
        "extended": cfg.subprocess_extended,
        "playbook": cfg.subprocess_playbook,
        "pipeline": cfg.subprocess_pipeline,
    }.get(variant, cfg.subprocess_standard)


@dataclass
class SupabaseConfig:
    """Supabase connection configuration"""

    url: str
    key: str

    @classmethod
    def from_env(cls) -> "SupabaseConfig":
        """Load Supabase config from environment variables"""
        url = os.getenv("SUPABASE_URL")
        key = os.getenv("SUPABASE_KEY")

        if not url:
            raise ValueError("SUPABASE_URL not set in environment")
        if not key:
            raise ValueError("SUPABASE_KEY not set in environment")

        return cls(url=url, key=key)


@dataclass
class DiscordConfig:
    """Discord bot configuration"""

    bot_token: str
    private_server_id: str
    analysis_channel_id: Optional[int] = None
    trades_channel_id: Optional[int] = None
    macro_channel_id: Optional[int] = None
    focus_channel_id: Optional[int] = None
    logs_channel_id: Optional[int] = None
    heartbeat_pulse_symbol: str = "💓"

    @classmethod
    def from_env(cls) -> "DiscordConfig":
        """Load Discord config from environment variables"""
        token = os.getenv("DISCORD_BOT_TOKEN")
        server_id = os.getenv("DISCORD_PRIVATE_SERVER_ID")
        
        def _to_int(env_key: str, default: Optional[int] = None) -> Optional[int]:
            val = os.getenv(env_key)
            try:
                return int(val) if val else default
            except (ValueError, TypeError):
                return default

        # Canonical channel IDs (Session 408+)
        analysis_id = _to_int("DISCORD_ANALYSIS_CHANNEL_ID", 1470403542253703369)
        trades_id = _to_int("DISCORD_TRADES_CHANNEL_ID", 1468948950412431598)
        macro_id = _to_int("DISCORD_MACRO_CHANNEL_ID", 1470361576237306058)
        focus_id = _to_int("DISCORD_FOCUS_CHANNEL_ID", 1470789144736174326)
        logs_id = _to_int("DISCORD_LOGS_CHANNEL_ID", 1468187517147939068)
        pulse_symbol = os.getenv("HEARTBEAT_PULSE_SYMBOL", "💓")

        # Discord is optional - allow None values if not configured
        # Raises error only if partially configured (one but not both)
        if token and not server_id:
            raise ValueError("DISCORD_BOT_TOKEN set but DISCORD_PRIVATE_SERVER_ID missing")
        if server_id and not token:
            raise ValueError("DISCORD_PRIVATE_SERVER_ID set but DISCORD_BOT_TOKEN missing")

        return cls(
            bot_token=token or "",
            private_server_id=server_id or "",
            analysis_channel_id=analysis_id,
            trades_channel_id=trades_id,
            macro_channel_id=macro_id,
            focus_channel_id=focus_id,
            logs_channel_id=logs_id,
            heartbeat_pulse_symbol=pulse_symbol,
        )


@dataclass
class RedisConfig:
    """Redis connection configuration"""

    url: str

    @classmethod
    def from_env(cls) -> "RedisConfig":
        """Load Redis config from environment variables"""
        url = os.getenv("REDIS_URL", "redis://localhost:6379")
        return cls(url=url)


@dataclass
class PerplexityConfig:
    """Perplexity API configuration (Session 79C)"""

    api_key: str

    @classmethod
    def from_env(cls) -> "PerplexityConfig":
        """Load Perplexity config from environment variables"""
        api_key = os.getenv("PERPLEXITY_API_KEY")

        if not api_key:
            raise ValueError("PERPLEXITY_API_KEY not set in environment")

        return cls(api_key=api_key)


@dataclass
class OpenAIConfig:
    """OpenAI API configuration (Session 79D)"""

    api_key: str
    model: str = "gpt-4o"  # GPT-4o: $2.50/1M tokens

    @classmethod
    def from_env(cls) -> "OpenAIConfig":
        """Load OpenAI config from environment variables"""
        api_key = os.getenv("OPENAI_API_KEY")

        if not api_key:
            raise ValueError("OPENAI_API_KEY not set in environment")

        model = os.getenv("OPENAI_MODEL", "gpt-4o")
        return cls(api_key=api_key, model=model)


@dataclass
class AppConfig:
    """Main application configuration"""

    env: str
    log_level: str
    supabase: SupabaseConfig
    discord: DiscordConfig
    redis: RedisConfig
    perplexity: Optional[PerplexityConfig]
    openai: Optional[OpenAIConfig]
    runtime_dir: Path = Path("data/runtime")
    scalper: dict = field(default_factory=dict)

    @classmethod
    def from_env(cls) -> "AppConfig":
        """Load complete app config from environment variables"""
        # Make Perplexity optional (Session 79C)
        perplexity_config = None
        if os.getenv("PERPLEXITY_API_KEY"):
            try:
                perplexity_config = PerplexityConfig.from_env()
            except ValueError:
                pass

        # Make OpenAI optional (Session 79D)
        openai_config = None
        if os.getenv("OPENAI_API_KEY"):
            try:
                openai_config = OpenAIConfig.from_env()
            except ValueError:
                pass

        # Session 495: Path resolution for runtime dir
        runtime_dir = Path(os.getenv("DACLE_RUNTIME_DIR", "data/runtime"))

        # Load scalper config from the canonical lighter.yaml file.
        scalper_cfg = {}
        try:
            # Find project root for config path
            current = Path(__file__).resolve().parent
            root = None
            while current != current.parent:
                if (current / "config" / "lighter.yaml").exists():
                    root = current
                    break
                current = current.parent
            
            if root:
                scalper_yaml = root / "config" / "lighter.yaml"
                import yaml
                with open(scalper_yaml) as f:
                    data = yaml.safe_load(f)
                    if isinstance(data, dict):
                        scalper_cfg = data.get("scalper", {})
        except Exception:
            pass

        return cls(
            env=os.getenv("DACLE_ENV", "development"),
            log_level=os.getenv("LOG_LEVEL", "INFO"),
            supabase=SupabaseConfig.from_env(),
            discord=DiscordConfig.from_env(),
            redis=RedisConfig.from_env(),
            perplexity=perplexity_config,
            openai=openai_config,
            runtime_dir=runtime_dir,
            scalper=scalper_cfg,
        )

    @property
    def is_development(self) -> bool:
        """Check if running in development mode"""
        return self.env.lower() == "development"

    @property
    def is_production(self) -> bool:
        """Check if running in production mode"""
        return self.env.lower() == "production"


# Singleton instance
_config: Optional[AppConfig] = None


def load_config(
    root_path: Optional[Path] = None,
    force_reload: bool = False,
    *,
    env_override: bool = True,
) -> AppConfig:
    """
    Explicitly load configuration from environment files at application startup.

    Implements the canonical environment hierarchy (Session 495):
    1. .env.shared (Base defaults)
    2. .env (Local environment overrides)
    3. .env.secret (Sensitive credentials - highest priority)

    Args:
        root_path: Path to project root directory. If None, auto-detects using __file__.
        force_reload: If True, reloads the configuration even if already loaded.
        env_override: Whether runtime dotenv files should overwrite already
            inherited environment variables.

    Returns:
        AppConfig: The loaded application configuration
    """
    global _config

    if _config is not None and not force_reload:
        return _config

    root_path = _detect_root_path(root_path)
    load_runtime_env_files(root_path=root_path, override=env_override)

    _config = AppConfig.from_env()
    return _config


def get_config() -> AppConfig:
    """
    Get application configuration singleton.

    Returns:
        AppConfig: The application configuration

    Raises:
        RuntimeError: If configuration has not been loaded via load_config()
    """
    global _config
    if _config is None:
        return load_config()
    return _config


def get_runtime_dir() -> Path:
    """Return canonical path for runtime state storage (Session 495)."""
    return Path(get_config().runtime_dir)


def get_polymarket_runtime_dir() -> Path:
    """Return canonical path for Polymarket runtime artifacts (Session 522)."""
    path = Path(get_runtime_dir() / "polymarket")
    return path


# Convenience functions for common configs
def get_supabase_config() -> SupabaseConfig:
    """Get Supabase configuration"""
    return get_config().supabase


def get_discord_config() -> DiscordConfig:
    """Get Discord configuration"""
    return get_config().discord


def get_redis_config() -> RedisConfig:
    """Get Redis configuration"""
    return get_config().redis


def get_perplexity_config() -> Optional[PerplexityConfig]:
    """Get Perplexity API configuration (Session 79C)"""
    return get_config().perplexity


def get_openai_config() -> Optional[OpenAIConfig]:
    """Get OpenAI API configuration (Session 79D)"""
    return get_config().openai
