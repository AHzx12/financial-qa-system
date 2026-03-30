# Financial Asset QA System / 金融资产问答系统

> An AI-powered full-stack financial Q&A system with real-time market data, RAG knowledge retrieval, multi-agent orchestration, news-backed analysis, and persistent conversation memory.
> 
> 基于大模型的全栈金融资产问答系统，集成实时行情数据、RAG 知识检索、多 Agent 协作、新闻证据链分析与持久化会话记忆。

-----

# English

## System Architecture

```
┌────────────────────────────────────────────────────────┐
│                    Next.js Frontend                     │
│       Chat UI · Markdown · SSE Streaming · Sources      │
└───────────────────────┬────────────────────────────────┘
                        │  POST /chat (SSE)
┌───────────────────────▼────────────────────────────────┐
│                    FastAPI Backend                       │
│  /chat · /health · /sessions CRUD · Rate Limiting       │
│  Dual-mode history: session_id → Redis/PG | inline      │
└──────┬────────────────┬──────────────────┬─────────────┘
       │                │                  │
  ┌────▼─────┐   ┌──────▼──────┐    ┌─────▼───────────┐
  │  Redis   │   │ PostgreSQL  │    │  Query Router    │
  │ Session  │   │  Messages   │    │ Tier 0: regex    │
  │  Cache   │   │ Persistent  │    │  (multi-ticker)  │
  │ 30m TTL  │   │   History   │    │ Tier 1: regex    │
  └──────────┘   └─────────────┘    │  (single-agent)  │
                                    │ Tier 2: Claude   │
                                    │  (tool_use)      │
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

|Layer        |Technology                            |Rationale                                                        |
|-------------|--------------------------------------|-----------------------------------------------------------------|
|Frontend     |Next.js 14 + TypeScript + Tailwind CSS|App Router for API proxy, SSE streaming, type safety             |
|Backend      |FastAPI (Python, async)               |Streaming SSE, rich financial ecosystem, async-first             |
|LLM          |Claude API (Anthropic)                |Native tool_use for routing, structured output, streaming        |
|Vector DB    |ChromaDB (embedded)                   |Zero-config, cosine similarity with relevance threshold          |
|Embeddings   |paraphrase-multilingual-MiniLM-L12-v2 |Multilingual (Chinese+English), 384-dim, local                   |
|Market Data  |Yahoo Finance (yfinance)              |Free, no API key required, global coverage                       |
|News         |Yahoo Finance news + Claude Web Search|Headlines as evidence; web search fallback for historical queries|
|Session DB   |PostgreSQL 16 (SQLAlchemy async)      |Persistent chat history, full message metadata                   |
|Session Cache|Redis 7                               |Hot-path context loading, 30-min TTL per session                 |
|Market Cache |cachetools TTLCache                   |In-process, 5-min TTL for market data, 10-min for news           |

## Core Design Decisions

### 1. Three-Tier Query Router

Tier 0 (instant, free): regex detects multi-ticker compound queries (e.g. “compare AAPL and MSFT”) → Supervisor. Tier 1 (instant, free): regex pre-filter for single-agent queries — ticker + market keywords → `market_data`, concept keywords → `knowledge`, ticker + knowledge + market → `compound`. Decision matrix covers all signal combinations. Tier 2 (fallback): Claude `tool_use` for ambiguous queries, also capable of detecting single-ticker compound needs via `is_compound` + `sub_tasks` schema. ~60-70% of queries are resolved at regex layers, saving one API call (~$0.002) and 1-2s latency per hit.

### 2. Multi-Agent Supervisor

Compound queries (multiple tickers, or knowledge + market data) are handled by a Supervisor that: (1) decomposes into sub-tasks, (2) fans out parallel data collection via services directly (not agents), (3) fans in results with error handling, (4) calls a synthesizer LLM once to produce unified analysis. Anti-hallucination rules in the synthesizer prompt forbid inventing causal links between data sources.

### 3. Backend Computes, LLM Explains

All numeric metrics are pre-computed in `market_data.py`: period change, percentage, high/low, average volume, trend classification (5 levels). The LLM receives these pre-computed values and only explains them. It never performs arithmetic — eliminating hallucinated numbers. Data anomaly checks flag `price=0`, `change>±100%`, `negative market cap`, `negative P/E` (with explanatory note for unprofitable companies), `current price exceeding 52-week high by >50%`, `empty fundamentals from Yahoo API`, and `>50% single-day price jumps` (likely stock splits).

### 4. Time Window Parsing (Relative + Absolute)

The backend extracts time windows from natural language queries using regex. Relative: “7天”/“一周” → `7d`, “最近”/“一个月” → `1mo`, “季度” → `3mo`, “半年” → `6mo`, “一年” → `1y`. Absolute quarters: “2025年第四季度”/“Q4 2025” → `start=2025-10-01, end=2025-12-31`. Absolute months: “2025年7月” → `start=2025-07-01, end=2025-07-31`. Absolute dates: “1月15日” → 7-day window centered on that date. Returns structured `{"mode": "relative"|"absolute", ...}` for downstream use.

### 5. News as Time-Aware Evidence

`news_service.py` fetches headlines filtered by time window (7d query → last 7 days of news). Returns transparent status: `ok` (with articles), `no_news` (no articles found), or `error` (service unavailable) — so the LLM never invents reasons. For historical queries (>14 days old), Yahoo news is skipped and Claude Web Search is used as fallback with a 20s timeout.

### 6. RAG with Multilingual Embeddings

Uses `paraphrase-multilingual-MiniLM-L12-v2` for Chinese+English. Vector search returns results with cosine distance filtered at threshold 0.9. Dynamic `n_results`: definition queries get 2 docs (less noise), analysis queries get 5 (broader coverage). Long documents are auto-chunked with overlap.

### 7. Dual-Mode History with Auto-Session Creation

The `/chat` endpoint auto-creates a session when no `session_id` is provided. The frontend receives the new `session_id` in the first SSE event and uses it for subsequent messages. Two modes: **Session mode** (auto or explicit): loads history from Redis (hot) → PostgreSQL (cold), auto-persists. **Stateless mode** (PostgreSQL unavailable): uses inline history directly. No DB required.

### 8. Atomic Message Persistence

`add_message_pair()` inserts user + assistant messages in a single PostgreSQL transaction — if either fails, both roll back. Redis cache uses `append_pair_to_context()` which writes both messages in one GET→modify→SET cycle. PG and Redis errors are logged separately.

### 9. History Windowing Strategy

PostgreSQL stores the **complete** conversation history with no limit. Redis caches the last 10 messages per session (30-min TTL). Before each LLM call, `truncate_history()` trims to the last 6 messages (3 turns) and ensures the first message is `user` role — this is the sliding window the model actually sees.

### 10. Adaptive Response Templates

Market queries are classified as `simple` (price lookup → concise 3-5 sentence response) or `detailed` (trend analysis → full 6-section briefing). Classification comes from the router’s `query_complexity` field. The regex pre-filter also infers complexity via keyword matching: price-lookup patterns → `simple`, analysis patterns → `detailed`.

### 11. Source Provenance

Every response includes a `sources` SSE event containing market data source, news citations (with status), or knowledge base documents used — rendered in the frontend as a “Data sources” section. Compound queries show sources for each ticker separately.

### 12. Automatic Language Matching

All prompts include a `LANGUAGE RULE` that instructs Claude to detect the user’s language and respond entirely in that language — including section headers, labels, and analysis text.

### 13. Security & Input Validation

CORS with `allow_origins=["*"]` for dev, `ALLOWED_ORIGINS` env var for production. Rate limiting via `slowapi`: 15 req/min on `/chat`, 60 req/min on `/sessions`. Message length capped at 4000 chars. Ticker regex filtered through 40-term blacklist.

### 14. Parallel Market Data Fetching

`market_agent.py` fetches stock data and news concurrently via `asyncio.gather(asyncio.to_thread(...))`. Saves 0.5-1s per request and fixes event loop blocking from synchronous yfinance.

### 15. Hybrid Search (Keywords + Vectors)

RAG queries extract financial keywords and pass them as `where_document: {"$contains": ...}` filter to ChromaDB, narrowing candidates before vector matching.

### 16. Cross-Encoder Reranker

For `detailed` queries, top candidates from bi-encoder search are re-scored with a multilingual cross-encoder. Lazy-loaded, graceful degradation. Simple queries skip reranking.

### 17. Confidence-Aware Answer Strategy

Different prompt tiers based on retrieval quality: **High** (>0.5): cite docs, minimal supplementation. **Medium** (0.2-0.5): supplement freely. **Low** (<0.2): general knowledge with disclaimer.

### 18. Streaming Status Events

SSE events include `{"type":"status"}` messages between processing steps. Frontend displays these as italic gray text while waiting for LLM output.

### 19. Multi-Format Document Ingestion

File parsers for PDF (PyMuPDF), CSV (row-group chunking), DOCX (heading-based sections), and JSON (seed-format arrays). Type-aware chunk sizes. CSV auto-detects encoding via `charset-normalizer`.

### 20. Incremental Ingest with Garbage Collection

Content-hash tracking for incremental updates. `--gc-only` removes orphaned vectors. `--force` rebuilds the entire index.

### 21. Dynamic Temperature Control

Market agent (0.2), RAG agent (0.3), General agent (0.6), Supervisor synthesizer (0.3). Router uses 0.0 for deterministic classification.

### 22. Hybrid RAG Enrichment

Knowledge queries with recognized tickers optionally fetch real-time market data via `get_stock_data` and append as `<realtime_market_data>` XML block. Failure silently degrades to knowledge-only.

## Prompt Design

|Prompt                      |Strategy                                                                                                                         |
|----------------------------|---------------------------------------------------------------------------------------------------------------------------------|
|**Router**                  |3 categories + `query_complexity` + `is_compound` + `sub_tasks`. Tool schema forces structured output. Temperature=0.0.          |
|**Market (simple)**         |Concise 3-5 sentence template. Temperature=0.2.                                                                                  |
|**Market (detailed)**       |Full 6-section template with no-data fallback rules. Temperature=0.2.                                                            |
|**RAG (high confidence)**   |“Documents HIGHLY RELEVANT” — cite docs, minimal supplementation. Temperature=0.3.                                               |
|**RAG (medium confidence)** |“Documents MODERATELY relevant” — supplement freely. Temperature=0.3.                                                            |
|**RAG (low confidence)**    |“KB has no match” — general knowledge with disclaimer. Temperature=0.3.                                                          |
|**Supervisor (synthesizer)**|Anti-hallucination: no invented causal links, cite sources explicitly, note data gaps. Token budgets per ticker. Temperature=0.3.|

## Data Sources

|Source                 |Usage                                       |Cache TTL |
|-----------------------|--------------------------------------------|----------|
|Yahoo Finance (prices) |Stock prices, fundamentals, historical data |5 min     |
|Yahoo Finance (news)   |Recent headlines for evidence-based analysis|10 min    |
|Claude Web Search      |Fallback for historical news (>14 days)     |None      |
|ChromaDB Knowledge Base|20+ financial knowledge docs, 12 topics     |Static    |
|PostgreSQL             |Full chat history, message metadata         |Persistent|
|Redis                  |Session context (last 10 messages)          |30 min    |

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
GET    /sessions/{id}         → Get with messages  → { id, title, messages: [...] }
DELETE /sessions/{id}         → Delete + clear cache → { deleted: true }
```

