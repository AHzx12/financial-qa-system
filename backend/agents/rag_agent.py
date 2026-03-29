"""
RAG Agent — all upgrades integrated.

1. Hybrid search: extracts keywords for where_document $contains
2. Confidence-aware: selects system prompt tier based on max_relevance
3. Status events: yields progress updates between steps
4. Metadata pre-filter: category/entity narrowing
5. Reranker: cross-encoder for detailed queries (skipped for simple)
"""
import re
import asyncio
import logging
from typing import AsyncGenerator
from services.vector_store import search, get_doc_count
from services.llm import stream_completion, LLMError
from services.market_data import get_stock_data

logger = logging.getLogger("rag_agent")
from prompts.rag_response import (
    get_rag_system_prompt, build_rag_prompt,
    CONFIDENCE_HIGH, CONFIDENCE_MEDIUM,
)

_DEFINITION_PATTERNS = re.compile(
    r"(什么是|是什么|定义|解释一下|什么意思|"
    r"what\s+is|define|meaning\s+of|explain\s+\w+$)",
    re.IGNORECASE,
)

# Financial keywords worth extracting for hybrid search
_KEYWORD_PATTERNS = re.compile(
    r"(市盈率|市值|营收|净利润|毛利率|负债|资产|股息|分红|估值|现金流|"
    r"PE|PB|ROE|ROA|EPS|EBIT|WACC|DCF|ETF|IPO|"
    r"revenue|earnings|profit|margin|dividend|valuation|"
    r"balance.?sheet|income.?statement|cash.?flow)",
    re.IGNORECASE,
)


def _is_chinese(text: str) -> bool:
    return bool(re.search(r"[\u4e00-\u9fff]", text))


def _estimate_n_results(query: str, complexity: str = "detailed") -> int:
    if complexity == "simple" or _DEFINITION_PATTERNS.search(query):
        return 2
    return 5


def _extract_keywords(query: str) -> list[str]:
    """Extract financial keywords for hybrid search ($contains filter)."""
    matches = _KEYWORD_PATTERNS.findall(query)
    # Deduplicate and limit
    seen = set()
    keywords = []
    for m in matches:
        m_lower = m.lower()
        if m_lower not in seen:
            seen.add(m_lower)
            keywords.append(m)
    return keywords[:3]  # Max 3 keywords to avoid over-filtering


def _build_where_filter(
    query: str, ticker: str = "", query_complexity: str = "detailed",
) -> dict | None:
    """Build ChromaDB metadata filter."""
    conditions = []
    if ticker:
        conditions.append({"entity": ticker})
    if _DEFINITION_PATTERNS.search(query):
        conditions.append({"category": "concept"})
    if not conditions:
        return None
    if len(conditions) == 1:
        return conditions[0]
    return {"$and": conditions}


def _build_messages(user_content: str, history: list[dict] | None = None) -> list[dict]:
    messages = []
    if history:
        messages.extend(history)
    messages.append({"role": "user", "content": user_content})
    return messages


