from datetime import timedelta
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from openpyxl import Workbook
from salma import product_imports as imports
from salma.db import Base, now
from salma.platform_models import Business
from salma.product_catalog import get_draft_products, upsert_draft_products, validate_product
from salma.product_import_models import ProductImport
from salma.product_models import ProductRevision, ProductVariant
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker


def source(tmp_path, text, filename="catalog.csv"):
    path = tmp_path / filename
    path.write_text(text, encoding="utf-8-sig")
    return path


def example_item(path, mapping=None):
    return SimpleNamespace(
        filename=path.name,
        details={
            "file_type": path.suffix[1:],
            "sheets": imports.parse_file(path),
            "worksheet": "CSV",
            "mapping": mapping or {"sku": "SKU", "name": "Name", "price": "Price"},
            "default_currency": "EGP",
            "generated_skus": {},
            "snapshot_at": now().isoformat(),
        },
    )


def test_utf8_bom_arabic_and_blank_rows(tmp_path):
    path = source(tmp_path, "SKU,Name,Price\nA,قميص أزرق,125.50\n,,\n")
    item = example_item(path)
    result = imports.preview_rows(item, [], validate_product)
    assert result["counts"] == {"new": 1, "updated": 0, "unchanged": 0, "invalid": 0, "total": 1}
    assert result["rows"][0]["product"]["name"] == "قميص أزرق"
    assert result["rows"][0]["product"]["price"] == "125.50"


@pytest.mark.parametrize(
    "text,message",
    [
        ("", "empty"),
        ("Name,Name\na,b", "duplicate"),
        (",Name\na,b", "header"),
        ("Name\na,b", "without headers"),
    ],
)
def test_bad_headers_are_actionable(tmp_path, text, message):
    with pytest.raises(ValueError, match=message):
        imports.parse_file(source(tmp_path, text))


def test_invalid_encoding_and_oversized_strings(tmp_path):
    path = source(tmp_path, "Name\n")
    path.write_bytes(b"Name\n\xff")
    with pytest.raises(ValueError, match="UTF-8"):
        imports.parse_file(path)
    path.write_text("Name\n" + "x" * (imports.MAX_CELL + 1), encoding="utf8")
    with pytest.raises(ValueError, match="Invalid CSV"):
        imports.parse_file(path)


def test_rows_are_bounded(tmp_path, monkeypatch):
    monkeypatch.setattr(imports, "MAX_ROWS", 1)
    with pytest.raises(ValueError, match="exceeds 1"):
        imports.parse_file(source(tmp_path, "SKU\nA\nB\n"))


def test_xlsx_worksheets_and_mapped_formulas(tmp_path):
    path = tmp_path / "catalog.xlsx"
    book = Workbook()
    sheet = book.active
    sheet.title = "Products"
    sheet.append(["SKU", "Name", "Price", "Ignored"])
    sheet.append(["A", "Cotton", "=1+1", "literal"])
    sheet.append(["B", "Linen", 10, "=2+2"])
    book.create_sheet("Other").append(["Name", "Price"])
    book.save(path)
    item = example_item(path)
    item.details["worksheet"] = "Products"
    result = imports.preview_rows(item, [], validate_product)
    assert len(item.details["sheets"]) == 2
    assert result["counts"]["invalid"] == 1
    assert "formulas" in result["rows"][0]["errors"][0]
    assert result["rows"][1]["action"] == "new"


def test_xlsx_expansion_limit(tmp_path, monkeypatch):
    import zipfile

    path = tmp_path / "catalog.xlsx"
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("sheet.xml", "a" * 100)
    monkeypatch.setattr(imports, "MAX_EXPANDED_BYTES", 50)
    with pytest.raises(ValueError, match="expands"):
        imports.parse_file(path)


@pytest.mark.parametrize("value", ["=HYPERLINK(1)", "+SUM(1)", "@SUM(1)", "-SUM(1)"])
def test_csv_formula_values_are_rejected(tmp_path, value):
    path = source(tmp_path, f"SKU,Name,Price\nA,{value},10\n")
    result = imports.preview_rows(example_item(path), [], validate_product)
    assert result["counts"]["invalid"] == 1
    assert any("formulas" in error for error in result["rows"][0]["errors"])


def test_negative_price_and_duplicate_skus(tmp_path):
    path = source(tmp_path, "SKU,Name,Price\nA,Cotton,-1\na,Linen,10\n")
    result = imports.preview_rows(example_item(path), [], validate_product)
    assert result["counts"]["invalid"] == 2
    assert any("price" in error.lower() for error in result["rows"][0]["errors"])
    assert all(any("duplicate SKU" in e for e in r["errors"]) for r in result["rows"])


def test_generated_skus_are_stable_and_variant_options_supported(tmp_path):
    path = source(tmp_path, "Name,Price,Color,Size\nCotton,100,Blue,L\nCotton,100,Red,L\n")
    item = example_item(
        path, {"name": "Name", "price": "Price", "options.color": "Color", "size": "Size"}
    )
    first = imports.preview_rows(item, [], validate_product)
    item.details = first
    second = imports.preview_rows(item, [], validate_product)
    assert first["generated_skus"] == second["generated_skus"]
    assert len(set(first["generated_skus"].values())) == 2
    assert first["rows"][0]["product"]["options"] == {"color": "Blue", "size": "L"}


