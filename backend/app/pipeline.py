"""
pipeline.py

The retrieval and reasoning pipeline, ported from the original Streamlit app
and reshaped so it can stream progress and serve concurrent users.

Question routing, unchanged in spirit from the original:

  named    an explicit person is mentioned, so skip judging and extract directly
  followup no person named but the question refers back, so reuse session state
  first    open ended discovery, so judge every candidate, rank, cut, extract

Changes worth knowing about:

  * Blocking retriever calls run on worker threads, so one slow search no
    longer stalls every other request on the event loop.
  * Follow up state lives per session instead of in shared JSON files.
  * Judging is capped by MAX_JUDGED_FACULTY, since that fan out is what makes
    an open ended question expensive.
  * The judge and extract prompts always return an object so JSON mode can be
    used, replacing the original bare "NONE" sentinel string.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from typing import Any, Awaitable, Callable

from . import llm
from .db import UnsafeCypherError, run_generated_cypher
from .faculty import canonicalise, faculty_list_text, faculty_names
from .ontology import describe_agents, load_agent_schemas
from .retrievers import (
    build_retriever,
    dedupe,
    faculty_from_source,
    normalise_items,
    retrieve_per_faculty,
    source_matches_faculty,
)
from .session import SessionStore
from .settings import settings
from .skills import Skill, get_skill, match_skill, run_skill, skill_catalogue

log = logging.getLogger(__name__)

Emit = Callable[[str, dict[str, Any]], Awaitable[None]]

# Character ceiling on one faculty member's combined evidence block, roughly
# 8k tokens. Bounds the cost of every judge and extract call and keeps a
# prolific person's block from overrunning the model's context.
BLOCK_CHAR_BUDGET = 32_000

# Pronouns and back references. A question containing one of these depends on the
# previous answer, so it must not be answered by a standalone graph query.
_BACK_REFERENCE = re.compile(
    r"\b(their|theirs|them|they|these|those|this\s+group|his|hers?|its"
    r"|the\s+(first|last|top|next|other)\s+\w+"
    r"|same|above|previous|aforementioned|just\s+(mentioned|listed))\b",
    re.IGNORECASE,
)


def _has_back_reference(question: str) -> bool:
    return bool(_BACK_REFERENCE.search(question))


async def _noop_emit(event: str, payload: dict[str, Any]) -> None:
    return None


class Stopwatch:
    def __init__(self) -> None:
        self.start = time.perf_counter()
        self.stages: list[dict[str, Any]] = []
        self._last = self.start

    def mark(self, stage: str, label: str, detail: str | None = None) -> dict[str, Any]:
        now = time.perf_counter()
        entry = {
            "stage": stage,
            "label": label,
            "detail": detail,
            "ms": int((now - self._last) * 1000),
        }
        self._last = now
        self.stages.append(entry)
        return entry

    @property
    def total_ms(self) -> int:
        return int((time.perf_counter() - self.start) * 1000)


# ----------------------------------------------------------------------
# Stage 1: classify the question
# ----------------------------------------------------------------------

_CLASSIFY_SYSTEM = "You are a strict JSON classifier for a faculty question answering system. Return JSON only."


async def classify_question(
    question: str, previous_faculty: list[str] | None = None
) -> dict[str, Any]:
    """
    One call decides two orthogonal things.

    `type` tracks conversational reference: is a person named, is this a follow
    up, or is it fresh. `intent` decides the retrieval strategy: is this a fact
    the graph holds, or an open judgement over CV prose. A question can be both
    named and factual, so these must not be collapsed into one axis.

    The conversation state is passed in because a back reference cannot be
    resolved without it. "What are their degrees" is a follow up when someone was
    just discussed and a fresh question otherwise, and the model cannot tell
    which from the sentence alone.
    """
    if previous_faculty:
        context_block = (
            "Faculty currently under discussion, from the previous answer:\n"
            + ", ".join(previous_faculty)
        )
    else:
        context_block = (
            "No faculty have been discussed yet in this conversation, so nothing "
            "can be referred back to."
        )

    prompt = f"""
You are the router for a faculty question answering system backed by a Neo4j
knowledge graph built from faculty CVs, publications, and abstracts.

Allowed Faculty:
{faculty_list_text()}

Conversation state:
{context_block}

Available graph skills, each a precise stored query:
{skill_catalogue()}

User Question:
{question}

First classify the conversational type:
- "named": it explicitly mentions one or more specific people from the Allowed
  Faculty list. A first name, last name, or full name all count.
- "followup": it does NOT name anyone, but refers back to the faculty under
  discussion. Any pronoun or back reference such as "their", "them", "these
  people", "those two", "the first three", "his", "her", or a bare continuation
  like "and their grants" makes this a followup whenever faculty are currently
  under discussion. This takes priority: if faculty are under discussion and the
  question does not name anyone new, prefer "followup" over "first".
- "first": a brand new self contained question, with no names and no back
  reference, or any question asked when nothing is under discussion yet.

Then classify the intent, which decides how the answer is found:
- "roster": asks who the faculty are, or how many there are, with NO subject
  matter criterion. Example: "what are the 20 faculty names", "list all faculty".
- "factual": asks for a specific fact, count, total, ranking, or structural
  relationship that a database query would answer exactly. Examples: "how many
  grants are there", "which funding agencies fund the most grants", "what
  document categories exist".
