"""
pipeline.py

The retrieval and reasoning pipeline, ported one to one from the original
Streamlit app so that the same question produces the same answer.

Question routing, exactly as the original does it:

  named    an explicit person is mentioned, so skip judging and extract directly
  followup no person named but the question refers back, so reuse prior faculty
  first    open ended discovery, so judge every candidate, rank, cut, extract

That is the whole router. There is no second intent axis, no deterministic skill
matcher ahead of it, and no automatic route to Cypher: every question asked in
Hybrid or Vector mode goes through classify then judge then extract, which is
what the baseline app does.

Parity notes, each of which reverses a change that produced better answers but
different ones:

  * The judge and extract prompts are the originals verbatim, including the
    bare "NONE" sentinel, and run WITHOUT JSON mode.
  * Retrieval is a single ranked search per query, like the baseline. That
    reaches only 9 of 20 faculty on some questions and silently omits the rest;
    set COVERAGE_RETRIEVAL=1 to close that hole at the cost of diverging from
    the baseline.
  * Evidence blocks are uncapped and every block is judged.
  * The answer is rendered to the original's markdown string.

Two things are deliberately kept from the rewrite because neither changes an
answer: retriever calls still run on worker threads so one slow search cannot
stall the event loop, and follow-up state is keyed per session rather than held
in two process-wide JSON files. See `run_query` for why the session store is
still faithful.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from typing import Any, Awaitable, Callable

from . import llm
from .db import UnsafeCypherError, run_generated_cypher
from .faculty import faculty_list_text, faculty_names
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

log = logging.getLogger(__name__)

Emit = Callable[[str, dict[str, Any]], Awaitable[None]]


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

_CLASSIFY_SYSTEM = "You are a strict JSON classifier. Return only JSON."


async def classify_question(question: str) -> dict[str, Any]:
    """
    Route the question to one of three types: named, followup, or first.

    The baseline prompt, verbatim. Two properties of it matter for parity.

    It is NOT given the conversation state. A back reference is classified from
    the sentence alone, so "what are their degrees" is a followup because it
    reads like one, not because anyone is currently under discussion. Passing the
    prior faculty in produces better routing and different routing.

    It also has no second axis. There is no intent, no skill id, and therefore
    no way for a question to leave this function bound for a graph query.
    """
    prompt = f"""
You are the router for a faculty-CV question-answering system.

Allowed Faculty:
{faculty_list_text()}

User Question:
{question}

Classify the question into exactly one type:
- "named": it explicitly mentions one or more specific people who appear in the
  Allowed Faculty list (a first name, last name, or full name all count).
- "followup": it does NOT name anyone, but refers back to faculty already being
  discussed (e.g. "their education", "list them", "these people", "the first two").
- "first": a brand-new, open-ended question with no names and no back-reference
  (e.g. "list faculty with expertise in cystic fibrosis").

If the question refers to a POSITIONAL SUBSET of the previously discussed faculty
(e.g. "the first two", "last three", "top 5"), report it in "subset":
- position: "first", "last", or "all"
- count: the integer requested, or null when the question means everyone.
Use {{"position": "all", "count": null}} whenever no subset is implied.

Return ONLY JSON, no explanation:

{{
"type": "named" | "followup" | "first",
"faculty": ["<name copied EXACTLY from Allowed Faculty>", ...],
"subset": {{"position": "first" | "last" | "all", "count": <integer or null>}}
}}

