"""
Configuration management for the Slack-Langflow Bridge Bot.

Required environment variables:
- SLACK_BOT_TOKEN: Bot token (xoxb-...)
- SLACK_APP_TOKEN: App token for Socket Mode (xapp-...)
- LANGFLOW_API_URL: Langflow base URL (e.g.: https://dev-langbuilder.cloudgeometry.com)
- LANGFLOW_FLOW_ID: Flow ID to execute
- LANGFLOW_API_KEY: Langflow API key

Optional environment variables:
- DATABASE_PATH: Path to SQLite file (default: ./data/sessions.db)
- REQUEST_TIMEOUT: Timeout for Langflow requests in seconds (default: 300)
- SESSION_TTL_HOURS: Cleanup sessions after X hours (default: 24)
- LOG_LEVEL: Logging level (default: INFO)
"""

import logging
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    # Slack Configuration
    slack_bot_token: str
    slack_app_token: str

    # Langflow Configuration
    langflow_api_url: str
    langflow_flow_id: str
    langflow_api_key: str

    # Application Configuration
    database_path: str = "./data/sessions.db"
    request_timeout: int = 300  # 5 minutes - agents can take long
    session_ttl_hours: int = 24  # Cleanup sessions after 24 hours

    # Logging
    log_level: str = "INFO"

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    @property
    def langflow_run_endpoint(self) -> str:
        """Returns the full Langflow run endpoint URL."""
        base = self.langflow_api_url.rstrip("/")
        return f"{base}/api/v1/run/{self.langflow_flow_id}"

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
