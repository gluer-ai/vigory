"""Unit tests for chunk_text: paragraph-boundary packing so a long
document can be extracted in LLM-sized pieces without splitting
mid-sentence when avoidable."""
from app.services.chunking import chunk_text


def test_chunk_text_returns_single_chunk_when_under_limit():
    text = "Paragraph one.\n\nParagraph two."
    assert chunk_text(text, max_chars=8000) == [text]


def test_chunk_text_splits_on_paragraph_boundaries_when_over_limit():
    para1 = "Ivan Petrov commands the unit."  # 30 chars
    para2 = "Ivan Petrov reports to HQ."      # 26 chars
    text = f"{para1}\n\n{para2}"

    chunks = chunk_text(text, max_chars=30)

    assert chunks == [para1, para2]


def test_chunk_text_hard_splits_a_single_oversized_paragraph():
    paragraph = "x" * 250
    chunks = chunk_text(paragraph, max_chars=100)

    assert len(chunks) == 3
    assert "".join(chunks) == paragraph
    assert all(len(c) <= 100 for c in chunks)


def test_chunk_text_returns_empty_list_for_blank_text():
    assert chunk_text("   \n\n  ", max_chars=8000) == []