- "expertise": asks which people have knowledge, experience, or a track record
  in some subject, or asks to describe someone's work. This needs judgement over
  CV prose. Examples: "which faculty work on cystic fibrosis", "who has
  experience with Bayesian trials", "what is Cole Brokamp working on".

If the intent is "roster" or "factual" and one of the graph skills above answers
it exactly, name that skill. Otherwise set skill to null.

If the question refers to a positional subset of previously discussed faculty,
for example "the first two", "last three", report it in "subset".

Return JSON with exactly this shape:
{{
  "type": "named" | "followup" | "first",
  "intent": "roster" | "factual" | "expertise",
  "skill": "<skill id from the list above>" | null,
  "faculty": ["<name copied exactly from Allowed Faculty>"],
  "subset": {{"position": "first" | "last" | "all", "count": <integer or null>}}
}}

Rules:
- Populate "faculty" only for type "named", otherwise use an empty list.
- Every entry in "faculty" must be copied verbatim from the Allowed Faculty list.
- If a person is mentioned but is not in the Allowed Faculty list, omit them.
- Do not invent names or skill ids.
- A question that names a subject area is "expertise", never "roster", even when
  it is phrased as a list request.
"""
    data = await llm.chat_json(_CLASSIFY_SYSTEM, prompt)
    return _clean_classification(data)


def _clean_classification(data: Any) -> dict[str, Any]:
    fallback = {
        "type": "first",
        "intent": "expertise",
        "skill": None,
        "faculty": [],
        "subset": {"position": "all", "count": None},
    }
    if not isinstance(data, dict):
        return fallback

    qtype = data.get("type")
    if qtype not in {"named", "followup", "first"}:
        qtype = "first"

    intent = data.get("intent")
    if intent not in {"roster", "factual", "expertise"}:
        intent = "expertise"

    # Only accept a skill id that actually exists in the registry.
    skill_id = data.get("skill")
    skill = get_skill(str(skill_id)) if skill_id else None

    resolved: list[str] = []
    for raw in data.get("faculty") or []:
        canonical = canonicalise(str(raw))
        if canonical and canonical not in resolved:
            resolved.append(canonical)

    # A "named" verdict with nothing resolvable is really an open question.
    if qtype == "named" and not resolved:
        qtype = "first"

    subset = data.get("subset") if isinstance(data.get("subset"), dict) else {}
    position = subset.get("position")
    if position not in {"first", "last", "all"}:
        position = "all"
    count = subset.get("count")
    if not isinstance(count, int) or count <= 0:
        count = None

    # A skill only makes sense for a graph answerable intent.
    if intent == "expertise":
        skill = None

    return {
        "type": qtype,
        "intent": intent,
        "skill": skill,
        "faculty": resolved,
        "subset": {"position": position, "count": count},
    }


def apply_subset(names: list[str], subset: dict[str, Any] | None) -> list[str]:
    if not subset:
        return names
    position = subset.get("position", "all")
    count = subset.get("count")
    if position == "all" or not count:
        return names
    if position == "last":
        return names[-count:]
    return names[:count]


# ----------------------------------------------------------------------
# Stage 2: pick a domain agent
# ----------------------------------------------------------------------

async def select_agent(question: str) -> str:
    schemas = load_agent_schemas()
    names = list(schemas.keys())
    if not names:
        return "General Agent"

    prompt = f"""You route questions to the single most relevant domain agent.

Question:
{question}

Available agents:
{describe_agents()}

Return JSON of the form {{"agent": "<exact agent name>"}} choosing exactly one
agent from the list above.
"""
    data = await llm.chat_json("You are a routing assistant. Return JSON only.", prompt)

    choice = ""
    if isinstance(data, dict):
        choice = str(data.get("agent") or "").strip()

    for name in names:
        if name.lower() == choice.lower():
            return name
    for name in names:
        if name.lower() in choice.lower():
            return name
    return "Research Agent" if "Research Agent" in names else names[0]


# ----------------------------------------------------------------------
# Stage 3: retrieval
# ----------------------------------------------------------------------

async def retrieve(retriever, query: str, top_k: int | None = None) -> list[dict[str, str]]:
    """Run a blocking retriever search on a worker thread."""
    k = top_k or settings.retrieval_top_k

    def _search() -> list[dict[str, str]]:
        result = retriever.search(query_text=query, top_k=k)
        return normalise_items(getattr(result, "items", []) or [])

    try:
        return await asyncio.to_thread(_search)
    except Exception as exc:
        log.exception("Retrieval failed for query %r", query[:120])
        raise RuntimeError(f"Graph retrieval failed: {exc}") from exc


async def retrieve_many(retriever, queries: list[str]) -> list[dict[str, str]]:
    results = await asyncio.gather(
        *(retrieve(retriever, q) for q in queries), return_exceptions=True
    )
    chunks: list[dict[str, str]] = []
    for item in results:
        if isinstance(item, BaseException):
            log.warning("A retrieval leg failed: %s", item)
            continue
        chunks.extend(item)
    return dedupe(chunks)


def group_by_faculty(chunks: list[dict[str, str]]) -> list[dict[str, str]]:
    """
    Collapse chunks into one text block per faculty member.

    Blocks are capped at BLOCK_CHAR_BUDGET characters. Passages arrive in
    relevance order, so the cap keeps the most relevant evidence and drops the
    tail. Without it a prolific faculty member's block can run to hundreds of
    passages, which inflates every judge and extract prompt and risks the model's
    context limit.
    """
    grouped: dict[str, dict[str, str]] = {}
    for chunk in chunks:
        name = faculty_from_source(chunk.get("source", ""))
        if not name:
            continue
        canonical = canonicalise(name) or name
        entry = grouped.get(canonical)
        if entry is None:
            grouped[canonical] = {
                "faculty": canonical,
                "text": chunk["text"][:BLOCK_CHAR_BUDGET],
                "source": chunk.get("source", ""),
                "chunks": 1,
            }
        else:
            if len(entry["text"]) >= BLOCK_CHAR_BUDGET:
                continue
            entry["text"] += "\n" + chunk["text"]
            entry["chunks"] = int(entry["chunks"]) + 1
    return list(grouped.values())


# ----------------------------------------------------------------------
# Stage 4: judge relevance
# ----------------------------------------------------------------------

async def judge_faculty(block: dict[str, Any], question: str) -> dict[str, Any] | None:
    prompt = f"""