### Chat (SSE Stream)

```
POST /chat
Body: { message: string, session_id?: string, history?: [{role, content}] }

SSE Events:
  data: {"type":"routing",  "category":"market_data|knowledge|general|compound", ...}
  data: {"type":"status",   "content":"Fetching BABA market data & news..."}
  data: {"type":"text",     "content":"..."}
  data: {"type":"sources",  "content":{...}}
  data: {"type":"done"}
```

## Database Schema

```sql
CREATE TABLE sessions (
    id          VARCHAR(36) PRIMARY KEY,
    title       VARCHAR(200) DEFAULT 'New chat',
    created_at  TIMESTAMP DEFAULT NOW(),
    updated_at  TIMESTAMP DEFAULT NOW()
);

CREATE TABLE messages (
    id                SERIAL PRIMARY KEY,
    session_id        VARCHAR(36) REFERENCES sessions(id) ON DELETE CASCADE,
    role              VARCHAR(20) NOT NULL,
    content           TEXT NOT NULL,
    routing_category  VARCHAR(20),   -- 'market_data' | 'knowledge' | 'general' | 'compound'
    routing_ticker    VARCHAR(20),
    sources           JSONB,
    created_at        TIMESTAMP DEFAULT NOW()
);
CREATE INDEX ix_messages_session_created ON messages(session_id, created_at);
```

