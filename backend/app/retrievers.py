"""
retrievers.py

Builds the neo4j_graphrag retrievers and normalises whatever they return into
one internal chunk shape: {"text": str, "source": str}.

This is a faithful port of the baseline app's retrieval layer. Three things in
here are known to be weaker than what they replaced, and all three are
deliberate, because each one changes which chunks reach the relevance judge and
therefore which faculty come back:

1. The hybrid retriever pairs the vector index with a fulltext index over
   `Chunk.embedding` (see settings.fulltext_index). Keyword searching a float
   array contributes nothing, so the keyword half of the hybrid search is inert.

2. Results are parsed out of the retriever's stringified record with regexes
   rather than read from the record's real fields. Any chunk of CV text
   containing a quote or bracket can defeat that parse and be dropped silently.

3. `source_matches_faculty` matches on ANY name token, so a chunk belonging to
   one person can be attributed to another who shares a first or last name.

Chunk.source2 follows the convention '<Faculty Name>_<Category>', for example
'Rhonda Szczesniak_Publications', which is what maps a chunk back to a person.
"""

from __future__ import annotations

import ast
import asyncio
import logging
import re
from typing import Any, Iterable

from neo4j_graphrag.embeddings.openai import OpenAIEmbeddings
from neo4j_graphrag.retrievers import HybridCypherRetriever, VectorRetriever

from .db import get_driver
from .settings import settings

log = logging.getLogger(__name__)

# Expands each matching chunk out into its neighbourhood so the model sees the
# graph context around a hit, not just the isolated text.
#
# Carried over verbatim, including the apoc.text.join calls. Returning real
# lists instead would be cleaner, but the parser below reads the stringified
# record and splits on the '\n---\n' separator these joins produce, and the two
# have to agree.
HYBRID_CONTEXT_QUERY = """
// 0) Start with top matching chunk nodes
WITH node AS chunk

OPTIONAL MATCH (chunk)<-[r1]-(n1)
WHERE type(r1) <> 'FROM_CHUNK'
OPTIONAL MATCH (n1)-[r2]-(n2)
WHERE type(r2) <> 'FROM_CHUNK'

WITH
  collect(DISTINCT chunk) AS chunk_nodes,
  collect(DISTINCT n1) AS n1_nodes,
  collect(DISTINCT n2) AS n2_nodes,
  collect(DISTINCT r1) AS r1_rels,
  collect(DISTINCT r2) AS r2_rels

WITH chunk_nodes + n1_nodes + n2_nodes AS all_nodes,
     r1_rels + r2_rels AS base_rels
UNWIND all_nodes AS c

OPTIONAL MATCH (c)-[nr:NEXT_CHUNK]->(c2)

WITH
  collect(DISTINCT c) AS base_nodes,
  collect(DISTINCT c2) AS next_nodes,
  collect(DISTINCT nr) AS next_rels,
  base_rels

WITH base_nodes + next_nodes AS final_nodes,
     base_rels + next_rels AS final_rels

WITH [n IN final_nodes WHERE n.text IS NOT NULL | n] AS chunks, final_rels AS rels

RETURN
  apoc.text.join([c IN chunks | c.text], '\\n---\\n') AS chunk_texts,
  apoc.text.join([c IN chunks | coalesce(c.source2, c.source, '')], '\\n---\\n') AS chunk_sources,
  apoc.text.join([
    r IN rels |
    coalesce(startNode(r).name, 'Unknown') + ' - ' + type(r) +
    '(' + coalesce(r.details, '') + ')' + ' -> ' + coalesce(endNode(r).name, 'Unknown')
  ], '\\n---\\n') AS relationship_texts
"""

_embedder: OpenAIEmbeddings | None = None


def get_embedder() -> OpenAIEmbeddings:
    global _embedder
    if _embedder is None:
        # settings.embedding_model is text-embedding-ada-002, which is also the
        # neo4j_graphrag default the baseline app gets from a bare
        # OpenAIEmbeddings(). Named explicitly so a library default change
        # cannot silently move the query into a different vector space.
        _embedder = OpenAIEmbeddings(
            model=settings.embedding_model,
            api_key=settings.openai_api_key,
        )
    return _embedder


