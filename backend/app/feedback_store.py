"""
feedback_store.py

Postgres persistence for user feedback.

Why a separate database from Neo4j: the graph is rebuilt by loading a dump, and
anything a user typed must never live somewhere that a restore could overwrite.
Feedback is also relational, small, and queryable in ways a graph adds nothing to.

Design notes:

  * Every function here is written so a database problem degrades the feature
    rather than the app. The query pipeline never touches this module, so if
    Postgres is down the Explorer keeps answering questions and only the feedback
    form reports an error.
  * The schema is created on startup. One table does not justify Alembic yet, but
    it will as soon as a second table arrives or a column needs changing on a
    database that already holds rows worth keeping.
  * `user_name` is a free text field today. When CCHMC SAML lands it should be
    populated from the authenticated principal instead, so the column stays and
    only the writer changes. See `submit_feedback`.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import (
    Column,
    DateTime,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    create_engine,
    desc,
    func,
    insert,
    select,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError

from .settings import settings

log = logging.getLogger(__name__)

# Core rather than a declarative model: this is one flat table and Core keeps the
# resulting SQL obvious.
metadata = MetaData()

feedback_table = Table(
    "feedback",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    # Free text today, the authenticated principal once SSO is in place.
    Column("user_name", String(200), nullable=False, server_default=""),
    Column("question", Text, nullable=False, server_default=""),
    Column("answer", Text, nullable=False, server_default=""),
    Column("mode", String(40), nullable=True),
    Column("intent", String(40), nullable=True),
    Column("skill", String(80), nullable=True),
    Column("comment", Text, nullable=False),
    # Everything the app knows about how the answer was produced: stages with
    # timings, retrieved and judged counts, the cutoff, per faculty judgements
    # with scores and rationales, and the Cypher when there was any.
    Column("trace_snapshot", JSONB, nullable=True),
    Column(
        "created_at",
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    ),
)

_engine: Engine | None = None


def get_engine() -> Engine:
    global _engine
    if _engine is None:
        _engine = create_engine(
            settings.database_url,
            pool_pre_ping=True,
            pool_size=5,
            max_overflow=5,
            future=True,
        )
    return _engine


def dispose_engine() -> None:
    global _engine
    if _engine is not None:
        _engine.dispose()
        _engine = None


def init_schema() -> None:
    """
    Create the table if it is not there yet.

    Safe to call on every boot. Raises so the caller can log it and carry on:
    a missing feedback table must not stop the Explorer from answering questions.
    """
    engine = get_engine()
    metadata.create_all(engine, tables=[feedback_table])
    log.info("Feedback table is ready")


def is_available() -> bool:
    try:
        with get_engine().connect() as conn:
            conn.execute(select(1))
        return True
    except SQLAlchemyError as exc:
        log.warning("Feedback database is not reachable: %s", exc)
        return False


def submit_feedback(
    *,
    user_name: str,
    question: str,
    answer: str,
    mode: str | None,
    intent: str | None,
    skill: str | None,
    comment: str,
    trace_snapshot: dict[str, Any] | None,
) -> int:
    """
    Insert one feedback row and return its id.

    When SSO arrives, `user_name` should stop coming from the request body and be
    taken from the session instead. That is the only change needed here.
    """
    engine = get_engine()
    statement = (
        insert(feedback_table)
        .values(
            user_name=(user_name or "").strip()[:200],
            question=question or "",
            answer=answer or "",
            mode=(mode or None),
            intent=(intent or None),
            skill=(skill or None),
            comment=comment,
            trace_snapshot=trace_snapshot,
            created_at=datetime.now(timezone.utc),
        )
        .returning(feedback_table.c.id)
    )
    with engine.begin() as conn:
        row = conn.execute(statement).first()
    return int(row[0]) if row else 0


def list_feedback(limit: int = 200, offset: int = 0) -> tuple[list[dict[str, Any]], int]:
    """Newest first, with the total count so the admin view can show progress."""
    engine = get_engine()
    with engine.connect() as conn:
        total = conn.execute(select(func.count()).select_from(feedback_table)).scalar_one()
        rows = conn.execute(
            select(feedback_table)
            .order_by(desc(feedback_table.c.created_at), desc(feedback_table.c.id))
            .limit(limit)
            .offset(offset)
        ).mappings()
        items = [_serialise(dict(row)) for row in rows]
    return items, int(total)


def _serialise(row: dict[str, Any]) -> dict[str, Any]:
    created = row.get("created_at")
    return {
        "id": row.get("id"),
        "userName": row.get("user_name") or "",
        "question": row.get("question") or "",
        "answer": row.get("answer") or "",
        "mode": row.get("mode"),
        "intent": row.get("intent"),
        "skill": row.get("skill"),
        "comment": row.get("comment") or "",
        "traceSnapshot": row.get("trace_snapshot"),
        "createdAt": created.isoformat() if hasattr(created, "isoformat") else str(created),
    }
