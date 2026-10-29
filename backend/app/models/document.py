"""Document Pydantic model — an uploaded knowledge-base file and its
extraction status, mirroring the Document Neo4j node."""
from typing import Literal

from pydantic import BaseModel

DocumentStatus = Literal["processing", "proposed", "committed", "error"]
DocumentFileType = Literal["txt", "md", "pdf", "docx", "xlsx"]


class Document(BaseModel):
    document_id: str
    filename: str
    file_type: DocumentFileType
    size_bytes: int
    file_path: str
    status: DocumentStatus
    error_message: str | None = None
    batch_id: str | None = None
    # Live progress while status == "processing" (a small model can take minutes).
    stage: str | None = None
    progress_current: int | None = None
    progress_total: int | None = None
    uploaded_at: str
