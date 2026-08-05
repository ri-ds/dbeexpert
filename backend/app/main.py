"""
main.py

FastAPI application: health, metadata, and the two query endpoints.

/api/query        runs the pipeline and returns one JSON response
/api/query/stream runs the same pipeline but emits Server Sent Events so the
                  UI can show live pipeline progress instead of a spinner
"""

from __future__ import annotations

import asyncio
import json
import logging
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

from . import db, feedback_store, llm, pipeline
from .faculty import faculty_names
from .ontology import agent_names
from .schemas import (
    FeedbackListResponse,
    FeedbackRequest,
    FeedbackResponse,
    HealthResponse,
    MetaResponse,
    Neo4jHealth,
    OpenAIHealth,
    QueryRequest,
    QueryResponse,
    ResetRequest,
    TitleRequest,
    TitleResponse,
)
from .session import SessionStore
from .settings import settings

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
)
log = logging.getLogger("dbe")

store = SessionStore(settings.session_ttl_s)

MODES = [
    {
        "id": "hybrid",
        "label": "Hybrid graph search",
        "description": (
            "Vector plus keyword search over CV passages, expanded through the "
            "surrounding graph. Best default for expertise questions."
        ),
    },
    {
        "id": "vector",
        "label": "Vector search",
        "description": (
            "Pure semantic similarity over CV passages, without graph expansion. "
            "Faster and narrower."
        ),
    },
    {
        "id": "cypher",
        "label": "Natural language to Cypher",
        "description": (
            "Translates the question into a read only Cypher query, runs it, and "
            "explains the rows. Best for counting and structural questions."
        ),
    },
]


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    log.info("Starting up. Neo4j at %s", settings.neo4j_uri)
    try:
        await asyncio.to_thread(db.verify_connectivity)
        log.info("Neo4j connectivity verified")
    except Exception as exc:
        log.error("Neo4j is not reachable yet: %s", exc)

    if not settings.openai_configured:
        log.warning("OPENAI_API_KEY is not set. Query endpoints will fail until it is.")
    else:
        try:
            info = await llm.probe()
            if info["matches_index"]:
                log.info("Embedding width %d matches the vector index", info["embedding_dimensions"])
            else:
                log.error(
                    "Embedding width %d does not match the vector index width %d. "
                    "Retrieval will return meaningless results. Set OPENAI_EMBEDDING_MODEL "
                    "to a model that produces %d dimensions.",
                    info["embedding_dimensions"],
                    settings.embedding_dimensions,
                    settings.embedding_dimensions,
                )
        except Exception as exc:
            log.warning("Could not verify the embedding model: %s", exc)

    # Feedback storage is optional infrastructure. If it is unavailable the
    # Explorer must still answer questions, so a failure here is logged and the
    # app carries on. Only the feedback endpoints will report an error.
    try:
        await asyncio.to_thread(feedback_store.init_schema)
    except Exception as exc:
        log.warning("Feedback storage is unavailable, the feature will be disabled: %s", exc)

    yield

    await asyncio.to_thread(db.close_driver)
    await asyncio.to_thread(feedback_store.dispose_engine)
    log.info("Shut down cleanly")


app = FastAPI(
    title="DBE Faculty Expertise API",
    version=settings.version,
    lifespan=lifespan,
    docs_url=f"{settings.api_prefix}/docs",
    openapi_url=f"{settings.api_prefix}/openapi.json",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=list(settings.cors_origins),
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)


# ----------------------------------------------------------------------
# Health and metadata
# ----------------------------------------------------------------------

