"""
General conversation agent.

Handles greetings, off-topic, and unclear queries.
Symmetric interface with market_agent and rag_agent.
"""
import re
from typing import AsyncGenerator
from services.llm import stream_completion, LLMError
from prompts.rag_response import GENERAL_SYSTEM


def _is_chinese(text: str) -> bool:
    return bool(re.search(r"[\u4e00-\u9fff]", text))


async def handle_general_query(
    query: str,
    history: list[dict] | None = None,
    query_complexity: str = "detailed",  # Accepted for interface symmetry, unused currently
) -> AsyncGenerator[dict, None]:
    """General conversation handler — same protocol as market/rag agents.
    Yields: {type: "status"|"text"|"sources", content: ...}"""
    cn = _is_chinese(query)

    yield {
        "type": "status",
        "content": "正在思考..." if cn else "Thinking...",
    }

    messages = []
    if history:
        messages.extend(history)
    messages.append({"role": "user", "content": query})

    try:
        async for chunk in stream_completion(messages=messages, system=GENERAL_SYSTEM, temperature=0.6):
            yield {"type": "text", "content": chunk}
    except LLMError as e:
        yield {"type": "text", "content": f"⚠️ AI service error: {e}"}

    yield {"type": "sources", "content": {"agent": "general"}}