## Prerequisites

- **Docker Desktop** (for PostgreSQL + Redis)
- **Python 3.10–3.12** (3.13+ has dependency issues; 3.12 recommended)
- **Node.js 18+**

## Quick Start

```bash
# 1. Start infrastructure
docker compose up -d

# 2. Backend
cd backend
python3.12 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env        # Fill in ANTHROPIC_API_KEY
python -m knowledge.ingest  # Load 20 docs into ChromaDB
uvicorn main:app --port 8000

# 3. Frontend
cd frontend
npm install
npm run dev                 # → http://localhost:3000

# 4. Verify
curl http://localhost:8000/health
```

### Troubleshooting

|Issue                      |Fix                                                                          |
|---------------------------|-----------------------------------------------------------------------------|
|`pymupdf` build fails      |Use Python 3.12; avoid spaces in project path                                |
|`onnxruntime not found`    |Downgrade to Python 3.12                                                     |
|`np.float_ removed`        |`pip install "numpy<2"`                                                      |
|ChromaDB telemetry warnings|`export ANONYMIZED_TELEMETRY=False`                                          |
|uvicorn keeps reloading    |`--reload-exclude venv` or no `--reload`                                     |
|yfinance 429               |Wait 10-15 min                                                               |
|`docker-compose` not found |Use `docker compose` (no hyphen)                                             |
|SSE not streaming          |Create `frontend/.env.local` with `NEXT_PUBLIC_API_URL=http://localhost:8000`|

