# DBE Faculty Expertise Explorer

A full stack GraphRAG application over the Cincinnati Children's Division of
Biostatistics and Epidemiology faculty knowledge graph. Ask questions in plain
English, get answers grounded in the graph.

```
Browser  ->  nginx (React SPA)  ->  FastAPI  ->  Neo4j  (43,915 nodes / 83,349 relationships)
                                        |
                                        +------->  OpenAI  (embeddings, reasoning, Cypher generation)
```

## Quick start

Everything runs in Docker. You need Docker with at least 6 GB of memory
available, and an OpenAI API key.

```bash
cp .env.example .env    # then put your real OPENAI_API_KEY in it
```

Place the Neo4j dump at `dump/neo4j.dump`, then:

```bash
docker compose up -d --build
```

First start restores the 163 MB dump, which takes a minute or two. Watch it with
`docker compose logs -f neo4j-load neo4j`. When the stack is healthy:

| Service | URL |
| --- | --- |
| Web app | http://localhost:8080 |
| API docs | http://localhost:8011/api/docs |
| Neo4j Browser | http://localhost:7474 |

## How a question is routed

This is the most important thing to understand about the system, and it was the
source of a serious bug worth recording.

Two axes are classified independently in a single model call:

**Conversational type** resolves references: `named` (a person is mentioned),
`followup` (refers back to the previous answer), `first` (fresh).

**Intent** decides where the answer comes from:

| Intent | Meaning | Route |
| --- | --- | --- |
| `roster` | Who the faculty are, no subject criterion | Hand written graph query |
| `factual` | A specific fact, count, total, or ranking | Graph skill, else generated Cypher |
| `expertise` | Who has a track record in some subject | Semantic retrieval and judging |

The axes are orthogonal. "How many grants does Cole Brokamp have" is `named` and
`factual` at once.

**Intent routing overrides the retrieval mode selector.** The vector and hybrid
toggle chooses *how semantic search works*, never *whether* a question with an
exact answer gets handled by similarity scoring.

Before any model call, `skills.match_skill` also does a high precision pattern
check, so the most common factual phrasings are guaranteed to route correctly
even if the classifier misbehaves. That matcher is deliberately conservative:
anything with a subject filter falls through to the model. "List all faculty" is
a roster question; "list the faculty who study asthma" is not, and answering the
second with the full roster would be a confident wrong answer.

### The bug this fixed

Every question in vector and hybrid mode used to be forced through a
*score each candidate against a criterion* pipeline. That shape is right for
"who works on X" and wrong in kind for "list the roster", because a roster is not
a similarity judgement.

Asked "what are the 20 faculty names", the old pipeline embedded the question,
pulled the 100 most similar CV passages, and found only **17 of 20** faculty in
them (coverage depends on how the question happens to embed). It then asked the
relevance judge whether each person "satisfies" the question, which is a category
error: the judge invented a criterion and reasoned about whether someone "fits the
category of a faculty name". Arbitrary scores followed, the adaptive cutoff cut 17
to 2, extraction found nothing extractable because the thing asked for was a
*name* rather than a fact inside CV prose, and the answer arrived empty after
roughly 20 OpenAI calls and 26 seconds. The judge's scoring prose was rendered as
the answer, which is where the blurbs came from.

The same question now returns all 20 names in about **10 milliseconds with zero
model calls**.

## The three query modes

| Mode | What it does | Good for |
| --- | --- | --- |
| **Hybrid graph search** | Vector plus keyword search over CV passages, expanded through the surrounding graph, then an LLM judges each candidate, ranks them, and extracts evidence. | Open ended expertise questions. This is the default. |
| **Vector search** | Pure semantic similarity, no graph expansion. | Faster, narrower lookups. |
| **Natural language to Cypher** | Translates the question into a read only Cypher query, runs it, and explains the rows. Shows you the generated Cypher. | Counting, ranking, and structural questions. |

The hybrid and vector modes classify every question first:

