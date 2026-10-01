"""Ingestion agent: raw scenario text -> proposed entities/links.

Per the ontology's "identity is a hypothesis" rule, extracted entities/links
are never auto-merged into confirmed data. They're validated against the
current ClassDef/LinkDef ontology and stored as a pending IngestBatch node
with status=proposed for human review via POST /ingest/{batch_id}/commit.
"""
import json
import logging
import uuid

from neo4j import AsyncSession

from app.llm.client import LLMError, complete_json
from app.models.entity import EntityCreate
from app.models.link import LinkCreate
from app.ontology.validate import ValidationError, validate_entity, validate_link
from app.services.ontology_extension import extend_ontology, normalize_link_type

logger = logging.getLogger(__name__)

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
always the COMPLETE dotted key starting with its root class (e.g. "LOCATION.ADMINISTRATIVE_AREA\
.MUNICIPALITY_SETTLEMENT", never "ADMINISTRATIVE_AREA.MUNICIPALITY_SETTLEMENT") — \
never invent, abbreviate, or paraphrase a key, even if a synonym in the text (e.g. \
"stationed at", "posted at") isn't literally one of these words. Use each link's domain \
-> range and notes to pick the real link_type whose meaning best matches the text. Every \
link is stored in its forward direction only (source -> target); if the text describes \
the inverse relationship (e.g. "operated by" for operator_of), still use the forward \
link_type and instead put the text's object as source_entity and its subject as \
target_entity. Link types have no past-tense variant — if the text describes a \
relationship that has ended, use the closest forward link_type and rely on review to add \
historical dates; do not invent a "previously_x" type.

