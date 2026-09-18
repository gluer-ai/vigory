"""Document upload API: multipart upload -> disk storage (via
document_ingest.save_upload) -> background extraction (process_document),
producing one proposed IngestBatch per document for review through the
existing /ingest endpoints.
"""
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, HTTPException, UploadFile
from fastapi.responses import FileResponse

from app.config import get_settings
from app.db.neo4j_client import get_driver
from app.models.document import Document
from app.services.document_ingest import process_document, save_upload

router = APIRouter(prefix="/documents", tags=["documents"])


_UPLOAD_READ_CHUNK_BYTES = 1024 * 1024  # 1MB


@router.post("/upload", response_model=Document)
async def upload_document(file: UploadFile, background_tasks: BackgroundTasks):
    # Read in bounded chunks and bail out as soon as the accumulated size
    # exceeds the configured cap, instead of reading the whole (possibly
    # huge) body into memory before save_upload ever gets a chance to
    # reject it — bounds memory usage against an oversized/malicious upload.
    settings = get_settings()
    max_bytes = settings.max_upload_mb * 1024 * 1024
    chunks: list[bytes] = []
    size = 0
    while True:
        piece = await file.read(_UPLOAD_READ_CHUNK_BYTES)
        if not piece:
            break
        chunks.append(piece)
        size += len(piece)
        if size > max_bytes:
            raise HTTPException(
                status_code=400,
                detail=f"file exceeds {settings.max_upload_mb}MB upload limit",
            )
    content = b"".join(chunks)
    driver = get_driver()
    async with driver.session() as session:
        try:
            doc = await save_upload(session, file.filename or "upload", content)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
    background_tasks.add_task(process_document, doc["document_id"])
    return doc


@router.get("", response_model=list[Document])
async def list_documents():
    driver = get_driver()
    async with driver.session() as session:
        result = await session.run("MATCH (d:Document) RETURN d ORDER BY d.uploaded_at DESC")
        return [dict(record["d"]) async for record in result]


@router.get("/{document_id}", response_model=Document)
async def get_document(document_id: str):
    driver = get_driver()
    async with driver.session() as session:
        result = await session.run(
            "MATCH (d:Document {document_id: $id}) RETURN d", id=document_id
        )
        record = await result.single()
        if record is None:
            raise HTTPException(status_code=404, detail="document not found")
        return dict(record["d"])


@router.get("/{document_id}/file")
async def download_document(document_id: str):
    driver = get_driver()
    async with driver.session() as session:
        result = await session.run(
            "MATCH (d:Document {document_id: $id}) RETURN d", id=document_id
        )
        record = await result.single()
        if record is None:
            raise HTTPException(status_code=404, detail="document not found")
        doc = dict(record["d"])

    full_path = Path(get_settings().upload_dir) / doc["file_path"]
    if not full_path.exists():
        raise HTTPException(status_code=404, detail="file not found on disk")
    return FileResponse(full_path, filename=doc["filename"])
