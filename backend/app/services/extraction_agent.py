"""Ingestion agent: raw scenario text -> proposed entities/links.

Per the ontology's "identity is a hypothesis" rule, extracted entities/links
are never auto-merged into confirmed data. They're validated against the
current ClassDef/LinkDef ontology and stored as a pending IngestBatch node
with status=proposed for human review via POST /ingest/{batch_id}/commit.
"""
import json
import uuid

from neo4j import AsyncSession

from app.llm.client import complete_json
from app.models.entity import EntityCreate
from app.models.link import LinkCreate
from app.ontology.validate import ValidationError, validate_entity, validate_link

PROMPT_TEMPLATE = """You are an intelligence analyst assistant. Extract entities and \
relationships (links) from the given scenario text as strict JSON:
{{
  "entities": [{{"entity_id": "<generated e.g. P-temp1>", "entity_class": "<ROOT CLASS>", \
"entity_subclass": "<one full key from ENTITY_SUBCLASSES below>", "label": "...", \
"confidence": "<A-F 1-6 code>", "source_ref": "<short ref>"}}],
  "links": [{{"link_id": "<generated e.g. L-temp1>", "link_type": "<one value from LINK_TYPES below>", \
"source_entity": "<entity_id>", "target_entity": "<entity_id>", "direction": "directed|symmetric", \
"confidence": "<A-F 1-6 code>", "source_ref": "<short ref>"}}]
}}
entity_subclass and link_type MUST be copied exactly (case-sensitive) from these lists — \
never invent, abbreviate, or paraphrase a key, even if a synonym in the text (e.g. \
"stationed at", "posted at") isn't literally one of these words. Use each link's domain \
-> range and notes to pick the real link_type whose meaning best matches the text. Every \
link is stored in its forward direction only (source -> target); if the text describes \
the inverse relationship (e.g. "operated by" for operator_of), still use the forward \
link_type and instead put the text's object as source_entity and its subject as \
target_entity. Link types have no past-tense variant — if the text describes a \
relationship that has ended, use the closest forward link_type and rely on review to add \
historical dates; do not invent a "previously_x" type. If nothing in the list fits, omit \
that entity/link rather than guessing.

This scenario may extend earlier ones already in the graph. If an entity in the text \
refers to the same real-world thing as one in EXISTING_ENTITIES below (same name, or an \
alias/clear variant of it), reuse that entity's exact entity_id in your links and do NOT \
repeat it in the "entities" array — only list entities that are genuinely new. If unsure \
whether it's the same entity, treat it as new rather than guessing a match.

ENTITY_SUBCLASSES:
{entity_subclasses}

LINK_TYPES — each line is "type (domain -> range); notes | inverse phrasing: ...":
{link_types}

EXISTING_ENTITIES — each line is "entity_id | label (aliases) | entity_class":
{existing_entities}

Return JSON only, no prose."""


async def _fetch_ontology_vocab(
    session: AsyncSession,
) -> tuple[list[str], list[dict], dict[str, str]]:
    """Pull the current valid entity_subclass keys and full LinkDef rows
    (domain/range/notes/inverse) from the graph, so the extraction prompt/
    normalization can only work with real ontology terms — this is what
    stops the LLM hallucinating plausible-but-invalid keys, and the extra
    domain/notes context is what lets it disambiguate synonyms (e.g.
    "stationed at") into the correct real link_type instead of guessing.
    """
    classes = await session.run("MATCH (c:ClassDef) RETURN c.key AS key ORDER BY c.key")
    class_keys = [record["key"] async for record in classes]

    links = await session.run(
        """
        MATCH (l:LinkDef)
        RETURN l.type AS type, l.domain AS domain, l.range AS range,
               l.notes AS notes, l.inverse AS inverse
        ORDER BY l.type
        """
    )
    link_defs: list[dict] = []
    inverse_to_forward: dict[str, str] = {}
    async for record in links:
        link_defs.append(dict(record))
        if record["inverse"]:
            inverse_to_forward[record["inverse"]] = record["type"]

    return class_keys, link_defs, inverse_to_forward


