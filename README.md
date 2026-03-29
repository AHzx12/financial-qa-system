# Financial Asset QA System / 金融资产问答系统

> An AI-powered full-stack financial Q&A system with real-time market data, RAG knowledge retrieval, multi-agent orchestration, news-backed analysis, and persistent conversation memory.
>
> 基于大模型的全栈金融资产问答系统，集成实时行情数据、RAG 知识检索、多 Agent 协作、新闻证据链分析与持久化会话记忆。

---

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

| Layer | Technology | Rationale |
|-------|-----------|-----------|
| Frontend | Next.js 14 + TypeScript + Tailwind CSS | App Router for API proxy, SSE streaming, type safety |
| Backend | FastAPI (Python, async) | Streaming SSE, rich financial ecosystem, async-first |
| LLM | Claude API (Anthropic) | Native tool_use for routing, structured output, streaming |
| Vector DB | ChromaDB (embedded) | Zero-config, cosine similarity with relevance threshold |
| Embeddings | paraphrase-multilingual-MiniLM-L12-v2 | Multilingual (Chinese+English), 384-dim, local |
| Market Data | Yahoo Finance (yfinance) | Free, no API key required, global coverage |
| News | Yahoo Finance news + Claude Web Search | Headlines as evidence; web search fallback for historical queries |
| Session DB | PostgreSQL 16 (SQLAlchemy async) | Persistent chat history, full message metadata |
| Session Cache | Redis 7 | Hot-path context loading, 30-min TTL per session |
| Market Cache | cachetools TTLCache | In-process, 5-min TTL for market data, 10-min for news |

## Core Design Decisions

### 1. Three-Tier Query Router
Tier 0 (instant, free): regex detects multi-ticker compound queries (e.g. "compare AAPL and MSFT") → routes to Supervisor.
Tier 1 (instant, free): regex pre-filter with decision matrix — ticker + market keywords → `market_data`, concept keywords → `knowledge`, ticker + knowledge + market → `compound`, ticker + comparison → `compound`. Saves ~1-2s and one API call per hit.
Tier 2 (fallback): Claude `tool_use` for ambiguous queries, also capable of detecting single-ticker compound needs via extended tool schema with `is_compound` and `sub_tasks` fields.

### 2. Multi-Agent Supervisor
Compound queries (multiple tickers, or mixed knowledge + market) are handled by a Supervisor that: (1) fans out parallel data collection via services layer (not agents), (2) collects results with per-task error handling, (3) calls a synthesizer LLM with anti-hallucination rules to produce a unified analysis. Sub-tasks are capped at 5 to prevent over-decomposition.

### 3. Backend Computes, LLM Explains
All numeric metrics are pre-computed in `market_data.py`: period change, percentage, high/low, average volume, trend classification (5 levels). The LLM receives these pre-computed values and only explains them. It never performs arithmetic — eliminating hallucinated numbers. Data anomaly checks flag `price=0`, `change>±100%`, `negative market cap`, `negative P/E`, `current price exceeding 52-week high by >50%`, `empty fundamentals`, and `>50% single-day price jumps`.

### 4. Time Window Parsing (Relative + Absolute)
Supports both relative ("最近一个月" → `1mo`) and absolute ("2025年第四季度" → `start=2025-10-01, end=2025-12-31`, "1月15日" → ±7 day window). Returns a structured dict `{"mode": "relative"|"absolute", ...}`.

### 5. News as Time-Aware Evidence
Returns transparent status: `ok`, `no_news`, or `error` — so the LLM never invents reasons. For historical queries (>14 days old), Yahoo Finance news is skipped and Claude Web Search is used as fallback with a 20-second timeout.

### 6. RAG with Multilingual Embeddings
Uses `paraphrase-multilingual-MiniLM-L12-v2` for Chinese+English. Dynamic `n_results`: definition queries get 2 docs, analysis queries get 5. Long documents are auto-chunked with overlap.

