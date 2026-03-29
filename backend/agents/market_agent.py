"""
Market Data Agent.

Upgrades:
- Parallel fetch: stock data + news fetched concurrently via asyncio.gather
  (also fixes event loop blocking by wrapping sync yfinance in to_thread)
- Status events: yields {"type":"status"} between steps for frontend progress display
"""
import asyncio
import re
from typing import AsyncGenerator
from services.market_data import get_stock_data, resolve_ticker, parse_time_window
from services.news_service import get_news, format_news_for_prompt
from services.llm import stream_completion, LLMError
from prompts.market_analysis import get_market_system_prompt, build_market_prompt
from services.news_service import get_news, format_news_for_prompt, search_news_via_llm


def _is_chinese(text: str) -> bool:
    """Check if text contains Chinese characters (for status message language)."""
    return bool(re.search(r"[\u4e00-\u9fff]", text))


async def handle_market_query(
    query: str,
    ticker: str = "",
    company_name: str = "",
    history: list[dict] | None = None,
    query_complexity: str = "detailed",
) -> AsyncGenerator[dict, None]:
    """
    Handle a market data query with parallel data fetching.
    Yields: {type: "status"|"text"|"sources", content: ...}
    """
    cn = _is_chinese(query)

    # Step 1: Resolve ticker
    resolved = resolve_ticker(query, ticker, company_name)
    if not resolved:
        yield {
            "type": "text",
            "content": (
                "⚠️ 无法识别股票代码。请提供具体的公司名称或股票代码，例如：\n"
                "- 阿里巴巴 / BABA\n- 特斯拉 / TSLA\n- 英伟达 / NVDA\n- 苹果 / AAPL\n- 星巴克 / SBUX"
            ),
        }
        return

    # Step 2: Parse time window
    time_window = parse_time_window(query)
    if time_window["mode"] == "relative":
        period = time_window["period"]
        period_to_days = {"7d": 7, "1mo": 30, "3mo": 90, "6mo": 180, "1y": 365}
        days_back = period_to_days.get(period, 30)
    else:
        period = "custom"
        from datetime import datetime as dt
        end_date = dt.strptime(time_window["end"], "%Y-%m-%d")
        days_back = max((dt.now() - end_date).days, 30)

    # Step 3: Parallel data fetch
    yield {
        "type": "status",
        "content": f"正在获取 {resolved} 行情数据与新闻..." if cn else f"Fetching {resolved} market data & news...",
    }

    # Decide news strategy based on time window
    is_historical = False
    if time_window["mode"] == "absolute":
        from datetime import datetime as dt
        end_date = dt.strptime(time_window["end"], "%Y-%m-%d")
        is_historical = (dt.now() - end_date).days > 14

    if is_historical:
        # Historical query: Yahoo news is irrelevant, skip it
        market_data = await asyncio.to_thread(get_stock_data, resolved, time_window)
        news_result = {"status": "no_news", "articles": [], "message": ""}

        # Try web search for historical news
        yield {
            "type": "status",
            "content": "正在搜索历史新闻..." if cn else "Searching historical news...",
        }
        date_context = f"{time_window['start']} to {time_window['end']}"
        try:
            news_result = await asyncio.wait_for(
                search_news_via_llm(resolved, query, date_context),
                timeout=20,
            )
        except asyncio.TimeoutError:
            news_result = {"status": "no_news", "articles": [], "message": "Historical news search timed out"}
    else:
        # Recent query: Yahoo news is relevant, fetch in parallel
        try:
            market_data, news_result = await asyncio.gather(
                asyncio.to_thread(get_stock_data, resolved, time_window),
                asyncio.to_thread(get_news, resolved, 5, days_back),
            )
        except Exception as e:
            yield {"type": "text", "content": f"⚠️ 数据获取失败: {e}"}
            return

    if "error" in market_data:
        yield {"type": "text", "content": f"⚠️ 数据获取失败: {market_data['error']}"}
        return

    news_text = format_news_for_prompt(news_result)
    news_articles = news_result.get("articles", [])

    # For recent queries, if Yahoo had no news, try web search fallback
    if not is_historical and (news_result.get("status") != "ok" or not news_result.get("articles")):
        if not news_result.get("web_search_summary"):
            yield {
                "type": "status",
                "content": "正在搜索新闻..." if cn else "Searching news...",
            }
            try:
                news_result = await asyncio.wait_for(
                    search_news_via_llm(resolved, query, ""),
                    timeout=15,
                )
                news_text = format_news_for_prompt(news_result)
                news_articles = news_result.get("articles", [])
            except asyncio.TimeoutError:
                pass  # Keep original no_news result

    
    # Step 4: Stream LLM analysis
    yield {
        "type": "status",
        "content": "正在生成分析报告..." if cn else "Generating analysis...",
    }

    user_message = build_market_prompt(query, market_data, news_text)
    system_prompt = get_market_system_prompt(query_complexity)

    messages = []
    if history:
        messages.extend(history)
    messages.append({"role": "user", "content": user_message})

    try:
        async for chunk in stream_completion(messages=messages, system=system_prompt, temperature=0.2):
            yield {"type": "text", "content": chunk}
    except LLMError as e:
        yield {"type": "text", "content": f"\n\n⚠️ AI 分析服务出错: {e}"}

    # Step 5: Sources
    sources: dict = {
        "market_data": {
            "source": "Yahoo Finance", "ticker": resolved,
            "period": time_window.get("period", f"{time_window.get('start', '')} ~ {time_window.get('end', '')}"),
        },
        "news_status": news_result.get("status", "unknown"),
    }
    if news_articles and query_complexity == "detailed":
        sources["news"] = [
            {"title": a["title"], "publisher": a["publisher"], "time": a["publish_time"]}
            for a in news_articles[:3]
        ]
    elif news_result.get("web_search_summary") and query_complexity == "detailed":
        sources["news"] = [{"title": "Web search results (Claude)", "publisher": "Web Search", "time": ""}]
    yield {"type": "sources", "content": sources}

