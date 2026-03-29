"""
Query Router — three-tier classification with compound detection.

Tier 0 (instant, free): multi-ticker regex detects compound queries
  e.g. "比较苹果和微软" → compound with sub_tasks for AAPL + MSFT
Tier 1 (instant, free): regex pre-filter for single-agent queries (unchanged)
Tier 2 (fallback): Claude tool_use for ambiguous queries + compound detection
"""
import re
import logging
from services.llm import chat_completion, LLMError
from services.market_data import resolve_ticker, COMPANY_TICKER_MAP, _TICKER_BLACKLIST
from prompts.router import ROUTER_SYSTEM, ROUTER_TOOLS

logger = logging.getLogger("router")

_KNOWLEDGE_PATTERNS = re.compile(
    r"(什么是|怎么算|怎么用|怎么看|怎么理解|怎么分析|"
    r"如何理解|如何计算|如何分析|如何阅读|如何使用|如何评估|" 
    r"解释|定义|区别|概念|原理|意思|"
    r"财报|季报|年报|业绩|营收|净利|利润表|资产负债|现金流|"
    r"what\s+is|explain|define|difference|how\s+does|how\s+to|meaning\s+of|"
    r"earnings|quarterly|annual\s+report|revenue|income\s+statement|balance\s+sheet)",
    re.IGNORECASE,
)

_MARKET_ACTION_PATTERNS = re.compile(
    r"(股价|股票|行情|走势|涨跌|涨了|跌了|价格|市值|"
    r"stock\s*price|share\s*price|market\s*cap|trend|performance)",
    re.IGNORECASE,
)

_SIMPLE_PATTERNS = re.compile(
    r"(多少钱|什么价|现在价格|当前价格|stock\s*price|trading\s*at|current\s*price|how\s*much|"
    r"price\s*of|价格是)",
    re.IGNORECASE,
)

_DETAILED_PATTERNS = re.compile(
    r"(分析|走势|趋势|为什么|原因|比较|对比|表现|如何看|怎么分析|"
    r"analyz|trend|why|compar|explain|performance|how\s+to\s+read)",
    re.IGNORECASE,
)

_COMPARISON_PATTERNS = re.compile(
    r"(比较|对比|vs\.?|versus|compare|相比|哪个好|哪个更|谁更|"
    r"differ|better|worse|优劣|差异|PK)",
    re.IGNORECASE,
)

# ================================================================
#  Complexity inference (unchanged)
# ================================================================

def _infer_complexity(query: str) -> str:
    if _SIMPLE_PATTERNS.search(query):
        return "simple"
    if _DETAILED_PATTERNS.search(query):
        return "detailed"
    if _MARKET_ACTION_PATTERNS.search(query) and len(query) < 20:
        return "simple"
    return "detailed"


# ================================================================
#  Compound detection — Tier 0 (multi-ticker regex)
# ================================================================

def _extract_all_tickers(query: str) -> list[str]:
    """Extract ALL ticker symbols mentioned in a query (deduplicated)."""
    tickers = []
    seen = set()

    # Check company name map (Chinese + English names → tickers)
    for name, tk in COMPANY_TICKER_MAP.items():
        if name in query and tk and tk not in seen:
            tickers.append(tk)
            seen.add(tk)

    # Check uppercase sequences (regex ticker patterns)
    for match in re.findall(r"[A-Z]{2,6}", query):
        if match not in _TICKER_BLACKLIST and match not in seen:
            tickers.append(match)
            seen.add(match)

    return tickers


