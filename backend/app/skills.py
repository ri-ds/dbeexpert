"""
skills.py

Deterministic graph queries for factual questions.

Why this exists: a question like "what are the 20 faculty names" is a fact the
graph already holds exactly. Answering it by embedding the question, pulling
similar CV passages, and asking an LLM to score each person against it is not
merely slower, it is wrong in kind. The roster is not a similarity judgement.

Every skill here is hand written, parameterised, and read only. No LLM writes
the Cypher, so these answers cannot hallucinate and cannot drift. A skill either
matches and returns the truth, or it does not match and the caller falls through
to generated Cypher or semantic search.

Roster answers are also summarised without an LLM, so the most common factual
question in this app costs nothing and cannot be garbled.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Callable

from .db import run_read

# Topical language means the user wants people who match a subject, which is a
# semantic question no matter how much it looks like a list request. This guard
# keeps "which faculty work on cystic fibrosis" out of the roster skill.
#
# Activity verbs need a preposition here. A bare "research" is far too common in
# this domain to treat as topical: "who funds the research" is a plain factual
# question about funding agencies, not a request to score people against a topic.
# Bare activity verbs are handled positionally by _FILTERED instead.
_TOPICAL = re.compile(
    r"\b(expertise|expert|specialis\w*|specializ\w*"
    r"|work(s|ing)?\s+on|research(es|ing)?\s+(on|in|into)"
    r"|focus(es|ing)?\s+on|interested\s+in|publish(es|ed|ing)?\s+(on|in|about)"
    r"|experience\s+(in|with)|background\s+in|skilled\s+in|involved\s+in"
    r"|stud(y|ies|ying)\s+\w|investigat(es|ing)\s+\w)\b",
    re.IGNORECASE,
)

# A qualifier or activity verb following "faculty" means the user wants a filtered
# subset, not the whole roster. Returning all 20 names to "list the faculty who
# study asthma" is a confident wrong answer, which is the worst kind.
#
# Position matters: "who" is only a filter when it follows the noun. "Who are the
# faculty" is a roster question, "faculty who study asthma" is not.
#
# An affiliation phrase is allowed to sit in between, because people write the
# division and the site into the question:
#
#   "faculty who are doing neuroimaging"              caught before
#   "faculty at CCHMC who are doing neuroimaging"      NOT caught, returned all 20
#
# Requiring strict adjacency made "at CCHMC" enough to defeat the guard. The
# filler is deliberately narrow, a preposition plus at most three words, so the
# positional rule still holds and "who are the faculty" stays a roster question.
_FILTER_FILLER = r"(?:\s+(?:at|in|of|from|within|across)(?:\s+[\w'&.-]+){1,3})?"
_FILTERED = re.compile(
    r"\bfacult\w*" + _FILTER_FILLER + r"\s+("
    r"who|whom|that|which|with|having|holding|receiving|awarded"
    r"|research\w*|stud(y|ies|ying)|investigat\w*|analy[sz]\w*"
    r"|publish\w*|work\w*|focus\w*|specialis\w*|specializ\w*|explor\w*"
    r"|do(es|ing)?|using|apply\w*|appl(y|ies|ying)"
    r")\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class Skill:
    id: str
    # Shown to the intent classifier so it can pick a skill by description.
    description: str
    cypher: str
    caption: str
    # High precision patterns that route without consulting the model at all.
    patterns: tuple[re.Pattern[str], ...] = ()
    # Phrases that rule this skill out even when a pattern above matches. Used
    # where a near miss would produce a confident wrong answer.
    blockers: tuple[re.Pattern[str], ...] = ()
    # Deterministic prose. Receives the rows and returns the answer text.
    summarise: Callable[[list[dict[str, Any]]], str] | None = None
    params: dict[str, Any] = field(default_factory=dict)
    # How the result should be presented.
    #
    #   "table"  the rows are the answer. `summarise` returns a single lead in
    #            line only, because restating every row in prose next to the
    #            table shows the user the same data twice.
    #   "prose"  the answer is a sentence about a single row, so the one row
    #            table adds nothing and is suppressed.
    presentation: str = "table"

    @property
    def show_table(self) -> bool:
        return self.presentation == "table"

    def matches(self, question: str) -> bool:
        # Deterministic routing must be high precision. Anything that hints at a
        # subject filter falls through to the model, which can weigh it properly.
        if _TOPICAL.search(question) or _FILTERED.search(question):
            return False
        # Per skill exclusions. These cannot live in the shared guards above: the
        # phrase that disqualifies one skill is often exactly what identifies
        # another, and a shared guard would block both.
        if any(pattern.search(question) for pattern in self.blockers):
            return False
        return any(pattern.search(question) for pattern in self.patterns)


def _p(*patterns: str) -> tuple[re.Pattern[str], ...]:
    return tuple(re.compile(p, re.IGNORECASE) for p in patterns)


# ----------------------------------------------------------------------
# Summarisers, so the common cases need no LLM call at all
# ----------------------------------------------------------------------

# Every summariser for a "table" skill returns ONE line. The table below it
# carries the data. Enumerating the rows here as well rendered the same answer
# twice, once as prose and once as a table, with the query wedged between them.


def _summarise_roster(rows: list[dict[str, Any]]) -> str:
    names = [str(r["faculty"]) for r in rows if r.get("faculty")]
    if not names:
        return "No faculty were found in the graph."
    plural = "member" if len(names) == 1 else "members"
    return (
        f"The Division of Biostatistics and Epidemiology has {len(names)} faculty "
        f"{plural} in the knowledge graph, listed below."
    )


def _summarise_categories(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return "No document categories were found."
    total = sum(int(r.get("chunks") or 0) for r in rows)
    return (
        f"The graph holds {total:,} document passages across {len(rows)} categories, "
        f"broken down below."
    )


def _summarise_per_faculty(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return "No per faculty document counts were found."
    total = sum(int(r.get("passages") or 0) for r in rows)
    return (
        f"Document coverage for all {len(rows)} faculty members, {total:,} passages in "
        f"total, ordered by volume."
    )


def _summarise_publications(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return "No publication records were found in the graph."
    total = sum(int(r.get("publications") or 0) for r in rows)
    return (
        f"Publication counts for all {len(rows)} faculty members, {total:,} distinct "
        f"titles in total, ordered by count. Titles are taken from the publications "
        f"section of each CV, so treat these as close rather than exact."
    )


def _summarise_grants_by_agency(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return "No funding agencies were found in the graph."
    top = rows[0]
    return (
        f"{len(rows)} funding agencies appear in the graph. {top['agency']} funds the "
        f"most with {int(top['grants'])} grants. Full breakdown below."
    )


def _summarise_graph(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return "The graph summary could not be read."
    r = rows[0]
    return (
        f"The knowledge graph holds {int(r['nodes']):,} nodes and "
        f"{int(r['relationships']):,} relationships, including "
        f"{int(r['chunks']):,} document passages covering "
        f"{int(r['faculty'])} faculty members."
    )


# ----------------------------------------------------------------------
# The registry
# ----------------------------------------------------------------------

# Chunk.source2 follows '<Faculty Name>_<Category>', so the faculty roster is
# the distinct set of prefixes. This is the only reliable roster in the graph:
# there are 6,402 :Person nodes with no label separating the 20 faculty from
# their co-authors, and each faculty member appears under many name variants.
_ROSTER_CYPHER = """
MATCH (c:Chunk)
WHERE c.source2 IS NOT NULL
RETURN DISTINCT split(c.source2, '_')[0] AS faculty
ORDER BY faculty
"""

_CATEGORIES_CYPHER = """
MATCH (c:Chunk)
WHERE c.source2 IS NOT NULL
WITH split(c.source2, '_') AS parts
WITH parts[size(parts) - 1] AS category
RETURN category, count(*) AS chunks
ORDER BY chunks DESC
"""

_PER_FACULTY_CYPHER = """
MATCH (c:Chunk)
WHERE c.source2 IS NOT NULL
WITH split(c.source2, '_')[0] AS faculty, c.source2 AS source
WITH faculty, count(*) AS passages, count(DISTINCT source) AS documents
RETURN faculty, passages, documents
ORDER BY passages DESC
"""

# Publications per person, which is a different question from how much CV text a
# person has. Asking for a publication count used to be answered with passage and
# document counts, which look like an answer and are not one.
#
# Two decisions worth recording, both measured against this graph:
#
#  1. Counting Publication nodes overcounts badly. The same paper is extracted
#     from several passages and lands as several nodes: one faculty member has 226
#     Publication nodes but only 145 distinct titles. So the count is over
#     lowercased, trimmed titles, not over nodes.
#  2. Only the Publications section of the CV counts. Restricting to source2
#     ending in "_Publications" excludes papers mentioned in passing inside a
#     grant or abstract section, and still covers all 20 faculty.
#
# These are titles extracted from CV text, so treat the totals as close rather
# than exact. The caption says so, because a precise looking number that is not
# precise is worse than an honest approximation.
_PUBLICATIONS_PER_FACULTY_CYPHER = """
MATCH (p:Publication)-[:FROM_CHUNK]->(c:Chunk)
WHERE c.source2 ENDS WITH '_Publications' AND p.name IS NOT NULL
WITH split(c.source2, '_')[0] AS faculty, toLower(trim(p.name)) AS title
WHERE title <> ''
RETURN faculty, count(DISTINCT title) AS publications
ORDER BY publications DESC, faculty
"""

_GRANTS_BY_AGENCY_CYPHER = """
MATCH (g:Grant)-[:fundedBy|funded_by]->(a:FundingAgency)
WHERE a.name IS NOT NULL
RETURN a.name AS agency, count(DISTINCT g) AS grants
ORDER BY grants DESC, agency
LIMIT 40
"""

_GRAPH_SUMMARY_CYPHER = """
CALL () { MATCH (n) RETURN count(n) AS nodes }
CALL () { MATCH ()-[r]->() RETURN count(r) AS relationships }
CALL () { MATCH (c:Chunk) RETURN count(c) AS chunks }
CALL () {
  MATCH (c:Chunk) WHERE c.source2 IS NOT NULL
  RETURN count(DISTINCT split(c.source2, '_')[0]) AS faculty
}
RETURN nodes, relationships, chunks, faculty
"""

SKILLS: dict[str, Skill] = {
    "faculty_roster": Skill(
        id="faculty_roster",
        description=(
            "The list of faculty members in the division, names only. Use for any "
            "request to list, name, enumerate, or count the faculty themselves, "
            "with no subject matter criterion attached and no other detail asked "
            "for about each person."
        ),
        cypher=_ROSTER_CYPHER,
        caption="Faculty roster",
        summarise=_summarise_roster,
        # This skill returns names and nothing else, so a request for names plus
        # some attribute of each person is not a roster question, even though it
        # opens like one. "List every faculty member and their number of
        # publications" was answered with the bare roster because this skill is
        # checked first and its list pattern matched the opening words.
        #
        # Note that a bare "how many faculty are there" must stay a roster
        # question, so counting words alone cannot be a blocker here.
        blockers=_p(
            r"\band\s+(their|his|her|its)\b",
            r"\bwith\s+(their|his|her)\b",
            r"\balong\s+with\b",
            r"\band\s+how\s+many\b",
            r"\b(their|the)\s+(number|count|total|amount)\s+of\b",
        ),
        patterns=_p(
            r"\b(list|name|show|give|enumerate)\b[^?]{0,40}\bfacult",
            r"\bwhat\s+(are|is)\b[^?]{0,40}\bfacult\w*\s*(names|members|list|roster)?",
            r"\bwho\s+(are|is)\b[^?]{0,30}\bfacult",
            r"\bhow\s+many\s+facult",
            r"\bfacult\w*\s+(names|roster|list|members)\b",
            r"\ball\s+(of\s+the\s+)?facult",
            r"\b(20|twenty)\s+facult",
        ),
    ),
    "document_categories": Skill(
        id="document_categories",
        description=(
            "The kinds of source documents in the graph, such as Publications, "
            "Abstracts, Mentoring, Leadership, and how many passages each has."
        ),
        cypher=_CATEGORIES_CYPHER,
        caption="Document categories",
        summarise=_summarise_categories,
        patterns=_p(
            r"\b(document|doc|source)\s+(categor|type|kind)",
            r"\bwhat\s+(kinds?|types?|categor\w+)\s+of\s+(document|source|data|record)",
            r"\bcategor\w+\s+of\s+(document|source)",
        ),
    ),
    "documents_per_faculty": Skill(
        id="documents_per_faculty",
        description=(
            "How much source material each faculty member has in the graph, as a "
            "count of passages and documents per person. This is a measure of CV "
            "text volume, NOT a count of publications. For publications use "
            "publications_per_faculty instead."
        ),
        cypher=_PER_FACULTY_CYPHER,
        caption="Documents per faculty member",
        summarise=_summarise_per_faculty,
        # Deliberately narrow, and deliberately not matching "publications".
        # These patterns used to swallow "how many publications does each faculty
        # member have" and answer it with passage counts.
        patterns=_p(
            r"\bhow\s+(many|much)\s+(document|passage|chunk|record|data)\w*\s+"
            r"(does|do|per|for)\b[^?]{0,30}\bfacult",
            r"\b(document|passage|chunk)\w*\s+per\s+facult",
        ),
    ),
    "publications_per_faculty": Skill(
        id="publications_per_faculty",
        description=(
            "How many publications each faculty member has, as a count of distinct "
            "publication titles listed in their CV. Use for any request for "
            "publication counts, numbers of papers, or who publishes most."
        ),
        cypher=_PUBLICATIONS_PER_FACULTY_CYPHER,
        caption="Publications per faculty member",
        summarise=_summarise_publications,
        patterns=_p(
            r"\b(number|count)\s+of\s+publications?\b",
            r"\bhow\s+many\s+(publications?|papers?|articles?)\b",
            r"\bpublications?\s+(count|per|each|total)\b",
            r"\b(publications?|papers?)\s+(does|do)\s+(each|every|the)\b",
            r"\btheir\s+number\s+of\s+publications?\b",
            r"\bwho\s+(has|have)\s+the\s+most\s+(publications?|papers?)\b",
            r"\bmost\s+(published|prolific)\b",
        ),
    ),
    "grants_by_agency": Skill(
        id="grants_by_agency",
        description=(
            "Which funding agencies fund the most grants in the graph, as a count "
            "of grants per agency."
        ),
        cypher=_GRANTS_BY_AGENCY_CYPHER,
        caption="Grants by funding agency",
        # Deterministic lead in rather than falling through to answer_from_rows,
        # which cost a model call to restate a table that was already on screen.
        summarise=_summarise_grants_by_agency,
        patterns=_p(
            r"\b(funding\s+agenc|funder)\w*\b",
            r"\bgrants?\s+(by|per|from)\s+(agenc|funder|sponsor)",
            r"\bwho\s+funds\b",
        ),
    ),
    "graph_summary": Skill(
        id="graph_summary",
        description=(
            "The overall size and shape of the knowledge graph: total nodes, "
            "relationships, passages, and faculty covered."
        ),
        cypher=_GRAPH_SUMMARY_CYPHER,
        caption="Knowledge graph summary",
        summarise=_summarise_graph,
        # One row of four counts. The sentence says it all, so a single row table
        # underneath would just be the same numbers again.
        presentation="prose",
        patterns=_p(
            r"\bhow\s+(big|large)\b[^?]{0,20}\b(graph|database|knowledge\s+graph)",
            r"\b(graph|database)\s+(size|summary|statistics|stats|overview)",
            r"\bhow\s+many\s+(nodes|relationships|entities)\b",
        ),
    ),
}


def skill_catalogue() -> str:
    """Compact description block for the intent classifier prompt."""
    return "\n".join(f"- {s.id}: {s.description}" for s in SKILLS.values())


def match_skill(question: str) -> Skill | None:
    """
    Deterministic routing for unmistakable phrasings.

    This runs before the model is consulted so the most common factual questions
    are guaranteed to route correctly even if the classifier misbehaves. Order is
    fixed by the registry, and roster is first because it is the most common.
    """
    for skill in SKILLS.values():
        if skill.matches(question):
            return skill
    return None


def get_skill(skill_id: str | None) -> Skill | None:
    if not skill_id:
        return None
    return SKILLS.get(skill_id.strip())


def run_skill(skill: Skill) -> tuple[list[str], list[dict[str, Any]]]:
    """Execute a skill's Cypher. Read only by construction."""
    rows = run_read(skill.cypher, skill.params)
    columns = list(rows[0].keys()) if rows else []
    return columns, rows
