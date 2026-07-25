"""
schema_loader.py — build per-agent ontology schemas, mirroring the notebook.

This is written to read like the notebook's schema cells: one block per
ontology builds numbered variables (entities_labels_N / relation_labels_N /
p_s_N), FRAPO+BIBOS are merged for the Research agent, and an explicit
AGENT_SCHEMAS dict wires them to agent names. load_all_schemas() returns that
dict. TTL files are read from ONTOLOGY_DIR (config.py / ONTOLOGY_DIR env var).
"""

import os
from rdflib import Graph
from ontology_utils import getSchemaFromOnto
from config import ONTOLOGY_DIR


def _schema2(neo4j_schema):
    """Same reshaping the notebook does into entities/relations/potential_schema."""
    return {
        "entities": [
            {"label": label, "properties": data["properties"]}
            for label, data in neo4j_schema["nodes"].items()
        ],
        "relations": [
            {"label": rel["type"]}
            for rel in neo4j_schema["relationships"]
        ],
        "potential_schema": [
            (rel["from"], rel["type"], rel["to"])
            for rel in neo4j_schema["relationships"]
        ],
    }


def _ttl(name):
    return os.path.join(ONTOLOGY_DIR, name)


def build_all():
    """Build every numbered schema exactly as the notebook cells do, then
    assemble AGENT_SCHEMAS. Returns AGENT_SCHEMAS."""

    g2 = Graph()

    # ---- FOAF -> _1 ----
    neo4j_schema = getSchemaFromOnto(g2.parse(_ttl("foaf.ttl")))
    s2 = _schema2(neo4j_schema)
    entities_labels_1 = [e["label"] for e in s2["entities"]]
    relation_labels_1 = [r["label"] for r in s2["relations"]]
    p_s_1 = s2["potential_schema"]

    # ---- Education -> _2 ----
    neo4j_schema = getSchemaFromOnto(g2.parse(_ttl("education_with_person.ttl")))
    s2 = _schema2(neo4j_schema)
    entities_labels_2 = [e["label"] for e in s2["entities"]]
    relation_labels_2 = [r["label"] for r in s2["relations"]]
    p_s_2 = s2["potential_schema"]

    # ---- Academic -> _3 ----
    neo4j_schema = getSchemaFromOnto(g2.parse(_ttl("academic_person_merged.ttl")))
    s2 = _schema2(neo4j_schema)
    entities_labels_3 = [e["label"] for e in s2["entities"]]
    relation_labels_3 = [r["label"] for r in s2["relations"]]
    p_s_3 = s2["potential_schema"]

    # ---- License / ctdl -> _4 ----
    neo4j_schema = getSchemaFromOnto(g2.parse(_ttl("ctdl.ttl")))
    s2 = _schema2(neo4j_schema)
    entities_labels_4 = [e["label"] for e in s2["entities"]]
    relation_labels_4 = [r["label"] for r in s2["relations"]]
    p_s_4 = s2["potential_schema"]

    # ---- Clinical -> _5 ----
    neo4j_schema = getSchemaFromOnto(g2.parse(_ttl("clinical_person_merged.ttl")))
    s2 = _schema2(neo4j_schema)
    entities_labels_5 = [e["label"] for e in s2["entities"]]
    relation_labels_5 = [r["label"] for r in s2["relations"]]
    p_s_5 = s2["potential_schema"]

    # ---- Distribution -> _6 ----
    neo4j_schema = getSchemaFromOnto(g2.parse(_ttl("distribution.ttl")))
    s2 = _schema2(neo4j_schema)
    entities_labels_6 = [e["label"] for e in s2["entities"]]
    relation_labels_6 = [r["label"] for r in s2["relations"]]
    p_s_6 = s2["potential_schema"]

    # ---- Teaching -> _7 ----
    neo4j_schema = getSchemaFromOnto(g2.parse(_ttl("teach_final.ttl")))
    s2 = _schema2(neo4j_schema)
    entities_labels_7 = [e["label"] for e in s2["entities"]]
    relation_labels_7 = [r["label"] for r in s2["relations"]]
    p_s_7 = s2["potential_schema"]

    # ---- Service -> _8 ----
    neo4j_schema = getSchemaFromOnto(g2.parse(_ttl("service_updated.ttl")))
    s2 = _schema2(neo4j_schema)
    entities_labels_8 = [e["label"] for e in s2["entities"]]
    relation_labels_8 = [r["label"] for r in s2["relations"]]
    p_s_8 = s2["potential_schema"]

    # ---- Grant -> _10 ----
    neo4j_schema = getSchemaFromOnto(g2.parse(_ttl("grant.ttl")))
    s2 = _schema2(neo4j_schema)
    entities_labels_10 = [e["label"] for e in s2["entities"]]
    relation_labels_10 = [r["label"] for r in s2["relations"]]
    p_s_10 = s2["potential_schema"]

    # ---- Research = FRAPO + BIBOS merged ----
    g2_frapo = Graph()
    neo4j_schema_frapo = getSchemaFromOnto(g2_frapo.parse(_ttl("frapo.ttl")))
    g2_bibos = Graph()
    neo4j_schema_bibos = getSchemaFromOnto(g2_bibos.parse(_ttl("bibos.ttl")))

    entities_frapo = [
        {"label": label, "properties": data["properties"]}
        for label, data in neo4j_schema_frapo["nodes"].items()
    ]
    entities_bibos = [
        {"label": label, "properties": data["properties"]}
        for label, data in neo4j_schema_bibos["nodes"].items()
    ]
    combined_entities = entities_frapo + entities_bibos
    seen_entity_labels = set()
    deduped_entities = []
    for entity in combined_entities:
        label = entity["label"]
        if label not in seen_entity_labels:
            deduped_entities.append(entity)
            seen_entity_labels.add(label)

    relations_frapo = [{"label": rel["type"]} for rel in neo4j_schema_frapo["relationships"]]
    relations_bibos = [{"label": rel["type"]} for rel in neo4j_schema_bibos["relationships"]]
    combined_relations = relations_frapo + relations_bibos
    seen_relation_labels = set()
    deduped_relations = []
    for relation in combined_relations:
        label = relation["label"]
        if label not in seen_relation_labels:
            deduped_relations.append(relation)
            seen_relation_labels.add(label)

    potential_schema_frapo = [(rel["from"], rel["type"], rel["to"]) for rel in neo4j_schema_frapo["relationships"]]
    potential_schema_bibos = [(rel["from"], rel["type"], rel["to"]) for rel in neo4j_schema_bibos["relationships"]]
    combined_potential_schema = list(dict.fromkeys(potential_schema_frapo + potential_schema_bibos))

    entities_labels_merged = [e["label"] for e in deduped_entities]
    relation_labels_merged = [r["label"] for r in deduped_relations]
    p_s_merged = combined_potential_schema

    # ---- Research hardcoded entity list -> _9 (matches notebook) ----
    entities_labels_9 = ['AccountStatement', 'AdmissionApplication', 'AnnualTurnover', 'ArticleProcessingCharge', 'AvailableFunds', 'BudgetedAmount', 'Bursary', 'College', 'Commitments', 'ComputationalAgent', 'ConferenceFee', 'ConsortiumAgreement', 'ConsultancyAgreement', 'DataRepository', 'Deliverable', 'Department', 'DocumentRepository', 'EmploymentApplication', 'EmploymentContract', 'Endowment', 'ExpenditureToDate', 'Faculty', 'Fellowship', 'FinancialControlSystem', 'FundingApplication', 'FundingProgramme', 'GovernmentOrganization', 'HostInstitution', 'Income', 'Investigation', 'Investment', 'Laboratory', 'Legacy', 'Library', 'MaterialOutput', 'NotForProfitOrganization', 'Owner', 'Payment', 'ProjectBudget', 'Purchase', 'Quotation', 'RegistrationAgency', 'RegistrationAuthority', 'ResearchGroup', 'ResearchInformationSystem', 'ResearchInstitute', 'SME', 'Scholarship', 'ScholarshipApplication', 'ServiceContract', 'ServiceContractFee', 'SpinOffCompany', 'Studentship', 'Subscription', 'Tender', 'University', 'Vendor', 'Account', 'Division', 'Endeavour', 'Expenditure', 'Grant', 'Invoice', 'PostalAddress', 'PurchaseOrder', 'Purchaser', 'Document', 'Group', 'Project', 'Company', 'ComputationalService', 'Equipment', 'Gift', 'Manufacturer', 'Output', 'Service', 'Budget', 'Facility', 'FundingAgency', 'InfrastructureEntity', 'Repository', 'Stipend', 'Supplier', 'Contract', 'Fee', 'Status', 'Application', 'BudgetInformation', 'Funding', 'Person', 'Organization', 'FinancialEntity', 'BudgetCategory', 'Agent', 'Affiliation', 'Journal', 'Conference', 'Publication']
    relation_labels_9 = relation_labels_merged
    p_s_9 = p_s_merged

    # ---- General (generic) -> _11 (matches notebook) ----
    entities_labels_11 = [
        "Person", "Institution", "Concept", "ResearchArea", "Method", "Disease",
        "Gene", "Chemical", "Publication", "Grant", "StatisticalResult",
    ]
    relation_labels_11 = [
        "affiliated_with", "studies", "researches", "focuses_on", "uses_method",
        "investigates", "associated_with", "targets", "funded_by", "authored",
        "collaborates_with", "reports_result",
    ]
    p_s_11 = [
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

    # ---- Explicit AGENT_SCHEMAS dict (exactly like the notebook) ----
    AGENT_SCHEMAS = {
        "FOAF Agent":         {"entities": entities_labels_1,  "relations": relation_labels_1,  "potential_schema": p_s_1},
        "Education Agent":    {"entities": entities_labels_2,  "relations": relation_labels_2,  "potential_schema": p_s_2},
        "Academic Agent":     {"entities": entities_labels_3,  "relations": relation_labels_3,  "potential_schema": p_s_3},
        "License Agent":      {"entities": entities_labels_4,  "relations": relation_labels_4,  "potential_schema": p_s_4},
        "Clinical Agent":     {"entities": entities_labels_5,  "relations": relation_labels_5,  "potential_schema": p_s_5},
        "Distribution Agent": {"entities": entities_labels_6,  "relations": relation_labels_6,  "potential_schema": p_s_6},
        "Teaching Agent":     {"entities": entities_labels_7,  "relations": relation_labels_7,  "potential_schema": p_s_7},
        "Service Agent":      {"entities": entities_labels_8,  "relations": relation_labels_8,  "potential_schema": p_s_8},
        "Grant Agent":        {"entities": entities_labels_10, "relations": relation_labels_10, "potential_schema": p_s_10},
        "Research Agent":     {"entities": entities_labels_9,  "relations": relation_labels_9,  "potential_schema": p_s_9},
        "General Agent":      {"entities": entities_labels_11, "relations": relation_labels_11, "potential_schema": p_s_11},
    }
    return AGENT_SCHEMAS


def load_all_schemas():
    """Return AGENT_SCHEMAS: {agent_name: {entities, relations, potential_schema}}."""
    return build_all()
