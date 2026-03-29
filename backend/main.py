"""
Financial Asset QA System — FastAPI Backend (v5)

Changes from v4:
- Extracted _general_agent to agents/general_agent.py (architecture consistency)
- Rate limiting via slowapi (15 req/min on /chat, 60 req/min on /sessions)
- Type annotations unified to X | None (no more Optional)
"""
import os
import re
import json
import logging
import traceback
from typing import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from dotenv import load_dotenv
load_dotenv()

from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded

from agents.router import route_query
from agents.market_agent import handle_market_query
from agents.rag_agent import handle_knowledge_query
from agents.general_agent import handle_general_query
from agents.supervisor import supervise
from services.llm import LLMError
from services.vector_store import get_doc_count
from services import database as db
from services import session_cache as cache

logger = logging.getLogger("financial_qa")


# ---- Pydantic models ----

class HistoryMessage(BaseModel):
    role: str
    content: str

class ChatRequest(BaseModel):
    message: str
    session_id: str | None = None
    history: list[HistoryMessage] = Field(default_factory=list)

class SessionCreate(BaseModel):
    title: str = "New chat"

class HealthResponse(BaseModel):
    status: str
    knowledge_base_docs: int
    api_key_configured: bool
    postgres_connected: bool
    redis_connected: bool


# ---- Constants ----

MAX_LLM_CONTEXT_TURNS = 3
MAX_SESSION_LIST_LIMIT = 100
MAX_MESSAGE_LENGTH = 4000


# ---- Rate Limiter ----
# Uses in-memory storage (single worker). For multi-worker, swap to Redis:
#   Limiter(key_func=get_remote_address, storage_uri=os.getenv("REDIS_URL"))
# Behind reverse proxy (nginx), configure trusted X-Forwarded-For headers.

limiter = Limiter(key_func=get_remote_address)


# ---- Lifecycle ----

@asynccontextmanager
async def lifespan(app: FastAPI):
    api_ok = bool(os.getenv("ANTHROPIC_API_KEY"))
    kb_count = get_doc_count()
    from knowledge.ingest import ingest
    try:
        ingest(force=False)  # 增量，只处理新文件
    except Exception as e:
        print(f"   Auto-ingest failed: {e}")
    pg_ok = False
    try:
        await db.init_db()
        pg_ok = True
    except Exception as e:
        print(f"   PostgreSQL init failed: {e}")
    redis_ok = await cache.ping()

    print("=" * 50)
    print("Financial QA System v5 starting")
    print(f"   API Key:      {'OK' if api_ok else 'NOT SET'}")
    print(f"   PostgreSQL:   {'connected' if pg_ok else 'unavailable'}")
    print(f"   Redis:        {'connected' if redis_ok else 'unavailable'}")
    print(f"   Knowledge DB: {kb_count} docs")
    print("=" * 50)
    yield
    await cache.close_redis()
    await db.close_db()


# ---- App ----

app = FastAPI(title="Financial Asset QA System", version="5.0.0", lifespan=lifespan)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

_origins_raw = os.getenv("ALLOWED_ORIGINS", "")
_origins = [o.strip() for o in _origins_raw.split(",") if o.strip()]

if not _origins:
    app.add_middleware(
        CORSMiddleware, allow_origins=["*"], allow_credentials=False,
        allow_methods=["*"], allow_headers=["*"],
    )
else:
    app.add_middleware(
        CORSMiddleware, allow_origins=_origins, allow_credentials=True,
        allow_methods=["*"], allow_headers=["*"],
    )


# ---- Helpers ----

def sse_event(data: dict) -> str:
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"


def truncate_history(messages: list[dict], max_turns: int = MAX_LLM_CONTEXT_TURNS) -> list[dict]:
    """Sliding window for LLM context. Full history stays in PG.
    Ensures first message is 'user' role (P1-1)."""
    valid = [
        {"role": m["role"], "content": m["content"]}
        for m in messages
        if m.get("role") in ("user", "assistant") and m.get("content")
    ]
    truncated = valid[-(max_turns * 2):]
    if truncated and truncated[0]["role"] == "assistant":
        truncated = truncated[1:]
    return truncated


async def load_session_history(session_id: str) -> list[dict]:
    """Redis (hot) → PostgreSQL (cold) → backfill Redis."""
    context = await cache.get_context(session_id)
    if context is not None:
        return context
    try:
        messages = await db.get_recent_messages(session_id, limit=10)
        if messages:
            await cache.set_context(session_id, messages)
        return messages
    except Exception:
        return []


# ---- Routes: Health ----

@app.get("/health", response_model=HealthResponse)
async def health_check():
    redis_ok = await cache.ping()
    pg_ok = False
    try:
        await db.list_sessions(limit=1)
        pg_ok = True
    except Exception:
        pass
    return HealthResponse(
        status="ok", knowledge_base_docs=get_doc_count(),
        api_key_configured=bool(os.getenv("ANTHROPIC_API_KEY")),
        postgres_connected=pg_ok, redis_connected=redis_ok,
    )


# ---- Routes: Sessions ----

@app.post("/sessions")
@limiter.limit("60/minute")
async def create_session(request: Request, body: SessionCreate):
    try:
        return await db.create_session(title=body.title)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/sessions")