You are a strict research evaluator.

Question:
{question}

Faculty Name:
{block['faculty']}

Faculty CV Content:
{block['text']}

Decide whether this faculty member explicitly satisfies the question.
Use semantic reasoning rather than keyword matching. Use only the content above
and do not invent anything.

Score the strength of the evidence from 0 to 100:
  90 to 100 = central, extensive, directly on topic body of work
  70 to 89  = clearly relevant with solid supporting evidence
  50 to 69  = relevant but limited or partial evidence
  30 to 49  = weak or peripheral relevance with some genuine supporting evidence
  below 30  = tangential or no real evidence, which does not satisfy the question

Return JSON with exactly this shape:
{{
  "relevant": true | false,
  "faculty_name": "{block['faculty']}",
  "relevance_score": <integer 0 to 100>,
  "details": "<5 to 6 sentences describing the relevant experience, or an empty string when not relevant>"
}}

Set "relevant" to false when the score would be below 30.
"""
    data = await llm.chat_json("Return JSON only.", prompt)
    if not isinstance(data, dict) or not data.get("relevant"):
        return None

    score = _as_score(data.get("relevance_score"))
    if score < 30:
        return None

    return {
        "faculty_name": canonicalise(str(data.get("faculty_name") or block["faculty"]))
        or block["faculty"],
        "score": score,
        "details": str(data.get("details") or "").strip(),
    }


def _as_score(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


# Adaptive cutoff constants, carried over from the original app.
MANY_HIGH_SCORERS = 3
HIGH_SCORE = 70
STRICT_CUTOFF = 60
LENIENT_CUTOFF = 40
GAP_THRESHOLD = 18


def apply_cutoff(ranked: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], str]:
    """
    Trim the ranked list at a natural break, then apply a strict or lenient
    floor depending on how many strong candidates there are.
    """
    if not ranked:
        return [], "no candidates"

    notes: list[str] = []

    cut = ranked
    for i in range(1, len(ranked)):
        drop = ranked[i - 1]["score"] - ranked[i]["score"]
        if drop >= GAP_THRESHOLD:
            notes.append(
                f"{int(drop)} point score gap after {ranked[i - 1]['faculty_name']}, "
                f"dropping {len(ranked) - i} lower scoring"
            )
            cut = ranked[:i]
            break

    high = [f for f in cut if f["score"] >= HIGH_SCORE]
    if len(high) >= MANY_HIGH_SCORERS:
        kept = [f for f in cut if f["score"] > STRICT_CUTOFF]
        notes.append(f"{len(high)} strong candidates, keeping score above {STRICT_CUTOFF}")
    else:
        kept = [f for f in cut if f["score"] >= LENIENT_CUTOFF]
        notes.append(f"only {len(high)} strong candidates, keeping score {LENIENT_CUTOFF} and above")

    return kept, ". ".join(notes)


# ----------------------------------------------------------------------
# Stage 5: extract the answer per faculty
# ----------------------------------------------------------------------

async def extract_for_faculty(
    chunks: list[dict[str, str]], question: str, faculty_name: str
) -> dict[str, Any] | None:
    relevant = [c for c in chunks if source_matches_faculty(c.get("source", ""), faculty_name)]
    if not relevant:
        log.info("No chunks retrieved for %s", faculty_name)
        return None

    text = "\n".join(c["text"] for c in relevant)
    prompt = f"""
Extract the information the question asks for.

Question:
{question}

Faculty:
{faculty_name}

Content:
{text}

Return JSON with exactly this shape:
{{
  "found": true | false,
  "faculty_name": "{faculty_name}",
  "information": ["concise factual item", "another item"]
}}