def test_new_updated_unchanged_and_existing_limit(tmp_path, monkeypatch):
    path = source(tmp_path, "SKU,Name,Price\nA,Cotton,10\nB,Linen,20\nC,Silk,30\n")
    item = example_item(path)
    old = [
        validate_product({"sku": "A", "name": "Cotton", "price": "10", "currency": "EGP"}),
        validate_product({"sku": "B", "name": "Linen", "price": "15", "currency": "EGP"}),
    ]
    result = imports.preview_rows(item, old, validate_product)
    assert [r["action"] for r in result["rows"]] == ["unchanged", "updated", "new"]
    monkeypatch.setattr(imports, "MAX_ROWS", 2)
    assert imports.preview_rows(item, old, validate_product)["counts"]["invalid"] == 3


@pytest.fixture
def database(tmp_path, monkeypatch):
    engine = create_engine("sqlite://")
    Base.metadata.create_all(
        engine,
        tables=[
            Business.__table__,
            ProductRevision.__table__,
            ProductVariant.__table__,
            ProductImport.__table__,
        ],
    )
    factory = sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(imports, "session", factory)
    with factory() as db:
        db.add_all([Business(id="one", name="One"), Business(id="two", name="Two")])
        db.commit()
    yield factory
    engine.dispose()


def add_job(factory, path, **kwargs):
    item = ProductImport(
        id="import",
        business_id="one",
        filename=path.name,
        storage_path=str(path),
        status="pending",
        pending_stage="parse",
        details={"file_type": "csv", "snapshot_at": now().isoformat(), "generated_skus": {}},
        **kwargs,
    )
    with factory() as db:
        db.add(item)
        db.commit()
    return item.id


def ready_job(factory, path):
    identifier = add_job(factory, path)
    assert imports.process_product_import_job(identifier)
    with factory() as db:
        item = db.get(ProductImport, identifier)
        assert item.status == "awaiting_mapping"
        item.details = {
            **item.details,
            "worksheet": "CSV",
            "default_currency": "EGP",
            "mapping": {"sku": "SKU", "name": "Name", "price": "Price"},
        }
        item.status = "pending_validation"
        item.pending_stage = "validate"
        db.commit()
    assert imports.process_product_import_job(identifier)
    return identifier


def test_durable_apply_idempotency_and_tenant_isolation(database, tmp_path):
    identifier = ready_job(database, source(tmp_path, "SKU,Name,Price\nA,Cotton,10\n"))
    with database() as db:
        with pytest.raises(HTTPException) as denied:
            imports.apply_import(db, "two", identifier)
        assert denied.value.status_code == 404
        item = imports.apply_import(db, "one", identifier)
        revision = item.applied_revision_id
        db.commit()
    with database() as db:
        item = imports.apply_import(db, "one", identifier)
        assert item.applied_revision_id == revision
        assert db.scalar(select(func.count()).select_from(ProductRevision)) == 1
        assert len(get_draft_products(db, "one")) == 1
        assert get_draft_products(db, "two") == []


def test_stale_preview_and_omitted_products(database, tmp_path):
    with database() as db:
        upsert_draft_products(
            db, "one", [{"sku": "KEPT", "name": "Old", "price": "2", "currency": "EGP"}]
        )
        db.commit()
    identifier = ready_job(database, source(tmp_path, "SKU,Name,Price\nA,Cotton,10\n"))
    with database() as db:
        upsert_draft_products(
            db, "one", [{"sku": "NEW", "name": "Change", "price": "3", "currency": "EGP"}]
        )
        db.commit()
    with database() as db:
        with pytest.raises(HTTPException) as stale:
            imports.apply_import(db, "one", identifier)
        assert stale.value.status_code == 409
        assert {p["sku"] for p in get_draft_products(db, "one")} == {"KEPT", "NEW"}
        item = db.get(ProductImport, identifier)
        item.status = "pending_validation"
        db.commit()
    assert imports.process_product_import_job(identifier)
    with database() as db:
        imports.apply_import(db, "one", identifier)
        db.commit()
        assert {p["sku"] for p in get_draft_products(db, "one")} == {"KEPT", "NEW", "A"}


def test_worker_lease_recovers_interrupted_job_and_preserves_active_claim(database, tmp_path):
    identifier = add_job(
        database,
        source(tmp_path, "SKU,Name,Price\nA,Cotton,10\n"),
        lease_token="previous",
        lease_until=now() + timedelta(minutes=1),
    )
    with database() as db:
        db.get(ProductImport, identifier).status = "processing"
        db.commit()
    assert imports.process_product_import_job(identifier) is False
    with database() as db:
        db.get(ProductImport, identifier).lease_until = now() - timedelta(seconds=1)
        db.commit()
    assert imports.process_product_import_job(identifier)
    with database() as db:
        item = db.get(ProductImport, identifier)
        assert item.status == "awaiting_mapping"
        assert item.lease_token is None


