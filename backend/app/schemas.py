"""
schemas.py

Request and response models. These mirror types.ts on the frontend exactly, so
any change here needs the same change there.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

Mode = Literal["hybrid", "vector", "cypher"]


class QueryRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    mode: Mode = "hybrid"
    sessionId: str = Field(min_length=1, max_length=128)
    agent: str | None = None

    @field_validator("question")
    @classmethod
    def not_blank(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("question cannot be blank")
        return cleaned


class FacultyResult(BaseModel):
    name: str
    score: float | None = None
    information: list[str] = Field(default_factory=list)
    # No rationale field here on purpose. The relevance judge's prose is an
    # internal scoring artefact, not an answer, and rendering it was the source
    # of the "fits the category of a faculty name" blurbs. It now travels in
    # Trace.judgements and surfaces only under the pipeline disclosure.


class CypherResult(BaseModel):
    query: str
    params: dict[str, Any] = Field(default_factory=dict)
    columns: list[str] = Field(default_factory=list)
    rows: list[dict[str, Any]] = Field(default_factory=list)
    # "builtin" is a hand written graph skill, "generated" came from the model.
    kind: Literal["builtin", "generated"] = "generated"
    explanation: str | None = None
    # False when the prose answer already states the whole result, so rendering a
    # table as well would show the same data twice. The query itself is always
    # shown either way, since that is what makes the answer auditable.
    showTable: bool = True


class TraceStage(BaseModel):
    stage: str
    label: str
    detail: str | None = None
    ms: int = 0


class Judgement(BaseModel):
    """One relevance verdict, for the pipeline disclosure only."""

    name: str
    score: float
    rationale: str | None = None
    kept: bool = False


class Trace(BaseModel):
    stages: list[TraceStage] = Field(default_factory=list)
    retrievedChunks: int = 0
    judged: int = 0
    kept: int = 0
    cutoff: str | None = None
    # How the question was routed, so the choice is always auditable.
    intent: str | None = None
    skill: str | None = None
    coverage: str | None = None
    judgements: list[Judgement] = Field(default_factory=list)
    # Candidates that passed the cutoff but yielded no extractable evidence.
    # These used to vanish silently, turning "kept 2" into zero results.
    noEvidence: list[str] = Field(default_factory=list)


class Timings(BaseModel):
    totalMs: int = 0


class QueryResponse(BaseModel):
    mode: str
    questionType: str
    # roster, factual, or expertise. Distinct from questionType, which tracks
    # conversational reference (named, followup, first). The two are orthogonal:
    # "how many grants does Cole Brokamp have" is named and factual at once.
    intent: str | None = None
    agent: str | None = None
    answerText: str | None = None
    # How the client should render this answer.
    #
    #   "legacy"  answerText already holds the whole answer, formatted the way
    #             the baseline app formats it, so render that and nothing else.
    #             `faculty` is still populated for feedback and the trace.
    #   None      answerText is prose about a table, or there is none, so the
    #             client falls back to cards and the result table.
    answerFormat: Literal["legacy"] | None = None
    faculty: list[FacultyResult] = Field(default_factory=list)
    cypher: CypherResult | None = None
    trace: Trace = Field(default_factory=Trace)
    timings: Timings = Field(default_factory=Timings)
    sessionId: str


class Neo4jHealth(BaseModel):
    connected: bool
    nodes: int = 0
    relationships: int = 0
    error: str | None = None


class OpenAIHealth(BaseModel):
    configured: bool
    chatModel: str
    embeddingModel: str


class HealthResponse(BaseModel):
    status: Literal["ok", "degraded"]
    neo4j: Neo4jHealth
    openai: OpenAIHealth
    version: str


class ModeInfo(BaseModel):
    id: str
    label: str
    description: str


class LabelCount(BaseModel):
    label: str
    count: int


class RelTypeCount(BaseModel):
    type: str
    count: int


class GraphMeta(BaseModel):
    nodes: int = 0
    relationships: int = 0
    labels: list[LabelCount] = Field(default_factory=list)
    relTypes: list[RelTypeCount] = Field(default_factory=list)


class MetaResponse(BaseModel):
    faculty: list[str] = Field(default_factory=list)
    modes: list[ModeInfo] = Field(default_factory=list)
    agents: list[str] = Field(default_factory=list)
    documentCategories: list[str] = Field(default_factory=list)
    graph: GraphMeta = Field(default_factory=GraphMeta)


class ResetRequest(BaseModel):
    sessionId: str = Field(min_length=1, max_length=128)


class TitleRequest(BaseModel):
    """
    Name a conversation from its first question.

    Only the question is accepted. The answer and the trace are deliberately not
    part of this contract, because sending them would multiply the input cost of
    what is a four word job.
    """

    question: str = Field(min_length=1, max_length=2000)


class TitleResponse(BaseModel):
    # Empty when generation failed, which tells the client to keep the fallback
    # title it already derived locally.
    title: str = ""


# ----------------------------------------------------------------------
# Identity and chat history
# ----------------------------------------------------------------------

class MeResponse(BaseModel):
    """
    Who the caller is, as far as the app can tell.

    `authenticated` false means the reverse proxy let the request through but did
    not forward the user, which is the current state. The client then keeps its
    existing browser local history rather than asking the server for any.
    """

    authenticated: bool = False
    userId: str | None = None
    displayName: str | None = None
    # True when server side history is usable, that is authenticated AND the
    # database is reachable.
    historyEnabled: bool = False
    # Where to send the user to sign out. Null when not configured, in which
    # case the client hides the Sign out button rather than offering a dead link.
    logoutUrl: str | None = None


class ConversationSummary(BaseModel):
    id: str
    title: str = ""
    titleSource: str = "derived"
    createdAt: str
    updatedAt: str
    messageCount: int = 0


class ConversationDetail(ConversationSummary):
    messages: list[dict[str, Any]] = Field(default_factory=list)


class ConversationListResponse(BaseModel):
    conversations: list[ConversationSummary] = Field(default_factory=list)


class SaveConversationRequest(BaseModel):
    id: str = Field(min_length=1, max_length=64)
    title: str = Field(default="", max_length=300)
    titleSource: Literal["derived", "generated"] = "derived"
    # The frontend's ChatMessage list, stored as given. Capped so one runaway
    # conversation cannot fill the database.
    messages: list[dict[str, Any]] = Field(default_factory=list, max_length=400)


# ----------------------------------------------------------------------
# Feedback
# ----------------------------------------------------------------------

class FeedbackRequest(BaseModel):
    """
    One piece of feedback about one answer.

    Everything except `comment` and `userName` is context the client already has
    from the answer it is reporting on, so the user never retypes anything.

    `userName` is supplied by the client today. Once CCHMC SSO is in place it
    should come from the authenticated session instead and this field should be
    dropped from the request body.
    """

    comment: str = Field(min_length=1, max_length=8000)
    userName: str = Field(default="", max_length=200)
    question: str = Field(default="", max_length=8000)
    answer: str = Field(default="", max_length=40000)
    mode: str | None = Field(default=None, max_length=40)
    intent: str | None = Field(default=None, max_length=40)
    skill: str | None = Field(default=None, max_length=80)
    # Whole trace plus timings, questionType, agent, sessionId, and any Cypher.
    traceSnapshot: dict[str, Any] | None = None

    @field_validator("comment")
    @classmethod
    def comment_not_blank(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("comment cannot be blank")
        return cleaned


class FeedbackResponse(BaseModel):
    ok: bool
    id: int


class FeedbackItem(BaseModel):
    id: int
    userName: str
    question: str
    answer: str
    mode: str | None = None
    intent: str | None = None
    skill: str | None = None
    comment: str
    traceSnapshot: dict[str, Any] | None = None
    createdAt: str


class FeedbackListResponse(BaseModel):
    items: list[FeedbackItem] = Field(default_factory=list)
    total: int = 0
