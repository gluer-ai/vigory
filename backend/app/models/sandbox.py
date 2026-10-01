"""Per-user what-if workspace ("sandbox"): a private working copy of part of
the graph that can be rearranged and edited without touching real data."""
from typing import Literal

from pydantic import BaseModel, Field

_ID = dict(min_length=1, max_length=80, pattern=r"^[A-Za-z0-9_.:\-]+$")
_COORD = dict(ge=-1_000_000, le=1_000_000)

Origin = Literal["graph", "new"]


class SandboxBase(BaseModel):
    """What a graph-origin node looked like when it was copied, so the UI can
    show what the user changed."""

    label: str = Field(max_length=200)
    entity_subclass: str = Field(max_length=200)


class SandboxNode(BaseModel):
    id: str = Field(**_ID)  # real entity_id for origin=graph, a fresh id for origin=new
    origin: Origin
    label: str = Field(min_length=1, max_length=200)
    entity_subclass: str = Field(min_length=1, max_length=200)
    x: float = Field(**_COORD)
    y: float = Field(**_COORD)
    base: SandboxBase | None = None
    # The entity or link this item lives *inside* (None = top level). Lets any
    # entity or relation be opened to reveal its own nested canvas.
    parent: str | None = Field(None, **_ID)


class SandboxEdge(BaseModel):
    id: str = Field(**_ID)
    origin: Origin
    source: str = Field(**_ID)
    target: str = Field(**_ID)
    link_type: str = Field(min_length=1, max_length=100)
    base_type: str | None = Field(None, max_length=100)
    parent: str | None = Field(None, **_ID)


class SandboxCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    trigger_entity_id: str | None = Field(None, max_length=200)
    hops: int = Field(2, ge=1, le=4)


class SandboxSave(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=100)
    nodes: list[SandboxNode]
    edges: list[SandboxEdge]
    version: int = Field(ge=1)  # the version this edit was based on


class SandboxSummary(BaseModel):
    sandbox_id: str
    name: str
    version: int
    node_count: int
    edge_count: int
    updated_at: str


class SandboxOut(SandboxSummary):
    nodes: list[SandboxNode]
    edges: list[SandboxEdge]
