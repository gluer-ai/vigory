"""Sandbox persistence and validation.

Isolation rules (the point of this module):
- Sandboxes live under their own :Sandbox label. Nothing here ever writes an
  :Entity node or a LINK relationship; the real graph is only *read* to seed a
  copy and to check ontology rules.
- Every query filters on the owner id, and a sandbox that exists but belongs
  to someone else is reported exactly like one that doesn't exist.
"""
import json
import uuid
from datetime import datetime, timezone

from fastapi import HTTPException
from neo4j import AsyncSession

from app.models.sandbox import SandboxEdge, SandboxNode, SandboxSave
from app.ontology.validate import _split_class_list
from app.services.scoping import scope_subgraph

MAX_NODES = 300
MAX_EDGES = 1000
MAX_DEPTH = 6  # how many levels deep an entity/relation can be opened
MAX_PER_USER = 50
MAX_STATE_BYTES = 1_000_000


class SandboxInvalid(Exception):
    """A sandbox edit that breaks ontology or size rules (HTTP 422)."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _root(subclass: str) -> str:
    return subclass.split(".")[0]


async def load_ontology(session: AsyncSession) -> tuple[dict[str, str], dict[str, dict]]:
    """(ClassDef key -> lower-cased root label for every key, link type -> LinkDef)."""
    res = await session.run("MATCH (c:ClassDef) RETURN c.key AS key, c.parent_key AS parent, c.label AS label")
    rows = [dict(r) async for r in res]
    root_label = {r["key"]: r["label"].lower() for r in rows if r["parent"] is None}
    classes = {r["key"]: root_label.get(_root(r["key"]), "") for r in rows}
    res = await session.run("MATCH (l:LinkDef) RETURN l.type AS type, l.domain AS domain, l.range AS range")
    links = {r["type"]: {"domain": r["domain"], "range": r["range"]} async for r in res}
    return classes, links


def validate_state(
    nodes: list[SandboxNode],
    edges: list[SandboxEdge],
    classes: dict[str, str],
    links: dict[str, dict],
) -> None:
    """Raise SandboxInvalid unless the whole canvas is consistent with the ontology."""
    if len(nodes) > MAX_NODES:
        raise SandboxInvalid(f"A sandbox holds at most {MAX_NODES} entities")
    if len(edges) > MAX_EDGES:
        raise SandboxInvalid(f"A sandbox holds at most {MAX_EDGES} links")
    ids = [n.id for n in nodes]
    if len(set(ids)) != len(ids):
        raise SandboxInvalid("Duplicate entity id in sandbox")
    edge_ids = [e.id for e in edges]
    if len(set(edge_ids)) != len(edge_ids):
        raise SandboxInvalid("Duplicate link id in sandbox")
    if set(ids) & set(edge_ids):
        raise SandboxInvalid("An entity and a link share the same id")

    # Nesting: every item lives at the top level or inside an existing entity /
    # link, with no cycles and a bounded depth.
    parent_of: dict[str, str | None] = {n.id: n.parent for n in nodes}
    parent_of.update({e.id: e.parent for e in edges})
    for item_id, parent in parent_of.items():
        if parent is not None and parent not in parent_of:
            raise SandboxInvalid(f"'{item_id}' is inside '{parent}', which is not in the sandbox")
        depth, cur = 0, parent
        while cur is not None:
            depth += 1
            if cur == item_id or depth > MAX_DEPTH:
                raise SandboxInvalid(
                    "Items cannot contain themselves" if cur == item_id
                    else f"Nesting is limited to {MAX_DEPTH} levels"
                )
            cur = parent_of[cur]

    root_of = {}
    for n in nodes:
        if n.entity_subclass not in classes:
            raise SandboxInvalid(f"'{n.entity_subclass}' is not a known class (entity '{n.label}')")
        root_of[n.id] = classes[n.entity_subclass]

    for e in edges:
        if e.source == e.target:
            raise SandboxInvalid("A link cannot connect an entity to itself")
        if e.source not in root_of or e.target not in root_of:
            raise SandboxInvalid(f"Link '{e.id}' points at an entity that is not in the sandbox")
        if parent_of[e.source] != e.parent or parent_of[e.target] != e.parent:
            raise SandboxInvalid(
                f"Link '{e.id}' must connect entities on the same level as the link itself"
            )
        definition = links.get(e.link_type)
        if definition is None:
            raise SandboxInvalid(f"'{e.link_type}' is not a known link type")
        for end, role in ((e.source, "domain"), (e.target, "range")):
            allowed = definition["domain" if role == "domain" else "range"]
            if allowed != "Any" and root_of[end] not in _split_class_list(allowed):
                raise SandboxInvalid(
                    f"'{e.link_type}' cannot use a {root_of[end] or 'unknown'} entity as its "
                    f"{'source' if role == 'domain' else 'target'} (allowed: {allowed})"
                )


def _layout(count: int, index: int, is_trigger: bool) -> tuple[float, float]:
    """Trigger in the centre, the rest on concentric rings that grow with count."""
    import math

    if is_trigger:
        return 0.0, 0.0
    ring, per_ring = 0, 10
    while index >= per_ring:
        index -= per_ring
        ring += 1
        per_ring += 8
    radius = 280 + ring * 220
    angle = 2 * math.pi * index / per_ring
    return round(radius * math.cos(angle), 1), round(radius * math.sin(angle), 1)


async def seed_from_graph(session: AsyncSession, trigger_id: str, hops: int) -> tuple[list[dict], list[dict]]:
    """Copy the N-hop neighbourhood of an entity (read-only) as sandbox nodes/edges."""
    sub = await scope_subgraph(session, trigger_id, hops=hops)
    if not sub["nodes"]:
        raise HTTPException(status_code=404, detail=f"Entity '{trigger_id}' not found")
    if len(sub["nodes"]) > MAX_NODES or len(sub["edges"]) > MAX_EDGES:
        raise SandboxInvalid(
            f"That neighbourhood has {len(sub['nodes'])} entities (max {MAX_NODES}); use fewer hops"
        )
    ordered = sorted(sub["nodes"], key=lambda n: (n["entity_id"] != trigger_id, n["label"]))
    nodes = []
    for i, n in enumerate(ordered):
        x, y = _layout(len(ordered), i - 1, n["entity_id"] == trigger_id)
        nodes.append({
            "id": n["entity_id"], "origin": "graph", "label": n["label"],
            "entity_subclass": n["entity_subclass"], "x": x, "y": y,
            "base": {"label": n["label"], "entity_subclass": n["entity_subclass"]},
            "parent": None,
        })
    edges = [
        {"id": e["link_id"], "origin": "graph", "source": e["source_entity"],
         "target": e["target_entity"], "link_type": e["link_type"], "base_type": e["link_type"],
         "parent": None}
        for e in sub["edges"]
    ]
    return nodes, edges


# ---- persistence (always scoped by owner) --------------------------------------


def _summary(s: dict) -> dict:
    return {k: s[k] for k in ("sandbox_id", "name", "version", "node_count", "edge_count", "updated_at")}


def _full(s: dict) -> dict:
    state = json.loads(s["state"])
    return {**_summary(s), "nodes": state["nodes"], "edges": state["edges"]}


async def list_for_owner(session: AsyncSession, owner: str) -> list[dict]:
    res = await session.run(
        "MATCH (s:Sandbox {owner: $owner}) RETURN s ORDER BY s.updated_at DESC", owner=owner
    )
    return [_summary(dict(r["s"])) async for r in res]


async def count_for_owner(session: AsyncSession, owner: str) -> int:
    res = await session.run("MATCH (s:Sandbox {owner: $owner}) RETURN count(s) AS n", owner=owner)
    return (await res.single())["n"]


async def get_for_owner(session: AsyncSession, owner: str, sandbox_id: str) -> dict:
    res = await session.run(
        "MATCH (s:Sandbox {sandbox_id: $id, owner: $owner}) RETURN s", id=sandbox_id, owner=owner
    )
    record = await res.single()
    if record is None:
        raise HTTPException(status_code=404, detail="Sandbox not found")
    return _full(dict(record["s"]))


async def create(session: AsyncSession, owner: str, name: str, nodes: list[dict], edges: list[dict]) -> dict:
    if await count_for_owner(session, owner) >= MAX_PER_USER:
        raise SandboxInvalid(f"You can keep at most {MAX_PER_USER} sandboxes; delete one first")
    now = _now()
    row = {
        "sandbox_id": f"SB-{uuid.uuid4().hex[:12]}", "owner": owner, "name": name, "version": 1,
        "state": json.dumps({"nodes": nodes, "edges": edges}),
        "node_count": len(nodes), "edge_count": len(edges), "created_at": now, "updated_at": now,
    }
    await session.run("CREATE (s:Sandbox) SET s = $row", row=row)
    return _full(row)


async def save(session: AsyncSession, owner: str, sandbox_id: str, body: SandboxSave) -> dict:
    state = json.dumps({
        "nodes": [n.model_dump() for n in body.nodes],
        "edges": [e.model_dump() for e in body.edges],
    })
    if len(state.encode()) > MAX_STATE_BYTES:
        raise SandboxInvalid("Sandbox is too large to save")
    # Compare-and-swap on version: a stale tab cannot silently overwrite newer work.
    res = await session.run(
        """
        MATCH (s:Sandbox {sandbox_id: $id, owner: $owner, version: $version})
        SET s.state = $state, s.version = s.version + 1, s.updated_at = $now,
            s.node_count = $nodes, s.edge_count = $edges, s.name = coalesce($name, s.name)
        RETURN s
        """,
        id=sandbox_id, owner=owner, version=body.version, state=state, now=_now(),
        nodes=len(body.nodes), edges=len(body.edges), name=body.name,
    )
    record = await res.single()
    if record is not None:
        return _full(dict(record["s"]))
    await get_for_owner(session, owner, sandbox_id)  # 404 if missing / not yours
    raise HTTPException(status_code=409, detail="This sandbox was changed elsewhere; reload it")


async def delete(session: AsyncSession, owner: str, sandbox_id: str) -> None:
    res = await session.run(
        "MATCH (s:Sandbox {sandbox_id: $id, owner: $owner}) DELETE s RETURN 1 AS ok",
        id=sandbox_id, owner=owner,
    )
    if await res.single() is None:
        raise HTTPException(status_code=404, detail="Sandbox not found")
