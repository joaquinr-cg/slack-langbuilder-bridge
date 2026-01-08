"""
Slack-Langflow Bridge Bot - Main Entry Point.

This module initializes and runs the bot that bridges Slack
conversations to Langflow agentic flows.

Usage:
    python -m src.main

Environment variables must be configured (see .env.example).
"""

import asyncio
import logging
import signal
import sys
from typing import Optional

from .config import get_settings, setup_logging
from .session_manager import SessionManager
from .langflow_client import LangflowClient
from .slack_handler import SlackHandler

logger = logging.getLogger(__name__)

# Global reference for cleanup
_handler: Optional[SlackHandler] = None
_session_manager: Optional[SessionManager] = None
_cleanup_task: Optional[asyncio.Task] = None


async def periodic_cleanup(
    session_manager: SessionManager, interval_hours: int, ttl_hours: int
) -> None:
    """
    Periodically cleanup old sessions.

    Args:
        session_manager: Session manager instance.
        interval_hours: How often to run cleanup (in hours).
        ttl_hours: Delete sessions older than this (in hours).
    """
    interval_seconds = interval_hours * 3600

    while True:
        try:
            await asyncio.sleep(interval_seconds)
            count = await session_manager.cleanup_old_sessions(ttl_hours)
            if count > 0:
                logger.info("Periodic cleanup: removed %d old sessions", count)
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error("Error during periodic cleanup: %s", e)


async def main() -> None:
    """Main entry point for the bot."""
    global _handler, _session_manager, _cleanup_task

    # Load settings
    try:
        settings = get_settings()
    except Exception as e:
        print(f"Error loading configuration: {e}", file=sys.stderr)
        print("Please ensure all required environment variables are set.", file=sys.stderr)
        sys.exit(1)

    # Setup logging
    setup_logging(settings.log_level)
    logger.info("Starting Slack-Langflow Bridge Bot...")

    # Ensure data directory exists
    settings.ensure_data_directory()

    # Initialize components
    _session_manager = SessionManager(settings.database_path)
    await _session_manager.initialize()

    langflow_client = LangflowClient(
        api_url=settings.langflow_api_url,
        flow_id=settings.langflow_flow_id,
        api_key=settings.langflow_api_key,
        timeout=settings.request_timeout,
    )

    _handler = SlackHandler(
        settings=settings,
        session_manager=_session_manager,
        langflow_client=langflow_client,
    )

    # Start periodic cleanup task
    _cleanup_task = asyncio.create_task(
        periodic_cleanup(
            _session_manager,
            interval_hours=1,  # Run cleanup every hour
            ttl_hours=settings.session_ttl_hours,
        )
    )

    # Log configuration (without sensitive values)
    logger.info("Configuration:")
    logger.info("  - Langflow URL: %s", settings.langflow_api_url)
    logger.info("  - Flow ID: %s", settings.langflow_flow_id)
    logger.info("  - Database: %s", settings.database_path)
    logger.info("  - Request timeout: %d seconds", settings.request_timeout)
    logger.info("  - Session TTL: %d hours", settings.session_ttl_hours)

    # Log session stats
    stats = await _session_manager.get_session_stats()
    logger.info("Session stats: %s", stats)

    try:
        # Start the Slack handler (blocks until shutdown)
        await _handler.start()
    except KeyboardInterrupt:
        logger.info("Received keyboard interrupt")
    except Exception as e:
        logger.exception("Fatal error: %s", e)
        raise
    finally:
        await shutdown()


async def shutdown() -> None:
    """Gracefully shutdown the bot."""
    global _handler, _cleanup_task

    logger.info("Shutting down...")

    # Cancel cleanup task
    if _cleanup_task:
        _cleanup_task.cancel()
        try:
            await _cleanup_task
        except asyncio.CancelledError:
            pass

    # Cleanup handler resources
    if _handler:
        await _handler.cleanup()

    logger.info("Shutdown complete")


def handle_signal(sig: signal.Signals) -> None:
    """Handle shutdown signals."""
    logger.info("Received signal %s", sig.name)
    # Get the running loop and schedule shutdown
    loop = asyncio.get_running_loop()
    loop.create_task(shutdown())
    loop.stop()


def run() -> None:
    """Entry point for running the bot."""
    # Handle signals for graceful shutdown
    if sys.platform != "win32":
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, lambda s=sig: handle_signal(s))

    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    run()
