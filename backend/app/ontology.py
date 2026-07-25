"""
ontology.py

Turns the RDF/OWL Turtle files in `ontology/` into the per agent schemas the
router uses to pick a domain for each question.

Difference from the original Streamlit code: that version reused a single
rdflib Graph across every parse, so each ontology accumulated all of the
previous ones and every agent past the first advertised a blended vocabulary.
Each ontology is parsed into its own Graph here, so agent descriptions are
actually distinct and routing has something real to discriminate on.
"""

from __future__ import annotations

import functools
import logging
from pathlib import Path
from typing import Any

from rdflib import Graph
from rdflib.namespace import OWL, RDF, RDFS

from .settings import settings

log = logging.getLogger(__name__)


def local_part(uri: Any) -> str:
    text = str(uri)
    for separator in ("#", "/", ":"):
        if separator in text:
            text = text.rsplit(separator, 1)[-1]
    return text


def _properties_for_class(graph: Graph, klass: Any) -> list[str]:
    return [
        local_part(prop)
        for prop in graph.subjects(RDFS.domain, klass)
        if (prop, RDF.type, OWL.DatatypeProperty) in graph
    ]


def schema_from_ontology(graph: Graph) -> dict[str, Any]:
    """Extract {nodes, relationships} from an OWL graph."""
    nodes: dict[str, dict[str, list[str]]] = {}
    relationships: list[dict[str, str]] = []
    classes = set()

    for klass in graph.subjects(RDF.type, OWL.Class):
        classes.add(klass)
        nodes[local_part(klass)] = {"properties": _properties_for_class(graph, klass)}

    # Classes that are only implied by being the domain or range of a property.
    for klass in graph.objects(None, RDFS.domain):
        if klass not in classes and not str(klass).startswith(str(RDFS)):
            nodes.setdefault(local_part(klass), {"properties": _properties_for_class(graph, klass)})

    for klass in graph.objects(None, RDFS.range):
        if klass not in classes and not str(klass).startswith("http://www.w3.org/2001/XMLSchema"):
            nodes.setdefault(local_part(klass), {"properties": _properties_for_class(graph, klass)})

    for prop in graph.subjects(RDF.type, OWL.ObjectProperty):
        rel = local_part(prop)
        domains = [local_part(d) for d in graph.objects(prop, RDFS.domain)]
        ranges = [local_part(r) for r in graph.objects(prop, RDFS.range)]
        for domain in domains:
            for range_ in ranges:
                relationships.append({"type": rel, "from": domain, "to": range_})

    return {"nodes": nodes, "relationships": relationships}


def _load(filename: str) -> dict[str, Any]:
    path = Path(settings.ontology_dir) / filename
    graph = Graph()
    graph.parse(str(path))
    return schema_from_ontology(graph)


def _labels(schema: dict[str, Any]) -> tuple[list[str], list[str], list[tuple[str, str, str]]]:
    entities = list(schema["nodes"].keys())
    relations = list(dict.fromkeys(rel["type"] for rel in schema["relationships"]))
    potential = list(
        dict.fromkeys((rel["from"], rel["type"], rel["to"]) for rel in schema["relationships"])
    )
    return entities, relations, potential


def _merge(*schemas: dict[str, Any]) -> dict[str, Any]:
    nodes: dict[str, dict[str, list[str]]] = {}
    relationships: list[dict[str, str]] = []
    for schema in schemas:
        for label, data in schema["nodes"].items():
            nodes.setdefault(label, data)
        relationships.extend(schema["relationships"])
    deduped = list({(r["from"], r["type"], r["to"]): r for r in relationships}.values())
    return {"nodes": nodes, "relationships": deduped}


# A generic fallback domain for questions that do not map onto any of the
# published ontologies. Kept from the original app.
_GENERAL_ENTITIES = [
    "Person", "Institution", "Concept", "ResearchArea", "Method", "Disease",
    "Gene", "Chemical", "Publication", "Grant", "StatisticalResult",
]
_GENERAL_RELATIONS = [
    "affiliated_with", "studies", "researches", "focuses_on", "uses_method",
    "investigates", "associated_with", "targets", "funded_by", "authored",
    "collaborates_with", "reports_result",
]
_GENERAL_POTENTIAL = [
    ("Person", "affiliated_with", "Institution"),
    ("Person", "researches", "ResearchArea"),
    ("Person", "focuses_on", "Concept"),
    ("Person", "uses_method", "Method"),
    ("Person", "authored", "Publication"),
    ("Publication", "investigates", "Disease"),
    ("Publication", "associated_with", "Gene"),
    ("Publication", "uses_method", "Method"),
    ("ResearchArea", "associated_with", "Disease"),
    ("ResearchArea", "targets", "Gene"),
    ("Grant", "funded_by", "Institution"),
    ("Person", "funded_by", "Grant"),
    ("Person", "collaborates_with", "Person"),
    ("Publication", "reports_result", "StatisticalResult"),
]

