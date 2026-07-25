"""
db.py

Neo4j driver lifecycle plus the read only query helpers the API exposes.

The neo4j_graphrag retrievers are synchronous, so they need a synchronous
driver. Every call that touches the driver from async code is pushed onto a
worker thread by the callers in pipeline.py so the event loop is never blocked.
"""

from __future__ import annotations

import logging
import re
from typing import Any

import neo4j
from neo4j import GraphDatabase

from .settings import settings

log = logging.getLogger(__name__)

_driver: neo4j.Driver | None = None

# Guarding generated Cypher happens in two passes, because the naive single
# regex has a false positive that matters a great deal in this graph: GRANT is
# an administrative privilege keyword, but `Grant` is also one of the most
# important node labels here, so `MATCH (g:Grant)` is entirely legitimate and
# must not be blocked.
#
# Pass one runs over the query with only string literals and comments removed,
# and catches administrative commands and write procedures. GRANT, REVOKE, and
# DENY are anchored to the start of a statement, which is the only place they
# are ever valid as privilege commands.
_ADMIN_PATTERNS = re.compile(
    r"(^|;)\s*(GRANT|REVOKE|DENY)\b"
    r"|\b(LOAD\s+CSV"
    r"|CREATE\s+(INDEX|CONSTRAINT|DATABASE|USER|ROLE|ALIAS)"
    r"|DROP\s+(INDEX|CONSTRAINT|DATABASE|USER|ROLE|ALIAS)"
    r"|ALTER\s+(USER|DATABASE)"
    r"|START\s+DATABASE|STOP\s+DATABASE"
    r"|dbms\.|db\.create|db\.index\.fulltext\.create"
    r"|apoc\.(create|merge|refactor|trigger|periodic|atomic|nodes\.link|schema\.assert))",
    re.IGNORECASE,
)

# Pass two runs over the same text with label references (:Label), relationship
# type references, and property access (.name) additionally removed, so a label
# or property that happens to share a name with a write clause cannot trip it.
_WRITE_CLAUSES = re.compile(
    r"\b(CREATE|MERGE|DELETE|DETACH|SET|REMOVE|DROP|FOREACH)\b",
    re.IGNORECASE,
)


class UnsafeCypherError(ValueError):
    """Raised when generated Cypher contains a write or admin operation."""


def get_driver() -> neo4j.Driver:
    global _driver
    if _driver is None:
        _driver = GraphDatabase.driver(
            settings.neo4j_uri,
            auth=(settings.neo4j_username, settings.neo4j_password),
            max_connection_lifetime=3600,
            max_connection_pool_size=32,
            connection_acquisition_timeout=60,
        )
    return _driver


def close_driver() -> None:
    global _driver
    if _driver is not None:
        _driver.close()
        _driver = None


def verify_connectivity() -> None:
    get_driver().verify_connectivity()