## Error Handling

|Scenario                 |Behavior                            |
|-------------------------|------------------------------------|
|API key missing          |`/chat` returns error before routing|
|PostgreSQL down          |`/chat` works with inline history   |
|Redis down               |Falls back to PostgreSQL            |
|Knowledge base empty     |General knowledge with disclaimer   |
|Ticker unrecognized      |Returns example tickers             |
|News unavailable         |Web search fallback (20s timeout)   |
|Supervisor sub-task fails|Other sub-tasks unaffected          |
|Message persistence fails|Non-fatal, logged only              |

## Project Structure

```
financial-qa-system/
├── docker-compose.yml
├── backend/
│   ├── main.py
│   ├── agents/
│   │   ├── router.py                   # 3-tier: regex compound + single + Claude
│   │   ├── supervisor.py               # Multi-agent orchestrator
│   │   ├── market_agent.py             # Parallel fetch + dual template
│   │   ├── rag_agent.py               # Hybrid search + confidence-aware
│   │   └── general_agent.py
│   ├── services/
│   │   ├── llm.py                      # Singleton Claude client
│   │   ├── market_data.py              # 70+ ticker mappings + validation
│   │   ├── news_service.py             # Yahoo news + web search fallback
│   │   ├── vector_store.py             # Hybrid search + reranker
│   │   ├── database.py                 # Atomic message persistence
│   │   └── session_cache.py            # Redis cache
│   ├── prompts/
│   │   ├── router.py                   # Classification + compound schema
│   │   ├── supervisor.py               # Synthesizer anti-hallucination
│   │   ├── market_analysis.py          # Dual template
│   │   └── rag_response.py             # Confidence tiers
│   └── knowledge/
│       ├── parsers/                    # PDF, CSV, DOCX, JSON
│       ├── docs/                       # seed_knowledge.json + file dirs
│       └── ingest.py                   # Incremental + GC
└── frontend/
    ├── .env.local
    ├── app/
    ├── components/                     # ChatWindow, RoutingBadge, SourcesDisplay, etc.
    └── lib/api.ts                      # SSE client + session CRUD
```

## Optimizations & Future Directions

### Performance

- **Router cost tradeoff:** Regex handles ~60-70% at zero cost. Local classifier (DistilBERT) could push to 90%+, worth it at >1000 queries/day.
- **Embedding hot-loading:** Pre-load to shared memory for sub-100ms cold starts.
- **Smart caching:** LFU with per-ticker TTLs (blue chips 5min, penny stocks 1min).

### Retrieval Quality

- **Hybrid RAG → GraphRAG:** At 20 docs, vectors suffice. Beyond ~200 docs, knowledge graphs enable multi-hop reasoning.
- **Adaptive chunking:** Semantic chunking (split on topic shifts) vs. current fixed-size.
- **Query expansion:** Auto-generate variants (“市盈率” → “P/E ratio”) for low-relevance results.

### Architecture

- **Multi-worker:** Replace TTLCache with Redis for market/news caching.
- **WebSocket:** Enable server-push for alerts and notifications.
- **Streaming Supervisor:** Start synthesizing as first sub-agent completes.

### Features

- **Interactive candlestick charts** with news event overlays
- **File upload API** for earnings reports and custom documents
- **User auth** with watchlists and cross-device history
- **Backtesting:** “What if I followed this strategy” queries

-----

# 中文

## 系统架构