def _detect_compound(query: str) -> dict | None:
    """
    Tier 0: Detect multi-ticker compound queries via regex.
    Returns routing dict if 2+ tickers found, None otherwise.

    Examples:
      "比较苹果和微软" → compound [AAPL, MSFT]
      "TSLA vs NVDA"   → compound [TSLA, NVDA]
      "苹果股价"        → None (single ticker, not compound)
    """
    tickers = _extract_all_tickers(query)
    if len(tickers) < 2:
        return None

    has_knowledge = bool(_KNOWLEDGE_PATTERNS.search(query))

    # Build sub_tasks: one market task per ticker
    sub_tasks = [
        {"agent": "market_data", "ticker": tk, "sub_query": f"{tk} market data"}
        for tk in tickers
    ]

    # If query also has knowledge keywords, add a knowledge sub_task
    if has_knowledge:
        sub_tasks.append({"agent": "knowledge", "ticker": "", "sub_query": query})

    logger.info("Compound detected: %d tickers [%s]%s",
                len(tickers), ", ".join(tickers),
                " + knowledge" if has_knowledge else "")

    return {
        "category": "compound",
        "is_compound": True,
        "sub_tasks": sub_tasks,
        "ticker": tickers[0],  # Primary ticker (for logging/display)
        "company_name": "",
        "query_complexity": "detailed",
        "query_summary": query[:80],
    }


# ================================================================
#  Single-agent pre-filter — Tier 1 (unchanged logic)
# ================================================================

def _pre_filter(query: str) -> dict | None:
    """
    Regex-based fast classification using a decision matrix.

    Signals extracted:
      - tickers: list of resolved tickers (0, 1, or 2+)
      - has_knowledge: bool (financial concept keywords)
      - has_market: bool (price/trend keywords)
      - has_comparison: bool (compare/vs keywords)

    Decision matrix:
    ┌──────────┬───────────┬────────┬────────────┬─────────────────────────┐
    │ tickers  │ knowledge │ market │ comparison │ result                  │
    ├──────────┼───────────┼────────┼────────────┼─────────────────────────┤
    │ 2+       │ any       │ any    │ any        │ compound (Tier 0)       │
    │ 1        │ ✓         │ ✓      │ any        │ compound (K+M)          │
    │ 1        │ any       │ any    │ ✓          │ compound (comparison)   │
    │ 1        │ ✓         │ ✗      │ ✗          │ knowledge (with ticker) │
    │ 1        │ ✗         │ ✓      │ ✗          │ market_data             │
    │ 1        │ ✗         │ ✗      │ ✗          │ market_data (default)   │
    │ 0        │ ✓         │ any    │ ✗          │ knowledge               │
    │ 0        │ any       │ any    │ any        │ None → Claude           │
    └──────────┴───────────┴────────┴────────────┴─────────────────────────┘
    """
    # ---- Extract all signals ----
    all_tickers = _extract_all_tickers(query)
    single_ticker = resolve_ticker(query) if len(all_tickers) <= 1 else ""
    has_knowledge = bool(_KNOWLEDGE_PATTERNS.search(query))
    has_market = bool(_MARKET_ACTION_PATTERNS.search(query))
    has_comparison = bool(_COMPARISON_PATTERNS.search(query))
    complexity = _infer_complexity(query)

    def _result(category: str, ticker: str = "", **extra) -> dict:
        r = {
            "category": category, "ticker": ticker, "company_name": "",
            "query_complexity": complexity, "query_summary": query[:80],
        }
        r.update(extra)
        return r

    # ---- Row 1: Multi-ticker → always compound ----
    # Handled by _detect_compound (Tier 0), but as safety net:
    if len(all_tickers) >= 2:
        sub_tasks = [
            {"agent": "market_data", "ticker": tk, "sub_query": f"{tk} market data"}
            for tk in all_tickers
        ]
        if has_knowledge:
            sub_tasks.append({"agent": "knowledge", "ticker": "", "sub_query": query})
        logger.info("Pre-filter: compound (%d tickers: %s)", len(all_tickers), all_tickers)
        return _result("compound", all_tickers[0],
                       is_compound=True, sub_tasks=sub_tasks)

    # ---- Row 2: Single ticker + knowledge + market → compound ----
    # "根据财报对比苹果股价" / "analyze Apple earnings vs stock price"
    if single_ticker and has_knowledge and has_market:
        logger.info("Pre-filter: compound (ticker=%s, knowledge+market)", single_ticker)
        return _result("compound", single_ticker,
                       is_compound=True,
                       sub_tasks=[
                           {"agent": "knowledge", "ticker": single_ticker, "sub_query": query},
                           {"agent": "market_data", "ticker": single_ticker, "sub_query": f"{single_ticker} market data"},
                       ])

    # ---- Row 3: Single ticker + comparison keywords → compound ----
    # "苹果和行业平均比较" — only one ticker found but comparison implies need for context
    # Note: if 2 tickers were found, Row 1 already caught it
    if single_ticker and has_comparison:
        logger.info("Pre-filter: compound (ticker=%s, comparison)", single_ticker)
        sub_tasks = [
            {"agent": "market_data", "ticker": single_ticker, "sub_query": f"{single_ticker} market data"},
            {"agent": "knowledge", "ticker": "", "sub_query": query},
        ]
        return _result("compound", single_ticker,
                       is_compound=True, sub_tasks=sub_tasks)

    # ---- Row 4: Single ticker + knowledge only → knowledge with ticker context ----
    # "什么是TSLA的市盈率" / "苹果财报分析"
    if single_ticker and has_knowledge:
        logger.info("Pre-filter: knowledge (ticker=%s)", single_ticker)
        return _result("knowledge", single_ticker)

    # ---- Row 5: Single ticker + market only → market_data ----
    # "BABA股价多少" / "苹果涨了多少"
    if single_ticker and has_market:
        logger.info("Pre-filter: market_data (ticker=%s)", single_ticker)
        return _result("market_data", single_ticker)

    # ---- Row 6: Single ticker + no keywords → market_data (default) ----
    # "BABA" / "苹果" — bare ticker, assume user wants price
    if single_ticker:
        logger.info("Pre-filter: market_data (ticker=%s, default)", single_ticker)
        return _result("market_data", single_ticker)

    # ---- Row 7: No ticker + knowledge → knowledge ----
    # "什么是市盈率" / "如何阅读财报"
    if has_knowledge:
        logger.info("Pre-filter: knowledge (no ticker)")
        return _result("knowledge")

    # ---- Row 8: No ticker + anything else → Claude fallthrough ----
    # "你好" / "今天天气" / ambiguous queries
    return None


