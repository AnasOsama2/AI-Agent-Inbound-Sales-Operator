from salma.knowledge import chunk_text, extract_pdf, partitions


class Tokenizer:
    model_max_length = 8

    def encode(self, text, add_special_tokens=True):
        return text.split() + (["start", "end"] if add_special_tokens else [])


def test_chunks_fit_model_token_limit_and_preserve_words():
    content = "one two three four five six seven eight nine ten"
    chunks = chunk_text(content, Tokenizer(), overlap=0)
    assert " ".join(chunks) == content
    assert all(len(Tokenizer().encode(chunk)) <= 8 for chunk in chunks)


def test_mixed_language_has_independent_model_partitions():
    selected = partitions("mixed")
    assert selected == [
        ("en", "sentence-transformers/all-MiniLM-L6-v2"),
        ("ar", "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"),
    ]


def test_scanned_pdf_fails_explicitly(tmp_path):
    import pytest
    from pypdf import PdfWriter

    filename = tmp_path / "scanned.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=300, height=300)
    with filename.open("wb") as output:
        writer.write(output)
    with pytest.raises(ValueError, match="OCR"):
        extract_pdf(filename)
