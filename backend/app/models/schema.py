"""ClassDef / LinkDef models: the runtime-extendable ontology."""
from pydantic import BaseModel


class ClassDef(BaseModel):
    key: str
    parent_key: str | None = None
    level: int
    label: str
    notes: str | None = None


class ClassDefCreate(BaseModel):
    key: str
    parent_key: str | None = None
    label: str
    notes: str | None = None


class LinkDef(BaseModel):
    type: str
    category: str | None = None
    domain: str
    range: str
    directionality: str | None = None
    inverse: str | None = None
    symmetric: str | None = None
    transitive: str | None = None
    notes: str | None = None


class LinkDefCreate(BaseModel):
    type: str
    category: str | None = None
    domain: str
    range: str
    directionality: str | None = None
    inverse: str | None = None
    symmetric: str | None = None
    transitive: str | None = None
    notes: str | None = None
