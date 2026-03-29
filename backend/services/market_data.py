"""
Market data service — Yahoo Finance wrapper.

Fixes applied:
- P1-3: _TICKER_BLACKLIST filters financial acronyms (PE, ETF, IPO, etc.)
- P1-6: _yfinance_ticker_search skips Chinese-only strings
"""
import os
import re
import logging
import unicodedata
import yfinance as yf
from datetime import datetime
from cachetools import TTLCache

logger = logging.getLogger("market_data")

# TODO: For multi-worker deployment (uvicorn --workers N), replace TTLCache
# with Redis to avoid duplicate yfinance requests across workers.
# See services/session_cache.py for the Redis client pattern.
_CACHE_TTL = int(os.getenv("MARKET_CACHE_TTL_SECONDS", "300"))
_cache: TTLCache = TTLCache(maxsize=128, ttl=_CACHE_TTL)
_ticker_cache: TTLCache = TTLCache(maxsize=256, ttl=3600)

# P1-3: Financial acronyms that look like tickers but aren't
_TICKER_BLACKLIST = {
    "PE", "EPS", "ETF", "IPO", "GDP", "CPI", "PPI", "PCE",
    "CEO", "CFO", "SEC", "EBIT", "COGS", "DCF", "RSI", "MACD",
    "SMA", "EMA", "YTM", "ROE", "ROA", "WACC", "FCF", "DCA",
    "TTM", "YOY", "QOQ", "GAAP", "NON", "USD", "CNY", "EUR",
    "GBP", "JPY", "HKD", "API", "SSE", "LLM", "RAG", "AI",
}

COMPANY_TICKER_MAP = {
    "阿里巴巴": "BABA", "阿里": "BABA",
    "腾讯": "0700.HK", "京东": "JD", "百度": "BIDU",
    "拼多多": "PDD", "网易": "NTES", "美团": "3690.HK",
    "小米": "1810.HK", "比亚迪": "BYDDF",
    "蔚来": "NIO", "理想": "LI", "小鹏": "XPEV",
    "哔哩哔哩": "BILI", "B站": "BILI", "携程": "TCOM",
    "特斯拉": "TSLA", "Tesla": "TSLA",
    "苹果": "AAPL", "Apple": "AAPL",
    "谷歌": "GOOGL", "Google": "GOOGL", "Alphabet": "GOOGL",
    "微软": "MSFT", "Microsoft": "MSFT",
    "亚马逊": "AMZN", "Amazon": "AMZN",
    "英伟达": "NVDA", "Nvidia": "NVDA", "NVIDIA": "NVDA",
    "Meta": "META", "脸书": "META", "Facebook": "META",
    "台积电": "TSM", "TSMC": "TSM",
    "奈飞": "NFLX", "Netflix": "NFLX",
    "迪士尼": "DIS", "Disney": "DIS",
    "星巴克": "SBUX", "Starbucks": "SBUX",
    "可口可乐": "KO", "Coca-Cola": "KO",
    "耐克": "NKE", "Nike": "NKE",
    "麦当劳": "MCD", "McDonald": "MCD",
    "沃尔玛": "WMT", "Walmart": "WMT",
    "摩根大通": "JPM", "JPMorgan": "JPM",
    "高盛": "GS", "Goldman": "GS",
    "波音": "BA", "Boeing": "BA",
    "英特尔": "INTC", "Intel": "INTC",
    "AMD": "AMD", "超威": "AMD",
    "高通": "QCOM", "Qualcomm": "QCOM",
    "标普": "SPY", "标普500": "SPY",
    # Long-tail: Chinese companies (A-share via Yahoo suffix, US ADR otherwise)
    "联想": "LNVGY", "Lenovo": "LNVGY",
    "中国平安": "PNGAY", "平安": "PNGAY",
    "招商银行": "CIHKY", "招行": "CIHKY",
    "工商银行": "IDCBY", "工行": "IDCBY",
    "中国银行": "BACHF",
    "贵州茅台": "600519.SS", "茅台": "600519.SS",
    "宁德时代": "300750.SZ",
    "华为": "HUAWY",  # OTC
    "中芯国际": "SMICY", "中芯": "SMICY",
    "字节跳动": "", "抖音": "",  # Not public, empty = intentional miss
    "Uber": "UBER", "优步": "UBER",
    "Spotify": "SPOT",
    "Coinbase": "COIN",
    "Snowflake": "SNOW",
    "Palantir": "PLTR",
    "CrowdStrike": "CRWD",
    "Salesforce": "CRM",
}


