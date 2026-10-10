"""Verify real seed PDF ingestion and English/Arabic/mixed vector retrieval."""

from pathlib import Path

from salma.db import session
from salma.knowledge import ingest, search

with session() as db, db.begin():
    for lang in ("en", "ar"):
        count = ingest(db, Path(f"data/business-{lang}.pdf"), lang)
        print(f"{lang}: stored {count} token-bounded PDF chunks")
    for lang, query in [
        ("en", "software agency"),
        ("ar", "وكالة برمجيات"),
        ("mixed", "AI برمجيات"),
    ]:
        results = search(db, query, lang)
        assert results, f"No {lang} vector results"
        assert all(row["page"] > 0 and row["document"] for row in results)
        if lang == "mixed":
            assert any(":en:" in row["id"] for row in results)
            assert any(":ar:" in row["id"] for row in results)
        print(f"{lang}: retrieved {len(results)} cited chunks")