```
┌──────────────────────────────────────────────────────┐
│                    Next.js 前端                       │
│        聊天界面 · Markdown 渲染 · SSE 流式 · 来源展示  │
└───────────────────────┬──────────────────────────────┘
                        │  POST /chat (SSE)
┌───────────────────────▼──────────────────────────────┐
│                    FastAPI 后端                       │
│  /chat · /health · /sessions 增删查 · 频率限制        │
│  双模式历史: session_id → Redis/PG | 内联 history     │
└──────┬────────────────┬──────────────────┬───────────┘
       │                │                  │
  ┌────▼─────┐   ┌──────▼──────┐    ┌─────▼──────────┐
  │  Redis   │   │ PostgreSQL  │    │    查询路由     │
  │ 会话缓存 │   │  消息持久化 │    │ Tier 0: regex  │
  │ 30分钟TTL│   │   完整历史  │    │ (多ticker检测) │
  └──────────┘   └─────────────┘    │ Tier 1: regex  │
                                    │ (单agent分类)  │
                                    │ Tier 2: Claude │
                                    │ (tool_use兜底) │
                                    └─────┬──────────┘
                           ┌──────────────┼──────────────┐
                           │              │              │
                    ┌──────▼───┐   ┌──────▼───┐   ┌─────▼────┐
                    │ 行情Agent│   │ RAG Agent│   │通用Agent │
                    └──┬───┬───┘   └──┬───┬───┘   └──────────┘
                       │   │          │   │
                 ┌─────▼┐ ┌▼────┐ ┌──▼──┐┌▼──────┐
                 │Yahoo │ │新闻 │ │向量 ││实时数据│
                 │Fin.  │ │服务 │ │检索 ││  补充  │
                 └──────┘ └─────┘ └─────┘└───────┘
                           │
                    ┌──────▼──────┐
                    │  Supervisor │
                    │ (复合查询)  │
                    │ 并行收集数据│
                    │ → 合成器LLM│
                    └─────────────┘
```

## 技术栈

|层级   |技术                                    |选型理由                             |
|-----|--------------------------------------|---------------------------------|
|前端   |Next.js 14 + TypeScript + Tailwind CSS|App Router 做 API 代理，SSE 流式传输，类型安全|
|后端   |FastAPI (Python, async)               |流式 SSE，丰富的金融 Python 生态，异步优先      |
|大模型  |Claude API (Anthropic)                |原生 tool_use 做路由，结构化输出，流式响应       |
|向量库  |ChromaDB（嵌入式）                         |零配置，余弦相似度，相关性阈值过滤                |
|嵌入模型 |paraphrase-multilingual-MiniLM-L12-v2 |多语言（中英双语）、384 维、本地推理             |
|行情数据 |Yahoo Finance (yfinance)              |免费、无需 API Key、全球覆盖               |
|新闻   |Yahoo Finance 新闻 + Claude Web Search  |近期新闻作为证据链；历史查询走网络搜索兜底            |
|会话数据库|PostgreSQL 16 (SQLAlchemy async)      |持久化聊天记录，完整消息元数据                  |
|会话缓存 |Redis 7                               |热路径上下文加载，每会话 30 分钟 TTL           |
|行情缓存 |cachetools TTLCache                   |进程内缓存，行情 5 分钟 TTL，新闻 10 分钟       |

## 核心设计决策

### 1. 三层查询路由

Tier 0（即时、免费）：正则检测多 ticker 复合查询（如”比较苹果和微软”）→ Supervisor。Tier 1（即时、免费）：正则预过滤单 agent 查询——ticker + 行情关键词 → `market_data`，概念关键词 → `knowledge`，ticker + 知识 + 行情 → `compound`。决策矩阵覆盖所有信号组合。Tier 2（兜底）：Claude `tool_use` 处理模糊查询，也能通过 `is_compound` + `sub_tasks` schema 检测单 ticker 复合需求。约 60-70% 的查询在 regex 层解决，每次节省一次 API 调用（~$0.002）和 1-2 秒延迟。

### 2. 多 Agent Supervisor

复合查询（多 ticker 或知识+行情）由 Supervisor 处理：（1）分解子任务，（2）并行扇出数据收集（直接调 services 层，不经过 agent），（3）扇入合并结果（含错误处理），（4）调一次合成器 LLM 生成统一分析。合成器 prompt 的反幻觉规则禁止编造数据源之间的因果关系。

### 3. 后端计算，大模型解释

所有数值指标在 `market_data.py` 中预先计算：涨跌额、涨跌幅、最高/最低、平均成交量、趋势分级（5级）。大模型只负责解释，绝不做算术。数据异常检查会标记 `price=0`、`change>±100%`、`负市值`、`负市盈率`（附亏损公司说明）、`当前价大幅偏离52周高点`、`基本面数据缺失`、`单日跳变>50%`（疑似拆股）。

### 4. 时间窗口智能解析（相对 + 绝对）

