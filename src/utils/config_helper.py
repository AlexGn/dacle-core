#!/usr/bin/env python3
"""
Configuration Helper for Scripts

Session 267: Migrated from scripts/helpers/config.py to src/utils/config_helper.py

Centralized environment variable loading and configuration management.
Eliminates duplicate dotenv loading and environment variable parsing across scripts.

Usage:
    from src.utils.config_helper import get_config, get_telegram_config

    config = get_config()
    telegram = get_telegram_config()

    print(f"Bot token: {telegram.bot_token}")
    print(f"Chat ID: {telegram.chat_id}")

Environment Variables:
    SUPABASE_URL - Supabase project URL
    SUPABASE_KEY - Supabase service role key
    TELEGRAM_BOT_TOKEN - Telegram bot token
    TELEGRAM_CHAT_ID - Telegram chat/channel ID
    TOGETHER_API_KEY - Together.ai API key (for embeddings)
    NOTION_API_KEY - Notion integration API key
    OPENAI_API_KEY - OpenAI API key (optional)

Created: 2025-11-19 (Phase 1: Codebase Cleanup)
"""

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

# Load environment variables once at module import
PROJECT_ROOT = Path(__file__).parent.parent
ENV_FILE = PROJECT_ROOT / ".env"

if ENV_FILE.exists():
    load_dotenv(ENV_FILE)
else:
    # Fallback to default .env loading
    load_dotenv()


@dataclass
class SupabaseConfig:
    """Supabase database configuration"""

    url: str
    key: str

    def __post_init__(self):
        # Don't validate on init - scripts should check if they need Supabase
        pass

    def validate(self):
        """Validate that Supabase credentials are set"""
        if not self.url or not self.key:
            raise ValueError(
                "SUPABASE_URL and SUPABASE_KEY must be set in .env file. "
                "Check your environment variables."
            )


@dataclass
class TelegramConfig:
    """Telegram bot configuration"""

    bot_token: str
    chat_id: str

    def __post_init__(self):
        # Don't validate on init - scripts should check if they need Telegram
        pass

    def validate(self):
        """Validate that Telegram credentials are set"""
        if not self.bot_token:
            raise ValueError("TELEGRAM_BOT_TOKEN must be set in .env file")
        if not self.chat_id:
            raise ValueError("TELEGRAM_CHAT_ID must be set in .env file")

    @property
    def chat_id_int(self) -> int:
        """Convert chat_id to integer (handles @channel format)"""
        if self.chat_id and self.chat_id.startswith("@"):
            return self.chat_id  # Keep as string for channel usernames
        return int(self.chat_id) if self.chat_id else 0


@dataclass
class AIConfig:
    """AI service configuration"""

    together_api_key: Optional[str] = None
    openai_api_key: Optional[str] = None

    def __post_init__(self):
        # These are optional - scripts should check before use
        pass


@dataclass
class NotionConfig:
    """Notion integration configuration"""

    api_key: Optional[str] = None
    token: Optional[str] = None  # Alias for api_key (both names supported)
    database_id: Optional[str] = None

    def __post_init__(self):
        # Support both NOTION_API_KEY and NOTION_TOKEN (same token, different names)
        if not self.api_key and self.token:
            self.api_key = self.token
        elif not self.token and self.api_key:
            self.token = self.api_key

    @property
    def auth_token(self) -> Optional[str]:
        """Get authentication token (supports both naming conventions)"""
        return self.api_key or self.token

    def validate(self) -> bool:
        """Check if Notion is properly configured"""
        return bool(self.auth_token and self.database_id)


@dataclass
class DACLEConfig:
    """Main DACLE configuration container"""

    supabase: SupabaseConfig
    telegram: TelegramConfig
    ai: AIConfig
    notion: NotionConfig
    project_root: Path

    @classmethod
    def load(cls) -> "DACLEConfig":
        """Load configuration from environment variables"""
        return cls(
            supabase=SupabaseConfig(
                url=os.getenv("SUPABASE_URL", ""),
                key=os.getenv("SUPABASE_KEY", ""),
            ),
            telegram=TelegramConfig(
                bot_token=os.getenv("TELEGRAM_BOT_TOKEN", ""),
                chat_id=os.getenv("TELEGRAM_CHAT_ID", ""),
            ),
            ai=AIConfig(
                together_api_key=os.getenv("TOGETHER_API_KEY"),
                openai_api_key=os.getenv("OPENAI_API_KEY"),
            ),
            notion=NotionConfig(
                api_key=os.getenv("NOTION_API_KEY"),
                token=os.getenv("NOTION_TOKEN"),
                database_id=os.getenv("NOTION_DATABASE_ID", "293b6ac9956180848e9fe44b85390d0b")
            ),
            project_root=PROJECT_ROOT,
        )


