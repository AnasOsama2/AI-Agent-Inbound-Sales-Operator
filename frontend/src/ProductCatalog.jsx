import React, { useEffect, useRef, useState } from "react";
import { Plus, Upload, Package, FileSpreadsheet, Search } from "lucide-react";
import {
  IMPORT_PROCESSING,
  PRODUCT_FIELDS,
  mappingError,
  productPayload,
  changeStockQuantity,
} from "./product-catalog.js";
import "./product-catalog.css";

const empty = () => ({
  sku: "",
  name: "",
  description: "",
  category: "",
  color: "",
  size: "",
  price: "",
  currency: "EGP",
  stock: "",
  stockStatus: "unknown",
  conditions: "",
  url: "",
});
const human = (value) =>
  String(value || "")
    .replaceAll("_", " ")
    .replace("options.", "");
function Input({ label, value, onChange, multiline, ...props }) {
  const Tag = multiline ? "textarea" : "input";
  return (
    <label className="field">
      <span>{label}</span>
      <Tag
        value={value ?? ""}
        onChange={(event) => onChange(event.target.value)}
        {...props}
      />
    </label>
  );
}

export default function ProductCatalog({ api, base, businessId, children }) {
  const [tab, setTab] = useState("Products");
  const [products, setProducts] = useState([]),
    [total, setTotal] = useState(0),
    [offset, setOffset] = useState(0),
    [query, setQuery] = useState("");
  const [form, setForm] = useState(null),
    [editing, setEditing] = useState(null),
    [archive, setArchive] = useState(null);
  const [imports, setImports] = useState([]),
    [job, setJob] = useState(null),
    [worksheet, setWorksheet] = useState(""),
    [mapping, setMapping] = useState({}),
    [currency, setCurrency] = useState(""),
    [confirmed, setConfirmed] = useState(false);
  const [busy, setBusy] = useState(false),
    [loading, setLoading] = useState(false),
    [error, setError] = useState(""),
    [notice, setNotice] = useState(""),
    [refresh, setRefresh] = useState(0);
  const scope = useRef(0),
    fileInput = useRef(null),
    previewSequence = useRef(0);
  useEffect(() => {
    scope.current += 1;
    setTab("Products");
    setProducts([]);
    setTotal(0);
    setOffset(0);
    setQuery("");
    setForm(null);
    setEditing(null);
    setArchive(null);
    setImports([]);
    setJob(null);
    setMapping({});
    setWorksheet("");
    setCurrency("");
    setConfirmed(false);
    setBusy(false);
    setError("");
    setNotice("");
    return () => {
      scope.current += 1;
    };
  }, [businessId]);
  useEffect(() => {
    if (tab !== "Products") return;
    let active = true;
    setLoading(true);
    const timeout = setTimeout(() => {
      api(
        `${base}/products?q=${encodeURIComponent(query)}&offset=${offset}&limit=50`,
      )
        .then((data) => {
          if (active) {
            setProducts(data.items || []);
            setTotal(data.total || 0);
          }
        })
        .catch((e) => {
          if (active) setError(e.message);
        })
        .finally(() => {
          if (active) setLoading(false);
        });
    }, 200);
    return () => {
      active = false;
      clearTimeout(timeout);
    };
  }, [api, base, businessId, tab, query, offset, refresh]);
  useEffect(() => {
    if (tab !== "Imports") return;
    let active = true;
    api(`${base}/product-imports`)
      .then((data) => {
        if (active) setImports(Array.isArray(data) ? data : data.items || []);
      })
      .catch((e) => {
        if (active) setError(e.message);
      });
    return () => {
      active = false;
    };
  }, [api, base, businessId, tab, refresh]);
  useEffect(() => {
    if (!job?.id || !IMPORT_PROCESSING.has(job.status)) return;
    let active = true;
    const timer = setTimeout(() => {
      api(`${base}/product-imports/${encodeURIComponent(job.id)}`)
        .then((data) => {
          if (active) adoptJob(data, false);
        })
        .catch((e) => {
          if (active) setError(e.message);
        });
    }, 1800);
    return () => {
      active = false;
      clearTimeout(timer);
    };
  }, [api, base, businessId, job]);

  function adoptJob(data, reset = true) {
    setJob(data);
    setImports((old) =>
      old.map((item) =>
        item.id === data.id ? { ...item, status: data.status } : item,
      ),
    );
    if (reset || !worksheet) {
      setWorksheet(data.worksheet || data.worksheets?.[0]?.name || "");
      setMapping(data.mapping || {});
      setCurrency(data.default_currency || "");
      setConfirmed(false);
    }
  }
  async function action(callback, message) {
    const generation = scope.current;
    setBusy(true);
    setError("");
    setNotice("");
    try {
      await callback(() => generation === scope.current);
      if (generation === scope.current && message) setNotice(message);
    } catch (e) {
      if (generation === scope.current) setError(e.message);
    } finally {
      if (generation === scope.current) setBusy(false);
    }
  }
  function changeMapping(field, value) {
    previewSequence.current += 1;
    setMapping((old) => ({ ...old, [field]: value }));
    setConfirmed(false);
  }
  const processing = IMPORT_PROCESSING.has(job?.status);
  const ready = ["ready", "validated", "awaiting_review"].includes(job?.status);
  const invalid = Number(job?.counts?.invalid || 0);
  const currentSheet = job?.worksheets?.find(
    (sheet) => sheet.name === worksheet,
  );
  const editField = (name, value) =>
    setForm((old) => ({ ...old, [name]: value }));
  const jobPath = job?.id
    ? `${base}/product-imports/${encodeURIComponent(job.id)}`
    : "";
  return (
    <div className="product-catalog">
      <div
        className="catalog-tabs"
        role="tablist"
        aria-label="Catalog sections"
      >
        {["Products", "Services", "Imports"].map((name) => (
          <button
            key={name}
            role="tab"
            aria-selected={tab === name}
            className={tab === name ? "active" : ""}
            onClick={() => {
              setTab(name);
              setError("");
              setNotice("");
            }}
          >
            {name}
          </button>
        ))}
      </div>
      {tab === "Services" ? (
        <div className="page-scroll catalog-scroll">{children}</div>
      ) : (
        <div className="page-scroll catalog-scroll">
          <div className="catalog-draft-note">
            <span className="catalog-badge">Draft catalog</span>
            <span>
              Changes reach customers only after you review and publish your
              business configuration.
            </span>
          </div>
          {error && (
            <div className="catalog-message error" role="alert">
              {error}
            </div>
          )}
          {notice && (
            <div className="catalog-message" role="status">
              {notice}
            </div>
          )}
          {tab === "Products" && (
            <>
              <div className="section-heading catalog-heading">
                <div>
                  <h2>Products</h2>
                  <p className="muted">
                    Manage the products Salma can recommend and quote.
                  </p>
                </div>
                <button
                  className="primary"
                  onClick={() => {
                    setEditing(null);
                    setForm(empty());
                    setArchive(null);
                  }}
                >
                  <Plus size={16} /> Add product
                </button>
              </div>
              <div className="catalog-search">
                <Search size={17} />
                <input
                  aria-label="Search products"
                  placeholder="Search by name, SKU or category"
                  value={query}
                  onChange={(e) => {
                    setQuery(e.target.value);
                    setOffset(0);
                  }}
                />
              </div>
              {form && (
                <form
                  className="card catalog-editor"
                  onSubmit={(e) => {
                    e.preventDefault();
                    action(async (active) => {
                      await api(
                        editing
                          ? `${base}/products/${encodeURIComponent(editing)}`
                          : `${base}/products`,
                        {
                          method: editing ? "PUT" : "POST",
                          body: JSON.stringify(productPayload(form)),
                        },
                      );
                      if (active()) {
                        setForm(null);
                        setRefresh((n) => n + 1);
                      }
                    }, "Product saved to draft. Review and publish when ready.");
                  }}
                >
                  <h2>{editing ? "Edit product" : "New product"}</h2>
                  <div className="catalog-form-grid">
                    <Input
                      label="SKU (blank generates an identifier)"
                      value={form.sku}
                      onChange={(v) => editField("sku", v)}
                      disabled={!!editing}
                      maxLength={120}
                    />
                    <Input
                      label="Product name"
                      value={form.name}
                      onChange={(v) => editField("name", v)}
                      required
                    />
                    <Input
                      label="Description"
                      value={form.description}
                      onChange={(v) => editField("description", v)}
                      multiline
                      rows={3}
                    />
                    <Input
                      label="Category"
                      value={form.category}
                      onChange={(v) => editField("category", v)}
                    />
                    <Input
                      label="Color"
                      value={form.color}
                      onChange={(v) => editField("color", v)}
                    />
                    <Input
                      label="Size"
                      value={form.size}
                      onChange={(v) => editField("size", v)}
                    />
                    <Input
                      label="Price"
                      value={form.price}
                      onChange={(v) => editField("price", v)}
                      required
                      type="number"
                      min="0"
                      step="0.01"
                    />
                    <Input
                      label="Currency"
                      value={form.currency}
                      onChange={(v) => editField("currency", v.toUpperCase())}
                      required
                      pattern="[A-Z]{3}"
                      maxLength={3}
                    />
                    <Input
                      label="Stock quantity (optional)"
                      value={form.stock}
                      onChange={(v) =>
                        setForm((old) => changeStockQuantity(old, v))
                      }
                      type="number"
                      min="0"
                      step="1"
                    />
                    <label className="field">
                      <span>Stock status (when quantity is unknown)</span>
                      <select
                        value={form.stockStatus}
                        disabled={form.stock !== ""}
                        onChange={(e) =>
                          editField("stockStatus", e.target.value)
                        }
                      >
                        <option value="unknown">Unknown</option>
                        <option value="in_stock">In stock</option>
                        <option value="out_of_stock">Out of stock</option>
                      </select>
                    </label>
                    <Input
                      label="Product URL"
                      value={form.url}
                      onChange={(v) => editField("url", v)}
                      type="url"
                    />
                    <Input
                      label="Conditions"
                      value={form.conditions}
                      onChange={(v) => editField("conditions", v)}
                      multiline
                      rows={2}
                    />
                  </div>
                  <div className="button-row">
                    <button className="primary" disabled={busy}>
                      Save to draft
                    </button>
                    <button
                      type="button"
                      className="secondary"
                      disabled={busy}
                      onClick={() => setForm(null)}
                    >
                      Cancel
                    </button>
                  </div>
                </form>
              )}
              {archive && (
                <div className="card catalog-archive">
                  <p>
                    Archive <strong>{archive.name}</strong> ({archive.sku}) from
                    the draft catalog?
                  </p>
                  <div className="button-row">
                    <button
                      className="secondary"
                      disabled={busy}
                      onClick={() =>
                        action(async (active) => {
                          await api(
                            `${base}/products/${encodeURIComponent(archive.sku)}/archive`,
                            { method: "POST" },
                          );
                          if (active()) {
                            setArchive(null);
                            setOffset(0);
                            setRefresh((n) => n + 1);
                          }
                        }, "Product archived in draft. Publish to update customer answers.")
                      }
                    >
                      Confirm archive
                    </button>
                    <button
                      className="secondary"
                      disabled={busy}
                      onClick={() => setArchive(null)}
                    >
                      Cancel
                    </button>
                  </div>
                </div>
              )}
              <section className="card catalog-products" aria-busy={loading}>
                {loading ? (
                  <p className="muted" role="status">
                    Loading products…
                  </p>
                ) : !products.length ? (
                  <div className="catalog-empty">
                    <Package size={30} />
                    <h3>
                      {query
                        ? "No matching products"
                        : "Build your product catalog"}
                    </h3>
                    <p className="muted">
                      Add a product manually or upload a spreadsheet in Imports.
                    </p>
                  </div>
                ) : (
                  <div className="catalog-table-wrap">
                    <table className="catalog-table">
                      <thead>
                        <tr>
                          <th>Product / SKU</th>
                          <th>Price</th>
                          <th>Stock</th>
                          <th>Actions</th>
                        </tr>
                      </thead>
                      <tbody>
                        {products.map((product) => (
                          <tr key={product.sku}>
                            <td>
                              <strong>{product.name}</strong>
                              <small>
                                {product.sku}
                                {product.category
                                  ? ` · ${product.category}`
                                  : ""}
                              </small>
                              {(product.options?.color ||
                                product.options?.size) && (
                                <small>
                                  {[
                                    product.options?.color,
                                    product.options?.size,
                                  ]
                                    .filter(Boolean)
                                    .join(" / ")}
                                </small>
                              )}
                              {product.archived && (
                                <span className="catalog-badge">Archived</span>
                              )}
                            </td>
                            <td>
                              {product.price} {product.currency}
                            </td>
                            <td>
                              {product.stock_quantity == null
                                ? human(product.stock_status || "unknown")
                                : product.stock_quantity}
                            </td>
                            <td>
                              <div className="catalog-row-actions">
                                <button
                                  className="secondary"
                                  onClick={() => {
                                    setEditing(product.sku);
                                    setForm({
                                      ...empty(),
                                      ...product,
                                      color: product.options?.color || "",
                                      size: product.options?.size || "",
                                      stock: product.stock_quantity ?? "",
                                      stockStatus:
                                        product.stock_status || "unknown",
                                      price: product.price ?? "",
                                    });
                                    setArchive(null);
                                  }}
                                >
                                  Edit
                                </button>
                                {!product.archived && (
                                  <button
                                    className="secondary"
                                    onClick={() => {
                                      setArchive(product);
                                      setForm(null);
                                    }}
                                  >
                                    Archive
                                  </button>
                                )}
                              </div>
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
                <div className="catalog-pagination">
                  <span className="muted">
                    {total
                      ? `${offset + 1}–${Math.min(offset + 50, total)} of ${total}`
                      : "0 products"}
                  </span>
                  <div className="button-row">
                    <button
                      className="secondary"
                      disabled={loading || offset === 0}
                      onClick={() => setOffset(Math.max(0, offset - 50))}
                    >
                      Previous
                    </button>
                    <button
                      className="secondary"
                      disabled={loading || offset + 50 >= total}
                      onClick={() => setOffset(offset + 50)}
                    >
                      Next
                    </button>
                  </div>
                </div>
              </section>
            </>
          )}
          {tab === "Imports" && (
            <>
              <div className="section-heading catalog-heading">
                <div>
                  <h2>Spreadsheet imports</h2>
                  <p className="muted">
                    Upload, map columns, review changes, then apply to draft.
                  </p>
                </div>
                <a
                  className="catalog-link"
                  href={`${base}/product-imports/template`}
                  download
                >
                  Download CSV template
                </a>
              </div>
              <section className="card catalog-upload">
                <FileSpreadsheet size={28} />
                <div>
                  <h3>Import products</h3>
                  <p className="muted">
                    CSV or Excel (.xlsx). Blank SKUs can be generated during
                    import.
                  </p>
                </div>
                <input
                  ref={fileInput}
                  className="catalog-file-input"
                  aria-label="Choose catalog spreadsheet"
                  type="file"
                  accept=".csv,.xlsx"
                  disabled={busy}
                  onChange={(e) => {
                    const file = e.target.files?.[0];
                    if (!file) return;
                    action(async (active) => {
                      const body = new FormData();
                      body.append("file", file);
                      const result = await api(`${base}/product-imports`, {
                        method: "POST",
                        body,
                      });
                      if (active()) {
                        adoptJob(result);
                        setRefresh((n) => n + 1);
                      }
                    });
                    e.target.value = "";
                  }}
                />
                <button
                  className="primary"
                  disabled={busy}
                  onClick={() => fileInput.current?.click()}
                >
                  <Upload size={16} /> Upload file
                </button>
              </section>
              {!!imports.length && (
                <label className="field">
                  <span>Import history</span>
                  <select
                    value={job?.id || ""}
                    disabled={busy}
                    onChange={(e) => {
                      const id = e.target.value;
                      if (!id) {
                        setJob(null);
                        return;
                      }
                      action(async (active) => {
                        const data = await api(
                          `${base}/product-imports/${encodeURIComponent(id)}`,
                        );
                        if (active()) adoptJob(data);
                      });
                    }}
                  >
                    <option value="">Select an import</option>
                    {imports.map((item) => (
                      <option key={item.id} value={item.id}>
                        {item.filename} — {human(item.status)}
                      </option>
                    ))}
                  </select>
                </label>
              )}
              {job && (
                <section className="card catalog-import-preview">
                  <div className="section-heading">
                    <h3>{job.filename || "Import preview"}</h3>
                    <span className="catalog-badge">{human(job.status)}</span>
                  </div>
                  {processing && (
                    <p role="status" className="muted">
                      Processing your spreadsheet… This view updates
                      automatically.
                    </p>
                  )}
                  {job.error && (
                    <p role="alert" className="error">
                      {typeof job.error === "string"
                        ? job.error
                        : JSON.stringify(job.error)}
                    </p>
                  )}
                  {!!job.worksheets?.length && job.status !== "applied" && (
                    <form
                      onSubmit={(e) => {
                        e.preventDefault();
                        const problem = mappingError(mapping, currency);
                        if (problem) {
                          setError(problem);
                          return;
                        }
                        const sequence = previewSequence.current;
                        action(async (active) => {
                          const data = await api(`${jobPath}/mapping`, {
                            method: "PUT",
                            body: JSON.stringify({
                              worksheet,
                              mapping: Object.fromEntries(
                                Object.entries(mapping).filter(
                                  ([, value]) => value,
                                ),
                              ),
                              default_currency: currency || null,
                            }),
                          });
                          if (active()) {
                            setJob(data);
                            setConfirmed(sequence === previewSequence.current);
                          }
                        });
                      }}
                    >
                      <div className="catalog-form-grid">
                        <label className="field">
                          <span>Worksheet</span>
                          <select
                            value={worksheet}
                            disabled={processing || busy}
                            onChange={(e) => {
                              previewSequence.current += 1;
                              setWorksheet(e.target.value);
                              setMapping({});
                              setConfirmed(false);
                            }}
                          >
                            {job.worksheets.map((sheet) => (
                              <option key={sheet.name} value={sheet.name}>
                                {sheet.name}
                              </option>
                            ))}
                          </select>
                        </label>
                        <Input
                          label="Default currency (when currency column is absent)"
                          value={currency}
                          onChange={(v) => {
                            previewSequence.current += 1;
                            setCurrency(v.toUpperCase());
                            setConfirmed(false);
                          }}
                          pattern="[A-Z]{3}"
                          maxLength={3}
                          disabled={processing || busy}
                        />
                        {PRODUCT_FIELDS.map((field) => (
                          <label className="field" key={field}>
                            <span>
                              {human(field)}
                              {["name", "price"].includes(field) ? " *" : ""}
                            </span>
                            <select
                              aria-label={`Map ${human(field)} column`}
                              value={mapping[field] || ""}
                              disabled={processing || busy}
                              onChange={(e) =>
                                changeMapping(field, e.target.value)
                              }
                            >
                              <option value="">
                                {field === "sku"
                                  ? "Generate SKU when blank"
                                  : "Not mapped"}
                              </option>
                              {(currentSheet?.columns || []).map((column) => (
                                <option key={column} value={column}>
                                  {column}
                                </option>
                              ))}
                            </select>
                          </label>
                        ))}
                      </div>
                      <button
                        className="secondary"
                        disabled={busy || processing}
                      >
                        Confirm mapping and preview
                      </button>
                      <p className="muted catalog-help">
                        Confirm the worksheet, column mapping and default
                        currency before applying. Missing stock stays unknown.
                      </p>
                    </form>
                  )}
                  {job.counts && (
                    <div className="catalog-counts">
                      {["new", "updated", "unchanged", "invalid"].map(
                        (name) => (
                          <div key={name}>
                            <strong>{job.counts[name] || 0}</strong>
                            <span>{human(name)}</span>
                          </div>
                        ),
                      )}
                    </div>
                  )}
                  {!!job.rows?.length && (
                    <>
                      <div className="catalog-table-wrap">
                        <table className="catalog-table">
                          <thead>
                            <tr>
                              <th>Row / SKU</th>
                              <th>Product</th>
                              <th>Status / errors</th>
                            </tr>
                          </thead>
                          <tbody>
                            {job.rows.map((row, index) => (
                              <tr key={`${row.row_number}-${index}`}>
                                <td>
                                  {row.row_number}
                                  <small>
                                    {row.sku ||
                                      row.product?.sku ||
                                      "Generated on apply"}
                                  </small>
                                </td>
                                <td>
                                  {row.product?.name || "—"}
                                  <small>
                                    {row.product?.price} {row.product?.currency}
                                  </small>
                                  {row.product && (
                                    <>
                                      <small>
                                        {Object.entries(
                                          row.product.options || {},
                                        )
                                          .map(
                                            ([key, value]) =>
                                              `${key}: ${value}`,
                                          )
                                          .join(" · ")}
                                      </small>
                                      <small>
                                        Stock snapshot:{" "}
                                        {row.product.stock_quantity ??
                                          human(
                                            row.product.stock_status ||
                                              "unknown",
                                          )}
                                      </small>
                                      <details className="catalog-row-details">
                                        <summary>More product details</summary>
                                        <dl>
                                          {[
                                            "description",
                                            "category",
                                            "conditions",
                                            "url",
                                            "stock_status",
                                            "snapshot_at",
                                            "source_ref",
                                          ].map((key) => (
                                            <React.Fragment key={key}>
                                              <dt>{human(key)}</dt>
                                              <dd>{row.product[key] || "—"}</dd>
                                            </React.Fragment>
                                          ))}
                                        </dl>
                                      </details>
                                    </>
                                  )}
                                </td>
                                <td>
                                  {human(row.action)}
                                  {(Array.isArray(row.errors)
                                    ? row.errors
                                    : row.errors
                                      ? [row.errors]
                                      : []
                                  ).map((message, i) => (
                                    <small className="error" key={i}>
                                      {typeof message === "string"
                                        ? message
                                        : JSON.stringify(message)}
                                    </small>
                                  ))}
                                </td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      </div>
                      <div className="catalog-pagination">
                        <span className="muted">
                          {job.total || job.rows.length} rows · preview{" "}
                          {Number(job.offset || 0) + 1}–
                          {Number(job.offset || 0) + job.rows.length}
                        </span>
                        <div className="button-row">
                          <button
                            className="secondary"
                            disabled={busy || !job.offset}
                            onClick={() =>
                              action(async (active) => {
                                const data = await api(
                                  `${jobPath}?offset=${Math.max(0, Number(job.offset) - Number(job.limit || 50))}&limit=${job.limit || 50}`,
                                );
                                if (active()) setJob(data);
                              })
                            }
                          >
                            Previous
                          </button>
                          <button
                            className="secondary"
                            disabled={
                              busy ||
                              Number(job.offset || 0) + job.rows.length >=
                                (job.total || job.rows.length)
                            }
                            onClick={() =>
                              action(async (active) => {
                                const data = await api(
                                  `${jobPath}?offset=${Number(job.offset || 0) + Number(job.limit || 50)}&limit=${job.limit || 50}`,
                                );
                                if (active()) setJob(data);
                              })
                            }
                          >
                            Next
                          </button>
                        </div>
                      </div>
                    </>
                  )}
                  {invalid > 0 && (
                    <p className="error">
                      Fix the {invalid} invalid rows in your spreadsheet and
                      upload again before applying.
                    </p>
                  )}
                  {job.status !== "applied" && (
                    <div className="catalog-apply">
                      <button
                        className="primary"
                        disabled={
                          busy ||
                          processing ||
                          !ready ||
                          !confirmed ||
                          invalid > 0
                        }
                        onClick={() =>
                          action(async (active) => {
                            const data = await api(`${jobPath}/apply`, {
                              method: "POST",
                            });
                            if (active()) {
                              setJob(data);
                              setRefresh((n) => n + 1);
                            }
                          }, "Import submitted to draft. Review and publish when ready.")
                        }
                      >
                        Apply reviewed import to draft
                      </button>
                      <small className="muted">
                        {!confirmed
                          ? "Confirm the mapping and inspect the preview first."
                          : "Applying does not publish your catalog."}
                      </small>
                    </div>
                  )}
                  {job.status === "applied" && (
                    <p className="catalog-message">
                      Applied to draft. Review and publish to make these
                      products available to customers.
                    </p>
                  )}
                  {job.status === "applied" && (
                    <a
                      className="catalog-link"
                      href={`${jobPath}/generated-skus`}
                      download
                    >
                      Download catalog with SKUs
                    </a>
                  )}
                </section>
              )}
              <p className="muted catalog-future">
                Shopify connection is a future optional integration.
              </p>
            </>
          )}
        </div>
      )}
    </div>
  );
}