后端通过正则从自然语言中提取时间窗口。相对时间：“7天”/“一周” → `7d`，“最近”/“一个月” → `1mo`，“季度” → `3mo`，“半年” → `6mo`，“一年” → `1y`。绝对季度：“2025年第四季度”/“Q4 2025” → `start=2025-10-01, end=2025-12-31`。绝对月份：“2025年7月” → `start=2025-07-01, end=2025-07-31`。绝对日期：“1月15日” → 以该日期为中心的 7 天窗口。返回结构化 `{"mode": "relative"|"absolute", ...}` 供下游使用。

### 5. 新闻作为时间关联证据

`news_service.py` 按时间窗口过滤新闻（7天查询 → 仅取7天内新闻）。返回透明状态：`ok`（有新闻）、`no_news`（无相关新闻）、`error`（服务不可用）——大模型不会凭空编造原因。对于历史查询（>14天），跳过 Yahoo 新闻，使用 Claude Web Search 作为兜底（20 秒超时）。

### 6. 多语言 RAG + 动态检索

使用 `paraphrase-multilingual-MiniLM-L12-v2` 支持中英双语。余弦距离阈值 0.9 过滤低相关文档。动态 `n_results`：定义类查询取 2 篇（减少噪音），分析类查询取 5 篇（广覆盖）。长文档自动分块。

### 7. 双模式历史 + 自动建会话

`/chat` 接口在没有 `session_id` 时自动创建会话。前端从 routing 事件获取新 `session_id`，后续消息都带上它。**会话模式**：从 Redis（热）→ PostgreSQL（冷）加载历史，自动持久化。**无状态模式**（PG 不可用）：直接用内联 history。

### 8. 原子消息持久化

`add_message_pair()` 在一个 PostgreSQL 事务中插入 user + assistant 两条消息——任一失败则全部回滚。Redis 缓存使用 `append_pair_to_context()` 在一次 GET→修改→SET 中写入双条。PG 和 Redis 错误分开记录。

### 9. 历史窗口策略

PostgreSQL 存储**完整的**会话历史。Redis 缓存最近 10 条（30 分钟 TTL）。`truncate_history()` 裁剪到最近 6 条（3 轮）并确保首条为 user 角色——这是模型看到的滑动窗口。

### 10. 自适应回答模板

行情查询分为 `simple`（价格查询 → 3-5 句精简回答）和 `detailed`（走势分析 → 完整 6 段报告）。由路由的 `query_complexity` 字段决定。正则预过滤也通过关键词匹配推断复杂度：价格查询模式 → `simple`，分析模式 → `detailed`。

### 11. 数据来源展示

每条回复的 `sources` SSE 事件携带行情来源、新闻引用（含状态）或知识库文档信息，前端渲染为”数据来源”区块。复合查询会分别展示每个 ticker 的来源。

### 12. 自动语言匹配

所有 Prompt 含 `LANGUAGE RULE`，Claude 自动检测用户语言并以该语言输出全部内容。

### 13. 安全与输入校验

CORS 开发环境 `origins=["*"]`，生产环境通过 `ALLOWED_ORIGINS` 指定域名。频率限制（`slowapi`）：每 IP 每分钟 15 次 `/chat`、60 次 `/sessions`。消息长度上限 4000 字符。Ticker 正则匹配经过 40 个金融术语黑名单过滤。

### 14. 并行行情数据获取

`market_agent.py` 通过 `asyncio.gather(asyncio.to_thread(...))` 并行获取股价和新闻，每次请求节省 0.5-1 秒，同时修复了 yfinance 同步调用阻塞 event loop 的问题。

### 15. 混合检索（关键词 + 向量）

RAG 查询从问题中提取金融关键词，通过 ChromaDB 的 `where_document: {"$contains": ...}` 过滤缩小候选集，然后在子集内做向量匹配。

### 16. Cross-Encoder 重排序

`detailed` 查询的 bi-encoder 检索结果会用多语言 cross-encoder 重新打分。懒加载，不可用时优雅降级。`simple` 查询跳过重排序。

### 17. 置信度感知回答策略

根据检索质量选择不同 prompt 层级：**高置信**（>0.5）：以知识库为主。**中置信**（0.2-0.5）：自由补充通用知识。**低置信**（<0.2）：通用知识 + 免责声明。

### 18. 流式状态提示

SSE 事件流中穿插 `{"type":"status"}` 消息。前端在 LLM 输出前以灰色斜体展示。

### 19. 多格式文档导入

文件解析器支持 PDF（PyMuPDF）、CSV（按行组分块）、DOCX（按 Heading 切段）、JSON（seed 格式数组/对象）。按文档类型自适应 chunk 大小。CSV 通过 `charset-normalizer` 自动检测编码。

### 20. 增量导入与垃圾回收

