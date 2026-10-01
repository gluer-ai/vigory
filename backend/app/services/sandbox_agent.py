"""Edit a sandbox by asking: chat text or a voice agent -> validated operations.

Both entry points end in `apply_ops`, the only place that changes a sandbox
document, so chat and voice share identical rules:
- operations are plain data (a fixed list of verbs); nothing is executed;
- each one is applied to a copy and the whole canvas is re-validated against
  the ontology (the same `validate_state` the save endpoint runs), so a bad
  operation is skipped with a reason instead of corrupting the sandbox;
- the real graph is only ever *read* (to copy an entity in); it is never written.
The caller owns persistence: the browser saves the returned canvas through the
normal autosave, which keeps the version check and per-user ownership intact.
"""
import json
import logging
import time
import uuid
from typing import Literal

from fastapi import HTTPException
from neo4j import AsyncSession
from pydantic import BaseModel, Field, ValidationError

from app.llm.client import complete_json
from app.models.sandbox import SandboxBase, SandboxEdge, SandboxNode
from app.ontology.validate import _split_class_list
from app.services.extraction_agent import resolve_class_key
from app.services.ontology_extension import normalize_link_type
from app.services.sandbox import SandboxInvalid, validate_state
from app.services.search import search_entities_by_name

logger = logging.getLogger(__name__)

MAX_OPS = 25
MAX_HISTORY = 6
MAX_PROMPT_NODES = 150
MAX_PROMPT_EDGES = 300

OpName = Literal[
    "add_entity", "copy_from_graph", "rename_entity", "reclassify_entity", "remove_entity",
    "add_link", "change_link", "remove_link",
]


class OpError(Exception):
    """One operation could not be applied; the message is shown to the user / model."""


class SandboxOp(BaseModel):
    op: OpName
    entity: str | None = Field(None, max_length=200)
    label: str | None = Field(None, max_length=200)
    entity_subclass: str | None = Field(None, max_length=200)
    entity_id: str | None = Field(None, max_length=80)
    inside: str | None = Field(None, max_length=200)
    source: str | None = Field(None, max_length=200)
    target: str | None = Field(None, max_length=200)
    link: str | None = Field(None, max_length=80)
    link_type: str | None = Field(None, max_length=100)
    from_type: str | None = Field(None, max_length=100)


# ---- lookups -----------------------------------------------------------------------


def _resolve_node(nodes: list[SandboxNode], ref: str | None, container: str | None) -> SandboxNode:
    ref = (ref or "").strip()
    if not ref:
        raise OpError("an entity name or id is required")
    by_id = [n for n in nodes if n.id == ref]
    if by_id:
        return by_id[0]
    matches = [n for n in nodes if n.label.casefold() == ref.casefold()]
    if not matches:
        raise OpError(f"no entity named '{ref}' in this sandbox")
    pool = [n for n in matches if n.parent == container] or matches
    if len(pool) > 1:
        raise OpError(f"'{ref}' matches {len(pool)} entities; use an id: " + ", ".join(n.id for n in pool[:5]))
    return pool[0]


def _resolve_container(
    nodes: list[SandboxNode], edges: list[SandboxEdge], ref: str | None, current: str | None
) -> str | None:
    """Where a new entity goes: omitted -> the level on screen; 'top' -> the top level."""
    ref = (ref or "").strip()
    if not ref:
        return current
    if ref.casefold() in {"top", "top level", "root", "top-level"}:
        return None
    if any(e.id == ref for e in edges):
        return ref
    return _resolve_node(nodes, ref, current).id


def _resolve_edge(
    nodes: list[SandboxNode], edges: list[SandboxEdge], op: SandboxOp, container: str | None
) -> SandboxEdge:
    if op.link:
        found = [e for e in edges if e.id == op.link.strip()]
        if not found:
            raise OpError(f"no link with id '{op.link}'")
        return found[0]
    if not (op.source and op.target):
        raise OpError("name the link by its id, or by its source and target entities")
    s, t = _resolve_node(nodes, op.source, container), _resolve_node(nodes, op.target, container)
    cands = [e for e in edges if {e.source, e.target} == {s.id, t.id}]
    current = normalize_link_type(op.from_type or (op.link_type if op.op == "remove_link" else None))
    if current:
        cands = [e for e in cands if e.link_type == current]
    if not cands:
        raise OpError(f"no link between '{s.label}' and '{t.label}'" + (f" of type '{current}'" if current else ""))
    if len(cands) > 1:
        raise OpError(
            f"'{s.label}' and '{t.label}' have {len(cands)} links ({', '.join(e.link_type for e in cands)}); "
            "say which one (from_type) or use its id"
        )
    return cands[0]