def run_read(cypher: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Run a read only query and return plain JSON friendly dicts."""
    driver = get_driver()
    with driver.session(database=settings.neo4j_database, default_access_mode=neo4j.READ_ACCESS) as session:
        result = session.run(cypher, params or {})
        return [_jsonify(record.data()) for record in result]


def run_generated_cypher(
    cypher: str,
    params: dict[str, Any] | None = None,
    row_limit: int = 200,
    timeout_s: int | None = None,
) -> tuple[list[str], list[dict[str, Any]]]:
    """
    Execute LLM generated Cypher after asserting it is read only.

    Returns (columns, rows). Two guardrails apply, because a generated query is
    not a reviewed one: rows are capped so a runaway result cannot flood the
    response, and the transaction carries a server side timeout so a careless
    variable length pattern such as [*1..6] cannot pin the database.
    """
    assert_read_only(cypher)
    limit = timeout_s if timeout_s is not None else settings.cypher_timeout_s
    driver = get_driver()
    with driver.session(
        database=settings.neo4j_database, default_access_mode=neo4j.READ_ACCESS
    ) as session:
        tx = session.begin_transaction(timeout=float(limit))
        try:
            result = tx.run(cypher, params or {})
            columns = list(result.keys())
            rows: list[dict[str, Any]] = []
            for record in result:
                if len(rows) >= row_limit:
                    break
                rows.append(_jsonify(record.data()))
            return columns, rows
        finally:
            # Read only work, so there is nothing to commit either way.
            tx.close()


def assert_read_only(cypher: str) -> None:
    """
    Reject generated Cypher that could mutate the graph or the DBMS.

    This is defence in depth rather than the only protection: every generated
    query also runs in a transaction opened with READ_ACCESS, which the server
    itself enforces. That means this check can afford to avoid false positives
    on legitimate reads instead of being maximally paranoid.
    """
    stripped = _strip_literals_and_comments(cypher)

    admin = _ADMIN_PATTERNS.search(stripped)
    if admin:
        raise UnsafeCypherError(
            f"Refusing to run generated Cypher because it contains an administrative "
            f"operation near '{admin.group(0).strip()}'. Only read queries are permitted."
        )

    write = _WRITE_CLAUSES.search(_strip_names(stripped))
    if write:
        raise UnsafeCypherError(
            f"Refusing to run generated Cypher because it contains a write clause "
            f"('{write.group(0)}'). Only read queries are permitted."
        )


def _strip_literals_and_comments(cypher: str) -> str:
    """
    Remove string literals and comments so a keyword inside quoted text does
    not trip the detectors, and so a comment cannot hide a write clause.
    """
    without_block = re.sub(r"/\*.*?\*/", " ", cypher, flags=re.DOTALL)
    without_line = re.sub(r"//[^\n]*", " ", without_block)
    without_double = re.sub(r'"(?:[^"\\]|\\.)*"', '""', without_line)
    without_single = re.sub(r"'(?:[^'\\]|\\.)*'", "''", without_double)
    return re.sub(r"`(?:[^`\\]|\\.)*`", "``", without_single)


def _strip_names(cypher: str) -> str:
    """
    Remove label references, relationship type references, and property access
    so an identifier that shares a name with a write clause cannot trip it.

    `MATCH (g:Grant)-[:hasGrant]->(x) RETURN x.set` becomes
    `MATCH (g)-[]->(x) RETURN x`, which contains no write clause. Applied only
    to the write pass, since the administrative pass needs the dotted procedure
    names such as apoc.periodic left intact.
    """
    # Label and relationship type references, including multi label a:B:C forms.
    without_labels = re.sub(r":\s*[A-Za-z_][A-Za-z0-9_]*", "", cypher)
    # Property access on a variable or a map key.
    return re.sub(r"\.\s*[A-Za-z_][A-Za-z0-9_]*", "", without_labels)


def _jsonify(value: Any) -> Any:
    """Convert Neo4j types into things json.dumps can handle."""
    if isinstance(value, dict):
        return {k: _jsonify(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonify(v) for v in value]
    if isinstance(value, neo4j.graph.Node):
        return {"_labels": sorted(value.labels), **{k: _jsonify(v) for k, v in value.items()}}
    if isinstance(value, neo4j.graph.Relationship):
        return {"_type": value.type, **{k: _jsonify(v) for k, v in value.items()}}
    if isinstance(value, neo4j.graph.Path):
        return {"_path_length": len(value)}
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


# ----------------------------------------------------------------------
# Introspection used by /api/health and /api/meta
# ----------------------------------------------------------------------

def graph_counts() -> dict[str, int]:
    rows = run_read(
        """
        CALL () { MATCH (n) RETURN count(n) AS nodes }
        CALL () { MATCH ()-[r]->() RETURN count(r) AS relationships }
        RETURN nodes, relationships
        """
    )
    if rows:
        return {"nodes": int(rows[0]["nodes"]), "relationships": int(rows[0]["relationships"])}
    return {"nodes": 0, "relationships": 0}


def label_counts(limit: int = 40) -> list[dict[str, Any]]:
    return run_read(
        """
        MATCH (n)
        UNWIND labels(n) AS label
        WITH label, count(*) AS count
        WHERE NOT label STARTS WITH '__'
        RETURN label, count
        ORDER BY count DESC
        LIMIT $limit
        """,
        {"limit": limit},
    )


def rel_type_counts(limit: int = 40) -> list[dict[str, Any]]:
    return run_read(
        """
        MATCH ()-[r]->()
        RETURN type(r) AS type, count(*) AS count
        ORDER BY count DESC
        LIMIT $limit
        """,
        {"limit": limit},
    )


def document_categories() -> list[str]:
    rows = run_read(
        """
        MATCH (c:Chunk)
        WHERE c.source2 IS NOT NULL
        WITH split(c.source2, '_') AS parts
        WITH parts[size(parts) - 1] AS category, count(*) AS count
        RETURN category
        ORDER BY count DESC
        """
    )
    return [r["category"] for r in rows if r.get("category")]


def faculty_from_graph() -> list[str]:
    """
    Faculty names as they actually appear in the graph, derived from the
    Chunk.source2 naming convention '<Faculty Name>_<Category>'.
    """
    rows = run_read(
        """
        MATCH (c:Chunk)
        WHERE c.source2 IS NOT NULL
        RETURN DISTINCT split(c.source2, '_')[0] AS faculty
        ORDER BY faculty
        """
    )
    return [r["faculty"] for r in rows if r.get("faculty")]


def vector_index_info() -> dict[str, Any] | None:
    rows = run_read(
        """
        SHOW INDEXES YIELD name, type, options
        WHERE name = $name
        RETURN name, options
        """,
        {"name": settings.vector_index},
    )
    if not rows:
        return None
    options = rows[0].get("options") or {}
    config = options.get("indexConfig", {}) if isinstance(options, dict) else {}
    return {
        "name": rows[0]["name"],
        "dimensions": config.get("vector.dimensions"),
        "similarity": config.get("vector.similarity_function"),
    }