Rules:
- Populate "faculty" ONLY for type "named"; otherwise use [].
- Every entry in "faculty" MUST be copied verbatim from the Allowed Faculty list.
- If a person is mentioned but is NOT in the Allowed Faculty list, omit them.
- Do NOT hallucinate names.
"""
    data = await llm.chat_strict_json(_CLASSIFY_SYSTEM, prompt)
    return _clean_classification(data)


def _clean_classification(data: Any) -> dict[str, Any]:
    """
    Validate the router's JSON, falling back to a fresh discovery question.

    Name resolution is an exact case-insensitive match against the allow list,
    which is what the baseline does. `canonicalise` in faculty.py additionally
    accepts an unambiguous surname or partial, and that leniency would rescue a
    name the baseline drops, so it is not used here.
    """
    fallback = {"type": "first", "faculty": [], "subset": {"position": "all", "count": None}}
    if not isinstance(data, dict):
        return fallback

    qtype = data.get("type")
    if qtype not in {"named", "followup", "first"}:
        qtype = "first"

    allowed = {name.lower(): name for name in faculty_names()}
    resolved: list[str] = []
    for raw in data.get("faculty") or []:
        # The baseline strips stray quotes from the model's answer before
        # comparing, via unique_preserve_order.
        cleaned = str(raw).strip().replace("'", "").replace('"', "")
        match = allowed.get(cleaned.lower())
        if match and match not in resolved:
            resolved.append(match)

    subset = data.get("subset") if isinstance(data.get("subset"), dict) else {}
    position = subset.get("position", "all")
    if position not in {"first", "last", "all"}:
        position = "all"
    count = subset.get("count")
    if not isinstance(count, int) or count <= 0:
        count = None

    return {"type": qtype, "faculty": resolved, "subset": {"position": position, "count": count}}


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
    """
    Pick one domain agent for the question.

    The baseline's router prompt, which asks for a bare agent name rather than
    JSON, and prefers the Research Agent whenever the model mentions it. The
    choice is reported in the trace and does not affect the answer: every agent
    shares one retriever and the selected schema is never passed downstream.
    """
    schemas = load_agent_schemas()
    names = list(schemas.keys())
    if not names:
        return "General Agent"

    prompt = f"""You are an intelligent router. Based on the user query and the agent
descriptions, select the SINGLE most relevant agent to answer the question.

Query:
{question}

Agent Descriptions:
{describe_agents()}

Return ONLY the exact agent name, nothing else.
"""
    raw = (await llm.chat("You are a helpful routing assistant.", prompt)).strip()
    selected = [n.strip() for n in raw.replace("\n", ",").split(",") if n.strip()]

    # Research Agent wins whenever the router named it anywhere in its reply.
    if any(n.lower() == "research agent" for n in selected):
        if "Research Agent" in names:
            return "Research Agent"

    for candidate in selected:
        for name in names:
            if name.lower() == candidate.lower():
                return name

    for name in names:
        if name.lower() in raw.lower():
            return name

    return names[0]
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
    """
    Retrieve for several queries and deduplicate across them.

    Results are concatenated in query order, matching the baseline's sequential
    loop, and a failing leg raises rather than degrading to partial results,
    because a partial answer is a different answer.
    """
    results = await asyncio.gather(*(retrieve(retriever, q) for q in queries))
    chunks: list[dict[str, str]] = []
    for item in results:
        chunks.extend(item)
    return dedupe(chunks)

def group_by_faculty(chunks: list[dict[str, str]]) -> list[dict[str, str]]:
    """
    Collapse chunks into one text block per faculty member.

    Three parity details. The grouping key is the raw source2 prefix with no
    canonicalisation against the allow list, so a source spelled differently
    forms its own block instead of merging into the right person's. Blocks are
    uncapped, however long the person's CV material runs. And an empty prefix is
    kept rather than skipped, because the baseline groups on
    `source.split("_")[0]` unconditionally.
    """
    grouped: dict[str, dict[str, Any]] = {}
    for chunk in chunks:
        name = faculty_from_source(chunk.get("source", ""))
        entry = grouped.get(name)
        if entry is None:
            grouped[name] = {
                "faculty": name,
                "text": chunk["text"],
                "source": chunk.get("source", ""),
                "chunks": 1,
            }
        else:
            entry["text"] += "\n" + chunk["text"]
            entry["chunks"] = int(entry["chunks"]) + 1
    return list(grouped.values())

# ----------------------------------------------------------------------
# Stage 4: judge relevance
# ----------------------------------------------------------------------

async def judge_faculty(block: dict[str, Any], question: str) -> dict[str, Any] | None:
    """
    Score one faculty member's evidence against the question, 0 to 100.

    The baseline prompt verbatim, and the differences from what it replaced all
    matter:

      * The Allowed Faculty list is included. It is arguably noise in a prompt
        that already names the one person being judged, but it is part of the
        input the baseline model sees.
      * The reply is JSON or the bare string NONE, and the call runs without
        JSON mode. Constraining the output space changes the text that comes
        back from the same evidence.
      * There is no score floor enforced here. "Below 30, return NONE" is an
        instruction to the model, and whether it obeys is part of the behaviour.
      * The rationale key is "Details", capitalised.
    """
    prompt = f"""
