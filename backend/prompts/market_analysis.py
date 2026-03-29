"""
Market Data Agent prompt — dual template.

SIMPLE: for "what's the price of X" → concise 2-3 section response.
FULL: for "analyze X's recent performance" → detailed 6-section briefing.
"""

MARKET_SYSTEM_SIMPLE = """You are a financial data assistant providing concise price lookups.

LANGUAGE RULE: Detect the user's language. Respond entirely in that language.

RULES:
1. Use ONLY the pre-computed data provided. NEVER fabricate numbers.
2. Keep the response SHORT — 3-5 sentences max.
3. Include: current price, period change (amount + %), and trend label.
4. Do NOT include lengthy analysis, news discussion, or risk disclaimers.
5. If the user only asked for a price, just give the price and basic context."""

MARKET_SYSTEM_FULL = """You are a senior financial analyst assistant providing 
structured, data-driven briefings.

LANGUAGE RULE (CRITICAL): Detect the user's language. ALL headers, labels, and prose
must be in that same language. Do NOT mix languages.

STRICT RULES:
1. Use ONLY the pre-computed data provided. NEVER fabricate or re-calculate numbers.
2. NEVER predict future price movements.
3. Clearly separate objective data from analytical commentary.
4. If news evidence is provided, cite specific headlines when discussing possible factors.
   If the news section says "no news found" or "service error", state that clearly —
   do NOT invent reasons.
5. Always include a brief risk disclaimer at the end.
6. Use the EXACT output structure below — do not rearrange sections.

OUTPUT STRUCTURE (translate headers to match user's language):
## 📊 [Company Name] ([Ticker]) — Market Overview

### Current price
(current price, currency, previous close, 52-week range)

### Period performance
(period change amount, percentage, high/low, avg volume, trend tag)

### Recent daily data
(table of daily prices if available)

### Trend summary
- Base your summary ONLY on the trend tag and daily data pattern.
- If daily data has fewer than 3 data points, state that the data is insufficient
  for trend analysis rather than drawing conclusions.

### Possible factors
- If news articles are provided: cite specific headlines as evidence.
- If news status is "no_news": State clearly that no recent news was found and you 
  cannot identify specific contributing factors. Do NOT speculate or suggest possible reasons.
- If news status is "error": State that the news service is temporarily unavailable.
  Do NOT attempt to fill in with guesses.

### ⚠️ Risk disclaimer
(historical data only, not investment advice)
"""


def get_market_system_prompt(complexity: str = "detailed") -> str:
    """Select system prompt based on query complexity."""
    if complexity == "simple":
        return MARKET_SYSTEM_SIMPLE
    return MARKET_SYSTEM_FULL


def build_market_prompt(
    query: str,
    market_data: dict,
    news_text: str = "",
) -> str:
    """Build the user message with pre-computed data + news evidence."""
    d = market_data
    daily = d.get("daily_data", [])

    daily_table = "Date | Close | Day Change | Day Change % | Volume\n"
    daily_table += "---|---|---|---|---\n"
    for row in daily:
        dc = row["day_change"]
        dp = row["day_change_pct"]
        if dc is not None:
            sign = "+" if dc >= 0 else ""
            change_str = f"{sign}{dc}"
            pct_str = f"{sign}{dp}%"
        else:
            change_str = "—"
            pct_str = "—"
        daily_table += (
            f"{row['date']} | {row['close']} | "
            f"{change_str} | {pct_str} | "
            f"{row['volume']:,}\n"
        )

    mc = d.get("market_cap")
    if mc:
        mc_str = f"${mc/1e12:.2f}T" if mc >= 1e12 else f"${mc/1e9:.2f}B" if mc >= 1e9 else f"${mc/1e6:.2f}M"
    else:
        mc_str = "N/A"

    # Data warnings section
    warnings = d.get("data_warnings", [])
    warnings_text = "\n".join(f"⚠️ {w}" for w in warnings) if warnings else "None"

    return f"""User question: {query}

Below is PRE-COMPUTED market data from Yahoo Finance. Use these numbers directly.
Do NOT recalculate any values — they are already computed by the system.

<market_data>
Ticker: {d['ticker']}
Company: {d['company_name']}
Sector: {d.get('sector', 'N/A')}
Industry: {d.get('industry', 'N/A')}
Currency: {d['currency']}

Current Price: {d['current_price']}
Previous Close: {d.get('previous_close', 'N/A')}
52-Week High: {d.get('52w_high', 'N/A')}
52-Week Low: {d.get('52w_low', 'N/A')}

--- {d['period_label']} Performance (period={d['period']}) ---
Start Price: {d['period_start_price']}
End Price: {d['current_price']}
Change: {'+' if d['period_change'] >= 0 else ''}{d['period_change']} ({'+' if d['period_change_pct'] >= 0 else ''}{d['period_change_pct']}%)
Period High: {d['period_high']}
Period Low: {d['period_low']}
Avg Daily Volume: {d['avg_volume']:,}
Trend: {d['trend']}

--- Fundamentals ---
Market Cap: {mc_str}
P/E (TTM): {d.get('pe_ratio_ttm') or 'N/A'}
P/E (Forward): {d.get('pe_ratio_forward') or 'N/A'}
EPS (TTM): {d.get('eps_ttm') or 'N/A'}
Dividend Yield: {f"{d['dividend_yield']*100:.2f}%" if d.get('dividend_yield') else 'N/A'}
Beta: {d.get('beta') or 'N/A'}

--- Recent Daily Data ---
{daily_table}
--- Data Quality Warnings ---
{warnings_text}
</market_data>

<recent_news>
{news_text}
</recent_news>

Data Source: {d['data_source']}
Data Timestamp: {d['data_timestamp']}

IMPORTANT: Respond in the SAME LANGUAGE as the user's question above.
"""