def resolve_ticker(query: str, ticker: str = "", company_name: str = "") -> str:
    if ticker:
        return ticker.upper().strip()

    for text in [company_name, query]:
        if not text:
            continue
        for cn_name, tk in COMPANY_TICKER_MAP.items():
            if cn_name in text and tk:  # Skip empty values (unlisted companies)
                return tk

    # P1-3: Regex match with blacklist filter
    matches = re.findall(r"[A-Z]{2,6}", query)
    if matches:
        candidate = matches[0]
        if candidate not in _TICKER_BLACKLIST:
            return candidate

    return _yfinance_ticker_search(query)


def _yfinance_ticker_search(query: str) -> str:
    """Last resort: yfinance search. Cached 1hr."""
    cache_key = f"search:{query[:50]}"
    if cache_key in _ticker_cache:
        return _ticker_cache[cache_key]

    try:
        clean = re.sub(
            r"(股价|股票|行情|走势|涨跌|价格|现在|多少|最近|当前|"
            r"stock|price|current|what|how|is|the|of|\?|？)",
            "", query, flags=re.IGNORECASE
        ).strip()

        if not clean or len(clean) < 2:
            return ""

        # P1-6: Skip if cleaned text is Chinese-only (no Latin characters)
        has_latin = any(
            unicodedata.category(c).startswith("L") and ord(c) < 0x4E00
            for c in clean
        )
        if not has_latin:
            _ticker_cache[cache_key] = ""
            return ""

        search_results = yf.Ticker(clean)
        info = search_results.info
        symbol = info.get("symbol", "")
        if symbol:
            logger.info("yfinance resolved '%s' → %s", clean, symbol)
            _ticker_cache[cache_key] = symbol
            return symbol
    except Exception:
        pass

    _ticker_cache[cache_key] = ""
    return ""


import re


def parse_time_window(query: str) -> dict:
    """解析时间窗口，支持相对时间和绝对时间。
    
    返回:
        {"mode": "relative", "period": "1mo"}
        {"mode": "absolute", "start": "2025-07-01", "end": "2025-09-30"}
    """
    q = query.lower()

    # ---- 绝对季度：2025年第四季度 / Q4 2025 / FY25 Q4 ----
    quarter_map = {"一": 1, "二": 2, "三": 3, "四": 4}
    quarter_dates = {
        1: ("01-01", "03-31"),
        2: ("04-01", "06-30"),
        3: ("07-01", "09-30"),
        4: ("10-01", "12-31"),
    }
    
    # 绝对日期：1月15日 / 2025年1月15日 / January 15
    m = re.search(r"(20\d{2})\s*年?\s*(\d{1,2})\s*月\s*(\d{1,2})\s*[日号]", q)
    if m:
        year, month, day = m.group(1), int(m.group(2)), int(m.group(3))
        # 取该日期前后各 7 天的数据
        from datetime import timedelta
        center = datetime(int(year), month, day)
        start = (center - timedelta(days=7)).strftime("%Y-%m-%d")
        end = (center + timedelta(days=7)).strftime("%Y-%m-%d")
        return {"mode": "absolute", "start": start, "end": end}

    # 无年份的日期：1月15日 → 默认今年
    m = re.search(r"(\d{1,2})\s*月\s*(\d{1,2})\s*[日号]", q)
    if m:
        month, day = int(m.group(1)), int(m.group(2))
        year = datetime.now().year
        from datetime import timedelta
        center = datetime(year, month, day)
        start = (center - timedelta(days=7)).strftime("%Y-%m-%d")
        end = (center + timedelta(days=7)).strftime("%Y-%m-%d")
        return {"mode": "absolute", "start": start, "end": end}

    # 中文：2025年第四季度 / 2025年Q4
    m = re.search(r"(20\d{2})\s*年?\s*第?([一二三四])\s*季", q)
    if m:
        year = m.group(1)
        quarter = quarter_map[m.group(2)]
        start, end = quarter_dates[quarter]
        return {"mode": "absolute", "start": f"{year}-{start}", "end": f"{year}-{end}"}

    # 英文：Q4 2025 / q4 2025
    m = re.search(r"[qQ]([1-4])\s*(20\d{2})", q)
    if m:
        quarter, year = int(m.group(1)), m.group(2)
        start, end = quarter_dates[quarter]
        return {"mode": "absolute", "start": f"{year}-{start}", "end": f"{year}-{end}"}

    # 英文：2025 Q4 / FY25 Q4 / FY2025 Q4
    m = re.search(r"(?:fy)?(20\d{2})\s*[qQ]([1-4])", q)
    if m:
        year, quarter = m.group(1), int(m.group(2))
        start, end = quarter_dates[quarter]
        return {"mode": "absolute", "start": f"{year}-{start}", "end": f"{year}-{end}"}

    # 绝对年份月份：2025年7月 / July 2025
    m = re.search(r"(20\d{2})\s*年?\s*(\d{1,2})\s*月", q)
    if m:
        year, month = m.group(1), int(m.group(2))
        import calendar
        last_day = calendar.monthrange(int(year), month)[1]
        return {"mode": "absolute", "start": f"{year}-{month:02d}-01", "end": f"{year}-{month:02d}-{last_day}"}

    # ---- 相对时间（原有逻辑）----
    if re.search(r"(7\s*[天日]|7\s*day|一周|本周|this week|last week|past week)", q):
        return {"mode": "relative", "period": "7d"}
    if re.search(r"(3\s*个?月|三个?月|3\s*month|quarter|季度)", q):
        return {"mode": "relative", "period": "3mo"}
    if re.search(r"(6\s*个?月|六个?月|6\s*month|半年)", q):
        return {"mode": "relative", "period": "6mo"}
    if re.search(r"(1?\s*年|一年|1\s*year|12\s*month|past year)", q):
        return {"mode": "relative", "period": "1y"}
    if re.search(r"(30\s*[天日]|30\s*day|一个?月|1\s*个?月|1\s*month|近期|最近|recently)", q):
        return {"mode": "relative", "period": "1mo"}

    return {"mode": "relative", "period": "1mo"}