@app.get(f"{settings.api_prefix}/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    neo4j_health = Neo4jHealth(connected=False)
    try:
        counts = await asyncio.to_thread(db.graph_counts)
        neo4j_health = Neo4jHealth(
            connected=True,
            nodes=counts["nodes"],
            relationships=counts["relationships"],
        )
    except Exception as exc:
        neo4j_health = Neo4jHealth(connected=False, error=str(exc)[:300])

    openai_health = OpenAIHealth(
        configured=settings.openai_configured,
        chatModel=settings.chat_model,
        embeddingModel=settings.embedding_model,
    )

    status = "ok" if neo4j_health.connected and openai_health.configured else "degraded"
    return HealthResponse(
        status=status, neo4j=neo4j_health, openai=openai_health, version=settings.version
    )


@app.get(f"{settings.api_prefix}/meta", response_model=MetaResponse)
async def meta() -> MetaResponse:
    payload: dict[str, Any] = {
        "faculty": list(faculty_names()),
        "modes": MODES,
        "agents": agent_names(),
        "documentCategories": [],
        "graph": {"nodes": 0, "relationships": 0, "labels": [], "relTypes": []},
    }

    try:
        counts, labels, rel_types, categories = await asyncio.gather(
            asyncio.to_thread(db.graph_counts),
            asyncio.to_thread(db.label_counts),
            asyncio.to_thread(db.rel_type_counts),
            asyncio.to_thread(db.document_categories),
        )
        payload["graph"] = {
            "nodes": counts["nodes"],
            "relationships": counts["relationships"],
            "labels": labels,
            "relTypes": rel_types,
        }
        payload["documentCategories"] = categories
    except Exception as exc:
        log.warning("Could not load graph metadata: %s", exc)

    return MetaResponse(**payload)


# ----------------------------------------------------------------------
# Query
# ----------------------------------------------------------------------

@app.post(f"{settings.api_prefix}/query", response_model=QueryResponse)
async def query(request: QueryRequest) -> QueryResponse:
    _require_openai()
    try:
        payload = await pipeline.run_query(
            question=request.question,
            mode=request.mode,
            session_id=request.sessionId,
            store=store,
            agent_override=request.agent,
        )
    except Exception as exc:
        log.exception("Query failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return QueryResponse(**payload)


@app.post(f"{settings.api_prefix}/query/stream")
async def query_stream(request: QueryRequest) -> StreamingResponse:
    _require_openai()

    async def event_stream() -> AsyncIterator[bytes]:
        queue: asyncio.Queue[tuple[str, dict[str, Any]] | None] = asyncio.Queue()

        async def emit(event: str, payload: dict[str, Any]) -> None:
            await queue.put((event, payload))

        async def run() -> None:
            try:
                payload = await pipeline.run_query(
                    question=request.question,
                    mode=request.mode,
                    session_id=request.sessionId,
                    store=store,
                    agent_override=request.agent,
                    emit=emit,
                )
                await queue.put(("result", payload))
            except Exception as exc:
                log.exception("Streaming query failed")
                await queue.put(("error", {"message": str(exc)}))
            finally:
                await queue.put(None)

        task = asyncio.create_task(run())

        try:
            while True:
                item = await queue.get()
                if item is None:
                    break
                event, payload = item
                yield _sse(event, payload)
            yield _sse("done", {})
        except asyncio.CancelledError:
            # The client hung up, so stop the pipeline rather than let it finish
            # its remaining OpenAI calls for nobody.
            task.cancel()
            raise
        finally:
            if not task.done():
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            # Stops nginx from buffering the stream when it proxies this route.
            "X-Accel-Buffering": "no",
        },
    )


@app.post(f"{settings.api_prefix}/title", response_model=TitleResponse)
async def title(request: TitleRequest) -> TitleResponse:
    """
    Name a conversation. Called once per conversation, never per message.

    Returns an empty title rather than an error when generation fails, so a
    naming problem can never interfere with asking questions.
    """
    if not settings.openai_configured:
        return TitleResponse(title="")
    return TitleResponse(title=await pipeline.generate_title(request.question))


@app.post(f"{settings.api_prefix}/session/reset")
async def reset_session(request: ResetRequest) -> dict[str, bool]:
    store.reset(request.sessionId)
    return {"ok": True}


# ----------------------------------------------------------------------
# Feedback
# ----------------------------------------------------------------------

@app.post(f"{settings.api_prefix}/feedback", response_model=FeedbackResponse)
async def submit_feedback(request: FeedbackRequest) -> FeedbackResponse:
    """
    Record feedback about one answer.

    When CCHMC SSO lands, `userName` should come from the authenticated session
    rather than the request body, and the client should stop sending it.
    """
    try:
        new_id = await asyncio.to_thread(
            feedback_store.submit_feedback,
            user_name=request.userName,
            question=request.question,
            answer=request.answer,
            mode=request.mode,
            intent=request.intent,
            skill=request.skill,
            comment=request.comment,
            trace_snapshot=request.traceSnapshot,
        )
    except Exception as exc:
        log.exception("Could not store feedback")
        raise HTTPException(
            status_code=503,
            detail=(
                "Feedback could not be saved because the feedback database is not "
                "reachable. Your answer is unaffected. Please try again later."
            ),
        ) from exc

    log.info("Stored feedback %s from %r", new_id, request.userName or "anonymous")
    return FeedbackResponse(ok=True, id=new_id)