Every item must be grounded in the content above. Set "found" to false with an
empty list when the content does not answer the question.
"""
    data = await llm.chat_json("Return JSON only.", prompt)
    if not isinstance(data, dict) or not data.get("found"):
        return None

    information = [str(i).strip() for i in (data.get("information") or []) if str(i).strip()]
    if not information:
        return None

    return {"name": faculty_name, "information": information}


# ----------------------------------------------------------------------
# Natural language to Cypher
# ----------------------------------------------------------------------

GRAPH_SCHEMA_HINT = """
Node labels and their meaning:
  Chunk(text, source2, index, id2)   A passage of a faculty document. source2 follows
                                     '<Faculty Name>_<Category>' where Category is one of
                                     Abstracts, Publications, Contracts, Mentoring, Leadership,
                                     Biography, Honors, Education, Appointments, Service,
                                     Certification, Data, Focus, Effort.
  Person(name)                       A person. Names are NOT normalised, so the same human
                                     appears as 'Rhonda Szczesniak', 'Szczesniak R', and
                                     'Szczesniak, R.'. Always match with a case insensitive
                                     CONTAINS on a surname, never equality on a full name.
  Publication(name), Journal(name), Conference(name)
  Grant(name), GrantPeriod(name), FundingAgency(name), Funding(name)
  ResearchArea(name), Concept(name), Method(name), Disease(name), Gene(name), Chemical(name)
  StatisticalResult(name), Investigation(name), Project(name)
  Organization(name), Institution(name), University(name), Department(name), Division(name)
  AcademicAppointment(name), Position(name), LeadershipRole(name), Committee(name)
  Course(name), Lesson(name), Student(name), Degree(name), Education(name), FieldOfStudy(name)
  Credential(name), CredentialAward(name), Certificate(name)
  Document(name, document_type, path, date, year)

Relationship types, most frequent first:
  FROM_CHUNK, FROM_DOCUMENT, NEXT_CHUNK          structural provenance
  collaboratesWith, uses_method, reports_result, associated_with, investigates
  publishedInJournal, presentedAtConference, affiliatedWith, focuses_on
  hasCurriculumVitae, hasAffiliation, hasGrant, hasResearchArea, publications
  fundedBy, holdsPosition, hasGrantPeriod, belongsTo, positionAt, topic
  hasGrantRole, hasDegree, researches, awardedBy, roleInCommittee, servedOn
  affiliated_with, inDepartment, awardedTo, credential, inField, appointedAt
  teaches, targets, heldRole, participatedIn, authored, offers, offersCourse
  knows, primaryTopic, hasSubject, contributedTo, hasEducation, funded_by
  collaborates_with, enrolledIn, isEmployedBy, locatedAt, member

Rules you must follow:
  * Read only. Never emit CREATE, MERGE, SET, DELETE, REMOVE, DROP, or CALL {} with writes.
  * Almost every entity node uses a `name` property. Chunks use `text` and `source2`.
  * To attribute anything to a specific faculty member, prefer Chunk.source2 with
    STARTS WITH or CONTAINS, because Person nodes are not deduplicated.
  * Always include a LIMIT, at most 100.
  * Return named columns with readable aliases.
"""


async def generate_cypher(question: str) -> dict[str, Any]:
    prompt = f"""
Translate the question into one read only Cypher query for Neo4j 5.

Graph schema:
{GRAPH_SCHEMA_HINT}

Question:
{question}

Return JSON with exactly this shape:
{{
  "cypher": "<single Cypher statement>",
  "explanation": "<one sentence describing what the query does>"
}}

Do not wrap the Cypher in markdown fences. Use no parameters, inline any literal
values directly in the statement.
"""
    data = await llm.chat_json(
        "You are a Neo4j Cypher expert. Return JSON only. Generate read only queries.", prompt
    )
    if not isinstance(data, dict) or not data.get("cypher"):
        raise RuntimeError("The model did not return a Cypher query.")

    cypher = str(data["cypher"]).strip().strip("`")
    return {"cypher": cypher, "explanation": str(data.get("explanation") or "").strip()}


async def answer_from_rows(
    question: str, cypher: str, columns: list[str], rows: list[dict], tabular: bool = False
) -> str:
    """
    Turn query results into prose.

    When the result is a table the caller renders it below this text, so the
    prose must be a short lead in rather than a restatement. Enumerating every
    row here produced the same answer twice on screen, once as a prose list and
    once as the table, with the query wedged in between.
    """
    preview = rows[:40]

    if tabular:
        instruction = f"""
Write at most two short sentences introducing the result. The full result is
displayed as a table directly below your text, so do NOT list, enumerate, or
repeat the rows. Say what the result covers and call out the single most notable
figure, for example the largest value or the total. Never write a bulleted or
numbered list.
"""
    else:
        instruction = """
Write a direct, factual answer in one or two sentences, stating the values from
the result.
"""

    prompt = f"""
Answer the question using only the query results below.

Question:
{question}

Cypher executed:
{cypher}

Columns: {", ".join(columns) if columns else "none"}
Rows returned: {len(rows)}
Results (up to 40 shown):
{preview}

