# Financial Asset QA System / 金融资产问答系统

> An AI-powered full-stack financial Q&A system with real-time market data, RAG knowledge retrieval, news-backed analysis, and persistent conversation memory.
>
> 基于大模型的全栈金融资产问答系统，集成实时行情数据、RAG 知识检索、新闻证据链分析与持久化会话记忆。

---

# English

## System Architecture

```
┌────────────────────────────────────────────────────────┐
│                    Next.js Frontend                    │
│       Chat UI · Markdown · SSE Streaming · Sources     │
└───────────────────────┬────────────────────────────────┘
                        │  POST /chat (SSE)
┌───────────────────────▼────────────────────────────────┐
│                    FastAPI Backend                     │
│  /chat · /health · /sessions CRUD · Rate Limiting      │
│  Dual-mode history: session_id → Redis/PG | inline     │
└──────┬────────────────┬──────────────────┬─────────────┘
       │                │                  │
  ┌────▼─────┐   ┌──────▼──────┐    ┌─────▼───────────┐
  │  Redis   │   │ PostgreSQL  │    │  Query Router   │
  │ Session  │   │  Messages   │    │ Tier 0: regex   │
  │  Cache   │   │ Persistent  │    │  (multi-ticker) │
  │ 30m TTL  │   │   History   │    │ Tier 1: regex   │
  └──────────┘   └─────────────┘    │  (single-agent) │
                                    │ Tier 2: Claude  │
                                    │  (tool_use)     │
                                    └─────┬───────────┘
                           ┌──────────────┼──────────────┐
                           │              │              │
                    ┌──────▼───┐   ┌──────▼───┐   ┌─────▼─────┐
                    │  Market  │   │   RAG    │   │  General  │
                    │  Agent   │   │  Agent   │   │   Agent   │
                    └──┬───┬───┘   └──┬───┬───┘   └───────────┘
                       │   │          │   │
                 ┌─────▼┐ ┌▼─────┐ ┌──▼──┐┌▼────────┐
                 │Yahoo │ │ News │ │Vec- ││ Market  │
                 │ Fin. │ │ Svc  │ │ tor ││Enrichmt │
                 └──────┘ └──────┘ └─────┘└─────────┘
                           │
                    ┌──────▼───────┐
                    │  Supervisor  │
                    │  (compound)  │
                    │ Parallel I/O │
                    │ → Synthesizer│
                    └──────────────┘
```

## Tech Stack

| Layer | Technology | Rationale |
|-------|-----------|-----------|
| Frontend | Next.js 14 + TypeScript + Tailwind CSS | App Router for API proxy, SSE streaming, type safety |
| Backend | FastAPI (Python, async) | Streaming SSE, rich financial ecosystem, async-first |
| LLM | Claude API (Anthropic) | Native tool_use for routing, structured output, streaming |
| Vector DB | ChromaDB (embedded) | Zero-config, cosine similarity with relevance threshold |
| Embeddings | paraphrase-multilingual-MiniLM-L12-v2 | Multilingual (Chinese+English), 384-dim, local |
| Market Data | Yahoo Finance (yfinance) | Free, no API key required, global coverage |
| News | Yahoo Finance news endpoint | Headlines as evidence for price movement analysis |
| Session DB | PostgreSQL 16 (SQLAlchemy async) | Persistent chat history, full message metadata |
| Session Cache | Redis 7 | Hot-path context loading, 30-min TTL per session |
| Market Cache | cachetools TTLCache | In-process, 5-min TTL for market data, 10-min for news |

## Core Design Decisions

### 1. Two-Tier Query Router
Tier 1 (instant, free): regex pre-filter catches obvious patterns — ticker + market keywords → `market_data`, financial concept keywords → `knowledge`, ticker + knowledge keywords → `knowledge` with ticker context. Saves ~1-2s and one API call per hit.
Tier 2 (fallback): Claude `tool_use` for ambiguous queries that don't match any pattern.

### 2. Backend Computes, LLM Explains
All numeric metrics are pre-computed in `market_data.py`: period change, percentage, high/low, average volume, trend classification (5 levels). The LLM receives these pre-computed values and only explains them. It never performs arithmetic — eliminating hallucinated numbers. Data anomaly checks flag `price=0`, `change>±100%`, `negative market cap`, `negative P/E` (with explanatory note for unprofitable companies), `current price exceeding 52-week high by >50%`, `empty fundamentals from Yahoo API`, and `>50% single-day price jumps` (likely stock splits).

### 3. Time Window Parsing
The backend extracts explicit time windows from natural language queries using regex:
- "7天" / "一周" / "this week" → `7d`
- "最近" / "30天" / "一个月" → `1mo`
- "季度" / "3个月" → `3mo`
- "半年" → `6mo`
- "一年" → `1y`

### 4. News as Time-Aware Evidence
`news_service.py` fetches headlines filtered by time window (7d query → last 7 days of news). Returns transparent status: `ok` (with articles), `no_news` (no articles found), or `error` (service unavailable) — so the LLM never invents reasons.

### 5. RAG with Multilingual Embeddings
Uses `paraphrase-multilingual-MiniLM-L12-v2` for Chinese+English. Vector search returns results with cosine distance filtered at threshold 0.9. Dynamic `n_results`: definition queries get 2 docs (less noise), analysis queries get 5 (broader coverage). Long documents are auto-chunked with overlap.

