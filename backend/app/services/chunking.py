"""Split extracted document text into LLM-sized chunks on paragraph
boundaries, so a document larger than one extraction call can still be
processed chunk-by-chunk into a single merged review batch
(see app.services.document_ingest.process_document).
"""


def chunk_text(text: str, max_chars: int = 8000) -> list[str]:
    paragraphs = [p for p in text.split("\n\n") if p.strip()]
    if not paragraphs:
        return [text] if text.strip() else []

    # A paragraph longer than max_chars is hard-split rather than dropped.
    pieces: list[str] = []
    for para in paragraphs:
        if len(para) <= max_chars:
            pieces.append(para)
        else:
            for start in range(0, len(para), max_chars):
                pieces.append(para[start : start + max_chars])

    chunks: list[str] = []
    current: list[str] = []
    current_len = 0
    for piece in pieces:
        extra = len(piece) + (2 if current else 0)  # +2 for the "\n\n" join
        if current and current_len + extra > max_chars:
            chunks.append("\n\n".join(current))
            current, current_len = [], 0
            extra = len(piece)
        current.append(piece)
        current_len += extra
    if current:
        chunks.append("\n\n".join(current))
    return chunks