def build_retriever(mode: str):
    """mode is 'hybrid' or 'vector'."""
    driver = get_driver()
    embedder = get_embedder()

    # No result_formatter on either retriever. The library's default formatter
    # renders each record with repr, and the parser below is written against
    # that repr. Supplying a formatter that hands over the real fields would be
    # more robust and would recover chunks the regexes drop, which is exactly
    # the divergence being avoided here.
    if mode == "vector":
        return VectorRetriever(
            driver,
            index_name=settings.vector_index,
            embedder=embedder,
            return_properties=["text", "source", "source2"],
            neo4j_database=settings.neo4j_database,
        )

    return HybridCypherRetriever(
        driver,
        vector_index_name=settings.vector_index,
        fulltext_index_name=settings.fulltext_index,
        retrieval_query=HYBRID_CONTEXT_QUERY,
        embedder=embedder,
        neo4j_database=settings.neo4j_database,
    )


# ----------------------------------------------------------------------
# Result normalisation
# ----------------------------------------------------------------------

def normalise_items(items: Iterable[Any]) -> list[dict[str, str]]:
    """
    Flatten retriever results into [{"text", "source"}].

    Auto-detects hybrid versus vector from the content, so one parser serves
    both retrievers.

    Note that this does NOT deduplicate. The baseline app dedupes only on the
    multi-query follow-up path, so a chunk returned twice within a single search
    is counted twice when the per-faculty blocks are built. Deduping here would
    change the text those blocks contain.
    """
    chunks: list[dict[str, str]] = []

    for item in items:
        combined = getattr(item, "content", item)
        if not isinstance(combined, str):
            combined = str(combined)

        if "chunk_texts=" in combined:
            chunks.extend(_from_hybrid(combined))
        else:
            chunks.extend(_from_vector(combined))

    return chunks


def _from_hybrid(combined: str) -> list[dict[str, str]]:
    """
    Pull the joined text and source blocks out of a stringified hybrid record.

    The separator is the six literal characters \\n---\\n, not a real newline:
    the record's repr escapes the newlines apoc.text.join wrote, so the escaped
    form is what appears in this string.
    """
    match = re.search(r"chunk_texts=(.*?)(?=chunk_sources=)", combined, re.DOTALL)
    chunk_texts = match.group(1).strip() if match else ""

    match2 = re.search(r"chunk_sources=(.*?)(?=relationship_texts=)", combined, re.DOTALL)
    chunk_sources = match2.group(1).strip() if match2 else ""

    texts = [t for t in chunk_texts.split("\\n---\\n") if t.strip()]
    sources = [s for s in chunk_sources.split("\\n---\\n") if s.strip()]

    # zip stops at the shorter list, so a text with no matching source is
    # dropped rather than paired with the wrong person.
    return [{"text": t, "source": s} for t, s in zip(texts, sources)]


def _from_vector(content: Any) -> list[dict[str, str]]:
    """Parse one VectorRetriever item, which carries a single chunk."""
    parsed = None
    if isinstance(content, dict):
        parsed = content
    else:
        try:
            candidate = ast.literal_eval(content)
            if isinstance(candidate, dict):
                parsed = candidate
        except Exception:
            parsed = None

    if parsed is not None:
        text = str(parsed.get("text", "")).strip()
        source = str(parsed.get("source2") or parsed.get("source") or "").strip()
    else:
        text_match = re.search(r"'text':\s*'((?:[^'\\]|\\.)*)'", content)
        source_match = re.search(r"'source2?':\s*'((?:[^'\\]|\\.)*)'", content)
        text = text_match.group(1).strip() if text_match else ""
        source = source_match.group(1).strip() if source_match else ""

    if not text:
        return []
    return [{"text": text, "source": source}]


def dedupe(chunks: list[dict[str, str]]) -> list[dict[str, str]]:
    """
    Drop repeated (source, text) pairs.

    Applied only where the baseline applies it: across the several searches a
    follow-up question runs, never within a single search.
    """
    seen: set[tuple[str, str]] = set()
    out: list[dict[str, str]] = []
    for chunk in chunks:
        key = (chunk.get("source", ""), chunk.get("text", ""))
        if key in seen:
            continue
        seen.add(key)
        out.append(chunk)
    return out


