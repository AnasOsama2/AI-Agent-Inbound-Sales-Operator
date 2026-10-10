"""Real PostgreSQL: owner imports -> draft -> publication -> bounded agent tools -> outbox."""

import os
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from salma import auth, product_imports
from salma.adapters import Inbound
from salma.db import Outbox, session
from salma.platform_api import publish_snapshot
from salma.platform_api import router as platform_router
from salma.platform_models import Business, ConfigVersion, Membership
from salma.product_catalog import router as product_router
from salma.product_catalog import upsert_draft_products
from salma.product_models import ProductVariant
from salma.product_tools import lookup, needs_variant_choice, reference, search
from salma.settings import Settings
from salma.store import accept
from salma.trust import Decision
from salma.worker import Worker
from sqlalchemy import select
from test_platform import MemoryRedis, create_identity, sign_in

pytestmark = pytest.mark.skipif(
    not os.environ.get("TEST_DATABASE_URL"), reason="PostgreSQL integration"
)


@pytest.fixture
def owner_catalog(monkeypatch, tmp_path):
    memory = MemoryRedis()
    monkeypatch.setattr(auth, "redis_client", lambda: memory)
    monkeypatch.setattr(product_imports, "settings", lambda: SimpleNamespace(uploads_dir=tmp_path))
    user = create_identity("owner")
    with session() as db:
        first = Business(name="Shirt Shop", draft={"name": "Shirt Shop"})
        other = Business(name="Other Shop", draft={"name": "Other Shop"})
        db.add_all([first, other])
        db.flush()
        db.add(Membership(user_id=user.id, business_id=first.id))
        db.commit()
    app = FastAPI()
    for router in (platform_router, product_router, product_imports.router):
        app.include_router(router)
    with TestClient(app) as client:
        yield client, sign_in(client, user), first.id, other.id


def test_owner_import_mapping_application_publication_and_isolation(owner_catalog):
    client, headers, bid, other = owner_catalog
    base = f"/api/businesses/{bid}"
    csv = "Code,Product,Amount,Color,Size,Qty\nBLUE-M,قميص Cotton Shirt,249.50,Blue,M,\n"
    assert client.post(base + "/product-imports", files={"file": ("x.csv", csv)}).status_code == 403
    denied = client.post(
        f"/api/businesses/{other}/product-imports", headers=headers, files={"file": ("x.csv", csv)}
    )
    assert denied.status_code == 404
    upload = client.post(
        base + "/product-imports",
        headers=headers,
        files={"file": ("products.csv", csv.encode("utf8"))},
    )
    assert upload.status_code == 202, upload.text
    identifier = upload.json()["id"]
    assert product_imports.process_product_import_job(identifier)
    mapping = {
        "worksheet": "CSV",
        "mapping": {
            "sku": "Code",
            "name": "Product",
            "price": "Amount",
            "options.color": "Color",
            "options.size": "Size",
            "stock_quantity": "Qty",
        },
    }
    assert (
        client.put(
            base + f"/product-imports/{identifier}/mapping", headers=headers, json=mapping
        ).status_code
        == 422
    )
    mapping["default_currency"] = "EGP"
    assert (
        client.put(
            base + f"/product-imports/{identifier}/mapping", headers=headers, json=mapping
        ).status_code
        == 202
    )
    assert product_imports.process_product_import_job(identifier)
    preview = client.get(base + f"/product-imports/{identifier}").json()
    assert preview["status"] == "ready" and preview["counts"]["new"] == 1
    assert client.get(f"/api/businesses/{other}/product-imports/{identifier}").status_code == 404
    assert (
        client.get(
            f"/api/businesses/{other}/product-imports/{identifier}/generated-skus"
        ).status_code
        == 404
    )
    for _ in range(2):
        assert (
            client.post(base + f"/product-imports/{identifier}/apply", headers=headers).status_code
            == 200
        )
    rows = client.get(base + "/products").json()
    assert rows["total"] == 1 and rows["items"][0]["stock_quantity"] is None
    assert client.post(base + "/publish", headers=headers).status_code == 200
    with session() as db:
        version = db.scalar(
            select(ConfigVersion).where(
                ConfigVersion.business_id == bid, ConfigVersion.version == 1
            )
        )
        assert version.product_revision_id == rows["revision_id"]
        assert lookup(db, bid, version, "blue-m")["price"] == "249.50"
        assert search(db, bid, version, "قميص")[0]["sku"] == "BLUE-M"
        assert search(db, other, version, "قميص") == []
        upsert_draft_products(
            db, bid, [{"sku": "BLUE-M", "name": "Changed", "price": "999", "currency": "EGP"}]
        )
        db.commit()
        assert lookup(db, bid, version, "BLUE-M")["price"] == "249.50"
    response = client.post(base + "/products/BLUE-M/archive", headers=headers)
    assert response.status_code == 200, response.text
    assert client.get(base + "/products").json()["items"][0]["archived"]
    assert (
        client.put(base + "/products/BLUE-M", headers=headers, json={"stock_quantity": 5}).json()[
            "product"
        ]["stock_status"]
        == "in_stock"
    )
    cleared = client.put(base + "/products/BLUE-M", headers=headers, json={"stock_quantity": None})
    assert cleared.json()["product"]["stock_status"] == "unknown"


