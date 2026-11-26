"""The one place the server talks to an LLM: OpenRouter's chat API (issue #37).

The key comes from OPENROUTER_API_KEY and the model from OPENROUTER_MODEL (see
config/settings.py). This runs on the server only; the key never reaches the
browser, the repo, a log line or an error message.
"""

import json
import logging
from http.client import HTTPException
from urllib.request import Request, urlopen

from django.conf import settings

logger = logging.getLogger(__name__)

API_URL = "https://openrouter.ai/api/v1/chat/completions"
TIMEOUT_SECONDS = 30


class OpenRouterError(Exception):
    """OpenRouter couldn't give us an answer: no key, no network, or a bad reply."""


def chat(messages, *, model=None):
    """Send chat messages and return the model's reply as text.

    Asks for a JSON object, which most models honour; callers still validate.
    Raises OpenRouterError for every failure, so callers have one thing to catch.
    """
    body = {
        "model": model or settings.OPENROUTER_MODEL,
        "messages": messages,
        "response_format": {"type": "json_object"},
        "temperature": 0,
    }
    try:
        return _post(body)["content"]
    except KeyError as error:
        raise OpenRouterError("OpenRouter's answer had no message in it") from error


def chat_message(messages, *, tools=None, model=None):
    """Like chat(), for an agent: returns the model's whole reply message
    ({"role", "content", and "tool_calls" when it wants a tool run}).

    `tools` are MCP-style definitions ({"name", "description", "inputSchema"});
    they're sent in OpenRouter's function-calling format. Without tools, the
    model has to answer, and the reply is asked to be a JSON object as in chat().
    """
    body = {"model": model or settings.OPENROUTER_MODEL, "messages": messages, "temperature": 0}
    if tools:
        body["tools"] = [
            {
                "type": "function",
                "function": {"name": tool["name"], "description": tool["description"],
                             "parameters": tool["inputSchema"]},
            }
            for tool in tools
        ]
    else:
        body["response_format"] = {"type": "json_object"}
    return _post(body)


def _post(body):
    """POST the body to OpenRouter and return the reply message (a dict)."""
    api_key = settings.OPENROUTER_API_KEY
    if not api_key:
        raise OpenRouterError("OPENROUTER_API_KEY is not set")
    if any(character.isspace() or not character.isprintable() for character in api_key):
        # A header can't carry a line break, and Python's error for one quotes
        # the whole header, key included, escaped so the redaction below would
        # miss it. Refuse it before there's a header to quote (#159).
        raise OpenRouterError("OPENROUTER_API_KEY has a space or line break in it")
    request = Request(
        API_URL,
        data=json.dumps(body).encode(),
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            data = json.load(response)
        message = data["choices"][0]["message"]
        if not isinstance(message, dict):
            raise TypeError("message is not an object")
        return message
    except (OSError, ValueError, HTTPException) as error:
        # OSError covers timeouts, refused connections and HTTP errors (HTTPError
        # is one); ValueError covers an answer that isn't JSON.
        # Show the reason (certificate trouble, DNS, a 401...) but never the key.
        reason = str(getattr(error, "reason", None) or error).replace(api_key, "[key]")
        raise OpenRouterError(f"OpenRouter request failed ({error.__class__.__name__}: {reason})") from error
    except (KeyError, IndexError, TypeError) as error:
        raise OpenRouterError("OpenRouter's answer had no message in it") from error
