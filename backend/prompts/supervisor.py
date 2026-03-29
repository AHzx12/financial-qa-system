"""
Supervisor synthesizer prompt — combines data from multiple agents into unified analysis.

Anti-hallucination: explicit rules against inventing causal links between data sources.
"""

from services.news_service import format_news_for_prompt

SYNTHESIZER_SYSTEM = """You are a senior financial analyst synthesizing data from 
multiple sources into a unified, comparative analysis.

LANGUAGE RULE: Detect the user's language. Respond entirely in that language.

CRITICAL RULES:
1. Use ONLY the data provided below. NEVER fabricate numbers or data points.
2. ONLY state relationships between data sources when the evidence directly supports it.
   DO NOT invent causal links (e.g. don't say "stock fell because of weak earnings" 
   unless the data explicitly shows an earnings miss).
3. Cite sources explicitly: use "AAPL 行情数据显示..." or "知识库文档#N 指出..." format.
4. If data sources appear contradictory, state both sides and note the discrepancy.
5. When comparing multiple tickers, use parallel structure (same metrics side by side).
6. If any data source had errors or is missing, state that transparently.
7. Include a brief risk disclaimer at the end.

OUTPUT STRUCTURE (translate headers to match user's language):
## 📊 Comparative Analysis

### Key Metrics
(side-by-side comparison table if multiple tickers; key data points if single ticker with multiple sources)

### Analysis
(synthesize findings across all data sources; connect data points only where evidence supports it)

### Data Limitations
(note any errors, missing data, or gaps in the analysis)

### ⚠️ Risk Disclaimer
(historical data only, not investment advice)
"""

MAX_MARKET_SECTION_CHARS = 800  # Keep each ticker's data compact
MAX_KNOWLEDGE_CHARS = 3000      # Total budget for knowledge docs


def build_synthesizer_prompt(
    query: str,
    market_results: list[dict],
    knowledge_results: list[dict],
    errors: list[str],
) -> str:
    """Build the synthesizer user prompt from collected sub-agent data."""
    sections = [f"User question: {query}\n"]

    # ---- Market data sections (one per ticker) ----
    for mr in market_results:
        ticker = mr["ticker"]
        d = mr["data"]
        news = mr.get("news", {})
        news_text = format_news_for_prompt(news) if news else "[No news data]"

        mc = d.get("market_cap")
        if mc:
            mc_str = f"${mc/1e12:.2f}T" if mc >= 1e12 else f"${mc/1e9:.2f}B" if mc >= 1e9 else f"${mc/1e6:.2f}M"
        else:
            mc_str = "N/A"

        pe_note = d.get("pe_note", "")
        pe_line = f"P/E (TTM): {d.get('pe_ratio_ttm') or 'N/A'}"
        if pe_note:
            pe_line += f" ({pe_note})"

        change_sign = "+" if d.get("period_change", 0) >= 0 else ""
        pct_sign = "+" if d.get("period_change_pct", 0) >= 0 else ""

        warnings = d.get("data_warnings", [])
        warnings_text = " | ".join(warnings) if warnings else ""

        sections.append(f"""<market_data ticker="{ticker}">
Company: {d.get('company_name', ticker)}
Sector: {d.get('sector', 'N/A')} | Industry: {d.get('industry', 'N/A')}
Currency: {d.get('currency', 'USD')}
Current Price: {d['current_price']}
Previous Close: {d.get('previous_close', 'N/A')}
Period: {d.get('period_label', 'N/A')}
Period Change: {change_sign}{d.get('period_change', 'N/A')} ({pct_sign}{d.get('period_change_pct', 'N/A')}%)
Trend: {d.get('trend', 'N/A')}
Market Cap: {mc_str}
{pe_line}
EPS (TTM): {d.get('eps_ttm') or 'N/A'}
Dividend Yield: {f"{d['dividend_yield']*100:.2f}%" if d.get('dividend_yield') else 'N/A'}
52W Range: {d.get('52w_low', 'N/A')} - {d.get('52w_high', 'N/A')}
Beta: {d.get('beta') or 'N/A'}
{f'Warnings: {warnings_text}' if warnings_text else ''}
News: {news_text}
</market_data>
""")

    # ---- Knowledge base sections ----
    if knowledge_results:
        doc_blocks = []
        total_chars = 0
        doc_idx = 1

        for kr in knowledge_results:
            for doc in kr.get("docs", []):
                content = doc["content"]
                # Truncate individual docs
                if len(content) > 500:
                    content = content[:500] + "...(truncated)"
                # Check total budget
                if total_chars + len(content) > MAX_KNOWLEDGE_CHARS:
                    break
                total_chars += len(content)

                source = doc.get("source", "unknown")
                topic = doc.get("topic", "general")
                relevance = doc.get("relevance_score", "N/A")
                doc_blocks.append(
                    f'<document id="{doc_idx}" source="{source}" topic="{topic}" '
                    f'relevance="{relevance}">\n{content}\n</document>'
                )
                doc_idx += 1

        if doc_blocks:
            sections.append(
                "<knowledge_base>\n" + "\n\n".join(doc_blocks) + "\n</knowledge_base>\n"
            )

    # ---- Errors section ----
    if errors:
        sections.append(
            "<data_errors>\nThe following data sources encountered errors:\n"
            + "\n".join(f"- {e}" for e in errors)
            + "\nNote these gaps in your analysis.\n</data_errors>\n"
        )

    sections.append(
        "Synthesize ALL data above into a unified analysis. "
        "Use pre-computed numbers directly — do NOT recalculate. "
        "Respond in the SAME LANGUAGE as the user's question."
    )

    return "\n".join(sections)