内容哈希追踪实现增量更新。`--gc-only` 删除孤儿向量。`--force` 全量重建索引。

### 21. 动态 Temperature 控制

行情 Agent（0.2）、RAG Agent（0.3）、通用 Agent（0.6）、Supervisor 合成器（0.3）。路由用 0.0 确保确定性分类。

### 22. 知识 + 行情交叉引用

知识查询包含已识别 ticker 时，可选获取实时行情数据并以 `<realtime_market_data>` XML 标签附加到 prompt 中。yfinance 失败时静默降级为纯知识库回答。

## Prompt 设计思路

|Prompt             |策略                                                                                       |
|-------------------|-----------------------------------------------------------------------------------------|
|**路由**             |3 类别 + `query_complexity` + `is_compound` + `sub_tasks`。工具 schema 强制结构化输出。temperature=0.0|
|**行情（精简）**         |3-5 句模板。temperature=0.2                                                                  |
|**行情（完整）**         |6 段模板，含无数据 fallback 规则。temperature=0.2                                                   |
|**RAG（高置信）**       |“文档高度相关”——引用文档，少量补充。temperature=0.3                                                      |
|**RAG（中置信）**       |“文档部分相关”——自由补充通用知识。temperature=0.3                                                       |
|**RAG（低置信）**       |“知识库无匹配”——通用知识 + 免责声明。temperature=0.3                                                    |
|**Supervisor（合成器）**|反幻觉：禁止编造因果，需引用来源，标注缺口。Token 预算控制。temperature=0.3                                         |

## 数据来源

|来源               |用途                |缓存 TTL|
|-----------------|------------------|------|
|Yahoo Finance（价格）|股价、基本面、历史数据       |5 分钟  |
|Yahoo Finance（新闻）|近期新闻标题作为分析证据      |10 分钟 |
|Claude Web Search|历史新闻兜底（>14天）      |无     |
|ChromaDB 知识库     |20+ 篇金融知识文档，12 个主题|静态    |
|PostgreSQL       |完整聊天记录，消息元数据      |持久化   |
|Redis            |会话上下文（最近 10 条消息）  |30 分钟 |

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
GET    /sessions/{id}         → 获取详情   → { id, title, messages: [...] }
DELETE /sessions/{id}         → 删除会话   → { deleted: true }
```

### 聊天（SSE 流式）

```
POST /chat
Body: { message: string, session_id?: string, history?: [{role, content}] }

SSE 事件流:
  data: {"type":"routing",  "category":"market_data|knowledge|general|compound", ...}
  data: {"type":"status",   "content":"正在获取 BABA 行情数据与新闻..."}
  data: {"type":"text",     "content":"..."}
  data: {"type":"sources",  "content":{...}}
  data: {"type":"done"}
```

## 数据库 Schema

```sql
CREATE TABLE sessions (
    id          VARCHAR(36) PRIMARY KEY,
    title       VARCHAR(200) DEFAULT 'New chat',
    created_at  TIMESTAMP DEFAULT NOW(),
    updated_at  TIMESTAMP DEFAULT NOW()
);

CREATE TABLE messages (
    id                SERIAL PRIMARY KEY,
    session_id        VARCHAR(36) REFERENCES sessions(id) ON DELETE CASCADE,
    role              VARCHAR(20) NOT NULL,
    content           TEXT NOT NULL,
    routing_category  VARCHAR(20),   -- 'market_data' | 'knowledge' | 'general' | 'compound'
    routing_ticker    VARCHAR(20),
    sources           JSONB,
    created_at        TIMESTAMP DEFAULT NOW()
);
CREATE INDEX ix_messages_session_created ON messages(session_id, created_at);
```

## 环境要求

- **Docker Desktop**（运行 PostgreSQL + Redis）
- **Python 3.10–3.12**（3.13+ 存在依赖问题；推荐 3.12）
- **Node.js 18+**

## 快速启动

```bash
# 1. 启动基础设施
docker compose up -d

# 2. 后端
cd backend
python3.12 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env        # 填入 ANTHROPIC_API_KEY
python -m knowledge.ingest  # 导入知识库
uvicorn main:app --port 8000

# 3. 前端
cd frontend
npm install
npm run dev                 # → http://localhost:3000