def _dependents(nodes: list[SandboxNode], edges: list[SandboxEdge], ids: set[str]) -> set[str]:
    """`ids` plus attached links and (recursively) everything nested inside."""
    gone = set(ids)
    grew = True
    while grew:
        grew = False
        for e in edges:
            if e.id not in gone and (e.source in gone or e.target in gone or e.parent in gone):
                gone.add(e.id)
                grew = True
        for n in nodes:
            if n.id not in gone and n.parent in gone:
                gone.add(n.id)
                grew = True
    return gone


def _free_position(nodes: list[SandboxNode], parent: str | None) -> tuple[float, float]:
    level = [n for n in nodes if n.parent == parent]
    for slot in range(400):
        x, y = float((slot % 4) * 260), float((slot // 4) * 170)
        if all(abs(n.x - x) >= 200 or abs(n.y - y) >= 110 for n in level):
            return x, y
    return 0.0, 0.0


def _class_key(value: str | None, classes: dict[str, str]) -> str:
    key = resolve_class_key(value, set(classes))
    if key is None:
        raise OpError(f"'{value}' is not a known class; use a full class key from the ontology")
    return key


def valid_link_types(classes: dict[str, str], links: dict[str, dict], src_sub: str, tgt_sub: str) -> list[str]:
    s, t = classes.get(src_sub, ""), classes.get(tgt_sub, "")
    ok = lambda allowed, root: allowed == "Any" or root in _split_class_list(allowed)  # noqa: E731
    return sorted(k for k, d in links.items() if ok(d["domain"], s) and ok(d["range"], t))


def _check_link_type(
    raw: str | None, src: SandboxNode, tgt: SandboxNode, classes: dict[str, str], links: dict[str, dict]
) -> str:
    link_type = normalize_link_type(raw)
    if link_type is None or link_type not in links:
        raise OpError(f"'{raw}' is not a known link type")
    if link_type not in valid_link_types(classes, links, src.entity_subclass, tgt.entity_subclass):
        options = valid_link_types(classes, links, src.entity_subclass, tgt.entity_subclass)
        raise OpError(
            f"'{link_type}' cannot connect '{src.label}' to '{tgt.label}'. "
            f"Valid types: {', '.join(options[:12]) or 'none'}"
        )
    return link_type


# ---- the real graph (read-only) ----------------------------------------------------


async def _load_graph_entity(session: AsyncSession, entity_id: str) -> dict | None:
    res = await session.run("MATCH (e:Entity {entity_id: $id}) RETURN e", id=entity_id)
    rec = await res.single()
    return dict(rec["e"]) if rec else None


async def _load_graph_links(session: AsyncSession, entity_id: str) -> list[dict]:
    res = await session.run(
        "MATCH (:Entity {entity_id: $id})-[r:LINK]-(:Entity) RETURN DISTINCT r", id=entity_id
    )
    return [dict(r["r"]) async for r in res]


async def _find_graph_entity(session: AsyncSession, op: SandboxOp) -> dict:
    if op.entity_id:
        found = await _load_graph_entity(session, op.entity_id.strip())
        if found is None:
            raise OpError(f"no entity '{op.entity_id}' in the knowledge graph")
        return found
    name = (op.label or op.entity or "").strip()
    if not name:
        raise OpError("give the name or id of the knowledge-graph entity to copy")
    hits = await search_entities_by_name(session, name, 6)
    exact = [h for h in hits if h["label"].casefold() == name.casefold()]
    pool = exact or hits
    if not pool:
        raise OpError(f"nothing in the knowledge graph matches '{name}'")
    if len(pool) > 1:
        raise OpError(
            f"'{name}' matches several graph entities: "
            + "; ".join(f"{h['label']} ({h['entity_id']})" for h in pool[:5])
            + ". Say which one."
        )
    return pool[0]


# ---- applying one operation ----------------------------------------------------------


def _new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


async def _apply_one(
    session: AsyncSession,
    nodes: list[SandboxNode],
    edges: list[SandboxEdge],
    op: SandboxOp,
    container: str | None,
    classes: dict[str, str],
    links: dict[str, dict],
) -> tuple[list[SandboxNode], list[SandboxEdge], str]:
    if op.op == "add_entity":
        label = (op.label or op.entity or "").strip()
        if not label:
            raise OpError("a name is required for the new entity")
        sub = _class_key(op.entity_subclass, classes)
        parent = _resolve_container(nodes, edges, op.inside, container)
        x, y = _free_position(nodes, parent)
        node = SandboxNode(id=_new_id("N"), origin="new", label=label, entity_subclass=sub, x=x, y=y, parent=parent)
        return nodes + [node], edges, f"Added '{label}' ({sub.split('.')[-1].replace('_', ' ').lower()})"

    if op.op == "copy_from_graph":
        real = await _find_graph_entity(session, op)
        taken = next((n for n in nodes if n.id == real["entity_id"]), None)
        if taken:
            raise OpError(f"'{taken.label}' is already in this sandbox")
        parent = _resolve_container(nodes, edges, op.inside, container)
        x, y = _free_position(nodes, parent)
        node = SandboxNode(
            id=real["entity_id"], origin="graph", label=real["label"], entity_subclass=real["entity_subclass"],
            x=x, y=y, parent=parent,
            base=SandboxBase(label=real["label"], entity_subclass=real["entity_subclass"]),
        )
        level = {n.id for n in nodes if n.parent == parent}
        used = {n.id for n in nodes} | {e.id for e in edges}
        pulled = [
            SandboxEdge(
                id=r["link_id"], origin="graph", source=r["source_entity"], target=r["target_entity"],
                link_type=r["link_type"], base_type=r["link_type"], parent=parent,
            )
            for r in await _load_graph_links(session, real["entity_id"])
            if r["link_id"] not in used
            and {r["source_entity"], r["target_entity"]} - {real["entity_id"]} <= level
            and r["source_entity"] != r["target_entity"]
        ]
        extra = f" with {len(pulled)} existing link{'s' if len(pulled) != 1 else ''}" if pulled else ""
        return nodes + [node], edges + pulled, f"Copied '{real['label']}' from the knowledge graph{extra}"

    if op.op in ("rename_entity", "reclassify_entity", "remove_entity"):
        target = _resolve_node(nodes, op.entity, container)
        if op.op == "remove_entity":
            gone = _dependents(nodes, edges, {target.id})
            return (
                [n for n in nodes if n.id not in gone],
                [e for e in edges if e.id not in gone],
                f"Removed '{target.label}'" + (f" and {len(gone) - 1} connected/nested item(s)" if len(gone) > 1 else ""),
            )
        if op.op == "rename_entity":
            new = (op.label or "").strip()
            if not new:
                raise OpError("the new name is required")
            return [n.model_copy(update={"label": new}) if n.id == target.id else n for n in nodes], edges, \
                f"Renamed '{target.label}' to '{new}'"
        sub = _class_key(op.entity_subclass, classes)
        return [n.model_copy(update={"entity_subclass": sub}) if n.id == target.id else n for n in nodes], edges, \
            f"Changed '{target.label}' to {sub.split('.')[-1].replace('_', ' ').lower()}"

    if op.op == "add_link":
        s, t = _resolve_node(nodes, op.source, container), _resolve_node(nodes, op.target, container)
        if s.id == t.id:
            raise OpError("a link needs two different entities")
        if s.parent != t.parent:
            raise OpError(f"'{s.label}' and '{t.label}' are on different levels; links join entities on the same level")
        link_type = _check_link_type(op.link_type, s, t, classes, links)
        edge = SandboxEdge(id=_new_id("E"), origin="new", source=s.id, target=t.id, link_type=link_type, parent=s.parent)
        return nodes, edges + [edge], f"Linked '{s.label}' {link_type} '{t.label}'"

    # change_link / remove_link
    edge = _resolve_edge(nodes, edges, op, container)
    by_id = {n.id: n for n in nodes}
    if op.op == "remove_link":
        gone = _dependents(nodes, edges, {edge.id})
        return (
            [n for n in nodes if n.id not in gone],
            [e for e in edges if e.id not in gone],
            f"Removed the '{edge.link_type}' link between '{by_id[edge.source].label}' and '{by_id[edge.target].label}'",
        )
    link_type = _check_link_type(op.link_type, by_id[edge.source], by_id[edge.target], classes, links)
    return nodes, [e.model_copy(update={"link_type": link_type}) if e.id == edge.id else e for e in edges], \
        f"Changed the link between '{by_id[edge.source].label}' and '{by_id[edge.target].label}' from '{edge.link_type}' to '{link_type}'"


async def apply_ops(
    session: AsyncSession,
    nodes: list[SandboxNode],
    edges: list[SandboxEdge],
    ops: list[SandboxOp],
    container: str | None,
    classes: dict[str, str],
    links: dict[str, dict],
) -> tuple[list[SandboxNode], list[SandboxEdge], list[dict]]:
    """Apply ops in order. Each is validated against the whole canvas; a failing
    one is skipped (with the reason) and later ones still run."""
    results: list[dict] = []
    cur_nodes, cur_edges = list(nodes), list(edges)
    for op in ops[:MAX_OPS]:
        try:
            n2, e2, message = await _apply_one(session, cur_nodes, cur_edges, op, container, classes, links)
            validate_state(n2, e2, classes, links)
        except (OpError, SandboxInvalid, ValidationError) as e:
            results.append({"op": op.op, "ok": False, "message": str(e).splitlines()[0]})
            continue
        cur_nodes, cur_edges = n2, e2
        results.append({"op": op.op, "ok": True, "message": message})
    for op in ops[MAX_OPS:]:
        results.append({"op": op.op, "ok": False, "message": f"Skipped: at most {MAX_OPS} changes per request"})
    return cur_nodes, cur_edges, results


def parse_ops(raw: object) -> tuple[list[SandboxOp], list[dict]]:
    """Model output -> ops. Malformed entries are reported, not fatal."""
    ops, problems = [], []
    for item in raw if isinstance(raw, list) else []:
        try:
            ops.append(SandboxOp(**item) if isinstance(item, dict) else SandboxOp.model_validate(item))
        except ValidationError:
            name = item.get("op") if isinstance(item, dict) else None
            problems.append({"op": str(name or "?")[:40], "ok": False, "message": "Skipped an unrecognised change"})
    return ops, problems


# ---- chat ----------------------------------------------------------------------------

OPS_DOC = """OPERATIONS:
- add_entity: label, entity_subclass (full class key), optional inside (an entity name/id or link id; \
omit = the level on screen; "top" = the top level)
- copy_from_graph: label (name of a real knowledge-graph entity) or entity_id - copies a real entity in
- rename_entity: entity (name or id), label (the new name)
- reclassify_entity: entity, entity_subclass (full class key)
- remove_entity: entity   (also removes its links and anything nested inside it)
- add_link: source, target (entity names or ids), link_type
- change_link: source, target, link_type (the new type), optional from_type (the current type, if the pair \
has several links); or link (a link id) instead of source/target
- remove_link: source, target; or link (a link id)

RULES:
- Refer to entities by the exact name or id shown under the current sandbox; never invent ids.
- entity_subclass: copy a full dotted key from ENTITY_CLASSES exactly; pick the closest one.
- link_type: copy exactly from LINK_TYPES. Its domain/range must accept the root classes of the two \
entities. A link joins two entities on the SAME level.
- Do only what was asked. If the request is unclear or cannot be done, make no changes and ask one short \
question (or explain)."""

_CANVAS_INTRO = """You edit a private "what-if" sandbox: a working copy of part of a knowledge graph shown \
as a canvas where entities are boxes and links connect them. Entities and links can be nested (opened) \
inside other entities or links. You never touch the real graph. """


def _ontology_lists(classes: dict[str, str], links: dict[str, dict]) -> str:
    parents = {k.rsplit(".", 1)[0] for k in classes if "." in k}
    leaves = sorted(k for k in classes if k not in parents)
    return (
        "ENTITY_CLASSES:\n" + "\n".join(leaves)
        + '\n\nLINK_TYPES ("type (domain -> range)"):\n'
        + "\n".join(f"{t} ({d['domain']} -> {d['range']})" for t, d in sorted(links.items()))
    )


def chat_prompt(classes: dict[str, str], links: dict[str, dict]) -> str:
    return (
        _CANVAS_INTRO
        + "Turn the user's request into a short list of operations; the system validates and applies them.\n\n"
        + OPS_DOC
        + '\n- Return them in "ops" in the order to apply, as objects like {"op": "rename_entity", '
        '"entity": "Acme", "label": "Acme Corp"}.\n- "reply": one or two plain sentences saying what you did.\n\n'
        + _ontology_lists(classes, links)
        + '\n\nReturn strict JSON only: {"reply": "...", "ops": [...]}'
    )


def voice_instructions(classes: dict[str, str], links: dict[str, dict]) -> str:
    return (
        _CANVAS_INTRO
        + "You are the VOICE assistant for it. Speak briefly and naturally, one or two short sentences.\n\n"
        "Tools: get_sandbox (read the canvas), edit_sandbox (apply operations), search_knowledge_graph (find "
        "real entities). Call get_sandbox first if you are not sure what is on the canvas. After edit_sandbox, "
        "say what changed in plain words; if a change failed, say why and offer the fix (the result lists "
        "valid link types). Never read ids aloud. Do not claim a change happened unless the tool result says "
        "it succeeded.\n\n"
        + OPS_DOC
        + "\n\n"
        + _ontology_lists(classes, links)
    )


def voice_tool_definitions() -> list[dict]:
    op_props = {k: {"type": "string"} for k in SandboxOp.model_fields if k != "op"}
    op_props["op"] = {"type": "string", "enum": list(OpName.__args__)}
    return [
        {
            "type": "function",
            "name": "get_sandbox",
            "description": "Read what is on the sandbox canvas right now: the level on screen, entities and links.",
            "parameters": {"type": "object", "properties": {}},
        },
        {
            "type": "function",
            "name": "edit_sandbox",
            "description": (
                "Change the sandbox canvas. Pass a list of operations; they are validated against the "
                "ontology and applied in order. Returns which succeeded and which failed (with the reason)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "ops": {
                        "type": "array",
                        "items": {"type": "object", "properties": op_props, "required": ["op"]},
                    }
                },
                "required": ["ops"],
            },
        },
    ]


