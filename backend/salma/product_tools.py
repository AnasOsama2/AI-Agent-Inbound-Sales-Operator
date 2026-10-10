"""Bounded published-product reads and deterministic customer-facing monetary claims."""

import logging
import re
from decimal import Decimal

from sqlalchemy import Float, String, bindparam, case, cast, func, or_, select, text

from salma.trust import Reply, handoff


def reference(revision_id, sku):
    return f"product:{revision_id}:{sku.casefold()}"


def exposed(row):
    return {**row.data, "id": reference(row.revision_id, row.sku), "product_key": row.product_key}


def scoped_query(business_id, version):
    from salma.product_models import ProductVariant

    if version.business_id != business_id or not version.product_revision_id:
        return select(ProductVariant).where(False)
    return select(ProductVariant).where(
        ProductVariant.business_id == business_id,
        ProductVariant.revision_id == version.product_revision_id,
        ProductVariant.data["archived"].as_boolean().is_not(True),
    )


def lookup(db, business_id, version, sku):
    from salma.product_models import ProductVariant

    row = db.scalar(
        scoped_query(business_id, version).where(ProductVariant.sku == sku.strip().casefold())
    )
    if row is None:
        raise LookupError("Unknown published SKU")
    return exposed(row)


def search(db, business_id, version, query, *, vector=False, limit=5):
    from salma.product_models import ProductVariant

    limit = max(1, min(limit, 5))
    query = query.strip()[:2000]
    if not query or not version.product_revision_id or version.business_id != business_id:
        return []
    base = scoped_query(business_id, version)
    exact = db.scalar(base.where(ProductVariant.sku == query.casefold()))
    if exact:
        return [exposed(exact)]
    # SQL filtering keeps 10,000-row catalogs out of the application and model context.
    ignored = {
        "the",
        "a",
        "is",
        "how",
        "much",
        "price",
        "of",
        "do",
        "you",
        "have",
        "what",
        "بكام",
        "سعر",
        "عندكم",
        "في",
        "هل",
        "كام",
        "عايز",
        "محتاج",
    }
    terms = [
        term
        for term in dict.fromkeys(re.findall(r"[\w-]+", query.casefold()))
        if term not in ignored
    ][:16]
    searchable = func.lower(
        func.concat(
            ProductVariant.data["name"].as_string(),
            " ",
            ProductVariant.data["description"].as_string(),
            " ",
            ProductVariant.data["category"].as_string(),
            " ",
            cast(ProductVariant.data["options"], String),
            " ",
            ProductVariant.sku,
        )
    )
    predicates = [searchable.contains(term, autoescape=True) for term in terms]
    matches = []
    if predicates:
        rank = sum(case((predicate, 1), else_=0) for predicate in predicates)
        matches = db.scalars(
            base.where(or_(*predicates)).order_by(rank.desc(), ProductVariant.sku).limit(limit)
        ).all()
    if (
        vector
        and len(matches) < limit
        and db.scalar(base.where(ProductVariant.embedding.is_not(None)).limit(1))
    ):
        from salma.knowledge import embedding_model

        try:
            query_vector = embedding_model("ar").encode(query, normalize_embeddings=True).tolist()
        except Exception:
            logging.getLogger(__name__).warning("Product semantic search unavailable")
            return [exposed(row) for row in matches[:limit]]
        from salma.db import Vector

        distance = ProductVariant.embedding.op("<=>", return_type=Float)(
            cast(bindparam("product_vector", str(query_vector), type_=String), Vector())
        )
        semantic = db.scalars(
            base.where(
                ProductVariant.embedding.is_not(None),
                distance <= 0.55,
            )
            .order_by(distance, ProductVariant.sku)
            .limit(limit)
        ).all()
        known = {row.sku for row in matches}
        matches += [row for row in semantic if row.sku not in known]
    return [exposed(row) for row in matches[:limit]]


def process_product_index_job(batch_size=32):
    """Durable indexing: committed null vectors are pending work after any restart."""
    from salma.db import session
    from salma.knowledge import embedding_model
    from salma.platform_models import Business, ConfigVersion
    from salma.product_models import ProductVariant

    with session() as db, db.begin():
        if not db.scalar(text("SELECT pg_try_advisory_xact_lock(1734029)")):
            return False
        rows = db.scalars(
            select(ProductVariant)
            .where(
                or_(
                    ProductVariant.revision_id.in_(select(Business.draft_product_revision_id)),
                    ProductVariant.revision_id.in_(
                        select(ConfigVersion.product_revision_id).join(
                            Business,
                            (ConfigVersion.business_id == Business.id)
                            & (ConfigVersion.version == Business.published_version),
                        )
                    ),
                ),
                ProductVariant.embedding.is_(None),
                ProductVariant.data["archived"].as_boolean().is_not(True),
            )
            .order_by(ProductVariant.revision_id, ProductVariant.sku)
            .limit(batch_size)
            .with_for_update(skip_locked=True)
        ).all()
        if not rows:
            return False
        descriptions = [
            " ".join(
                str(row.data.get(key) or "")
                for key in ("name", "description", "category", "options")
            )[:4000]
            for row in rows
        ]
        vectors = embedding_model("ar").encode(descriptions, normalize_embeddings=True)
        for row, vector in zip(rows, vectors, strict=True):
            row.embedding = vector.tolist()
    return True


