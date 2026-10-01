"""Per-user sandbox endpoints. Every route resolves the caller's id through
require_auth and passes it to the service, which scopes every query by owner.
"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.auth import require_auth
from app.db.neo4j_client import get_driver
from app.llm.client import LLMError
from app.models.sandbox import SandboxCreate, SandboxEdge, SandboxNode, SandboxOut, SandboxSave, SandboxSummary
from app.services import sandbox as svc
from app.services import sandbox_agent as agent
from app.services.extraction_agent import extract_preview

router = APIRouter(prefix="/sandbox", tags=["sandbox"])


def _invalid(e: svc.SandboxInvalid) -> HTTPException:
    return HTTPException(status_code=422, detail=str(e))


@router.get("", response_model=list[SandboxSummary])
async def list_sandboxes(user: str = Depends(require_auth)):
    async with get_driver().session() as session:
        return await svc.list_for_owner(session, user)


@router.post("", response_model=SandboxOut, status_code=201)
async def create_sandbox(body: SandboxCreate, user: str = Depends(require_auth)):
    async with get_driver().session() as session:
        try:
            nodes, edges = [], []
            if body.trigger_entity_id:
                nodes, edges = await svc.seed_from_graph(session, body.trigger_entity_id.strip(), body.hops)
            return await svc.create(session, user, body.name.strip(), nodes, edges)
        except svc.SandboxInvalid as e:
            raise _invalid(e)


@router.get("/{sandbox_id}", response_model=SandboxOut)
async def get_sandbox(sandbox_id: str, user: str = Depends(require_auth)):
    async with get_driver().session() as session:
        return await svc.get_for_owner(session, user, sandbox_id)


@router.put("/{sandbox_id}", response_model=SandboxOut)
async def save_sandbox(sandbox_id: str, body: SandboxSave, user: str = Depends(require_auth)):
    async with get_driver().session() as session:
        try:
            classes, links = await svc.load_ontology(session)
            svc.validate_state(body.nodes, body.edges, classes, links)
            return await svc.save(session, user, sandbox_id, body)
        except svc.SandboxInvalid as e:
            raise _invalid(e)


@router.delete("/{sandbox_id}", status_code=204)
async def delete_sandbox(sandbox_id: str, user: str = Depends(require_auth)):
    async with get_driver().session() as session:
        await svc.delete(session, user, sandbox_id)


# ---- assistant: edit the canvas by asking (chat) or by voice ------------------------
# These never save anything: they take the canvas the browser is showing and
# return the edited canvas. The browser saves it through the normal autosave,
# so ownership and the version check still apply.


class _Canvas(BaseModel):
    nodes: list[SandboxNode] = Field(max_length=svc.MAX_NODES)
    edges: list[SandboxEdge] = Field(max_length=svc.MAX_EDGES)
    container: str | None = Field(None, max_length=80)


class ChatBody(_Canvas):
    message: str = Field(min_length=1, max_length=2000)
    history: list[agent.ChatTurn] = Field(default_factory=list, max_length=20)


class OpsBody(_Canvas):
    ops: list[agent.SandboxOp] = Field(min_length=1, max_length=agent.MAX_OPS * 2)


@router.post("/agent/chat")
async def agent_chat(body: ChatBody, user: str = Depends(require_auth)):
    agent.check_rate(user)
    async with get_driver().session() as session:
        classes, links = await svc.load_ontology(session)
        try:
            return await agent.run_chat(
                session, body.message.strip(), body.history, body.nodes, body.edges, body.container, classes, links
            )
        except LLMError as e:
            raise HTTPException(status_code=502, detail=str(e))


@router.post("/agent/ops")
async def agent_ops(body: OpsBody, user: str = Depends(require_auth)):
    agent.check_rate(user)
    async with get_driver().session() as session:
        classes, links = await svc.load_ontology(session)
        nodes, edges, results = await agent.apply_ops(
            session, body.nodes, body.edges, body.ops, body.container, classes, links
        )
    return {
        "nodes": [n.model_dump() for n in nodes],
        "edges": [e.model_dump() for e in edges],
        "results": results,
        "changed": any(r["ok"] for r in results),
        "summary": agent.describe_sandbox(nodes, edges, body.container),
    }


@router.post("/agent/describe")
async def agent_describe(body: _Canvas, user: str = Depends(require_auth)):
    return {"summary": agent.describe_sandbox(body.nodes, body.edges, body.container)}


# ---- ingest a scenario into a sandbox ------------------------------------------------


class ExtractBody(BaseModel):
    text: str = Field(min_length=1, max_length=20_000)


@router.post("/extract")
async def extract_scenario(body: ExtractBody, user: str = Depends(require_auth)):
    """Turn scenario text into proposed entities and links for the sandbox.

    Nothing is saved anywhere: no ingest batch and no entities. Links may point
    at real graph entities the text mentions; those are returned (read-only) in
    `existing_entities` so the browser can copy them into the canvas too.
    """
    agent.check_rate(user)
    text = body.text.strip()
    if not text:
        raise HTTPException(status_code=422, detail="Scenario text is empty")
    async with get_driver().session() as session:
        try:
            result = await extract_preview(session, text)
        except LLMError as e:
            raise HTTPException(status_code=502, detail=str(e))
        in_batch = {e["entity_id"] for e in result["entities"]}
        existing = []
        for link in result["links"]:
            for end in (link["source_entity"], link["target_entity"]):
                if end not in in_batch and all(x["entity_id"] != end for x in existing):
                    found = await agent._load_graph_entity(session, end)
                    if found:
                        existing.append(
                            {k: found[k] for k in ("entity_id", "label", "entity_subclass")}
                        )
    return {**result, "existing_entities": existing}