You are a strict research evaluator.

Question:
{question}

Allowed Faculty:
{faculty_list_text()}

Faculty Name:
{block['faculty']}

Faculty CV Content:
{block['text']}

Instructions:
- Determine if this faculty member explicitly satisfies the question.
- Use semantic reasoning.
- Do NOT rely on keyword matching.
- Do NOT hallucinate.
- Only use provided content.
- If the member satisfies the question, assign a relevance_score from 0 to 100
  reflecting HOW STRONGLY the CV evidence answers the question:
    90-100 = central, extensive, directly-on-topic body of work
    70-89  = clearly relevant with solid supporting evidence
    50-69  = relevant but limited or partial evidence
    30-49  = weak or peripheral relevance, but with some genuine supporting evidence
    below 30 = tangential or no real evidence - treat as NOT satisfying and return NONE
- Base the score only on the strength of evidence in the CV content above.

Return JSON:

{{
"faculty_name": "<name>",
"relevance_score": <integer 0-100>,
"Details": "<5-6 sentences explaining relevant experience>"
}}

If the faculty member does NOT satisfy the question return ONLY:
NONE
"""
    data = await llm.chat_strict_json("Return JSON or NONE.", prompt)
    if not isinstance(data, dict):
        return None

    return {
        "faculty_name": str(data.get("faculty_name") or block["faculty"]),
        "score": _as_score(data.get("relevance_score")),
        "details": str(data.get("Details") or "").strip(),
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
    """
    Pull this person's chunks, combine them, and extract what the question asks.

    The baseline prompt verbatim: no "found" flag, no grounding instruction, no
    JSON mode, and a bare NONE when nothing is there. A result is kept whenever
    the model returns an object, even one whose information list is empty,
    because that is what the baseline renders.
    """
    relevant = [c for c in chunks if source_matches_faculty(c.get("source", ""), faculty_name)]
    if not relevant:
        log.info("No CV chunks retrieved for %s", faculty_name)
        return None

    text = "\n".join(c["text"] for c in relevant)
    prompt = f"""
Extract the information requested.

Question:
{question}

Faculty:
{faculty_name}

Content:
{text}

Return JSON:

{{
"faculty_name": "{faculty_name}",
"information": ["item1","item2","item3"]
}}