### 7. Dual-Mode History with Auto-Session Creation
Session mode: Redis (hot) → PostgreSQL (cold). Stateless mode: inline history when PostgreSQL is unavailable.

### 8. Atomic Message Persistence
`add_message_pair()` inserts user + assistant in a single transaction — if either fails, both roll back.

### 9. History Windowing Strategy
PostgreSQL stores complete history. Redis caches last 10 messages. `truncate_history()` trims to last 6 messages (3 turns) for the LLM context.

### 10. Adaptive Response Templates
`simple` (3-5 sentences) vs `detailed` (6-section briefing) based on `query_complexity` from the router.

### 11. Source Provenance
Every response includes sources. Compound queries show multi-ticker sources with dynamic keys (`market_AAPL`, `news_MSFT`).

### 12. Automatic Language Matching
All prompts include a `LANGUAGE RULE` — Claude responds in the user's language.

### 13. Security & Input Validation
CORS, rate limiting (slowapi), 4000-char message cap, 40-term ticker blacklist, application-layer field validation.

### 14. Parallel Market Data Fetching
`asyncio.gather(asyncio.to_thread(...))` for concurrent stock data + news fetching.

### 15. Hybrid Search (Keywords + Vectors)
`where_document: {"$contains": ...}` narrows candidates before vector matching.

### 16. Cross-Encoder Reranker
Multilingual cross-encoder for `detailed` queries. Lazy-loaded, gracefully degrades.

### 17. Confidence-Aware Answer Strategy
High (>0.5): cite docs. Medium (0.2-0.5): supplement freely. Low (<0.2): general knowledge + disclaimer.

### 18. Streaming Status Events
Real-time progress messages via SSE between processing steps.

### 19. Multi-Format Document Ingestion
PDF, CSV, DOCX, JSON parsers with type-aware chunk sizes. CSV auto-detects encoding.

### 20. Incremental Ingest with Garbage Collection
Content-hash tracking. `--gc-only` removes orphans. `--force` rebuilds.

### 21. Dynamic Temperature Control
Market (0.2), RAG (0.3), General (0.6), Supervisor synthesizer (0.3).

### 22. Hybrid RAG Enrichment
Knowledge queries with tickers optionally fetch real-time market data. Failure silently degrades.

## Prompt Design

| Prompt | Strategy |
|--------|----------|
| **Router** | 3 categories + `query_complexity` + `is_compound` + `sub_tasks`. Temperature=0.0. |
| **Market (simple)** | 3-5 sentence template. Temperature=0.2. |
| **Market (detailed)** | 6-section template with no-data fallback rules. Temperature=0.2. |
| **RAG (high/medium/low)** | Confidence-tiered prompts. Temperature=0.3. |
| **Supervisor** | Anti-hallucination: no invented causal links, cite sources, note data gaps. Token budgets: ≤800 chars/ticker, ≤3000 chars knowledge. Temperature=0.3. |

## Data Sources

| Source | Usage | Cache TTL |
|--------|-------|-----------|
| Yahoo Finance (prices) | Stock prices, fundamentals, historical data | 5 min |
| Yahoo Finance (news) | Recent headlines for evidence-based analysis | 10 min |
| Claude Web Search | Historical news fallback | None |
| ChromaDB Knowledge Base | 20+ financial knowledge docs, 12 topics | Static |
| PostgreSQL | Full chat history, message metadata | Persistent |
| Redis | Session context (last 10 messages) | 30 min |

### Knowledge Base (20 documents)

**Concepts (12):** P/E ratio, market cap, revenue vs. profit, EPS, dividends, ETF, bull/bear markets, moving averages, options basics, bonds, inflation & interest rates, short selling

**Analysis (8):** Balance sheet, income statement, cash flow, valuation methods, risk management, reading earnings reports, sector rotation, technical patterns

## API Reference