@limiter.limit("60/minute")
async def list_sessions(request: Request, limit: int = Query(default=20, ge=1, le=MAX_SESSION_LIST_LIMIT)):
    try:
        return await db.list_sessions(limit=limit)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/sessions/{session_id}")
async def get_session(session_id: str):
    try:
        session = await db.get_session(session_id)
        if not session:
            raise HTTPException(status_code=404, detail="Session not found")
        return session
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.delete("/sessions/{session_id}")
async def delete_session(session_id: str):
    try:
        if not await db.delete_session(session_id):
            raise HTTPException(status_code=404, detail="Session not found")
        await cache.invalidate_context(session_id)
        return {"deleted": True}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    
@app.post("/ingest")
@limiter.limit("5/minute")
async def trigger_ingest(request: Request):
    try:
        from knowledge.ingest import ingest
        import asyncio
        result = await asyncio.to_thread(ingest, force=False)
        return {"status": "ok", "docs": get_doc_count()}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ---- Routes: Chat ----

@app.post("/chat")
@limiter.limit("15/minute")
async def chat(request: Request, chat_request: ChatRequest):
    query = chat_request.message.strip()
    if not query:
        raise HTTPException(status_code=400, detail="Message cannot be empty")
    if len(query) > MAX_MESSAGE_LENGTH:
        raise HTTPException(status_code=400, detail=f"Message too long (max {MAX_MESSAGE_LENGTH} chars)")

    session_id = chat_request.session_id
    inline_history = [m.model_dump() for m in chat_request.history]

    async def event_stream() -> AsyncGenerator[str, None]:
        nonlocal session_id

        if not os.getenv("ANTHROPIC_API_KEY"):
            yield sse_event({"type": "error", "content": "ANTHROPIC_API_KEY is not configured."})
            yield sse_event({"type": "done"})
            return

        # Auto-create session
        if not session_id:
            try:
                new_session = await db.create_session(title=query[:80])
                session_id = new_session["id"]
                logger.info("Auto-created session=%s", session_id)
            except Exception:
                session_id = None
                logger.warning("Session auto-creation failed, stateless mode")

        # Load history
        if session_id:
            raw_history = await load_session_history(session_id)
        else:
            raw_history = inline_history
        history = truncate_history(raw_history, max_turns=MAX_LLM_CONTEXT_TURNS)

        # Route
        cn = bool(re.search(r"[\u4e00-\u9fff]", query))
        yield sse_event({"type": "status", "content": "正在分析问题..." if cn else "Analyzing query..."})

        try:
            routing = await route_query(query)
        except Exception as e:
            yield sse_event({"type": "error", "content": f"Routing failed: {e}"})
            yield sse_event({"type": "done"})
            return

        category = routing.get("category", "general")
        ticker = routing.get("ticker", "")
        company_name = routing.get("company_name", "")
        complexity = routing.get("query_complexity", "detailed")

        logger.info("Routed: session=%s cat=%s ticker=%s complexity=%s",
                     session_id or "stateless", category, ticker or "-", complexity)

        yield sse_event({
            "type": "routing", "category": category, "ticker": ticker,
            "company_name": company_name, "session_id": session_id or "",
        })

        # Select agent — compound queries go to supervisor
        if routing.get("is_compound") and routing.get("sub_tasks"):
            agent = supervise(query, routing["sub_tasks"], history=history)
        elif category == "market_data":
            agent = handle_market_query(query, ticker, company_name, history=history, query_complexity=complexity)
        elif category == "knowledge":
            agent = handle_knowledge_query(query, history=history, query_complexity=complexity, ticker=ticker)
        else:
            agent = handle_general_query(query, history=history, query_complexity=complexity)
        
        # Stream + accumulate (forwards ALL event types: text, status, sources)
        full_response = []
        sources_data = None
        try:
            async for event in agent:
                event_type = event.get("type", "text")
                yield sse_event({"type": event_type, "content": event.get("content", "")})
                if event_type == "text":
                    full_response.append(event.get("content", ""))
                elif event_type == "sources":
                    sources_data = event.get("content")
        except LLMError as e:
            yield sse_event({"type": "error", "content": f"AI service error: {e}"})
        except Exception as e:
            traceback.print_exc()
            yield sse_event({"type": "error", "content": f"Internal error: {e}"})

        # Persist (PG and Redis errors logged separately)
        if session_id:
            assistant_text = "".join(full_response)
            try:
                await db.add_message_pair(
                    session_id=session_id, user_content=query,
                    assistant_content=assistant_text, routing_category=category,
                    routing_ticker=ticker, sources=sources_data,
                )
                logger.info("PG persisted: session=%s", session_id)
            except Exception as e:
                logger.error("PG persist failed: session=%s error=%s", session_id, e)
            try:
                await cache.append_pair_to_context(session_id, query, assistant_text)
                logger.info("Redis updated: session=%s", session_id)
            except Exception as e:
                logger.warning("Redis cache update failed: session=%s error=%s", session_id, e)

        yield sse_event({"type": "done"})

    return StreamingResponse(
        event_stream(), media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"},
    )

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