### 6. Dual-Mode History with Auto-Session Creation
The `/chat` endpoint auto-creates a session when no `session_id` is provided. The frontend receives the new `session_id` in the first SSE event and uses it for subsequent messages.
Two modes:
- **Session mode** (auto or explicit): loads history from Redis (hot) → PostgreSQL (cold), auto-persists.
- **Stateless mode** (PostgreSQL unavailable): uses inline history directly. No DB required.

### 7. Atomic Message Persistence
`add_message_pair()` inserts user + assistant messages in a single PostgreSQL transaction — if either fails, both roll back. Redis cache uses `append_pair_to_context()` which writes both messages in one GET→modify→SET cycle. PG and Redis errors are logged separately.

### 8. History Windowing Strategy
PostgreSQL stores the **complete** conversation history with no limit. Redis caches the last 10 messages per session (30-min TTL). Before each LLM call, `truncate_history()` trims to the last 6 messages (3 turns) and ensures the first message is `user` role — this is the sliding window the model actually sees.

### 9. Adaptive Response Templates
Market queries are classified as `simple` (price lookup → concise 3-5 sentence response) or `detailed` (trend analysis → full 6-section briefing). Classification comes from the router's `query_complexity` field. The regex pre-filter also infers complexity via keyword matching: price-lookup patterns (多少钱/stock price/how much) → `simple`, analysis patterns (分析/走势/为什么/compare) → `detailed`. This ensures the 60-70% of queries handled by regex get appropriate response length without waiting for Claude routing.

### 10. Source Provenance
Every response includes a `sources` SSE event containing market data source, news citations (with status), or knowledge base documents used — rendered in the frontend as a "Data sources" section.

### 11. Automatic Language Matching
All prompts include a `LANGUAGE RULE` that instructs Claude to detect the user's language and respond entirely in that language — including section headers, labels, and analysis text.

### 12. Security & Input Validation
- CORS: `allow_credentials=False` with `allow_origins=["*"]` for dev. Production uses `ALLOWED_ORIGINS` env var with specific domains + credentials. Trailing-comma safe parsing.
- Rate limiting via `slowapi`: 15 requests/min per IP on `/chat`, 60 requests/min on `/sessions`. Returns 429 when exceeded. Uses in-memory storage (swap to Redis for multi-worker).
- Message length capped at 4000 characters.
- Ticker regex filtered through a 40-term blacklist (PE, ETF, IPO, GDP, etc.) to prevent false matches.
- Database role/category fields validated at application layer before insert.

### 13. Parallel Market Data Fetching
`market_agent.py` fetches stock data and news concurrently via `asyncio.gather(asyncio.to_thread(...))`. This saves 0.5-1s per request and also fixes an event loop blocking issue (yfinance is synchronous).

### 14. Hybrid Search (Keywords + Vectors)
RAG queries extract financial keywords (市盈率, PE, revenue, etc.) and pass them as a `where_document: {"$contains": ...}` filter to ChromaDB. This narrows the candidate set before vector matching, improving precision for terminology-heavy queries.

### 15. Cross-Encoder Reranker
For `detailed` queries, the top candidates from bi-encoder search are re-scored with a multilingual cross-encoder (`cross-encoder/ms-marco-multilingual-MiniLM-L12-v2`). The reranker is lazy-loaded and gracefully degrades if unavailable. Simple queries skip reranking for speed.

### 16. Confidence-Aware Answer Strategy
The system selects different prompt tiers based on retrieval quality:
- **High** (relevance > 0.5): "Documents are HIGHLY RELEVANT — base answer on them."
- **Medium** (0.2-0.5): "Documents are MODERATELY relevant — supplement freely."
- **Low** (< 0.2): "KB has no match — answer from general knowledge with disclaimer."

### 17. Streaming Status Events
SSE events include `{"type":"status"}` messages between processing steps (e.g. "Fetching BABA market data...", "Searching knowledge base...", "Found 3 docs (confidence: high)..."). The frontend displays these as italic gray text while waiting for LLM output to begin.

### 18. Multi-Format Document Ingestion
File parsers for PDF (PyMuPDF, section splitting), CSV (row-group chunking with column headers), and DOCX (heading-based sections, table→markdown). Metadata (category, entity, topic) is inferred from file paths and content. Type-aware chunk sizes: PDF/DOCX 500 chars, CSV 600 chars (no overlap), JSON 300 chars. CSV parser auto-detects file encoding via `charset-normalizer` (handles GBK/GB2312/GB18030 from Chinese Windows systems). Files that produce no output (scanned PDFs, empty CSVs) are tracked and reported during ingestion.

### 19. Incremental Ingest with Garbage Collection
Content-hash tracking enables incremental updates — only changed/new documents are re-embedded. `--gc-only` mode removes vectors whose source files have been deleted. `--force` rebuilds the entire index.

### 20. Dynamic Temperature Control
Different query types need different creativity levels. `llm.py` exposes a `temperature` parameter on both `chat_completion` (default 0.0 for deterministic routing) and `stream_completion`. Each agent uses a tailored value:
- **Market agent (0.2):** Almost no deviation from pre-computed data. The model should explain, not improvise.
- **RAG agent (0.3):** Slight wording variation allowed, but no fabrication beyond retrieved documents.
- **General agent (0.6):** More natural conversational style for open-ended questions.

### 21. Hybrid RAG Enrichment
When a knowledge query includes a recognized ticker (e.g. "苹果市盈率是多少" → knowledge + AAPL), the RAG agent optionally fetches real-time market data via `asyncio.to_thread(get_stock_data)` and appends it as a `<realtime_market_data>` XML block in the prompt. The RAG system prompt (HIGH/MEDIUM tiers) instructs the model to use these values as concrete examples when explaining concepts. Enrichment is try/except guarded — yfinance failure silently degrades to knowledge-only response.