If NOTHING in a list is a genuinely good fit, do NOT force a poor match (a pharmaceutical \
company is not a DEFENCE_INDUSTRIAL_FIRM, a CEO is not a DIPLOMAT, a drug-development \
programme is not a SHIPMENT). Instead propose a new term and the system will validate it and \
add it to the ontology: for entity_subclass use the closest existing key as a parent plus a \
new UPPER_SNAKE_CASE segment (e.g. "ORGANIZATION.COMMERCIAL_ENTITY.PHARMACEUTICAL_COMPANY"); \
for link_type use a new lower_snake_case forward verb phrase (e.g. "developed"). Prefer an \
existing key whenever one reasonably fits.

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

    Only leaf classes (no other ClassDef subclasses them) are offered as
    entity_subclass options — intermediate/root nodes like "PERSON" or
    "PERSON.MILITARY_PERSONNEL" are category headings, not meant to be
    picked directly when a more specific descendant exists. Trimming them
    shrinks the injected vocabulary and cuts ambiguity between a category
    and its own children.
    """
    classes = await session.run(
        """
        MATCH (c:ClassDef)
        WHERE NOT ()-[:SUBCLASS_OF]->(c)
        RETURN c.key AS key
        ORDER BY c.key
        """
    )
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


async def _fetch_all_class_keys(session: AsyncSession) -> set[str]:
    """Every ClassDef key (leaf or not), used to repair abbreviated keys."""
    result = await session.run("MATCH (c:ClassDef) RETURN c.key AS key ORDER BY c.key")
    return {record["key"] async for record in result}


def resolve_class_key(value: object, all_keys: set[str]) -> str | None:
    """Map a model-written entity_subclass to a real ClassDef key, or None.

    Models routinely drop the root segment ("MUNICIPALITY_SETTLEMENT" or
    "ADMINISTRATIVE_AREA.MUNICIPALITY_SETTLEMENT" for
    "LOCATION.ADMINISTRATIVE_AREA.MUNICIPALITY_SETTLEMENT") or change case.
    Those are unambiguous abbreviations, so accept them - but only when
    exactly one real key matches. Anything ambiguous or unknown stays None
    and is rejected for a human to classify; we never guess between keys.
    """
    if not isinstance(value, str):
        return None
    v = value.strip()
    if v in all_keys:
        return v
    normalized = v.upper().replace(" ", "_")
    if normalized in all_keys:
        return normalized
    matches = [k for k in all_keys if k.endswith("." + normalized)]
    return matches[0] if len(matches) == 1 else None


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


async def _extract_and_validate(
    session: AsyncSession, text: str, extra_existing_entities: list[dict] | None = None
) -> dict:
    """Call the LLM, validate each proposed entity/link, return the valid +
    rejected rows — no IngestBatch is persisted here, so this can be called
    once per chunk of a larger document without creating a batch per chunk
    (see app.services.document_ingest.process_document). extra_existing_entities
    lets a caller fold entities accepted from earlier chunks of the same
    document into this chunk's "existing entities" context, so a repeated
    mention across chunks reuses the same entity_id instead of duplicating it.
    """
    class_keys, link_defs, inverse_to_forward = await _fetch_ontology_vocab(session)
    all_class_keys = await _fetch_all_class_keys(session)
    existing_entities = await _fetch_existing_entities(session)
    if extra_existing_entities:
        existing_entities = existing_entities + extra_existing_entities

    system_prompt = PROMPT_TEMPLATE.format(
        entity_subclasses="\n".join(class_keys),
        link_types=_format_link_types(link_defs),
        existing_entities=_format_existing_entities(existing_entities),
    )
    raw = await complete_json(system_prompt, text)

    def normalize_links(rows: list[dict], inverse_map: dict[str, str]) -> None:
        # The model sometimes echoes the prompt's "type (Domain -> Range)" line,
        # or emits a documented inverse name (e.g. "operated_by" instead of
        # "operator_of"). Clean the type, and for an inverse use the canonical
        # forward type with source/target swapped, rather than rejecting a link
        # the ontology actually supports.
        for row in rows:
            cleaned = normalize_link_type(row.get("link_type"))
            if cleaned:
                row["link_type"] = cleaned
            forward = inverse_map.get(row.get("link_type"))
            if forward:
                row["link_type"] = forward
                row["source_entity"], row["target_entity"] = (
                    row.get("target_entity"),
                    row.get("source_entity"),
                )

    normalize_links(raw.get("links", []), inverse_to_forward)

    valid_entities: list[dict] = []
    valid_links: list[dict] = []
    # A link's endpoints may be a brand-new entity from this batch, or a real
    # entity_id the model chose to reuse from EXISTING_ENTITIES - both are
    # valid targets; anything else is a hallucinated reference.
    class_by_id: dict[str, str] = {e["entity_id"]: e["entity_class"] for e in existing_entities}

    async def try_entity(row: dict) -> str | None:
        """Validate one entity row; returns a rejection reason, or None if accepted."""
        try:
            resolved = resolve_class_key(row.get("entity_subclass"), all_class_keys)
            if resolved:
                row["entity_subclass"] = resolved
                row["entity_class"] = resolved.split(".")[0]
            entity = EntityCreate(**row)
            await validate_entity(session, entity)
        except (ValidationError, ValueError, TypeError) as e:
            return str(e)
        dumped = entity.model_dump(mode="json")
        valid_entities.append(dumped)
        class_by_id[dumped["entity_id"]] = dumped["entity_class"]
        return None

    async def try_link(row: dict) -> str | None:
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
        except (ValidationError, ValueError, TypeError) as e:
            return str(e)
        valid_links.append(link.model_dump(mode="json"))
        return None

    rejected_entities: list[dict] = []
    for row in raw.get("entities", []):
        reason = await try_entity(row)
        if reason:
            rejected_entities.append({"row": row, "reason": reason})
    rejected_links: list[dict] = []
    for row in raw.get("links", []):
        reason = await try_link(row)
        if reason:
            rejected_links.append({"row": row, "reason": reason})

    # Nothing in the ontology fits these rows. Let the agent map them to
    # existing terms or extend the ontology (validated, tagged origin=extraction),
    # then give every rejected row one more chance. Fail open: if this step
    # errors, the rows simply stay rejected for manual review.
    if rejected_entities or rejected_links:
        try:
            ext = await extend_ontology(
                session,
                rejected_entities,
                rejected_links,
                endpoint_info=_endpoint_info(raw, existing_entities),
            )
        except LLMError as e:
            logger.warning("ontology extension skipped: %s", e)
            ext = None
        if ext and (ext["entity_assignments"] or ext["link_assignments"]):
            all_class_keys = await _fetch_all_class_keys(session)
            _, _, inverse_to_forward = await _fetch_ontology_vocab(session)

            still_rejected_entities = []
            for idx, item in enumerate(rejected_entities):
                row = item["row"]
                if idx in ext["entity_assignments"]:
                    row["entity_subclass"] = ext["entity_assignments"][idx]
                reason = await try_entity(row)
                if reason:
                    still_rejected_entities.append({"row": row, "reason": reason})
            rejected_entities = still_rejected_entities

            # Retry every rejected link, not just reassigned ones: many were
            # rejected only because an endpoint entity was, which may now be valid.
            for idx, item in enumerate(rejected_links):
                if idx in ext["link_assignments"]:
                    item["row"]["link_type"] = ext["link_assignments"][idx]
            normalize_links([i["row"] for i in rejected_links], inverse_to_forward)
            still_rejected_links = []
            for item in rejected_links:
                reason = await try_link(item["row"])
                if reason:
                    still_rejected_links.append({"row": item["row"], "reason": reason})
            rejected_links = still_rejected_links

    return {
        "valid_entities": valid_entities,
        "rejected_entities": rejected_entities,
        "valid_links": valid_links,
        "rejected_links": rejected_links,
    }


def _endpoint_info(raw: dict, existing_entities: list[dict]) -> dict[str, str]:
    """entity_id -> "Label (ROOT)" for readable lines in the extension prompt."""
    info = {e["entity_id"]: f"{e['label']} ({e['entity_class']})" for e in existing_entities}
    for row in raw.get("entities", []):
        if isinstance(row, dict) and row.get("entity_id"):
            info[row["entity_id"]] = f"{row.get('label', '?')} ({row.get('entity_class', '?')})"
    return info


CLASSIFY_PROMPT_TEMPLATE = """You are re-classifying entities that were rejected from a batch because \
their proposed entity_subclass wasn't a real key in this ontology. For each numbered entity below, pick \
the single best-matching REAL leaf key from ENTITY_SUBCLASSES. Use the label and the entity's originally- \
attempted class/subclass as context for what kind of thing it is — a hallucinated subclass name like \
"Event.Bankruptcy" still tells you it's an EVENT-shaped thing, so pick the closest real EVENT leaf. If \
truly nothing fits, omit that entity rather than guessing wildly.