### Health
```
GET /health → { status, knowledge_base_docs, api_key_configured, postgres_connected, redis_connected }
```

### Sessions
```
POST   /sessions              → { id, title, created_at }
GET    /sessions?limit=20     → [{ id, title, created_at, updated_at }]
GET    /sessions/{id}         → { id, title, messages: [...] }
DELETE /sessions/{id}         → { deleted: true }
```

### Chat (SSE Stream)
```
POST /chat
Body: { message: string, session_id?: string, history?: [{role, content}] }

SSE Events:
  routing → status → text (streaming) → sources → done
  Categories: market_data | knowledge | general | compound
```

## Database Schema

```sql
CREATE TABLE sessions (
    id VARCHAR(36) PRIMARY KEY, title VARCHAR(200) DEFAULT 'New chat',
    created_at TIMESTAMP DEFAULT NOW(), updated_at TIMESTAMP DEFAULT NOW()
);

CREATE TABLE messages (
    id SERIAL PRIMARY KEY,
    session_id VARCHAR(36) REFERENCES sessions(id) ON DELETE CASCADE,
    role VARCHAR(20) NOT NULL,        -- 'user' | 'assistant'
    content TEXT NOT NULL,
    routing_category VARCHAR(20),     -- 'market_data' | 'knowledge' | 'general' | 'compound'
    routing_ticker VARCHAR(20), sources JSONB, created_at TIMESTAMP DEFAULT NOW()
);
CREATE INDEX ix_messages_session_created ON messages(session_id, created_at);
```

## Prerequisites

- **Docker Desktop** (for PostgreSQL + Redis)
- **Python 3.10–3.12** (3.13+ has dependency issues; 3.12 recommended)
- **Node.js 18+**

## Quick Start

```bash
docker compose up -d                              # PostgreSQL + Redis
cd backend && python3.12 -m venv venv && source venv/bin/activate
pip install -r requirements.txt                   # First run downloads models (~550MB)
cp .env.example .env                              # Fill in ANTHROPIC_API_KEY
python -m knowledge.ingest                        # Load 20 docs into ChromaDB
uvicorn main:app --port 8000                      # Backend → http://localhost:8000

cd ../frontend && npm install && npm run dev      # Frontend → http://localhost:3000
curl http://localhost:8000/health                  # Verify
```

### Troubleshooting

| Issue | Fix |
|-------|-----|
| `pymupdf` build fails | Use Python 3.12; avoid spaces in project path |
| `onnxruntime not found` | Downgrade to Python 3.12 |
| `np.float_ removed` | `pip install "numpy<2"` |
| ChromaDB telemetry warnings | `export ANONYMIZED_TELEMETRY=False` |
| uvicorn keeps reloading | `--reload-dir . --reload-exclude "venv/*"` |
| yfinance 429 | Wait 10-15 min |
| `docker-compose` not found | Use `docker compose` (no hyphen) |
| SSE not streaming | Set `NEXT_PUBLIC_API_URL=http://localhost:8000` in `frontend/.env.local` |

## Project Structure

```
financial-qa-system/
├── docker-compose.yml
├── backend/
│   ├── main.py
│   ├── agents/
│   │   ├── router.py                   # 3-tier routing
│   │   ├── supervisor.py               # Multi-agent orchestrator
│   │   ├── market_agent.py             # Parallel fetch + dual template
│   │   ├── rag_agent.py               # Hybrid search + confidence-aware
│   │   └── general_agent.py
│   ├── services/
│   │   ├── llm.py, market_data.py, news_service.py
│   │   ├── vector_store.py, database.py, session_cache.py
│   ├── prompts/
│   │   ├── router.py, supervisor.py, market_analysis.py, rag_response.py
│   └── knowledge/
│       ├── parsers/                    # PDF, CSV, DOCX, JSON
│       ├── docs/                       # seed_knowledge.json + file dirs
│       └── ingest.py
└── frontend/
    ├── components/                     # ChatWindow, RoutingBadge, SourcesDisplay, etc.
    └── lib/api.ts
```

