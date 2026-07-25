"""
ontology_utils.py — RDF/OWL ontology -> dict schema helpers.

Reproduced verbatim from the notebook's schema-helper cell. getSchemaFromOnto
returns {"nodes": {label: {"properties": [...]}}, "relationships": [{type,from,to}]}.
"""

from rdflib import Graph
from rdflib.namespace import RDF, OWL, RDFS


def getLocalPart(uri):
    uri = str(uri)
    for sep in ['#', '/', ':']:
        if sep in uri:
            uri = uri.rsplit(sep, 1)[-1]
    return uri


def getNLOntology(g):
    result = ""
    definedcats = set()

    result += "\nNode Labels:\n"
    for cat in g.subjects(RDF.type, OWL.Class):
        result += getLocalPart(cat)
        definedcats.add(cat)
        for desc in g.objects(cat, RDFS.comment):
            result += ": " + str(desc) + "\n"

    result += "\nNode Properties:\n"
    for att in g.subjects(RDF.type, OWL.DatatypeProperty):
        result += getLocalPart(att)
        for dom in g.objects(att, RDFS.domain):
            result += f": Attribute of {getLocalPart(dom)}"
        for desc in g.objects(att, RDFS.comment):
            result += ". " + str(desc)
        result += "\n"

    result += "\nRelationships:\n"
    for att in g.subjects(RDF.type, OWL.ObjectProperty):
        result += getLocalPart(att)
        for dom in g.objects(att, RDFS.domain):
            result += f": from {getLocalPart(dom)}"
        for ran in g.objects(att, RDFS.range):
            result += f" to {getLocalPart(ran)}"
        for desc in g.objects(att, RDFS.comment):
            result += ". " + str(desc)
        result += "\n"

    return result


def getPropertiesForClass(g, cat):
    props = []
    for p in g.subjects(RDFS.domain, cat):
        if (p, RDF.type, OWL.DatatypeProperty) in g:
            props.append(getLocalPart(p))
    return props


def getSchemaFromOnto(g):
    """RETURNS A DICT-BASED SCHEMA (neo4j_graphrag compatible)."""
    nodes = {}
    relationships = []
    classes = set()

    # ---- Classes ----
    for cat in g.subjects(RDF.type, OWL.Class):
        label = getLocalPart(cat)
        classes.add(cat)
        nodes[label] = {"properties": getPropertiesForClass(g, cat)}

    # ---- Domains / Ranges not explicitly typed as OWL.Class ----
    for cat in g.objects(None, RDFS.domain):
        if cat not in classes and not str(cat).startswith(str(RDFS)):
            label = getLocalPart(cat)
            nodes.setdefault(label, {"properties": getPropertiesForClass(g, cat)})

    for cat in g.objects(None, RDFS.range):
        if (cat not in classes
                and not str(cat).startswith("http://www.w3.org/2001/XMLSchema")):
            label = getLocalPart(cat)
            nodes.setdefault(label, {"properties": getPropertiesForClass(g, cat)})

    # ---- Object Properties -> Relationships ----
    for op in g.subjects(RDF.type, OWL.ObjectProperty):
        rel = getLocalPart(op)
        domains = [getLocalPart(d) for d in g.objects(op, RDFS.domain)]
        ranges = [getLocalPart(r) for r in g.objects(op, RDFS.range)]
        for d in domains:
            for r in ranges:
                relationships.append({"type": rel, "from": d, "to": r})

    return {"nodes": nodes, "relationships": relationships}


def getPKs(g):
    return [getLocalPart(k) for k in g.subjects(RDF.type, OWL.InverseFunctionalProperty)]
