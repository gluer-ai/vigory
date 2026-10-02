"""Turn a sandbox into a *proposed* ingest batch for the normal review-and-commit flow.

Nothing here writes to the real graph. The result is an IngestBatch with status
"proposed"; a person reviews it and commits it through the existing endpoint.

Safety rules (this is what keeps real records from being overwritten - commit
uses MERGE ... SET on the entity id):
- every new entity and link gets a FRESH random id, verified unused; a sandbox's
  own ids (N-..., E-...) are never written to the graph;
- real entities are never modified: edits made to a copied entity or link are
  reported and skipped, only new entities and new links are proposed;
- a new entity the user tied to a real one with "same_as" is treated as that real
  entity (its links are re-pointed to it) instead of creating a duplicate;
- saving again is safe: a new entity that an earlier committed save already created is
  reused (via the sandbox-id -> entity-id map stored on that batch), not created twice;
- links the graph already has are skipped; links to missing entities are skipped;
- only the top level is exported - nested (opened) content has no place in the
  flat graph and is reported as skipped.
"""
import json
import uuid
from dataclasses import dataclass, field

from neo4j import AsyncSession
from pydantic import ValidationError as PydanticError

from app.models.entity import EntityCreate
from app.models.link import LinkCreate
from app.ontology.validate import ValidationError, validate_entity, validate_link

CONFIDENCE = "C3"  # a sandbox proposal is "possibly true / reliability not yet judged"
SAME_AS = "same_as"


@dataclass
class Plan:
    entities: list[dict] = field(default_factory=list)
    links: list[dict] = field(default_factory=list)
    rejected_entities: list[dict] = field(default_factory=list)
    rejected_links: list[dict] = field(default_factory=list)
    merged: list[dict] = field(default_factory=list)  # {"label", "into_id", "into_label"}
    skipped: dict[str, int] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    id_map: dict[str, str] = field(default_factory=dict)  # sandbox node id -> proposed entity id

    def skip(self, reason: str, n: int = 1) -> None:
        if n:
            self.skipped[reason] = self.skipped.get(reason, 0) + n


def _fresh_id(prefix: str, taken: set[str]) -> str:
    while True:
        candidate = f"{prefix}-{uuid.uuid4().hex[:12]}"
        if candidate not in taken:
            taken.add(candidate)
            return candidate


