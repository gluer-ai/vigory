import json

from fastapi import APIRouter, HTTPException, Query

from app.db.neo4j_client import get_driver
from app.llm.client import LLMError
from app.services.scenario_agent import explain_scope
from app.services.scoping import scope_subgraph

router = APIRouter(prefix="/scenarios", tags=["scenarios"])


def _clean_node(n: dict) -> dict:
    return {**n, "attrs": json.loads(n.get("attrs") or "{}")}


def _clean_edge(e: dict) -> dict:
    return {**e, "attrs": json.loads(e.get("attrs") or "{}")}


@router.get("/{entity_id}/scope")
async def get_scope(
    entity_id: str,
    hops: int = Query(2, ge=1, le=5),
    link_types: str | None = Query(None, description="comma-separated link_type filter"),
):
    driver = get_driver()
    types = [t.strip() for t in link_types.split(",")] if link_types else None
    async with driver.session() as session:
        exists = await session.run("MATCH (e:Entity {entity_id: $id}) RETURN e", id=entity_id)
        if await exists.single() is None:
            raise HTTPException(status_code=404, detail="trigger entity not found")

        subgraph = await scope_subgraph(session, entity_id, hops=hops, link_types=types)
        return {
            "trigger_entity_id": entity_id,
            "nodes": [_clean_node(n) for n in subgraph["nodes"]],
            "edges": [_clean_edge(e) for e in subgraph["edges"]],
        }


@router.post("/{entity_id}/explain")
async def post_explain(entity_id: str, hops: int = Query(2, ge=1, le=5)):
    driver = get_driver()
    async with driver.session() as session:
        exists = await session.run("MATCH (e:Entity {entity_id: $id}) RETURN e", id=entity_id)
        if await exists.single() is None:
            raise HTTPException(status_code=404, detail="trigger entity not found")

        subgraph = await scope_subgraph(session, entity_id, hops=hops)
        cleaned = {
            "nodes": [_clean_node(n) for n in subgraph["nodes"]],
            "edges": [_clean_edge(e) for e in subgraph["edges"]],
        }
        try:
            return await explain_scope(entity_id, cleaned)
        except LLMError as e:
            raise HTTPException(status_code=502, detail=str(e))
