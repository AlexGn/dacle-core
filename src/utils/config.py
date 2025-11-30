"""
Configuration management for DACLE
Loads environment variables and provides typed access

Configuration is loaded explicitly at application startup via load_config().
This avoids import-time side effects and makes the config loading predictable.
"""

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv


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

    @classmethod
    def from_env(cls) -> "DiscordConfig":
        """Load Discord config from environment variables"""
        token = os.getenv("DISCORD_BOT_TOKEN")
        server_id = os.getenv("DISCORD_PRIVATE_SERVER_ID")

        if not token:
            raise ValueError("DISCORD_BOT_TOKEN not set in environment")
        if not server_id:
            raise ValueError("DISCORD_PRIVATE_SERVER_ID not set in environment")

        return cls(bot_token=token, private_server_id=server_id)


@dataclass
class TogetherConfig:
    """Together.ai API configuration"""

    api_key: str
    model: str
    embedding_model: str

    @classmethod
    def from_env(cls) -> "TogetherConfig":
        """Load Together.ai config from environment variables"""
        api_key = os.getenv("TOGETHER_API_KEY")

        if not api_key:
            raise ValueError("TOGETHER_API_KEY not set in environment")

        # Default models - can be overridden in .env
        model = os.getenv("TOGETHER_MODEL", "meta-llama/Llama-3.3-70B-Instruct-Turbo")
        embedding_model = os.getenv(
            "TOGETHER_EMBEDDING_MODEL", "togethercomputer/m2-bert-80M-8k-retrieval"
        )

        return cls(api_key=api_key, model=model, embedding_model=embedding_model)


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
    together: Optional[TogetherConfig]
    redis: RedisConfig
    perplexity: Optional[PerplexityConfig]
    openai: Optional[OpenAIConfig]

    @classmethod
    def from_env(cls) -> "AppConfig":
        """Load complete app config from environment variables"""
        # Make Together.ai optional since it's no longer used
        together_config = None
        if os.getenv("TOGETHER_API_KEY"):
            together_config = TogetherConfig.from_env()

        # Make Perplexity optional (Session 79C)
        perplexity_config = None
        if os.getenv("PERPLEXITY_API_KEY"):
            perplexity_config = PerplexityConfig.from_env()

        # Make OpenAI optional (Session 79D)
        openai_config = None
        if os.getenv("OPENAI_API_KEY"):
            openai_config = OpenAIConfig.from_env()

        return cls(
            env=os.getenv("ENV", "development"),
            log_level=os.getenv("LOG_LEVEL", "INFO"),
            supabase=SupabaseConfig.from_env(),
            discord=DiscordConfig.from_env(),
            together=together_config,
            redis=RedisConfig.from_env(),
            perplexity=perplexity_config,
            openai=openai_config,
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


def load_config(root_path: Optional[Path] = None) -> AppConfig:
    """
    Explicitly load configuration from .env file at application startup.

    This function should be called once at the beginning of each application entrypoint
    (e.g., run_bot.py, run_tge_analysis.py) before any other imports that depend on config.

    Args:
        root_path: Path to project root directory. If None, auto-detects using __file__.

    Returns:
        AppConfig: The loaded application configuration

    Raises:
        ValueError: If required environment variables are not set
        RuntimeError: If config is already loaded (prevents double-loading)
    """
    global _config

    if _config is not None:
        raise RuntimeError(
            "Configuration already loaded. load_config() should only be called once at startup."
        )

    # Auto-detect root path if not provided
    if root_path is None:
        # Find project root by looking for pyproject.toml or .env
        current = Path(__file__).resolve().parent
        while current != current.parent:
            if (current / "pyproject.toml").exists() or (current / ".env").exists():
                root_path = current
                break
            current = current.parent

        if root_path is None:
            # Fallback to parent.parent.parent if auto-detection fails
            root_path = Path(__file__).parent.parent.parent

    # Load .env file
    env_file = root_path / ".env"
    load_dotenv(env_file)

    # Create config singleton
    _config = AppConfig.from_env()
    return _config


def get_config() -> AppConfig:
    """
    Get application configuration singleton.

    Returns:
        AppConfig: The application configuration

    Raises:
        RuntimeError: If configuration has not been loaded via load_config()
        ValueError: If required environment variables are not set
    """
    global _config
    if _config is None:
        raise RuntimeError(
            "Configuration has not been loaded. Call load_config() at application startup first."
        )
    return _config


# Convenience functions for common configs
def get_supabase_config() -> SupabaseConfig:
    """Get Supabase configuration"""
    return get_config().supabase


def get_discord_config() -> DiscordConfig:
    """Get Discord configuration"""
    return get_config().discord


def get_together_config() -> TogetherConfig:
    """Get Together.ai configuration"""
    return get_config().together


def get_redis_config() -> RedisConfig:
    """Get Redis configuration"""
    return get_config().redis


def get_perplexity_config() -> Optional[PerplexityConfig]:
    """Get Perplexity API configuration (Session 79C)"""
    return get_config().perplexity


def get_openai_config() -> Optional[OpenAIConfig]:
    """Get OpenAI API configuration (Session 79D)"""
    return get_config().openai