def plan_export(
    nodes: list[dict],
    edges: list[dict],
    sandbox_name: str,
    taken_ids: set[str],
    real_ids: set[str] | None = None,
    saved: dict[str, str] | None = None,
) -> Plan:
    """Pure planning step (no database): decide what to propose. `taken_ids` are
    ids that must not be reused (every entity/link id already in the graph, plus
    any the caller reserves). `real_ids` are the copied entities that still exist in the
    graph; when given, anything else is treated as gone (never a merge target, and links
    to it are dropped). None means "assume they all exist" (unit tests). `saved` maps sandbox
    node ids to entity ids created by earlier committed saves of this sandbox; those that
    still exist are reused instead of being created again."""
    plan = Plan()
    source_ref = f"Sandbox: {sandbox_name}"[:200]
    top = [n for n in nodes if n.get("parent") is None]
    top_ids = {n["id"] for n in top}
    plan.skip("inside opened entities/links (not exported)", len(nodes) - len(top) + sum(1 for e in edges if e.get("parent") is not None))
    top_edges = [e for e in edges if e.get("parent") is None]

    graph_nodes = {
        n["id"]: n for n in top if n["origin"] == "graph" and (real_ids is None or n["id"] in real_ids)
    }
    gone = {n["id"] for n in top if n["origin"] == "graph" and n["id"] not in graph_nodes}
    new_nodes = {n["id"]: n for n in top if n["origin"] == "new"}

    # Real entities the user edited: reported, never written back.
    for n in graph_nodes.values():
        base = n.get("base") or {}
        if base and (base.get("label") != n["label"] or base.get("entity_subclass") != n["entity_subclass"]):
            plan.skip("edits to existing entities (real records are never changed)")
    # Real links whose type was changed.
    for e in top_edges:
        if e["origin"] == "graph" and e.get("base_type") not in (None, e["link_type"]):
            plan.skip("changes to existing links (real records are never changed)")

    # "same_as" to exactly one real entity => this new entity IS that entity.
    alias: dict[str, str] = {}
    merge_edges: set[str] = set()
    for nid, n in new_nodes.items():
        twins = {
            (e["target"] if e["source"] == nid else e["source"])
            for e in top_edges
            if e["origin"] == "new" and e["link_type"] == SAME_AS and nid in (e["source"], e["target"])
        }
        real = [t for t in twins if t in graph_nodes]
        if len(real) == 1:
            alias[nid] = real[0]
            merge_edges |= {
                e["id"] for e in top_edges
                if e["link_type"] == SAME_AS and {e["source"], e["target"]} == {nid, real[0]}
            }
            plan.merged.append({"label": n["label"], "into_id": real[0], "into_label": graph_nodes[real[0]]["label"]})
        elif len(real) > 1:
            plan.notes.append(f"'{n['label']}' is marked the same as {len(real)} real entities, so it was kept as a new entity.")

    # Already created by an earlier committed save of this sandbox (and still in the graph).
    for nid, entity_id in (saved or {}).items():
        if nid in new_nodes and nid not in alias and (real_ids is None or entity_id in real_ids):
            alias[nid] = entity_id
            plan.skip("entities already saved to the graph from this sandbox")

    # New entities (those not merged) get fresh ids.
    new_id: dict[str, str] = {}
    for nid, n in new_nodes.items():
        if nid in alias:
            continue
        eid = _fresh_id("E", taken_ids)
        new_id[nid] = eid
        plan.id_map[nid] = eid
        root = n["entity_subclass"].split(".")[0]
        plan.entities.append(
            {
                "entity_id": eid,
                "entity_class": root,
                "entity_subclass": n["entity_subclass"],
                "label": n["label"],
                "aliases": [],
                "status": "active",
                "confidence": CONFIDENCE,
                "source_ref": source_ref,
                "first_observed": None,
                "last_observed": None,
                "attrs": {},
            }
        )

    def resolve(node_id: str) -> str | None:
        if node_id in alias:
            return alias[node_id]
        if node_id in new_id:
            return new_id[node_id]
        if node_id in graph_nodes:
            return node_id
        return None

    seen: set[tuple[str, str, str]] = set()
    for e in top_edges:
        if e["origin"] != "new" or e["id"] in merge_edges:
            continue
        if e["source"] in gone or e["target"] in gone:
            plan.skip("links to entities that are no longer in the knowledge graph")
            continue
        s, t = resolve(e["source"]), resolve(e["target"])
        if s is None or t is None:
            plan.skip("links to something that is not on the top level")
            continue
        if s == t:
            plan.skip("links that became self-links after matching to real entities")
            continue
        key = (s, t, e["link_type"])
        if key in seen:
            plan.skip("duplicate links")
            continue
        seen.add(key)
        plan.links.append(
            {
                "link_id": _fresh_id("L", taken_ids),
                "link_type": e["link_type"],
                "source_entity": s,
                "target_entity": t,
                "direction": "directed",
                "inverse_type": None,
                "valid_from": None,
                "valid_to": None,
                "assertion_status": "reported",
                "confidence": CONFIDENCE,
                "source_ref": source_ref,
                "attrs": {},
            }
        )
    return plan


async def _existing_entities(session: AsyncSession, ids: list[str]) -> dict[str, dict]:
    if not ids:
        return {}
    res = await session.run("MATCH (e:Entity) WHERE e.entity_id IN $ids RETURN e", ids=ids)
    return {r["e"]["entity_id"]: dict(r["e"]) async for r in res}


