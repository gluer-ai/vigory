"""Finance / corporate extension to the military-intelligence ontology.

The base taxonomy (milinteltaxonomyontology.xlsx) has no place for things like
a bankruptcy filing, an acquisition or a financial regulator, so documents
outside the military domain extract almost nothing. This adds a small set of
classes and link types, upserted idempotently (safe to re-run, and safe to run
before or after import_ontology.py - it never deletes or edits base rows).

Usage: python ontology/finance_extension.py
Reads NEO4J_URI / NEO4J_USER / NEO4J_PASSWORD from the environment (or .env).
"""
import os
from pathlib import Path

from neo4j import GraphDatabase

try:  # optional: the deployed container has real env vars and no .env
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
except ImportError:
    pass

# (full key, notes). Parent = key minus last segment; it must already exist
# in the base ontology or be listed earlier here.
CLASSES: list[tuple[str, str]] = [
    ("EVENT.TRANSACTION.BANKRUPTCY_FILING", "Insolvency or bankruptcy proceeding (e.g. Chapter 11)"),
    ("EVENT.TRANSACTION.ACQUISITION_MERGER", "Takeover, merger or sale of a business or its assets"),
    ("EVENT.TRANSACTION.BAILOUT_RESCUE_FINANCING", "Emergency funding or government/consortium rescue"),
    ("EVENT.ECONOMIC_EVENT", "Market-level events"),
    ("EVENT.ECONOMIC_EVENT.MARKET_CRASH_CRISIS", "Financial crisis, market crash, credit freeze"),
    ("EVENT.LEGAL_AND_ADMINISTRATIVE_EVENT.REGULATORY_ACTION", "Enforcement, fine, sanction or rule-making by a regulator"),
    ("ORGANIZATION.GOVERNMENT_BODY.FINANCIAL_REGULATOR", "e.g. securities or banking supervisor"),
    ("ORGANIZATION.GOVERNMENT_BODY.CENTRAL_BANK", "National or regional central bank"),
    ("ORGANIZATION.COMMERCIAL_ENTITY.HOLDING_COMPANY", "Parent company whose main asset is its subsidiaries"),
    ("ORGANIZATION.COMMERCIAL_ENTITY.INVESTMENT_FUND", "Fund, asset manager or private-equity vehicle"),
    ("ORGANIZATION.COMMERCIAL_ENTITY.CREDIT_RATING_AGENCY", "Issues credit ratings"),
]

CATEGORY = "Financial & Commercial"

# Domain/range use root-class labels, '; '-separated, exactly like the base
# Link_Ontology sheet. `inverse` is the phrasing the extractor may emit; it is
# normalised back to the forward type.
LINKS: list[dict] = [
    dict(type="acquired", domain="Organization", range="Organization", inverse="acquired_by",
         transitive="No", notes="Acquirer -> target; reify as an Acquisition event when dated or valued"),
    dict(type="subsidiary_of", domain="Organization", range="Organization", inverse="has_subsidiary",
         transitive="Yes", notes="Child company -> parent company"),
    dict(type="investor_in", domain="Person; Organization", range="Organization", inverse="has_investor",
         transitive="No", notes="Equity or fund investment"),
    dict(type="creditor_of", domain="Person; Organization", range="Person; Organization", inverse="debtor_of",
         transitive="No", notes="Lender -> borrower"),
    dict(type="regulated_by", domain="Organization", range="Organization", inverse="regulates",
         transitive="No", notes="Supervised entity -> regulator"),
    dict(type="headquartered_in", domain="Organization", range="Location; Facility", inverse="headquarters_of",
         transitive="No", notes="Registered or principal seat"),
    dict(type="filed_for", domain="Organization", range="Event", inverse="filed_by",
         transitive="No", notes="Organization that filed for (or was put into) a bankruptcy/insolvency event"),
]


def class_rows() -> list[dict]:
    rows = []
    for key, notes in CLASSES:
        levels = key.split(".")
        rows.append(
            {
                "key": key,
                "parent_key": ".".join(levels[:-1]),
                "level": len(levels),
                "label": levels[-1].replace("_", " ").title(),
                "notes": notes,
            }
        )
    return rows


def link_rows() -> list[dict]:
    return [
        {
            "category": CATEGORY,
            "directionality": "Directed",
            "symmetric": "No",
            **row,
        }
        for row in LINKS
    ]


def apply(driver) -> dict:
    with driver.session() as session:
        session.run(
            """
            UNWIND $rows AS row
            MERGE (c:ClassDef {key: row.key})
            SET c += row
            """,
            rows=class_rows(),
        )
        session.run(
            """
            MATCH (c:ClassDef) WHERE c.key IN $keys
            MATCH (p:ClassDef {key: c.parent_key})
            MERGE (c)-[:SUBCLASS_OF]->(p)
            """,
            keys=[k for k, _ in CLASSES],
        )
        session.run(
            """
            UNWIND $rows AS row
            MERGE (l:LinkDef {type: row.type})
            SET l += row
            """,
            rows=link_rows(),
        )
        counts = session.run(
            """
            RETURN count { MATCH (c:ClassDef) RETURN c } AS classes,
                   count { MATCH (l:LinkDef) RETURN l } AS links,
                   count { MATCH (c:ClassDef) WHERE c.key IN $keys
                           MATCH (c)-[:SUBCLASS_OF]->(:ClassDef) RETURN c } AS linked_to_parent
            """,
            keys=[k for k, _ in CLASSES],
        ).single()
        return dict(counts)


def main():
    uri = os.environ.get("NEO4J_URI", "bolt://localhost:7687")
    driver = GraphDatabase.driver(
        uri,
        auth=(os.environ.get("NEO4J_USER", "neo4j"), os.environ.get("NEO4J_PASSWORD", "changeme123")),
    )
    try:
        print(f"Applied finance extension to {uri}: {apply(driver)} "
              f"(+{len(CLASSES)} classes, +{len(LINKS)} link types)")
    finally:
        driver.close()


if __name__ == "__main__":
    main()
