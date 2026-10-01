"""Let the extraction agent grow the ontology when nothing in it fits.

When extracted entities/links are rejected because the ontology has no
suitable class or link type (e.g. a "bankruptcy filing" in a military
taxonomy), one LLM call either maps each rejected row to an existing key or
proposes a NEW class / link type. Every proposal is validated strictly here
- the model never writes to the graph directly - and anything created is
tagged origin="extraction" so it can be found and reviewed or removed later.

Guard rails: new classes must hang under an existing parent, names/types must
match a fixed pattern and must not collide with existing keys or inverses,
domain/range must be real root classes, and the number of additions per call
is capped so one bad response cannot flood the ontology.
"""
import logging
import re

from neo4j import AsyncSession

from app.llm.client import complete_json

logger = logging.getLogger(__name__)

CLASS_NAME_RE = re.compile(r"^[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)*$")
LINK_TYPE_RE = re.compile(r"^[a-z][a-z0-9]*(?:_[a-z0-9]+)*$")
MAX_NAME_LEN = 48
MAX_DEPTH = 5
MAX_NEW_CLASSES = 12
MAX_NEW_LINKS = 12
BATCH_SIZE = 25  # rejected rows per LLM call
ORIGIN = "extraction"

PROMPT = """You maintain the ontology of an entity/relationship knowledge graph. Items \
extracted from a document were REJECTED because the ontology has no suitable class or \
link type for them. For each numbered item, either map it to an EXISTING key/type, or \
propose a NEW class / link type so it can be accepted.

Rules:
- Prefer an existing key. Only propose a new one when nothing existing fits well.
- New class: pick an existing parent_key from ENTITY_CLASSES and a short UPPER_SNAKE_CASE \
name for the specific kind of thing (parent "EVENT.TRANSACTION" + name "BANKRUPTCY_FILING"). \
The name must NOT repeat the parent. Refer to it in entity_assignments as "<parent_key>.<name>".
- New link type: lower_snake_case forward-direction verb phrase (e.g. "acquired"), a domain \
and range made of ROOT_CLASSES separated by "; " (or "Any"), an optional inverse phrase, \
and a one-line note. Domain/range must match the real endpoints' root classes.
- Be conservative: reuse and generalise rather than inventing near-duplicates.

ENTITY_CLASSES (all valid parent / assignment keys):
{classes}

ROOT_CLASSES: {roots}

LINK_TYPES (existing, "type (domain -> range)"):
{links}

REJECTED ENTITIES - "<idx> | label | attempted class/subclass | reason":
{entities}

REJECTED LINKS - "<idx> | link_type | source (root class) -> target (root class) | reason":
{link_rows}

Return strict JSON only:
{{"new_classes": [{{"parent_key": "...", "name": "...", "notes": "..."}}],
 "new_link_types": [{{"type": "...", "domain": "...", "range": "...", "inverse": "...", "notes": "..."}}],
 "entity_assignments": [{{"idx": 0, "entity_subclass": "<existing key or parent_key.NAME>"}}],
 "link_assignments": [{{"idx": 0, "link_type": "<existing or new link type>"}}]}}
Omit any item you cannot place confidently."""


def normalize_class_name(name: object) -> str | None:
    if not isinstance(name, str):
        return None
    n = re.sub(r"[\s\-]+", "_", name.strip()).upper()
    return n if CLASS_NAME_RE.match(n) and len(n) <= MAX_NAME_LEN else None


def normalize_link_type(value: object) -> str | None:
    """'Headquartered In (Company -> Place)' -> 'headquartered_in'."""
    if not isinstance(value, str):
        return None
    head = re.sub(r"\s*\(.*$", "", value.strip())  # drop a "(Domain -> Range)" suffix
    n = re.sub(r"[\s\-]+", "_", head).lower()
    return n if LINK_TYPE_RE.match(n) and len(n) <= MAX_NAME_LEN else None


