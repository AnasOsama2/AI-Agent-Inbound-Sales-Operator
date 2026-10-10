"""Validation, immutable revisions, and authenticated product administration."""

import copy
import hashlib
import json
import re
from decimal import Decimal, InvalidOperation
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Query, Request
from sqlalchemy import select

from salma.auth import require_business
from salma.db import now, session
from salma.platform_models import Business
from salma.product_models import ProductRevision, ProductVariant

router = APIRouter(prefix="/api/businesses/{business_id}/products")
MAX_ACTIVE_PRODUCTS = 10_000


def _text(value, field, limit=4000, required=False):
    if not isinstance(value, str) or len(value) > limit:
        raise ValueError(f"{field} must be text of at most {limit} characters")
    value = value.strip()
    if required and not value:
        raise ValueError(f"{field} is required")
    if value.startswith(("=", "+", "-", "@")):
        raise ValueError(f"Formula-like value is not allowed in {field}")
    return value


def validate_product(data, default_currency=None):
    if not isinstance(data, dict):
        raise ValueError("Product must be an object")
    row = {}
    for field, limit in (
        ("sku", 200),
        ("name", 300),
        ("description", 12000),
        ("category", 300),
        ("conditions", 4000),
        ("url", 2000),
    ):
        row[field] = _text(data.get(field, ""), field, limit, required=field in ("sku", "name"))
    if row["url"] and not re.match(r"^https?://[^\s]+$", row["url"], re.I):
        raise ValueError("Product URL must use http or https")
    amount = data.get("price")
    if isinstance(amount, bool) or not isinstance(amount, (str, int, float, Decimal)):
        raise ValueError("Price must be a decimal amount")
    if isinstance(amount, str) and amount.strip().startswith(("=", "+", "-", "@")):
        raise ValueError("Price must be non-negative; formula-like values are not allowed")
    try:
        price = Decimal(str(amount).strip())
        if not price.is_finite() or not 0 <= price <= 1_000_000_000:
            raise ValueError("Price must be finite, non-negative, and at most 1000000000")
        if price != price.quantize(Decimal("0.01")):
            raise ValueError("Price cannot have more than two decimal places")
        row["price"] = format(price, ".2f")
    except InvalidOperation as exc:
        raise ValueError("Price must be a decimal amount") from exc
    currency = _text(data.get("currency") or default_currency or "", "currency", 3, True).upper()
    if not re.fullmatch(r"[A-Z]{3}", currency):
        raise ValueError("Currency must be a three-letter code")
    row["currency"] = currency
    options = data.get("options", {})
    if not isinstance(options, dict) or len(options) > 30:
        raise ValueError("Options must be an object of at most 30 values")
    row["options"] = {
        _text(k, "option name", 100, True): _text(v, "option value", 300, True)
        for k, v in options.items()
    }
    stock = data.get("stock_quantity")
    if stock is None or stock == "":
        row["stock_quantity"] = None
    else:
        if isinstance(stock, bool) or not re.fullmatch(r"\d+", str(stock)):
            raise ValueError("Stock quantity must be a non-negative integer")
        row["stock_quantity"] = int(stock)
        if row["stock_quantity"] > 2_147_483_647:
            raise ValueError("Stock quantity is too large")
    status = data.get("stock_status") or "unknown"
    if status not in ("unknown", "in_stock", "out_of_stock"):
        raise ValueError("Invalid stock status")
    if row["stock_quantity"] is not None:
        status = "in_stock" if row["stock_quantity"] else "out_of_stock"
    row["stock_status"] = status
    if not isinstance(data.get("archived", False), bool):
        raise ValueError("Archived must be true or false")
    row["archived"] = data.get("archived", False)
    row["source_type"] = data.get("source_type", "manual")
    if row["source_type"] not in ("manual", "file"):
        raise ValueError("Invalid product source")
    for field in ("external_product_id", "external_variant_id"):
        value = data.get(field)
        row[field] = None if value in (None, "") else _text(value, field, 200)
    row["snapshot_at"] = data.get("snapshot_at") or now().isoformat()
    from datetime import datetime

    try:
        datetime.fromisoformat(row["snapshot_at"])
    except (ValueError, TypeError) as exc:
        raise ValueError("Snapshot time must be an ISO timestamp") from exc
    row["source_ref"] = _text(data.get("source_ref", ""), "source_ref", 500)
    return row


