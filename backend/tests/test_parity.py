"""
Parity tests against the baseline app.

Every assertion here encodes what ankitaexpert does, taken from its source:

    ankitaexpert/llm_utils.py    classify cleanup, chunk merging, source
                                 matching, relevance cutoff
    ankitaexpert/app.py          format_pipeline_results
    ankitaexpert/retrievers.py   retriever construction

These are the parts of the pipeline that are pure functions, so they can be
checked exactly, with no database and no OpenAI key. What cannot be checked here
is the model's own output: both apps call a reasoning model that rejects
`temperature`, so identical prompts still sample differently. These tests pin
down everything except that.

Run with:  .venv/bin/python -m pytest backend/tests -q
        or .venv/bin/python backend/tests/test_parity.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import pipeline  # noqa: E402
from app.retrievers import source_matches_faculty  # noqa: E402
from app.settings import settings  # noqa: E402


# ----------------------------------------------------------------------
# Routing: there is one path, and nothing bypasses it
# ----------------------------------------------------------------------

def test_no_skill_router_remains() -> None:
    """
    The deterministic matcher and the intent axis are what made the same
    question take a different path, so neither may come back by accident.
    """
    assert not hasattr(pipeline, "match_skill")
    assert not hasattr(pipeline, "_run_skill")
    assert not hasattr(pipeline, "_run_chitchat")
    assert not hasattr(pipeline, "_has_back_reference")

    import importlib.util

    assert importlib.util.find_spec("app.skills") is None, (
        "app.skills is importable again, which means the graph skill router "
        "could be reintroduced ahead of the pipeline"
    )


def test_classification_has_no_intent_axis() -> None:
    cleaned = pipeline._clean_classification(
        {"type": "first", "intent": "roster", "skill": "faculty_roster", "faculty": []}
    )
    assert set(cleaned) == {"type", "faculty", "subset"}, (
        f"classification grew fields the baseline does not have: {sorted(cleaned)}"
    )


# ----------------------------------------------------------------------
# classify cleanup, against ankitaexpert/llm_utils.py:148-172
# ----------------------------------------------------------------------

def test_unparseable_classification_falls_back_to_first() -> None:
    for bad in (None, "NONE", [], "not json"):
        assert pipeline._clean_classification(bad) == {
            "type": "first",
            "faculty": [],
            "subset": {"position": "all", "count": None},
        }


def test_unknown_type_becomes_first() -> None:
    assert pipeline._clean_classification({"type": "roster"})["type"] == "first"


def test_named_faculty_must_match_the_allow_list_exactly() -> None:
    """
    The baseline resolves names with `f.lower() in allowed_lower` only. A
    surname alone does not resolve, which is why the app's own `canonicalise`
    helper is not used on this path.
    """
    from app.faculty import faculty_names

    names = faculty_names()
    if not names:
        print("  (skipped: names.csv not readable)")
        return

    full = names[0]
    surname = full.split()[-1]

    resolved = pipeline._clean_classification({"type": "named", "faculty": [full.lower()]})
    assert resolved["faculty"] == [full], "an exact name, case insensitive, must resolve"

    partial = pipeline._clean_classification({"type": "named", "faculty": [surname]})
    assert partial["faculty"] == [], (
        f"{surname!r} resolved to a full name; the baseline drops bare surnames"
    )

    unknown = pipeline._clean_classification({"type": "named", "faculty": ["Ada Lovelace"]})
    assert unknown["faculty"] == []


def test_quoted_names_are_stripped_then_matched() -> None:
    """unique_preserve_order strips quotes before comparing."""
    from app.faculty import faculty_names

    names = faculty_names()
    if not names:
        print("  (skipped: names.csv not readable)")
        return
    quoted = f"'{names[0]}'"
    assert pipeline._clean_classification({"type": "named", "faculty": [quoted]})["faculty"] == [
        names[0]
    ]


def test_named_with_no_resolvable_faculty_keeps_its_type() -> None:
    """
    The rewrite downgraded an unresolvable "named" to "first". The baseline does
    not: run_query checks `qtype == "named" and named`, so an empty list falls
    through to the discovery branch with the type still reading "named".
    """
    result = pipeline._clean_classification({"type": "named", "faculty": ["Nobody At All"]})
    assert result["type"] == "named"
    assert result["faculty"] == []


def test_subset_normalisation() -> None:
    assert pipeline._clean_classification({"type": "followup"})["subset"] == {
        "position": "all",
        "count": None,
    }
    got = pipeline._clean_classification(
        {"type": "followup", "subset": {"position": "last", "count": 3}}
    )
    assert got["subset"] == {"position": "last", "count": 3}
    for bad_count in (0, -2, "two", None, 1.5):
        cleaned = pipeline._clean_classification(
            {"type": "followup", "subset": {"position": "first", "count": bad_count}}
        )
        assert cleaned["subset"]["count"] is None, f"{bad_count!r} should normalise to None"


def test_apply_subset() -> None:
    people = ["A", "B", "C", "D"]
    assert pipeline.apply_subset(people, {"position": "all", "count": None}) == people
    assert pipeline.apply_subset(people, {"position": "first", "count": 2}) == ["A", "B"]
    assert pipeline.apply_subset(people, {"position": "last", "count": 3}) == ["B", "C", "D"]
    assert pipeline.apply_subset(people, None) == people


# ----------------------------------------------------------------------
# Answer formatting, against ankitaexpert/app.py:85-100
# ----------------------------------------------------------------------

def test_format_matches_the_baseline_string() -> None:
    results = [
        {"name": "Rhonda Szczesniak", "information": ["Longitudinal CF modeling", "Bayesian methods"]},
        {"name": "Cole Brokamp", "information": ["Spatial exposure"]},
    ]
    expected = (
        "**Rhonda Szczesniak**\n"
        "- Longitudinal CF modeling\n"
        "- Bayesian methods\n"
        "\n"
        "**Cole Brokamp**\n"
        "- Spatial exposure"
    )
    assert pipeline.format_pipeline_results(results) == expected


def test_format_empty_is_the_one_flat_sentence() -> None:
    assert (
        pipeline.format_pipeline_results([])
        == "No matching faculty were found for that question."
    )


def test_format_handles_a_non_list_information_value() -> None:
    """The baseline renders a bare string as a single bullet rather than failing."""
    assert pipeline.format_pipeline_results([{"name": "X", "information": "just one"}]) == (
        "**X**\n- just one"
    )


# ----------------------------------------------------------------------
# Chunk merging, against ankitaexpert/llm_utils.py:265-277
# ----------------------------------------------------------------------

def test_blocks_group_on_the_raw_source_prefix() -> None:
    chunks = [
        {"text": "one", "source": "Cole Brokamp_Publications"},
        {"text": "two", "source": "Cole Brokamp_Abstracts"},
        {"text": "three", "source": "Rhonda Szczesniak_Publications"},
    ]
    blocks = {b["faculty"]: b for b in pipeline.group_by_faculty(chunks)}
    assert set(blocks) == {"Cole Brokamp", "Rhonda Szczesniak"}
    assert blocks["Cole Brokamp"]["text"] == "one\ntwo"
    assert blocks["Cole Brokamp"]["chunks"] == 2


def test_blocks_are_not_truncated() -> None:
    """The 32k character ceiling is gone; a long CV keeps all of its evidence."""
    big = "x" * 40_000
    chunks = [{"text": big, "source": "A B_Publications"} for _ in range(3)]
    block = pipeline.group_by_faculty(chunks)[0]
    assert len(block["text"]) == 3 * 40_000 + 2, "block was capped"
    assert block["chunks"] == 3


def test_blocks_are_not_canonicalised_against_the_allow_list() -> None:
    """
    A source that does not match the allow list forms its own block rather than
    being folded into the nearest known name.
    """
    chunks = [
        {"text": "a", "source": "Szczesniak R_Publications"},
        {"text": "b", "source": "Rhonda Szczesniak_Publications"},
    ]
    faculties = {b["faculty"] for b in pipeline.group_by_faculty(chunks)}
    assert faculties == {"Szczesniak R", "Rhonda Szczesniak"}


# ----------------------------------------------------------------------
# Source matching, against ankitaexpert/llm_utils.py:88-91
# ----------------------------------------------------------------------

def test_source_matching_is_loose() -> None:
    assert source_matches_faculty("Cole Brokamp_Publications", "Cole Brokamp")
    # ANY token over two characters is enough, so a shared surname collides.
    assert source_matches_faculty("Cole Brokamp_Publications", "Jane Brokamp"), (
        "loose matching was tightened; the baseline matches on any single token"
    )
    # Tokens of two characters or fewer are ignored entirely.
    assert not source_matches_faculty("Cole Brokamp_Publications", "Xu Li")
    assert not source_matches_faculty("", "Cole Brokamp")


# ----------------------------------------------------------------------
# Relevance cutoff, against ankitaexpert/llm_utils.py:416-440
# ----------------------------------------------------------------------

def test_cutoff_constants() -> None:
    assert (
        pipeline.MANY_HIGH_SCORERS,
        pipeline.HIGH_SCORE,
        pipeline.STRICT_CUTOFF,
        pipeline.LENIENT_CUTOFF,
        pipeline.GAP_THRESHOLD,
    ) == (3, 70, 60, 40, 18)
    assert (pipeline.LATE_GAP_THRESHOLD, pipeline.LATE_GAP_AFTER, pipeline.TOP_BAND) == (10, 8, 15)


def _ranked(*scores: float) -> list[dict]:
    return [{"faculty_name": f"P{i}", "score": s} for i, s in enumerate(scores)]


def test_top_band_drops_anyone_far_below_the_leader() -> None:
    """
    TOP_BAND runs first: with a top score of 95 the floor is 80, exclusive, so
    80 and below go regardless of the other rules.
    """
    kept, note = pipeline.apply_cutoff(_ranked(95, 92, 88, 80, 55))
    assert [k["score"] for k in kept] == [95, 92, 88]
    assert "top score 95" in note and "above 80" in note


def test_eighteen_point_gap_rule_still_works_on_its_own() -> None:
    """The 18 point rule, tested directly on the gap stage."""
    cut, note = pipeline._truncate_at_gap(_ranked(95, 92, 88, 60, 55))
    assert [c["score"] for c in cut] == [95, 92, 88]
    assert "18" not in note or "point gap" in note


def test_top_band_makes_the_eighteen_point_gap_unreachable() -> None:
    """
    Worth recording, because it is a side effect of TOP_BAND rather than an
    intended rule. TOP_BAND is 15, so after the band cut every survivor is
    within 15 points of the leader and the largest possible drop between them is
    14. GAP_THRESHOLD is 18, so it can never fire once the band has run.

    Only LATE_GAP_THRESHOLD (10) is reachable through apply_cutoff.
    """
    assert pipeline.TOP_BAND < pipeline.GAP_THRESHOLD
    kept, note = pipeline.apply_cutoff(_ranked(95, 92, 88, 60, 55))
    assert [k["score"] for k in kept] == [95, 92, 88]
    assert "point gap" not in note, "the band already removed the low scorers"


def test_late_gap_threshold_tightens_after_eight_kept() -> None:
    """From the ninth candidate on, a 10 point drop is enough to cut."""
    kept, note = pipeline.apply_cutoff(_ranked(*([95] * 9 + [85])))
    assert len(kept) == 9
    assert "threshold 10" in note


def test_strict_cutoff_when_three_or_more_score_seventy_plus() -> None:
    kept, _ = pipeline.apply_cutoff(_ranked(75, 73, 71, 66, 61))
    # All within 15 of 75, no 18 point gap, three at 70 or above, so the floor
    # is "strictly above 60".
    assert [k["score"] for k in kept] == [75, 73, 71, 66, 61]


def test_lenient_cutoff_when_fewer_than_three_high_scorers() -> None:
    kept, _ = pipeline.apply_cutoff(_ranked(68, 62, 56, 54))
    # Top band floor is 53, no 18 point gap, only one scorer at 70 or above, so
    # the floor is "40 and above".
    assert [k["score"] for k in kept] == [68, 62, 56, 54]


def test_cutoff_order_is_band_then_gap_then_floor() -> None:
    """
    Order matters. 75, 55, 45, 39: the top band floor is 60, so everything below
    it goes before the gap or floor rules are even consulted.
    """
    kept, note = pipeline.apply_cutoff(_ranked(75, 55, 45, 39))
    assert [k["score"] for k in kept] == [75]
    assert note.index("top score") < note.index("strong candidates")


def test_empty_ranking() -> None:
    kept, note = pipeline.apply_cutoff([])
    assert kept == []
    assert note


# ----------------------------------------------------------------------
# Configuration that changes which chunks are retrieved
# ----------------------------------------------------------------------

def test_fulltext_index_is_the_baseline_one() -> None:
    assert settings.fulltext_index == "text_embeddings2", (
        "the hybrid retriever is paired with chunk_text_fulltext again, which "
        "changes which chunks come back and therefore which faculty answer"
    )


def test_judging_is_uncapped_by_default() -> None:
    assert settings.max_judged_faculty == 0, "a judge cap changes who is considered"


def test_coverage_retrieval_is_off_by_default() -> None:
    """
    Coverage retrieval must be OFF for parity with the baseline.

    With it off, a single ranked search reaches only 9 of 20 faculty on
    "expertise in cystic fibrosis" and the other 11 are never scored. That is a
    real hole, and it is the baseline's hole too, which is the point: matching
    the baseline means reproducing it. COVERAGE_RETRIEVAL=1 closes it and makes
    this app find faculty the baseline misses.
    """
    assert settings.coverage_retrieval is False, (
        "coverage retrieval is enabled; this app will find faculty ankitaexpert "
        "misses, so the two will not agree"
    )


def test_retrievers_use_no_result_formatter() -> None:
    """
    A formatter hands over the record's real fields and recovers chunks the
    baseline's regexes drop, so both retrievers must be built without one.
    """
    import inspect

    import app.retrievers as retrievers

    source = inspect.getsource(retrievers.build_retriever)
    assert "result_formatter=" not in source, "a custom result formatter is being passed"


# ----------------------------------------------------------------------
# LLM call shape
# ----------------------------------------------------------------------

def test_pipeline_prompts_do_not_use_json_mode() -> None:
    """
    JSON mode constrains the output space, so the same prompt over the same
    evidence returns different text with it on. The three pipeline calls must
    use the strict, unconstrained path.
    """
    import inspect

    for fn in (pipeline.classify_question, pipeline.judge_faculty, pipeline.extract_for_faculty):
        source = inspect.getsource(fn)
        assert "chat_strict_json" in source, f"{fn.__name__} is not on the strict path"
        assert "chat_json" not in source.replace("chat_strict_json", ""), (
            f"{fn.__name__} still calls JSON mode"
        )


def test_strict_parse_discards_what_the_baseline_discards() -> None:
    """
    parse_strict must accept and reject exactly what the baseline's inline parse
    does (llm_utils.py: `if answer == "NONE"` then `json.loads` in a bare
    try/except).

    The fenced-JSON case is the one that matters. gpt-4o wraps JSON in a ```json
    fence and gpt-5-mini returns it bare, so this parser and the chat model are a
    matched pair: rescuing fences here would keep replies the baseline drops, and
    switching the model to one that fences would drop everything and collapse
    every answer to "No matching faculty were found for that question."
    """
    from app.llm import parse_json, parse_strict

    cases = [
        ('{"a": 1}', {"a": 1}),
        ('  {"a": 1}  ', {"a": 1}),
        ("NONE", None),
        ('```json\n{"a": 1}\n```', None),
        ('```\n{"a": 1}\n```', None),
        ('Here you go: {"a": 1}', None),
        ("", None),
        ("not json at all", None),
    ]
    for raw, expected in cases:
        assert parse_strict(raw) == expected, f"parse_strict({raw!r}) != {expected!r}"

    # The tolerant parser stays available for the Cypher path and is unchanged.
    assert parse_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert parse_json('Here you go: {"a": 1}') == {"a": 1}


def test_temperature_is_never_sent_but_seed_is() -> None:
    """
    gpt-5-mini rejects temperature outright, so it must never be sent. A seed is
    accepted and is the only reproducibility lever the API offers here, so the
    baseline sets one and this app matches it.
    """
    import inspect

    from app import llm

    source = inspect.getsource(llm.chat)
    assert "temperature" not in source, "chat() is sending temperature"
    assert "seed" in source, "chat() is not sending a seed"
    assert settings.llm_seed == 42, f"seed is {settings.llm_seed!r}, baseline uses 42"
    assert settings.chat_model == "gpt-5-mini", (
        f"chat model is {settings.chat_model!r}; the baseline hardcodes gpt-5-mini"
    )


def test_seed_only_on_the_pipeline_calls() -> None:
    """Agent selection and conversation naming are left unseeded, as in the baseline."""
    import inspect

    from app import llm

    assert "use_seed=True" in inspect.getsource(llm.chat_strict_json)
    assert "use_seed" not in inspect.getsource(pipeline.select_agent)
    assert "use_seed" not in inspect.getsource(pipeline.generate_title)


def test_judge_prompt_carries_the_allowed_faculty_block() -> None:
    import inspect

    source = inspect.getsource(pipeline.judge_faculty)
    assert "Allowed Faculty:" in source
    assert '"Details"' in source, "the rationale key is capitalised in the baseline"
    assert "return ONLY:\nNONE" in source or "NONE" in source


def test_judge_applies_no_code_level_score_floor() -> None:
    import inspect

    source = inspect.getsource(pipeline.judge_faculty)
    assert "< 30" not in source, (
        "a hard score floor was reintroduced; the baseline leaves the 30 point "
        "rule to the model and keeps whatever object comes back"
    )


# ----------------------------------------------------------------------
# End to end through the real orchestration, with the model and the graph faked
# ----------------------------------------------------------------------

def _run_pipeline_with_fakes(question: str, chunks: list[dict], replies: dict[str, str]):
    """
    Drive `_run_graphrag_mode` with a stub LLM and a stub retriever.

    `replies` maps a marker found in the prompt to the raw string the model
    should return, so each stage can be steered independently. Everything
    between them is the real code path.
    """
    import asyncio

    from app import pipeline as pl
    from app.session import SessionStore

    calls: list[str] = []

    async def fake_chat(system, user, *, json_mode=False, model=None,
                        max_completion_tokens=None, use_seed=False):
        assert not json_mode, "a pipeline stage asked for JSON mode"
        for marker, reply in replies.items():
            if marker in user:
                calls.append(marker)
                return reply
        raise AssertionError(f"unexpected prompt: {user[:120]!r}")

    original_chat = pl.llm.chat
    original_build = pl.build_retriever
    original_retrieve = pl.retrieve
    original_retrieve_many = pl.retrieve_many

    async def fake_retrieve(retriever, query, top_k=None):
        return list(chunks)

    async def fake_retrieve_many(retriever, queries):
        from app.retrievers import dedupe
        out = []
        for _ in queries:
            out.extend(chunks)
        return dedupe(out)

    pl.llm.chat = fake_chat
    pl.build_retriever = lambda mode: object()
    pl.retrieve = fake_retrieve
    pl.retrieve_many = fake_retrieve_many
    try:
        return asyncio.run(
            pl._run_graphrag_mode(
                question, "hybrid", "s1", SessionStore(3600), None, pl.Stopwatch(), pl._noop_emit
            )
        ), calls
    finally:
        pl.llm.chat = original_chat
        pl.build_retriever = original_build
        pl.retrieve = original_retrieve
        pl.retrieve_many = original_retrieve_many


def test_discovery_end_to_end_renders_the_baseline_string() -> None:
    chunks = [
        {"text": "Bayesian trial design work.", "source": "Alpha One_Publications"},
        {"text": "Unrelated genomics work.", "source": "Beta Two_Publications"},
    ]
    replies = {
        "You are the router for a faculty-CV": '{"type":"first","faculty":[],'
        '"subset":{"position":"all","count":null}}',
        "select the SINGLE most relevant agent": "Research Agent",
        "Faculty Name:\nAlpha One": '{"faculty_name":"Alpha One","relevance_score":91,'
        '"Details":"Extensive Bayesian trial work."}',
        "Faculty Name:\nBeta Two": "NONE",
        "Faculty:\nAlpha One": '{"faculty_name":"Alpha One",'
        '"information":["Bayesian adaptive designs","Interim analyses"]}',
    }
    payload, calls = _run_pipeline_with_fakes("who does bayesian trials", chunks, replies)

    assert payload["answerFormat"] == "legacy"
    assert payload["answerText"] == (
        "**Alpha One**\n- Bayesian adaptive designs\n- Interim analyses"
    )
    assert payload["questionType"] == "first"
    assert payload["intent"] is None, "the intent axis leaked back into the payload"
    assert payload["cypher"] is None, "a discovery question produced a Cypher block"
    assert payload["agent"] == "Research Agent"
    # Beta Two returned NONE and must not appear anywhere in the answer.
    assert "Beta Two" not in payload["answerText"]
    assert [f["name"] for f in payload["faculty"]] == ["Alpha One"]
    # Both candidates were judged: no cap, no coverage pass, one search.
    assert payload["trace"]["judged"] == 2


def test_no_relevant_faculty_gives_the_flat_sentence() -> None:
    chunks = [{"text": "Unrelated.", "source": "Beta Two_Publications"}]
    replies = {
        "You are the router for a faculty-CV": '{"type":"first","faculty":[],'
        '"subset":{"position":"all","count":null}}',
        "select the SINGLE most relevant agent": "Research Agent",
        "Faculty Name:\nBeta Two": "NONE",
    }
    payload, _ = _run_pipeline_with_fakes("who does quantum chemistry", chunks, replies)
    assert payload["answerText"] == "No matching faculty were found for that question."
    assert payload["faculty"] == []


def test_a_roster_question_still_goes_through_the_judge() -> None:
    """
    The regression this whole change is about. "list all faculty" used to be
    intercepted by the skill matcher and answered from Cypher. It must now take
    the same classify then judge then extract path as everything else.
    """
    chunks = [{"text": "CV text.", "source": "Alpha One_Publications"}]
    replies = {
        "You are the router for a faculty-CV": '{"type":"first","faculty":[],'
        '"subset":{"position":"all","count":null}}',
        "select the SINGLE most relevant agent": "Research Agent",
        "Faculty Name:\nAlpha One": '{"faculty_name":"Alpha One","relevance_score":80,'
        '"Details":"Is a faculty member."}',
        "Faculty:\nAlpha One": '{"faculty_name":"Alpha One","information":["Alpha One"]}',
    }
    payload, calls = _run_pipeline_with_fakes("list all faculty", chunks, replies)
    assert payload["cypher"] is None, "a roster question was answered from the graph again"
    assert payload["trace"]["skill"] is None
    assert any("strict research evaluator" in c or "Faculty Name:" in c for c in calls), (
        "the relevance judge did not run"
    )


def test_judge_rationale_never_reaches_the_answer() -> None:
    chunks = [{"text": "CV text.", "source": "Alpha One_Publications"}]
    replies = {
        "You are the router for a faculty-CV": '{"type":"first","faculty":[],'
        '"subset":{"position":"all","count":null}}',
        "select the SINGLE most relevant agent": "Research Agent",
        "Faculty Name:\nAlpha One": '{"faculty_name":"Alpha One","relevance_score":91,'
        '"Details":"SCORING PROSE THAT MUST NOT BE SHOWN."}',
        "Faculty:\nAlpha One": '{"faculty_name":"Alpha One","information":["Real evidence"]}',
    }
    payload, _ = _run_pipeline_with_fakes("who does x", chunks, replies)
    assert "SCORING PROSE" not in payload["answerText"]
    assert payload["trace"]["judgements"][0]["rationale"] == (
        "SCORING PROSE THAT MUST NOT BE SHOWN."
    )


def test_corrupt_source_prefix_folds_onto_the_real_person() -> None:
    """
    A stray apostrophe in Chunk.source2 must not create a phantom 21st faculty
    member. Measured on the live graph: one chunk carried "'Emrah Gecili" and
    formed its own 504 character block beside the real 40,483 character one, so
    the pipeline judged 21 candidates when only 20 faculty exist.
    """
    from app.retrievers import faculty_from_source

    assert faculty_from_source("'Emrah Gecili_Publications") == "Emrah Gecili"
    assert faculty_from_source('"Bin Huang_Abstracts') == "Bin Huang"
    assert faculty_from_source("Emrah Gecili_Publications") == "Emrah Gecili"

    chunks = [
        {"text": "a", "source": "Emrah Gecili_Publications"},
        {"text": "b", "source": "'Emrah Gecili_Abstracts"},
    ]
    blocks = pipeline.group_by_faculty(chunks)
    assert len(blocks) == 1, f"corrupt prefix split into {len(blocks)} blocks"
    assert blocks[0]["faculty"] == "Emrah Gecili"

if __name__ == "__main__":
    failures = 0
    tests = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_") and callable(f)]
    for name, fn in tests:
        try:
            fn()
            print(f"PASS  {name}")
        except AssertionError as exc:
            failures += 1
            print(f"FAIL  {name}: {exc}")
        except Exception as exc:  # noqa: BLE001
            failures += 1
            print(f"ERROR {name}: {type(exc).__name__}: {exc}")
    print(f"\n{len(tests) - failures}/{len(tests)} passed")
    sys.exit(1 if failures else 0)
