"""
config.py — environment, Neo4j driver, indexes, LLM/embeddings, paths.

Mirrors the notebook's setup cells (env, driver, gpt-5-mini LLM, embeddings,
vector + fulltext index creation) and centralizes the file paths the rest of
the app needs (ontology folder + faculty names CSV).
"""

import os

# Load .env if present (local dev convenience)
try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

# ------------------------------------------------------------------
# Paths (edit these or set the matching env vars)
# ------------------------------------------------------------------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ONTOLOGY_DIR = os.environ.get("ONTOLOGY_DIR", os.path.join(BASE_DIR, "ontology"))
NAMES_CSV = os.environ.get("NAMES_CSV", os.path.join(BASE_DIR, "names.csv"))

# ------------------------------------------------------------------
# Environment defaults (overridable via real env vars)
# ------------------------------------------------------------------
os.environ.setdefault("NEO4J_URI", "neo4j://127.0.0.1:7687")
os.environ.setdefault("NEO4J_USERNAME", "neo4j")
os.environ.setdefault("NEO4J_PASSWORD", "password4")
# OPENAI_API_KEY must be provided by the environment.

NEO4J_URI = os.environ["NEO4J_URI"]
NEO4J_USERNAME = os.environ["NEO4J_USERNAME"]
NEO4J_PASSWORD = os.environ["NEO4J_PASSWORD"]

# ------------------------------------------------------------------
# Neo4j driver
# ------------------------------------------------------------------
import neo4j
from neo4j import GraphDatabase

driver = neo4j.GraphDatabase.driver(
    NEO4J_URI, auth=(NEO4J_USERNAME, NEO4J_PASSWORD)
)

# ------------------------------------------------------------------
# LLM + embeddings  (as in the notebook)
# ------------------------------------------------------------------
from neo4j_graphrag.llm import OpenAILLM
from neo4j_graphrag.embeddings.openai import OpenAIEmbeddings

llm = OpenAILLM(
    model_name="gpt-5-mini",
    model_params={"response_format": {"type": "json_object"}},
)

embedding = OpenAIEmbeddings()

# ------------------------------------------------------------------
# Indexes (idempotent) — vector + fulltext, matching the notebook
# ------------------------------------------------------------------
def ensure_indexes():
    """Create the vector + fulltext indexes if they don't already exist."""
    try:
        from neo4j_graphrag.indexes import create_vector_index, create_fulltext_index
        try:
            create_vector_index(
                driver, name="text_embeddings", label="Chunk",
                embedding_property="embedding", dimensions=1536,
                similarity_fn="cosine",
            )
        except Exception:
            pass
        try:
            create_fulltext_index(
                driver, name="text_embeddings2", label="Chunk",
                node_properties=["embedding"], fail_if_exists=False,
            )
        except Exception:
            pass
    except Exception:
        # neo4j_graphrag not importable or DB unreachable — let callers surface it
        pass


# ------------------------------------------------------------------
# Faculty list helper
# ------------------------------------------------------------------
import pandas as pd

def load_faculty_names():
    """Return the list of allowed faculty names from the first CSV column."""
    if not NAMES_CSV or not os.path.exists(NAMES_CSV):
        return []
    try:
        df = pd.read_csv(NAMES_CSV)
        return df.iloc[:, 0].dropna().tolist()
    except Exception:
        return []

faculty_names = load_faculty_names()
faculty_list_text = ", ".join(faculty_names)