If none exists return NONE.
"""
    data = await llm.chat_strict_json("Return JSON or NONE", prompt)
    if not isinstance(data, dict):
        return None

    information = data.get("information")
    if isinstance(information, list):
        items = [str(i).strip() for i in information if str(i).strip()]
    elif information:
        items = [str(information).strip()]
    else:
        items = []

    return {"name": str(data.get("faculty_name") or faculty_name), "information": items}


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

# The first version of this prompt produced titles that described the ACT of
# asking rather than the subject: "List of faculty names requested", "Cystic
# fibrosis faculty expertise inquiry", "Understanding their educational path".
# Naming the failure mode explicitly and showing three examples fixes it, and
# costs about 70 extra input tokens, which is nothing next to the value of a
# readable sidebar.
_TITLE_SYSTEM = (
    "You name chat conversations for a tool that searches faculty expertise.\n"
    "\n"
    "Name the SUBJECT of the question, never the act of asking it. Do not use the "
    "words request, inquiry, needed, asking, question, query, list, or overview.\n"
    "\n"
    "Reply with two to five words in sentence case. No quotes, no trailing "
    "punctuation, no explanation.\n"
    "\n"
    "Examples:\n"
    "Which faculty have expertise in cystic fibrosis? -> Cystic fibrosis expertise\n"
    "What are the 20 faculty names? -> Faculty roster\n"
    "Who works on machine learning for electronic health records? -> "
    "Machine learning on health records"
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

    There is exactly one automatic route. Every question asked in Hybrid or
    Vector mode goes to the classify then judge then extract pipeline, which is
    what the baseline app does with every question it has ever been asked.

    Cypher mode is still honoured because the user selects it explicitly in the
    composer. Nothing routes there on its own, so a question typed in Hybrid mode
    can never be answered by a generated query the way it could before.
    """
    watch = Stopwatch()

    if mode == "cypher":
        return await _run_cypher_mode(question, session_id, watch, emit)

    return await _run_graphrag_mode(
        question, mode, session_id, store, agent_override, watch, emit
    )