async def handle_knowledge_query(
    query: str,
    history: list[dict] | None = None,
    query_complexity: str = "detailed",
    ticker: str = "",
) -> AsyncGenerator[dict, None]:
    """
    Handle a financial knowledge query using RAG with all upgrades.
    Yields: {type: "status"|"text"|"sources", content: ...}
    """
    cn = _is_chinese(query)
    doc_count = get_doc_count()

    # --- No knowledge base ---
    if doc_count == 0:
        yield {"type": "text", "content": "📚 知识库尚未初始化。使用模型通用知识回答：\n\n"}
        messages = _build_messages(query, history)
        try:
            async for chunk in stream_completion(messages=messages, system=get_rag_system_prompt(0.0), temperature=0.3):
                yield {"type": "text", "content": chunk}
        except LLMError as e:
            yield {"type": "text", "content": f"\n\n⚠️ AI 服务出错: {e}"}
        yield {"type": "sources", "content": {"knowledge_base": {"status": "not_initialized", "docs_used": []}}}
        return

    # --- Status: searching ---
    yield {
        "type": "status",
        "content": "正在检索知识库..." if cn else "Searching knowledge base...",
    }

    # Build filters
    where_filter = _build_where_filter(query, ticker, query_complexity)
    keywords = _extract_keywords(query)
    n = _estimate_n_results(query, query_complexity)
    use_reranker = query_complexity == "detailed"

    # --- Search (hybrid: metadata filter + keyword filter + reranker) ---
    result = search(
        query, n_results=n, where=where_filter,
        keywords=keywords if keywords else None,
        use_reranker=use_reranker,
    )
    retrieved = result["docs"]
    max_relevance = result["max_relevance"]

    # Fallback: if filtered search returned nothing, retry without filters
    if not retrieved and (where_filter or keywords):
        result = search(query, n_results=n, where=None, keywords=None, use_reranker=use_reranker)
        retrieved = result["docs"]
        max_relevance = result["max_relevance"]

    # --- No results at all ---
    if not retrieved:
        yield {"type": "text", "content": "未在知识库中找到相关文档，使用通用知识回答：\n\n" if cn else "No relevant documents found, answering from general knowledge:\n\n"}
        messages = _build_messages(query, history)
        try:
            async for chunk in stream_completion(messages=messages, system=get_rag_system_prompt(0.0), temperature=0.3):
                yield {"type": "text", "content": chunk}
        except LLMError as e:
            yield {"type": "text", "content": f"\n\n⚠️ AI 服务出错: {e}"}
        yield {"type": "sources", "content": {"knowledge_base": {"status": "no_relevant_docs", "docs_used": [], "max_relevance": 0.0}}}
        return

    # --- Status: generating ---
    confidence_label = "高" if max_relevance >= CONFIDENCE_HIGH else "中" if max_relevance >= CONFIDENCE_MEDIUM else "低"
    confidence_label_en = "high" if max_relevance >= CONFIDENCE_HIGH else "medium" if max_relevance >= CONFIDENCE_MEDIUM else "low"
    yield {
        "type": "status",
        "content": f"找到 {len(retrieved)} 篇相关文档（置信度: {confidence_label}），正在生成回答..." if cn
        else f"Found {len(retrieved)} relevant docs (confidence: {confidence_label_en}), generating answer...",
    }

    # --- Hybrid enrichment: attach real-time data when ticker is known ---
    # e.g. "苹果市盈率是多少" → knowledge docs about P/E + AAPL live P/E value
    enrichment_block = ""
    if ticker:
        yield {
            "type": "status",
            "content": f"正在获取 {ticker} 实时数据补充..." if cn else f"Fetching {ticker} real-time data...",
        }
        try:
            market_data = await asyncio.to_thread(get_stock_data, ticker, {"mode": "relative", "period": "1mo"})
            if "error" not in market_data:
                parts = [f'<realtime_market_data ticker="{ticker}" timestamp="{market_data.get("data_timestamp", "")}">']
                parts.append("This is LIVE market data for reference, NOT from the knowledge base.")
                for key, label in [
                    ("current_price", "Current Price"),
                    ("pe_ratio_ttm", "P/E (TTM)"),
                    ("eps_ttm", "EPS (TTM)"),
                    ("market_cap", "Market Cap"),
                    ("dividend_yield", "Dividend Yield"),
                ]:
                    val = market_data.get(key)
                    if val is not None:
                        parts.append(f"{label}: {val}")
                pe_note = market_data.get("pe_note")
                if pe_note:
                    parts.append(f"Note: {pe_note}")
                parts.append("</realtime_market_data>")
                enrichment_block = "\n".join(parts)
        except Exception as e:
            logger.debug("Market enrichment failed for %s (non-fatal): %s", ticker, e)

    # --- Confidence-aware prompt selection ---
    system_prompt = get_rag_system_prompt(max_relevance)
    user_message = build_rag_prompt(query, retrieved, enrichment=enrichment_block)
    messages = _build_messages(user_message, history)

    try:
        async for chunk in stream_completion(messages=messages, system=system_prompt, temperature=0.3):
            yield {"type": "text", "content": chunk}
    except LLMError as e:
        yield {"type": "text", "content": f"\n\n⚠️ AI 服务出错: {e}"}

    # --- Sources with confidence metadata ---
    docs_meta = [
        {
            "id": doc.get("id", f"doc_{i}"),
            "source": doc["source"],
            "topic": doc.get("topic", ""),
            "entity": doc.get("entity", ""),
            "relevance": doc.get("relevance_score", 0),
            "rerank_score": doc.get("rerank_score"),
        }
        for i, doc in enumerate(retrieved)
    ]
    yield {
        "type": "sources",
        "content": {
            "knowledge_base": {
                "status": "ok",
                "total_docs": doc_count,
                "docs_used": docs_meta,
                "n_results_requested": n,
                "filter_applied": where_filter,
                "keywords_used": keywords,
                "max_relevance": max_relevance,
                "confidence_tier": confidence_label_en,
                "reranker_used": use_reranker,
            }
        },
    }