## Prompt Design

| Prompt | Strategy |
|--------|----------|
| **Router** | Defines 3 categories + `query_complexity` (simple/detailed). Forces structured output via `classify_query` tool schema. Temperature=0.0 for deterministic classification. |
| **Market (simple)** | Concise 3-5 sentence template for price lookups. Temperature=0.2. |
| **Market (detailed)** | Full 6-section template with explicit no-data fallback rules: no news → fixed statement (no speculation allowed), insufficient daily data → declare data insufficient rather than draw conclusions. Temperature=0.2. |
| **RAG (high confidence)** | "Documents HIGHLY RELEVANT" — cite docs, minimal supplementation. Temperature=0.3. |
| **RAG (medium confidence)** | "Documents MODERATELY relevant" — use docs where applicable, supplement freely. Temperature=0.3. |
| **RAG (low confidence)** | "KB has no match" — general knowledge with disclaimer. Temperature=0.3. |

## Data Sources

| Source | Usage | Cache TTL |
|--------|-------|-----------|
| Yahoo Finance (prices) | Stock prices, fundamentals, historical data | 5 min |
| Yahoo Finance (news) | Recent headlines for evidence-based analysis | 10 min |
| ChromaDB Knowledge Base | 20 financial knowledge docs, 12 topics | Static |
| PostgreSQL | Full chat history, message metadata | Persistent |
| Redis | Session context (last 10 messages) | 30 min |

### Knowledge Base (20 documents)

**Concepts (12):** P/E ratio, market cap, revenue vs. profit, EPS, dividends, ETF, bull/bear markets, moving averages, options basics, bonds, inflation & interest rates, short selling

**Analysis (8):** Balance sheet, income statement, cash flow, valuation methods, risk management, reading earnings reports, sector rotation, technical patterns

## API Reference

### Health
```
GET /health
→ { status, knowledge_base_docs, api_key_configured, postgres_connected, redis_connected }
```

### Sessions
```
POST   /sessions              → Create session   → { id, title, created_at }
GET    /sessions?limit=20     → List sessions     → [{ id, title, created_at, updated_at }]
                                 (limit capped at 100)
GET    /sessions/{id}         → Get with messages  → { id, title, messages: [...] }
DELETE /sessions/{id}         → Delete + clear cache → { deleted: true }
```

### Chat (SSE Stream)
```
POST /chat
Body: { message: string, session_id?: string, history?: [{role, content}] }

- If session_id is omitted, a new session is auto-created.
- The new/existing session_id is returned in the routing event.
- Frontend should capture it and pass it in subsequent messages.

SSE Events:
  data: {"type":"routing",  "category":"market_data", "ticker":"BABA", "session_id":"..."}
  data: {"type":"status",   "content":"Fetching BABA market data & news..."}
  data: {"type":"text",     "content":"## 📊 Alibaba ..."}
  data: {"type":"text",     "content":"Current price..."}
  ... (streaming chunks)
  data: {"type":"sources",  "content":{"market_data":{...}, "news":[...]}}
  data: {"type":"done"}
```

## Database Schema

```sql
-- sessions: conversation containers
CREATE TABLE sessions (
    id          VARCHAR(36) PRIMARY KEY,  -- UUID
    title       VARCHAR(200) DEFAULT 'New chat',
    created_at  TIMESTAMP DEFAULT NOW(),
    updated_at  TIMESTAMP DEFAULT NOW()
);

-- messages: individual chat messages
CREATE TABLE messages (
    id                SERIAL PRIMARY KEY,
    session_id        VARCHAR(36) REFERENCES sessions(id) ON DELETE CASCADE,
    role              VARCHAR(20) NOT NULL,   -- validated: 'user' | 'assistant'
    content           TEXT NOT NULL,
    routing_category  VARCHAR(20),            -- validated: 'market_data' | 'knowledge' | 'general'
    routing_ticker    VARCHAR(20),
    sources           JSONB,                  -- raw source metadata
    created_at        TIMESTAMP DEFAULT NOW()
);
-- Composite index: covers both "all messages for session" and "recent N for session"
CREATE INDEX ix_messages_session_created ON messages(session_id, created_at);
```

```
Redis key pattern:
  session:{session_id}:context  →  JSON array of {role, content}  (TTL: 30 min)
```

## Prerequisites

- **Docker Desktop** (for PostgreSQL + Redis)
- **Python 3.10–3.12** (3.13+ has dependency issues with numpy/onnxruntime; 3.12 recommended)
- **Node.js 18+**

## Quick Start

```bash
# 1. Start infrastructure
docker compose up -d         # PostgreSQL 16 + Redis 7 (note: no hyphen)

# 2. Backend
cd backend
python3.12 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
# First run downloads embedding model (~471MB) and reranker model (~80MB)

cp .env.example .env        # Fill in ANTHROPIC_API_KEY (see below)
python -m knowledge.ingest  # Load 20 docs into ChromaDB
uvicorn main:app --port 8000
# For development with auto-reload:
# uvicorn main:app --reload --reload-exclude venv --port 8000

# 3. Frontend
cd frontend
npm install
npm run dev                 # → http://localhost:3000

# 4. Verify
curl http://localhost:8000/health
# Should return: postgres_connected: true, redis_connected: true, knowledge_base_docs: ~20
```

