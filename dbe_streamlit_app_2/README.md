# DBE Faculty Expertise — Streamlit App

Streamlit front-end for the notebook pipeline. An LLM router picks **one**
ontology agent per question, and that agent runs the
`classify → judge → rank → extract` pipeline over your Neo4j graph.

## Files

| File | Role |
| --- | --- |
| `app.py` | Streamlit UI (chat, retriever selector, feedback form) |
| `config.py` | Env, Neo4j driver, LLM/embeddings, index creation, paths, faculty list |
| `ontology_utils.py` | TTL → dict schema helpers |
| `schema_loader.py` | Builds all agent schemas from the `.ttl` files |
| `retrievers.py` | Hybrid + Vector retrievers |
| `llm_utils.py` | The pipeline (classify / judge / rank / extract / run_query) |
| `agents.py` | `OntologyAgent` + `GraphRAGOrchestrator` (LLM agent selection) |
| `ontology/` | The `.ttl` ontology files |
| `names.csv` | Allowed faculty list (first column) |

## Retriever options

- **Hybrid** — option 1 — `HybridCypherRetriever` (graph-expanded context)
- **Vector** — option 2 — `VectorRetriever` (plain chunk text)

## Setup

```bash
pip install -r requirements.txt
cp .env.example .env   # then edit with your OpenAI key + Neo4j creds
```

Make sure Neo4j is running with the APOC plugin and your graph loaded
(`Chunk` nodes with `text`, `source`/`source2`, and `embedding`).

## Run

```bash
streamlit run app.py
```

## Notes

- Agent selection is **domain routing**: all agents share one retriever, so
  the pipeline (retrieval + judging + extraction) is common — the selected
  agent's schema drives the router's pick. This matches the original app.
- Follow-up questions ("their education", "the first two") reuse
  `previous_faculty.json`; named questions ("expertise of Maurizio and
  Mekibib") overwrite it. These files are written in the working directory.
- If the Vector path returns "No CV chunks retrieved", confirm your `Chunk`
  nodes actually carry a `source`/`source2` property containing the person's
  name; adjust `return_properties` in `retrievers.py` if they use another key.