ENTITY_SUBCLASSES:
{entity_subclasses}

ENTITIES TO CLASSIFY — each line is "<idx> | label (attempted: entity_class/entity_subclass) | reason":
{entities}

Return strict JSON: {{"classifications": [{{"idx": <int>, "entity_subclass": "<key>"}}, ...]}} — one \
entry per entity you could confidently classify, omitting the rest. Return JSON only, no prose."""


# A single-call classification prompt degrades as row count grows — more
# candidates to weigh per row on top of a larger overall response to hold
# together, and a big real-world rejected batch (chunked extraction over a
# long document, no cross-chunk dedup on rejections) easily runs past 90
# rows. Chunking here mirrors chunking.chunk_text's role for extraction
# itself: bound each LLM call's scope instead of asking one call to reason
# over the whole set at once.
_CLASSIFY_CHUNK_SIZE = 25


def _format_classify_lines(rejected_entities: list[dict], start: int) -> list[str]:
    lines = []
    for offset, r in enumerate(rejected_entities):
        idx = start + offset
        row = r.get("row", {})
        label = row.get("label") or row.get("entity_id") or f"row {idx}"
        attempted = f"{row.get('entity_class', '?')}/{row.get('entity_subclass', '?')}"
        lines.append(f"{idx} | {label} (attempted: {attempted}) | {r.get('reason', '')}")
    return lines


async def classify_rejected_entities(session: AsyncSession, rejected_entities: list[dict]) -> list[dict]:
    """For entities rejected only because their proposed entity_subclass wasn't
    a real ontology key, ask the LLM to pick the closest real leaf key using
    the label + originally-attempted class/subclass as context — the bulk-
    create counterpart to picking a subclass by hand for every row. Processes
    rejected_entities in chunks of _CLASSIFY_CHUNK_SIZE (one LLM call per
    chunk) rather than one call for the whole set. Returns
    [{"idx": <index into rejected_entities>, "entity_subclass": <key>,
    "entity_class": <root>}], one entry per row it could confidently
    classify; a row it can't match is simply omitted, left for the reviewer
    to pick manually. Raises LLMError untouched if any chunk's LLM call
    fails — chunks already classified before the failure are lost with it,
    since a partial result silently hiding a real failure is worse than
    surfacing it and letting the reviewer retry.
    """
    if not rejected_entities:
        return []

    class_keys, _, _ = await _fetch_ontology_vocab(session)
    valid_keys = set(class_keys)

    results = []
    for start in range(0, len(rejected_entities), _CLASSIFY_CHUNK_SIZE):
        chunk = rejected_entities[start : start + _CLASSIFY_CHUNK_SIZE]
        lines = _format_classify_lines(chunk, start)
        prompt = CLASSIFY_PROMPT_TEMPLATE.format(
            entity_subclasses="\n".join(class_keys), entities="\n".join(lines)
        )
        raw = await complete_json(prompt, "Classify the entities listed above.")

        valid_indices = set(range(start, start + len(chunk)))
        for c in raw.get("classifications", []):
            idx = c.get("idx")
            key = c.get("entity_subclass")
            if idx in valid_indices and key in valid_keys:
                results.append({"idx": idx, "entity_subclass": key, "entity_class": key.split(".")[0]})
    return results


async def resolve_rejected(
    session: AsyncSession,
    batch_entities: list[dict],
    rejected_entities: list[dict],
    rejected_links: list[dict],
) -> dict:
    """Backs the review panel's "Create N entities" / "Create N links" buttons.

    Lets the extraction agent map each rejected row to an existing term or
    extend the ontology for it (see ontology_extension), then falls back to
    the leaf-key classifier for any entity still unplaced. Returns
    {"classifications": [{idx, entity_subclass, entity_class}],
     "link_types": [{idx, link_type}], "added_classes": [...], "added_links": [...]}
    where idx indexes the passed rejected_* lists. Ontology extension failing
    is non-fatal; the classifier's LLMError still propagates.
    """
    endpoint_info = {e["entity_id"]: f"{e['label']} ({e['entity_class']})" for e in batch_entities}
    for r in rejected_entities:
        row = r.get("row", {})
        if row.get("entity_id"):
            endpoint_info[row["entity_id"]] = f"{row.get('label', '?')} ({row.get('entity_class', '?')})"

    ext = {"entity_assignments": {}, "link_assignments": {}, "added_classes": [], "added_links": []}
    if rejected_entities or rejected_links:
        try:
            ext = await extend_ontology(session, rejected_entities, rejected_links, endpoint_info)
        except LLMError as e:
            logger.warning("ontology extension skipped: %s", e)

    classifications = [
        {"idx": idx, "entity_subclass": key, "entity_class": key.split(".")[0]}
        for idx, key in ext["entity_assignments"].items()
    ]
    unplaced = [i for i in range(len(rejected_entities)) if i not in ext["entity_assignments"]]
    if unplaced:
        fallback = await classify_rejected_entities(session, [rejected_entities[i] for i in unplaced])
        classifications += [{**c, "idx": unplaced[c["idx"]]} for c in fallback]

    return {
        "classifications": sorted(classifications, key=lambda c: c["idx"]),
        "link_types": [
            {"idx": idx, "link_type": lt} for idx, lt in sorted(ext["link_assignments"].items())
        ],
        "added_classes": ext["added_classes"],
        "added_links": ext["added_links"],
    }


async def extract_from_text(session: AsyncSession, text: str) -> dict:
    """Single-shot extraction for pasted scenario text: validate via
    _extract_and_validate, then persist as a standalone proposed
    IngestBatch (unlike document_ingest.process_document, which merges
    several chunks' results into one batch before persisting).
    """
    result = await _extract_and_validate(session, text)
    batch_id = f"B-{uuid.uuid4().hex[:8]}"
    batch = {
        "batch_id": batch_id,
        "status": "proposed",
        "source_text": text,
        "entities": result["valid_entities"],
        "links": result["valid_links"],
        "rejected_entities": result["rejected_entities"],
        "rejected_links": result["rejected_links"],
    }
    await session.run(
        """
        CREATE (b:IngestBatch {batch_id: $batch_id, status: $status, source_text: $source_text,
                                entities: $entities, links: $links,
                                rejected_entities: $rejected_entities, rejected_links: $rejected_links})
        """,
        batch_id=batch_id,
        status="proposed",
        source_text=text,
        entities=json.dumps(result["valid_entities"]),
        links=json.dumps(result["valid_links"]),
        rejected_entities=json.dumps(result["rejected_entities"]),
        rejected_links=json.dumps(result["rejected_links"]),
    )
    return batch