### `.env.example`

```env
ANTHROPIC_API_KEY=your-api-key-here
DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5432/financial_qa
REDIS_URL=redis://localhost:6379/0
LLM_MODEL=claude-sonnet-4-20250514
MARKET_CACHE_TTL_SECONDS=300
NEWS_CACHE_TTL_SECONDS=600
SESSION_CONTEXT_TTL=1800
# Production only:
# ALLOWED_ORIGINS=https://your-domain.com
```

### Troubleshooting

| Issue | Fix |
|-------|-----|
| `pymupdf` build fails | Path has spaces or Python too new. Use Python 3.12; avoid spaces in project path |
| `onnxruntime not found` | Python 3.13+ not supported. Downgrade to 3.12 |
| `np.float_ removed` | Run `pip install "numpy<2"` then retry |
| ChromaDB telemetry warnings | Harmless. Suppress with `export ANONYMIZED_TELEMETRY=False` |
| uvicorn keeps reloading | Use `--reload-exclude venv` or run without `--reload` |
| yfinance 429 Too Many Requests | Wait 10-15 min (Yahoo rate limit). Don't rapid-fire queries |
| `docker-compose` not found | Use `docker compose` (no hyphen) with Docker Desktop v2+ |

## Error Handling

| Scenario | Behavior |
|----------|----------|
| API key missing | Startup warning + `/chat` returns error before routing |
| PostgreSQL down | Startup warning, session endpoints return 500, `/chat` still works with inline history |
| Redis down | Startup warning, context loading falls back to PostgreSQL directly |
| Knowledge base empty | Startup warning, RAG falls back to general knowledge with disclaimer |
| Ticker unrecognized | Returns example tickers in Chinese + English |
| Market data fetch fails | Returns specific error with ticker name |
| LLM call fails | Caught per-agent, error surfaced to frontend via SSE |
| News service fails | Silently degrades; market analysis proceeds without news |
| Message persistence fails | Non-fatal; logs error but doesn't break the stream |

## Project Structure

```
financial-qa-system/
├── docker-compose.yml                  # PostgreSQL 16 + Redis 7 (AOF persistence)
├── backend/
│   ├── main.py                         # FastAPI: /chat, /health, /sessions CRUD
│   ├── requirements.txt
│   ├── .env.example                    # All config vars incl. ALLOWED_ORIGINS
│   ├── agents/
│   │   ├── router.py                   # Regex pre-filter + complexity inference + Claude fallback
│   │   ├── market_agent.py             # Parallel fetch + streaming status + dual template
│   │   ├── rag_agent.py               # Hybrid search + metadata filter + confidence-aware + enrichment
│   │   └── general_agent.py           # General conversation (symmetric interface)
│   ├── services/
│   │   ├── llm.py                      # Singleton Claude client, unified LLMError, dynamic temperature
│   │   ├── market_data.py              # 88 ticker mappings, blacklist, expanded data validation
│   │   ├── news_service.py             # UTC time-filtered news, transparent status, env-configurable TTL
│   │   ├── vector_store.py             # Hybrid search, reranker, parent dedup, GC
│   │   ├── database.py                 # Composite index, atomic add_message_pair()
│   │   └── session_cache.py            # Redis cache, append_pair_to_context()
│   ├── prompts/
│   │   ├── router.py                   # Classification + query_complexity extraction
│   │   ├── market_analysis.py          # Dual template (simple/detailed), language-adaptive
│   │   └── rag_response.py             # Confidence-aware tiers, prompt length control
│   └── knowledge/
│       ├── parsers/                    # PDF, CSV, DOCX file parsers
│       │   ├── pdf_parser.py           # PyMuPDF: section splitting, topic inference
│       │   ├── csv_parser.py           # Row-group chunking with column headers
│       │   └── docx_parser.py          # Heading-based sections, table→markdown
│       ├── docs/
│       │   ├── seed_knowledge.json     # 20 hand-written docs (12 concepts + 8 analysis)
│       │   ├── pdf/                    # Place PDF files here for ingestion
│       │   ├── csv/                    # Place CSV/TSV files here
│       │   └── docx/                   # Place DOCX files here
│       └── ingest.py                   # Multi-format, incremental, GC, progress
└── frontend/
    ├── app/
    │   ├── layout.tsx, page.tsx, globals.css
    ├── components/
    │   ├── ChatWindow.tsx              # Chat UI + session_id capture + status display + loading skeleton
    │   ├── SessionSidebar.tsx          # Session list, new chat, switch, delete
    │   ├── MessageBubble.tsx           # Markdown + status text + sources
    │   ├── RoutingBadge.tsx            # Agent type indicator
    │   ├── SourcesDisplay.tsx          # Data provenance display (relevance-clamped)
    │   ├── ErrorBoundary.tsx           # React error boundary for render failures
    │   └── Suggestions.tsx             # Quick query chips
    └── lib/
        └── api.ts                      # SSE client (routing/status/text/sources) + session CRUD + apiFetch
```

---

# 中文

## 系统架构

