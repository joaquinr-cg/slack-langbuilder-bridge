"""
Slack Event Handler using slack_bolt with Socket Mode.

Handles:
- app_mention: When the bot is mentioned in a channel
- message.im: Direct messages to the bot
- message (in threads): Thread replies where the bot has participated

Key responsibilities:
- Extract message text and thread information
- Manage session mapping via SessionManager
- Call Langflow via LangflowClient
- Send responses back to Slack in the correct thread
"""

import asyncio
import logging
import re
from typing import Optional, Set

from slack_bolt.async_app import AsyncApp
from slack_bolt.adapter.socket_mode.async_handler import AsyncSocketModeHandler
from slack_sdk.web.async_client import AsyncWebClient

from .config import Settings
from .session_manager import SessionManager
from .langflow_client import (
    LangflowClient,
    LangflowTimeoutError,
    LangflowAPIError,
    LangflowError,
)
from .response_parser import format_for_slack

logger = logging.getLogger(__name__)


class SlackHandler:
    """Handles Slack events and bridges to Langflow."""

    def __init__(
        self,
        settings: Settings,
        session_manager: SessionManager,
        langflow_client: LangflowClient,
    ):
        """
        Initialize the Slack handler.

        Args:
            settings: Application settings.
            session_manager: Session manager for thread-to-session mapping.
            langflow_client: Client for Langflow API.
        """
        self.settings = settings
        self.session_manager = session_manager
        self.langflow_client = langflow_client

        # Track which threads the bot has participated in
        self._bot_threads: Set[str] = set()
        # Track messages currently being processed to avoid duplicates
        self._processing: Set[str] = set()

        # Initialize Slack app
        self.app = AsyncApp(token=settings.slack_bot_token)
        self._bot_user_id: Optional[str] = None

        # Register event handlers
        self._register_handlers()

    def _register_handlers(self) -> None:
        """Register Slack event handlers."""

        @self.app.event("app_mention")
        async def handle_mention(event: dict, client: AsyncWebClient) -> None:
            await self._handle_message(event, client, is_mention=True)

        @self.app.event("message")
        async def handle_message(event: dict, client: AsyncWebClient) -> None:
            # Filter out bot messages and subtypes we don't want
            if event.get("bot_id") or event.get("subtype"):
                return
            await self._handle_message(event, client, is_mention=False)

    async def _get_bot_user_id(self, client: AsyncWebClient) -> str:
        """Get and cache the bot's user ID."""
        if self._bot_user_id is None:
            response = await client.auth_test()
            self._bot_user_id = response["user_id"]
            logger.info("Bot user ID: %s", self._bot_user_id)
        return self._bot_user_id

    def _clean_message_text(self, text: str, bot_user_id: str) -> str:
        """
        Remove bot mention from message text.

        Args:
            text: Original message text.
            bot_user_id: The bot's user ID.

        Returns:
            Cleaned message text without the bot mention.
        """
        # Remove <@BOTID> mention pattern
        pattern = f"<@{bot_user_id}>"
        cleaned = re.sub(pattern, "", text).strip()
        return cleaned

    def _make_message_key(self, channel: str, ts: str) -> str:
        """Create a unique key for a message to track processing state."""
        return f"{channel}:{ts}"

    async def _handle_message(
        self,
        event: dict,
        client: AsyncWebClient,
        is_mention: bool,
    ) -> None:
        """
        Handle an incoming Slack message.

        Args:
            event: Slack event data.
            client: Slack web client.
            is_mention: Whether this is an app_mention event.
        """
        channel = event.get("channel", "")
        ts = event.get("ts", "")
        thread_ts = event.get("thread_ts")
        text = event.get("text", "")
        user = event.get("user", "")
        channel_type = event.get("channel_type", "")

        # Create a unique key for this message
        message_key = self._make_message_key(channel, ts)

        # Skip if already processing this message
        if message_key in self._processing:
            logger.debug("Already processing message %s", message_key)
            return

        # Get bot user ID
        bot_user_id = await self._get_bot_user_id(client)

        # Skip bot's own messages
        if user == bot_user_id:
            return

        # Determine if we should process this message
        should_process = False
        thread_key = None

        if is_mention:
            # Always process mentions
            should_process = True
            # Use thread_ts if in a thread, otherwise use ts (this message starts a thread)
            effective_thread_ts = thread_ts or ts
            thread_key = self._make_message_key(channel, effective_thread_ts)

        elif channel_type == "im":
            # Always process DMs
            should_process = True
            effective_thread_ts = thread_ts or ts
            thread_key = self._make_message_key(channel, effective_thread_ts)

        elif thread_ts:
            # This is a thread reply - only process if bot participated
            thread_key = self._make_message_key(channel, thread_ts)
            if thread_key in self._bot_threads:
                should_process = True

        if not should_process:
            return

        if not text.strip():
            logger.debug("Ignoring empty message")
            return

        # Mark as processing
        self._processing.add(message_key)

        try:
            # Clean the message text (remove bot mention)
            cleaned_text = self._clean_message_text(text, bot_user_id)
            if not cleaned_text:
                logger.debug("Message empty after cleaning")
                return

            logger.info(
                "Processing message | channel=%s | thread=%s | user=%s | text=%s",
                channel,
                thread_key,
                user,
                cleaned_text[:100],
            )

            # Get or create session
            effective_thread_ts = thread_ts or ts
            session_id = await self.session_manager.get_or_create_session(
                channel, effective_thread_ts
            )

            # Start typing indicator task
            typing_task = asyncio.create_task(
                self._maintain_typing_indicator(client, channel, effective_thread_ts)
            )

            try:
                # Send to Langflow
                response_text = await self.langflow_client.send_message(
                    cleaned_text, session_id
                )

                # Mark thread as bot-participated
                self._bot_threads.add(thread_key)

                # Send response
                await self._send_response(
                    client, channel, effective_thread_ts, response_text
                )

            finally:
                # Stop typing indicator
                typing_task.cancel()
                try:
                    await typing_task
                except asyncio.CancelledError:
                    pass

        except LangflowTimeoutError:
            logger.error("Langflow timeout for message %s", message_key)
            await self._send_error_message(
                client,
                channel,
                thread_ts or ts,
                "The agent is taking longer than expected. Please try again.",
            )

        except LangflowAPIError as e:
            logger.error(
                "Langflow API error for message %s: %s", message_key, str(e)
            )
            await self._send_error_message(
                client,
                channel,
                thread_ts or ts,
                "There was an error processing your message. Please try again.",
            )

        except LangflowError as e:
            logger.error(
                "Langflow error for message %s: %s", message_key, str(e)
            )
            await self._send_error_message(
                client,
                channel,
                thread_ts or ts,
                "There was an error communicating with the agent. Please try again.",
            )

        except Exception as e:
            logger.exception("Unexpected error processing message %s", message_key)
            await self._send_error_message(
                client,
                channel,
                thread_ts or ts,
                "An unexpected error occurred. Please try again.",
            )

        finally:
            # Remove from processing set
            self._processing.discard(message_key)

    async def _maintain_typing_indicator(
        self,
        client: AsyncWebClient,
        channel: str,
        thread_ts: str,
    ) -> None:
        """
        Maintain the typing indicator while processing.

        Slack's typing indicator only lasts ~3 seconds, so we need
        to periodically refresh it.

        Args:
            client: Slack web client.
            channel: Channel ID.
            thread_ts: Thread timestamp.
        """
        try:
            while True:
                # Note: chat.postMessage with type="typing" isn't available
                # The typing indicator is shown automatically when the bot
                # is about to respond, but for long operations we can't
                # maintain it. Instead, we could send a "processing" message
                # and delete it later, but that adds noise.
                # For now, we just wait - users understand AI can take time.
                await asyncio.sleep(2)
        except asyncio.CancelledError:
            pass

    async def _send_response(
        self,
        client: AsyncWebClient,
        channel: str,
        thread_ts: str,
        text: str,
    ) -> None:
        """
        Send a response to Slack, splitting if necessary.

        Args:
            client: Slack web client.
            channel: Channel ID.
            thread_ts: Thread timestamp.
            text: Response text.
        """
        if not text:
            await self._send_error_message(
                client,
                channel,
                thread_ts,
                "The agent didn't generate a response. Please rephrase your question.",
            )
            return

        # Split long messages
        chunks = format_for_slack(text)

        for i, chunk in enumerate(chunks):
            try:
                await client.chat_postMessage(
                    channel=channel,
                    thread_ts=thread_ts,
                    text=chunk,
                )
                logger.debug(
                    "Sent response chunk %d/%d to channel %s",
                    i + 1,
                    len(chunks),
                    channel,
                )
            except Exception as e:
                logger.error("Failed to send message to Slack: %s", e)
                raise

    async def _send_error_message(
        self,
        client: AsyncWebClient,
        channel: str,
        thread_ts: str,
        message: str,
    ) -> None:
        """
        Send an error message to Slack.

        Args:
            client: Slack web client.
            channel: Channel ID.
            thread_ts: Thread timestamp.
            message: Error message.
        """
        try:
            await client.chat_postMessage(
                channel=channel,
                thread_ts=thread_ts,
                text=f":warning: {message}",
            )
        except Exception as e:
            logger.error("Failed to send error message to Slack: %s", e)

    async def start(self) -> None:
        """Start the Socket Mode handler."""
        handler = AsyncSocketModeHandler(self.app, self.settings.slack_app_token)
        logger.info("Starting Slack Socket Mode handler...")
        await handler.start_async()

    async def cleanup(self) -> None:
        """Cleanup resources."""
        await self.langflow_client.close()