def format_pipeline_results(results: list[dict[str, Any]]) -> str:
    """
    Render the pipeline's results into the baseline app's answer string.

    One bold name per person followed by a dash bullet for each item, blank line
    between people, and a single flat sentence when there is nothing. This is the
    text the original chat window shows, reproduced exactly so the two apps can
    be compared side by side.
    """
    if not results:
        return "No matching faculty were found for that question."

    lines: list[str] = []
    for item in results:
        lines.append(f"**{item.get('name', 'Unknown')}**")
        information = item.get("information")
        if isinstance(information, list):
            for point in information:
                lines.append(f"- {point}")
        else:
            lines.append(f"- {information}")
        lines.append("")
    return "\n".join(lines).strip()

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
    """
    The baseline pipeline: classify, then either extract directly or discover.

    Agent selection runs concurrently with classification purely to save wall
    clock. It has no influence on either branch below.
    """
    await emit("stage", {"stage": "classify", "label": "Understanding the question"})
    await emit("stage", {"stage": "route", "label": "Choosing a domain agent"})

    classification_task = asyncio.create_task(classify_question(question))
    agent_task: asyncio.Task | None = None
    if agent_override:
        agent_name = agent_override
    else:
        agent_task = asyncio.create_task(select_agent(question))
        agent_name = ""

    classification = await classification_task
    qtype = classification["type"]
    watch.mark("classify", "Classified the question", qtype)

    if agent_task is not None:
        agent_name = await agent_task
    watch.mark("route", "Selected domain agent", agent_name)
    await emit("stage", {"stage": "route", "label": "Selected domain agent", "detail": agent_name})

    retriever = build_retriever(mode)

    # ---- Which faculty are we extracting for, if any are already decided ----
    #
    # Follow-up state is read from the session rather than previous_faculty.json.
    # That file is process-wide in the baseline, so two people using the app at
    # once resolve "their degrees" against each other's last answer. Keying it by
    # session gives one user in one conversation exactly the same sequence of
    # values the file would have held, without the crosstalk.
    if qtype == "named" and classification["faculty"]:
        target_faculty = classification["faculty"]
        store.set_previous_faculty(session_id, target_faculty)
    elif qtype == "followup":
        previous = list(store.get(session_id).previous_faculty)
        if not previous:
            # The baseline prints "No previous faculty found." and returns an
            # empty result rather than falling back to a discovery search.
            log.info("No previous faculty found for session %s", session_id)
            return _empty_response(mode, qtype, agent_name, session_id, watch)
        target_faculty = apply_subset(previous, classification["subset"])
    else:
        target_faculty = []

    # ---- Direct extraction: named or follow up ----
    if qtype in {"named", "followup"} and target_faculty:
        await emit(
            "stage",
            {
                "stage": "retrieve",
                "label": "Searching the graph",
                "detail": f"{len(target_faculty)} faculty",
            },
        )
        # The baseline searches for the question and then for each faculty name,
        # deduplicating across the set.
        chunks = await retrieve_many(retriever, [question] + target_faculty)
        watch.mark("retrieve", "Retrieved graph context", f"{len(chunks)} chunks")

        if not chunks:
            return _empty_response(mode, qtype, agent_name, session_id, watch)

        results, missing = await _extract_all(chunks, question, target_faculty, watch, emit)
        store.append_history(session_id, question, results)

        return _answer_response(
            mode=mode,
            qtype=qtype,
            agent_name=agent_name,
            session_id=session_id,
            watch=watch,
            results=results,
            retrieved=len(chunks),
            judged=0,
            kept=len(target_faculty),
            cutoff=None,
            judgements=[],
            missing=missing,
        )

    # ---- Discovery ----
    await emit("stage", {"stage": "retrieve", "label": "Searching the graph"})

    # Two retrievals, unioned, because they do different jobs.
    #
    # The selected retriever goes DEEP: it returns many passages for whoever
    # ranks highest, which is what gives a strong candidate enough evidence to
    # score well.
    #
    # The coverage query goes WIDE: it guarantees every faculty member with any
    # material gets judged. Ranked retrieval alone reached only 9 of 20 faculty
    # on "expertise in cystic fibrosis" — the other 11 were never scored, and the
    # answer gave no hint they had been skipped.
    #
    # Coverage alone is worse: capping each person at a handful of passages
    # starves the genuinely strong candidates of evidence and their scores
    # collapse. Depth plus breadth is what works.
    if settings.coverage_retrieval:
        primary, coverage = await asyncio.gather(
            retrieve(retriever, question),
            retrieve_per_faculty(question),
            return_exceptions=True,
        )
        primary_chunks = primary if isinstance(primary, list) else []
        coverage_chunks = coverage if isinstance(coverage, list) else []
        if not isinstance(primary, list):
            log.warning("Ranked retrieval failed: %s", primary)
        if not isinstance(coverage, list):
            log.warning("Coverage retrieval failed: %s", coverage)
        chunks = dedupe(primary_chunks + coverage_chunks)
    else:
        # One ranked search, exactly as the baseline does it. Faculty whose CV
        # text does not rank inside top_k are never scored on this question.
        primary_chunks = await retrieve(retriever, question)
        coverage_chunks = []
        chunks = primary_chunks

    covered = len({faculty_from_source(c["source"]) for c in chunks if c.get("source")})
    if coverage_chunks:
        detail = (f"{len(chunks)} chunks, all {covered} faculty evaluated "
                  f"({len(primary_chunks)} ranked + {len(coverage_chunks)} coverage)")
    else:
        detail = f"{len(chunks)} chunks, {covered} faculty reached"
    watch.mark("retrieve", "Retrieved graph context", detail)

    if not chunks:
        return _empty_response(mode, qtype, agent_name, session_id, watch)

    blocks = group_by_faculty(chunks)

    # Every block is judged unless a ceiling is configured, which it is not by
    # default. The baseline judges all of them.
    if settings.max_judged_faculty > 0:
        blocks.sort(key=lambda b: int(b.get("chunks", 0)), reverse=True)
        capped = blocks[: settings.max_judged_faculty]
    else:
        capped = blocks
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
        detail += f", {dropped} not judged"
    watch.mark("judge", "Assessed faculty relevance", detail)

    await emit(
        "trace",
        {"ranked": [{"name": v["faculty_name"], "score": v["score"]} for v in ranked]},
    )

    kept, cutoff_note = apply_cutoff(ranked)
    watch.mark("rank", "Ranked and filtered", cutoff_note)
    await emit("stage", {"stage": "rank", "label": "Ranked and filtered", "detail": cutoff_note})

    judgements = [
        {
            "name": v["faculty_name"],
            "score": v["score"],
            "rationale": v["details"] or None,
            "kept": any(k["faculty_name"] == v["faculty_name"] for k in kept),
        }
        for v in ranked
    ]

    # Names are deduplicated in rank order, matching unique_preserve_order.
    names: list[str] = []
    for entry in kept:
        name = entry["faculty_name"].strip().replace("'", "").replace('"', "")
        if name not in names:
            names.append(name)

    if not names:
        return _empty_response(
            mode, qtype, agent_name, session_id, watch,
            judged=len(capped), judgements=judgements, retrieved=len(chunks),
        )

    store.set_previous_faculty(session_id, names)

    results, missing = await _extract_all(chunks, question, names, watch, emit)

    by_name = {k["faculty_name"]: k for k in kept}
    for item in results:
        verdict = by_name.get(item["name"])
        if verdict:
            item["score"] = verdict["score"]

    # No re-sort. `_extract_all` gathers in `names` order, which is already
    # relevance order, and the baseline never reorders afterwards. Sorting on the
    # returned name would push a result whose name the model rephrased to the end
    # rather than leaving it in rank position.
    store.append_history(session_id, question, results)

    return _answer_response(
        mode=mode,
        qtype=qtype,
        agent_name=agent_name,
        session_id=session_id,
        watch=watch,
        results=results,
        retrieved=len(chunks),
        judged=len(capped),
        kept=len(kept),
        cutoff=cutoff_note,
        judgements=judgements,
        missing=missing,
    )


