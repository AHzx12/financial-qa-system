"""
Multi-agent Supervisor.

Handles compound queries that need data from multiple agents/tickers.
Calls services directly (not agents) for data collection, then uses
a synthesizer LLM call to produce a unified analysis.

Flow:
  1. Parse time window from original query (once)
  2. Fan-out: parallel data collection (market + knowledge)
  3. Fan-in: collect results, handle errors
  4. Synthesize: single LLM call with all data
  5. Stream result + merged sources

Design choice: supervisor calls services directly rather than agents
in "collect mode". This avoids modifying agent interfaces and keeps
agents focused on their standalone use case.
"""
import asyncio
import re
import logging
from typing import AsyncGenerator
from datetime import datetime

from services.market_data import get_stock_data, parse_time_window
from services.news_service import get_news
from services.vector_store import search
from services.llm import stream_completion, LLMError
from prompts.supervisor import SYNTHESIZER_SYSTEM, build_synthesizer_prompt

logger = logging.getLogger("supervisor")

MAX_SUB_TASKS = 5  # Hard limit to prevent over-decomposition


def _is_chinese(text: str) -> bool:
    return bool(re.search(r"[\u4e00-\u9fff]", text))


# ================================================================
#  Data collectors — one per agent type
# ================================================================

async def _collect_market(ticker: str, time_window: dict) -> dict:
    """Collect market data + news for one ticker. Runs in thread pool."""
    try:
        # Determine news lookback
        if time_window["mode"] == "relative":
            period_to_days = {"7d": 7, "1mo": 30, "3mo": 90, "6mo": 180, "1y": 365}
            days_back = period_to_days.get(time_window.get("period", "1mo"), 30)
        else:
            end_date = datetime.strptime(time_window["end"], "%Y-%m-%d")
            days_back = max((datetime.now() - end_date).days, 30)

        # Parallel: stock data + news
        market_data, news_result = await asyncio.gather(
            asyncio.to_thread(get_stock_data, ticker, time_window),
            asyncio.to_thread(get_news, ticker, 3, days_back),
        )

        if "error" in market_data:
            return {"type": "market", "ticker": ticker, "error": market_data["error"]}

        return {
            "type": "market",
            "ticker": ticker,
            "data": market_data,
            "news": news_result,
        }
    except Exception as e:
        logger.warning("Market collection failed for %s: %s", ticker, e)
        return {"type": "market", "ticker": ticker, "error": str(e)}


async def _collect_knowledge(sub_query: str, ticker: str = "") -> dict:
    """Collect knowledge base documents via vector search."""
    try:
        where_filter = None
        if ticker:
            where_filter = {"entity": ticker}

        result = search(sub_query, n_results=5, where=where_filter, use_reranker=True)

        # Fallback: retry without filter if no results
        if not result["docs"] and where_filter:
            result = search(sub_query, n_results=5, where=None, use_reranker=True)

        return {
            "type": "knowledge",
            "docs": result["docs"],
            "max_relevance": result["max_relevance"],
        }
    except Exception as e:
        logger.warning("Knowledge collection failed: %s", e)
        return {"type": "knowledge", "docs": [], "max_relevance": 0.0, "error": str(e)}


# ================================================================
#  Supervisor — orchestrates collection + synthesis
# ================================================================

