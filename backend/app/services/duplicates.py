"""Possible-duplicate detection and resolution for a proposed ingest batch.

Detection is deterministic (no model call - small self-hosted models are slow
and non-repeatable): within the same entity_class, a proposed entity is
flagged when it matches an entity already in the graph, or an earlier entity
in the same batch, by

  same_id    its entity_id already exists in the graph. Extraction mints ids
             like "P-temp1", so committing would silently OVERWRITE that entity
  same_label label equal after case/accent/punctuation normalisation
  alias      a label/alias of one equals a label/alias of the other
  reordered  same words in a different order ("Petrov Ivan" / "Ivan Petrov")
  similar    near-identical spelling (difflib ratio >= 0.9)

A human then decides per entity (see apply_decisions):

  keep_separate  it really is a different thing: keep it (given a fresh
                 entity_id if the current one collides) and stop flagging it
  remove         it is the same thing: drop the proposed copy and re-point its
                 links at the entity it duplicates, so no relationship is lost
"""
import difflib
import re
import unicodedata

from neo4j import AsyncSession

ACTIONS = ("keep_separate", "remove")
SIMILARITY_THRESHOLD = 0.9
MIN_SIMILAR_LEN = 5
MAX_MATCHES = 5
GRAPH_CANDIDATE_LIMIT = 5000

_REASON_RANK = {"same_id": 0, "same_label": 1, "alias": 2, "reordered": 3, "similar": 4}


class DuplicateDecisionError(ValueError):
    """A resolve request that cannot be applied; surfaces as HTTP 422."""


def normalize_label(value: object) -> str:
    if not isinstance(value, str):
        return ""
    text = unicodedata.normalize("NFKD", value)
    text = "".join(ch for ch in text if not unicodedata.combining(ch)).casefold()
    return " ".join(re.sub(r"[^\w\s]", " ", text).split())


def _names(entity: dict) -> set[str]:
    names = {normalize_label(entity.get("label"))}
    names.update(normalize_label(a) for a in entity.get("aliases") or [])
    names.discard("")
    return names


def _token_key(name: str) -> str:
    return " ".join(sorted(name.split()))


def match_reason(a: dict, b: dict) -> tuple[str, float] | None:
    """Why a and b look like the same thing, or None. Same entity_class only."""
    if a.get("entity_class") != b.get("entity_class"):
        return None
    label_a, label_b = normalize_label(a.get("label")), normalize_label(b.get("label"))
    if label_a and label_a == label_b:
        return "same_label", 1.0
    names_a, names_b = _names(a), _names(b)
    if names_a & names_b:
        return "alias", 1.0
    if {_token_key(n) for n in names_a} & {_token_key(n) for n in names_b}:
        return "reordered", 0.98
    best = 0.0
    for x in names_a:
        for y in names_b:
            if min(len(x), len(y)) < MIN_SIMILAR_LEN:
                continue
            matcher = difflib.SequenceMatcher(None, x, y, autojunk=False)
            if (
                matcher.real_quick_ratio() >= SIMILARITY_THRESHOLD
                and matcher.quick_ratio() >= SIMILARITY_THRESHOLD
            ):
                best = max(best, matcher.ratio())
    return ("similar", round(best, 3)) if best >= SIMILARITY_THRESHOLD else None


def _summary(entity: dict, where: str) -> dict:
    return {
        "entity_id": entity["entity_id"],
        "label": entity.get("label", ""),
        "aliases": list(entity.get("aliases") or []),
        "entity_class": entity.get("entity_class", ""),
        "entity_subclass": entity.get("entity_subclass", ""),
        "where": where,  # "graph" | "batch"
    }


async def _graph_candidates(session: AsyncSession, entities: list[dict]) -> list[dict]:
    result = await session.run(
        """
        MATCH (e:Entity)
        WHERE e.entity_class IN $classes OR e.entity_id IN $ids
        RETURN e.entity_id AS entity_id, e.label AS label, e.aliases AS aliases,
               e.entity_class AS entity_class, e.entity_subclass AS entity_subclass
        LIMIT $limit
        """,
        classes=sorted({e.get("entity_class") for e in entities if e.get("entity_class")}),
        ids=[e["entity_id"] for e in entities],
        limit=GRAPH_CANDIDATE_LIMIT,
    )
    return [dict(record) async for record in result]


