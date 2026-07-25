"""
retrievers.py

Builds the neo4j_graphrag retrievers and normalises whatever they return into
one internal chunk shape: {"text": str, "source": str}.

Two corrections against the original app, both verified by inspecting the
restored graph rather than assumed:

1. The hybrid retriever was pairing the vector index with `text_embeddings2`,
   a fulltext index defined over `Chunk.embedding`. Keyword searching a float
   array cannot contribute anything, so the keyword half of that hybrid search
   was inert. The graph also ships `chunk_text_fulltext` over `Chunk.text`,
   which is the real partner index and is what gets used here.

2. Chunk nodes carry `source2`, `id2`, `index`, `text`, and `embedding`. There
   is no `source` property, so requesting one returned nulls. Only `source2` is
   requested now and it is normalised to `source` internally.

Chunk.source2 follows the convention '<Faculty Name>_<Category>', for example
'Rhonda Szczesniak_Publications', which is what maps a chunk back to a person.
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Any, Iterable

import neo4j
from neo4j_graphrag.embeddings.openai import OpenAIEmbeddings
from neo4j_graphrag.retrievers import HybridCypherRetriever, VectorRetriever
from neo4j_graphrag.types import RetrieverResultItem

from .db import get_driver
from .settings import settings

log = logging.getLogger(__name__)

# Expands each matching chunk out into its neighbourhood so the model sees the
# graph context around a hit, not just the isolated text. Carried over from the
# original app with the traversal left intact.
HYBRID_CONTEXT_QUERY = """
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
  [c IN chunks | c.text] AS chunk_texts,
  [c IN chunks | coalesce(c.source2, '')] AS chunk_sources,
  [
    r IN rels |
    coalesce(startNode(r).name, 'Unknown') + ' - ' + type(r) +
    '(' + coalesce(toString(r.details), '') + ')' + ' -> ' +
    coalesce(endNode(r).name, 'Unknown')
  ] AS relationship_texts