## Optimizations & Future Directions

### Performance
- **Router:** Regex handles ~60-70% at zero cost. Local classifier (DistilBERT) could push to 90%+, worth it at >1000 queries/day.
- **Embedding hot-loading:** Cold start ~3s. Pre-load or microservice for <100ms.
- **Smart caching:** LFU with per-ticker TTLs (blue chips 5min, penny stocks 1min).

### Retrieval Quality
- **Hybrid RAG → GraphRAG:** Beyond ~200 docs, knowledge graphs enable multi-hop reasoning.
- **Adaptive chunking:** Semantic splitting on embedding similarity drops.
- **Query expansion:** Auto-generate variants ("市盈率" → "P/E ratio") and merge results.

### Architecture
- **Multi-worker:** TTLCache → Redis for shared caching.
- **WebSocket:** Server-push for price alerts and notifications.
- **Streaming Supervisor:** Start synthesizing before all sub-tasks complete.

### Features
- **Interactive candlestick charts** with news event overlay
- **File upload API** for user-provided earnings reports
- **User authentication** with watchlists
- **Backtesting:** "What if I followed this strategy" queries

---

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
  │ 30分钟TTL│   │   完整历史  │    │  (多ticker检测) │
  └──────────┘   └─────────────┘    │ Tier 1: regex  │
                                    │  (单agent分类)  │
                                    │ Tier 2: Claude │
                                    │  (tool_use兜底) │
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
                    │  (复合查询) │
                    │ 并行收集数据│
                    │ → 合成器LLM│
                    └─────────────┘
```

## 核心设计决策

### 1. 三层查询路由
Tier 0：正则检测多 ticker 复合查询 → Supervisor。Tier 1：正则预过滤 + 决策矩阵。Tier 2：Claude `tool_use` 兜底。

### 2. 多 Agent Supervisor
并行扇出到 services 层收集数据 → 汇总 → 合成器 LLM 统一分析。子任务上限 5 个。

### 3. 后端计算，大模型解释
所有数值预计算。数据异常自动标记。LLM 只负责解释。

### 4. 时间窗口智能解析
支持相对（"最近一个月"）和绝对（"2025年第四季度"、"1月15日"）。

### 5. 新闻证据链
透明状态（ok/no_news/error）。历史查询 >14 天走 Claude Web Search 兜底。

### 6-22. 其余设计决策
多语言 RAG、双模式历史、原子持久化、历史窗口、自适应模板、数据来源展示、语言匹配、安全校验、并行获取、混合检索、Cross-Encoder 重排序、置信度感知、流式状态、多格式导入、增量 GC、动态 Temperature、交叉引用。

详见英文版对应章节。

## Prompt 设计

| Prompt | 策略 |
|--------|------|
| **路由** | 3 类别 + `is_compound` + `sub_tasks`。temperature=0.0 |
| **行情** | 精简（3-5句）/ 完整（6段 + fallback 规则）。temperature=0.2 |
| **RAG** | 高/中/低置信度三级。temperature=0.3 |
| **Supervisor** | 反幻觉规则 + Token 预算。temperature=0.3 |

## 快速启动

```bash
docker compose up -d
cd backend && python3.12 -m venv venv && source venv/bin/activate
pip install -r requirements.txt && cp .env.example .env
python -m knowledge.ingest && uvicorn main:app --port 8000
cd ../frontend && npm install && npm run dev
```

## 优化与扩展思考

### 性能优化
- **路由：** regex 覆盖 60-70%，本地分类器可提升至 90%+
- **嵌入模型：** 冷启动 ~3s，可预加载或独立微服务
- **缓存：** LFU + 差异化 TTL

### 功能扩展
- **交互式 K 线图** + 新闻标注
- **文件上传 API**
- **用户认证** + 自选股
- **回测集成**
