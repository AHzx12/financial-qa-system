"""
News service — fetches recent news for a given ticker.

Fixes applied:
- P1-4: All time comparisons use UTC consistently.
"""
import os
import logging
import yfinance as yf
from datetime import datetime, timedelta, timezone
from cachetools import TTLCache
from services.llm import get_client


logger = logging.getLogger("news_service")
# TODO: For multi-worker deployment, replace with Redis cache.
# See services/session_cache.py for the Redis pattern.
_NEWS_CACHE_TTL = int(os.getenv("NEWS_CACHE_TTL_SECONDS", "600"))
_news_cache: TTLCache = TTLCache(maxsize=64, ttl=_NEWS_CACHE_TTL)


def get_news(ticker: str, max_results: int = 5, days_back: int = 30) -> dict:
    """Fetch recent news. Returns {status, articles, message}."""
    cache_key = f"{ticker}:{days_back}"
    if cache_key in _news_cache:
        return _news_cache[cache_key]

    try:
        stock = yf.Ticker(ticker)
        raw_news = stock.news or []
    except Exception as e:
        logger.warning("News fetch failed for %s: %s", ticker, e)
        return {"status": "error", "articles": [], "message": f"News service temporarily unavailable: {e}"}

    if not raw_news:
        result = {"status": "no_news", "articles": [], "message": f"No recent news articles found for {ticker}."}
        _news_cache[cache_key] = result
        return result

    # P1-4: UTC-consistent cutoff
    cutoff = datetime.now(timezone.utc) - timedelta(days=days_back)
    articles = []

    for item in raw_news:
        content = item.get("content") or item
        title = content.get("title") or item.get("title") or "Untitled"
        publisher = content.get("provider", {}).get("displayName") or item.get("publisher") or "Unknown"
        link = content.get("canonicalUrl", {}).get("url") or item.get("link") or ""

        pub_ts = content.get("pubDate") or item.get("providerPublishTime")
        pub_dt: datetime | None = None
        if isinstance(pub_ts, (int, float)):
            # P1-4: parse as UTC
            pub_dt = datetime.fromtimestamp(pub_ts, tz=timezone.utc)
        elif isinstance(pub_ts, str):
            try:
                pub_dt = datetime.fromisoformat(pub_ts.replace("Z", "+00:00"))
            except ValueError:
                pass

        pub_time_str = pub_dt.strftime("%Y-%m-%d %H:%M UTC") if pub_dt else "N/A"

        if pub_dt and pub_dt < cutoff:
            continue

        articles.append({
            "title": title, "publisher": publisher, "link": link,
            "publish_time": pub_time_str, "publish_dt": pub_dt,
        })

    articles.sort(key=lambda a: a.get("publish_dt") or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
    articles = articles[:max_results]
    for a in articles:
        a.pop("publish_dt", None)

    if not articles:
        result = {"status": "no_news", "articles": [], "message": f"No news for {ticker} in the last {days_back} days."}
    else:
        result = {"status": "ok", "articles": articles, "message": f"Found {len(articles)} articles for {ticker}."}

    _news_cache[cache_key] = result
    return result

async def search_news_via_llm(ticker: str, query: str, date_context: str = "") -> dict:
    """Use Claude web search as fallback when Yahoo news is unavailable."""
    try:
        client = get_client()
        search_query = f"{ticker} stock news {date_context}".strip()

        response = await client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=1000,
            tools=[{"type": "web_search_20250305", "name": "web_search"}],
            messages=[{
                "role": "user",
                "content": (
                    f"Search for recent news about {ticker} stock. "
                    f"{f'Focus on events around {date_context}.' if date_context else ''} "
                    f"Return ONLY a brief summary of the top 3 most relevant news items. "
                    f"For each item, include: title, source, and a 1-sentence summary. "
                    f"If no relevant news is found, say 'No relevant news found.'"
                ),
            }],
        )

        # Extract text from response
        text_parts = [block.text for block in response.content if block.type == "text"]
        news_text = "\n".join(text_parts)

        if not news_text or "no relevant news" in news_text.lower():
            return {"status": "no_news", "articles": [], "message": "Web search found no relevant news"}

        return {
            "status": "ok",
            "articles": [],  # No structured articles, just raw text
            "web_search_summary": news_text,
            "message": f"Web search results for {ticker}",
        }
    except Exception as e:
        return {"status": "error", "articles": [], "message": f"Web search failed: {e}"}


def format_news_for_prompt(result: dict) -> str:
    if result.get("web_search_summary"):
        return f"[Web Search Results]\n{result['web_search_summary']}"
    status = result.get("status", "error")
    articles = result.get("articles", [])
    message = result.get("message", "")

    if status == "error":
        return f"[News service error: {message}]"
    if status == "no_news" or not articles:
        return f"[{message}]"

    lines = [f"Found {len(articles)} recent news articles:"]
    for i, a in enumerate(articles, 1):
        lines.append(f"{i}. [{a['publish_time']}] {a['title']} — {a['publisher']}")
    return "\n".join(lines)