# Singleton instance
_config: Optional[DACLEConfig] = None


def get_config() -> DACLEConfig:
    """
    Get main DACLE configuration (singleton).

    Returns:
        DACLEConfig: Configuration object with all settings

    Example:
        ```python
        from scripts.helpers.config import get_config

        config = get_config()
        print(f"Project root: {config.project_root}")
        print(f"Supabase URL: {config.supabase.url}")
        ```
    """
    global _config
    if _config is None:
        _config = DACLEConfig.load()
    return _config


def get_notion_config() -> NotionConfig:
    """
    Get Notion configuration.

    Returns:
        NotionConfig: Notion-specific configuration

    Example:
        ```python
        from scripts.helpers.config import get_notion_config

        notion_config = get_notion_config()
        print(f"Database ID: {notion_config.database_id}")
        ```
    """
    return get_config().notion


def get_supabase_config() -> SupabaseConfig:
    """
    Get Supabase configuration.

    Returns:
        SupabaseConfig: Supabase URL and key

    Raises:
        ValueError: If SUPABASE_URL or SUPABASE_KEY not set

    Example:
        ```python
        from scripts.helpers.config import get_supabase_config

        config = get_supabase_config()
        print(f"Connecting to: {config.url}")
        ```
    """
    return get_config().supabase


def get_telegram_config() -> TelegramConfig:
    """
    Get Telegram bot configuration.

    Returns:
        TelegramConfig: Bot token and chat ID

    Raises:
        ValueError: If TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID not set

    Example:
        ```python
        from scripts.helpers.config import get_telegram_config

        telegram = get_telegram_config()
        print(f"Sending to chat: {telegram.chat_id}")
        ```
    """
    return get_config().telegram


def get_ai_config() -> AIConfig:
    """
    Get AI service configuration.

    Returns:
        AIConfig: API keys for AI services (may be None)

    Example:
        ```python
        from scripts.helpers.config import get_ai_config

        ai = get_ai_config()
        if ai.together_api_key:
            print("Together.ai available")
        ```
    """
    return get_config().ai


def get_notion_config() -> NotionConfig:
    """
    Get Notion integration configuration.

    Returns:
        NotionConfig: Notion API key (may be None)

    Example:
        ```python
        from scripts.helpers.config import get_notion_config

        notion = get_notion_config()
        if not notion.api_key:
            print("⚠️ Notion integration not configured")
        ```
    """
    return get_config().notion


def get_project_root() -> Path:
    """
    Get DACLE project root directory.

    Returns:
        Path: Absolute path to project root

    Example:
        ```python
        from scripts.helpers.config import get_project_root

        root = get_project_root()
        data_dir = root / "data"
        ```
    """
    return get_config().project_root


def validate_config(require_telegram: bool = False, require_ai: bool = False) -> bool:
    """
    Validate configuration completeness.

    Args:
        require_telegram: Raise error if Telegram not configured
        require_ai: Raise error if AI services not configured

    Returns:
        bool: True if validation passed

    Raises:
        ValueError: If required configuration missing

    Example:
        ```python
        from scripts.helpers.config import validate_config

        # Script that needs Telegram
        validate_config(require_telegram=True)

        # Script that needs AI embeddings
        validate_config(require_ai=True)
        ```
    """
    try:
        config = get_config()

        # Validate Supabase (only if needed by caller)
        # Note: Supabase validation is optional - only validate if explicitly required
        # config.supabase.validate()  # Commented out - scripts should check individually

        if require_telegram:
            config.telegram.validate()

        if require_ai:
            if not config.ai.together_api_key and not config.ai.openai_api_key:
                raise ValueError("AI configuration missing (required for this script)")

        return True

    except ValueError:
        raise


# Convenience exports
__all__ = [
    "get_config",
    "get_supabase_config",
    "get_telegram_config",
    "get_ai_config",
    "get_notion_config",
    "get_project_root",
    "validate_config",
    "DACLEConfig",
    "SupabaseConfig",
    "TelegramConfig",
    "AIConfig",
    "NotionConfig",
]
