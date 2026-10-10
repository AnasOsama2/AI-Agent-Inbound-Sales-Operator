# Product catalog implementation ledger

Approved scope: manual products and Excel/UTF-8 CSV imports, 20 MB, 10,000 active variants
per business, immutable tenant-scoped catalog revisions, owner-reviewed draft changes,
explicit publication, rollback, and bounded sales-agent reads. Shopify and live Sheets
remain deferred.

## Contracts and rulings

- Ruling: separate immutable product revisions referenced by published configurations;
  no products in configuration JSON or the prompt. Draft edits create new revisions.
- Ruling: exact case-insensitive workspace SKU is the update identity. Explicit equal
  product name/category groups variants; similar names never merge records.
- Ruling: archived records remain in historical revisions and do not count toward the
  10,000 active variant limit. Imports never delete omitted rows.
- Ruling: an import preview captures the draft revision. Application locks the import
  and business, rejects stale previews, and records the applied revision atomically.
- Ruling: rollback creates a new published configuration referencing the saved product
  revision; the owner's current draft stays intact, matching existing config behavior.
- Ruling: multilingual embeddings are restart-safe background work represented by null
  vectors in durable product rows. Exact SKU and keyword lookup work while indexing.
- Ruling: availability always includes snapshot date and staff confirmation wording.
  Blank quantity is unknown; zero is an explicit out-of-stock snapshot.
- Ruling: private file originals and durable import jobs use existing infrastructure;
  no extra message queue or live commerce connector is introduced.

## Work tracks

- Catalog models, validation, CRUD, migration, publication and rollback.
- Durable import parsing, mapping, previews, generated SKUs and idempotent application.
- Owner Products / Services / Imports UI and responsive verification.
- Sales tools, grounded prices/stock, variant clarification and regression verification.

## Verification

- Complete final backend regression run: 222 passed, including an actual 10,000-variant
  PostgreSQL catalog and rejection of variant 10,001, tenant isolation, publication,
  rollback, import recovery/idempotency, and obsolete outgoing reply suppression.
- Frontend helper tests pass; Vite production build passes. Browser workflow exercised
  manual creation, generated SKU, Arabic/English CSV upload, worksheet/column mapping,
  explicit currency fallback, preview, apply-to-draft and explicit publication.
- Real Groq sales rehearsal: BLUE-M returns 249.5 EGP and unknown dated availability;
  Egyptian Arabic BLUE-L returns 279 EGP and five units with staff confirmation wording.
  Unspecified shirt size requests clarification without handing off.
- Missing products request clarification. Explicit discount negotiation pauses Salma
  with discount_negotiation and acknowledges staff follow-up; published-offer questions
  remain eligible for ordinary answers. The real simulator handoff was verified.
- Products and import controls checked at 866×715 and 1242×715. Setup Assistant and
  Test Chat composers remain within the viewport at both widths.
- Independent review fixed substring matches of short SKUs/sizes, clearing quantity while
  retaining stale stock status, and checking siblings outside bounded search results.
- Database migration 003 applied in the isolated test database and local Docker app.
  Local services rebuilt; originals are retained on the private uploads volume.

## Owner workflow

Open **Products & Services**. Use **Products** for manual entries and **Services** for the
existing service catalog. In **Imports**, download the CSV template or upload a UTF-8 CSV
or XLSX workbook, select the worksheet, map columns, and confirm the default currency
when needed. Review variants, prices, availability and row errors before applying.

Applying only changes the draft. Open **Agent Settings → Publish draft** to make the
revision available to sales conversations. Rollback republishes the saved product revision
while preserving the current draft. Download the catalog with generated SKUs and use those
SKUs in future files: another upload with blank SKUs intentionally creates new records.

Stock is a dated snapshot. Blank quantity with no explicit status means unknown; zero
means out of stock in that snapshot. Staff confirm current availability before orders.
Shopify and Google Sheets remain deferred; external IDs are retained for future sources.