def test_invalid_rows_cannot_apply_and_failed_parse_is_durable(database, tmp_path):
    identifier = ready_job(database, source(tmp_path, "SKU,Name,Price\nA,Cotton,wrong\n"))
    with database() as db:
        assert db.get(ProductImport, identifier).status == "invalid"
        with pytest.raises(HTTPException, match="409"):
            imports.apply_import(db, "one", identifier)
        assert get_draft_products(db, "one") == []
        db.delete(db.get(ProductImport, identifier))
        db.commit()
    identifier = add_job(database, source(tmp_path, "Name,Name\na,b\n"))
    assert imports.process_product_import_job(identifier)
    with database() as db:
        item = db.get(ProductImport, identifier)
        assert item.status == "failed"
        assert "duplicate headers" in item.error


def test_csv_download_protects_formula_cells():
    response = imports.csv_download([["worksheet", "sku"], ["=bad", "A"]], "safe.csv")
    assert "'=bad" in response.body.decode("utf8")


def test_unchanged_values_still_refresh_approved_file_snapshot(database, tmp_path):
    with database() as db:
        old = validate_product(
            {
                "sku": "A",
                "name": "Cotton",
                "price": "10",
                "currency": "EGP",
                "snapshot_at": "2020-01-01T00:00:00+00:00",
            }
        )
        previous = upsert_draft_products(db, "one", [old])
        db.commit()
    identifier = ready_job(database, source(tmp_path, "SKU,Name,Price\nA,Cotton,10\n"))
    with database() as db:
        item = db.get(ProductImport, identifier)
        assert item.details["counts"]["unchanged"] == 1
        imports.apply_import(db, "one", identifier)
        db.commit()
        assert item.applied_revision_id != previous
        assert get_draft_products(db, "one")[0]["snapshot_at"] == item.details["snapshot_at"]


def test_empty_selected_sheet_cannot_be_approved(tmp_path):
    item = example_item(source(tmp_path, "SKU,Name,Price\n"))
    with pytest.raises(ValueError, match="no product rows"):
        imports.preview_rows(item, [], validate_product)


def test_default_currency_requires_explicit_confirmation(database, tmp_path, monkeypatch):
    monkeypatch.setattr(imports, "require_business", lambda *args: None)
    identifier = add_job(database, source(tmp_path, "SKU,Name,Price\nA,Cotton,10\n"))
    imports.process_product_import_job(identifier)
    mapping = imports.ImportMapping(worksheet="CSV", mapping={"name": "Name", "price": "Price"})
    with pytest.raises(HTTPException) as missing:
        imports.save_mapping("one", identifier, mapping, None)
    assert missing.value.status_code == 422
    assert "confirm" in missing.value.detail
    mapping.default_currency = "egp"
    assert imports.save_mapping("one", identifier, mapping, None)["status"] == "pending_validation"
    imports.process_product_import_job(identifier)
    with database() as db:
        assert db.get(ProductImport, identifier).details["rows"][0]["product"]["currency"] == "EGP"


def test_download_generated_skus_round_trips_actual_preview_ids(database, tmp_path, monkeypatch):
    import copy
    import csv
    import io

    monkeypatch.setattr(imports, "require_business", lambda *args: None)
    identifier = ready_job(database, source(tmp_path, "SKU,Name,Price\n,Cotton,10\n"))
    response = imports.generated_skus("one", identifier, None)
    records = list(csv.DictReader(io.StringIO(response.body.decode("utf-8-sig"))))
    with database() as db:
        sku = db.get(ProductImport, identifier).details["rows"][0]["sku"]
    assert records == [{"SKU": sku, "Name": "Cotton", "Price": "10"}]
    # Remapping to an explicit SKU must export the new explicit value, even when a
    # generated identifier from an earlier mapping remains in the durable history.
    with database() as db:
        item = db.get(ProductImport, identifier)
        details = copy.deepcopy(item.details)
        details["sheets"][0]["rows"][0]["values"][0] = "EXPLICIT"
        item.details = details
        item.status = "pending_validation"
        db.commit()
    imports.process_product_import_job(identifier)
    response = imports.generated_skus("one", identifier, None)
    records = list(csv.DictReader(io.StringIO(response.body.decode("utf-8-sig"))))
    assert records[0]["SKU"] == "EXPLICIT"


def test_preview_pagination(tmp_path):
    item = example_item(source(tmp_path, "SKU,Name,Price\nA,One,1\nB,Two,2\n"))
    item.details = imports.preview_rows(item, [], validate_product)
    item.id, item.status, item.error = "id", "ready", None
    item.created_at, item.applied_revision_id = now(), None
    view = imports.import_view(item, page_size=1, offset=1)
    assert view["rows"][0]["sku"] == "B"
    assert (view["total"], view["offset"], view["limit"]) == (2, 1, 1)
