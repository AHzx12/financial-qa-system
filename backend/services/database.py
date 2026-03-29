"""
PostgreSQL database layer — SQLAlchemy async.

Tables: sessions, messages.
Index: messages(session_id, created_at) composite.

Key addition: add_message_pair() inserts user + assistant messages in a single
transaction — if either fails, both roll back. Prevents orphaned user messages.
"""
import os
import uuid
import logging
from datetime import datetime


from sqlalchemy import (
    Column, String, Text, DateTime, Integer, ForeignKey, JSON, Index,
    select, desc, delete,
)
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.orm import DeclarativeBase, relationship

logger = logging.getLogger("database")

VALID_ROLES = {"user", "assistant"}
VALID_ROUTING_CATEGORIES = {"market_data", "knowledge", "general", None}


class Base(DeclarativeBase):
    pass


class Session(Base):
    __tablename__ = "sessions"
    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    title = Column(String(200), default="New chat")
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    messages = relationship("Message", back_populates="session", cascade="all, delete-orphan",
                            order_by="Message.created_at")


class Message(Base):
    __tablename__ = "messages"
    __table_args__ = (
        Index("ix_messages_session_created", "session_id", "created_at"),
    )
    id = Column(Integer, primary_key=True, autoincrement=True)
    session_id = Column(String(36), ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False)
    role = Column(String(20), nullable=False)
    content = Column(Text, nullable=False)
    routing_category = Column(String(20), nullable=True)
    routing_ticker = Column(String(20), nullable=True)
    sources = Column(JSON, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    session = relationship("Session", back_populates="messages")


# ---- Engine ----

_engine = None
_session_factory = None


def _get_database_url() -> str:
    url = os.getenv("DATABASE_URL", "")
    if not url:
        raise RuntimeError("DATABASE_URL not set.")
    return url


def get_engine():
    global _engine
    if _engine is None:
        _engine = create_async_engine(_get_database_url(), echo=False, pool_size=5, max_overflow=10)
    return _engine


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    global _session_factory
    if _session_factory is None:
        _session_factory = async_sessionmaker(get_engine(), expire_on_commit=False)
    return _session_factory


async def init_db():
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def close_db():
    global _engine, _session_factory
    if _engine:
        await _engine.dispose()
        _engine = None
        _session_factory = None


# ---- Validation ----

def _validate_message_fields(role: str, routing_category: str = None):
    if role not in VALID_ROLES:
        raise ValueError(f"Invalid role '{role}'. Must be one of: {VALID_ROLES}")
    if routing_category not in VALID_ROUTING_CATEGORIES:
        raise ValueError(f"Invalid routing_category '{routing_category}'.")


# ---- CRUD: Sessions ----

async def create_session(title: str = "New chat") -> dict:
    factory = get_session_factory()
    async with factory() as db:
        session = Session(title=title)
        db.add(session)
        await db.commit()
        await db.refresh(session)
        return {"id": session.id, "title": session.title, "created_at": session.created_at.isoformat()}


async def list_sessions(limit: int = 20) -> list[dict]:
    factory = get_session_factory()
    async with factory() as db:
        result = await db.execute(select(Session).order_by(desc(Session.updated_at)).limit(limit))
        return [
            {"id": s.id, "title": s.title, "created_at": s.created_at.isoformat(),
             "updated_at": s.updated_at.isoformat()}
            for s in result.scalars().all()
        ]


async def get_session(session_id: str) -> dict | None:
    factory = get_session_factory()
    async with factory() as db:
        result = await db.execute(select(Session).where(Session.id == session_id))
        session = result.scalar_one_or_none()
        if not session:
            return None
        msg_result = await db.execute(
            select(Message).where(Message.session_id == session_id).order_by(Message.created_at)
        )
        return {
            "id": session.id, "title": session.title,
            "created_at": session.created_at.isoformat(), "updated_at": session.updated_at.isoformat(),
            "messages": [
                {"id": m.id, "role": m.role, "content": m.content,
                 "routing_category": m.routing_category, "routing_ticker": m.routing_ticker,
                 "sources": m.sources, "created_at": m.created_at.isoformat()}
                for m in msg_result.scalars().all()
            ],
        }


async def delete_session(session_id: str) -> bool:
    factory = get_session_factory()
    async with factory() as db:
        result = await db.execute(delete(Session).where(Session.id == session_id))
        await db.commit()
        return result.rowcount > 0


# ---- CRUD: Messages ----

async def add_message(
    session_id: str, role: str, content: str,
    routing_category: str = None, routing_ticker: str = None, sources: dict = None,
) -> dict:
    """Add a single message. Validates fields and session existence."""
    _validate_message_fields(role, routing_category)
    factory = get_session_factory()
    async with factory() as db:
        result = await db.execute(select(Session).where(Session.id == session_id))
        session = result.scalar_one_or_none()
        if not session:
            raise ValueError(f"Session '{session_id}' does not exist")
        msg = Message(session_id=session_id, role=role, content=content,
                      routing_category=routing_category, routing_ticker=routing_ticker, sources=sources)
        db.add(msg)
        session.updated_at = datetime.utcnow()
        if session.title == "New chat" and role == "user":
            session.title = content[:80]
        await db.commit()
        await db.refresh(msg)
        return {"id": msg.id, "role": msg.role, "content": msg.content,
                "created_at": msg.created_at.isoformat()}


async def add_message_pair(
    session_id: str,
    user_content: str,
    assistant_content: str,
    routing_category: str = None,
    routing_ticker: str = None,
    sources: dict = None,
) -> dict:
    """
    Insert user + assistant messages in a SINGLE TRANSACTION.
    If either insert fails, both roll back. Prevents orphaned messages.

    Returns: {"user_id": int, "assistant_id": int}
    """
    _validate_message_fields("user", routing_category)
    _validate_message_fields("assistant", routing_category)

    factory = get_session_factory()
    async with factory() as db:
        # Verify session
        result = await db.execute(select(Session).where(Session.id == session_id))
        session = result.scalar_one_or_none()
        if not session:
            raise ValueError(f"Session '{session_id}' does not exist")

        # Create both messages
        user_msg = Message(
            session_id=session_id, role="user", content=user_content,
            routing_category=routing_category, routing_ticker=routing_ticker,
        )
        assistant_msg = Message(
            session_id=session_id, role="assistant", content=assistant_content,
            routing_category=routing_category, routing_ticker=routing_ticker,
            sources=sources,
        )
        db.add(user_msg)
        db.add(assistant_msg)

        # Touch session
        session.updated_at = datetime.utcnow()
        if session.title == "New chat":
            session.title = user_content[:80]

        # Single commit — both messages or neither
        await db.commit()
        await db.refresh(user_msg)
        await db.refresh(assistant_msg)

        logger.info(
            "Persisted message pair: session=%s user_id=%d assistant_id=%d",
            session_id, user_msg.id, assistant_msg.id,
        )
        return {"user_id": user_msg.id, "assistant_id": assistant_msg.id}


async def get_recent_messages(session_id: str, limit: int = 10) -> list[dict]:
    factory = get_session_factory()
    async with factory() as db:
        result = await db.execute(
            select(Message).where(Message.session_id == session_id)
            .order_by(desc(Message.created_at)).limit(limit)
        )
        messages = list(reversed(result.scalars().all()))
        return [{"role": m.role, "content": m.content} for m in messages]
