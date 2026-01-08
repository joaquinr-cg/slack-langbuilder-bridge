"""
Langflow Response Parser.

The Langflow API returns complex nested JSON structures.
This module extracts the agent's final message from various
possible locations in the response.

Response structure varies but typically looks like:
{
    "outputs": [
        {
            "outputs": [
                {
                    "results": { "message": { "text": "..." } },
                    "artifacts": { "message": "..." },
                    "messages": [ { "message": "..." } ]
                }
            ]
        }
    ]
}
"""

import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)


def extract_message(response: dict[str, Any]) -> str:
    """
    Extract the agent message from a Langflow response.

    Tries multiple paths because the structure can vary depending
    on the flow configuration and Langflow version.

    Priority order:
    1. artifacts.message (most common for agent flows)
    2. messages[0].message (array of messages)
    3. results.message.text (nested message object)
    4. results.message.data.text (alternative nested structure)
    5. results.message (if it's a direct string)

    Args:
        response: The JSON response from Langflow API.

    Returns:
        Extracted message text, or empty string if not found.
    """
    try:
        outputs = response.get("outputs", [])
        if not outputs:
            logger.warning("No 'outputs' array in response")
            return ""

        inner_outputs = outputs[0].get("outputs", [])
        if not inner_outputs:
            logger.warning("No inner 'outputs' array in response")
            return ""

        result = inner_outputs[0]

        # Path 1: artifacts.message (most common for agents)
        message = _try_artifacts_message(result)
        if message:
            logger.debug("Extracted message from artifacts.message")
            return message

        # Path 2: messages array
        message = _try_messages_array(result)
        if message:
            logger.debug("Extracted message from messages array")
            return message

        # Path 3: results.message.text
        message = _try_results_message_text(result)
        if message:
            logger.debug("Extracted message from results.message.text")
            return message

        # Path 4: results.message.data.text
        message = _try_results_message_data_text(result)
        if message:
            logger.debug("Extracted message from results.message.data.text")
            return message

        # Path 5: results.message as direct string
        message = _try_results_message_string(result)
        if message:
            logger.debug("Extracted message from results.message (string)")
            return message

        logger.warning(
            "Could not extract message from response. Keys in result: %s",
            list(result.keys()) if isinstance(result, dict) else "not a dict",
        )
        return ""

    except Exception as e:
        logger.error("Error parsing Langflow response: %s", e, exc_info=True)
        return ""


def _try_artifacts_message(result: dict[str, Any]) -> Optional[str]:
    """Try to extract message from artifacts.message."""
    if "artifacts" not in result:
        return None

    artifacts = result["artifacts"]
    if not isinstance(artifacts, dict):
        return None

    message = artifacts.get("message")
    if isinstance(message, str) and message.strip():
        return message.strip()

    return None


def _try_messages_array(result: dict[str, Any]) -> Optional[str]:
    """Try to extract message from messages array."""
    if "messages" not in result:
        return None

    messages = result["messages"]
    if not isinstance(messages, list) or not messages:
        return None

    first_message = messages[0]
    if not isinstance(first_message, dict):
        return None

    message = first_message.get("message")
    if isinstance(message, str) and message.strip():
        return message.strip()

    return None


def _try_results_message_text(result: dict[str, Any]) -> Optional[str]:
    """Try to extract message from results.message.text."""
    if "results" not in result:
        return None

    results = result["results"]
    if not isinstance(results, dict):
        return None

    message_obj = results.get("message")
    if not isinstance(message_obj, dict):
        return None

    text = message_obj.get("text")
    if isinstance(text, str) and text.strip():
        return text.strip()

    return None


def _try_results_message_data_text(result: dict[str, Any]) -> Optional[str]:
    """Try to extract message from results.message.data.text."""
    if "results" not in result:
        return None

    results = result["results"]
    if not isinstance(results, dict):
        return None

    message_obj = results.get("message")
    if not isinstance(message_obj, dict):
        return None

    data = message_obj.get("data")
    if not isinstance(data, dict):
        return None

    text = data.get("text")
    if isinstance(text, str) and text.strip():
        return text.strip()

    return None


def _try_results_message_string(result: dict[str, Any]) -> Optional[str]:
    """Try to extract message from results.message as direct string."""
    if "results" not in result:
        return None

    results = result["results"]
    if not isinstance(results, dict):
        return None

    message = results.get("message")
    if isinstance(message, str) and message.strip():
        return message.strip()

    return None


def format_for_slack(message: str, max_length: int = 3900) -> list[str]:
    """
    Format a message for Slack, splitting if necessary.

    Slack has a 4000 character limit per message. This function
    splits long messages into chunks, preferring to split at
    paragraph boundaries.

    Args:
        message: The message to format.
        max_length: Maximum length per chunk (default 3900 for margin).

    Returns:
        List of message chunks.
    """
    if not message:
        return []

    if len(message) <= max_length:
        return [message]

    chunks = []
    remaining = message

    while remaining:
        if len(remaining) <= max_length:
            chunks.append(remaining)
            break

        # Try to split at paragraph boundary
        split_point = remaining.rfind("\n\n", 0, max_length)

        # If no paragraph boundary, try newline
        if split_point == -1:
            split_point = remaining.rfind("\n", 0, max_length)

        # If no newline, try space
        if split_point == -1:
            split_point = remaining.rfind(" ", 0, max_length)

        # Last resort: hard split
        if split_point == -1:
            split_point = max_length

        chunks.append(remaining[:split_point].rstrip())
        remaining = remaining[split_point:].lstrip()

    logger.info("Split message into %d chunks", len(chunks))
    return chunks
