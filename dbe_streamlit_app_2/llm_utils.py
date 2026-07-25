"""
llm_utils.py — the retrieval + reasoning pipeline (from the notebook).

classify_query -> (first | followup | named), then:
  * first    : judge every faculty block, rank, adaptive cutoff, extract
  * followup : reuse previous_faculty.json (optional positional subset)
  * named    : extract for the explicitly named faculty

extract_context_from_retriever auto-detects Hybrid vs Vector content, so the
same pipeline serves both retrievers.
"""

import openai
import re
import time
import asyncio
import json
import os
import ast

from config import faculty_names, faculty_list_text

# ===============================
# Global holders
#
# These two files hold the conversation/faculty state that classify_query
# routes on. If the notebook and the app use DIFFERENT files, the same
# question can be classified differently (discovery vs follow-up) and return
# different faculty. Point BOTH at the same absolute path so they share state.
# Set DBE_STATE_DIR to the same folder in the notebook and the app.
# ===============================
_STATE_DIR = os.environ.get("DBE_STATE_DIR", os.path.dirname(os.path.abspath(__file__)))
conversation_history_file = os.path.join(_STATE_DIR, "conversation_history.json")
faculty_history_file = os.path.join(_STATE_DIR, "previous_faculty.json")

# ===============================
# Concurrency Limit
#
# An asyncio.Semaphore binds to the event loop that first uses it. Streamlit
# runs each query on a fresh loop, so a single module-level semaphore would be
# "bound to a different event loop" on the 2nd question. Keep one semaphore per
# loop instead, created lazily for whichever loop is currently running.
# ===============================
MAX_CONCURRENCY = 10
_semaphores = {}

def get_semaphore():
    loop = asyncio.get_event_loop()
    sem = _semaphores.get(loop)
    if sem is None:
        sem = asyncio.Semaphore(MAX_CONCURRENCY)
        _semaphores[loop] = sem
    return sem

client = openai.AsyncOpenAI()

# ===============================
# Utility: Remove duplicates
# ===============================
def unique_preserve_order(items):
    seen = set()
    unique = []
    for i in items:
        name = i.strip().replace("'", "").replace('"', "")
        if name not in seen:
            seen.add(name)
            unique.append(name)
    return unique


# ===============================
# Utility: dedup retrieved chunks
# ===============================
def dedup_chunks(chunk_data):
    seen = set()
    out = []
    for c in chunk_data:
        key = (c["source"], c["text"])
        if key not in seen:
            seen.add(key)
            out.append(c)
    return out


# ===============================
# Utility: does a chunk's source belong to this faculty?
# ===============================
def source_matches_faculty(source, faculty_name):
    s = source.lower()
    parts = [p for p in re.split(r"[,\s_]+", faculty_name.lower()) if len(p) > 2]
    return any(p in s for p in parts)


# ===============================
# LLM ROUTER: classify the query as first / followup / named
# ===============================
async def classify_query(query):

    prompt = f"""
You are the router for a faculty-CV question-answering system.

Allowed Faculty:
{faculty_list_text}

User Question:
{query}

Classify the question into exactly one type:
- "named": it explicitly mentions one or more specific people who appear in the
  Allowed Faculty list (a first name, last name, or full name all count).
- "followup": it does NOT name anyone, but refers back to faculty already being
  discussed (e.g. "their education", "list them", "these people", "the first two").
- "first": a brand-new, open-ended question with no names and no back-reference
  (e.g. "list faculty with expertise in cystic fibrosis").

If the question refers to a POSITIONAL SUBSET of the previously discussed faculty
(e.g. "the first two", "last three", "top 5"), report it in "subset":
- position: "first", "last", or "all"
- count: the integer requested, or null when the question means everyone.
Use {{"position": "all", "count": null}} whenever no subset is implied.

Return ONLY JSON, no explanation:

{{
"type": "named" | "followup" | "first",
"faculty": ["<name copied EXACTLY from Allowed Faculty>", ...],
"subset": {{"position": "first" | "last" | "all", "count": <integer or null>}}
}}

Rules:
- Populate "faculty" ONLY for type "named"; otherwise use [].
- Every entry in "faculty" MUST be copied verbatim from the Allowed Faculty list.
- If a person is mentioned but is NOT in the Allowed Faculty list, omit them.
- Do NOT hallucinate names.
"""

    async with get_semaphore():
        response = await client.chat.completions.create(
            model="gpt-5-mini",
            messages=[
                {"role": "system", "content": "You are a strict JSON classifier. Return only JSON."},
                {"role": "user", "content": prompt}
            ]
        )

    answer = response.choices[0].message.content.strip()

    try:
        data = json.loads(answer)
    except:
        return {"type": "first", "faculty": [], "subset": {"position": "all", "count": None}}

    if data.get("type") not in {"named", "followup", "first"}:
        data["type"] = "first"

    allowed_lower = {n.lower(): n for n in faculty_names}
    data["faculty"] = unique_preserve_order([
        allowed_lower[f.lower()]
        for f in (data.get("faculty") or [])
        if f.lower() in allowed_lower
    ])

    subset = data.get("subset") or {}
    position = subset.get("position", "all")
    if position not in {"first", "last", "all"}:
        position = "all"
    count = subset.get("count")
    if not isinstance(count, int) or count <= 0:
        count = None
    data["subset"] = {"position": position, "count": count}

    return data


