import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from salma import auth, platform_api, product_catalog
from salma.db import Base, Outbox
from salma.platform_models import Business, ConfigVersion, Membership, User
from salma.product_catalog import get_draft_products, upsert_draft_products, validate_product
from salma.product_models import ProductRevision, ProductVariant
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool


@pytest.fixture
def catalog_db():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(
        engine,
        tables=[
            Business.__table__,
            ConfigVersion.__table__,
            ProductRevision.__table__,
            ProductVariant.__table__,
            Outbox.__table__,
            User.__table__,
            Membership.__table__,
        ],
    )
    with Session(engine) as db:
        db.add_all([Business(id="a", name="A", draft={}), Business(id="b", name="B", draft={})])
        db.commit()
        yield db


def product(**changes):
    return {"sku": "Shirt-M", "name": "Shirt", "price": "12.30", "currency": "EGP", **changes}


@pytest.mark.parametrize(
    "changes",
    [
        {"price": "NaN"},
        {"price": "-1"},
        {"price": True},
        {"price": "1e100"},
        {"price": "+1"},
        {"currency": ""},
        {"name": "=IMPORTXML(1)"},
        {"stock_quantity": -1},
        {"stock_quantity": "1.5"},
        {"options": {"color": "+formula"}},
    ],
)
def test_rejects_invalid_product_values(changes):
    with pytest.raises(ValueError):
        validate_product(product(**changes))


def test_price_stays_decimal_and_blank_stock_is_unknown():
    row = validate_product(product(stock_quantity="", stock_status=""))
    assert row["price"] == "12.30"
    assert row["stock_quantity"] is None
    assert row["stock_status"] == "unknown"
    assert validate_product(product(stock_quantity=0))["stock_status"] == "out_of_stock"


def test_revisions_are_immutable_and_business_scoped(catalog_db):
    db = catalog_db
    first = upsert_draft_products(db, "a", [product()])
    db.commit()
    second = upsert_draft_products(db, "a", [product(sku="shirt-m", price="20.00")])
    db.commit()
    assert first != second
    old = db.scalar(select(ProductVariant).where(ProductVariant.revision_id == first))
    assert old.data["price"] == "12.30"
    assert get_draft_products(db, "a")[0]["price"] == "20.00"
    assert get_draft_products(db, "b") == []
    with pytest.raises(HTTPException) as exc:
        upsert_draft_products(db, "a", [product()], expected_revision=first)
    assert exc.value.status_code == 409


def test_duplicate_skus_are_case_insensitive_and_variants_group_exact_names(catalog_db):
    with pytest.raises(ValueError):
        upsert_draft_products(catalog_db, "a", [product(), product(sku="shirt-m")])
    revision = upsert_draft_products(
        catalog_db,
        "a",
        [
            product(),
            product(sku="Shirt-L"),
            product(sku="Other", name="Shirts"),
        ],
    )
    rows = {
        row.sku: row
        for row in catalog_db.scalars(
            select(ProductVariant).where(ProductVariant.revision_id == revision)
        )
    }
    assert rows["shirt-m"].product_key == rows["shirt-l"].product_key
    assert rows["shirt-m"].product_key != rows["other"].product_key


def test_archived_variant_preserves_history(catalog_db):
    upsert_draft_products(catalog_db, "a", [product()])
    upsert_draft_products(catalog_db, "a", [product(archived=True)])
    assert get_draft_products(catalog_db, "a", include_archived=False) == []
    assert get_draft_products(catalog_db, "a")[0]["archived"] is True


def test_unchanged_variants_keep_embeddings_and_changed_variants_clear_them(catalog_db):
    first = upsert_draft_products(catalog_db, "a", [product()])
    variant = catalog_db.get(ProductVariant, (first, "shirt-m"))
    variant.embedding = [0.5] * 384
    catalog_db.commit()
    catalog_db.expire_all()
    second = upsert_draft_products(catalog_db, "a", [product(sku="new")])
    kept = catalog_db.get(ProductVariant, (second, "shirt-m"))
    assert kept.embedding is not None
    third = upsert_draft_products(catalog_db, "a", [product(price="13.00")])
    assert catalog_db.get(ProductVariant, (third, "shirt-m")).embedding is None


def test_active_limit_does_not_count_archived_rows(catalog_db, monkeypatch):
    monkeypatch.setattr(product_catalog, "MAX_ACTIVE_PRODUCTS", 2)
    upsert_draft_products(
        catalog_db, "a", [product(), product(sku="second"), product(sku="old", archived=True)]
    )
    with pytest.raises(ValueError, match="10000"):
        upsert_draft_products(catalog_db, "a", [product(sku="third")])


def test_publication_freezes_products_and_rollback_reuses_saved_revision(catalog_db):
    from salma.platform_api import publish_snapshot

    db = catalog_db
    db.get(Business, "a").draft = {"name": "A"}
    first = upsert_draft_products(db, "a", [product()])
    publish_snapshot(db, "a")
    published = db.scalar(select(ConfigVersion).where(ConfigVersion.version == 1))
    assert published.product_revision_id == first
    second = upsert_draft_products(db, "a", [product(price="99.00")])
    publish_snapshot(db, "a")
    assert published.product_revision_id == first
    assert second != first
    publish_snapshot(db, "a", published.config, saved_version=True, product_revision_id=first)
    reverted = db.scalar(select(ConfigVersion).where(ConfigVersion.version == 3))
    assert reverted.product_revision_id == first
    assert db.get(Business, "a").draft_product_revision_id == second


def test_crud_requires_csrf_and_scopes_workspace_access(catalog_db, monkeypatch):
    from test_platform import MemoryRedis

    engine = catalog_db.get_bind()
    for module in (auth, platform_api, product_catalog):
        monkeypatch.setattr(module, "session", lambda: Session(engine, expire_on_commit=False))
    memory = MemoryRedis()
    monkeypatch.setattr(auth, "redis_client", lambda: memory)
    with Session(engine) as db:
        user = User(
            id="owner",
            email="owner@example.test",
            role="owner",
            password_hash=auth.hash_password("test password 123"),
        )
        db.add(user)
        db.flush()
        db.add(Membership(user_id=user.id, business_id="a"))
        db.commit()
    app = FastAPI()
    app.include_router(platform_api.router)
    app.include_router(product_catalog.router)
    with TestClient(app) as client:
        path = "/api/businesses/a/products"
        assert client.get(path).status_code == 401
        login = client.post(
            "/api/auth/login", json={"email": "owner@example.test", "password": "test password 123"}
        )
        assert login.status_code == 200
        token = login.json()["csrf_token"]
        assert client.post(path, json=product()).status_code == 403
        headers = {"X-CSRF-Token": token}
        assert client.post(path, json=product(sku=123), headers=headers).status_code == 422
        created = client.post(path, json=product(), headers=headers)
        assert created.status_code == 200, created.text
        assert client.get("/api/businesses/b/products").status_code == 404
        assert (
            client.put(
                "/api/businesses/b/products/Shirt-M", json={"price": "1.00"}, headers=headers
            ).status_code
            == 404
        )
        assert (
            client.put(path + "/Shirt-M", json={"price": "15.20"}, headers=headers).status_code
            == 200
        )
        results = client.get(path + "?q=shirt&limit=1").json()
        assert results["total"] == 1
        assert results["items"][0]["price"] == "15.20"
        assert client.post(path + "/Shirt-M/archive", headers=headers).status_code == 200
        assert client.get(path).json()["items"][0]["archived"] is True
        assert client.get(path + "?limit=101").status_code == 422