async def find_duplicates(
    session: AsyncSession, entities: list[dict], decisions: dict[str, dict]
) -> list[dict]:
    """Proposed entities that look like duplicates and still need a decision.
    Entities the human already resolved (keep_separate) are not flagged again."""
    if not entities:
        return []
    graph = await _graph_candidates(session, entities)
    graph_by_id = {g["entity_id"]: g for g in graph}

    flagged: list[dict] = []
    for index, entity in enumerate(entities):
        if decisions.get(entity["entity_id"], {}).get("action") == "keep_separate":
            continue
        matches: list[dict] = []

        same_id = graph_by_id.get(entity["entity_id"])
        if same_id is not None:
            matches.append({**_summary(same_id, "graph"), "reason": "same_id", "score": 1.0})
        for other in graph:
            if other["entity_id"] == entity["entity_id"]:
                continue
            found = match_reason(entity, other)
            if found:
                matches.append({**_summary(other, "graph"), "reason": found[0], "score": found[1]})
        # Only compare with EARLIER batch entities so a pair is flagged once.
        for other in entities[:index]:
            found = match_reason(entity, other)
            if found:
                matches.append({**_summary(other, "batch"), "reason": found[0], "score": found[1]})

        if matches:
            matches.sort(key=lambda m: (_REASON_RANK[m["reason"]], -m["score"], m["entity_id"]))
            flagged.append(
                {
                    "entity_id": entity["entity_id"],
                    "label": entity.get("label", ""),
                    "aliases": list(entity.get("aliases") or []),
                    "entity_class": entity.get("entity_class", ""),
                    "entity_subclass": entity.get("entity_subclass", ""),
                    "matches": matches[:MAX_MATCHES],
                }
            )
    return flagged


def _repoint(links: list[dict], old_id: str, new_id: str) -> list[dict]:
    """Re-point links from old_id to new_id; drop self-links and repeats of a
    (type, source, target) triple that the batch already has."""
    out: list[dict] = []
    seen: set[tuple[str, str, str]] = set()
    for link in links:
        link = {
            **link,
            "source_entity": new_id if link["source_entity"] == old_id else link["source_entity"],
            "target_entity": new_id if link["target_entity"] == old_id else link["target_entity"],
        }
        if link["source_entity"] == link["target_entity"]:
            continue
        key = (link["link_type"], link["source_entity"], link["target_entity"])
        if key in seen:
            continue
        seen.add(key)
        out.append(link)
    return out


def apply_decisions(
    entities: list[dict],
    links: list[dict],
    decisions: dict[str, dict],
    requests: list[dict],
    *,
    known_target_ids: set[str],
    unused_id,
) -> tuple[list[dict], list[dict], dict[str, dict]]:
    """Apply human decisions to a batch; returns (entities, links, decisions).

    requests: [{"entity_id", "action", "target_id"?}]. known_target_ids are the
    ids a "remove" may merge into (graph entities that exist); batch entities
    are always valid targets. unused_id(base, taken) returns a fresh id for a
    keep_separate whose id collides with the graph.
    """
    entities = [dict(e) for e in entities]
    decisions = dict(decisions)

    for req in requests:
        entity_id, action = req.get("entity_id"), req.get("action")
        if action not in ACTIONS:
            raise DuplicateDecisionError(f"action must be one of {', '.join(ACTIONS)}")
        current = next((e for e in entities if e["entity_id"] == entity_id), None)
        if current is None:
            raise DuplicateDecisionError(f"entity '{entity_id}' is not in this batch")

        if action == "keep_separate":
            if entity_id in known_target_ids:  # id collides with a graph entity
                taken = {e["entity_id"] for e in entities} | known_target_ids
                new_id = unused_id(entity_id, taken)
                current["entity_id"] = new_id
                links = [
                    {
                        **l,
                        "source_entity": new_id if l["source_entity"] == entity_id else l["source_entity"],
                        "target_entity": new_id if l["target_entity"] == entity_id else l["target_entity"],
                    }
                    for l in links
                ]
                entity_id = new_id
            decisions[entity_id] = {"action": "keep_separate"}
        else:
            target_id = req.get("target_id")
            if not target_id or target_id == entity_id:
                raise DuplicateDecisionError("remove needs target_id: the entity it duplicates")
            in_batch = any(e["entity_id"] == target_id for e in entities)
            if not in_batch and target_id not in known_target_ids:
                raise DuplicateDecisionError(f"target '{target_id}' does not exist")
            entities = [e for e in entities if e["entity_id"] != entity_id]
            links = _repoint(links, entity_id, target_id)
            decisions[entity_id] = {"action": "remove", "into": target_id}
    return entities, links, decisions