# ===============================
# Apply a positional subset to a faculty list
# ===============================
def apply_subset(faculty_list, subset):
    if not subset:
        return faculty_list
    position = subset.get("position", "all")
    count = subset.get("count")
    if position == "all" or not count:
        return faculty_list
    if position == "last":
        return faculty_list[-count:]
    return faculty_list[:count]


# ===============================
# Vector-item parser (VectorRetriever returns per-chunk text + source props)
# ===============================
def _extract_vector_item(content):
    d = None
    if isinstance(content, dict):
        d = content
    else:
        try:
            parsed = ast.literal_eval(content)
            if isinstance(parsed, dict):
                d = parsed
        except Exception:
            d = None

    if d is not None:
        text = str(d.get("text", "")).strip()
        source = str(d.get("source2") or d.get("source") or "").strip()
    else:
        cstr = content if isinstance(content, str) else str(content)
        tm = re.search(r"'text':\s*'((?:[^'\\]|\\.)*)'", cstr)
        sm = re.search(r"'source2?':\s*'((?:[^'\\]|\\.)*)'", cstr)
        text = tm.group(1).strip() if tm else ""
        source = sm.group(1).strip() if sm else ""

    if not text:
        return []
    return [{"text": text, "source": source}]


# ===============================
# Extract Retriever Context  (auto-detects Hybrid vs Vector)
# ===============================
def extract_context_from_retriever(retriever, query, top_k=100):

    results = retriever.search(query_text=query, top_k=top_k).items
    chunk_data = []

    for item in results:
        combined = item.content
        #combined_str = combined if isinstance(combined, str) else str(combined)

        if "chunk_texts=" in combined:
            # ---- Hybrid retriever (unchanged parsing) ----
            match = re.search(r"chunk_texts=(.*?)(?=chunk_sources=)", combined, re.DOTALL)
            chunk_texts = match.group(1).strip() if match else ""

            match2 = re.search(r"chunk_sources=(.*?)(?=relationship_texts=)", combined, re.DOTALL)
            chunk_sources = match2.group(1).strip() if match2 else ""

            texts = [t for t in chunk_texts.split("\\n---\\n") if t.strip()]
            sources = [s for s in chunk_sources.split("\\n---\\n") if s.strip()]

            for t, s in zip(texts, sources):
                chunk_data.append({"text": t, "source": s})
        else:
            # ---- Vector retriever ----
            chunk_data.extend(_extract_vector_item(combined))

    return chunk_data


# ===============================
# Retrieve for several queries, then dedup
# ===============================
def extract_context_multi(retriever, queries, top_k=100):
    all_chunks = []
    for q in queries:
        all_chunks.extend(extract_context_from_retriever(retriever, q, top_k=top_k))
    return dedup_chunks(all_chunks)