def _business(db, business_id):
    business = db.scalar(
        select(Business)
        .where(Business.id == business_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if business is None:
        raise HTTPException(404, "Workspace not found")
    return business


def get_revision_products(db, business_id, revision_id, include_archived=True):
    if revision_id is None:
        return []
    revision = db.get(ProductRevision, revision_id)
    if revision is None or revision.business_id != business_id:
        raise ValueError("Product revision does not belong to this workspace")
    rows = db.scalars(
        select(ProductVariant)
        .where(ProductVariant.business_id == business_id, ProductVariant.revision_id == revision_id)
        .order_by(ProductVariant.sku)
    )
    return [copy.deepcopy(row.data) for row in rows if include_archived or not row.data["archived"]]


def get_draft_products(db, business_id, include_archived=True):
    business = db.get(Business, business_id)
    if business is None:
        raise HTTPException(404, "Workspace not found")
    return get_revision_products(
        db, business_id, business.draft_product_revision_id, include_archived
    )


def _freeze(db, business, rows):
    canonical = [validate_product(row) for row in rows]
    keys = [row["sku"].casefold() for row in canonical]
    if len(set(keys)) != len(keys):
        raise ValueError("SKUs must be unique, ignoring case")
    if sum(not row["archived"] for row in canonical) > MAX_ACTIVE_PRODUCTS:
        raise ValueError("Catalog supports at most 10000 active variants")
    previous = {
        row.sku: row
        for row in db.scalars(
            select(ProductVariant).where(
                ProductVariant.business_id == business.id,
                ProductVariant.revision_id == business.draft_product_revision_id,
            )
        )
    }
    revision = ProductRevision(business_id=business.id)
    db.add(revision)
    db.flush()
    for row in canonical:
        key = row["sku"].casefold()
        old = previous.get(key)
        embedding = old.embedding if old is not None and old.data == row else None
        if isinstance(embedding, str):
            embedding = json.loads(embedding)
        grouping = json.dumps([row["name"], row["category"]], ensure_ascii=False)
        db.add(
            ProductVariant(
                revision_id=revision.id,
                business_id=business.id,
                sku=key,
                product_key=hashlib.sha256(grouping.encode()).hexdigest(),
                data=row,
                embedding=embedding,
            )
        )
    business.draft_product_revision_id = revision.id
    db.flush()
    return revision.id


def replace_draft_products(db, business_id, rows):
    return _freeze(db, _business(db, business_id), rows)


def upsert_draft_products(db, business_id, rows, expected_revision=None):
    business = _business(db, business_id)
    if expected_revision is not None and business.draft_product_revision_id != expected_revision:
        raise HTTPException(409, "Catalog changed; preview the import again")
    existing = {row["sku"].casefold(): row for row in get_draft_products(db, business_id)}
    incoming = [validate_product(row) for row in rows]
    if len({row["sku"].casefold() for row in incoming}) != len(incoming):
        raise ValueError("SKUs must be unique, ignoring case")
    existing.update({row["sku"].casefold(): row for row in incoming})
    return _freeze(db, business, list(existing.values()))


@router.get("")
def list_products(
    business_id: str,
    request: Request,
    q: str = Query("", max_length=200),
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
):
    require_business(request, business_id)
    with session() as db:
        rows = get_draft_products(db, business_id)
        if q:
            needle = q.casefold()
            rows = [
                row
                for row in rows
                if needle
                in " ".join(
                    [row["sku"], row["name"], row["description"], row["category"]]
                ).casefold()
            ]
        return {
            "items": rows[offset : offset + limit],
            "total": len(rows),
            "revision_id": db.get(Business, business_id).draft_product_revision_id,
        }


@router.post("")
def create_product(business_id: str, body: dict, request: Request):
    require_business(request, business_id)
    body = {**body, "sku": body.get("sku") or str(uuid4()), "source_type": "manual"}
    with session() as db:
        _business(db, business_id)
        try:
            row = validate_product(body)
            if any(
                current["sku"].casefold() == row["sku"].casefold()
                for current in get_draft_products(db, business_id)
            ):
                raise HTTPException(409, "SKU already exists")
            revision = upsert_draft_products(db, business_id, [row])
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        db.commit()
        return {"product": row, "revision_id": revision}


def _edit_product(db, business_id, sku, changes):
    if "stock_quantity" in changes and changes["stock_quantity"] in (None, ""):
        changes = {**changes, "stock_status": changes.get("stock_status") or "unknown"}
    _business(db, business_id)
    current = next(
        (
            row
            for row in get_draft_products(db, business_id)
            if row["sku"].casefold() == sku.casefold()
        ),
        None,
    )
    if current is None:
        raise HTTPException(404, "Product not found")
    if "sku" in changes and str(changes["sku"]).casefold() != sku.casefold():
        raise HTTPException(422, "SKU cannot be changed")
    try:
        row = validate_product(
            {**current, **changes, "sku": current["sku"], "snapshot_at": now().isoformat()}
        )
        revision = upsert_draft_products(db, business_id, [row])
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    db.commit()
    return {"product": row, "revision_id": revision}


@router.put("/{sku:path}")
def edit_product(business_id: str, sku: str, body: dict, request: Request):
    require_business(request, business_id)
    with session() as db:
        return _edit_product(db, business_id, sku, body)


@router.post("/{sku:path}/archive")
def archive_product(business_id: str, sku: str, request: Request):
    require_business(request, business_id)
    with session() as db:
        return _edit_product(db, business_id, sku, {"archived": True})