def _level_name(nodes: list[SandboxNode], edges: list[SandboxEdge], container: str | None) -> str:
    if container is None:
        return "top level"
    by_id = {n.id: n.label for n in nodes}
    edge = next((e for e in edges if e.id == container), None)
    if edge:
        return f'inside the link "{by_id.get(edge.source, "?")} {edge.link_type} {by_id.get(edge.target, "?")}"'
    return f'inside "{by_id.get(container, container)}"'


def describe_sandbox(nodes: list[SandboxNode], edges: list[SandboxEdge], container: str | None) -> str:
    by_id = {n.id: n.label for n in nodes}
    where = lambda p: "top" if p is None else by_id.get(p, p)  # noqa: E731
    lines = [f"ON SCREEN NOW: {_level_name(nodes, edges, container)}", "ENTITIES (id | name | class | level):"]
    lines += [f"{n.id} | {n.label} | {n.entity_subclass} | {where(n.parent)}" for n in nodes[:MAX_PROMPT_NODES]]
    if len(nodes) > MAX_PROMPT_NODES:
        lines.append(f"... {len(nodes) - MAX_PROMPT_NODES} more entities not shown")
    lines.append("LINKS (id | source | type | target | level):")
    lines += [
        f"{e.id} | {by_id.get(e.source, e.source)} | {e.link_type} | {by_id.get(e.target, e.target)} | {where(e.parent)}"
        for e in edges[:MAX_PROMPT_EDGES]
    ]
    if len(edges) > MAX_PROMPT_EDGES:
        lines.append(f"... {len(edges) - MAX_PROMPT_EDGES} more links not shown")
    return "\n".join(lines)


