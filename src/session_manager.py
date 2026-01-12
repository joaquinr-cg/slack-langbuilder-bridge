"""
Session Manager for mapping Slack threads to Langflow sessions.

Uses SQLite to persist the mapping between Slack thread identifiers
and Langflow session IDs, enabling conversation continuity.

Database Schema:
    CREATE TABLE sessions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        thread_key TEXT UNIQUE NOT NULL,  -- format: "{channel_id}:{thread_ts}"
        session_id TEXT NOT NULL,          -- UUID for Langflow
        flow_name TEXT,                    -- Which flow this session uses
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
"""

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional

import aiosqlite

logger = logging.getLogger(__name__)


@dataclass
class SessionInfo:
    """Information about a session."""
    session_id: str
    flow_name: Optional[str] = None
    is_new: bool = False


class SessionManager:
    """Manages the mapping between Slack threads and Langflow sessions."""

    def __init__(self, database_path: str):
        """
        Initialize the session manager.

        Args:
            database_path: Path to the SQLite database file.
        """
        self.database_path = database_path
        self._initialized = False

    async def initialize(self) -> None:
        """Initialize the database and create tables if they don't exist."""
        if self._initialized:
            return

        async with aiosqlite.connect(self.database_path) as db:
            # Check if we need to migrate (add flow_name column)
            cursor = await db.execute("PRAGMA table_info(sessions)")
            columns = [row[1] for row in await cursor.fetchall()]

            if "sessions" not in columns:
                # Table doesn't exist, create it with flow_name
                await db.execute("""
                    CREATE TABLE IF NOT EXISTS sessions (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        thread_key TEXT UNIQUE NOT NULL,
                        session_id TEXT NOT NULL,
                        flow_name TEXT,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                """)
            elif "flow_name" not in columns:
                # Table exists but missing flow_name, add it
                await db.execute(
                    "ALTER TABLE sessions ADD COLUMN flow_name TEXT"
                )
                logger.info("Migrated sessions table: added flow_name column")

            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_thread_key ON sessions(thread_key)"
            )
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_updated_at ON sessions(updated_at)"
            )
            await db.commit()

        self._initialized = True
        logger.info("Session database initialized at %s", self.database_path)

    def _make_thread_key(self, channel_id: str, thread_ts: str) -> str:
        """
        Create a unique thread key from channel and thread timestamp.

        Args:
            channel_id: Slack channel ID.
            thread_ts: Slack thread timestamp.

        Returns:
            A unique key in format "channel_id:thread_ts".
        """
        return f"{channel_id}:{thread_ts}"

    async def get_session(
        self, channel_id: str, thread_ts: str
    ) -> Optional[SessionInfo]:
        """
        Get existing session info for a thread.

        Args:
            channel_id: Slack channel ID.
            thread_ts: Slack thread timestamp.

        Returns:
            SessionInfo if found, None otherwise.
        """
        thread_key = self._make_thread_key(channel_id, thread_ts)

        async with aiosqlite.connect(self.database_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT session_id, flow_name FROM sessions WHERE thread_key = ?",
                (thread_key,),
            )
            row = await cursor.fetchone()

            if row:
                # Update the updated_at timestamp
                await db.execute(
                    "UPDATE sessions SET updated_at = ? WHERE thread_key = ?",
                    (datetime.utcnow(), thread_key),
                )
                await db.commit()

                session_info = SessionInfo(
                    session_id=row["session_id"],
                    flow_name=row["flow_name"],
                    is_new=False,
                )
                logger.debug(
                    "Found existing session %s (flow=%s) for thread %s",
                    session_info.session_id,
                    session_info.flow_name,
                    thread_key,
                )
                return session_info

        return None

    async def create_session(
        self, channel_id: str, thread_ts: str, flow_name: Optional[str] = None
    ) -> SessionInfo:
        """
        Create a new session for a thread.

        Args:
            channel_id: Slack channel ID.
            thread_ts: Slack thread timestamp.
            flow_name: Name of the flow this session will use.

        Returns:
            Newly created SessionInfo.
        """
        thread_key = self._make_thread_key(channel_id, thread_ts)
        session_id = str(uuid.uuid4())

        async with aiosqlite.connect(self.database_path) as db:
            await db.execute(
                """
                INSERT INTO sessions (thread_key, session_id, flow_name, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (thread_key, session_id, flow_name, datetime.utcnow(), datetime.utcnow()),
            )
            await db.commit()

        logger.info(
            "Created new session %s (flow=%s) for thread %s",
            session_id,
            flow_name,
            thread_key,
        )
        return SessionInfo(session_id=session_id, flow_name=flow_name, is_new=True)

    async def get_or_create_session(
        self, channel_id: str, thread_ts: str, flow_name: Optional[str] = None
    ) -> SessionInfo:
        """
        Get existing session or create a new one for a thread.

        This is the main method to use for getting a session.
        It ensures conversation continuity by returning the same
        session for messages in the same thread.

        Note: If an existing session is found, the flow_name parameter
        is ignored - the session continues with its original flow.

        Args:
            channel_id: Slack channel ID.
            thread_ts: Slack thread timestamp.
            flow_name: Name of the flow for new sessions.

        Returns:
            SessionInfo (existing or newly created).
        """
        session_info = await self.get_session(channel_id, thread_ts)
        if session_info:
            return session_info
        return await self.create_session(channel_id, thread_ts, flow_name)

    async def cleanup_old_sessions(self, hours: int) -> int:
        """
        Delete sessions older than the specified number of hours.

        Args:
            hours: Delete sessions not updated in this many hours.

        Returns:
            Number of sessions deleted.
        """
        cutoff = datetime.utcnow() - timedelta(hours=hours)

        async with aiosqlite.connect(self.database_path) as db:
            cursor = await db.execute(
                "SELECT COUNT(*) FROM sessions WHERE updated_at < ?",
                (cutoff,),
            )
            row = await cursor.fetchone()
            count = row[0] if row else 0

            if count > 0:
                await db.execute(
                    "DELETE FROM sessions WHERE updated_at < ?", (cutoff,)
                )
                await db.commit()
                logger.info("Cleaned up %d old sessions", count)

        return count

    async def get_session_stats(self) -> dict:
        """
        Get statistics about stored sessions.

        Returns:
            Dictionary with session statistics.
        """
        async with aiosqlite.connect(self.database_path) as db:
            cursor = await db.execute("SELECT COUNT(*) FROM sessions")
            total_row = await cursor.fetchone()
            total = total_row[0] if total_row else 0

            one_hour_ago = datetime.utcnow() - timedelta(hours=1)
            cursor = await db.execute(
                "SELECT COUNT(*) FROM sessions WHERE updated_at > ?",
                (one_hour_ago,),
            )
            active_row = await cursor.fetchone()
            active = active_row[0] if active_row else 0

            cursor = await db.execute(
                "SELECT MIN(created_at), MAX(updated_at) FROM sessions"
            )
            dates_row = await cursor.fetchone()
            oldest = dates_row[0] if dates_row else None
            newest = dates_row[1] if dates_row else None

            # Count sessions per flow
            cursor = await db.execute(
                """
                SELECT flow_name, COUNT(*) as count
                FROM sessions
                GROUP BY flow_name
                """
            )
            flow_counts = {
                row[0] or "unknown": row[1]
                for row in await cursor.fetchall()
            }

        return {
            "total_sessions": total,
            "active_last_hour": active,
            "oldest_session": oldest,
            "newest_activity": newest,
            "sessions_per_flow": flow_counts,
        }