# ================================================================
#  Route query — Tier 0 → Tier 1 → Tier 2
# ================================================================

async def route_query(query: str) -> dict:
    """
    Classify a user query.

    Priority:
      Tier 0: multi-ticker compound detection (regex, instant, free)
      Tier 1: single-agent pre-filter (regex, instant, free)
      Tier 2: Claude tool_use (1-2s, can detect single-ticker compounds)

    Falls back to 'general' on any error.
    """
    fallback = {
        "category": "general", "ticker": "", "company_name": "",
        "query_complexity": "detailed", "query_summary": query,
    }

    # Tier 0: compound (multi-ticker)
    compound = _detect_compound(query)
    if compound is not None:
        return compound

    # Tier 1: single-agent pre-filter
    pre = _pre_filter(query)
    if pre is not None:
        return pre

    # Tier 2: Claude tool_use (handles ambiguous + single-ticker compounds)
    logger.info("Pre-filter miss, calling Claude router")
    try:
        response = await chat_completion(
            messages=[{"role": "user", "content": query}],
            system=ROUTER_SYSTEM, tools=ROUTER_TOOLS,
        )
        for block in response.content:
            if block.type == "tool_use" and block.name == "classify_query":
                result = block.input

                # Check if Claude detected a compound query
                if result.get("is_compound") and isinstance(result.get("sub_tasks"), list):
                    sub_tasks = result["sub_tasks"]
                    # Validate: need at least 2 sub_tasks for compound
                    if len(sub_tasks) >= 2:
                        logger.info("Claude detected compound: %d sub_tasks", len(sub_tasks))
                        return {
                            "category": "compound",
                            "is_compound": True,
                            "sub_tasks": sub_tasks,
                            "ticker": result.get("ticker", ""),
                            "company_name": result.get("company_name", ""),
                            "query_complexity": result.get("query_complexity", "detailed"),
                            "query_summary": result.get("query_summary", query),
                        }

                # Standard single-agent routing
                return {
                    "category": result.get("category", "general"),
                    "ticker": result.get("ticker", ""),
                    "company_name": result.get("company_name", ""),
                    "query_complexity": result.get("query_complexity", "detailed"),
                    "query_summary": result.get("query_summary", query),
                }

        return fallback
    except Exception:
        return fallback