"""
chat_store.py

Per user chat history in Postgres.

Why this exists: history used to live in the browser's localStorage, which is
scoped to the browser, not the person. Two people sharing a PC saw each other's
conversations, and nobody's history followed them to another machine. Once the
proxy tells us who the user is, history belongs on the server keyed by that user.

Design notes:

  * Every read and write is scoped by `user_id` taken from the authenticated
    request. A conversation id from the client is never trusted on its own, it is
    always matched together with the owner, so one user cannot read or delete
    another's conversation by guessing an id.
  * Messages are stored as JSONB rather than a wide table. The message shape is
    the frontend's `ChatMessage`, which includes the whole `QueryResponse`, and
    modelling that relationally would buy nothing and break every time the
    response shape changes.
  * Kept in the same database as feedback, deliberately apart from Neo4j, because
    the graph is rebuilt from a dump and user data must never sit somewhere a
    restore could overwrite.
  * Schema is created on startup, same as feedback. Two tables still does not
    justify Alembic, but a third change to an existing column will.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import (
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Table,
    Text,
    delete,
    func,
    insert,
    select,
    update,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import SQLAlchemyError

from .feedback_store import get_engine, metadata

log = logging.getLogger(__name__)

# One row per person, created on first sight. `id` is the lower cased username
# from the proxy header, which is stable and already unique.
users_table = Table(
    "users",
    metadata,
    Column("id", String(200), primary_key=True),
    Column("display_name", String(200), nullable=False, server_default=""),
    Column("first_seen", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("last_seen", DateTime(timezone=True), nullable=False, server_default=func.now()),
)

conversations_table = Table(
    "conversations",
    metadata,
    # The client generated id, also used as the pipeline session id. Unique per
    # user rather than globally, so two users cannot collide.
    Column("id", String(64), primary_key=True),
    Column(
        "user_id",
        String(200),
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column("title", Text, nullable=False, server_default=""),
    # "derived" or "generated", mirroring the frontend so a generated title is
    # never recomputed and never regenerated.
    Column("title_source", String(20), nullable=False, server_default="derived"),
    Column("messages", JSONB, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Index("ix_conversations_user_updated", "user_id", "updated_at"),
)

# Oldest conversations beyond this are pruned per user, matching the browser cap
# the frontend already applied.
MAX_CONVERSATIONS_PER_USER = 100


def init_schema() -> None:
    """Create the chat tables if absent. Safe on every boot."""
    engine = get_engine()
    metadata.create_all(engine, tables=[users_table, conversations_table])
    log.info("Chat history tables are ready")


def touch_user(user_id: str, display_name: str) -> None:
    """Record that this person exists and was just seen."""
    engine = get_engine()
    now = datetime.now(timezone.utc)
    with engine.begin() as conn:
        existing = conn.execute(
            select(users_table.c.id).where(users_table.c.id == user_id)
        ).first()
        if existing is None:
            conn.execute(
                insert(users_table).values(
                    id=user_id, display_name=display_name, first_seen=now, last_seen=now
                )
            )
        else:
            conn.execute(
                update(users_table)
                .where(users_table.c.id == user_id)
                .values(last_seen=now, display_name=display_name)
            )


def list_conversations(user_id: str) -> list[dict[str, Any]]:
    """
    Every conversation for this user, newest updated first, without messages.

    Messages are excluded because the sidebar only needs titles and timestamps,
    and a user with 100 conversations would otherwise transfer megabytes.
    """
    engine = get_engine()
    with engine.connect() as conn:
        rows = conn.execute(
            select(
                conversations_table.c.id,
                conversations_table.c.title,
                conversations_table.c.title_source,
                conversations_table.c.created_at,
                conversations_table.c.updated_at,
                func.jsonb_array_length(conversations_table.c.messages).label("message_count"),
            )
            .where(conversations_table.c.user_id == user_id)
            .order_by(conversations_table.c.updated_at.desc())
            .limit(MAX_CONVERSATIONS_PER_USER)
        ).mappings()
        return [_summary(dict(row)) for row in rows]


def get_conversation(user_id: str, conversation_id: str) -> dict[str, Any] | None:
    """One conversation with its messages, or None when it is not this user's."""
    engine = get_engine()
    with engine.connect() as conn:
        row = conn.execute(
            select(conversations_table).where(
                conversations_table.c.user_id == user_id,
                conversations_table.c.id == conversation_id,
            )
        ).mappings().first()
    return _full(dict(row)) if row else None