async def supervise(
    query: str,
    sub_tasks: list[dict],
    history: list[dict] | None = None,
) -> AsyncGenerator[dict, None]:
    """
    Multi-agent supervisor. Collects data in parallel, synthesizes with one LLM call.
    Yields: {type: "status"|"text"|"sources", content: ...}

    Args:
        query: Original user query
        sub_tasks: List of {agent, ticker, sub_query} from router
        history: Conversation history for LLM context
    """
    cn = _is_chinese(query)

    # Safety: cap sub_tasks
    if len(sub_tasks) > MAX_SUB_TASKS:
        logger.warning("Truncating sub_tasks from %d to %d", len(sub_tasks), MAX_SUB_TASKS)
        sub_tasks = sub_tasks[:MAX_SUB_TASKS]

    time_window = parse_time_window(query)
    total = len(sub_tasks)

    # ---- Phase 1: Build task list ----
    tasks = []
    task_labels = []

    for st in sub_tasks:
        agent_type = st.get("agent", "")
        ticker = st.get("ticker", "")
        sub_query = st.get("sub_query", query)

        if agent_type == "market_data" and ticker:
            tasks.append(_collect_market(ticker, time_window))
            task_labels.append(ticker)
        elif agent_type == "knowledge":
            tasks.append(_collect_knowledge(sub_query, ticker))
            task_labels.append("知识库" if cn else "KB")
        else:
            logger.warning("Unknown sub_task agent type: %s", agent_type)

    if not tasks:
        yield {"type": "text", "content": "⚠️ 无法分解查询任务。" if cn else "⚠️ Unable to decompose query."}
        return

    # ---- Phase 2: Parallel data collection ----
    label_str = " + ".join(task_labels)
    yield {
        "type": "status",
        "content": f"正在并行获取数据: {label_str} ({total} 项)..."
        if cn else f"Collecting data in parallel: {label_str} ({total} sources)...",
    }

    results = await asyncio.gather(*tasks, return_exceptions=True)

    # ---- Phase 3: Process results ----
    market_results = []
    knowledge_results = []
    errors = []

    for i, result in enumerate(results):
        label = task_labels[i] if i < len(task_labels) else f"task_{i}"

        if isinstance(result, Exception):
            errors.append(f"{label}: {result}")
            logger.warning("Sub-task %s raised exception: %s", label, result)
            continue
        if not isinstance(result, dict):
            errors.append(f"{label}: unexpected result type")
            continue
        if result.get("error"):
            errors.append(f"{result.get('ticker', label)}: {result['error']}")
            continue

        if result["type"] == "market":
            market_results.append(result)
        elif result["type"] == "knowledge":
            knowledge_results.append(result)

    # All failed?
    if not market_results and not knowledge_results:
        error_text = "\n".join(f"- {e}" for e in errors) if errors else "Unknown error"
        yield {
            "type": "text",
            "content": f"⚠️ 所有数据源均获取失败:\n{error_text}"
            if cn else f"⚠️ All data sources failed:\n{error_text}",
        }
        return

    # ---- Phase 4: Synthesize ----
    collected = len(market_results) + len(knowledge_results)
    failed = len(errors)
    status_parts = []
    if market_results:
        tickers = [mr["ticker"] for mr in market_results]
        status_parts.append(f"行情: {', '.join(tickers)}" if cn else f"Market: {', '.join(tickers)}")
    if knowledge_results:
        doc_count = sum(len(kr.get("docs", [])) for kr in knowledge_results)
        status_parts.append(f"知识库: {doc_count} 篇文档" if cn else f"KB: {doc_count} docs")
    if failed:
        status_parts.append(f"{failed} 项失败" if cn else f"{failed} failed")

    yield {
        "type": "status",
        "content": f"已收集 {collected}/{total} 项数据（{', '.join(status_parts)}），正在综合分析..."
        if cn else f"Collected {collected}/{total} sources ({', '.join(status_parts)}), synthesizing...",
    }

    user_prompt = build_synthesizer_prompt(query, market_results, knowledge_results, errors)

    messages = []
    if history:
        messages.extend(history)
    messages.append({"role": "user", "content": user_prompt})

    try:
        async for chunk in stream_completion(
            messages=messages, system=SYNTHESIZER_SYSTEM, temperature=0.3,
        ):
            yield {"type": "text", "content": chunk}
    except LLMError as e:
        yield {"type": "text", "content": f"\n\n⚠️ 综合分析出错: {e}"}

    # ---- Phase 5: Merged sources ----
    sources: dict = {}

    for mr in market_results:
        ticker = mr["ticker"]
        data = mr["data"]
        sources[f"market_{ticker}"] = {
            "source": "Yahoo Finance",
            "ticker": ticker,
            "period": data.get("period_label", ""),
            "timestamp": data.get("data_timestamp", ""),
        }
        news_articles = mr.get("news", {}).get("articles", [])
        if news_articles:
            sources[f"news_{ticker}"] = [
                {"title": a["title"], "publisher": a["publisher"], "time": a["publish_time"]}
                for a in news_articles[:2]
            ]

    if knowledge_results:
        all_docs_meta = []
        for kr in knowledge_results:
            for doc in kr.get("docs", []):
                all_docs_meta.append({
                    "id": doc.get("id", ""),
                    "source": doc.get("source", ""),
                    "topic": doc.get("topic", ""),
                    "relevance": doc.get("relevance_score", 0),
                })
        if all_docs_meta:
            sources["knowledge_base"] = {
                "status": "ok",
                "docs_used": all_docs_meta,
            }

    if errors:
        sources["errors"] = errors

    yield {"type": "sources", "content": sources}