def validate_new_class(item: object, all_keys: set[str]) -> dict | None:
    if not isinstance(item, dict):
        return None
    parent = item.get("parent_key")
    name = normalize_class_name(item.get("name"))
    if not isinstance(parent, str) or parent not in all_keys or name is None:
        return None
    key = f"{parent}.{name}"
    if key in all_keys or key.count(".") + 1 > MAX_DEPTH:
        return None
    notes = item.get("notes")
    return {
        "key": key,
        "parent_key": parent,
        "level": key.count(".") + 1,
        "label": name.replace("_", " ").title(),
        "notes": notes.strip()[:300] if isinstance(notes, str) and notes.strip() else None,
        "origin": ORIGIN,
    }


def _canonical_classes(value: object, roots: dict[str, str]) -> str | None:
    """'organization; PERSON' -> 'Organization; Person'; None if any part is unknown."""
    if not isinstance(value, str) or not value.strip():
        return None
    if value.strip().lower() == "any":
        return "Any"
    parts = [p.strip().lower() for p in value.split(";") if p.strip()]
    if not parts or any(p not in roots for p in parts):
        return None
    return "; ".join(dict.fromkeys(roots[p] for p in parts))


def validate_new_link(item: object, taken: set[str], roots: dict[str, str]) -> dict | None:
    """`taken` = every existing link type AND inverse phrase; `roots` maps
    lower-cased root labels to their canonical label."""
    if not isinstance(item, dict):
        return None
    link_type = normalize_link_type(item.get("type"))
    domain = _canonical_classes(item.get("domain"), roots)
    range_ = _canonical_classes(item.get("range"), roots)
    if link_type is None or link_type in taken or domain is None or range_ is None:
        return None
    inverse = normalize_link_type(item.get("inverse")) if item.get("inverse") else None
    if inverse is not None and (inverse in taken or inverse == link_type):
        inverse = None  # drop a clashing inverse rather than reject the link type
    notes = item.get("notes")
    return {
        "type": link_type,
        "category": "Extracted",
        "domain": domain,
        "range": range_,
        "directionality": "Directed",
        "inverse": inverse,
        "symmetric": "No",
        "transitive": "No",
        "notes": notes.strip()[:300] if isinstance(notes, str) and notes.strip() else None,
        "origin": ORIGIN,
    }


async def _load(session: AsyncSession) -> tuple[set[str], dict[str, str], list[dict], set[str]]:
    keys_res = await session.run("MATCH (c:ClassDef) RETURN c.key AS key, c.parent_key AS parent")
    keys: set[str] = set()
    roots: dict[str, str] = {}
    async for r in keys_res:
        keys.add(r["key"])
        if r["parent"] is None:
            roots[r["key"].lower().replace("_", " ")] = r["key"].replace("_", " ").title()
    links_res = await session.run(
        "MATCH (l:LinkDef) RETURN l.type AS type, l.domain AS domain, l.range AS range, "
        "l.inverse AS inverse ORDER BY l.type"
    )
    links = [dict(r) async for r in links_res]
    taken = {l["type"] for l in links} | {l["inverse"] for l in links if l["inverse"]}
    return keys, roots, links, taken


async def _persist(session: AsyncSession, classes: list[dict], links: list[dict]) -> None:
    if classes:
        await session.run(
            "UNWIND $rows AS row MERGE (c:ClassDef {key: row.key}) ON CREATE SET c += row",
            rows=classes,
        )
        await session.run(
            """
            MATCH (c:ClassDef) WHERE c.key IN $keys
            MATCH (p:ClassDef {key: c.parent_key})
            MERGE (c)-[:SUBCLASS_OF]->(p)
            """,
            keys=[c["key"] for c in classes],
        )
    if links:
        await session.run(
            "UNWIND $rows AS row MERGE (l:LinkDef {type: row.type}) ON CREATE SET l += row",
            rows=links,
        )


def _entity_lines(rows: list[dict], start: int) -> list[str]:
    out = []
    for offset, r in enumerate(rows):
        row = r.get("row", {})
        label = row.get("label") or row.get("entity_id") or "?"
        attempted = f"{row.get('entity_class', '?')}/{row.get('entity_subclass', '?')}"
        out.append(f"{start + offset} | {label} | {attempted} | {r.get('reason', '')}")
    return out