def _ontology_prompt(classes: dict[str, str], links: dict[str, dict]) -> str:
    parents = {k.rsplit(".", 1)[0] for k in classes if "." in k}
    leaves = sorted(k for k in classes if k not in parents)
    return SYSTEM_PROMPT.format(
        classes="\n".join(leaves),
        links="\n".join(f"{t} ({d['domain']} -> {d['range']})" for t, d in sorted(links.items())),
    )


class ChatTurn(BaseModel):
    role: Literal["user", "assistant"]
    text: str = Field(max_length=1000)


async def run_chat(
    session: AsyncSession,
    message: str,
    history: list[ChatTurn],
    nodes: list[SandboxNode],
    edges: list[SandboxEdge],
    container: str | None,
    classes: dict[str, str],
    links: dict[str, dict],
) -> dict:
    """One chat turn. Returns {"reply", "nodes", "edges", "results", "changed"}."""
    system = chat_prompt(classes, links)
    convo = "\n".join(f"{t.role.upper()}: {t.text}" for t in history[-MAX_HISTORY:]) or "(none)"

    def user_prompt(cur_nodes, cur_edges, extra: str = "") -> str:
        return (
            f"CURRENT SANDBOX\n{describe_sandbox(cur_nodes, cur_edges, container)}\n\n"
            f"CONVERSATION (oldest first):\n{convo}\n\nUSER REQUEST: {message}{extra}"
        )

    raw = await complete_json(system, user_prompt(nodes, edges))
    ops, results = parse_ops(raw.get("ops"))
    cur_nodes, cur_edges, applied = await apply_ops(session, nodes, edges, ops, container, classes, links)
    results += applied
    reply = str(raw.get("reply") or "").strip()

    explained = False  # the model saw the failures and told the user itself
    failed = [r for r in applied if not r["ok"]]
    if failed:  # one corrective retry: the model sees why each change failed
        note = (
            "\n\nSome of your operations failed and were NOT applied:\n"
            + "\n".join(f"- {r['op']}: {r['message']}" for r in failed)
            + "\nThe successful ones are already applied (see CURRENT SANDBOX). Return ONLY corrected "
            'operations for the failed ones, plus an updated "reply". If they cannot be fixed, return no ops '
            "and explain briefly."
        )
        try:
            raw2 = await complete_json(system, user_prompt(cur_nodes, cur_edges, note))
            ops2, bad2 = parse_ops(raw2.get("ops"))
            cur_nodes, cur_edges, applied2 = await apply_ops(session, cur_nodes, cur_edges, ops2, container, classes, links)
            if ops2 or bad2:
                # the model offered corrections: they replace the failures they address
                results = [r for r in results if r["ok"]] + bad2 + applied2
            # no corrections offered: the original failures stay visible
            explained = not (ops2 or bad2) and bool(str(raw2.get("reply") or "").strip())
            reply = str(raw2.get("reply") or reply).strip()
        except Exception as e:  # noqa: BLE001 - the first pass already applied; never lose it
            logger.warning("sandbox agent retry skipped: %s", e)

    # A model can claim success for a change that failed. Unless it was shown the
    # failures and explained them, the reply must not contradict the results.
    still_failed = any(not r["ok"] for r in results)
    if still_failed and not explained:
        if any(r["ok"] for r in results):
            reply = (reply + " Some changes could not be applied; see below.").strip()
        else:
            reply = "I couldn't make that change."

    return {
        "reply": reply[:800],
        "nodes": [n.model_dump() for n in cur_nodes],
        "edges": [e.model_dump() for e in cur_edges],
        "results": results,
        "changed": any(r["ok"] for r in results),
    }


# ---- per-user request throttle (the LLM calls cost money) -----------------------------

CHAT_PER_MINUTE = 20
_calls: dict[str, list[float]] = {}


def check_rate(user: str, now: float | None = None) -> None:
    t = time.time() if now is None else now
    recent = [c for c in _calls.get(user, []) if t - c < 60]
    if len(recent) >= CHAT_PER_MINUTE:
        _calls[user] = recent
        raise HTTPException(status_code=429, detail="Too many requests; try again in a minute")
    recent.append(t)
    _calls[user] = recent