async def _fetch_existing_entities(session: AsyncSession, limit: int = 500) -> list[dict]:
    """Committed entities already in the graph, so a later ingest can extend
    earlier scenarios instead of duplicating people/orgs it re-mentions.
    simplification: a flat recent-N list, no similarity search — fine for a
    demo-scale graph; a real deployment would need embedding-based retrieval
    once entity counts get too large for one prompt.
    """
    result = await session.run(
        """
        MATCH (e:Entity)
        RETURN e.entity_id AS entity_id, e.label AS label, e.aliases AS aliases,
               e.entity_class AS entity_class
        ORDER BY e.entity_id
        LIMIT $limit
        """,
        limit=limit,
    )
    return [dict(record) async for record in result]


def _format_existing_entities(entities: list[dict]) -> str:
    if not entities:
        return "(none yet — this is the first scenario)"
    lines = []
    for e in entities:
        aliases = f" ({', '.join(e['aliases'])})" if e.get("aliases") else ""
        lines.append(f"{e['entity_id']} | {e['label']}{aliases} | {e['entity_class']}")
    return "\n".join(lines)


def _format_link_types(link_defs: list[dict]) -> str:
    """One line per link type with its domain/range and, when present, its
    notes and documented inverse phrasing — the semantic context the model
    needs to map a synonym like "stationed at" to the right real link_type."""
    lines = []
    for l in link_defs:
        line = f"{l['type']} ({l['domain']} -> {l['range']})"
        if l.get("notes"):
            line += f"; {l['notes']}"
        if l.get("inverse"):
            line += f" | inverse phrasing: {l['inverse']}"
        lines.append(line)
    return "\n".join(lines)


async def extract_from_text(session: AsyncSession, text: str) -> dict:
    """Call the LLM, validate each proposed entity/link, return a batch dict
    (valid items + rejected items with reasons) — nothing is committed here.
    """
    class_keys, link_defs, inverse_to_forward = await _fetch_ontology_vocab(session)
    existing_entities = await _fetch_existing_entities(session)
    system_prompt = PROMPT_TEMPLATE.format(
        entity_subclasses="\n".join(class_keys),
        link_types=_format_link_types(link_defs),
        existing_entities=_format_existing_entities(existing_entities),
    )
    raw = await complete_json(system_prompt, text)

    # Belt-and-suspenders: even if the model emits a documented inverse name
    # despite the instructions (e.g. "operated_by" instead of "operator_of"),
    # normalize it to the canonical forward type and swap source/target
    # rather than rejecting a link the ontology actually supports.
    for row in raw.get("links", []):
        forward = inverse_to_forward.get(row.get("link_type"))
        if forward:
            row["link_type"] = forward
            row["source_entity"], row["target_entity"] = (
                row.get("target_entity"),
                row.get("source_entity"),
            )

    valid_entities, rejected_entities = [], []
    for row in raw.get("entities", []):
        try:
            entity = EntityCreate(**row)
            await validate_entity(session, entity)
            valid_entities.append(entity.model_dump(mode="json"))
        except (ValidationError, ValueError, TypeError) as e:
            rejected_entities.append({"row": row, "reason": str(e)})

    # A link's endpoints may be a brand-new entity from this batch, or a real
    # entity_id the model chose to reuse from EXISTING_ENTITIES — both are
    # valid targets; anything else is a hallucinated reference.
    class_by_id = {e["entity_id"]: e["entity_class"] for e in valid_entities}
    class_by_id.update({e["entity_id"]: e["entity_class"] for e in existing_entities})

    valid_links, rejected_links = [], []
    for row in raw.get("links", []):
        try:
            link = LinkCreate(**row)
            if link.source_entity not in class_by_id or link.target_entity not in class_by_id:
                raise ValidationError(
                    "link references an entity not in this batch's valid set "
                    "and not an existing entity_id"
                )
            await validate_link(
                session, link, class_by_id[link.source_entity], class_by_id[link.target_entity]
            )
            valid_links.append(link.model_dump(mode="json"))
        except (ValidationError, ValueError, TypeError) as e:
            rejected_links.append({"row": row, "reason": str(e)})

    batch_id = f"B-{uuid.uuid4().hex[:8]}"
    batch = {
        "batch_id": batch_id,
        "status": "proposed",
        "source_text": text,
        "entities": valid_entities,
        "links": valid_links,
        "rejected_entities": rejected_entities,
        "rejected_links": rejected_links,
    }
    await session.run(
        """
        CREATE (b:IngestBatch {batch_id: $batch_id, status: $status, source_text: $source_text,
                                entities: $entities, links: $links})
        """,
        batch_id=batch_id,
        status="proposed",
        source_text=text,
        entities=json.dumps(valid_entities),
        links=json.dumps(valid_links),
    )
    return batch
