"""
Configuration management for the Slack-Langflow Bridge Bot.

Required environment variables:
- SLACK_BOT_TOKEN: Bot token (xoxb-...)
- SLACK_APP_TOKEN: App token for Socket Mode (xapp-...)

Optional environment variables (for single-flow mode / initial setup):
- LANGFLOW_API_URL: Langflow base URL
- LANGFLOW_FLOW_ID: Flow ID to execute
- LANGFLOW_API_KEY: Langflow API key
- DEFAULT_FLOW_NAME: Name for the default flow (default: "default")

Application settings:
- DATABASE_PATH: Path to SQLite file (default: ./data/sessions.db)
- REQUEST_TIMEOUT: Timeout for Langflow requests in seconds (default: 300)
- SESSION_TTL_HOURS: Cleanup sessions after X hours (default: 24)
- ADMIN_USER_IDS: Comma-separated list of Slack user IDs who can manage flows
- LOG_LEVEL: Logging level (default: INFO)
"""

import logging
from functools import lru_cache
from pathlib import Path
from typing import Optional

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    # Slack Configuration
    slack_bot_token: str
    slack_app_token: str

    # Langflow Configuration (optional - for single-flow mode or initial setup)
    langflow_api_url: Optional[str] = None
    langflow_flow_id: Optional[str] = None
    langflow_api_key: Optional[str] = None
    default_flow_name: str = "default"

    # Application Configuration
    database_path: str = "./data/sessions.db"
    request_timeout: int = 300  # 5 minutes - agents can take long
    session_ttl_hours: int = 24  # Cleanup sessions after 24 hours

    # Admin Configuration (comma-separated Slack user IDs)
    admin_user_ids: str = ""

    # Logging
    log_level: str = "INFO"

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    @property
    def has_default_flow_config(self) -> bool:
        """Check if default flow configuration is provided via env vars."""
        return all([
            self.langflow_api_url,
            self.langflow_flow_id,
            self.langflow_api_key,
        ])

    @property
    def admin_users(self) -> set[str]:
        """Returns set of admin user IDs."""
        if not self.admin_user_ids:
            return set()
        return {uid.strip() for uid in self.admin_user_ids.split(",") if uid.strip()}

    def is_admin(self, user_id: str) -> bool:
        """Check if a user is an admin."""
        # If no admins configured, allow all users (for backward compatibility)
        if not self.admin_users:
            return True
        return user_id in self.admin_users

    def ensure_data_directory(self) -> None:
        """Ensures the data directory exists for the SQLite database."""
        db_path = Path(self.database_path)
        db_path.parent.mkdir(parents=True, exist_ok=True)


@lru_cache
def get_settings() -> Settings:
    """Returns cached settings instance."""
    return Settings()


def setup_logging(level: str = "INFO") -> None:
    """Configures structured logging for the application."""
    log_level = getattr(logging, level.upper(), logging.INFO)

    logging.basicConfig(
        level=log_level,
        format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # Reduce noise from third-party libraries
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("slack_bolt").setLevel(logging.INFO)
    logging.getLogger("slack_sdk").setLevel(logging.WARNING)
    logging.getLogger("aiosqlite").setLevel(logging.WARNING)