# 4. 验证
curl http://localhost:8000/health
```

### 常见问题

|问题                   |解决方案                                                                |
|---------------------|--------------------------------------------------------------------|
|`pymupdf` 编译失败       |用 Python 3.12；避免路径有空格                                               |
|找不到 `onnxruntime`    |降级到 Python 3.12                                                     |
|`np.float_ removed`  |`pip install "numpy<2"`                                             |
|ChromaDB telemetry 警告|`export ANONYMIZED_TELEMETRY=False`                                 |
|uvicorn 反复重启         |`--reload-exclude venv` 或不加 `--reload`                              |
|yfinance 429         |等 10-15 分钟                                                          |
|`docker-compose` 找不到 |用 `docker compose`（无连字符）                                            |
|SSE 不流式              |`frontend/.env.local` 写入 `NEXT_PUBLIC_API_URL=http://localhost:8000`|

## 错误处理

|场景              |行为                      |
|----------------|------------------------|
|API Key 未设置     |`/chat` 在路由前返回明确错误      |
|PostgreSQL 不可用  |`/chat` 仍可用内联 history 模式|
|Redis 不可用       |回退到 PostgreSQL          |
|知识库为空           |通用知识 + 免责声明             |
|无法识别股票代码        |返回示例 ticker 列表          |
|历史新闻不可用         |Web Search 兜底（20 秒超时）   |
|Supervisor 子任务失败|其余子任务不受影响               |
|消息持久化失败         |非致命，仅打日志                |

## 项目结构

```
financial-qa-system/
├── docker-compose.yml
├── backend/
│   ├── main.py
│   ├── agents/
│   │   ├── router.py                   # 三层路由: regex compound + single + Claude
│   │   ├── supervisor.py               # 多 Agent 编排器
│   │   ├── market_agent.py             # 并行获取 + 双模板
│   │   ├── rag_agent.py               # 混合检索 + 置信度感知
│   │   └── general_agent.py
│   ├── services/
│   │   ├── llm.py                      # 单例 Claude 客户端
│   │   ├── market_data.py              # 70+ ticker 映射 + 数据校验
│   │   ├── news_service.py             # Yahoo 新闻 + Web Search 兜底
│   │   ├── vector_store.py             # 混合检索 + 重排序
│   │   ├── database.py                 # 原子消息持久化
│   │   └── session_cache.py            # Redis 缓存
│   ├── prompts/
│   │   ├── router.py                   # 分类 + compound schema
│   │   ├── supervisor.py               # 合成器反幻觉 prompt
│   │   ├── market_analysis.py          # 双模板
│   │   └── rag_response.py             # 置信度三级
│   └── knowledge/
│       ├── parsers/                    # PDF、CSV、DOCX、JSON
│       ├── docs/                       # seed_knowledge.json + 文件目录
│       └── ingest.py                   # 增量 + GC
└── frontend/
    ├── .env.local
    ├── app/
    ├── components/                     # ChatWindow, RoutingBadge, SourcesDisplay 等
    └── lib/api.ts                      # SSE 客户端 + 会话 CRUD
```

## 优化与扩展思考

### 性能优化

- **路由成本权衡：** regex 处理约 60-70%（零成本）。轻量级本地分类器（微调 DistilBERT，~5MB）可将未命中率降到 10% 以下，日查询量 >1000 时值得引入。
- **嵌入模型热加载：** 冷启动首次查询 ~3s。预加载到共享内存或独立嵌入微服务，实现 <100ms 冷启动。
- **智能缓存：** 频率感知缓存（LFU）+ 差异化 TTL（蓝筹股 5min、小盘股 1min）。

### 检索质量

- **Hybrid RAG → GraphRAG：** 20 篇文档够用。超过 ~200 篇时，知识图谱（实体-关系三元组）支持多跳推理，如”比较科技公司时哪些估值指标最相关”。
- **自适应分块：** 语义分块（通过嵌入相似度骤降检测主题切换）替代固定大小切块，提升长文档检索精度。
- **查询扩展：** 低相关度时自动生成 2-3 个查询变体（如”市盈率”→ “P/E ratio”）并合并结果。

### 架构扩展

- **多 Worker 部署：** 将进程内 TTLCache 替换为 Redis 统一缓存行情和新闻数据。
- **WebSocket 升级：** SSE 是单向的。WebSocket 可实现服务端推送，支持价格预警和实时通知。
- **流式 Supervisor：** 当前 Supervisor 等所有子任务完成后才合成。流式变体可在第一个子任务完成后即开始合成，降低感知延迟。

### 功能扩展

- **交互式 K 线图**（recharts / lightweight-charts）+ 新闻事件标注叠加
- **文件上传 API**，支持用户上传财报、10-K 文件和自定义知识文档
- **用户认证** + 个人自选股列表 + 跨设备查询历史
- **回测集成：** 连接历史价格数据与知识库分析，支持”如果按这个策略操作，结果会怎样”类查询