def _answer_response(
    *,
    mode: str,
    qtype: str,
    agent_name: str | None,
    session_id: str,
    watch: Stopwatch,
    results: list[dict[str, Any]],
    retrieved: int,
    judged: int,
    kept: int,
    cutoff: str | None,
    judgements: list[dict[str, Any]],
    missing: list[str],
) -> dict[str, Any]:
    """
    Build the API payload for a pipeline answer.

    `answerText` carries the baseline's rendered string and is what the client
    displays. The structured `faculty` list travels alongside it so feedback
    reports and the pipeline disclosure still have the underlying data, and
    `answerFormat` tells the client which of the two to render.
    """
    return {
        "mode": mode,
        "questionType": qtype,
        "intent": None,
        "agent": agent_name,
        "answerText": format_pipeline_results(results),
        "answerFormat": "legacy",
        "faculty": results,
        "cypher": None,
        "trace": {
            "stages": watch.stages,
            "retrievedChunks": retrieved,
            "judged": judged,
            "kept": kept,
            "cutoff": cutoff,
            "intent": None,
            "skill": None,
            "coverage": None,
            "judgements": judgements,
            "noEvidence": missing,
        },
        "timings": {"totalMs": watch.total_ms},
        "sessionId": session_id,
    }

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
    judgements: list[dict[str, Any]] | None = None,
    retrieved: int = 0,
) -> dict[str, Any]:
    """
    The answer when nothing survived.

    One flat sentence, the same one the baseline shows for every empty case:
    no context found, no previous faculty, nothing past the cutoff, nothing
    extractable. The rewrite explained which of those it was and named the
    people who scored well but yielded nothing, which reads better and is
    different text. The scores are still in the trace either way.
    """
    return {
        "mode": mode,
        "questionType": qtype,
        "intent": None,
        "agent": agent_name,
        "answerText": "No matching faculty were found for that question.",
        "answerFormat": "legacy",
        "faculty": [],
        "cypher": None,
        "trace": {
            "stages": watch.stages,
            "retrievedChunks": retrieved,
            "judged": judged,
            "kept": 0,
            "cutoff": None,
            "intent": None,
            "skill": None,
            "coverage": None,
            "judgements": judgements or [],
            "noEvidence": [],
        },
        "timings": {"totalMs": watch.total_ms},
        "sessionId": session_id,
    }