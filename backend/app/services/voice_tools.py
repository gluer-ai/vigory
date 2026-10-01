"""Knowledge-base tools and grounding for the voice agent.

The voice platform runs the conversation; these functions are what it can
*do* against Vigory. Everything is read-only except `propose_ingestion`,
which only creates a *proposed* IngestBatch — nothing reaches the graph
until a human commits it in the review panel.
"""
import json
from typing import Any

from neo4j import AsyncSession
from pydantic import BaseModel, Field, ValidationError

from app.services.extraction_agent import extract_from_text
from app.services.scenario_agent import explain_scope
from app.services.scoping import scope_subgraph
from app.services.search import search_entities_by_name

MAX_SCENARIO_NODES = 40  # keep tool output small enough to be spoken about
MAX_INGEST_CHARS = 8000


class ToolError(Exception):
    """A tool call that failed in a way the model should hear about."""


class SearchArgs(BaseModel):
    query: str = Field(min_length=1, max_length=200)
    limit: int = Field(8, ge=1, le=20)


class ScenarioArgs(BaseModel):
    entity_id: str = Field(min_length=1, max_length=200)
    hops: int = Field(2, ge=1, le=4)
    explain: bool = True


class IngestArgs(BaseModel):
    text: str = Field(min_length=1, max_length=MAX_INGEST_CHARS)


class NoArgs(BaseModel):
    pass


# Realtime API function-tool format.
TOOL_DEFINITIONS: list[dict[str, Any]] = [
    {
        "type": "function",
        "name": "search_knowledge_graph",
        "description": (
            "Search entities in the knowledge graph by name or alias. Use this first to find "
            "the entity_id for anything the user mentions."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Name or alias to look for"},
                "limit": {"type": "integer", "description": "Max results (default 8)"},
            },
            "required": ["query"],
        },
    },
    {
        "type": "function",
        "name": "build_scenario",
        "description": (
            "Build a scenario around a trigger entity: the connected entities and "
            "relationships within N hops, ranked by relevance. Only the graph's real "
            "entities and links are returned."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "entity_id": {"type": "string", "description": "Trigger entity_id from search"},
                "hops": {"type": "integer", "description": "Link depth 1-4 (default 2)"},
                "explain": {"type": "boolean", "description": "Rank relevance (default true)"},
            },
            "required": ["entity_id"],
        },
    },
    {
        "type": "function",
        "name": "propose_ingestion",
        "description": (
            "When the user states new facts (people, units, places, relationships) that are "
            "not in the graph, submit them as plain text. Entities and links are extracted "
            "and shown in the review panel as a PROPOSAL; a human must approve them."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "text": {
                    "type": "string",
                    "description": "The new facts, written as clear complete sentences",
                }
            },
            "required": ["text"],
        },
    },
    {
        "type": "function",
        "name": "list_documents",
        "description": "List the knowledge-base documents that have been uploaded and their status.",
        "parameters": {"type": "object", "properties": {}},
    },
]


def _parse(model: type[BaseModel], arguments: dict[str, Any]) -> Any:
    try:
        return model(**arguments)
    except ValidationError as e:
        raise ToolError(f"Invalid arguments: {e.errors()[0]['msg']}") from e


def _brief_entity(e: dict) -> dict:
    return {
        "entity_id": e.get("entity_id"),
        "label": e.get("label"),
        "class": e.get("entity_subclass") or e.get("entity_class"),
        "status": e.get("status"),
        "aliases": e.get("aliases") or [],
    }


async def _search(session: AsyncSession, args: SearchArgs) -> dict:
    rows = await search_entities_by_name(session, args.query, args.limit)
    return {"output": {"count": len(rows), "entities": [_brief_entity(r) for r in rows]}}