- **named**: a specific faculty member is mentioned, so extraction runs directly.
- **followup**: no name, but the question refers back ("their education", "the
  first two"), so the previous result set for that session is reused.
- **first**: an open question, so every candidate is judged and ranked with an
  adaptive score cutoff.

## Architecture

```
backend/app/
  main.py         FastAPI routes, Server Sent Events streaming
  settings.py     all configuration, environment driven
  db.py           Neo4j driver, read only query helpers, generated Cypher guard
  llm.py          OpenAI client, concurrency gate, tolerant JSON parsing
  retrievers.py   hybrid and vector retrievers, result normalisation
  pipeline.py     classify, route, retrieve, judge, rank, extract, text to Cypher
  skills.py       hand written graph queries for factual questions
  ontology.py     Turtle files to per agent schemas for domain routing
  faculty.py      the 20 name allow list
  session.py      per conversation state with TTL eviction
  schemas.py      request and response models, mirrors frontend types.ts

frontend/src/
  App.tsx                  layout and query orchestration
  api.ts                   fetch wrappers and the SSE stream client
  hooks/useConversations.ts chat history, persisted to localStorage
  components/InfoDialog     graph stats, mode explanations, faculty list
  components/ModeToggle     retrieval mode control, below the send button
  components/              chat thread, faculty cards, Cypher block, pipeline trace
```

The sidebar holds only New Chat, the conversation history, and the appearance
control. Reference material lives in the Info dialog, and the retrieval mode
control sits under the composer. Conversation ids double as backend session ids,
so reopening a past conversation restores its follow up context for as long as
the server side session lives. Streaming state is never persisted, so a reloaded
conversation cannot show a stuck spinner.

### API

| Endpoint | Purpose |
| --- | --- |
| `GET /api/health` | Neo4j connectivity, node counts, OpenAI configuration |
| `GET /api/meta` | Faculty list, modes, agents, document categories, graph stats |
| `POST /api/query` | Run one question, return the full result |
| `POST /api/query/stream` | Same, as Server Sent Events with live stage progress |
| `POST /api/session/reset` | Clear follow up state for a session |

The streaming endpoint is what drives the live progress display in the UI. It
emits `stage`, `trace`, `result`, `error`, and `done` events, so a 25 second
query shows what it is doing rather than an opaque spinner.

## The graph

Built with the `neo4j-graphrag` KG builder from faculty CVs, publication lists,
and abstracts.

| | |
| --- | --- |
| Nodes | 43,915 |
| Relationships | 83,349 |
| `Chunk` nodes | 8,375, each with a 1536 dimension embedding |
| Faculty covered | 20 |
| Source documents | 280 across 14 categories |

`Chunk` nodes carry `text`, `source2`, `index`, `id2`, and `embedding`.
`source2` follows the convention `<Faculty Name>_<Category>`, for example
`Rhonda Szczesniak_Publications`, and that prefix is what maps a passage back to
a person. Categories are Abstracts, Publications, Contracts, Mentoring,
Leadership, Biography, Honors, Education, Appointments, Service, Certification,
Data, Focus, and Effort.

Around the chunks sits a domain graph: `Person`, `Publication`, `Journal`,
`Conference`, `Grant`, `FundingAgency`, `ResearchArea`, `Method`, `Disease`,
`Gene`, `Course`, `Committee`, `AcademicAppointment`, and more, linked by
`collaboratesWith`, `uses_method`, `investigates`, `publishedInJournal`,
`hasGrant`, `fundedBy`, `teaches`, and others.

Indexes that matter:

- `text_embeddings`, a vector index on `Chunk.embedding`, 1536 dimensions, cosine.
- `chunk_text_fulltext`, a fulltext index on `Chunk.text`.
- `text_embeddings2`, a fulltext index on `Chunk.embedding`. This one is not
  useful, see the notes below.

## Neo4j licensing, please read

**The supplied dump uses Neo4j's block storage format, which only Enterprise
Edition can read.** Loading it into Community fails with
`Block format detected for database neo4j but unavailable in this edition`. The
dump also postdates 5.26, so it needs the 2025 or 2026 CalVer line. It was
verified to load on `neo4j:2026.06-enterprise`.

The compose file therefore uses an Enterprise image with
`NEO4J_ACCEPT_LICENSE_AGREEMENT=eval`, which is the 30 day evaluation licence.
Before hosting this anywhere real, pick one of these:

1. **Get an Enterprise licence.** Neo4j offers commercial and academic licences,
   and there is a free single instance development licence. Then set
   `NEO4J_ACCEPT_LICENSE_AGREEMENT=yes`.
2. **Convert the store to the `aligned` format so Community can run it.** This
   needs Enterprise tooling once, then never again:

   ```bash
   # Copy the block format database into an aligned format copy
   docker run --rm -e NEO4J_ACCEPT_LICENSE_AGREEMENT=eval \
     -v dbeexpert_neo4j_data:/data \
     neo4j:2026.06-enterprise \
     neo4j-admin database copy neo4j neo4jaligned --to-format=aligned

   # Dump the aligned copy, rename it, and load it into Community
   docker run --rm -e NEO4J_ACCEPT_LICENSE_AGREEMENT=eval \
     -v dbeexpert_neo4j_data:/data -v "$PWD/dump:/dumps" \
     neo4j:2026.06-enterprise \
     neo4j-admin database dump neo4jaligned --to-path=/dumps
   ```

   Then point `NEO4J_IMAGE_TAG` at a Community tag. Recreate the vector and
   fulltext indexes afterwards if the copy drops them; vector indexes populate
   themselves from the existing `embedding` properties.
3. **Use Neo4j Aura.** Aura is Enterprise backed, so it accepts the dump as is.
   At 43,915 nodes and 83,349 relationships this graph fits inside the Aura Free
   tier limits of 200,000 nodes and 400,000 relationships. Import the dump
   through the Aura console, then set `NEO4J_URI` to the `neo4j+s://` connection
   string and remove the `neo4j` and `neo4j-load` services from compose.

## Configuration

Everything lives in `.env`. The values worth knowing about:

| Variable | Default | Notes |
| --- | --- | --- |
| `OPENAI_API_KEY` | none | Required. The backend refuses queries without it. |
| `OPENAI_CHAT_MODEL` | `gpt-5-mini` | Used for classification, judging, extraction, and Cypher generation. |
| `OPENAI_EMBEDDING_MODEL` | `text-embedding-3-small` | Must produce 1536 dimensions to match the stored vectors. |
| `MAX_JUDGED_FACULTY` | 24 | The main cost control. See below. |
| `RETRIEVAL_TOP_K` | 100 | Chunks pulled per retrieval pass. |
| `PIPELINE_MAX_CONCURRENCY` | 10 | Concurrent OpenAI calls. |
| `NEO4J_FULLTEXT_INDEX` | `chunk_text_fulltext` | See the fixes below. |

### Retrieval is coverage complete

Discovery questions run two retrievals and union the results, because they do
different jobs. The selected retriever goes **deep**, returning many passages for
whoever ranks highest, which is what lets a strong candidate accumulate enough
evidence to score well. A second query goes **wide**, reusing the same single
embedding to pull each faculty member's best passages from a large candidate pool,
so everyone with material gets a fair hearing.

Coverage alone was tried and is measurably worse. Capping each person at a handful
of passages starves the genuinely strong candidates and the relevance scores
collapse: "which faculty have expertise in cystic fibrosis" fell from three well
evidenced matches to one. Depth plus breadth is what works.

Faculty evidence blocks are capped at `BLOCK_CHAR_BUDGET` characters, since
unioning grows them and these prompts were previously unbounded.

### Internal reasoning never reaches the answer

The relevance judge produces a prose rationale for each candidate. That is a
scoring artefact, not an answer, and it used to be rendered directly under each
faculty name. It now travels in `trace.judgements` and surfaces only inside the
collapsed pipeline disclosure, alongside the routing decision, coverage note, and
any candidate that passed scoring but yielded no extractable evidence.

That last list matters: candidates that pass the cutoff but extract nothing used
to vanish silently, so "kept 2" could present as zero results with no explanation.

### Cost and latency

Cost depends entirely on which route a question takes.

| Question | Model calls | Latency |
| --- | --- | --- |
| Roster or factual, matched by pattern | **0** | about 10 ms |
| Factual, matched by the classifier | 1, plus prose for some skills | under 1 s to 5 s |
| Named or follow up | 2 plus one extraction per person | a few seconds |
| Open ended expertise | roughly `2 + judged + kept`, about 26 with 20 faculty | 20 to 30 s |

Only the last row is expensive, and it is expensive because it genuinely reasons
over every faculty member's evidence. Turn `MAX_JUDGED_FACULTY` down to trade
recall for cost and speed.

## What changed from the original Streamlit app

The original `dbe_streamlit_app_2/` is still in the repo for reference. The
pipeline logic carried over nearly intact. These are the substantive changes,
each found by inspecting the restored graph rather than by reading the code:

1. **The hybrid search keyword half was inert.** It paired the vector index with
   `text_embeddings2`, a fulltext index built over `Chunk.embedding`. Keyword
   searching a float array contributes nothing. The graph also ships
   `chunk_text_fulltext` over `Chunk.text`, which is the real partner index, and
   that is what the backend uses now.
2. **`Chunk.source` does not exist.** The retriever asked for `text`, `source`,
   and `source2`, and only `source2` is a real property, so `source` came back
   null everywhere. Only `source2` is requested now.
3. **Retriever output is parsed structurally.** The original recovered fields
   from the library's `repr` output with regexes, which breaks on CV text
   containing brackets or quotes. Both retrievers now use a `result_formatter`,
   so the record fields are read directly. `apoc.text.join` is gone from the
   retrieval query as a side effect, so APOC is no longer strictly required.
4. **Follow up state is per session.** It used to live in
   `previous_faculty.json` and `conversation_history.json` in the working
   directory, shared by every visitor. Two people using the app at once would
   overwrite each other's follow up context.
5. **The event loop is no longer blocked.** `retriever.search()` is synchronous
   and was being called from async functions, so one slow search stalled every
   other request. Those calls run on worker threads now.
6. **Ontologies are parsed independently.** A single rdflib `Graph` was reused
   across all eleven Turtle files, so each ontology accumulated every previous
   one and ten of the eleven agents advertised a blended vocabulary. Routing had
   almost nothing to discriminate on.
7. **JSON responses are enforced and parsed tolerantly.** Prompts that used a
   bare `NONE` string sentinel now return an explicit boolean so OpenAI JSON
   mode can be used, with fenced block and salvage handling as a fallback.
8. **Generated Cypher is guarded, in three layers.** Every generated query runs
   in a transaction opened with `READ_ACCESS`, which the server enforces, under
   a `CYPHER_TIMEOUT_SECONDS` transaction timeout so an expensive variable
   length pattern cannot pin the database, and behind a static check for write
   and administrative clauses. That static check takes two passes, because the
   obvious single regex has a false positive that matters here: `GRANT` is an
   administrative keyword, but `Grant` is also one of the most important node
   labels in this graph, so `MATCH (g:Grant)` has to be allowed. Literals and
   comments are stripped first, administrative commands are matched with
   `GRANT`, `REVOKE`, and `DENY` anchored to the start of a statement, then
   label and property references are stripped before the write clause pass so no
   identifier can collide with a keyword.
9. **Judging is capped** by `MAX_JUDGED_FACULTY`, since unbounded fan out was
   the dominant cost.

## Development without Docker

Backend:

```bash
python3 -m venv .venv && .venv/bin/pip install -r backend/requirements.txt
set -a && . ./.env && set +a
NEO4J_URI=bolt://localhost:7687 .venv/bin/python -m uvicorn app.main:app --reload --app-dir backend
```

Frontend:

```bash
cd frontend && npm install && npm run dev
```

The Vite dev server proxies `/api` to `http://localhost:8000`, so the same
relative paths work in development and production.

## Known gaps

- **Person nodes are not deduplicated.** The same human appears as
  `Rhonda Szczesniak`, `Szczesniak R`, `Szczesniak, R.`, and
  `Rhonda D Szczesniak`. There are 6,402 `Person` nodes for a graph covering 20
  faculty and their co-authors. Any structural query over `Person` should match
  with a case insensitive `CONTAINS` on a surname, never equality on a full
  name, and the Cypher generation prompt says so. Entity resolution would make
  the graph far more queryable and is the single highest value improvement left.
- **Generated Cypher is syntactically safe but not semantically guaranteed.**
  Read this before trusting a number from the Cypher mode. The query is checked
  for write clauses and capped by a transaction timeout, so it cannot damage or
  pin the database, but nothing verifies that it answers the question the way
  you meant. A real example from testing: asked for publication counts per
  faculty member, the model generated a `[*1..4]` variable length traversal and
  returned figures in the low thousands per person, which counts every
  `Publication` node reachable within four hops rather than that person's own
  publications. The query is displayed in the UI precisely so this is visible.
  Treat the Cypher mode as a fast way to interrogate the graph whose work you
  can check, not as an authoritative reporting tool.
- **Session state is in process.** Fine for one backend container, but scaling to
  multiple replicas needs Redis behind the same `SessionStore` interface.
- **No authentication.** Anyone who can reach the frontend can spend your OpenAI
  budget. Put it behind SSO or a reverse proxy with auth before exposing it.
- **Graph relationship text is retrieved but not fed to the model.** The hybrid
  query renders edges as `A - REL(details) -> B` strings. They are not used for
  per faculty attribution because an expanded neighbourhood can cross between
  people, and a misattributed claim in a research tool is worse than a missing
  one. The expanded chunks themselves are correctly attributed and do get used.
- **The feedback form is gone.** The original posted to a hardcoded Google Form.
  That was not carried over.