# ----------------------------------------------------------------------
# TEMPORARY diagnostic. Delete once identity is wired up.
# ----------------------------------------------------------------------

# Header names that a reverse proxy or SAML service provider might use to pass
# the signed in user through to an application.
_IDENTITY_HEADERS = (
    "x-forwarded-user", "x-forwarded-email", "x-forwarded-preferred-username",
    "x-remote-user", "remote-user", "x-authenticated-user", "x-user", "x-username",
    "x-auth-request-user", "x-auth-request-email", "x-auth-request-preferred-username",
    "eppn", "x-eppn", "mail", "x-mail", "displayname", "x-displayname",
    "uid", "x-uid", "samaccountname", "x-samaccountname", "sn", "givenname",
    "x-shib-eppn", "x-shib-mail", "x-shib-displayname", "x-shib-uid",
    "shib-eppn", "shib-mail", "shib-identity-provider",
    "x-forwarded-groups", "x-auth-request-groups", "ismemberof", "x-ismemberof",
)

# Never echoed, because they carry the session itself.
_SECRET_HEADERS = ("cookie", "authorization", "proxy-authorization", "x-admin-password")


@app.get(f"{settings.api_prefix}/whoami")
async def whoami(request: Request) -> dict[str, Any]:
    """
    Show whether the reverse proxy tells this app who the signed in user is.

    Deliberately safe to open in a browser: it lists the NAMES of every header
    received, but only reveals VALUES for known identity headers. Cookies and
    authorization headers are never echoed, only reported as present.

    This exists to answer one question, whether per user chat history is possible
    without asking IT for a change. Delete it once identity is wired up.
    """
    incoming = {k.lower(): v for k, v in request.headers.items()}

    identity = {k: v for k, v in incoming.items() if k in _IDENTITY_HEADERS}
    return {
        "identityFound": bool(identity),
        "identityHeaders": identity,
        "allHeaderNames": sorted(incoming.keys()),
        "secretsPresentButHidden": [h for h in _SECRET_HEADERS if h in incoming],
        "note": (
            "identityFound true means per user history can be built now. "
            "False means the proxy authenticates but does not forward the user, "
            "and IT need to add one header."
        ),
    }


async def require_admin(x_admin_password: str = Header(default="")) -> None:
    """
    Temporary gate on the admin view.

    A shared password sent in a header, which is adequate only because this is a
    placeholder. When CCHMC SSO is added this dependency should check a group
    claim on the session instead, and the password should be deleted outright.
    Compared with `secrets.compare_digest` to avoid leaking length by timing.
    """
    import secrets

    expected = settings.admin_password
    if not expected or not secrets.compare_digest(x_admin_password, expected):
        raise HTTPException(status_code=401, detail="Incorrect admin password.")


@app.get(
    f"{settings.api_prefix}/admin/feedback",
    response_model=FeedbackListResponse,
    dependencies=[Depends(require_admin)],
)
async def list_feedback(limit: int = 200, offset: int = 0) -> FeedbackListResponse:
    limit = max(1, min(limit, 500))
    offset = max(0, offset)
    try:
        items, total = await asyncio.to_thread(feedback_store.list_feedback, limit, offset)
    except Exception as exc:
        log.exception("Could not read feedback")
        raise HTTPException(
            status_code=503, detail="The feedback database is not reachable."
        ) from exc
    return FeedbackListResponse(items=items, total=total)


def _sse(event: str, payload: dict[str, Any]) -> bytes:
    body = json.dumps(payload, ensure_ascii=False, default=str)
    return f"event: {event}\ndata: {body}\n\n".encode("utf-8")


def _require_openai() -> None:
    if not settings.openai_configured:
        raise HTTPException(
            status_code=503,
            detail=(
                "OPENAI_API_KEY is not configured on the backend. Add it to the .env "
                "file at the project root and restart the backend container."
            ),
        )
