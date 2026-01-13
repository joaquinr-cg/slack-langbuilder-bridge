"""
Slack Event Handler using slack_bolt with Socket Mode.

Handles:
- app_mention: When the bot is mentioned in a channel
- message.im: Direct messages to the bot
- message (in threads): Thread replies where the bot has participated

Admin commands (when mentioned):
- help: Show available commands
- flows: List all configured flows
- flows add <name> <url> <flow_id> <api_key> [description]: Add a flow
- flows remove <name>: Remove a flow
- flows default <name>: Set the default flow
- channel set <flow_name>: Set this channel's flow
- channel info: Show this channel's flow configuration
- channel reset: Remove channel-specific flow (use default)
"""

import asyncio
import logging
import re
import shlex
import time
from typing import Optional, Set

from slack_bolt.async_app import AsyncApp
from slack_bolt.adapter.socket_mode.async_handler import AsyncSocketModeHandler
from slack_sdk.web.async_client import AsyncWebClient

from .config import Settings
from .session_manager import SessionManager
from .flow_manager import FlowManager, FlowConfig
from .langflow_client import (
    LangflowClientManager,
    LangflowTimeoutError,
    LangflowAPIError,
    LangflowError,
)
from .response_parser import format_for_slack

logger = logging.getLogger(__name__)


