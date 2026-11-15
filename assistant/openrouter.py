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
    api_key = settings.OPENROUTER_API_KEY
    if not api_key:
        raise OpenRouterError("OPENROUTER_API_KEY is not set")

    body = json.dumps({
        "model": model or settings.OPENROUTER_MODEL,
        "messages": messages,
        "response_format": {"type": "json_object"},
        "temperature": 0,
    }).encode()
    request = Request(
        API_URL,
        data=body,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            data = json.load(response)
        return data["choices"][0]["message"]["content"]
    except (OSError, ValueError, HTTPException) as error:
        # OSError covers timeouts, refused connections and HTTP errors (HTTPError
        # is one); ValueError covers an answer that isn't JSON.
        raise OpenRouterError(f"OpenRouter request failed ({error.__class__.__name__})") from error
    except (KeyError, IndexError, TypeError) as error:
        raise OpenRouterError("OpenRouter's answer had no message in it") from error
