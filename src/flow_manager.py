"""
Flow Manager for multi-flow support.

Manages multiple Langflow configurations and channel-to-flow mappings.
Allows runtime configuration via Slack commands.

Database Schema:
    CREATE TABLE flows (
        name TEXT PRIMARY KEY,
        langflow_url TEXT NOT NULL,
        flow_id TEXT NOT NULL,
        api_key TEXT NOT NULL,
        description TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        is_default BOOLEAN DEFAULT FALSE
    );

    CREATE TABLE channel_flows (
        channel_id TEXT PRIMARY KEY,
        flow_name TEXT NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (flow_name) REFERENCES flows(name)
    );
"""

import logging
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

import aiosqlite

logger = logging.getLogger(__name__)


def clean_slack_formatting(text: str) -> str:
    """
    Remove Slack auto-formatting from text.

    Slack converts URLs like https://example.com to <https://example.com>
    This function strips those angle brackets.

    Args:
        text: Text that may contain Slack formatting.

    Returns:
        Cleaned text without Slack formatting.
    """
    # Remove <url> formatting (e.g., <https://example.com> -> https://example.com)
    # Also handles <url|label> format (e.g., <https://example.com|example.com>)
    cleaned = re.sub(r'<(https?://[^|>]+)(?:\|[^>]*)?>',  r'\1', text)
    return cleaned.strip()


@dataclass
class FlowConfig:
    """Configuration for a Langflow flow."""
    name: str
    langflow_url: str
    flow_id: str
    api_key: str
    description: Optional[str] = None
    is_default: bool = False

    @property
    def endpoint(self) -> str:
        """Returns the full run endpoint URL."""
        base = self.langflow_url.rstrip("/")
        return f"{base}/api/v1/run/{self.flow_id}"