```
┌──────────────────────────────────────────────────────┐
│                    Next.js 前端                      ｜
│        聊天界面 · Markdown 渲染 · SSE 流式 · 来源展示    │
└───────────────────────┬──────────────────────────────┘
                        │  POST /chat (SSE)
┌───────────────────────▼──────────────────────────────┐
│                    FastAPI 后端                       │
│  /chat · /health · /sessions 增删查 · 频率限制          │
│  双模式历史: session_id → Redis/PG | 内联 history       │
└──────┬────────────────┬──────────────────┬───────────┘
       │                │                  │
  ┌────▼─────┐   ┌──────▼──────┐    ┌─────▼──────────┐
  │  Redis   │   │ PostgreSQL  │    │    查询路由      │
  │ 会话缓存  │   │  消息持久化  │    │ Tier 0: regex    │
  │ 30分钟TTL │   │    完整历史  │    │  (多ticker检测)  │
  └──────────┘   └─────────────┘    │ Tier 1: regex   │
                                    │  (单agent分类)   │
                                    │ Tier 2: Claude  │
                                    │  (tool_use兜底)  │
                                    └─────┬──────────┘
                           ┌──────────────┼──────────────┐
                           │              │              │
                    ┌──────▼───┐   ┌──────▼───┐   ┌─────▼────┐
                    │ 行情Agent │   │ RAG Agent │   │通用Agent │
                    └──┬───┬───┘   └──┬───┬────┘   └──────────┘
                       │   │          │   │
                 ┌─────▼┐ ┌▼────┐ ┌──▼──┐┌▼──────┐
                 │Yahoo │ │新闻  │ │向量  ││实时数据│
                 │Fin.  │ │服务  │ │检索  ││  补充  │
                 └──────┘ └─────┘ └─────┘└───────┘
                           │
                    ┌──────▼──────┐
                    │  Supervisor │
                    │ (复合查询)   │
                    │ 并行收集数据  │
                    │ → 合成器LLM  │
                    └─────────────┘
```

## 技术栈

| 层级 | 技术 | 选型理由 |
|------|------|---------|
| 前端 | Next.js 14 + TypeScript + Tailwind CSS | App Router 做 API 代理，SSE 流式传输，类型安全 |
| 后端 | FastAPI (Python, async) | 流式 SSE，丰富的金融 Python 生态，异步优先 |
| 大模型 | Claude API (Anthropic) | 原生 tool_use 做路由，结构化输出，流式响应 |
| 向量库 | ChromaDB（嵌入式） | 零配置，余弦相似度，相关性阈值过滤 |
| 嵌入模型 | paraphrase-multilingual-MiniLM-L12-v2 | 多语言（中英双语）、384 维、本地推理 |
| 行情数据 | Yahoo Finance (yfinance) | 免费、无需 API Key、全球覆盖 |
| 新闻 | Yahoo Finance 新闻接口 | 作为价格波动分析的证据链 |
| 会话数据库 | PostgreSQL 16 (SQLAlchemy async) | 持久化聊天记录，完整消息元数据 |
| 会话缓存 | Redis 7 | 热路径上下文加载，每会话 30 分钟 TTL |
| 行情缓存 | cachetools TTLCache | 进程内缓存，行情 5 分钟 TTL，新闻 10 分钟 |

## 核心设计决策

### 1. 双层查询路由
第一层（即时、免费）：正则预过滤捕获明显模式——ticker + 行情关键词 → `market_data`，金融概念关键词 → `knowledge`，ticker + 知识关键词 → `knowledge`（附带 ticker 上下文）。节省 ~1-2 秒和一次 API 调用。
第二层（兜底）：Claude `tool_use` 处理模糊查询。

### 2. 后端计算，大模型解释
所有数值指标在 `market_data.py` 中预先计算：涨跌额、涨跌幅、最高/最低、平均成交量、趋势分级（5级）。大模型只负责解释，绝不做算术。数据异常检查会标记 `price=0`、`change>±100%`、`负市值`、`负市盈率`（附亏损公司说明）、`当前价大幅偏离52周高点`、`基本面数据缺失`、`单日跳变>50%`（疑似拆股）。

### 3. 时间窗口智能解析
后端通过正则表达式从自然语言中提取时间窗口：
- "7天" / "一周" / "this week" → `7d`
- "最近" / "30天" / "一个月" → `1mo`
- "季度" / "3个月" → `3mo`
- "半年" → `6mo`
- "一年" → `1y`

### 4. 新闻作为时间关联证据
`news_service.py` 按时间窗口过滤新闻（7天查询 → 仅取7天内新闻）。返回透明状态：`ok`（有新闻）、`no_news`（无相关新闻）、`error`（服务不可用）——大模型不会凭空编造原因。

### 5. 多语言 RAG + 动态检索
使用 `paraphrase-multilingual-MiniLM-L12-v2` 支持中英双语。余弦距离阈值 0.9 过滤低相关文档。动态 `n_results`：定义类查询取 2 篇（减少噪音），分析类查询取 5 篇（广覆盖）。长文档自动分块。

### 6. 双模式历史 + 自动建会话
`/chat` 接口在没有 `session_id` 时自动创建会话。前端从 routing 事件获取新 `session_id`，后续消息都带上它。
- **会话模式**：从 Redis（热）→ PostgreSQL（冷）加载历史，自动持久化。
- **无状态模式**（PG 不可用）：直接用内联 history。

### 7. 原子消息持久化
`add_message_pair()` 在一个 PostgreSQL 事务中插入 user + assistant 两条消息——任一失败则全部回滚。Redis 缓存使用 `append_pair_to_context()` 在一次 GET→修改→SET 中写入双条。PG 和 Redis 错误分开记录。

### 8. 历史窗口策略
PostgreSQL 存储 **完整的** 会话历史。Redis 缓存最近 10 条（30 分钟 TTL）。`truncate_history()` 裁剪到最近 6 条（3 轮）并确保首条为 user 角色——这是模型看到的滑动窗口。

