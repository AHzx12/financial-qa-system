"""
Claude API client — all LLM interactions go through here.

Design choices:
- API key read at call time (not import time) so dotenv works.
- Singleton client: one AsyncAnthropic instance per process, reuses httpx connection pool.
- Dedicated LLMError for clean upstream handling.
- Both chat_completion and stream_completion wrap exceptions into LLMError
  so upstream agents can catch a single type.
"""
import os
from typing import AsyncGenerator
from anthropic import AsyncAnthropic


class LLMError(Exception):
    """Raised when the LLM service is misconfigured or a call fails."""
    pass


# ---- Config ----

def _get_api_key() -> str:
    key = os.getenv("ANTHROPIC_API_KEY", "")
    if not key:
        raise LLMError(
            "ANTHROPIC_API_KEY not set. Copy .env.example → .env and fill it in."
        )
    return key


def _get_model() -> str:
    return os.getenv("LLM_MODEL", "claude-sonnet-4-20250514")


# ---- Singleton client ----
# One AsyncAnthropic per process. Reuses the internal httpx.AsyncClient
# connection pool across router + agent calls within the same request.

_client: AsyncAnthropic | None = None


def get_client() -> AsyncAnthropic:
    global _client
    if _client is None:
        _client = AsyncAnthropic(api_key=_get_api_key())
    return _client


def reset_client():
    """Reset the singleton (for testing or key rotation)."""
    global _client
    _client = None


# ---- Non-streaming ----

async def chat_completion(
    messages: list[dict],
    system: str = "",
    tools: list[dict] | None = None,
    max_tokens: int = 4096,
    temperature: float = 0.0,
):
    """Non-streaming completion (routing, short structured output).
    Default temperature=0.0 for deterministic classification."""
    client = get_client()
    kwargs = {
        "model": _get_model(),
        "max_tokens": max_tokens,
        "messages": messages,
        "temperature": temperature,
    }
    if system:
        kwargs["system"] = system
    if tools:
        kwargs["tools"] = tools

    try:
        return await client.messages.create(**kwargs)
    except LLMError:
        raise
    except Exception as e:
        raise LLMError(f"Claude API error: {e}")


# ---- Streaming ----

async def stream_completion(
    messages: list[dict],
    system: str = "",
    max_tokens: int = 4096,
    temperature: float = 1.0,
) -> AsyncGenerator[str, None]:
    """Streaming completion — yields text chunks for SSE forwarding.

    Temperature guide:
    - 0.2: Market analysis (strict adherence to data)
    - 0.3: RAG responses (slight wording variation, no fabrication)
    - 0.6: General conversation (natural dialogue style)
    - 1.0: Claude default
    """
    client = get_client()
    kwargs = {
        "model": _get_model(),
        "max_tokens": max_tokens,
        "messages": messages,
        "temperature": temperature,
    }
    if system:
        kwargs["system"] = system

    try:
        async with client.messages.stream(**kwargs) as stream:
            async for text in stream.text_stream:
                yield text
    except LLMError:
        raise
    except Exception as e:
        raise LLMError(f"Claude streaming error: {e}")
