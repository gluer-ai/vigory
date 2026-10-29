"""Which import ("source") is creating data right now.

Everything an import writes - committed entities and links, and ontology
classes/link types the extraction agent adds - is stamped with that import's
`source_id`, so deleting a source can later remove exactly what it created
(see app.services.source_cleanup). A source is an uploaded Document
(source_id = document_id) or a pasted-text batch (source_id = batch_id).

Ontology growth happens deep inside extraction, far from where the source is
known, so the id travels in a context variable (same pattern as
app.llm.activity.llm_operation).
"""
import contextvars
from collections.abc import Iterator
from contextlib import contextmanager

_source: contextvars.ContextVar[str | None] = contextvars.ContextVar("ingest_source", default=None)


@contextmanager
def ingest_source(source_id: str | None) -> Iterator[None]:
    token = _source.set(source_id)
    try:
        yield
    finally:
        _source.reset(token)


def current_source() -> str | None:
    return _source.get()


def batch_source_id(batch: dict) -> str:
    """The source a batch's data belongs to: its document, else the batch itself."""
    return batch.get("document_id") or batch["batch_id"]
