"""
HTTP Client for the Langflow API.

Handles communication with Langflow's run endpoint, including:
- Long timeouts for agent processing
- Retry logic with exponential backoff for 5xx errors
- Proper error handling and logging
- Multi-flow support via LangflowClientManager
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Optional, TYPE_CHECKING

import httpx

from .response_parser import extract_message

if TYPE_CHECKING:
    from .flow_manager import FlowConfig

logger = logging.getLogger(__name__)


class LangflowError(Exception):
    """Base exception for Langflow client errors."""

    pass


class LangflowTimeoutError(LangflowError):
    """Raised when Langflow request times out."""

    pass


class LangflowAPIError(LangflowError):
    """Raised when Langflow returns an error response."""

    def __init__(self, message: str, status_code: int, response_body: str = ""):
        super().__init__(message)
        self.status_code = status_code
        self.response_body = response_body


class LangflowClient:
    """Async HTTP client for Langflow API."""

    def __init__(
        self,
        api_url: str,
        flow_id: str,
        api_key: str,
        timeout: int = 300,
        max_retries: int = 2,
    ):
        """
        Initialize the Langflow client.

        Args:
            api_url: Base URL of the Langflow server.
            flow_id: ID of the flow to execute.
            api_key: API key for authentication.
            timeout: Request timeout in seconds (default 300 = 5 minutes).
            max_retries: Maximum number of retries for 5xx errors.
        """
        self.api_url = api_url.rstrip("/")
        self.flow_id = flow_id
        self.api_key = api_key
        self.timeout = timeout
        self.max_retries = max_retries
        self._client: Optional[httpx.AsyncClient] = None

    @classmethod
    def from_flow_config(
        cls, config: FlowConfig, timeout: int = 300, max_retries: int = 2
    ) -> LangflowClient:
        """
        Create a client from a FlowConfig object.

        Args:
            config: FlowConfig containing connection details.
            timeout: Request timeout in seconds.
            max_retries: Maximum number of retries.

        Returns:
            Configured LangflowClient instance.
        """
        return cls(
            api_url=config.langflow_url,
            flow_id=config.flow_id,
            api_key=config.api_key,
            timeout=timeout,
            max_retries=max_retries,
        )

    @property
    def endpoint(self) -> str:
        """Returns the full run endpoint URL."""
        return f"{self.api_url}/api/v1/run/{self.flow_id}"

    async def _get_client(self) -> httpx.AsyncClient:
        """Get or create the HTTP client."""
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(self.timeout, connect=30.0),
                headers={
                    "Content-Type": "application/json",
                    "x-api-key": self.api_key,
                },
            )
        return self._client

    async def close(self) -> None:
        """Close the HTTP client."""
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

    async def run_flow(
        self, message: str, session_id: str
    ) -> dict[str, Any]:
        """
        Execute a Langflow flow with the given message.

        Args:
            message: The user's message to send to the flow.
            session_id: Session ID for conversation continuity.

        Returns:
            The full JSON response from Langflow.

        Raises:
            LangflowTimeoutError: If the request times out.
            LangflowAPIError: If Langflow returns an error.
        """
        payload = {
            "input_type": "chat",
            "output_type": "chat",
            "input_value": message,
            "session_id": session_id,
        }

        logger.info(
            "Sending message to Langflow | session_id=%s | message_length=%d",
            session_id,
            len(message),
        )

        client = await self._get_client()
        last_error: Optional[Exception] = None

        for attempt in range(self.max_retries + 1):
            try:
                response = await client.post(self.endpoint, json=payload)

                if response.status_code == 200:
                    data = response.json()
                    logger.info(
                        "Received response from Langflow | session_id=%s",
                        session_id,
                    )
                    return data

                # Handle error responses
                error_body = response.text
                logger.error(
                    "Langflow error | status=%d | body=%s",
                    response.status_code,
                    error_body[:500],
                )

                # Don't retry 4xx errors (client errors)
                if 400 <= response.status_code < 500:
                    raise LangflowAPIError(
                        f"Langflow returned {response.status_code}",
                        response.status_code,
                        error_body,
                    )

                # Retry 5xx errors (server errors)
                last_error = LangflowAPIError(
                    f"Langflow returned {response.status_code}",
                    response.status_code,
                    error_body,
                )

            except httpx.TimeoutException as e:
                logger.warning(
                    "Langflow timeout | attempt=%d/%d | session_id=%s",
                    attempt + 1,
                    self.max_retries + 1,
                    session_id,
                )
                last_error = LangflowTimeoutError(
                    f"Request timed out after {self.timeout} seconds"
                )

            except httpx.RequestError as e:
                logger.error(
                    "Langflow request error | attempt=%d/%d | error=%s",
                    attempt + 1,
                    self.max_retries + 1,
                    str(e),
                )
                last_error = LangflowError(f"Request failed: {e}")

            # Exponential backoff before retry
            if attempt < self.max_retries:
                wait_time = 2 ** attempt
                logger.info("Retrying in %d seconds...", wait_time)
                await asyncio.sleep(wait_time)

        # All retries exhausted
        raise last_error or LangflowError("Unknown error occurred")

    async def send_message(self, message: str, session_id: str) -> str:
        """
        Send a message to Langflow and extract the response text.

        This is a convenience method that combines run_flow() with
        response parsing.

        Args:
            message: The user's message to send to the flow.
            session_id: Session ID for conversation continuity.

        Returns:
            The extracted message text from the response.

        Raises:
            LangflowTimeoutError: If the request times out.
            LangflowAPIError: If Langflow returns an error.
        """
        response = await self.run_flow(message, session_id)
        return extract_message(response)


class LangflowClientManager:
    """
    Manages multiple Langflow clients for different flows.

    Caches clients to avoid creating new connections for each request.
    """

    def __init__(self, timeout: int = 300, max_retries: int = 2):
        """
        Initialize the client manager.

        Args:
            timeout: Default timeout for all clients.
            max_retries: Default max retries for all clients.
        """
        self.timeout = timeout
        self.max_retries = max_retries
        self._clients: dict[str, LangflowClient] = {}

    def get_client(self, config: FlowConfig) -> LangflowClient:
        """
        Get or create a client for a flow configuration.

        Args:
            config: FlowConfig for the desired flow.

        Returns:
            LangflowClient instance.
        """
        # Use flow name as cache key
        if config.name not in self._clients:
            self._clients[config.name] = LangflowClient.from_flow_config(
                config, self.timeout, self.max_retries
            )
            logger.debug("Created new client for flow: %s", config.name)
        return self._clients[config.name]

    def invalidate(self, flow_name: str) -> None:
        """
        Invalidate a cached client (e.g., after flow config update).

        Args:
            flow_name: Name of the flow to invalidate.
        """
        if flow_name in self._clients:
            # Don't await close here - let it be garbage collected
            del self._clients[flow_name]
            logger.debug("Invalidated client cache for flow: %s", flow_name)

    async def close_all(self) -> None:
        """Close all cached clients."""
        for name, client in self._clients.items():
            await client.close()
            logger.debug("Closed client for flow: %s", name)
        self._clients.clear()