def test_worker_product_reply_is_versioned_and_superseded_before_delivery():
    bid = str(uuid4())
    with session() as db:
        db.add(Business(id=bid, name="Test", draft={"name": "Test"}))
        db.flush()
        first = upsert_draft_products(
            db,
            bid,
            [
                {
                    "sku": "BLUE-M",
                    "name": "Shirt",
                    "price": "249.50",
                    "currency": "EGP",
                    "options": {"color": "Blue", "size": "M"},
                }
            ],
        )
        publish_snapshot(db, bid)
    incoming = Inbound(
        provider="simulator",
        channel="simulator",
        business_id=bid,
        config_version=1,
        account_id="local",
        inbox_id="sim",
        external_message_id=str(uuid4()),
        external_conversation_id=str(uuid4()),
        external_customer_id=str(uuid4()),
        text="BLUE-M price?",
    )
    with session() as db:
        accept(db, incoming)
    decision = Decision(
        intent="price",
        reply_kind="product",
        product_skus=["BLUE-M"],
        evidence_ids=[reference(first, "BLUE-M")],
    )
    worker = Worker(Settings(agent_mode="simulator"), generate=lambda *_: {"decision": decision})
    assert worker.process_conversation(incoming.conversation_key)
    with session() as db:
        outbox = db.scalar(
            select(Outbox).where(Outbox.conversation_key == incoming.conversation_key)
        )
        assert (
            "249.5 EGP" in outbox.text and "snapshot" in outbox.text and outbox.config_version == 1
        )
        upsert_draft_products(
            db, bid, [{"sku": "BLUE-M", "name": "Shirt", "price": "999", "currency": "EGP"}]
        )
        publish_snapshot(db, bid)
        db.refresh(outbox)
        assert outbox.status == "suppressed"
    assert not worker.deliver_conversation(incoming.conversation_key)


def test_full_published_variant_group_and_vector_search(monkeypatch):
    bid = str(uuid4())
    with session() as db:
        db.add(Business(id=bid, name="Variants", draft={"name": "Variants"}))
        db.flush()
        revision = upsert_draft_products(
            db,
            bid,
            [
                {
                    "sku": f"SHIRT-{size}",
                    "name": "Cotton Shirt",
                    "price": "10",
                    "currency": "EGP",
                    "options": {"size": size},
                }
                for size in ("M", "L", "XL", "XXL", "S", "XS")
            ],
        )
        publish_snapshot(db, bid)
        version = db.scalar(select(ConfigVersion).where(ConfigVersion.business_id == bid))
        selected = lookup(db, bid, version, "SHIRT-M")
        assert needs_variant_choice(db, bid, version, "Cotton shirt price?", [selected])
        assert not needs_variant_choice(db, bid, version, "SHIRT-M price?", [selected])
        # Several selected products must still check siblings hidden by bounded retrieval.
        another = lookup(db, bid, version, "SHIRT-L")
        assert needs_variant_choice(db, bid, version, "Cotton shirts price?", [selected, another])
        row = db.scalar(
            select(ProductVariant).where(
                ProductVariant.revision_id == revision, ProductVariant.sku == "shirt-m"
            )
        )
        row.embedding = [1.0] + [0.0] * 383
        db.commit()

        import salma.knowledge

        class Vector(list):
            def tolist(self):
                return list(self)

        class Model:
            def encode(self, *args, **kwargs):
                return Vector([1.0] + [0.0] * 383)

        monkeypatch.setattr(salma.knowledge, "embedding_model", lambda *_: Model())
        assert search(db, bid, version, "ملابس قطنية", vector=True)[0]["sku"] == "SHIRT-M"
        # PostgreSQL vector results round-trip into revision clones correctly.
        upsert_draft_products(
            db, bid, [{"sku": "OTHER", "name": "Other", "price": "2", "currency": "EGP"}]
        )
        db.commit()


def test_real_ten_thousand_variant_boundary_and_bounded_retrieval():
    from salma.product_catalog import replace_draft_products

    bid = str(uuid4())
    with session() as db:
        db.add(Business(id=bid, name="Capacity fixture", draft={"name": "Capacity fixture"}))
        db.flush()
        rows = [
            {"sku": f"CAP-{index:05}", "name": "Capacity item", "price": "10.10", "currency": "EGP"}
            for index in range(10_000)
        ]
        revision = replace_draft_products(db, bid, rows)
        publish_snapshot(db, bid)
        version = db.scalar(select(ConfigVersion).where(ConfigVersion.business_id == bid))
        assert version.product_revision_id == revision
        assert len(search(db, bid, version, "Capacity")) == 5
        assert lookup(db, bid, version, "CAP-09999")["price"] == "10.10"
        with pytest.raises(ValueError, match="10000"):
            upsert_draft_products(
                db, bid, [{"sku": "EXCESS", "name": "Extra", "price": "1", "currency": "EGP"}]
            )
        db.rollback()
        assert db.get(Business, bid).draft_product_revision_id == revision