def save_conversation(
    user_id: str,
    conversation_id: str,
    title: str,
    title_source: str,
    messages: list[dict[str, Any]],
) -> dict[str, Any]:
    """
    Insert or update one conversation, then prune this user's oldest.

    An empty conversation is not stored, matching the frontend rule that an
    untouched New Chat stays out of history.
    """
    engine = get_engine()
    now = datetime.now(timezone.utc)
    source = title_source if title_source in {"derived", "generated"} else "derived"

    with engine.begin() as conn:
        # Make sure the owner row exists. Saving must not depend on /api/me having
        # been called first, otherwise the very first save of a session fails on a
        # foreign key violation.
        conn.execute(
            pg_insert(users_table)
            .values(id=user_id, display_name="", first_seen=now, last_seen=now)
            .on_conflict_do_nothing(index_elements=[users_table.c.id])
        )

        existing = conn.execute(
            select(conversations_table.c.id).where(
                conversations_table.c.user_id == user_id,
                conversations_table.c.id == conversation_id,
            )
        ).first()

        if existing is None:
            conn.execute(
                insert(conversations_table).values(
                    id=conversation_id,
                    user_id=user_id,
                    title=title,
                    title_source=source,
                    messages=messages,
                    created_at=now,
                    updated_at=now,
                )
            )
        else:
            conn.execute(
                update(conversations_table)
                .where(
                    conversations_table.c.user_id == user_id,
                    conversations_table.c.id == conversation_id,
                )
                .values(
                    title=title, title_source=source, messages=messages, updated_at=now
                )
            )

        _prune(conn, user_id)

    return {"id": conversation_id, "title": title, "titleSource": source}


def delete_conversation(user_id: str, conversation_id: str) -> bool:
    """Delete one conversation. Returns False when it was not this user's."""
    engine = get_engine()
    with engine.begin() as conn:
        result = conn.execute(
            delete(conversations_table).where(
                conversations_table.c.user_id == user_id,
                conversations_table.c.id == conversation_id,
            )
        )
    return bool(result.rowcount)


def _prune(conn: Any, user_id: str) -> None:
    """Drop this user's oldest conversations beyond the cap."""
    keep = conn.execute(
        select(conversations_table.c.id)
        .where(conversations_table.c.user_id == user_id)
        .order_by(conversations_table.c.updated_at.desc())
        .limit(MAX_CONVERSATIONS_PER_USER)
    ).scalars().all()
    if len(keep) < MAX_CONVERSATIONS_PER_USER:
        return
    conn.execute(
        delete(conversations_table).where(
            conversations_table.c.user_id == user_id,
            conversations_table.c.id.notin_(keep),
        )
    )


def is_available() -> bool:
    try:
        with get_engine().connect() as conn:
            conn.execute(select(1))
        return True
    except SQLAlchemyError:
        return False


def _iso(value: Any) -> str:
    return value.isoformat() if hasattr(value, "isoformat") else str(value)


def _summary(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row["id"],
        "title": row.get("title") or "",
        "titleSource": row.get("title_source") or "derived",
        "createdAt": _iso(row.get("created_at")),
        "updatedAt": _iso(row.get("updated_at")),
        "messageCount": int(row.get("message_count") or 0),
    }


def _full(row: dict[str, Any]) -> dict[str, Any]:
    messages = row.get("messages")
    return {
        **_summary({**row, "message_count": len(messages) if isinstance(messages, list) else 0}),
        "messages": messages if isinstance(messages, list) else [],
    }