class FlowManager:
    """Manages multiple Langflow flow configurations."""

    def __init__(self, database_path: str):
        """
        Initialize the flow manager.

        Args:
            database_path: Path to the SQLite database file.
        """
        self.database_path = database_path
        self._initialized = False

    async def initialize(self) -> None:
        """Initialize the database tables."""
        if self._initialized:
            return

        async with aiosqlite.connect(self.database_path) as db:
            # Create flows table
            await db.execute("""
                CREATE TABLE IF NOT EXISTS flows (
                    name TEXT PRIMARY KEY,
                    langflow_url TEXT NOT NULL,
                    flow_id TEXT NOT NULL,
                    api_key TEXT NOT NULL,
                    description TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    is_default BOOLEAN DEFAULT FALSE
                )
            """)

            # Create channel_flows table
            await db.execute("""
                CREATE TABLE IF NOT EXISTS channel_flows (
                    channel_id TEXT PRIMARY KEY,
                    flow_name TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (flow_name) REFERENCES flows(name)
                )
            """)

            await db.commit()

        self._initialized = True
        logger.info("Flow manager database initialized")

    async def add_flow(
        self,
        name: str,
        langflow_url: str,
        flow_id: str,
        api_key: str,
        description: Optional[str] = None,
        is_default: bool = False,
    ) -> bool:
        """
        Add a new flow configuration.

        Args:
            name: Unique name for the flow.
            langflow_url: Base URL of the Langflow server.
            flow_id: ID of the flow to execute.
            api_key: API key for authentication.
            description: Optional description.
            is_default: Whether this is the default flow.

        Returns:
            True if added successfully, False if name already exists.
        """
        # Clean Slack formatting from URL
        langflow_url = clean_slack_formatting(langflow_url)

        async with aiosqlite.connect(self.database_path) as db:
            try:
                # If setting as default, unset any existing default
                if is_default:
                    await db.execute(
                        "UPDATE flows SET is_default = FALSE WHERE is_default = TRUE"
                    )

                await db.execute(
                    """
                    INSERT INTO flows (name, langflow_url, flow_id, api_key, description, is_default, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (name, langflow_url, flow_id, api_key, description, is_default, datetime.utcnow()),
                )
                await db.commit()
                logger.info("Added flow: %s", name)
                return True

            except aiosqlite.IntegrityError:
                logger.warning("Flow already exists: %s", name)
                return False

    async def update_flow(
        self,
        name: str,
        langflow_url: Optional[str] = None,
        flow_id: Optional[str] = None,
        api_key: Optional[str] = None,
        description: Optional[str] = None,
    ) -> bool:
        """
        Update an existing flow configuration.

        Args:
            name: Name of the flow to update.
            langflow_url: New base URL (optional).
            flow_id: New flow ID (optional).
            api_key: New API key (optional).
            description: New description (optional).

        Returns:
            True if updated, False if flow not found.
        """
        updates = []
        params = []

        if langflow_url is not None:
            updates.append("langflow_url = ?")
            params.append(langflow_url)
        if flow_id is not None:
            updates.append("flow_id = ?")
            params.append(flow_id)
        if api_key is not None:
            updates.append("api_key = ?")
            params.append(api_key)
        if description is not None:
            updates.append("description = ?")
            params.append(description)

        if not updates:
            return False

        params.append(name)

        async with aiosqlite.connect(self.database_path) as db:
            cursor = await db.execute(
                f"UPDATE flows SET {', '.join(updates)} WHERE name = ?",
                params,
            )
            await db.commit()

            if cursor.rowcount > 0:
                logger.info("Updated flow: %s", name)
                return True
            return False

    async def remove_flow(self, name: str) -> bool:
        """
        Remove a flow configuration.

        Args:
            name: Name of the flow to remove.

        Returns:
            True if removed, False if not found.
        """
        async with aiosqlite.connect(self.database_path) as db:
            # First remove any channel mappings
            await db.execute(
                "DELETE FROM channel_flows WHERE flow_name = ?", (name,)
            )

            cursor = await db.execute(
                "DELETE FROM flows WHERE name = ?", (name,)
            )
            await db.commit()

            if cursor.rowcount > 0:
                logger.info("Removed flow: %s", name)
                return True
            return False

    async def get_flow(self, name: str) -> Optional[FlowConfig]:
        """
        Get a flow configuration by name.

        Args:
            name: Name of the flow.

        Returns:
            FlowConfig if found, None otherwise.
        """
        async with aiosqlite.connect(self.database_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM flows WHERE name = ?", (name,)
            )
            row = await cursor.fetchone()

            if row:
                return FlowConfig(
                    name=row["name"],
                    langflow_url=row["langflow_url"],
                    flow_id=row["flow_id"],
                    api_key=row["api_key"],
                    description=row["description"],
                    is_default=bool(row["is_default"]),
                )
            return None

    async def get_default_flow(self) -> Optional[FlowConfig]:
        """
        Get the default flow configuration.

        Returns:
            Default FlowConfig if set, None otherwise.
        """
        async with aiosqlite.connect(self.database_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM flows WHERE is_default = TRUE LIMIT 1"
            )
            row = await cursor.fetchone()

            if row:
                return FlowConfig(
                    name=row["name"],
                    langflow_url=row["langflow_url"],
                    flow_id=row["flow_id"],
                    api_key=row["api_key"],
                    description=row["description"],
                    is_default=True,
                )
            return None

    async def set_default_flow(self, name: str) -> bool:
        """
        Set a flow as the default.

        Args:
            name: Name of the flow to set as default.

        Returns:
            True if set, False if flow not found.
        """
        async with aiosqlite.connect(self.database_path) as db:
            # Check if flow exists
            cursor = await db.execute(
                "SELECT name FROM flows WHERE name = ?", (name,)
            )
            if not await cursor.fetchone():
                return False

            # Unset current default and set new one
            await db.execute("UPDATE flows SET is_default = FALSE")
            await db.execute(
                "UPDATE flows SET is_default = TRUE WHERE name = ?", (name,)
            )
            await db.commit()
            logger.info("Set default flow: %s", name)
            return True

    async def list_flows(self) -> list[FlowConfig]:
        """
        List all configured flows.

        Returns:
            List of FlowConfig objects.
        """
        async with aiosqlite.connect(self.database_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute("SELECT * FROM flows ORDER BY name")
            rows = await cursor.fetchall()

            return [
                FlowConfig(
                    name=row["name"],
                    langflow_url=row["langflow_url"],
                    flow_id=row["flow_id"],
                    api_key=row["api_key"],
                    description=row["description"],
                    is_default=bool(row["is_default"]),
                )
                for row in rows
            ]

    async def set_channel_flow(self, channel_id: str, flow_name: str) -> bool:
        """
        Map a channel to a specific flow.

        Args:
            channel_id: Slack channel ID.
            flow_name: Name of the flow to use.

        Returns:
            True if set, False if flow doesn't exist.
        """
        # Verify flow exists
        flow = await self.get_flow(flow_name)
        if not flow:
            return False

        async with aiosqlite.connect(self.database_path) as db:
            await db.execute(
                """
                INSERT OR REPLACE INTO channel_flows (channel_id, flow_name, created_at)
                VALUES (?, ?, ?)
                """,
                (channel_id, flow_name, datetime.utcnow()),
            )
            await db.commit()
            logger.info("Set channel %s to flow %s", channel_id, flow_name)
            return True

    async def remove_channel_flow(self, channel_id: str) -> bool:
        """
        Remove a channel's flow mapping (will use default).

        Args:
            channel_id: Slack channel ID.

        Returns:
            True if removed, False if not found.
        """
        async with aiosqlite.connect(self.database_path) as db:
            cursor = await db.execute(
                "DELETE FROM channel_flows WHERE channel_id = ?", (channel_id,)
            )
            await db.commit()
            return cursor.rowcount > 0

    async def get_channel_flow(self, channel_id: str) -> Optional[FlowConfig]:
        """
        Get the flow configuration for a channel.

        Falls back to default flow if no specific mapping exists.

        Args:
            channel_id: Slack channel ID.

        Returns:
            FlowConfig for the channel, or None if no flow configured.
        """
        async with aiosqlite.connect(self.database_path) as db:
            # Check for specific channel mapping
            cursor = await db.execute(
                "SELECT flow_name FROM channel_flows WHERE channel_id = ?",
                (channel_id,),
            )
            row = await cursor.fetchone()

            if row:
                return await self.get_flow(row[0])

        # Fall back to default flow
        return await self.get_default_flow()

    async def get_channel_flow_name(self, channel_id: str) -> Optional[str]:
        """
        Get just the flow name for a channel.

        Args:
            channel_id: Slack channel ID.

        Returns:
            Flow name if mapped, None otherwise (doesn't check default).
        """
        async with aiosqlite.connect(self.database_path) as db:
            cursor = await db.execute(
                "SELECT flow_name FROM channel_flows WHERE channel_id = ?",
                (channel_id,),
            )
            row = await cursor.fetchone()
            return row[0] if row else None