# Short human readable hints that give the router more signal than a raw list
# of OWL class names can.
AGENT_HINTS: dict[str, str] = {
    "FOAF Agent": "People, names, contact details, online accounts, and who knows whom.",
    "Education Agent": "Degrees, training, schools attended, and fields of study.",
    "Academic Agent": "Academic appointments, faculty titles, departments, and universities.",
    "License Agent": "Credentials, certifications, licences, and formal awards.",
    "Clinical Agent": "Clinical roles, healthcare facilities, procedures, and clinical services.",
    "Distribution Agent": "Data distribution, repositories, and dataset access.",
    "Teaching Agent": "Courses taught, lessons, mentoring, assessments, and students.",
    "Service Agent": "Committee service, leadership roles, and professional service activities.",
    "Grant Agent": "Grants, funding agencies, grant periods, amounts, and grant roles.",
    "Research Agent": (
        "Research expertise, scientific interests, publications, abstracts, journals, "
        "conferences, methods, diseases, projects, and research funding. "
        "This is the broadest domain and fits most expertise questions."
    ),
    "General Agent": "Anything that does not clearly belong to another domain.",
}

_ONTOLOGY_FILES: dict[str, str] = {
    "FOAF Agent": "foaf.ttl",
    "Education Agent": "education_with_person.ttl",
    "Academic Agent": "academic_person_merged.ttl",
    "License Agent": "ctdl.ttl",
    "Clinical Agent": "clinical_person_merged.ttl",
    "Distribution Agent": "distribution.ttl",
    "Teaching Agent": "teach_final.ttl",
    "Service Agent": "service_updated.ttl",
    "Grant Agent": "grant.ttl",
}


@functools.lru_cache(maxsize=1)
def load_agent_schemas() -> dict[str, dict[str, Any]]:
    """
    Build every agent schema once per process. Parsing eleven Turtle files
    takes a noticeable moment, and the result never changes at runtime.
    """
    schemas: dict[str, dict[str, Any]] = {}

    for agent_name, filename in _ONTOLOGY_FILES.items():
        try:
            entities, relations, potential = _labels(_load(filename))
        except Exception as exc:
            log.warning("Skipping %s, could not parse %s: %s", agent_name, filename, exc)
            continue
        schemas[agent_name] = {
            "entities": entities,
            "relations": relations,
            "potential_schema": potential,
            "hint": AGENT_HINTS.get(agent_name, ""),
        }

    # The research domain spans two published ontologies, FRAPO for funded
    # research administration and BIBOS for bibliographic records.
    try:
        research = _merge(_load("frapo.ttl"), _load("bibos.ttl"))
        entities, relations, potential = _labels(research)
        schemas["Research Agent"] = {
            "entities": entities,
            "relations": relations,
            "potential_schema": potential,
            "hint": AGENT_HINTS["Research Agent"],
        }
    except Exception as exc:
        log.warning("Could not build Research Agent schema: %s", exc)

    schemas["General Agent"] = {
        "entities": _GENERAL_ENTITIES,
        "relations": _GENERAL_RELATIONS,
        "potential_schema": _GENERAL_POTENTIAL,
        "hint": AGENT_HINTS["General Agent"],
    }

    return schemas


def agent_names() -> list[str]:
    return list(load_agent_schemas().keys())


def describe_agents(max_terms: int = 28) -> str:
    """Compact router prompt block. Long class lists are truncated."""
    blocks = []
    for name, schema in load_agent_schemas().items():
        entities = schema["entities"][:max_terms]
        relations = schema["relations"][:max_terms]
        blocks.append(
            f"Agent: {name}\n"
            f"Covers: {schema['hint']}\n"
            f"Entities: {', '.join(entities)}\n"
            f"Relations: {', '.join(relations)}"
        )
    return "\n\n".join(blocks)
