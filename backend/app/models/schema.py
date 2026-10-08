"""ClassDef / LinkDef models: the runtime-extendable ontology."""
from pydantic import BaseModel, Field


class ClassDef(BaseModel):
    key: str
    parent_key: str | None = None
    level: int
    label: str
    notes: str | None = None
    origin: str | None = None  # "extraction" for terms the AI added at runtime


class ClassDefCreate(BaseModel):
    key: str
    parent_key: str | None = None
    label: str
    notes: str | None = None


class ClassUpdate(BaseModel):
    """Editable class fields. Omitted fields are left alone; `notes: null`
    clears the notes. The key (a path) is never editable here."""

    label: str | None = Field(default=None, min_length=1, max_length=120)
    notes: str | None = Field(default=None, max_length=1000)


class ClassMerge(BaseModel):
    into: str


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