# ===============================
# Merge chunks by faculty
# ===============================
def merge_chunks_by_faculty(chunk_data):
    merged = {}
    for item in chunk_data:
        faculty_name = item['source'].split("_")[0].strip()
        if faculty_name not in merged:
            merged[faculty_name] = {
                "faculty": faculty_name,
                "text": item["text"],
                "source": item["source"]
            }
        else:
            merged[faculty_name]["text"] += "\n" + item["text"]
    return list(merged.values())


# ===============================
# Identify Relevant Faculty  (returns a relevance_score 0-100)
# ===============================
async def identify_relevant_faculty(block, question):

    prompt = f"""
You are a strict research evaluator.

Question:
{question}

Allowed Faculty:
{faculty_list_text}

Faculty Name:
{block['faculty']}

Faculty CV Content:
{block['text']}

Instructions:
- Determine if this faculty member explicitly satisfies the question.
- Use semantic reasoning.
- Do NOT rely on keyword matching.
- Do NOT hallucinate.
- Only use provided content.
- If the member satisfies the question, assign a relevance_score from 0 to 100
  reflecting HOW STRONGLY the CV evidence answers the question:
    90-100 = central, extensive, directly-on-topic body of work
    70-89  = clearly relevant with solid supporting evidence
    50-69  = relevant but limited or partial evidence
    30-49  = weak or peripheral relevance, but with some genuine supporting evidence
    below 30 = tangential or no real evidence - treat as NOT satisfying and return NONE
- Base the score only on the strength of evidence in the CV content above.

Return JSON:

{{
"faculty_name": "<name>",
"relevance_score": <integer 0-100>,
"Details": "<5-6 sentences explaining relevant experience>"
}}

If the faculty member does NOT satisfy the question return ONLY:
NONE
"""

    async with get_semaphore():
        response = await client.chat.completions.create(
            model="gpt-5-mini",
            messages=[
                {"role": "system", "content": "Return JSON or NONE."},
                {"role": "user", "content": prompt}
            ]
        )

    answer = response.choices[0].message.content.strip()
    if answer == "NONE":
        return None
    try:
        return json.loads(answer)
    except:
        return None


# ===============================
# Extract Faculty Information
# ===============================
async def extract_faculty_information(block, question, faculty_name):

    prompt = f"""
Extract the information requested.

Question:
{question}

Faculty:
{faculty_name}

Content:
{block['text']}

Return JSON:

{{
"faculty_name": "{faculty_name}",
"information": ["item1","item2","item3"]
}}

If none exists return NONE.
"""

    async with get_semaphore():
        response = await client.chat.completions.create(
            model="gpt-5-mini",
            messages=[
                {"role": "system", "content": "Return JSON or NONE"},
                {"role": "user", "content": prompt}
            ]
        )

    answer = response.choices[0].message.content.strip()
    if answer == "NONE":
        return None
    try:
        return json.loads(answer)
    except:
        return None


# ===============================
# Shared extractor: pull every chunk for a faculty (by source), combine, extract.
# ===============================
async def extract_for_faculty(chunk_data, question, faculty_name):
    fac_chunks = [c for c in chunk_data if source_matches_faculty(c["source"], faculty_name)]
    if not fac_chunks:
        print(f"[warn] No CV chunks retrieved for: {faculty_name}")
        return None
    block = {
        "faculty": faculty_name,
        "text": "\n".join(c["text"] for c in fac_chunks),
        "source": fac_chunks[0]["source"],
    }
    return await extract_faculty_information(block, question, faculty_name)


# ===============================
# Relevance helpers + adaptive cutoff
# ===============================
def _relevance_score(judge_output):
    s = judge_output.get("relevance_score", 0)
    try:
        return float(s)
    except (TypeError, ValueError):
        return 0.0

MANY_HIGH_SCORERS = 3
HIGH_SCORE = 70
STRICT_CUTOFF = 60
LENIENT_CUTOFF = 40
GAP_THRESHOLD = 18

def _truncate_at_gap(ranked):
    for i in range(1, len(ranked)):
        drop = _relevance_score(ranked[i - 1]) - _relevance_score(ranked[i])
        if drop >= GAP_THRESHOLD:
            print(f"(gap cut: {drop:.0f}-pt drop after "
                  f"{ranked[i - 1]['faculty_name']} -> dropping {len(ranked) - i} lower-scoring)")
            return ranked[:i]
    return ranked

