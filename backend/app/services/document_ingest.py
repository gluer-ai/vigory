"""Document upload orchestration: persist an uploaded file, then run
chunked extraction against it in the background, merging all chunks into
one review batch — the file-upload counterpart to extract_from_text.
"""
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

from neo4j import AsyncSession

from app.config import get_settings
from app.db.neo4j_client import get_driver
from app.services.chunking import chunk_text
from app.services.document_parser import extract_text
from app.services.extraction_agent import _extract_and_validate

ALLOWED_EXTENSIONS = {"txt", "md", "pdf", "docx", "xlsx"}


def _extension(filename: str) -> str:
    return filename.rsplit(".", 1)[-1].lower() if "." in filename else ""


def _dedupe_chunk_entity_ids(
    valid_entities: list[dict], valid_links: list[dict], used_entity_ids: set[str]
) -> tuple[list[dict], list[dict]]:
    """Rename any entity_id newly declared in this chunk's valid_entities
    that happens to collide with an id already used by an EARLIER chunk of
    this same document. Each chunk is an independent LLM call and the
    extraction prompt's id examples (e.g. "P-temp1") lead different chunks
    to number their own new entities from scratch, so two unrelated new
    entities from different chunks can coincidentally mint the same id —
    unlike the intentional-reuse path (extra_existing_entities), where a
    later chunk deliberately reuses a real earlier id and simply omits that
    entity from its own "entities" array, so it never reaches this function.

    Only ids genuinely new to this chunk are ever renamed; remaps the same
    old->new id in this chunk's own valid_links so the chunk stays
    internally consistent. Ids that reuse an earlier chunk's real id via a
    link only (not re-declared as an entity here) are left untouched.
    """
    id_remap: dict[str, str] = {}
    claimed = set(used_entity_ids)
    renamed_entities = []
    for entity in valid_entities:
        old_id = entity["entity_id"]
        if old_id in claimed:
            new_id, suffix = old_id, 1
            while new_id in claimed:
                new_id = f"{old_id}-dup{suffix}"
                suffix += 1
            id_remap[old_id] = new_id
            entity = {**entity, "entity_id": new_id}
        claimed.add(entity["entity_id"])
        renamed_entities.append(entity)

    if not id_remap:
        return renamed_entities, valid_links

    renamed_links = [
        {
            **link,
            "source_entity": id_remap.get(link["source_entity"], link["source_entity"]),
            "target_entity": id_remap.get(link["target_entity"], link["target_entity"]),
        }
        if link["source_entity"] in id_remap or link["target_entity"] in id_remap
        else link
        for link in valid_links
    ]
    return renamed_entities, renamed_links


async def save_upload(session: AsyncSession, filename: str, content: bytes) -> dict:
    """Validate, write to disk, and record a new Document node with
    status='processing'. Raises ValueError on a rejected extension or an
    over-limit file — the caller (POST /documents/upload) turns that into
    a 400 before any Document node is created.
    """
    safe_filename = Path(filename).name or "upload"  # strip any path components
    ext = _extension(safe_filename)
    if ext not in ALLOWED_EXTENSIONS:
        raise ValueError(
            f"unsupported file type '.{ext}' — allowed: {', '.join(sorted(ALLOWED_EXTENSIONS))}"
        )
    settings = get_settings()
    max_bytes = settings.max_upload_mb * 1024 * 1024
    if len(content) > max_bytes:
        raise ValueError(f"file exceeds {settings.max_upload_mb}MB upload limit")

    document_id = f"D-{uuid.uuid4().hex[:8]}"
    doc_dir = Path(settings.upload_dir) / document_id
    doc_dir.mkdir(parents=True, exist_ok=True)
    (doc_dir / safe_filename).write_bytes(content)

    doc = {
        "document_id": document_id,
        "filename": safe_filename,
        "file_type": ext,
        "size_bytes": len(content),
        "file_path": f"{document_id}/{safe_filename}",
        "status": "processing",
        "error_message": None,
        "batch_id": None,
        "uploaded_at": datetime.now(timezone.utc).isoformat(),
    }
    await session.run("CREATE (d:Document $props)", props=doc)
    return doc


async def process_document(document_id: str) -> None:
    """Background job: parse, chunk, and extract a previously-uploaded
    document, merging every chunk's results into a single review batch.
    Runs detached from the upload request (scheduled via BackgroundTasks),
    so failures are recorded on the Document node rather than raised.
    """
    driver = get_driver()
    async with driver.session() as session:
        result = await session.run(
            "MATCH (d:Document {document_id: $id}) RETURN d", id=document_id
        )
        record = await result.single()
        if record is None:
            return
        doc = dict(record["d"])

        try:
            settings = get_settings()
            file_bytes = (Path(settings.upload_dir) / doc["file_path"]).read_bytes()
            text = extract_text(doc["filename"], file_bytes)
            chunks = chunk_text(text, max_chars=settings.ingest_chunk_chars)

            pending_entities: list[dict] = []
            valid_entities: list[dict] = []
            rejected_entities: list[dict] = []
            valid_links: list[dict] = []
            rejected_links: list[dict] = []
            used_entity_ids: set[str] = set()
            for chunk in chunks:
                chunk_result = await _extract_and_validate(
                    session, chunk, extra_existing_entities=pending_entities
                )
                chunk_entities, chunk_links = _dedupe_chunk_entity_ids(
                    chunk_result["valid_entities"], chunk_result["valid_links"], used_entity_ids
                )
                used_entity_ids.update(e["entity_id"] for e in chunk_entities)

                valid_entities.extend(chunk_entities)
                rejected_entities.extend(chunk_result["rejected_entities"])
                valid_links.extend(chunk_links)
                rejected_links.extend(chunk_result["rejected_links"])
                pending_entities.extend(chunk_entities)

            batch_id = f"B-{uuid.uuid4().hex[:8]}"
            await session.run(
                """
                CREATE (b:IngestBatch {batch_id: $batch_id, status: $status, source_text: $source_text,
                                        entities: $entities, links: $links,
                                        rejected_entities: $rejected_entities, rejected_links: $rejected_links,
                                        document_id: $document_id})
                """,
                batch_id=batch_id,
                status="proposed",
                source_text=text,
                entities=json.dumps(valid_entities),
                links=json.dumps(valid_links),
                rejected_entities=json.dumps(rejected_entities),
                rejected_links=json.dumps(rejected_links),
                document_id=document_id,
            )
            await session.run(
                "MATCH (d:Document {document_id: $id}) SET d.status = $status, d.batch_id = $batch_id",
                id=document_id,
                status="proposed",
                batch_id=batch_id,
            )
        except Exception as e:
            await session.run(
                "MATCH (d:Document {document_id: $id}) SET d.status = $status, d.error_message = $error_message",
                id=document_id,
                status="error",
                error_message=str(e),
            )