# ----------------------------------------------------------------------
# Coverage retrieval
# ----------------------------------------------------------------------

# Plain top_k retrieval returns whichever passages rank highest, which means a
# broad question silently omits people. Measured on this graph:
#
#   "which faculty have expertise in cystic fibrosis"   9 of 20 reached
#   "spatial methods and environmental exposure"       14 of 20
#   "Bayesian adaptive clinical trial design"          19 of 20
#
# The 11 faculty missing from the first question were never scored at all, and
# nothing in the answer said so. That is not a ranking problem to be tuned, it is
# a coverage hole.
#
# This takes the same single query embedding, pulls a wide candidate pool from
# the vector index, groups by faculty, and keeps each person's best passages. No
# extra embedding call and no extra LLM call, so every faculty member is
# guaranteed a hearing for the cost of one more Cypher query.
#
# Note this is a deliberate divergence from the baseline app, which has the same
# coverage hole. It is here because not missing people matters more than matching
# ankitaexpert exactly.
_PER_FACULTY_QUERY = """
CALL db.index.vector.queryNodes($index, $pool, $vector) YIELD node, score
WHERE node.source2 IS NOT NULL AND node.text IS NOT NULL
WITH split(node.source2, '_')[0] AS faculty, node, score
ORDER BY score DESC
WITH faculty, collect({text: node.text, source: node.source2})[0..$per_faculty] AS passages
RETURN faculty, passages
"""


async def retrieve_per_faculty(
    query: str, per_faculty: int = 8, pool: int = 4000
) -> list[dict[str, str]]:
    """
    Retrieve the best passages for every faculty member for one query.

    Returns the same {"text", "source"} shape as the other retrievers, so the
    rest of the pipeline is unchanged. Failure is non-fatal: an empty list means
    the caller falls back to whatever the ranked search found.
    """
    from .db import run_read

    embedder = get_embedder()

    def _work() -> list[dict[str, str]]:
        vector = embedder.embed_query(query)
        rows = run_read(
            _PER_FACULTY_QUERY,
            {
                "index": settings.vector_index,
                "vector": vector,
                "pool": pool,
                "per_faculty": per_faculty,
            },
        )
        chunks: list[dict[str, str]] = []
        for row in rows:
            for passage in row.get("passages") or []:
                text = (passage or {}).get("text")
                if not text:
                    continue
                chunks.append(
                    {"text": str(text), "source": str((passage or {}).get("source") or "")}
                )
        return chunks

    try:
        return dedupe(await asyncio.to_thread(_work))
    except Exception as exc:
        log.warning("Coverage retrieval failed, falling back to plain search: %s", exc)
        return []


def faculty_from_source(source: str) -> str:
    """
    'Rhonda Szczesniak_Publications' becomes 'Rhonda Szczesniak'.

    Surrounding quotes are stripped too, because a few Chunk.source2 values in
    the graph carry a stray leading apostrophe. Without this, one such chunk
    forms its own block and the pipeline sees a 21st faculty member:

        504 chars,   1 chunk    "'Emrah Gecili"   <- corrupt duplicate
      40483 chars,  81 chunks   "Emrah Gecili"    <- the real one

    That phantom is scored as a real candidate: it wastes a model call, pushes
    the "assessing faculty relevance" count past the 20 faculty that exist, and
    can surface in an answer as a mis-spelled name. Folding it back onto the
    right person fixes all three.
    """
    return (source or "").split("_")[0].strip().strip("'\"").strip()


def source_matches_faculty(source: str, faculty_name: str) -> bool:
    """
    True when a chunk's source belongs to the given person.

    Matches if ANY name token longer than two characters appears anywhere in the
    source. That is loose enough to attribute one person's chunks to another who
    shares a first or last name, which inflates the evidence block and can move a
    relevance score.

    Requiring an exact source2 prefix fixes it, and is what this function did
    before. It is reverted because a changed evidence block changes the score,
    and a changed score can cross the 60/40 cutoffs and add or remove someone
    from the answer.
    """
    s = (source or "").lower()
    parts = [p for p in re.split(r"[,\s_]+", faculty_name.lower()) if len(p) > 2]
    return any(p in s for p in parts)
