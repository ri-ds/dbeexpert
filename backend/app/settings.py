"""
settings.py

All runtime configuration in one place, driven entirely by environment
variables so the same image runs locally and in production. Nothing here
reaches out to Neo4j or OpenAI; that happens in db.py and llm.py.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent


def _env(name: str, default: str = "") -> str:
    value = os.environ.get(name, default)
    return value.strip() if isinstance(value, str) else value


def _env_int(name: str, default: int) -> int:
    try:
        return int(_env(name, str(default)))
    except ValueError:
        return default


@dataclass(frozen=True)
class Settings:
    # ---- Service ----
    version: str = "1.0.0"
    api_prefix: str = "/api"

    # ---- Neo4j ----
    neo4j_uri: str = field(default_factory=lambda: _env("NEO4J_URI", "bolt://localhost:7687"))
    neo4j_username: str = field(default_factory=lambda: _env("NEO4J_USERNAME", "neo4j"))
    neo4j_password: str = field(default_factory=lambda: _env("NEO4J_PASSWORD", "dbepassword123"))
    neo4j_database: str = field(default_factory=lambda: _env("NEO4J_DATABASE", "neo4j"))

    # ---- Index names, verified against the restored dump ----
    # text_embeddings is a 1536 dimension cosine vector index on Chunk.embedding.
    vector_index: str = field(default_factory=lambda: _env("NEO4J_VECTOR_INDEX", "text_embeddings"))
    # The restored graph ships two fulltext indexes. text_embeddings2 is defined
    # over Chunk.embedding, which cannot be keyword searched in any useful way,
    # so the correct hybrid partner is chunk_text_fulltext over Chunk.text.
    fulltext_index: str = field(
        default_factory=lambda: _env("NEO4J_FULLTEXT_INDEX", "chunk_text_fulltext")
    )

    # ---- OpenAI ----
    openai_api_key: str = field(default_factory=lambda: _env("OPENAI_API_KEY"))
    chat_model: str = field(default_factory=lambda: _env("OPENAI_CHAT_MODEL", "gpt-5-mini"))
    # Naming a conversation is a four word job, so it gets its own cheap model.
    # Deliberately not a reasoning model: those bill hidden reasoning tokens that
    # a small max_completion_tokens cap cannot bound, which would cost more for a
    # title than for some real answers.
    title_model: str = field(default_factory=lambda: _env("OPENAI_TITLE_MODEL", "gpt-4o"))
    # MUST match the model that wrote Chunk.embedding in the graph, which is
    # text-embedding-ada-002. That is the neo4j_graphrag default, and the original
    # app never passed a model, so the stored vectors are ada-002 vectors.
    #
    # Do not "upgrade" this. text-embedding-3-small is also 1536 dimensions, so the
    # startup width check still passes, but it is a different embedding space:
    # measured on this graph, top cosine similarity collapses from 0.92 to 0.52 and
    # the faculty ranking changes. Changing this safely means re-embedding all
    # 8,375 Chunk nodes.
    embedding_model: str = field(
        default_factory=lambda: _env("OPENAI_EMBEDDING_MODEL", "text-embedding-ada-002")
    )
    # The stored vectors are 1536 dimensions. A query embedding of any other
    # width will silently return meaningless neighbours, so this is asserted
    # at startup rather than trusted.
    embedding_dimensions: int = field(
        default_factory=lambda: _env_int("OPENAI_EMBEDDING_DIMENSIONS", 1536)
    )

    # ---- Pipeline tuning ----
    max_concurrency: int = field(default_factory=lambda: _env_int("PIPELINE_MAX_CONCURRENCY", 10))
    retrieval_top_k: int = field(default_factory=lambda: _env_int("RETRIEVAL_TOP_K", 100))
    # Hard ceiling on how many faculty blocks get an LLM relevance judgement,
    # which is the dominant cost driver on open ended questions.
    max_judged_faculty: int = field(default_factory=lambda: _env_int("MAX_JUDGED_FACULTY", 24))
    request_timeout_s: int = field(default_factory=lambda: _env_int("LLM_REQUEST_TIMEOUT", 120))
    # Server side ceiling on a generated Cypher transaction. Generated queries
    # are not reviewed queries, so an expensive variable length pattern must not
    # be able to pin the database indefinitely.
    cypher_timeout_s: int = field(default_factory=lambda: _env_int("CYPHER_TIMEOUT_SECONDS", 30))

    # ---- CORS, used only for local frontend dev; in Docker nginx proxies /api ----
    cors_origins: tuple[str, ...] = field(
        default_factory=lambda: tuple(
            o.strip()
            for o in _env(
                "CORS_ORIGINS",
                "http://localhost:5173,http://127.0.0.1:5173,http://localhost:3000",
            ).split(",")
            if o.strip()
        )
    )

    # ---- Session state ----
    session_ttl_s: int = field(default_factory=lambda: _env_int("SESSION_TTL_SECONDS", 60 * 60 * 6))

    # ---- Identity ----
    # Header the reverse proxy uses to tell us who the signed in user is. The SSO
    # layer in front authenticates every request but does not currently forward
    # the user, so until IT add this the app runs anonymously exactly as before.
    # Several common alternatives are also accepted, see identity.py.
    auth_user_header: str = field(
        default_factory=lambda: _env("AUTH_USER_HEADER", "X-Forwarded-User")
    )
    # Where the Sign out button sends the user. Empty by default and the button is
    # hidden until it is set, because a logout link that 404s is worse than none.
    #
    # Deliberately not guessed: the CCHMC service provider does not expose logout
    # at any of the usual SimpleSAMLphp paths. Every candidate returned 404 while
    # the auth endpoint returned 303, so the real URL has to come from IT. Note
    # that SAML single logout is often left disabled on purpose, since it can sign
    # the user out of every other application too.
    auth_logout_url: str = field(default_factory=lambda: _env("AUTH_LOGOUT_URL", ""))

    # ---- Feedback storage ----
    # Postgres, kept separate from Neo4j because the graph is rebuilt from a dump
    # and user submitted data must never sit somewhere a restore could overwrite.
    database_url: str = field(
        default_factory=lambda: _env(
            "DATABASE_URL",
            "postgresql+psycopg://dbe:dbefeedback@postgres:5432/dbefeedback",
        )
    )
    # Temporary gate on the admin feedback view. This is a placeholder until
    # CCHMC SSO lands, at which point the admin view should be behind a group
    # claim rather than a shared password.
    admin_password: str = field(default_factory=lambda: _env("ADMIN_PASSWORD", "admin123"))

    # ---- Paths ----
    ontology_dir: Path = field(
        default_factory=lambda: Path(_env("ONTOLOGY_DIR", str(BASE_DIR / "ontology")))
    )
    names_csv: Path = field(
        default_factory=lambda: Path(_env("NAMES_CSV", str(BASE_DIR / "data" / "names.csv")))
    )

    @property
    def openai_configured(self) -> bool:
        return bool(self.openai_api_key)


settings = Settings()