def apply_relevance_cutoff(ranked):
    ranked = _truncate_at_gap(ranked)
    high = [f for f in ranked if _relevance_score(f) >= HIGH_SCORE]
    if len(high) >= MANY_HIGH_SCORERS:
        kept = [f for f in ranked if _relevance_score(f) > STRICT_CUTOFF]
        print(f"(strict cutoff: {len(high)} high scorers -> keeping score > {STRICT_CUTOFF})")
    else:
        kept = [f for f in ranked if _relevance_score(f) >= LENIENT_CUTOFF]
        print(f"(lenient cutoff: only {len(high)} high scorers -> keeping score >= {LENIENT_CUTOFF})")
    return kept


# ===============================
# CASE 1: open-ended discovery
# ===============================
async def run_first_query(query, retriever):
    chunk_data = extract_context_from_retriever(retriever, query)
    if not chunk_data:
        print("No context found.")
        return []

    faculty_blocks = merge_chunks_by_faculty(chunk_data)

    judge_tasks = [identify_relevant_faculty(block, query) for block in faculty_blocks]
    faculty_outputs = await asyncio.gather(*judge_tasks)

    ranked = [f for f in faculty_outputs if f]
    ranked.sort(key=_relevance_score, reverse=True)
    ranked = apply_relevance_cutoff(ranked)

    identified_faculty = unique_preserve_order([f["faculty_name"] for f in ranked])

    print("Identified faculty (most -> least relevant):")
    for f in ranked:
        print(f"  {int(_relevance_score(f)):>3}  {f['faculty_name']}")

    with open(faculty_history_file, "w") as f:
        json.dump(identified_faculty, f)

    extract_tasks = [extract_for_faculty(chunk_data, query, faculty) for faculty in identified_faculty]
    info_outputs = await asyncio.gather(*extract_tasks)
    results = [r for r in info_outputs if r]

    with open(conversation_history_file, "w") as f:
        json.dump([(query, results)], f)

    for item in results:
        print(f"\nfaculty_name: {item['faculty_name']}")
        print(f"information: {item['information']}")

    return results


# ===============================
# CASE 2 & 3: follow-up / named extraction (no relevance judge)
# ===============================
async def run_followup_query(query, retriever, faculty_list=None, subset=None):
    if faculty_list is None:
        if os.path.exists(faculty_history_file):
            with open(faculty_history_file, "r") as f:
                previous_faculty = json.load(f)
        else:
            previous_faculty = []
        if not previous_faculty:
            print("No previous faculty found.")
            return []
        faculty_list = apply_subset(previous_faculty, subset)
    else:
        with open(faculty_history_file, "w") as f:
            json.dump(faculty_list, f)

    chunk_data = extract_context_multi(retriever, [query] + faculty_list)
    if not chunk_data:
        print("No context found.")
        return []

    extract_tasks = [extract_for_faculty(chunk_data, query, faculty) for faculty in faculty_list]
    info_outputs = await asyncio.gather(*extract_tasks)
    results = [r for r in info_outputs if r]

    if os.path.exists(conversation_history_file):
        with open(conversation_history_file, "r") as f:
            history = json.load(f)
    else:
        history = []
    history.append((query, results))
    with open(conversation_history_file, "w") as f:
        json.dump(history, f)

    for item in results:
        print(f"\nfaculty_name: {item['faculty_name']}")
        print(f"information: {item['information']}")

    return results


# ===============================
# ROUTER — chooses first / followup / named
# ===============================
async def run_query(query, retriever):
    route = await classify_query(query)
    qtype = route["type"]
    named = route["faculty"]
    subset = route["subset"]

    if qtype == "named" and named:
        print("-> Named-faculty query:", named)
        return await run_followup_query(query, retriever, faculty_list=named)

    if qtype == "followup":
        print("-> Follow-up query (using previous faculty). subset:", subset)
        return await run_followup_query(query, retriever, subset=subset)

    print("-> New discovery query.")
    return await run_first_query(query, retriever)