### 9. 自适应回答模板
行情查询分为 `simple`（价格查询 → 3-5 句精简回答）和 `detailed`（走势分析 → 完整 6 段报告）。由路由的 `query_complexity` 字段决定。正则预过滤也通过关键词匹配推断复杂度：价格查询模式（多少钱/stock price/how much）→ `simple`，分析模式（分析/走势/为什么/compare）→ `detailed`。确保被 regex 命中的 60-70% 查询也能获得合适的响应长度。

### 10. 数据来源展示
每条回复的 `sources` SSE 事件携带行情来源、新闻引用（含状态）或知识库文档信息，前端渲染为"数据来源"区块。

### 11. 自动语言匹配
所有 Prompt 含 `LANGUAGE RULE`，Claude 自动检测用户语言并以该语言输出全部内容。

### 12. 安全与输入校验
- CORS：开发环境 `credentials=False` + `origins=["*"]`。生产环境通过 `ALLOWED_ORIGINS` 指定域名。解析时自动过滤尾部逗号和空格。
- 频率限制（`slowapi`）：每 IP 每分钟 15 次 `/chat`、60 次 `/sessions`。超限返回 429。使用内存存储（多 Worker 部署时切换为 Redis）。
- 消息长度上限 4000 字符。
- Ticker 正则匹配经过 40 个金融术语黑名单过滤（PE/ETF/IPO/GDP 等）。
- 数据库 role/category 字段在应用层校验后才写入。

### 13. 并行行情数据获取
`market_agent.py` 通过 `asyncio.gather(asyncio.to_thread(...))` 并行获取股价和新闻，每次请求节省 0.5-1 秒，同时修复了 yfinance 同步调用阻塞 event loop 的问题。

### 14. 混合检索（关键词 + 向量）
RAG 查询从问题中提取金融关键词（市盈率/PE/revenue 等），通过 ChromaDB 的 `where_document: {"$contains": ...}` 过滤缩小候选集，然后在子集内做向量匹配，提高术语密集型查询的精度。

### 15. Cross-Encoder 重排序
`detailed` 查询的 bi-encoder 检索结果会用多语言 cross-encoder（`ms-marco-multilingual-MiniLM-L12-v2`）重新打分。懒加载，不可用时优雅降级。`simple` 查询跳过重排序以加快速度。

### 16. 置信度感知回答策略
根据检索质量选择不同 prompt 层级：
- **高置信**（relevance > 0.5）："文档高度相关——以知识库为主"
- **中置信**（0.2-0.5）："文档部分相关——可自由补充通用知识"
- **低置信**（< 0.2）："知识库无匹配——用通用知识回答并附免责声明"

### 17. 流式状态提示
SSE 事件流中穿插 `{"type":"status"}` 消息（如"正在获取 BABA 行情数据..."、"找到 3 篇相关文档（置信度: 高），正在生成回答..."）。前端在 LLM 输出前以灰色斜体展示。

### 18. 多格式文档导入
文件解析器支持 PDF（PyMuPDF，按标题切段）、CSV（按行组分块，列名前缀）、DOCX（按 Heading 样式切段，表格转 markdown）。从文件路径和内容推断 metadata（category/entity/topic）。按文档类型自适应 chunk 大小：PDF/DOCX 500 字符、CSV 600 字符（无重叠）、JSON 300 字符。CSV 解析器通过 `charset-normalizer` 自动检测文件编码（支持中国 Windows 系统的 GBK/GB2312/GB18030）。无输出的文件（扫描件 PDF、空 CSV）在导入时统计并报告。

### 19. 增量导入与垃圾回收
内容哈希追踪实现增量更新——仅重新 embed 变更/新增的文档。`--gc-only` 模式删除已被移除文件的孤儿向量。`--force` 全量重建索引。

### 20. 动态 Temperature 控制
不同查询类型需要不同的创造力水平。`llm.py` 的 `chat_completion`（默认 0.0，确定性路由分类）和 `stream_completion` 均支持 `temperature` 参数。各 Agent 使用差异化配置：
- **行情 Agent（0.2）：** 几乎不允许偏离预计算数据，模型只解释不发挥。
- **RAG Agent（0.3）：** 允许轻微措辞变化，但不允许编造超出检索文档的内容。
- **通用 Agent（0.6）：** 更自然的对话风格，适合开放式问题。

### 21. 知识 + 行情交叉引用
当知识查询包含已识别的 ticker（如"苹果市盈率是多少"→ knowledge + AAPL）时，RAG Agent 通过 `asyncio.to_thread(get_stock_data)` 获取实时行情数据，以 `<realtime_market_data>` XML 标签附加到 prompt 中。RAG system prompt（高/中置信档）指示模型在解释概念时引用实际数值作为示例。Enrichment 用 try/except 保护——yfinance 失败时静默降级为纯知识库回答。

## Prompt 设计思路

| Prompt | 策略 |
|--------|------|
| **路由** | 定义 3 个类别 + `query_complexity`（simple/detailed）。工具 schema 强制结构化输出。temperature=0.0 |
| **行情（精简）** | 3-5 句模板，仅回答价格查询。temperature=0.2 |
| **行情（完整）** | 6 段模板，含显式无数据 fallback 规则：无新闻→固定措辞（禁止推测），数据点不足→声明数据不足。temperature=0.2 |
| **RAG（高置信）** | "文档高度相关"——引用文档，少量补充。temperature=0.3 |
| **RAG（中置信）** | "文档部分相关"——自由补充通用知识。temperature=0.3 |
| **RAG（低置信）** | "知识库无匹配"——通用知识 + 免责声明。temperature=0.3 |