def _link_lines(rows: list[dict], start: int, endpoint: dict[str, str]) -> list[str]:
    out = []
    for offset, r in enumerate(rows):
        row = r.get("row", {})
        src, tgt = row.get("source_entity"), row.get("target_entity")
        out.append(
            f"{start + offset} | {row.get('link_type', '?')} | "
            f"{endpoint.get(src, src)} -> {endpoint.get(tgt, tgt)} | {r.get('reason', '')}"
        )
    return out


async def extend_ontology(
    session: AsyncSession,
    rejected_entities: list[dict],
    rejected_links: list[dict],
    endpoint_info: dict[str, str] | None = None,
) -> dict:
    """Map rejected rows to existing or newly created ontology terms.

    `endpoint_info` maps entity_id -> "Label (ROOT)" for readable link lines.
    Returns {"entity_assignments": {idx: key}, "link_assignments": {idx: type},
    "added_classes": [keys], "added_links": [types]}. idx indexes the passed
    lists. LLMError from the model call propagates; callers decide whether
    that is fatal.
    """
    endpoint = endpoint_info or {}
    result = {
        "entity_assignments": {},
        "link_assignments": {},
        "added_classes": [],
        "added_links": [],
    }
    total = max(len(rejected_entities), len(rejected_links))
    for start in range(0, total, BATCH_SIZE):
        ents = rejected_entities[start : start + BATCH_SIZE]
        lnks = rejected_links[start : start + BATCH_SIZE]
        if not ents and not lnks:
            continue
        keys, roots, link_defs, taken = await _load(session)
        prompt = PROMPT.format(
            classes="\n".join(sorted(keys)),
            roots="; ".join(sorted(roots.values())),
            links="\n".join(f"{l['type']} ({l['domain']} -> {l['range']})" for l in link_defs),
            entities="\n".join(_entity_lines(ents, start)) or "(none)",
            link_rows="\n".join(_link_lines(lnks, start, endpoint)) or "(none)",
        )
        raw = await complete_json(prompt, "Resolve the rejected items listed above.")

        new_classes: list[dict] = []
        seen = set(keys)
        for item in (raw.get("new_classes") or [])[:MAX_NEW_CLASSES]:
            cls = validate_new_class(item, seen)
            if cls:
                new_classes.append(cls)
                seen.add(cls["key"])
        new_links: list[dict] = []
        seen_links = set(taken)
        for item in (raw.get("new_link_types") or [])[:MAX_NEW_LINKS]:
            link = validate_new_link(item, seen_links, roots)
            if link:
                new_links.append(link)
                seen_links.add(link["type"])
                if link["inverse"]:
                    seen_links.add(link["inverse"])
        await _persist(session, new_classes, new_links)
        if new_classes or new_links:
            logger.info(
                "ontology extended by extraction: classes=%s links=%s",
                [c["key"] for c in new_classes],
                [l["type"] for l in new_links],
            )
        result["added_classes"] += [c["key"] for c in new_classes]
        result["added_links"] += [l["type"] for l in new_links]

        valid_ent_idx = set(range(start, start + len(ents)))
        valid_link_idx = set(range(start, start + len(lnks)))
        for a in raw.get("entity_assignments") or []:
            idx, key = (a.get("idx"), a.get("entity_subclass")) if isinstance(a, dict) else (None, None)
            if isinstance(idx, int) and idx in valid_ent_idx and isinstance(key, str) and key in seen:
                result["entity_assignments"][idx] = key
        known_types = seen_links | {l["type"] for l in link_defs}
        for a in raw.get("link_assignments") or []:
            idx, lt = (a.get("idx"), normalize_link_type(a.get("link_type"))) if isinstance(a, dict) else (None, None)
            if isinstance(idx, int) and idx in valid_link_idx and lt is not None and lt in known_types:
                result["link_assignments"][idx] = lt
    return result