class SlackHandler:
    """Handles Slack events and bridges to Langflow."""

    # Command patterns
    COMMAND_PATTERN = re.compile(r"^(help|flows|channel)\b", re.IGNORECASE)

    def __init__(
        self,
        settings: Settings,
        session_manager: SessionManager,
        flow_manager: FlowManager,
        client_manager: LangflowClientManager,
    ):
        """
        Initialize the Slack handler.

        Args:
            settings: Application settings.
            session_manager: Session manager for thread-to-session mapping.
            flow_manager: Flow manager for multi-flow support.
            client_manager: Client manager for Langflow connections.
        """
        self.settings = settings
        self.session_manager = session_manager
        self.flow_manager = flow_manager
        self.client_manager = client_manager

        # Track which threads the bot has participated in
        self._bot_threads: Set[str] = set()
        # Track messages currently being processed to avoid duplicates
        self._processing: Set[str] = set()
        # Track recently processed messages (message_key -> timestamp)
        self._processed_messages: dict[str, float] = {}
        # How long to remember processed messages (in seconds)
        self._dedup_window: int = 60

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

    def _is_command(self, text: str) -> bool:
        """Check if the message is an admin command."""
        return bool(self.COMMAND_PATTERN.match(text.strip()))

    def _cleanup_processed_messages(self, current_time: float) -> None:
        """Remove old entries from the processed messages dict."""
        cutoff = current_time - self._dedup_window
        expired = [k for k, v in self._processed_messages.items() if v < cutoff]
        for k in expired:
            del self._processed_messages[k]

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

        # Skip if recently processed (deduplication for Slack retries)
        current_time = time.time()
        if message_key in self._processed_messages:
            if current_time - self._processed_messages[message_key] < self._dedup_window:
                logger.debug("Skipping duplicate message %s", message_key)
                return

        # Cleanup old entries from processed messages dict
        self._cleanup_processed_messages(current_time)

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
                effective_thread_ts = thread_ts

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

            # Check if this is a command
            if is_mention and self._is_command(cleaned_text):
                await self._handle_command(client, channel, ts, user, cleaned_text)
                return

            logger.info(
                "Processing message | channel=%s | thread=%s | user=%s | text=%s",
                channel,
                thread_key,
                user,
                cleaned_text[:100],
            )

            # Get the flow for this channel
            flow_config = await self.flow_manager.get_channel_flow(channel)
            if not flow_config:
                await self._send_error_message(
                    client,
                    channel,
                    thread_ts or ts,
                    "No flow configured for this channel. "
                    "Ask an admin to configure one with: `@bot flows add ...`",
                )
                return

            # Get or create session
            effective_thread_ts = thread_ts or ts
            session_info = await self.session_manager.get_or_create_session(
                channel, effective_thread_ts, flow_config.name
            )

            # If existing session has a different flow, use that flow instead
            if session_info.flow_name and session_info.flow_name != flow_config.name:
                original_flow = await self.flow_manager.get_flow(session_info.flow_name)
                if original_flow:
                    flow_config = original_flow
                    logger.info(
                        "Using session's original flow %s instead of channel flow",
                        flow_config.name,
                    )

            # Get the client for this flow
            langflow_client = self.client_manager.get_client(flow_config)

            # Start typing indicator task
            typing_task = asyncio.create_task(
                self._maintain_typing_indicator(client, channel, effective_thread_ts)
            )

            try:
                # Send to Langflow
                response_text = await langflow_client.send_message(
                    cleaned_text, session_info.session_id
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
            # Mark as processed for deduplication
            self._processed_messages[message_key] = time.time()

    async def _handle_command(
        self,
        client: AsyncWebClient,
        channel: str,
        ts: str,
        user: str,
        text: str,
    ) -> None:
        """
        Handle admin commands.

        Args:
            client: Slack web client.
            channel: Channel ID.
            ts: Message timestamp.
            user: User ID who sent the command.
            text: Command text.
        """
        try:
            parts = shlex.split(text)
        except ValueError:
            parts = text.split()

        if not parts:
            return

        command = parts[0].lower()
        args = parts[1:] if len(parts) > 1 else []

        logger.info("Handling command | user=%s | command=%s | args=%s", user, command, args)

        try:
            if command == "help":
                await self._cmd_help(client, channel, ts)
            elif command == "flows":
                await self._cmd_flows(client, channel, ts, user, args)
            elif command == "channel":
                await self._cmd_channel(client, channel, ts, user, args)
            else:
                await self._send_message(
                    client, channel, ts, f"Unknown command: `{command}`. Try `help`."
                )
        except Exception as e:
            logger.exception("Error handling command: %s", command)
            await self._send_error_message(
                client, channel, ts, f"Error executing command: {e}"
            )

    async def _cmd_help(
        self, client: AsyncWebClient, channel: str, ts: str
    ) -> None:
        """Show help message."""
        help_text = """*Available Commands:*

*Flow Management:*
- `flows` - List all configured flows
- `flows add <name> <url> <flow_id> <api_key> [description]` - Add a new flow
- `flows remove <name>` - Remove a flow
- `flows default <name>` - Set the default flow
- `flows info <name>` - Show flow details

*Channel Configuration:*
- `channel info` - Show this channel's flow configuration
- `channel set <flow_name>` - Set this channel to use a specific flow
- `channel reset` - Remove channel-specific flow (use default)

*General:*
- `help` - Show this message

Any other message will be sent to the configured Langflow agent."""
        await self._send_message(client, channel, ts, help_text)

    async def _cmd_flows(
        self,
        client: AsyncWebClient,
        channel: str,
        ts: str,
        user: str,
        args: list[str],
    ) -> None:
        """Handle flows commands."""
        if not args:
            # List all flows
            flows = await self.flow_manager.list_flows()
            if not flows:
                await self._send_message(
                    client, channel, ts,
                    "No flows configured. Add one with: `flows add <name> <url> <flow_id> <api_key>`"
                )
                return

            lines = ["*Configured Flows:*"]
            for flow in flows:
                default_marker = " (default)" if flow.is_default else ""
                desc = f" - {flow.description}" if flow.description else ""
                lines.append(f"- `{flow.name}`{default_marker}{desc}")
            await self._send_message(client, channel, ts, "\n".join(lines))
            return

        subcommand = args[0].lower()

        if subcommand == "add":
            # Check admin permission
            if not self.settings.is_admin(user):
                await self._send_message(
                    client, channel, ts, ":no_entry: You don't have permission to add flows."
                )
                return

            if len(args) < 5:
                await self._send_message(
                    client, channel, ts,
                    "Usage: `flows add <name> <url> <flow_id> <api_key> [description]`"
                )
                return

            name, url, flow_id, api_key = args[1:5]
            description = " ".join(args[5:]) if len(args) > 5 else None

            success = await self.flow_manager.add_flow(
                name=name,
                langflow_url=url,
                flow_id=flow_id,
                api_key=api_key,
                description=description,
            )

            if success:
                await self._send_message(
                    client, channel, ts, f":white_check_mark: Flow `{name}` added successfully."
                )
            else:
                await self._send_message(
                    client, channel, ts, f":warning: Flow `{name}` already exists."
                )

        elif subcommand == "remove":
            if not self.settings.is_admin(user):
                await self._send_message(
                    client, channel, ts, ":no_entry: You don't have permission to remove flows."
                )
                return

            if len(args) < 2:
                await self._send_message(
                    client, channel, ts, "Usage: `flows remove <name>`"
                )
                return

            name = args[1]
            success = await self.flow_manager.remove_flow(name)
            self.client_manager.invalidate(name)

            if success:
                await self._send_message(
                    client, channel, ts, f":white_check_mark: Flow `{name}` removed."
                )
            else:
                await self._send_message(
                    client, channel, ts, f":warning: Flow `{name}` not found."
                )

        elif subcommand == "default":
            if not self.settings.is_admin(user):
                await self._send_message(
                    client, channel, ts, ":no_entry: You don't have permission to set default flow."
                )
                return

            if len(args) < 2:
                await self._send_message(
                    client, channel, ts, "Usage: `flows default <name>`"
                )
                return

            name = args[1]
            success = await self.flow_manager.set_default_flow(name)

            if success:
                await self._send_message(
                    client, channel, ts, f":white_check_mark: Default flow set to `{name}`."
                )
            else:
                await self._send_message(
                    client, channel, ts, f":warning: Flow `{name}` not found."
                )

        elif subcommand == "info":
            if len(args) < 2:
                await self._send_message(
                    client, channel, ts, "Usage: `flows info <name>`"
                )
                return

            name = args[1]
            flow = await self.flow_manager.get_flow(name)

            if flow:
                info_lines = [
                    f"*Flow: {flow.name}*",
                    f"- URL: `{flow.langflow_url}`",
                    f"- Flow ID: `{flow.flow_id}`",
                    f"- API Key: `****{flow.api_key[-4:] if len(flow.api_key) > 4 else '****'}`",
                    f"- Default: {'Yes' if flow.is_default else 'No'}",
                ]
                if flow.description:
                    info_lines.append(f"- Description: {flow.description}")
                await self._send_message(client, channel, ts, "\n".join(info_lines))
            else:
                await self._send_message(
                    client, channel, ts, f":warning: Flow `{name}` not found."
                )

        else:
            await self._send_message(
                client, channel, ts,
                f"Unknown subcommand: `{subcommand}`. Try `flows`, `flows add`, `flows remove`, `flows default`, or `flows info`."
            )

    async def _cmd_channel(
        self,
        client: AsyncWebClient,
        channel: str,
        ts: str,
        user: str,
        args: list[str],
    ) -> None:
        """Handle channel commands."""
        if not args:
            args = ["info"]

        subcommand = args[0].lower()

        if subcommand == "info":
            flow_name = await self.flow_manager.get_channel_flow_name(channel)
            flow = await self.flow_manager.get_channel_flow(channel)

            if flow_name:
                await self._send_message(
                    client, channel, ts,
                    f"This channel is configured to use flow: `{flow_name}`"
                )
            elif flow:
                await self._send_message(
                    client, channel, ts,
                    f"This channel uses the default flow: `{flow.name}`"
                )
            else:
                await self._send_message(
                    client, channel, ts,
                    "No flow configured for this channel and no default flow set."
                )

        elif subcommand == "set":
            if not self.settings.is_admin(user):
                await self._send_message(
                    client, channel, ts, ":no_entry: You don't have permission to configure channels."
                )
                return

            if len(args) < 2:
                await self._send_message(
                    client, channel, ts, "Usage: `channel set <flow_name>`"
                )
                return

            flow_name = args[1]
            success = await self.flow_manager.set_channel_flow(channel, flow_name)

            if success:
                await self._send_message(
                    client, channel, ts,
                    f":white_check_mark: This channel now uses flow: `{flow_name}`"
                )
            else:
                await self._send_message(
                    client, channel, ts,
                    f":warning: Flow `{flow_name}` not found. Use `flows` to see available flows."
                )

        elif subcommand == "reset":
            if not self.settings.is_admin(user):
                await self._send_message(
                    client, channel, ts, ":no_entry: You don't have permission to configure channels."
                )
                return

            success = await self.flow_manager.remove_channel_flow(channel)
            default_flow = await self.flow_manager.get_default_flow()

            if success:
                if default_flow:
                    await self._send_message(
                        client, channel, ts,
                        f":white_check_mark: Channel reset. Now using default flow: `{default_flow.name}`"
                    )
                else:
                    await self._send_message(
                        client, channel, ts,
                        ":white_check_mark: Channel reset. No default flow configured."
                    )
            else:
                await self._send_message(
                    client, channel, ts,
                    "This channel was already using the default flow."
                )

        else:
            await self._send_message(
                client, channel, ts,
                f"Unknown subcommand: `{subcommand}`. Try `channel info`, `channel set`, or `channel reset`."
            )

    async def _maintain_typing_indicator(
        self,
        client: AsyncWebClient,
        channel: str,
        thread_ts: str,
    ) -> None:
        """
        Maintain the typing indicator while processing.

        Args:
            client: Slack web client.
            channel: Channel ID.
            thread_ts: Thread timestamp.
        """
        try:
            while True:
                await asyncio.sleep(2)
        except asyncio.CancelledError:
            pass

    async def _send_message(
        self,
        client: AsyncWebClient,
        channel: str,
        thread_ts: str,
        text: str,
    ) -> None:
        """Send a simple message."""
        await client.chat_postMessage(
            channel=channel,
            thread_ts=thread_ts,
            text=text,
        )

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
        await self.client_manager.close_all()