async def _scenario(session: AsyncSession, args: ScenarioArgs) -> dict:
    exists = await (
        await session.run("MATCH (e:Entity {entity_id: $id}) RETURN e", id=args.entity_id)
    ).single()
    if exists is None:
        raise ToolError(f"No entity with id '{args.entity_id}'. Use search_knowledge_graph first.")

    sub = await scope_subgraph(session, args.entity_id, hops=args.hops)
    nodes = sub["nodes"][:MAX_SCENARIO_NODES]
    kept = {n["entity_id"] for n in nodes}
    edges = [
        e for e in sub["edges"] if e["source_entity"] in kept and e["target_entity"] in kept
    ]
    truncated = len(sub["nodes"]) > len(nodes)

    annotations: list[dict] = []
    if args.explain and len(nodes) > 1:
        cleaned = {
            "nodes": [{**n, "attrs": json.loads(n.get("attrs") or "{}")} for n in nodes],
            "edges": [{**e, "attrs": json.loads(e.get("attrs") or "{}")} for e in edges],
        }
        annotations = (await explain_scope(args.entity_id, cleaned))["annotations"]

    labels = {n["entity_id"]: n["label"] for n in nodes}
    by_id = {a["entity_id"]: a for a in annotations}
    output = {
        "trigger": {"entity_id": args.entity_id, "label": labels[args.entity_id]},
        "truncated": truncated,
        "related": [
            {
                **_brief_entity(n),
                "relevance": by_id.get(n["entity_id"], {}).get("relevance"),
                "why": by_id.get(n["entity_id"], {}).get("rationale"),
            }
            for n in nodes
            if n["entity_id"] != args.entity_id
        ],
        "links": [
            {
                "from": labels[e["source_entity"]],
                "type": e["link_type"],
                "to": labels[e["target_entity"]],
            }
            for e in edges
        ],
    }
    ui = {"type": "scenario", "trigger_entity_id": args.entity_id, "hops": args.hops}
    return {"output": output, "ui": ui}


async def _ingest(session: AsyncSession, args: IngestArgs) -> dict:
    batch = await extract_from_text(session, args.text)
    output = {
        "status": "proposed_for_human_review",
        "batch_id": batch["batch_id"],
        "entities": [e.get("label") for e in batch["entities"]],
        "links": len(batch["links"]),
        "rejected": len(batch["rejected_entities"]) + len(batch["rejected_links"]),
        "note": "Nothing is saved until the user commits it in the review panel.",
    }
    return {"output": output, "ui": {"type": "batch", "batch": batch}}


async def _documents(session: AsyncSession, _: NoArgs) -> dict:
    result = await session.run("MATCH (d:Document) RETURN d ORDER BY d.uploaded_at DESC LIMIT 50")
    docs = [
        {"filename": r["d"]["filename"], "status": r["d"]["status"]} async for r in result
    ]
    return {"output": {"count": len(docs), "documents": docs}}


_HANDLERS = {
    "search_knowledge_graph": (SearchArgs, _search),
    "build_scenario": (ScenarioArgs, _scenario),
    "propose_ingestion": (IngestArgs, _ingest),
    "list_documents": (NoArgs, _documents),
}


async def run_tool(session: AsyncSession, name: str, arguments: dict[str, Any]) -> dict:
    """Execute one tool. Returns {"output": <for the model>, "ui": <optional, for the panel>}.
    Unknown tools and bad arguments raise ToolError; nothing else is callable.
    """
    handler = _HANDLERS.get(name)
    if handler is None:
        raise ToolError(f"Unknown tool '{name}'")
    model, fn = handler
    return await fn(session, _parse(model, arguments))


BASE_INSTRUCTIONS = """You are the voice interface to Vigory.ai, an intelligence knowledge graph. \
Speak briefly and naturally; this is a voice conversation.

Rules:
- The knowledge graph is your only source of facts about entities and relationships. \
Never invent entities, links or details. If you have not looked something up, look it up with \
a tool, or say you don't know.
- To find an entity, call search_knowledge_graph. To describe how things connect or to create \
a scenario, call build_scenario with the entity_id, then summarize the real result.
- When the user tells you new facts, repeat them back briefly, then call propose_ingestion. \
Tell them it is a proposal waiting in the review panel; you cannot approve or save it.
- Entity ids are for tools, not for speaking; say names."""


async def build_instructions(session: AsyncSession) -> str:
    """Ground the agent in what the knowledge base currently contains."""
    counts = await session.run(
        "MATCH (e:Entity) RETURN e.entity_class AS cls, count(*) AS n ORDER BY n DESC"
    )
    class_counts = [(r["cls"], r["n"]) async for r in counts]
    sample = await session.run(
        """
        MATCH (e:Entity)
        OPTIONAL MATCH (e)-[r:LINK]-()
        RETURN e.label AS label, e.entity_class AS cls, count(r) AS degree
        ORDER BY degree DESC, label LIMIT 30
        """
    )
    top = [(r["label"], r["cls"]) async for r in sample]
    docs = await session.run("MATCH (d:Document {status: 'committed'}) RETURN d.filename AS f LIMIT 20")
    filenames = [r["f"] async for r in docs]

    lines = [BASE_INSTRUCTIONS, "", "Current knowledge base:"]
    if class_counts:
        lines.append(
            "- Entities by class: " + ", ".join(f"{c} {n}" for c, n in class_counts if c)
        )
        lines.append("- Best-connected entities: " + "; ".join(f"{l} ({c})" for l, c in top))
    else:
        lines.append("- The graph is empty so far.")
    if filenames:
        lines.append("- Uploaded documents: " + ", ".join(filenames))
    return "\n".join(lines)
