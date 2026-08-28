"""Entity Pydantic models, mirroring the Entity_Attributes "ALL" core fields."""
from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, Field

EntityStatus = Literal["active", "inactive", "destroyed", "unknown"]


class EntityBase(BaseModel):
    entity_class: str = Field(..., description="One of the nine root classes, e.g. PERSON")
    entity_subclass: str = Field(..., description="Full taxonomy key, e.g. PERSON.MILITARY_PERSONNEL")
    label: str
    aliases: list[str] = Field(default_factory=list)
    status: EntityStatus = "active"
    confidence: str = Field(..., description="Confidence code, e.g. B2")
    source_ref: str
    first_observed: date | None = None
    last_observed: date | None = None
    attrs: dict[str, Any] = Field(default_factory=dict)


class EntityCreate(EntityBase):
    entity_id: str


class EntityUpdate(BaseModel):
    entity_class: str | None = None
    entity_subclass: str | None = None
    label: str | None = None
    aliases: list[str] | None = None
    status: EntityStatus | None = None
    confidence: str | None = None
    source_ref: str | None = None
    first_observed: date | None = None
    last_observed: date | None = None
    attrs: dict[str, Any] | None = None


class Entity(EntityBase):
    entity_id: str