_PERIOD_LABEL = {
    "7d": "近 7 天", "1mo": "近 1 个月", "3mo": "近 3 个月",
    "6mo": "近 6 个月", "1y": "近 1 年",
}


def _validate_stock_data(data: dict, ticker: str) -> list[str]:
    """Check for data anomalies. Returns list of warning strings."""
    warnings = []
    price = data.get("current_price", 0)
    if price <= 0:
        warnings.append(f"Current price is {price} — likely bad data")

    pct = data.get("period_change_pct", 0)
    if abs(pct) > 100:
        warnings.append(f"Period change is {pct}% — may include stock split or data error")

    mc = data.get("market_cap")
    if mc and mc < 0:
        warnings.append("Negative market cap — data error")

    # Negative P/E: not an error (unprofitable company) but needs context for LLM
    pe = data.get("pe_ratio_ttm")
    if pe is not None and pe < 0:
        data["pe_note"] = "亏损公司，市盈率为负 / Unprofitable — negative P/E not meaningful for valuation"
        warnings.append(f"Negative P/E ({pe}) — company is currently unprofitable")

    # Current price far exceeds 52w high → possible post-split data inconsistency
    high_52w = data.get("52w_high")
    if price > 0 and high_52w and price > high_52w * 1.5:
        warnings.append(f"Current price ({price}) exceeds 52w high ({high_52w}) by >50% — possible data inconsistency")

    # Info dict was empty → fundamentals unreliable
    if not data.get("sector") or data.get("sector") == "N/A":
        if not data.get("pe_ratio_ttm") and not data.get("market_cap"):
            warnings.append("Fundamentals data unavailable — Yahoo Finance returned incomplete info")

    # Daily data: check for >50% single-day jumps (likely stock split, not normal trading)
    daily = data.get("daily_data", [])
    for i in range(1, len(daily)):
        prev_close = daily[i-1].get("close", 0)
        curr_close = daily[i].get("close", 0)
        if prev_close > 0 and abs(curr_close - prev_close) / prev_close > 0.5:
            warnings.append(
                f"Large daily price jump on {daily[i]['date']}: "
                f"{prev_close}→{curr_close} ({abs(curr_close-prev_close)/prev_close*100:.0f}%) "
                f"— possible stock split or data error"
            )
            break  # One warning is enough

    return warnings