def contains_value(message, value):
    """Match whole values: size M must never match the word 'much'."""
    value = str(value).strip().casefold()
    if not value:
        return False
    aliases = {
        "blue": ["blue", "أزرق", "ازرق", "زرقاء"],
        "red": ["red", "أحمر", "احمر", "حمراء"],
        "black": ["black", "أسود", "اسود", "سوداء"],
        "white": ["white", "أبيض", "ابيض", "بيضاء"],
        "green": ["green", "أخضر", "اخضر", "خضراء"],
    }
    return any(
        re.search(
            r"(?<!\w)"
            + (r"(?:ال)?" if re.search(r"[\u0600-\u06ff]", option) else "")
            + re.escape(option)
            + r"(?!\w)",
            message.casefold(),
        )
        for option in aliases.get(value, [value])
    )


def contains_sku(message, sku):
    sku = str(sku).strip()
    if len(sku) < 3:
        return message.strip().casefold() == sku.casefold() or bool(
            re.search(r"\bsku\s*[:#-]?\s*" + re.escape(sku) + r"(?!\w)", message, re.I)
        )
    return bool(re.search(r"(?<!\w)" + re.escape(sku) + r"(?!\w)", message, re.I))


def ambiguous_variant(message, selected, candidates):
    value = message.casefold()
    for row in selected:
        siblings = [
            item for item in candidates if item.get("product_key") == row.get("product_key")
        ]
        if len(siblings) < 2 or contains_sku(value, row["sku"]):
            continue
        options = row.get("options") or {}
        if not options or not all(contains_value(value, option) for option in options.values()):
            return True
        matching = [
            item
            for item in siblings
            if all(contains_value(value, option) for option in (item.get("options") or {}).values())
        ]
        if len(matching) != 1:
            return True
    return False


def needs_variant_choice(db, business_id, version, message, selected):
    """Check the full published group, even when bounded search returned fewer variants."""
    from salma.product_models import ProductVariant

    for row in selected:
        if contains_sku(message, row["sku"]):
            continue
        siblings = db.scalars(
            scoped_query(business_id, version)
            .where(ProductVariant.product_key == row["product_key"])
            .order_by(ProductVariant.sku)
        ).all()
        if ambiguous_variant(message, [row], [exposed(item) for item in siblings]):
            return True
    return False


def render_products(decision, products):
    ar = decision.language != "en"
    if not decision.product_skus:
        return handoff(decision.language, "missing_product")
    lines = []
    for sku in decision.product_skus:
        row = products.get(sku.casefold())
        if not row or row["id"] not in decision.evidence_ids:
            return handoff(decision.language, "missing_product_evidence")
        options = ", ".join(f"{key}: {value}" for key, value in (row.get("options") or {}).items())
        name = row["name"] + (f" ({options})" if options else "")
        amount = format(Decimal(row["price"]).normalize(), "f")
        lines.append(f"{name} [{row['sku']}]: {amount} {row['currency']}.")
        status = row.get("stock_status") or "unknown"
        quantity = row.get("stock_quantity")
        stock = (
            {
                "unknown": "التوفر غير معروف",
                "in_stock": "متوفر حسب البيانات",
                "out_of_stock": "غير متوفر حسب البيانات",
            }
            if ar
            else {
                "unknown": "availability unknown",
                "in_stock": "in stock in the snapshot",
                "out_of_stock": "out of stock in the snapshot",
            }
        ).get(status, "unknown")
        if quantity is not None:
            stock += f" ({quantity})"
        day = (row.get("snapshot_at") or "")[:10]
        lines.append(
            f"بيانات التوفر بتاريخ {day}: {stock}. الفريق هيأكد التوفر الحالي قبل الطلب."
            if ar
            else f"Availability snapshot {day}: {stock}. "
            "Staff must confirm current availability before an order."
        )
        if row.get("conditions"):
            lines.append(row["conditions"])
        if row.get("description"):
            lines.append(row["description"][:1000])
        if row.get("url"):
            lines.append(row["url"])
    return Reply("\n".join(lines))