{instruction}
State plainly when the results are empty or do not answer the question. Do not
invent anything that is not in the results. Do not use em dashes.
"""
    answer = await llm.chat("You are a precise data analyst. Answer in plain prose.", prompt)
    return _strip_tabular_prose(answer) if tabular else answer.strip()


_BULLET = re.compile(r"^\s*(?:[-*•]\s+\S|\d+[.)]\s+\S)")
_SEPARATOR = re.compile(r"^\s*[-:\s|]*[-|][-:|]{2,}[-:\s|]*$")


def _is_row_like(line: str) -> bool:
    """
    True when a line looks like part of a table or list rather than prose.

    Models reliably ignore "do not list the rows" and append a table anyway, so
    the prose is truncated at the first such line rather than trusting the
    instruction. A single pipe can appear legitimately in a sentence, so the
    discriminator for pipe lines is that table cells are short and a row does not
    end in sentence punctuation. Being too eager here would delete a valid
    answer, which is worse than leaving one stray row visible.
    """
    stripped = line.strip()
    if not stripped:
        return False
    if _BULLET.match(line) or _SEPARATOR.match(line):
        return True
    if stripped.startswith("|"):
        return True
    if "|" not in stripped:
        return False
    if stripped.endswith((".", ":", "!", "?")):
        return False
    cells = [c.strip() for c in stripped.split("|")]
    cells = [c for c in cells if c]
    return len(cells) >= 2 and all(len(c) <= 40 for c in cells)


def _strip_tabular_prose(text: str) -> str:
    """
    Keep only the leading prose, dropping any table or list the model appended.

    The rows are rendered as a real table by the client, so repeating them in the
    answer text showed the user the same data twice.
    """
    kept: list[str] = []
    for line in (text or "").splitlines():
        if _is_row_like(line):
            break
        kept.append(line)

    result = "\n".join(kept).strip()
    # If the model led with a table and said nothing else, fall back to a caption
    # rather than returning an empty answer.
    return result or "The full result is shown below."


# ----------------------------------------------------------------------
# Conversation naming
# ----------------------------------------------------------------------

# Longest slice of the question that is sent. A title needs the subject, not the
# whole sentence, and this bounds the input cost no matter what someone pastes in.
TITLE_INPUT_CHARS = 300
# Hard ceiling on generated tokens. Four or five words never needs more.
TITLE_MAX_TOKENS = 16
# Longest title kept, so the sidebar and the header never have to ellipsise.
TITLE_MAX_CHARS = 48

_TITLE_SYSTEM = (
    "You name chat conversations. Reply with a title of at most five words, "
    "in sentence case, with no quotes and no trailing punctuation."
)


async def generate_title(question: str) -> str:
    """
    Name a conversation from its first question.

    Deliberately cheap: one call per conversation, the question only with no
    answer and no trace, a terse system prompt, a small non reasoning model, and
    a hard token cap. Roughly 55 input tokens and at most 16 output tokens.

    Never raises. On any failure the caller keeps the fallback title it already
    has, because a conversation with a plain name is fine and a failed request
    that breaks the chat is not.
    """
    trimmed = " ".join((question or "").split())[:TITLE_INPUT_CHARS]
    if not trimmed:
        return ""

    try:
        raw = await llm.chat(
            _TITLE_SYSTEM,
            trimmed,
            model=settings.title_model,
            max_completion_tokens=TITLE_MAX_TOKENS,
        )
    except Exception as exc:
        log.warning("Title generation failed, keeping the fallback: %s", exc)
        return ""

    return _clean_title(raw)


def _clean_title(raw: str) -> str:
    """Strip the decoration models like to add around a title."""
    title = " ".join((raw or "").split())
    # Models sometimes answer with a quoted phrase, or prefix it with a label.
    title = re.sub(r'^(title|chat title)\s*[:\-]\s*', "", title, flags=re.IGNORECASE)
    title = title.strip().strip("\"'“”‘’")
    title = title.rstrip(".,;:!")
    if len(title) > TITLE_MAX_CHARS:
        title = title[:TITLE_MAX_CHARS].rstrip()
    return title


# ----------------------------------------------------------------------
# Orchestration
# ----------------------------------------------------------------------

async def run_query(
    question: str,
    mode: str,
    session_id: str,
    store: SessionStore,
    agent_override: str | None = None,
    emit: Emit = _noop_emit,
) -> dict[str, Any]:
    """
    Run one question end to end and return the API response payload.

    Routing order matters. A factual question is answered from the graph no
    matter which retrieval mode the user selected, because the vector and hybrid
    toggle chooses how semantic search works, not whether a question that has an
    exact answer should be handled by similarity scoring. Forcing everything
    through the semantic path is what made "what are the 20 faculty names" return
    five wrong names with scoring prose attached.
    """
    watch = Stopwatch()

    # Explicitly choosing Cypher mode is always honoured.
    if mode == "cypher":
        return await _run_cypher_mode(question, session_id, watch, emit)

    # A guaranteed route for unmistakable factual phrasings, before any model
    # call. This costs nothing and cannot be talked out of the right answer.
    #
    # Skipped when the question contains a back reference, since a pronoun means
    # the answer depends on conversation state that a standalone graph query
    # cannot see. Those go to the classifier, which is given the state.
    direct = None if _has_back_reference(question) else match_skill(question)
    if direct is not None:
        await emit("stage", {"stage": "classify", "label": "Recognised a graph question"})
        return await _run_skill(
            direct, question, mode, session_id, watch, emit, intent="roster_or_factual"
        )

    return await _run_graphrag_mode(
        question, mode, session_id, store, agent_override, watch, emit
    )


async def _run_skill(
    skill: Skill,
    question: str,
    mode: str,
    session_id: str,
    watch: Stopwatch,
    emit: Emit,
    intent: str,
) -> dict[str, Any]:
    """Answer from a hand written graph query."""
    await emit(
        "stage",
        {"stage": "graph_query", "label": "Querying the graph", "detail": skill.caption},
    )
    columns, rows = await asyncio.to_thread(run_skill, skill)
    watch.mark("graph_query", "Queried the graph", f"{len(rows)} rows")

    # Deterministic prose where the skill provides it, so the most common
    # factual answers involve no model at all and cannot be garbled.
    if skill.summarise is not None:
        answer = skill.summarise(rows)
        watch.mark("answer", "Composed the answer", "deterministic, no model call")
    else:
        await emit("stage", {"stage": "answer", "label": "Composing the answer"})
        answer = await answer_from_rows(question, skill.cypher, columns, rows)
        watch.mark("answer", "Composed the answer")

    return {
        "mode": mode,
        "questionType": "first",
        "intent": intent,
        "agent": None,
        "answerText": answer,
        "faculty": [],
        "cypher": {
            "query": skill.cypher.strip(),
            "params": dict(skill.params),
            "columns": columns,
            "rows": rows,
            "kind": "builtin",
            "explanation": skill.description,
            "showTable": skill.show_table,
        },
        "trace": {
            "stages": watch.stages,
            "retrievedChunks": 0,
            "judged": 0,
            "kept": 0,
            "cutoff": None,
            "intent": intent,
            "skill": skill.id,
            "coverage": None,
            "judgements": [],
            "noEvidence": [],
        },
        "timings": {"totalMs": watch.total_ms},
        "sessionId": session_id,
    }


async def _run_cypher_mode(
    question: str, session_id: str, watch: Stopwatch, emit: Emit
) -> dict[str, Any]:
    await emit("stage", {"stage": "cypher_generate", "label": "Generating Cypher"})
    generated = await generate_cypher(question)
    watch.mark("cypher_generate", "Generated Cypher", generated["explanation"] or None)

    await emit(
        "stage",
        {"stage": "cypher_execute", "label": "Running query on the graph", "detail": None},
    )
    try:
        columns, rows = await asyncio.to_thread(run_generated_cypher, generated["cypher"], None, 200)
    except UnsafeCypherError as exc:
        watch.mark("cypher_execute", "Blocked unsafe query", str(exc))
        return {
            "mode": "cypher",
            "questionType": "first",
            "agent": None,
            "answerText": str(exc),
            "faculty": [],
            "cypher": {"query": generated["cypher"], "params": {}, "columns": [], "rows": []},
            "trace": {
                "stages": watch.stages,
                "retrievedChunks": 0,
                "judged": 0,
                "kept": 0,
                "cutoff": None,
            },
            "timings": {"totalMs": watch.total_ms},
            "sessionId": session_id,
        }
    except Exception as exc:
        watch.mark("cypher_execute", "Query failed", str(exc))
        return {
            "mode": "cypher",
            "questionType": "first",
            "agent": None,
            "answerText": (
                f"The generated Cypher did not run against the graph. {exc}"
            ),
            "faculty": [],
            "cypher": {"query": generated["cypher"], "params": {}, "columns": [], "rows": []},
            "trace": {
                "stages": watch.stages,
                "retrievedChunks": 0,
                "judged": 0,
                "kept": 0,
                "cutoff": None,
            },
            "timings": {"totalMs": watch.total_ms},
            "sessionId": session_id,
        }

    watch.mark("cypher_execute", "Query returned", f"{len(rows)} rows")

    await emit("stage", {"stage": "answer", "label": "Composing the answer"})

    # More than one row means the table is the answer, so the prose becomes a
    # short lead in. A single row is a scalar result the sentence can state
    # outright, and a one row table beside it is noise.
    tabular = len(rows) > 1
    answer = await answer_from_rows(
        question, generated["cypher"], columns, rows, tabular=tabular
    )
    watch.mark("answer", "Composed the answer")

    return {
        "mode": "cypher",
        "questionType": "first",
        "agent": None,
        "answerText": answer,
        "faculty": [],
        "cypher": {
            "query": generated["cypher"],
            "params": {},
            "columns": columns,
            "rows": rows,
            "kind": "generated",
            "explanation": generated["explanation"] or None,
            "showTable": tabular,
        },
        "trace": {
            "stages": watch.stages,
            "retrievedChunks": 0,
            "judged": 0,
            "kept": 0,
            "cutoff": None,
        },
        "timings": {"totalMs": watch.total_ms},
        "sessionId": session_id,
    }


async def _run_graphrag_mode(
    question: str,
    mode: str,
    session_id: str,
    store: SessionStore,
    agent_override: str | None,
    watch: Stopwatch,
    emit: Emit,
) -> dict[str, Any]:
    await emit("stage", {"stage": "classify", "label": "Understanding the question"})
    await emit("stage", {"stage": "route", "label": "Choosing a domain agent"})

    previous_faculty = list(store.get(session_id).previous_faculty)
    classification_task = asyncio.create_task(
        classify_question(question, previous_faculty)
    )
    agent_task: asyncio.Task | None = None
    if agent_override:
        agent_name = agent_override
    else:
        agent_task = asyncio.create_task(select_agent(question))
        agent_name = ""

    classification = await classification_task
    intent = classification["intent"]
    watch.mark(
        "classify", "Classified the question", f"{classification['type']}, {intent}"
    )

    # A follow up is never eligible for the graph route, whatever its intent.
    # "What are their degrees" is factual in the abstract, but "their" only means
    # something in this conversation, and a standalone Cypher query cannot see
    # that. Routing it to the graph loses the context and returns nothing.
    is_followup = classification["type"] == "followup"

    # The classifier judged this answerable from the graph. Take that route and
    # skip agent selection, retrieval, judging, and extraction entirely.
    skill: Skill | None = classification.get("skill")
    if skill is not None and not is_followup:
        if agent_task is not None:
            agent_task.cancel()
        return await _run_skill(skill, question, mode, session_id, watch, emit, intent=intent)

    # Factual with no matching skill still belongs on the graph, so fall through
    # to generated Cypher rather than scoring people against it.
    if intent == "factual" and not is_followup:
        if agent_task is not None:
            agent_task.cancel()
        await emit(
            "stage",
            {
                "stage": "route",
                "label": "Routed to a graph query",
                "detail": "factual question, no stored skill",
            },
        )
        response = await _run_cypher_mode(question, session_id, watch, emit)
        response["intent"] = intent
        response["trace"]["intent"] = intent
        return response

    if agent_task is not None:
        agent_name = await agent_task
    watch.mark("route", "Selected domain agent", agent_name)
    await emit("stage", {"stage": "route", "label": "Selected domain agent", "detail": agent_name})

    retriever = build_retriever(mode)
    qtype = classification["type"]

    if qtype == "named":
        target_faculty = classification["faculty"]
        store.set_previous_faculty(session_id, target_faculty)
    elif qtype == "followup":
        previous = store.get(session_id).previous_faculty
        target_faculty = apply_subset(list(previous), classification["subset"])
        if not target_faculty:
            # Nothing to follow up on, so treat it as a fresh question.
            qtype = "first"
            target_faculty = []
    else:
        target_faculty = []

    # ---- Direct extraction path: named or follow up ----
    if qtype in {"named", "followup"} and target_faculty:
        await emit(
            "stage",
            {
                "stage": "retrieve",
                "label": "Searching the graph",
                "detail": f"{len(target_faculty)} faculty",
            },
        )
        chunks = await retrieve_many(retriever, [question] + target_faculty)
        watch.mark("retrieve", "Retrieved graph context", f"{len(chunks)} chunks")

        results, missing = await _extract_all(chunks, question, target_faculty, watch, emit)
        store.append_history(session_id, question, results)

        return {
            "mode": mode,
            "questionType": qtype,
            "intent": intent,
            "agent": agent_name,
            "answerText": _no_evidence_note(missing) if not results else None,
            "faculty": results,
            "cypher": None,
            "trace": {
                "stages": watch.stages,
                "retrievedChunks": len(chunks),
                "judged": 0,
                "kept": len(target_faculty),
                "cutoff": None,
                "intent": intent,
                "skill": None,
                "coverage": None,
                "judgements": [],
                "noEvidence": missing,
            },
            "timings": {"totalMs": watch.total_ms},
            "sessionId": session_id,
        }

    # ---- Discovery path ----
    await emit("stage", {"stage": "retrieve", "label": "Searching the graph"})

    # Two retrievals, unioned, because they do different jobs.
    #
    # The selected retriever goes deep: it returns many passages for whoever
    # ranks highest, which is what gives a strong candidate enough evidence to
    # score well. The coverage query goes wide: it guarantees every faculty
    # member with any material gets a fair hearing, which plain ranked retrieval
    # does not (measured: 17 of 20 on some phrasings).
    #
    # Using coverage alone was tried and is worse. Capping each person at a
    # handful of passages starves the genuinely strong candidates of evidence and
    # the relevance scores collapse. Depth plus breadth is what works.
    primary, coverage = await asyncio.gather(
        retrieve(retriever, question),
        retrieve_per_faculty(question),
        return_exceptions=True,
    )
    primary_chunks = primary if isinstance(primary, list) else []
    coverage_chunks = coverage if isinstance(coverage, list) else []

    chunks = dedupe(primary_chunks + coverage_chunks)
    covered = len({faculty_from_source(c["source"]) for c in chunks if c.get("source")})
    if coverage_chunks:
        coverage_note = (
            f"all {covered} faculty evaluated, {len(primary_chunks)} ranked plus "
            f"{len(coverage_chunks)} coverage passages"
        )
    else:
        coverage_note = f"{covered} faculty reached, coverage query unavailable"

    watch.mark("retrieve", "Retrieved graph context", f"{len(chunks)} chunks, {coverage_note}")

    if not chunks:
        return _empty_response(mode, qtype, agent_name, session_id, watch, intent=intent)

    blocks = group_by_faculty(chunks)
    blocks.sort(key=lambda b: int(b.get("chunks", 0)), reverse=True)
    capped = blocks[: settings.max_judged_faculty]
    dropped = len(blocks) - len(capped)

    await emit(
        "stage",
        {
            "stage": "judge",
            "label": "Assessing faculty relevance",
            "detail": f"{len(capped)} candidates",
            "progress": {"done": 0, "total": len(capped)},
        },
    )

    completed = 0
    lock = asyncio.Lock()

    async def judge_one(block: dict[str, Any]) -> dict[str, Any] | None:
        nonlocal completed
        try:
            verdict = await judge_faculty(block, question)
        except Exception as exc:
            log.warning("Judging failed for %s: %s", block.get("faculty"), exc)
            verdict = None
        async with lock:
            completed += 1
            await emit(
                "stage",
                {
                    "stage": "judge",
                    "label": "Assessing faculty relevance",
                    "detail": f"{len(capped)} candidates",
                    "progress": {"done": completed, "total": len(capped)},
                },
            )
        return verdict

    verdicts = await asyncio.gather(*(judge_one(b) for b in capped))
    ranked = sorted([v for v in verdicts if v], key=lambda v: v["score"], reverse=True)
    detail = f"{len(ranked)} of {len(capped)} relevant"
    if dropped:
        detail += f", {dropped} lower coverage candidates not judged"
    watch.mark("judge", "Assessed faculty relevance", detail)

    await emit("trace", {"ranked": [{"name": v["faculty_name"], "score": v["score"]} for v in ranked]})

    kept, cutoff_note = apply_cutoff(ranked)
    watch.mark("rank", "Ranked and filtered", cutoff_note)
    await emit("stage", {"stage": "rank", "label": "Ranked and filtered", "detail": cutoff_note})

    # Every judgement travels in the trace, kept or not, so the scoring is
    # auditable under the pipeline disclosure without ever reaching the answer.
    judgements = [
        {
            "name": v["faculty_name"],
            "score": v["score"],
            "rationale": v["details"] or None,
            "kept": any(k["faculty_name"] == v["faculty_name"] for k in kept),
        }
        for v in ranked
    ]

    if not kept:
        return _empty_response(
            mode,
            qtype,
            agent_name,
            session_id,
            watch,
            judged=len(capped),
            intent=intent,
            judgements=judgements,
            coverage=coverage_note,
        )

    names = [k["faculty_name"] for k in kept]
    store.set_previous_faculty(session_id, names)

    results, missing = await _extract_all(chunks, question, names, watch, emit)

    # Attach the score only. The judge's prose stays out of the answer.
    by_name = {k["faculty_name"]: k for k in kept}
    for item in results:
        verdict = by_name.get(item["name"])
        if verdict:
            item["score"] = verdict["score"]

    order = {name: i for i, name in enumerate(names)}
    results.sort(key=lambda r: order.get(r["name"], 999))
    store.append_history(session_id, question, results)

    return {
        "mode": mode,
        "questionType": qtype,
        "intent": intent,
        "agent": agent_name,
        "answerText": _no_evidence_note(missing) if not results else None,
        "faculty": results,
        "cypher": None,
        "trace": {
            "stages": watch.stages,
            "retrievedChunks": len(chunks),
            "judged": len(capped),
            "kept": len(kept),
            "cutoff": cutoff_note,
            "intent": intent,
            "skill": None,
            "coverage": coverage_note,
            "judgements": judgements,
            "noEvidence": missing,
        },
        "timings": {"totalMs": watch.total_ms},
        "sessionId": session_id,
    }


def _no_evidence_note(missing: list[str]) -> str:
    """
    Explain an empty result instead of returning nothing.

    Candidates that pass the relevance cutoff but yield no extractable evidence
    used to disappear without trace, so "kept 2" could present as zero results
    with no explanation.
    """
    if not missing:
        return "No faculty in the graph matched that question."
    listed = ", ".join(missing)
    if len(missing) == 1:
        return (
            f"{listed} looked relevant, but the retrieved documents contained nothing "
            f"specific enough to answer the question. Try asking about a narrower topic."
        )
    return (
        f"{len(missing)} faculty looked relevant ({listed}), but the retrieved documents "
        f"contained nothing specific enough to answer the question. Try asking about a "
        f"narrower topic."
    )


async def _extract_all(
    chunks: list[dict[str, str]],
    question: str,
    names: list[str],
    watch: Stopwatch,
    emit: Emit,
) -> tuple[list[dict[str, Any]], list[str]]:
    await emit(
        "stage",
        {
            "stage": "extract",
            "label": "Extracting evidence",
            "detail": f"{len(names)} faculty",
            "progress": {"done": 0, "total": len(names)},
        },
    )

    completed = 0
    lock = asyncio.Lock()

    async def extract_one(name: str) -> dict[str, Any] | None:
        nonlocal completed
        try:
            result = await extract_for_faculty(chunks, question, name)
        except Exception as exc:
            log.warning("Extraction failed for %s: %s", name, exc)
            result = None
        async with lock:
            completed += 1
            await emit(
                "stage",
                {
                    "stage": "extract",
                    "label": "Extracting evidence",
                    "detail": f"{len(names)} faculty",
                    "progress": {"done": completed, "total": len(names)},
                },
            )
        return result

    extracted = await asyncio.gather(*(extract_one(n) for n in names))
    results = [r for r in extracted if r]
    # Names whose extraction came back empty. Returned rather than dropped so the
    # caller can explain an empty answer instead of presenting silence.
    missing = [name for name, result in zip(names, extracted) if not result]
    detail = f"{len(results)} answered"
    if missing:
        detail += f", {len(missing)} with no extractable evidence"
    watch.mark("extract", "Extracted evidence", detail)
    return results, missing


def _empty_response(
    mode: str,
    qtype: str,
    agent_name: str | None,
    session_id: str,
    watch: Stopwatch,
    judged: int = 0,
    intent: str | None = None,
    judgements: list[dict[str, Any]] | None = None,
    coverage: str | None = None,
) -> dict[str, Any]:
    if judged:
        answer = (
            f"None of the {judged} faculty assessed had strong enough evidence in their "
            f"documents to answer that question. Open the pipeline details to see how "
            f"each one scored."
        )
    else:
        answer = "No faculty in the graph matched that question."

    return {
        "mode": mode,
        "questionType": qtype,
        "intent": intent,
        "agent": agent_name,
        "answerText": answer,
        "faculty": [],
        "cypher": None,
        "trace": {
            "stages": watch.stages,
            "retrievedChunks": 0,
            "judged": judged,
            "kept": 0,
            "cutoff": None,
            "intent": intent,
            "skill": None,
            "coverage": coverage,
            "judgements": judgements or [],
            "noEvidence": [],
        },
        "timings": {"totalMs": watch.total_ms},
        "sessionId": session_id,
    }