def get_stock_data(ticker: str, time_window: dict | None = None) -> dict:
    """Fetch + compute all market metrics. Supports relative and absolute time windows.
    
    Args:
        time_window: 
            {"mode": "relative", "period": "1mo"}
            {"mode": "absolute", "start": "2025-07-01", "end": "2025-09-30"}
            None → defaults to relative 1mo
    """
    if time_window is None:
        time_window = {"mode": "relative", "period": "1mo"}

    # Cache key
    if time_window["mode"] == "relative":
        ck = f"{ticker}:{time_window['period']}"
    else:
        ck = f"{ticker}:{time_window['start']}:{time_window['end']}"

    if ck in _cache:
        return _cache[ck]

    try:
        stock = yf.Ticker(ticker)
        try:
            info = stock.info or {}
            if not isinstance(info, dict):
                info = {}
        except Exception:
            info = {}

        # Fetch history based on mode
        if time_window["mode"] == "relative":
            period = time_window["period"]
            hist = stock.history(period=period)
            period_label = _PERIOD_LABEL.get(period, period)
        else:
            hist = stock.history(start=time_window["start"], end=time_window["end"])
            period_label = f"{time_window['start']} ~ {time_window['end']}"
            period = "custom"

        if hist.empty:
            return {"error": f"No data found for '{ticker}' in {period_label}."}

        latest = hist.iloc[-1]
        first = hist.iloc[0]
        current_price = round(float(latest["Close"]), 2)
        open_price = round(float(first["Close"]), 2)
        price_change = round(current_price - open_price, 2)
        pct_change = round((price_change / open_price) * 100, 2) if open_price else 0.0
        period_high = round(float(hist["High"].max()), 2)
        period_low = round(float(hist["Low"].min()), 2)
        avg_volume = int(hist["Volume"].mean())

        if pct_change > 5: trend = "明显上涨 (Strong uptrend)"
        elif pct_change > 1: trend = "小幅上涨 (Mild uptrend)"
        elif pct_change < -5: trend = "明显下跌 (Strong downtrend)"
        elif pct_change < -1: trend = "小幅下跌 (Mild downtrend)"
        else: trend = "横盘震荡 (Sideways)"

        recent = hist.tail(7)
        daily_data = []
        prev_close: float | None = None
        for date, row in recent.iterrows():
            close = round(float(row["Close"]), 2)
            day_change = round(close - prev_close, 2) if prev_close is not None else None
            day_pct = round((day_change / prev_close) * 100, 2) if prev_close is not None else None
            daily_data.append({
                "date": date.strftime("%Y-%m-%d"),
                "open": round(float(row["Open"]), 2),
                "high": round(float(row["High"]), 2),
                "low": round(float(row["Low"]), 2),
                "close": close,
                "volume": int(row["Volume"]),
                "day_change": day_change,
                "day_change_pct": day_pct,
            })
            prev_close = close

        result = {
            "ticker": ticker,
            "company_name": info.get("longName") or info.get("shortName") or ticker,
            "sector": info.get("sector", "N/A"),
            "industry": info.get("industry", "N/A"),
            "currency": info.get("currency", "USD"),
            "current_price": current_price,
            "previous_close": round(float(info.get("previousClose", 0)), 2),
            "period": period,
            "period_label": period_label,
            "period_start_price": open_price,
            "period_change": price_change,
            "period_change_pct": pct_change,
            "period_high": period_high,
            "period_low": period_low,
            "avg_volume": avg_volume,
            "trend": trend,
            "market_cap": info.get("marketCap"),
            "pe_ratio_ttm": info.get("trailingPE"),
            "pe_ratio_forward": info.get("forwardPE"),
            "eps_ttm": info.get("trailingEps"),
            "dividend_yield": info.get("dividendYield"),
            "52w_high": info.get("fiftyTwoWeekHigh"),
            "52w_low": info.get("fiftyTwoWeekLow"),
            "beta": info.get("beta"),
            "daily_data": daily_data,
            "data_source": "Yahoo Finance",
            "data_timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }

        warnings = _validate_stock_data(result, ticker)
        if warnings:
            result["data_warnings"] = warnings
            for w in warnings:
                logger.warning("Data anomaly for %s: %s", ticker, w)

        _cache[ck] = result
        return result
    except Exception as e:
        return {"error": f"Failed to fetch data for '{ticker}': {str(e)}"}