async def build_batch(session: AsyncSession, sandbox: dict) -> dict:
    """Plan, check against the live graph and ontology, and store a proposed IngestBatch."""
    nodes, edges = sandbox["nodes"], sandbox["edges"]

    # Which copied (real) entities still exist? Decided BEFORE planning, so a vanished one
    # can neither absorb a new entity as a "same_as" twin nor be linked to.
    copied = sorted({n["id"] for n in nodes if n["origin"] == "graph" and n.get("parent") is None})
    saved: dict[str, str] = {}
    res = await session.run(
        "MATCH (b:IngestBatch {sandbox_id: $sid, status: 'committed'}) RETURN b.id_map AS m ORDER BY b.batch_id",
        sid=sandbox["sandbox_id"],
    )
    async for rec in res:
        saved.update(json.loads(rec["m"] or "{}"))
    real_ids = set(await _existing_entities(session, sorted(set(copied) | set(saved.values()))))

    # Ids that must never be reused: everything already in the graph.
    res = await session.run("MATCH (e:Entity) RETURN collect(e.entity_id) AS ids")
    taken = set((await res.single())["ids"])
    res = await session.run("MATCH ()-[r:LINK]->() RETURN collect(r.link_id) AS ids")
    taken |= set((await res.single())["ids"])

    plan = plan_export(nodes, edges, sandbox["name"], taken, real_ids, saved)

    # Real entities a link points at must still exist (they could have been removed).
    needed = {x for l in plan.links for x in (l["source_entity"], l["target_entity"])} - {e["entity_id"] for e in plan.entities}
    real = await _existing_entities(session, sorted(needed))
    class_of = {e["entity_id"]: e["entity_class"] for e in plan.entities}
    class_of.update({i: r["entity_class"] for i, r in real.items()})

    valid_entities: list[dict] = []
    for row in plan.entities:
        try:
            await validate_entity(session, EntityCreate(**row))
            valid_entities.append(row)
        except (ValidationError, PydanticError, ValueError) as e:
            plan.rejected_entities.append({"row": row, "reason": str(e)})
    valid_ids = {e["entity_id"] for e in valid_entities}

    valid_links: list[dict] = []
    for row in plan.links:
        ends = (row["source_entity"], row["target_entity"])
        proposed_ids = {e["entity_id"] for e in plan.entities}
        if any(x in proposed_ids and x not in valid_ids for x in ends):
            plan.rejected_links.append({"row": row, "reason": "an entity this link joins was rejected"})
            continue
        if any(x not in valid_ids and x not in real for x in ends):
            plan.rejected_links.append({"row": row, "reason": "an entity this link joins is no longer in the knowledge graph"})
            continue
        dup = await session.run(
            "MATCH (:Entity {entity_id: $s})-[r:LINK {link_type: $t}]->(:Entity {entity_id: $o}) RETURN r LIMIT 1",
            s=row["source_entity"], t=row["link_type"], o=row["target_entity"],
        )
        if await dup.single() is not None:
            plan.skip("links the graph already has")
            continue
        try:
            await validate_link(session, LinkCreate(**row), class_of[ends[0]], class_of[ends[1]])
            valid_links.append(row)
        except (ValidationError, PydanticError, ValueError) as e:
            plan.rejected_links.append({"row": row, "reason": str(e)})

    batch_id = f"B-{uuid.uuid4().hex[:8]}"
    source_text = f"Sandbox: {sandbox['name']}"
    await session.run(
        """
        CREATE (b:IngestBatch {batch_id: $batch_id, status: 'proposed', source_text: $source_text,
                                entities: $entities, links: $links,
                                rejected_entities: $rejected_entities, rejected_links: $rejected_links,
                                sandbox_id: $sandbox_id, id_map: $id_map})
        """,
        batch_id=batch_id,
        sandbox_id=sandbox["sandbox_id"],
        id_map=json.dumps(plan.id_map),
        source_text=source_text,
        entities=json.dumps(valid_entities),
        links=json.dumps(valid_links),
        rejected_entities=json.dumps(plan.rejected_entities),
        rejected_links=json.dumps(plan.rejected_links),
    )
    return {
        "batch": {
            "batch_id": batch_id,
            "status": "proposed",
            "source_text": source_text,
            "entities": valid_entities,
            "links": valid_links,
            "rejected_entities": plan.rejected_entities,
            "rejected_links": plan.rejected_links,
        },
        "merged": plan.merged,
        # the real entities the proposed links attach to, so the browser can draw them
        "existing_entities": [
            {"entity_id": i, "label": r["label"], "entity_subclass": r["entity_subclass"]}
            for i, r in sorted(real.items())
            if any(i in (l["source_entity"], l["target_entity"]) for l in valid_links)
        ],
        "skipped": [{"reason": k, "count": v} for k, v in sorted(plan.skipped.items())],
        "notes": plan.notes,
    }
