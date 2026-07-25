"""
Routing regression tests.

These lock in the behaviour that fixed the "what are the 20 faculty names"
failure. They need no database and no OpenAI key: the deterministic matcher and
the classification cleaner are both pure functions.

Run with:  .venv/bin/python -m pytest backend/tests -q
        or .venv/bin/python backend/tests/test_routing.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.skills import SKILLS, match_skill  # noqa: E402

# Roster phrasings with no subject criterion. These must route deterministically,
# with no model call, because the graph holds the exact answer.
ROSTER = [
    "What are the 20 faculty names?",
    "what are the twenty faculty names",
    "list all faculty",
    "list the faculty",
    "List all faculty members",
    "who are the faculty?",
    "who are the faculty members",
    "how many faculty members are in DBE",
    "how many faculty are there",
    "show me all faculty",
    "name the faculty",
    "faculty list",
    "give me the faculty roster",
    "all of the faculty",
]

# Anything with a subject filter must NOT hit the roster skill. Answering these
# with the full roster would be a confident wrong answer, which is the worst kind.
NOT_ROSTER = [
    "list the faculty who study asthma",
    "name all faculty with NIH funding",
    "Which faculty have expertise in cystic fibrosis?",
    "list faculty who work on machine learning",
    "which faculty research spatial methods",
    "faculty that publish on genomics",
    "list all faculty with grants",
    "who works on Bayesian methods",
    "faculty investigating pediatric asthma",
    "which faculty analyze EHR data",
    "Find faculty with experience in clinical trial design",
    "tell me about Cole Brokamp",
    "what is Rhonda Szczesniak working on",
    "their education",
]

OTHER_SKILLS = [
    ("what document categories exist", "document_categories"),
    ("What kinds of documents are in the graph?", "document_categories"),
    ("which funding agencies fund the most grants", "grants_by_agency"),
    ("who funds the research", "grants_by_agency"),
    ("how big is the knowledge graph", "graph_summary"),
    ("how many nodes are there", "graph_summary"),
]


def test_roster_routes_deterministically() -> None:
    for question in ROSTER:
        skill = match_skill(question)
        assert skill is not None, f"no skill matched: {question!r}"
        assert skill.id == "faculty_roster", f"{question!r} routed to {skill.id}"


def test_filtered_questions_do_not_hit_roster() -> None:
    for question in NOT_ROSTER:
        skill = match_skill(question)
        assert skill is None or skill.id != "faculty_roster", (
            f"{question!r} wrongly routed to the roster skill, which would return "
            f"all 20 names instead of the filtered subset"
        )


def test_other_skills_route() -> None:
    for question, expected in OTHER_SKILLS:
        skill = match_skill(question)
        assert skill is not None, f"no skill matched: {question!r}"
        assert skill.id == expected, f"{question!r} routed to {skill.id}, want {expected}"


def test_every_skill_has_a_summariser_or_falls_back() -> None:
    # Not a hard requirement, but a skill with no summariser costs a model call,
    # so this documents which ones do.
    for skill in SKILLS.values():
        assert skill.cypher.strip(), f"{skill.id} has no Cypher"
        assert skill.description.strip(), f"{skill.id} has no description"
        assert skill.caption.strip(), f"{skill.id} has no caption"


BACK_REFERENCES = [
    "What are their degrees?",
    "list them",
    "the first two",
    "what about these people",
    "his publications",
    "and their grants",
    "the top three",
    "same question for the others",
]

SELF_CONTAINED = [
    "What are the 20 faculty names?",
    "list all faculty",
    "which faculty work on asthma",
    "how many grants are there",
    "who funds the research",
    "what document categories exist",
]


def test_back_references_are_detected() -> None:
    """
    A pronoun means the answer depends on the previous turn, so the question must
    never be handed to a standalone graph query. Missing this sent "what are their
    degrees" to a Cypher query that could not see who "their" referred to, and it
    returned nothing.
    """
    from app.pipeline import _has_back_reference

    for question in BACK_REFERENCES:
        assert _has_back_reference(question), f"missed a back reference: {question!r}"

    for question in SELF_CONTAINED:
        assert not _has_back_reference(question), (
            f"{question!r} is self contained but was treated as a back reference, "
            f"which would block the fast graph route"
        )


def test_faculty_result_carries_no_rationale() -> None:
    # The judge's prose must never be part of the answer payload again.
    from app.schemas import FacultyResult

    assert "details" not in FacultyResult.model_fields
    assert "rationale" not in FacultyResult.model_fields
    assert set(FacultyResult.model_fields) == {"name", "score", "information"}


def test_trace_carries_the_internals() -> None:
    from app.schemas import Trace

    for field in ("intent", "skill", "coverage", "judgements", "noEvidence"):
        assert field in Trace.model_fields, f"Trace is missing {field}"


if __name__ == "__main__":
    failures = 0
    for name, fn in sorted(globals().items()):
        if not name.startswith("test_") or not callable(fn):
            continue
        try:
            fn()
            print(f"PASS  {name}")
        except AssertionError as exc:
            failures += 1
            print(f"FAIL  {name}: {exc}")
    total = sum(1 for n in globals() if n.startswith("test_"))
    print(f"\n{total - failures}/{total} passed")
    sys.exit(1 if failures else 0)
