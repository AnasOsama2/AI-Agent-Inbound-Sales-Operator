import argparse
import hashlib
import re
import ssl
import unicodedata
from functools import lru_cache
from pathlib import Path

from pypdf import PdfReader
from sqlalchemy import delete, select, text

from salma.db import Knowledge, session

MODELS = {
    "en": "sentence-transformers/all-MiniLM-L6-v2",
    "ar": "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
}


def partitions(language):
    if language not in {"en", "ar", "mixed"}:
        raise ValueError("Language must be en, ar or mixed")
    return list(MODELS.items()) if language == "mixed" else [(language, MODELS[language])]


@lru_cache(maxsize=2)
def embedding_model(language):
    try:
        import httpx
        from huggingface_hub import set_client_factory
        from sentence_transformers import SentenceTransformer
    except ImportError:
        raise ValueError(
            "Install the knowledge extra before vector ingestion or retrieval"
        ) from None
    set_client_factory(
        lambda: httpx.Client(verify=ssl.create_default_context(), timeout=60, follow_redirects=True)
    )
    try:
        return SentenceTransformer(MODELS[language], device="cpu", local_files_only=True)
    except OSError:
        # Download only on first installation; restarts should work without Hub access.
        return SentenceTransformer(MODELS[language], device="cpu")


def chunk_text(content, tokenizer, overlap=24, limit=None):
    limit = min(limit or tokenizer.model_max_length, tokenizer.model_max_length)
    if limit < 4:
        raise ValueError("Tokenizer limit is too small")

    def fits(value):
        return len(tokenizer.encode(value, add_special_tokens=True)) <= limit

    def split(value, separators):
        if fits(value):
            return [value]
        separator = separators[0]
        parts = value.split(separator) if separator else list(value)
        if len(parts) == 1:
            return split(value, separators[1:])
        output, current = [], ""
        for part in parts:
            units = split(part, separators[1:]) if not fits(part) else [part]
            for unit in units:
                candidate = (current + separator + unit) if current else unit
                if not fits(candidate):
                    if current.strip():
                        output.append(current.strip())
                    current = unit
                else:
                    current = candidate
        if current.strip():
            output.append(current.strip())
        return output

    chunks = split(content, ["\n\n", "\n", ". ", " ", ""])
    if overlap and hasattr(tokenizer, "decode"):
        enriched = [chunks[0]] if chunks else []
        for previous, chunk in zip(chunks, chunks[1:]):
            tokens = tokenizer.encode(previous, add_special_tokens=False)[-overlap:]
            while tokens:
                candidate = tokenizer.decode(tokens) + " " + chunk
                if fits(candidate):
                    chunk = candidate
                    break
                tokens = tokens[1:]
            enriched.append(chunk)
        return enriched
    return chunks


def extract_pdf(path):
    reader = PdfReader(path)
    if reader.is_encrypted:
        raise ValueError("Encrypted PDFs are unsupported")
    pages = []
    for number, page in enumerate(reader.pages, 1):
        content = unicodedata.normalize("NFKC", page.extract_text() or "")
        if len(re.sub(r"\s", "", content)) < 10:
            raise ValueError(
                f"Page {number}: text extraction failed; scanned PDFs/OCR are outside V1"
            )
        pages.append((number, content))
    if not pages:
        raise ValueError("PDF has no pages")
    return pages


def ingest(db, path: Path, language: str):
    if language not in MODELS:
        raise ValueError("Each document must use one language partition")
    pages = extract_pdf(path)
    model = embedding_model(language)
    digest = hashlib.sha256(path.read_bytes()).hexdigest()[:20]
    # Store one model family per partition and document/page citations on every chunk.
    rows = []
    for page, content in pages:
        for index, chunk in enumerate(
            chunk_text(content, model.tokenizer, limit=model.max_seq_length)
        ):
            vector = model.encode(chunk, normalize_embeddings=True).tolist()
            rows.append(
                Knowledge(
                    id=f"pdf:{digest}:{language}:{page}:{index}",
                    document=path.name,
                    page=page,
                    language=language,
                    model=MODELS[language],
                    text=chunk,
                    embedding=vector,
                )
            )
    db.execute(
        delete(Knowledge).where(Knowledge.document == path.name, Knowledge.language == language)
    )
    db.add_all(rows)
    db.flush()
    return len(rows)


def search(db, query, language, vector=True, limit=4):
    results = []
    for partition, model_id in partitions(language):
        if vector:
            vector_data = (
                embedding_model(partition).encode(query, normalize_embeddings=True).tolist()
            )
            matches = (
                db.execute(
                    text("""SELECT id, document, page, text FROM knowledge
                WHERE language=:language AND model=:model AND embedding IS NOT NULL
                ORDER BY embedding <=> CAST(:vector AS vector) LIMIT :limit"""),
                    {
                        "language": partition,
                        "model": model_id,
                        "vector": str(vector_data),
                        "limit": limit,
                    },
                )
                .mappings()
                .all()
            )
            results.append([dict(row) for row in matches])
        else:
            terms = set(re.findall(r"\w+", query.lower()))
            rows = db.scalars(select(Knowledge).where(Knowledge.language == partition)).all()
            rows.sort(
                key=lambda row: len(terms & set(re.findall(r"\w+", row.text.lower()))), reverse=True
            )
            results.append(
                [
                    {"id": row.id, "document": row.document, "page": row.page, "text": row.text}
                    for row in rows[:limit]
                    if terms & set(re.findall(r"\w+", row.text.lower()))
                ]
            )
    # Interleave by rank; cosine distances from different models are never compared.
    return [group[index] for index in range(limit) for group in results if index < len(group)][
        :limit
    ]


def main():
    parser = argparse.ArgumentParser(description="Staff-only text PDF ingestion")
    parser.add_argument("path", type=Path)
    parser.add_argument("--language", choices=["en", "ar"], required=True)
    args = parser.parse_args()
    with session() as db, db.begin():
        count = ingest(db, args.path, args.language)
    print(f"Stored {count} evidence chunks with document/page references")


if __name__ == "__main__":
    main()
