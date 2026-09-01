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


def _env_seed(name: str, default: int | None) -> int | None:
    """Empty string means send no seed at all."""
    raw = _env(name, "" if default is None else str(default))
    if raw == "":
        return None
    try:
        return int(raw)
    except ValueError:
        return default


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

    # ---- Index names ----
    # text_embeddings is a 1536 dimension cosine vector index on Chunk.embedding.
    vector_index: str = field(default_factory=lambda: _env("NEO4J_VECTOR_INDEX", "text_embeddings"))
    # text_embeddings2 is a fulltext index over Chunk.embedding, a float array,
    # so keyword searching it contributes nothing and the keyword half of the
    # hybrid search is effectively inert.
    #
    # That is deliberate. The graph also ships chunk_text_fulltext over
    # Chunk.text, which is the index a hybrid search actually wants, and pointing
    # here at it measurably changes which chunks come back and therefore which
    # faculty an open ended question returns. The baseline app runs on
    # text_embeddings2, so parity requires running on it too. Set
    # NEO4J_FULLTEXT_INDEX=chunk_text_fulltext to opt back into the better index.
    fulltext_index: str = field(
        default_factory=lambda: _env("NEO4J_FULLTEXT_INDEX", "text_embeddings2")
    )

    # ---- OpenAI ----
    openai_api_key: str = field(default_factory=lambda: _env("OPENAI_API_KEY"))
    # gpt-5-mini, matching the baseline app, which hardcodes it.
    #
    # Worth knowing what this costs: gpt-5-mini is a reasoning model and REJECTS
    # temperature. The API answers "Unsupported value: 'temperature' does not
    # support 0 with this model. Only the default (1) value is supported." Locked
    # at 1, the same judge prompt over the same CV text scores differently on
    # every run (measured: 60, 60, 50, 40, 60), and the cutoffs in pipeline.py
    # are sharp, so a faculty member can move in and out of the answer between
    # two identical requests. Answers are therefore NOT reproducible, and no
    # amount of code alignment changes that.
    #
    # Switching to gpt-4o would allow temperature=0 and make the faculty
    # selection reproducible, but it is a different model with different
    # judgement, it wraps its JSON in ```json fences (which the baseline's strict
    # parser discards), and even at temperature=0 long generations still diverge.
    # It also would not match the baseline, which is the point of this build.
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
    # Second, wider retrieval pass used only when the first pass produces no
    # ranked faculty at all. From Ankita's updated llm_utils.py.
    expanded_top_k: int = field(default_factory=lambda: _env_int("EXPANDED_TOP_K", 400))
    # Fixed seed on every pipeline call. gpt-5-mini rejects temperature=0, so a
    # seed is the only reproducibility lever the API offers here, and it is
    # best effort rather than a guarantee. Set LLM_SEED= (empty) to send none.
    llm_seed: int | None = field(default_factory=lambda: _env_seed("LLM_SEED", 42))
    # Ceiling on how many faculty blocks get an LLM relevance judgement. 0 means
    # no ceiling, which is what the baseline app does: it judges every block the
    # retrieval produced. A cap is the single best cost control on an open ended
    # question, but capping changes which people are considered, so parity
    # requires leaving it off. Set MAX_JUDGED_FACULTY to a positive number to
    # bound the fan out again.
    max_judged_faculty: int = field(default_factory=lambda: _env_int("MAX_JUDGED_FACULTY", 0))
    # Union a per-faculty coverage query into the discovery retrieval, so every
    # faculty member is judged on every question.
    #
    # OFF by default, because it is a deliberate divergence from the baseline app
    # and the goal here is matching that app. Measured with it off, ranked
    # retrieval alone reaches only 9 of 20 faculty on "expertise in cystic
    # fibrosis" and 14 of 20 on "spatial methods" — the rest are never scored and
    # nothing in the answer says so. The baseline has exactly the same hole.
    #
    # Set COVERAGE_RETRIEVAL=1 to reach 20 of 20 on every question, accepting
    # that this app will then find faculty the baseline misses, and that each
    # question costs more because 20 people get judged instead of 9.
    coverage_retrieval: bool = field(
        default_factory=lambda: _env("COVERAGE_RETRIEVAL", "0").lower() in {"1", "true", "yes"}
    )
    # Matches the OpenAI SDK's own default, which is what the baseline app runs
    # on because it constructs AsyncOpenAI() with no arguments. A shorter timeout
    # turns a slow judge call into an error response rather than an answer, which
    # is a visible difference against the baseline.
    request_timeout_s: int = field(default_factory=lambda: _env_int("LLM_REQUEST_TIMEOUT", 600))
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