"""

_embedder: OpenAIEmbeddings | None = None


def get_embedder() -> OpenAIEmbeddings:
    global _embedder
    if _embedder is None:
        _embedder = OpenAIEmbeddings(
            model=settings.embedding_model,
            api_key=settings.openai_api_key,
        )
    return _embedder


def _hybrid_formatter(record: neo4j.Record) -> RetrieverResultItem:
    """
    Keep the record's real lists instead of letting the library stringify it.

    The default formatter renders the whole record with repr, which then has to
    be recovered with regexes that break as soon as a chunk of CV text contains
    a bracket or a quote. Reading the fields directly removes that whole class
    of bug.
    """
    return RetrieverResultItem(
        content="",
        metadata={
            "chunk_texts": list(record.get("chunk_texts") or []),
            "chunk_sources": list(record.get("chunk_sources") or []),
            "relationship_texts": list(record.get("relationship_texts") or []),
        },
    )


def _vector_formatter(record: neo4j.Record) -> RetrieverResultItem:
    node = record.get("node") or {}
    text = str(node.get("text") or "")
    return RetrieverResultItem(
        content=text,
        metadata={
            "text": text,
            "source2": node.get("source2") or "",
            "score": record.get("score"),
        },
    )


def build_retriever(mode: str):
    """mode is 'hybrid' or 'vector'."""
    driver = get_driver()
    embedder = get_embedder()

    if mode == "vector":
        return VectorRetriever(
            driver,
            index_name=settings.vector_index,
            embedder=embedder,
            return_properties=["text", "source2", "index"],
            result_formatter=_vector_formatter,
            neo4j_database=settings.neo4j_database,
        )

    return HybridCypherRetriever(
        driver,
        vector_index_name=settings.vector_index,
        fulltext_index_name=settings.fulltext_index,
        retrieval_query=HYBRID_CONTEXT_QUERY,
        embedder=embedder,
        result_formatter=_hybrid_formatter,
        neo4j_database=settings.neo4j_database,
    )


# ----------------------------------------------------------------------
# Result normalisation
# ----------------------------------------------------------------------

def normalise_items(items: Iterable[Any]) -> list[dict[str, str]]:
    """
    Flatten retriever results into [{"text", "source"}].

    Both retrievers are configured with result formatters that put the real
    record fields in `metadata`, so this reads structured data. The string
    parsing path below survives only as a fallback for a formatter that did not
    take effect.
    """
    chunks: list[dict[str, str]] = []

    for item in items:
        metadata = getattr(item, "metadata", None)
        if isinstance(metadata, dict) and ("chunk_texts" in metadata or "text" in metadata):
            chunks.extend(_from_mapping(metadata))
            continue

        content = getattr(item, "content", item)
        if isinstance(content, dict):
            chunks.extend(_from_mapping(content))
        elif isinstance(content, str) and content.strip():
            chunks.extend(_from_string(content))

    return dedupe(chunks)


def _from_mapping(mapping: dict[str, Any]) -> list[dict[str, str]]:
    texts = mapping.get("chunk_texts")
    if isinstance(texts, list):
        sources = mapping.get("chunk_sources") or []
        out = []
        for i, text in enumerate(texts):
            if not text:
                continue
            source = sources[i] if i < len(sources) else ""
            out.append({"text": str(text), "source": str(source or "")})
        return out

    text = mapping.get("text")
    if text:
        source = mapping.get("source2") or mapping.get("source") or ""
        return [{"text": str(text), "source": str(source)}]

    return []


_TEXT_RE = re.compile(r"'text':\s*'((?:[^'\\]|\\.)*)'")
_SOURCE_RE = re.compile(r"'source2?':\s*'((?:[^'\\]|\\.)*)'")
_LIST_RE = re.compile(r"chunk_texts=(\[.*?\])\s+chunk_sources=(\[.*?\])", re.DOTALL)


def _from_string(content: str) -> list[dict[str, str]]:
    """Fallback for retriever versions that stringify their records."""
    import ast

    if "chunk_texts=" in content:
        match = _LIST_RE.search(content)
        if match:
            try:
                texts = ast.literal_eval(match.group(1))
                sources = ast.literal_eval(match.group(2))
            except (ValueError, SyntaxError):
                return []
            out = []
            for i, text in enumerate(texts or []):
                if not text:
                    continue
                source = sources[i] if i < len(sources or []) else ""
                out.append({"text": str(text), "source": str(source or "")})
            return out
        return []

    try:
        parsed = ast.literal_eval(content)
        if isinstance(parsed, dict):
            return _from_mapping(parsed)
    except (ValueError, SyntaxError):
        pass

    text_match = _TEXT_RE.search(content)
    if text_match:
        source_match = _SOURCE_RE.search(content)
        return [
            {
                "text": text_match.group(1).strip(),
                "source": source_match.group(1).strip() if source_match else "",
            }
        ]
    return []


def dedupe(chunks: list[dict[str, str]]) -> list[dict[str, str]]:
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
# Coverage complete retrieval
# ----------------------------------------------------------------------

# Plain top_k retrieval gives whichever faculty happen to rank highest, which
# means a broad question can silently omit people. Measured on the live graph,
# "which faculty have expertise in machine learning" reached all 20, but "list
# all faculty members" reached only 17, so coverage depends on how the question
# happens to embed. That is not an acceptable basis for "which faculty ...".
#
# This takes the same single query embedding, pulls a wide candidate pool from
# the vector index, groups by faculty, and keeps each person's best passages. No
# extra embedding calls and no extra LLM calls, and every faculty member with any
# material is guaranteed a fair hearing.
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

    Returns the same {"text", "source"} shape as the other retrievers so the rest
    of the pipeline is unchanged.
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
                chunks.append({"text": str(text), "source": str((passage or {}).get("source") or "")})
        return chunks

    try:
        return dedupe(await asyncio.to_thread(_work))
    except Exception as exc:
        log.warning("Coverage retrieval failed, falling back to plain search: %s", exc)
        return []


def faculty_from_source(source: str) -> str:
    """'Rhonda Szczesniak_Publications' becomes 'Rhonda Szczesniak'."""
    return (source or "").split("_")[0].strip()


def source_matches_faculty(source: str, faculty_name: str) -> bool:
    """
    True when a chunk's source belongs to the given person.

    Prefers an exact match on the faculty portion of source2 and falls back to
    token overlap, which is what the original app relied on exclusively.
    """
    if not source:
        return False

    prefix = faculty_from_source(source).lower()
    target = faculty_name.strip().lower()
    if prefix and prefix == target:
        return True

    tokens = [t for t in re.split(r"[,\s_]+", target) if len(t) > 2]
    if not tokens:
        return False
    haystack = source.lower()
    return all(token in haystack for token in tokens) or (
        len(tokens) > 1 and tokens[-1] in haystack
    )
