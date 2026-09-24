"""Synonym/duplicate check for manually-created entities: before a new
entity is added to a proposed batch, ask the LLM whether its label is a
synonym or the same real-world thing as an existing entity of the same
class, so 'Automobile' and 'Car' resolve to one entity instead of two.
"""
from neo4j import AsyncSession

from app.llm.client import complete_json

_CANDIDATE_LIMIT = 200

SYNONYM_PROMPT_TEMPLATE = """You are deduplicating entities in an intelligence graph. A new \
entity is about to be created. Decide whether it refers to the SAME real-world thing as one \
of the existing entities below, OR whether its label is a synonym/alias/generic-vs-specific \
variant of the same thing an existing entity already represents — for example "Automobile" \
and "Car" are synonyms of the same kind of thing, "UN" and "United Nations" are the same \
organization. If genuinely different, even if related or similar-sounding, do not match.

EXISTING_ENTITIES — each line is "entity_id | label (aliases)":
{candidates}

Return strict JSON: {{"match_entity_id": "<id>" or null, "reason": "<one sentence>"}}. Return \
JSON only, no prose."""


def _format_candidates(candidates: list[dict]) -> str:
    lines = []
    for c in candidates:
        aliases = f" ({', '.join(c['aliases'])})" if c.get("aliases") else ""
        lines.append(f"{c['entity_id']} | {c['label']}{aliases}")
    return "\n".join(lines)


async def find_synonym_match(
    session: AsyncSession,
    label: str,
    entity_class: str,
    aliases: list[str],
    batch_entities: list[dict],
) -> dict | None:
    """Look for an existing entity — committed, or already staged in this
    batch — of the same entity_class that `label` is a synonym/duplicate
    of. Returns the matching candidate (plus a "reason" key), or None if
    there are no candidates or none match. Propagates LLMError untouched.
    """
    result = await session.run(
        """
        MATCH (e:Entity)
        WHERE e.entity_class = $entity_class
        RETURN e.entity_id AS entity_id, e.label AS label, e.aliases AS aliases,
               e.entity_subclass AS entity_subclass
        ORDER BY e.entity_id
        LIMIT $limit
        """,
        entity_class=entity_class,
        limit=_CANDIDATE_LIMIT,
    )
    candidates = [dict(record) async for record in result]
    candidates += [
        {
            "entity_id": e["entity_id"],
            "label": e["label"],
            "aliases": e.get("aliases", []),
            "entity_subclass": e.get("entity_subclass", ""),
        }
        for e in batch_entities
        if e.get("entity_class") == entity_class
    ]
    if not candidates:
        return None

    system_prompt = SYNONYM_PROMPT_TEMPLATE.format(candidates=_format_candidates(candidates))
    user_prompt = f"New entity: {label} (aliases: {', '.join(aliases) or 'none'})"
    raw = await complete_json(system_prompt, user_prompt)

    match_id = raw.get("match_entity_id")
    if not match_id:
        return None
    match = next((c for c in candidates if c["entity_id"] == match_id), None)
    if match is None:
        return None
    return {**match, "entity_class": entity_class, "reason": raw.get("reason", "")}
