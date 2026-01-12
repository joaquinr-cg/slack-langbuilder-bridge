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
from .flow_manager import FlowManager
from .langflow_client import LangflowClientManager
from .slack_handler import SlackHandler

logger = logging.getLogger(__name__)

# Global references for cleanup
_handler: Optional[SlackHandler] = None
_session_manager: Optional[SessionManager] = None
_flow_manager: Optional[FlowManager] = None
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


async def setup_default_flow(settings, flow_manager: FlowManager) -> None:
    """
    Set up default flow from environment variables if provided.

    This provides backward compatibility with the single-flow configuration.

    Args:
        settings: Application settings.
        flow_manager: Flow manager instance.
    """
    if not settings.has_default_flow_config:
        logger.info("No default flow configured via environment variables")
        return

    # Check if flow already exists
    existing = await flow_manager.get_flow(settings.default_flow_name)
    if existing:
        logger.info("Default flow '%s' already exists in database", settings.default_flow_name)
        return

    # Create the default flow from env vars
    await flow_manager.add_flow(
        name=settings.default_flow_name,
        langflow_url=settings.langflow_api_url,
        flow_id=settings.langflow_flow_id,
        api_key=settings.langflow_api_key,
        description="Default flow configured via environment variables",
        is_default=True,
    )
    logger.info("Created default flow '%s' from environment variables", settings.default_flow_name)


async def main() -> None:
    """Main entry point for the bot."""
    global _handler, _session_manager, _flow_manager, _cleanup_task

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

    # Initialize session manager
    _session_manager = SessionManager(settings.database_path)
    await _session_manager.initialize()

    # Initialize flow manager
    _flow_manager = FlowManager(settings.database_path)
    await _flow_manager.initialize()

    # Set up default flow from env vars if provided
    await setup_default_flow(settings, _flow_manager)

    # Initialize client manager
    client_manager = LangflowClientManager(
        timeout=settings.request_timeout,
    )

    # Initialize Slack handler
    _handler = SlackHandler(
        settings=settings,
        session_manager=_session_manager,
        flow_manager=_flow_manager,
        client_manager=client_manager,
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
    logger.info("  - Database: %s", settings.database_path)
    logger.info("  - Request timeout: %d seconds", settings.request_timeout)
    logger.info("  - Session TTL: %d hours", settings.session_ttl_hours)
    if settings.admin_users:
        logger.info("  - Admin users: %d configured", len(settings.admin_users))
    else:
        logger.info("  - Admin users: all users (no restriction)")

    # Log flow stats
    flows = await _flow_manager.list_flows()
    logger.info("Flows configured: %d", len(flows))
    for flow in flows:
        default_marker = " (default)" if flow.is_default else ""
        logger.info("  - %s%s: %s", flow.name, default_marker, flow.langflow_url)

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
