"""
retrievers.py — build the Hybrid and Vector retrievers from the notebook.

Per request, the retriever menu is:
    option 1 = Hybrid   (HybridCypherRetriever, graph-expanded context)
    option 2 = Vector   (VectorRetriever, plain chunk text)

`choice` is only a label — the pipeline auto-detects the retriever type from
the returned content, so 1 vs 2 does not change parsing. The vector retriever
returns source/source2 so chunks can be mapped back to a faculty member.
"""

from neo4j_graphrag.retrievers import VectorRetriever, HybridCypherRetriever

# Graph-expansion Cypher used by the Hybrid retriever (verbatim from notebook).
CUSTOM_HYBRID_QUERY = """
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


def get_retriever(choice_label: str, driver, embedding):
    """
    Return (retriever, choice).
      choice_label == "Hybrid" -> HybridCypherRetriever, choice 1
      choice_label == "Vector" -> VectorRetriever,       choice 2
    """
    if choice_label == "Vector":
        retriever = VectorRetriever(
            driver,
            index_name="text_embeddings",
            embedder=embedding,
            # source props so source_matches_faculty() can map chunks -> faculty
            return_properties=["text", "source", "source2"],
        )
        return retriever, 2
    else:
        retriever = HybridCypherRetriever(
            driver,
            "text_embeddings",
            "text_embeddings2",
            CUSTOM_HYBRID_QUERY,
            embedding,
        )
        return retriever, 1