## 数据来源

| 来源 | 用途 | 缓存 TTL |
|------|------|----------|
| Yahoo Finance（价格） | 股价、基本面、历史数据 | 5 分钟 |
| Yahoo Finance（新闻） | 近期新闻标题作为分析证据 | 10 分钟 |
| ChromaDB 知识库 | 20 篇金融知识文档，12 个主题 | 静态 |
| PostgreSQL | 完整聊天记录，消息元数据 | 持久化 |
| Redis | 会话上下文（最近 10 条消息） | 30 分钟 |

### 知识库内容（20 篇文档）

**概念类（12 篇）：** 市盈率、市值、收入与净利润、每股收益、股息、ETF、牛熊市、移动平均线、期权基础、债券、通胀与利率、做空

**分析类（8 篇）：** 资产负债表分析、利润表分析、现金流分析、估值方法、风险管理、财报阅读、行业轮动、技术形态

## API 接口

### 健康检查
```
GET /health
→ { status, knowledge_base_docs, api_key_configured, postgres_connected, redis_connected }
```

### 会话管理
```
POST   /sessions              → 创建会话   → { id, title, created_at }
GET    /sessions?limit=20     → 会话列表   → [{ id, title, created_at, updated_at }]
                                 (limit 上限 100)
GET    /sessions/{id}         → 获取详情   → { id, title, messages: [...] }
DELETE /sessions/{id}         → 删除会话   → { deleted: true }
```

### 聊天（SSE 流式）
```
POST /chat
Body: { message: string, session_id?: string, history?: [{role, content}] }

- 不传 session_id 时自动创建新会话。
- 新建/已有的 session_id 在 routing 事件中返回。
- 前端应捕获该 id 并在后续消息中传回。

SSE 事件流:
  data: {"type":"routing",  "category":"market_data", "ticker":"BABA", "session_id":"..."}
  data: {"type":"status",   "content":"正在获取 BABA 行情数据与新闻..."}
  data: {"type":"text",     "content":"## 📊 阿里巴巴..."}
  data: {"type":"text",     "content":"当前价格..."}
  ... (流式文本块)
  data: {"type":"sources",  "content":{"market_data":{...}, "news":[...]}}
  data: {"type":"done"}
```

## 数据库 Schema

```sql
-- sessions: 会话容器
CREATE TABLE sessions (
    id          VARCHAR(36) PRIMARY KEY,  -- UUID
    title       VARCHAR(200) DEFAULT 'New chat',
    created_at  TIMESTAMP DEFAULT NOW(),
    updated_at  TIMESTAMP DEFAULT NOW()    -- 每次新消息自动更新
);

-- messages: 单条消息
CREATE TABLE messages (
    id                SERIAL PRIMARY KEY,
    session_id        VARCHAR(36) REFERENCES sessions(id) ON DELETE CASCADE,
    role              VARCHAR(20) NOT NULL,   -- 应用层校验: 'user' | 'assistant'
    content           TEXT NOT NULL,
    routing_category  VARCHAR(20),            -- 应用层校验: 'market_data' | 'knowledge' | 'general'
    routing_ticker    VARCHAR(20),
    sources           JSONB,                  -- 原始来源元数据
    created_at        TIMESTAMP DEFAULT NOW()
);
-- 组合索引: 覆盖 "按会话获取全部消息" 和 "按会话获取最近N条" 两种查询模式
CREATE INDEX ix_messages_session_created ON messages(session_id, created_at);
```

```
Redis 键模式:
  session:{session_id}:context  →  JSON 数组 [{role, content}]  (TTL: 30 分钟)
```

## 环境要求

- **Docker Desktop**（运行 PostgreSQL + Redis）
- **Python 3.10–3.12**（3.13+ 存在 numpy/onnxruntime 依赖问题；推荐 3.12）
- **Node.js 18+**

## 快速启动

```bash
# 1. 启动基础设施
docker compose up -d         # PostgreSQL 16 + Redis 7（注意：没有连字符）

# 2. 后端
cd backend
python3.12 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
# 首次运行会下载嵌入模型（~471MB）和重排序模型（~80MB）

cp .env.example .env        # 填入 ANTHROPIC_API_KEY（见下方）
python -m knowledge.ingest  # 导入 20 篇金融知识到 ChromaDB
uvicorn main:app --port 8000
# 开发时如需自动重载：
# uvicorn main:app --reload --reload-exclude venv --port 8000

# 3. 前端
cd frontend
npm install
npm run dev                 # → http://localhost:3000

# 4. 验证
curl http://localhost:8000/health
# 应返回: postgres_connected: true, redis_connected: true, knowledge_base_docs: ~20
```

### `.env.example` 配置

```env
ANTHROPIC_API_KEY=your-api-key-here
DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5432/financial_qa
REDIS_URL=redis://localhost:6379/0
LLM_MODEL=claude-sonnet-4-20250514
MARKET_CACHE_TTL_SECONDS=300
NEWS_CACHE_TTL_SECONDS=600
SESSION_CONTEXT_TTL=1800
# 仅生产环境:
# ALLOWED_ORIGINS=https://your-domain.com
```

### 常见问题

