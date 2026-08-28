"""Link Pydantic models, mirroring the Link_Attributes column spec."""
from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, Field

AssertionStatus = Literal["reported", "assessed", "confirmed", "disputed"]


class LinkBase(BaseModel):
    link_type: str
    source_entity: str
    target_entity: str
    direction: Literal["directed", "symmetric"] = "directed"
    inverse_type: str | None = None
    valid_from: date | None = None
    valid_to: date | None = None
    assertion_status: AssertionStatus = "reported"
    confidence: str = Field(..., description="Confidence code, e.g. B2")
    source_ref: str
    attrs: dict[str, Any] = Field(default_factory=dict)


class LinkCreate(LinkBase):
    link_id: str


class Link(LinkBase):
    link_id: str
