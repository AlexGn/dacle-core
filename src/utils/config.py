"""
Configuration management for DACLE
Loads environment variables and provides typed access
"""

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

# Load .env file from project root
project_root = Path(__file__).parent.parent.parent
env_file = project_root / ".env"
load_dotenv(env_file)


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
class AppConfig:
    """Main application configuration"""

    env: str
    log_level: str
    supabase: SupabaseConfig
    discord: DiscordConfig
    together: TogetherConfig
    redis: RedisConfig

    @classmethod
    def from_env(cls) -> "AppConfig":
        """Load complete app config from environment variables"""
        return cls(
            env=os.getenv("ENV", "development"),
            log_level=os.getenv("LOG_LEVEL", "INFO"),
            supabase=SupabaseConfig.from_env(),
            discord=DiscordConfig.from_env(),
            together=TogetherConfig.from_env(),
            redis=RedisConfig.from_env(),
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


def get_config() -> AppConfig:
    """
    Get application configuration singleton

    Returns:
        AppConfig: The application configuration

    Raises:
        ValueError: If required environment variables are not set
    """
    global _config
    if _config is None:
        _config = AppConfig.from_env()
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