| 问题 | 解决方案 |
|------|---------|
| `pymupdf` 编译失败 | 项目路径含空格或 Python 版本过新。使用 Python 3.12；避免路径中有空格 |
| 找不到 `onnxruntime` | Python 3.13+ 暂不支持，请降级到 3.12 |
| `np.float_ removed` 错误 | 执行 `pip install "numpy<2"` 后重试 |
| ChromaDB telemetry 警告 | 无害，可忽略。消除方法：`export ANONYMIZED_TELEMETRY=False` |
| uvicorn 反复重启 | 使用 `--reload-exclude venv` 或不加 `--reload` 启动 |
| yfinance 429 限频 | 等 10-15 分钟（Yahoo 限流）。避免连续快速发送查询 |
| `docker-compose` 找不到 | 使用 `docker compose`（无连字符），需要 Docker Desktop v2+ |

## 错误处理

| 场景 | 行为 |
|------|------|
| API Key 未设置 | 启动时警告 + `/chat` 在路由前返回明确错误 |
| PostgreSQL 不可用 | 启动时警告，会话接口返回 500，`/chat` 仍可用内联 history 模式 |
| Redis 不可用 | 启动时警告，上下文加载直接回退到 PostgreSQL |
| 知识库为空 | 启动时警告，RAG 回退到通用知识并附带免责声明 |
| 无法识别股票代码 | 返回中英文示例股票代码列表 |
| 行情数据获取失败 | 返回包含股票代码的具体错误信息 |
| LLM 调用失败 | 每个 Agent 内部捕获，通过 SSE 传递给前端 |
| 新闻服务失败 | 静默降级，行情分析照常进行但不含新闻 |
| 消息持久化失败 | 非致命错误，仅打日志，不中断流式响应 |

## 项目结构

```
financial-qa-system/
├── docker-compose.yml                  # PostgreSQL 16 + Redis 7（AOF 持久化）
├── backend/
│   ├── main.py                         # FastAPI 入口: /chat, /health, /sessions
│   ├── requirements.txt
│   ├── .env.example                    # 所有配置变量，含 ALLOWED_ORIGINS
│   ├── agents/
│   │   ├── router.py                   # 正则预过滤 + 复杂度推断 + Claude tool_use 兜底
│   │   ├── market_agent.py             # 并行获取 + 流式状态 + 双模板
│   │   ├── rag_agent.py               # 混合检索 + 元数据过滤 + 置信度感知 + 实时数据补充
│   │   └── general_agent.py           # 通用对话（对称接口）
│   ├── services/
│   │   ├── llm.py                      # 单例 Claude 客户端，统一 LLMError，动态 temperature
│   │   ├── market_data.py              # 88 条 ticker 映射，黑名单，扩展数据异常检测
│   │   ├── news_service.py             # UTC 时间过滤新闻，透明状态，可配置缓存 TTL
│   │   ├── vector_store.py             # 混合检索，重排序，parent 去重，GC
│   │   ├── database.py                 # 组合索引，原子 add_message_pair()
│   │   └── session_cache.py            # Redis 缓存，append_pair_to_context()
│   ├── prompts/
│   │   ├── router.py                   # 分类 + query_complexity 提取
│   │   ├── market_analysis.py          # 双模板（精简/完整），语言自适应
│   │   └── rag_response.py             # 置信度三级 prompt，长度控制
│   └── knowledge/
│       ├── parsers/                    # PDF、CSV、DOCX 文件解析器
│       │   ├── pdf_parser.py           # PyMuPDF：按标题切段，主题推断
│       │   ├── csv_parser.py           # 按行组分块，列名前缀
│       │   └── docx_parser.py          # 按 Heading 切段，表格→markdown
│       ├── docs/
│       │   ├── seed_knowledge.json     # 20 篇手写文档（12 概念 + 8 分析）
│       │   ├── pdf/                    # 放置 PDF 文件
│       │   ├── csv/                    # 放置 CSV/TSV 文件
│       │   └── docx/                   # 放置 DOCX 文件
│       └── ingest.py                   # 多格式、增量、GC、进度报告
└── frontend/
    ├── app/
    │   ├── layout.tsx, page.tsx, globals.css
    ├── components/
    │   ├── ChatWindow.tsx              # 聊天界面 + session_id 捕获 + 状态提示 + 骨架屏
    │   ├── SessionSidebar.tsx          # 会话列表、新建、切换、删除
    │   ├── MessageBubble.tsx           # Markdown + 状态文字 + 来源展示
    │   ├── RoutingBadge.tsx            # Agent 类型指示器
    │   ├── SourcesDisplay.tsx          # 数据来源展示组件（相关性值防护）
    │   ├── ErrorBoundary.tsx           # React 错误边界，防止渲染崩溃白屏
    │   └── Suggestions.tsx             # 快捷查询建议
    └── lib/
        └── api.ts                      # SSE 客户端（routing/status/text/sources）+ 会话 CRUD + apiFetch
```
优化与扩展思考

Hybrid RAG → GraphRAG： 在 20 篇文档规模下，向量检索已足够。超过 ~200 篇时，知识图谱（实体-关系三元组：AAPL → has_metric → 市盈率 → explained_by → 文档#3）可以支持多跳推理，如"比较科技公司时哪些估值指标最相关"。

自适应分块： 当前固定大小切块（300-600 字符）在边界处丢失上下文。语义分块（通过嵌入相似度骤降检测主题切换点）可以提升长文档的检索精度。

交互式 K 线图（recharts / lightweight-charts），叠加新闻事件标注

用户认证 + 个人自选股列表 + 跨设备查询历史

回测集成： 连接历史价格数据与知识库分析，支持"如果按这个策略操作，结果会怎样"类查询
