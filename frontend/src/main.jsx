import React, { useCallback, useEffect, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import { requestAdminReply } from "./admin-chat.js";
import ProductCatalog from "./ProductCatalog.jsx";
import Overview from "./Overview.jsx";
import Monitoring from "./Monitoring.jsx";
import { ProviderTools, ChannelAccess, SocialConnections } from "./ProviderAccess.jsx";
import {
  Leaf,
  LayoutDashboard,
  Bot,
  BookOpen,
  Tag,
  SlidersHorizontal,
  Radio,
  MessagesSquare,
  Inbox,
  ArrowUp,
  Plus,
  LogOut,
  ChevronRight,
  ShieldCheck,
  RefreshCw,
  Upload,
  Trash2,
} from "lucide-react";
import "./style.css";
const NAV = [
  ["Overview", LayoutDashboard],
  ["Setup Assistant", Bot],
  ["Knowledge", BookOpen],
  ["Products & Services", Tag],
  ["Agent Settings", SlidersHorizontal],
  ["Channels", Radio],
  ["Test Chat", MessagesSquare],
  ["Conversations", Inbox],
  ["Monitoring", SlidersHorizontal],
];
const list = (v, key) => (Array.isArray(v) ? v : v?.[key] || []);
const pretty = (v) => JSON.stringify(v, null, 2);
const label = (v) => String(v || "").replaceAll("_", " ");
const emptyService = () => ({
  id: crypto.randomUUID(),
  name: "",
  currency: "EGP",
  price_type: "fixed",
  amount: 0,
  unit: "",
  description: "",
  description_ar: "",
  conditions: "",
  offers: [],
});
function Field({ label: name, value, onChange, multiline = false, ...props }) {
  return (
    <label className="field">
      <span>{name}</span>
      {multiline ? (
        <textarea
          value={value ?? ""}
          onChange={(e) => onChange(e.target.value)}
          rows={3}
          {...props}
        />
      ) : (
        <input
          value={value ?? ""}
          onChange={(e) => onChange(e.target.value)}
          {...props}
        />
      )}
    </label>
  );
}
function JsonField({ label: name, value, onChange, hint }) {
  const [raw, setRaw] = useState(pretty(value));
  const [err, setErr] = useState("");
  useEffect(() => setRaw(pretty(value)), [value]);
  return (
    <label className="field">
      <span>{name}</span>
      <textarea
        className="code"
        rows={5}
        value={raw}
        onChange={(e) => {
          setRaw(e.target.value);
          try {
            onChange(JSON.parse(e.target.value));
            setErr("");
            e.target.setCustomValidity("");
          } catch {
            setErr("Enter valid JSON before saving.");
            e.target.setCustomValidity("Enter valid JSON before saving.");
          }
        }}
      />
      <small className={err ? "error" : "muted"}>{err || hint}</small>
    </label>
  );
}
function App() {
  const [auth, setAuth] = useState(null),
    [boot, setBoot] = useState(true),
    [businesses, setBusinesses] = useState([]),
    [bid, setBid] = useState(""),
    [view, setView] = useState("Overview"),
    [conversationTarget, setConversationTarget] = useState(null),
    [error, setError] = useState(""),
    [notice, setNotice] = useState(""),
    [busy, setBusy] = useState(false),
    [draft, setDraft] = useState(null),
    [published, setPublished] = useState(null),
    [prompt, setPrompt] = useState(""),
    [versions, setVersions] = useState([]),
    [documents, setDocuments] = useState([]),
    [channels, setChannels] = useState([]),
    [adminMessages, setAdminMessages] = useState([]),
    [proposal, setProposal] = useState(null),
    [adminText, setAdminText] = useState(""),
    [adminError, setAdminError] = useState(""),
    [adminPending, setAdminPending] = useState(false),
    [qr, setQr] = useState({}),
    [invite, setInvite] = useState("");
  const [email, setEmail] = useState(""),
    [password, setPassword] = useState("");
  const inviteToken =
    new URLSearchParams(window.location.search).get("invite") ||
    new URLSearchParams(window.location.search).get("token");
  const api = useCallback(
    async (path, opts = {}) => {
      const multipart = opts.body instanceof FormData;
      const response = await fetch(path, {
        credentials: "same-origin",
        ...opts,
        headers: {
          ...(!multipart ? { "Content-Type": "application/json" } : {}),
          ...(auth?.csrf_token ? { "X-CSRF-Token": auth.csrf_token } : {}),
          ...opts.headers,
        },
      });
      if (!response.ok) {
        if (response.status === 401) setAuth(null);
        const b = await response.json().catch(() => ({}));
        throw new Error(
          typeof b.detail === "string"
            ? b.detail
            : `Request failed (${response.status})`,
        );
      }
      if (response.status === 204) return {};
      return response.json();
    },
    [auth?.csrf_token],
  );
  const establish = useCallback((data) => {
    setAuth(data);
    setBusinesses(data.businesses || []);
    setBid((old) =>
      data.businesses?.some((b) => b.id === old)
        ? old
        : data.businesses?.[0]?.id || "",
    );
  }, []);
  useEffect(() => {
    fetch("/api/auth/me", { credentials: "same-origin" })
      .then(async (r) => {
        if (r.ok) establish(await r.json());
      })
      .catch(() =>
        setError("Unable to reach Salma. Check that the API is running."),
      )
      .finally(() => setBoot(false));
  }, [establish]);
  const run = async (fn, success) => {
    setBusy(true);
    setError("");
    setNotice("");
    try {
      await fn();
      if (success) setNotice(success);
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  };
  const base = `/api/businesses/${bid}`;
  const workspaceRef = useRef(bid);
  workspaceRef.current = bid;
  const load = useCallback(async () => {
    if (!bid) return;
    const [d, v, docs, chats, m] = await Promise.all([
      api(`/api/businesses/${bid}/draft`),
      api(`/api/businesses/${bid}/versions`),
      api(`/api/businesses/${bid}/documents`),
      api(`/api/businesses/${bid}/channels`),
      api(`/api/businesses/${bid}/admin/messages`),
    ]);
    if (workspaceRef.current !== bid) return;
    setDraft(d.config);
    setPublished(d.published_version);
    setPrompt(d.effective_prompt || "");
    setVersions(list(v, "versions"));
    setDocuments(list(docs, "documents"));
    setChannels(list(chats, "channels"));
    setAdminMessages(list(m, "messages"));
    setProposal(list(m, "messages").at(-1)?.proposal || null);
  }, [bid, api]);
  useEffect(() => {
    setDraft(null);
    setPublished(null);
    setConversationTarget(null);
    setAdminMessages([]);
    setProposal(null);
    setAdminText("");
    setAdminError("");
    setAdminPending(false);
    setError("");
    setNotice("");
    if (bid) load().catch((e) => setError(e.message));
  }, [bid, load]);
  useEffect(() => {
    if (!bid || view !== "Knowledge") return;
    const id = setInterval(
      () =>
        api(`${base}/documents`)
          .then((d) => setDocuments(list(d, "documents")))
          .catch(() => {}),
      4000,
    );
    return () => clearInterval(id);
  }, [bid, view, api, base]);
  const set = (key, value) => setDraft((d) => ({ ...d, [key]: value }));
  const save = async () => {
    const invalid = document.querySelector(
      "input:invalid, textarea:invalid, select:invalid",
    );
    if (invalid) {
      invalid.reportValidity();
      throw new Error("Correct the highlighted field before saving.");
    }
    const d = await api(`${base}/draft`, {
      method: "PUT",
      body: JSON.stringify(draft),
    });
    setDraft(d.config);
    setPrompt(d.effective_prompt || "");
  };
  const login = (e) => {
    e.preventDefault();
    run(async () => {
      const data = await api(
        inviteToken ? "/api/auth/accept-invite" : "/api/auth/login",
        {
          method: "POST",
          body: JSON.stringify(
            inviteToken
              ? { token: inviteToken, password }
              : { email, password },
          ),
        },
      );
      establish(data);
      setPassword("");
      if (inviteToken)
        window.history.replaceState({}, "", window.location.pathname);
    });
  };
  const operator =
    auth?.user?.role === "operator" || auth?.user?.role === "platform_operator";
  if (boot)
    return (
      <div className="auth-screen">
        <Leaf className="loading" />
        <p>Opening your workspace…</p>
      </div>
    );
  if (!auth)
    return (
      <div className="auth-screen">
        <section className="auth-card">
          <Brand />
          <p className="eyebrow">YOUR BUSINESS. YOUR SALMA.</p>
          <h1>
            {inviteToken
              ? "Welcome to your workspace"
              : "Good conversations start here."}
          </h1>
          <p className="muted">
            {inviteToken
              ? "Choose a password to accept your invitation."
              : "Sign in to build and manage your inbound sales agent."}
          </p>
          <form onSubmit={login}>
            {!inviteToken && (
              <Field
                label="Email"
                type="email"
                required
                autoComplete="username"
                value={email}
                onChange={setEmail}
              />
            )}
            <Field
              label={inviteToken ? "Choose password" : "Password"}
              type="password"
              minLength={12}
              required
              autoComplete={inviteToken ? "new-password" : "current-password"}
              value={password}
              onChange={setPassword}
            />
            {error && (
              <p className="error" role="alert">
                {error}
              </p>
            )}
            <button className="primary wide" disabled={busy}>
              {busy
                ? "Connecting…"
                : inviteToken
                  ? "Accept invitation"
                  : "Sign in"}
              <ChevronRight size={17} />
            </button>
          </form>
          <small className="muted">
            Invite-only access · Contact your platform operator for a workspace.
          </small>
        </section>
      </div>
    );
  return (
    <div className="admin-shell">
      <aside className="nav-panel">
        <Brand />
        <label className="workspace-picker">
          <span>BUSINESS WORKSPACE</span>
          <select
            value={bid}
            onChange={(e) => setBid(e.target.value)}
            aria-label="Business workspace"
          >
            <option value="" disabled>
              Select a workspace
            </option>
            {businesses.map((b) => (
              <option key={b.id} value={b.id}>
                {b.name}
              </option>
            ))}
          </select>
        </label>
        <button
          className="secondary"
          onClick={() => {
            setBid("");
            setView("Overview");
          }}
        >
          <Plus size={16} /> New business workspace
        </button>
        <nav>
          {NAV.map(([name, Icon]) => (
            <button
              key={name}
              className={view === name ? "active" : ""}
              onClick={() => {
                setConversationTarget(null);
                setView(name);
                setError("");
                setNotice("");
              }}
            >
              <Icon size={18} />
              {name}
              {name === "Test Chat" && <span className="nav-dot" />}
            </button>
          ))}
        </nav>
        <div className="nav-bottom">
          <span className="user-email">{auth.user.email}</span>
          <button
            onClick={() =>
              run(async () => {
                await api("/api/auth/logout", { method: "POST" });
                setAuth(null);
                setBid("");
              })
            }
          >
            <LogOut size={16} />
            Sign out
          </button>
        </div>
      </aside>
      <main className="admin-main">
        <header className="page-header">
          <div>
            <p className="eyebrow">
              {businesses.find((b) => b.id === bid)?.name || "SALMA PLATFORM"}
            </p>
            <h1>{view}</h1>
          </div>
          <span className={`status-chip ${published ? "good" : ""}`}>
            <span className="dot" />
            {published ? `Published v${published}` : "Not published"}
          </span>
        </header>
        {error && (
          <div className="banner error" role="alert">
            {error}
            <button onClick={() => setError("")}>×</button>
          </div>
        )}
        {notice && (
          <div className="banner success" role="status">
            {notice}
            <button onClick={() => setNotice("")}>×</button>
          </div>
        )}
        {!bid ? (
          <div className="page-scroll">
            <section className="card">
              <h2>Your business starts here</h2>
              <p className="muted">
                Create a fresh workspace for your business. Its knowledge,
                prices, settings and conversations start empty.
              </p>
              <WorkspaceCreator
                api={api}
                run={run}
                onCreated={async (b) => {
                  setBusinesses((old) => [...old, b]);
                  setBid(b.id);
                  setView("Setup Assistant");
                }}
              />
            </section>
          </div>
        ) : !draft ? (
          <div className="page-scroll muted">Loading workspace…</div>
        ) : (
          <>
            {view === "Monitoring" && <Monitoring api={api} base={base} operator={operator} />}
            {view === "Overview" && (
              <div className="page-scroll">
                <Overview
                  key={bid}
                  api={api}
                  base={base}
                  draft={draft}
                  navigate={(name) => {
                    setConversationTarget(null);
                    setView(name);
                  }}
                  openConversation={(key) => {
                    setConversationTarget(key);
                    setView("Conversations");
                  }}
                />
                {operator && (
                  <section className="card">
                    <h2>Platform operator</h2>
                    <WorkspaceCreator
                      api={api}
                      run={run}
                      onCreated={(b) => {
                        setBusinesses((old) => [...old, b]);
                        setBid(b.id);
                      }}
                    />
                    <Invite
                      api={api}
                      base={base}
                      run={run}
                      value={invite}
                      setValue={setInvite}
                    />
                  </section>
                )}
              </div>
            )}
            {view === "Setup Assistant" && (
              <div className="setup-grid">
                <section className="assistant-chat card">
                  <div className="section-heading">
                    <div>
                      <h2>Your setup partner</h2>
                      <p className="muted">
                        Salma's setup assistant for any business. Review every
                        suggested change.
                      </p>
                    </div>
                    <Bot size={22} />
                  </div>
                  <div className="setup-workspace-context" role="note">
                    <strong>
                      Editing: {businesses.find((b) => b.id === bid)?.name}
                    </strong>
                    <span>
                      This chat configures this workspace only. For another
                      business, select its workspace
                      {" or use New business workspace."}
                    </span>
                  </div>
                  <div className="admin-messages">
                    {!adminMessages.length && (
                      <div className="empty-state">
                        <Bot size={34} />
                        <h3>Let’s get to know your business.</h3>
                        <p>
                          Tell me what you sell, who your customers are, and
                          when you want a person to step in.
                        </p>
                      </div>
                    )}
                    {adminMessages.map((m) => (
                      <div key={m.id} className={`admin-message ${m.role}`}>
                        <small>
                          {m.role === "user" ? "You" : "Setup assistant"}
                        </small>
                        <p dir="auto">{m.text}</p>
                      </div>
                    ))}
                  </div>
                  {adminPending && (
                    <p className="muted" role="status">
                      Salma is preparing a reply…
                    </p>
                  )}
                  {adminError && (
                    <p className="error" role="alert">
                      {adminError}
                    </p>
                  )}
                  {proposal && (
                    <details className="proposal">
                      <summary>Review suggested draft changes</summary>
                      <pre>{pretty(proposal)}</pre>
                      <button
                        className="primary"
                        disabled={busy}
                        onClick={() =>
                          run(async () => {
                            await api(`${base}/draft`, {
                              method: "PUT",
                              body: JSON.stringify({ ...draft, ...proposal }),
                            });
                            await load();
                          }, "Suggested changes applied to draft.")
                        }
                      >
                        Apply to draft
                      </button>
                    </details>
                  )}
                  <Composer
                    value={adminText}
                    setValue={setAdminText}
                    busy={busy}
                    placeholder="Tell Salma about your business…"
                    send={() =>
                      run(async () => {
                        const requestBusinessId = bid;
                        setAdminError("");
                        setAdminPending(true);
                        try {
                          await requestAdminReply({
                            businessId: requestBusinessId,
                            text: adminText,
                            api,
                            isCurrent: () =>
                              workspaceRef.current === requestBusinessId,
                            onResult: (result, history) => {
                              setAdminText("");
                              setAdminMessages(list(history, "messages"));
                              setProposal(
                                result.proposal ||
                                  result.message?.proposal ||
                                  null,
                              );
                            },
                          });
                        } catch (err) {
                          if (workspaceRef.current === requestBusinessId) {
                            setAdminError(err.message);
                          }
                        } finally {
                          if (workspaceRef.current === requestBusinessId) {
                            setAdminPending(false);
                          }
                        }
                      })
                    }
                  />
                </section>
                <section className="card form-scroll">
                  <div className="section-heading">
                    <h2>Business profile</h2>
                    <span className="draft-tag">Draft</span>
                  </div>
                  <Field
                    label="Business name"
                    value={draft.name}
                    onChange={(v) => set("name", v)}
                  />
                  <Field
                    label="What your business does"
                    multiline
                    value={draft.description}
                    onChange={(v) => set("description", v)}
                  />
                  <Field
                    label="Timezone"
                    value={draft.timezone}
                    onChange={(v) => set("timezone", v)}
                  />
                  <Field
                    label="Languages (comma separated)"
                    value={(draft.languages || []).join(", ")}
                    onChange={(v) =>
                      set(
                        "languages",
                        v
                          .split(",")
                          .map((x) => x.trim())
                          .filter(Boolean),
                      )
                    }
                  />
                  <Field
                    label="Tone of voice"
                    value={draft.tone}
                    onChange={(v) => set("tone", v)}
                  />
                  <FlexibleField
                    label="Opening hours"
                    value={draft.opening_hours}
                    onChange={(v) => set("opening_hours", v)}
                  />
                  <FlexibleField
                    label="Contact details"
                    value={draft.contact_details}
                    onChange={(v) => set("contact_details", v)}
                  />
                  <FlexibleField
                    label="Sales objectives"
                    value={draft.sales_objectives}
                    onChange={(v) => set("sales_objectives", v)}
                  />
                  <button
                    className="primary"
                    disabled={busy}
                    onClick={() =>
                      run(save, "Business profile saved to draft.")
                    }
                  >
                    Save profile
                  </button>
                </section>
              </div>
            )}
            {view === "Knowledge" && (
              <div className="page-scroll">
                <section className="card">
                  <div className="section-heading">
                    <div>
                      <h2>Your business knowledge</h2>
                      <p className="muted">
                        PDF or UTF-8 TXT · Up to 20 MB · Text-based documents
                        only
                      </p>
                    </div>
                    <BookOpen size={23} />
                  </div>
                  <label className="upload-zone">
                    <Upload size={26} />
                    <strong>Choose a document to upload</strong>
                    <span>
                      Policies, FAQs, service guides and business information
                    </span>
                    <input
                      type="file"
                      accept=".pdf,.txt"
                      disabled={busy}
                      onChange={(e) => {
                        const file = e.target.files?.[0];
                        e.target.value = "";
                        if (!file) return;
                        if (file.size > 20 * 1024 * 1024) {
                          setError("Choose a file smaller than 20 MB.");
                          return;
                        }
                        run(async () => {
                          const body = new FormData();
                          body.append("file", file);
                          await api(`${base}/documents`, {
                            method: "POST",
                            body,
                          });
                          setDocuments(
                            list(await api(`${base}/documents`), "documents"),
                          );
                        }, "Upload received. Extraction and indexing run in the background.");
                      }}
                    />
                  </label>
                  <p className="muted">
                    Approve an indexed document, then publish your draft to make
                    it available to the sales agent.
                  </p>
                </section>
                <section className="card">
                  {!documents.length ? (
                    <Empty
                      title="No knowledge sources yet"
                      text="Upload your first document to give Salma business facts."
                    />
                  ) : (
                    documents.map((d) => (
                      <div className="document-row" key={d.id}>
                        <BookOpen size={21} />
                        <div className="grow">
                          <strong>{d.filename}</strong>
                          <p className="muted">
                            {label(d.status)}
                            {d.pages?.length
                              ? ` · ${d.pages.length} pages`
                              : ""}
                            {d.approved ? " · Approved" : ""}
                          </p>
                          {d.error && <p className="error">{d.error}</p>}
                          {!!d.pages?.length && (
                            <details>
                              <summary>Review extracted text</summary>
                              <pre>
                                {d.pages
                                  .map(
                                    (page) => `Page ${page.page}\n${page.text}`,
                                  )
                                  .join("\n\n")}
                              </pre>
                              <a
                                className="muted"
                                href={`${base}/documents/${d.id}/download`}
                              >
                                Download original
                              </a>
                            </details>
                          )}
                        </div>
                        <button
                          className="secondary"
                          disabled={
                            busy ||
                            !["ready", "indexed", "completed"].includes(
                              d.status,
                            )
                          }
                          onClick={() =>
                            run(async () => {
                              await api(`${base}/documents/${d.id}`, {
                                method: "PATCH",
                                body: JSON.stringify({ approved: !d.approved }),
                              });
                              setDraft((current) => ({
                                ...current,
                                document_ids: d.approved
                                  ? (current.document_ids || []).filter(
                                      (id) => id !== d.id,
                                    )
                                  : [
                                      ...new Set([
                                        ...(current.document_ids || []),
                                        d.id,
                                      ]),
                                    ],
                              }));
                              setDocuments(
                                list(
                                  await api(`${base}/documents`),
                                  "documents",
                                ),
                              );
                            }, "Document approval updated. Publish to update the sales agent.")
                          }
                        >
                          {d.approved ? "Unapprove" : "Approve"}
                        </button>
                        <button
                          className="icon-button"
                          aria-label={`Remove ${d.filename}`}
                          disabled={busy}
                          onClick={() =>
                            run(async () => {
                              await api(`${base}/documents/${d.id}`, {
                                method: "DELETE",
                              });
                              setDraft((current) => ({
                                ...current,
                                document_ids: (
                                  current.document_ids || []
                                ).filter((id) => id !== d.id),
                              }));
                              setDocuments(
                                list(
                                  await api(`${base}/documents`),
                                  "documents",
                                ),
                              );
                            })
                          }
                        >
                          <Trash2 size={17} />
                        </button>
                      </div>
                    ))
                  )}
                </section>
              </div>
            )}
            {view === "Products & Services" && (
              <ProductCatalog key={bid} api={api} base={base} businessId={bid}>
                <div className="section-heading">
                  <p className="muted">
                    Only reviewed catalog prices can be quoted to customers.
                  </p>
                  <button
                    className="secondary"
                    onClick={() =>
                      set("catalog", [...(draft.catalog || []), emptyService()])
                    }
                  >
                    <Plus size={16} />
                    Add service
                  </button>
                </div>
                {!draft.catalog?.length && (
                  <section className="card">
                    <Empty
                      title="Build your catalog"
                      text="Add services manually or ask the setup assistant to propose them from your documents."
                    />
                  </section>
                )}
                {(draft.catalog || []).map((service, index) => {
                  const edit = (k, v) =>
                    set(
                      "catalog",
                      draft.catalog.map((s, i) =>
                        i === index ? { ...s, [k]: v } : s,
                      ),
                    );
                  return (
                    <section className="card" key={service.id || index}>
                      <div className="section-heading">
                        <h2>{service.name || `Service ${index + 1}`}</h2>
                        <button
                          className="icon-button"
                          aria-label="Remove service"
                          onClick={() =>
                            set(
                              "catalog",
                              draft.catalog.filter((_, i) => i !== index),
                            )
                          }
                        >
                          <Trash2 size={17} />
                        </button>
                      </div>
                      <div className="form-grid">
                        <Field
                          label="Service name"
                          required
                          value={service.name}
                          onChange={(v) => edit("name", v)}
                        />
                        <label className="field">
                          <span>Price type</span>
                          <select
                            value={service.price_type}
                            onChange={(e) => edit("price_type", e.target.value)}
                          >
                            {[
                              "fixed",
                              "base",
                              "starting_from",
                              "quote_only",
                            ].map((t) => (
                              <option key={t} value={t}>
                                {label(t)}
                              </option>
                            ))}
                          </select>
                        </label>
                        <Field
                          label="Amount"
                          required={service.price_type !== "quote_only"}
                          type="number"
                          min="0"
                          step="0.01"
                          disabled={service.price_type === "quote_only"}
                          value={service.amount ?? ""}
                          onChange={(v) =>
                            edit("amount", v === "" ? null : Number(v))
                          }
                        />
                        <Field
                          label="Currency"
                          required
                          pattern="[A-Z]{3}"
                          value={service.currency}
                          onChange={(v) => edit("currency", v.toUpperCase())}
                        />
                        <Field
                          label="Unit (e.g. per session)"
                          value={service.unit}
                          onChange={(v) => edit("unit", v)}
                        />
                        <Field
                          label="Duration (e.g. 30 minutes)"
                          value={service.duration}
                          onChange={(v) => edit("duration", v)}
                        />
                        <Field
                          label="Arabic name (optional)"
                          value={service.name_ar}
                          onChange={(v) => edit("name_ar", v)}
                        />
                        <Field
                          label="Conditions"
                          value={service.conditions}
                          onChange={(v) => edit("conditions", v)}
                        />
                        <Field
                          label="Service description (optional)"
                          multiline
                          maxLength={1000}
                          placeholder="What the service includes and how it helps customers"
                          value={service.description_en ?? service.description}
                          onChange={(v) =>
                            set(
                              "catalog",
                              draft.catalog.map((s, i) =>
                                i === index
                                  ? { ...s, description: v, description_en: v }
                                  : s,
                              ),
                            )
                          }
                        />
                        <Field
                          label="Arabic description (optional)"
                          multiline
                          maxLength={1000}
                          value={service.description_ar}
                          onChange={(v) => edit("description_ar", v)}
                        />
                      </div>
                      <OffersEditor
                        value={service.offers || []}
                        onChange={(v) => edit("offers", v)}
                      />
                    </section>
                  );
                })}
                <div className="sticky-actions">
                  <button
                    className="primary"
                    disabled={busy}
                    onClick={() =>
                      run(save, "Catalog saved to draft. Publish when ready.")
                    }
                  >
                    Save services
                  </button>
                </div>
              </ProductCatalog>
            )}
            {view === "Agent Settings" && (
              <div className="page-scroll">
                <section className="card">
                  <div className="section-heading">
                    <h2>Sales instructions</h2>
                    <span className="draft-tag">Draft</span>
                  </div>
                  <Field
                    multiline
                    label="Your instructions for Salma"
                    value={draft.owner_instructions}
                    onChange={(v) => set("owner_instructions", v)}
                    placeholder="How should Salma help your customers?"
                  />
                  <FlexibleField
                    label="Lead and appointment fields to collect"
                    value={draft.lead_fields}
                    onChange={(v) => set("lead_fields", v)}
                  />
                  <Field
                    multiline
                    label="Escalation instructions"
                    value={draft.escalation_instructions}
                    onChange={(v) => set("escalation_instructions", v)}
                  />
                  <p className="safety-note">
                    <ShieldCheck size={16} />
                    Human requests, isolation, grounded answers and staff
                    confirmation of commitments always apply.
                  </p>
                </section>
                <section className="card">
                  <div className="section-heading">
                    <h2>Human handoff rules</h2>
                    <button
                      className="secondary"
                      onClick={() =>
                        set("handoff_rules", [
                          ...(draft.handoff_rules || []),
                          {
                            name: "",
                            conditions: "",
                            examples: [],
                            destination: "staff",
                            acknowledgment: "",
                            enabled: true,
                          },
                        ])
                      }
                    >
                      <Plus size={15} />
                      Add rule
                    </button>
                  </div>
                  {(draft.handoff_rules || []).map((rule, index) => {
                    const edit = (k, v) =>
                      set(
                        "handoff_rules",
                        draft.handoff_rules.map((r, i) =>
                          i === index ? { ...r, [k]: v } : r,
                        ),
                      );
                    return (
                      <div className="rule" key={index}>
                        <div className="section-heading">
                          <Field
                            label="Rule name"
                            value={rule.name}
                            onChange={(v) => edit("name", v)}
                          />
                          <button
                            className="icon-button"
                            aria-label="Remove handoff rule"
                            onClick={() =>
                              set(
                                "handoff_rules",
                                draft.handoff_rules.filter(
                                  (_, i) => i !== index,
                                ),
                              )
                            }
                          >
                            <Trash2 size={16} />
                          </button>
                        </div>
                        <Field
                          label="When should a person step in?"
                          multiline
                          value={rule.conditions}
                          onChange={(v) => edit("conditions", v)}
                        />
                        <Field
                          label="Examples (one per line)"
                          multiline
                          value={(rule.examples || []).join("\n")}
                          onChange={(v) =>
                            edit("examples", v.split("\n").filter(Boolean))
                          }
                        />
                        <div className="form-grid">
                          <Field
                            label="Destination"
                            value={rule.destination}
                            onChange={(v) => edit("destination", v)}
                          />
                          <Field
                            label="Acknowledgment to customer"
                            value={rule.acknowledgment}
                            onChange={(v) => edit("acknowledgment", v)}
                          />
                        </div>
                        <label className="check-field">
                          <input
                            type="checkbox"
                            checked={rule.enabled !== false}
                            onChange={(e) => edit("enabled", e.target.checked)}
                          />
                          Rule enabled
                        </label>
                      </div>
                    );
                  })}
                  <button
                    className="primary"
                    disabled={busy}
                    onClick={() => run(save, "Agent settings saved to draft.")}
                  >
                    Save settings
                  </button>
                </section>
                <section className="card">
                  <details>
                    <summary>Effective system prompt preview</summary>
                    <pre className="prompt-preview">
                      {prompt ||
                        "Save settings to refresh the effective prompt."}
                    </pre>
                  </details>
                </section>
                <section className="card">
                  <h2>Publish your agent</h2>
                  <p className="muted">
                    Publishing saves an immutable version of your settings,
                    approved knowledge and catalog. Unpublished edits never
                    affect customers.
                  </p>
                  <button
                    className="primary"
                    disabled={busy}
                    onClick={() =>
                      run(async () => {
                        await save();
                        await api(`${base}/publish`, {
                          method: "POST",
                          body: "{}",
                        });
                        await load();
                      }, "Agent published. Test the new version before activating a channel.")
                    }
                  >
                    Publish draft
                  </button>
                  <div className="versions">
                    {versions.map((v) => (
                      <div key={v.version}>
                        <span>
                          Version {v.version}
                          <small className="muted">
                            {new Date(v.created_at).toLocaleString()}
                          </small>
                        </span>
                        <button
                          className="secondary"
                          disabled={busy || v.version === published}
                          onClick={() =>
                            run(async () => {
                              await api(`${base}/rollback`, {
                                method: "POST",
                                body: JSON.stringify({ version: v.version }),
                              });
                              await load();
                            }, "Published configuration restored.")
                          }
                        >
                          Restore
                        </button>
                      </div>
                    ))}
                  </div>
                </section>
              </div>
            )}
            {view === "Channels" && (
              <div className="page-scroll">
                <ProviderTools key={base} api={api} base={base} />
                <SocialConnections key={`social-${base}`} api={api} base={base} refresh={async () => setChannels(list(await api(`${base}/channels`), "channels"))} />
                <section className="card">
                  <h2>Meet customers where they are</h2>
                  <p className="muted">
                    WhatsApp connects directly through Evolution. Messenger
                    and Instagram use Chatwoot. Pair your WhatsApp number here,
                    or connect your social inboxes above.
                  </p>
                  {!channels.some((c) => c.kind === "whatsapp") && <button disabled={busy} onClick={() => run(async () => {
                    await api(`${base}/whatsapp`, { method: "POST", body: "{}" });
                    setChannels(list(await api(`${base}/channels`), "channels"));
                  })}>Set up WhatsApp via Evolution</button>}
                  <p className="safety-note">
                    <ShieldCheck size={16} />
                    Every new connection is paused until you publish your agent
                    and activate it.
                  </p>
                </section>
                {operator && (
                  <ChannelForm
                    api={api}
                    base={base}
                    run={run}
                    refresh={async () =>
                      setChannels(
                        list(await api(`${base}/channels`), "channels"),
                      )
                    }
                  />
                )}{" "}
                {!channels.length && (
                  <section className="card">
                    <Empty
                      title="No connected channels"
                      text="Ask your operator to provision a WhatsApp connection or link your social inbox."
                    />
                  </section>
                )}
                {channels.map((c) => (
                  <section className="card" key={c.id}>
                    <div className="section-heading">
                      <div>
                        <p className="eyebrow">{c.kind}</p>
                        <h2>{c.name}</h2>
                      </div>
                      <span
                        className={`status-chip ${c.live_enabled ? "good" : ""}`}
                      >
                        {c.live_enabled ? "Live" : "Paused"} · {label(c.status)}
                      </span>
                    </div>
                    <p className="muted">
                      {c.kind === "whatsapp" && c.details?.transport === "evolution" ? "WhatsApp → Evolution → Salma · Replies through Evolution" : `Chatwoot account ${c.account_id}${c.inbox_id ? ` · Inbox ${c.inbox_id}` : ""}`}
                    </p>
                    <ChannelAccess key={`${base}/${c.id}`} api={api} base={base} channelId={c.id} />
                    {c.details &&
                      (typeof c.details === "string" ? (
                        <p className="muted">{c.details}</p>
                      ) : (
                        <div>
                          {c.details.guidance && (
                            <p className="muted">{c.details.guidance}</p>
                          )}
                          {c.details.connection_state && (
                            <p className="muted">
                              Connection: {label(c.details.connection_state)}
                            </p>
                          )}
                          {c.details.inbox_verified && (
                            <p className="muted">
                              Chatwoot inbox verified
                              {c.details.webhook_registered
                                ? " · Message routing configured"
                                : ""}
                              .
                            </p>
                          )}
                          {(c.details.error || c.details.assignment_error) && (
                            <p className="error" role="alert">
                              {String(
                                c.details.error || c.details.assignment_error,
                              )}
                            </p>
                          )}
                          {c.details.errors && (
                            <pre className="error">
                              {typeof c.details.errors === "string"
                                ? c.details.errors
                                : pretty(c.details.errors)}
                            </pre>
                          )}
                          {c.details.assignment === "failed" && (
                            <p className="error" role="alert">
                              Staff assignment failed. Check the team assignment
                              and Chatwoot connection.
                            </p>
                          )}
                        </div>
                      ))}
                    <div className="button-row">
                      {c.kind === "whatsapp" && c.details?.transport !== "evolution" && <button disabled={busy} className="secondary" onClick={() => run(async () => {
                        await api(`${base}/channels/${c.id}/direct-whatsapp`, { method: "POST", body: "{}" });
                        setChannels(list(await api(`${base}/channels`), "channels"));
                      })}>Use Evolution directly</button>}
                      {c.kind !== "whatsapp" && (
                        <button
                          className="secondary"
                          disabled={busy}
                          onClick={() =>
                            run(async () => {
                              await api(`${base}/channels/${c.id}/connect`, {
                                method: "POST",
                                body: "{}",
                              });
                              setChannels(
                                list(await api(`${base}/channels`), "channels"),
                              );
                            }, "Chatwoot inbox linked. Review its state before activation.")
                          }
                        >
                          Link Chatwoot inbox
                        </button>
                      )}
                      {c.kind === "whatsapp" && (
                        <>
                          <button
                            className="secondary"
                            disabled={busy}
                            onClick={() =>
                              run(async () => {
                                await api(`${base}/channels/${c.id}/connect`, {
                                  method: "POST",
                                  body: "{}",
                                });
                                const data = await api(
                                  `${base}/channels/${c.id}/qr`,
                                );
                                setQr((old) => ({ ...old, [c.id]: data }));
                                setChannels(
                                  list(
                                    await api(`${base}/channels`),
                                    "channels",
                                  ),
                                );
                              })
                            }
                          >
                            Pair WhatsApp
                          </button>
                          <button
                            className="secondary"
                            disabled={busy}
                            onClick={() =>
                              run(async () => {
                                setQr((old) => ({ ...old, [c.id]: null }));
                                const data = await api(
                                  `${base}/channels/${c.id}/qr`,
                                );
                                setQr((old) => ({ ...old, [c.id]: data }));
                              })
                            }
                          >
                            <RefreshCw size={15} />
                            Refresh QR
                          </button>
                        </>
                      )}
                      <button
                        className="secondary"
                        disabled={busy}
                        onClick={() =>
                          run(async () => {
                            await api(`${base}/channels/${c.id}/status`);
                            setChannels(
                              list(await api(`${base}/channels`), "channels"),
                            );
                          })
                        }
                      >
                        Check connection
                      </button>
                      <button
                        className={c.live_enabled ? "secondary" : "primary"}
                        disabled={busy || (!published && !c.live_enabled)}
                        onClick={() =>
                          run(
                            async () => {
                              await api(`${base}/channels/${c.id}/activate`, {
                                method: "POST",
                                body: JSON.stringify({
                                  enabled: !c.live_enabled,
                                }),
                              });
                              setChannels(
                                list(await api(`${base}/channels`), "channels"),
                              );
                            },
                            c.live_enabled
                              ? "Channel paused."
                              : "Channel activated.",
                          )
                        }
                      >
                        {c.live_enabled ? "Pause channel" : "Activate channel"}
                      </button>
                    </div>
                    {c.kind === "whatsapp" && c.details?.transport === "evolution" ? <p className="muted">Human handoffs appear in Conversations. Reply from the connected phone to take over; explicitly resume Salma in Conversations.</p> : <DestinationSettings
                      api={api}
                      base={`${base}/channels/${c.id}`}
                      run={run}
                    />}
                    {qr[c.id] && (
                      <div className="qr-panel">
                        {qr[c.id].qr_code?.startsWith("data:image/") ? (
                          <img
                            src={qr[c.id].qr_code}
                            alt="Scan with WhatsApp Linked devices"
                          />
                        ) : (
                          <p className="muted">
                            {label(qr[c.id].status)} ·{" "}
                            {qr[c.id].qr_code
                              ? "QR received in an unsupported format. Ask your operator to check the Evolution response."
                              : "No QR available. Check connection status or retry pairing."}
                          </p>
                        )}
                        <p>
                          On your phone: WhatsApp → Linked devices → Link a
                          device.
                        </p>
                      </div>
                    )}
                  </section>
                ))}
              </div>
            )}
            {(view === "Test Chat" || view === "Conversations") && (
              <Conversation
                key={`${bid}:${view}:${conversationTarget || ""}`}
                initialSelected={view === "Conversations" ? conversationTarget : null}
                api={api}
                base={base}
                run={run}
                live={view === "Conversations"}
                published={published}
              />
            )}
          </>
        )}
      </main>
    </div>
  );
}
function Brand() {
  return (
    <div className="brand">
      <span className="brand-icon">
        <Leaf size={23} />
      </span>
      <div>
        <strong>
          salma<span>.</span>
        </strong>
        <small>YOUR INBOUND SALES TEAM</small>
      </div>
    </div>
  );
}
function Empty({ title, text }) {
  return (
    <div className="empty-state">
      <Leaf size={29} />
      <h3>{title}</h3>
      <p>{text}</p>
    </div>
  );
}
function OffersEditor({ value, onChange }) {
  const edit = (index, key, next) =>
    onChange(
      value.map((offer, i) =>
        i === index
          ? {
              ...(typeof offer === "string" ? { name: offer } : offer),
              [key]: next,
            }
          : offer,
      ),
    );
  return (
    <div className="offers-editor">
      <div className="section-heading">
        <strong>Approved offers</strong>
        <button
          className="secondary"
          onClick={() =>
            onChange([
              ...value,
              { name: "", amount: 0, conditions: "", valid_until: "" },
            ])
          }
        >
          <Plus size={14} />
          Add offer
        </button>
      </div>
      {value.map((raw, index) => {
        const offer = typeof raw === "string" ? { name: raw } : raw;
        return (
          <div className="rule" key={index}>
            <div className="section-heading">
              <Field
                label="Offer name"
                required
                value={offer.name}
                onChange={(v) => edit(index, "name", v)}
              />
              <button
                className="icon-button"
                aria-label="Remove offer"
                onClick={() => onChange(value.filter((_, i) => i !== index))}
              >
                <Trash2 size={15} />
              </button>
            </div>
            <div className="form-grid">
              <Field
                label="Offer amount (same currency)"
                required
                type="number"
                min="0"
                step="0.01"
                value={offer.amount ?? ""}
                onChange={(v) =>
                  edit(index, "amount", v === "" ? null : Number(v))
                }
              />
              <Field
                label="Conditions and validity"
                value={offer.conditions}
                onChange={(v) => edit(index, "conditions", v)}
              />
              <Field
                label="Valid until (optional)"
                type="date"
                value={offer.valid_until}
                onChange={(v) => edit(index, "valid_until", v)}
              />
            </div>
          </div>
        );
      })}
      <p className="muted">
        Only add offers your business has approved. Include eligibility and
        expiry conditions.
      </p>
    </div>
  );
}
function FlexibleField({ value, onChange, ...props }) {
  if (Array.isArray(value) && value.every((item) => typeof item === "string"))
    return (
      <Field
        multiline
        value={value.join("\n")}
        onChange={(v) => onChange(v.split("\n").filter(Boolean))}
        {...props}
      />
    );
  return typeof value === "object" && value !== null ? (
    <JsonField value={value} onChange={onChange} {...props} />
  ) : (
    <Field multiline value={value} onChange={onChange} {...props} />
  );
}
function Composer({ value, setValue, send, busy, placeholder }) {
  return (
    <form
      className="composer"
      onSubmit={(e) => {
        e.preventDefault();
        if (value.trim() && !busy) send();
      }}
    >
      <textarea
        aria-label={placeholder}
        placeholder={placeholder}
        rows={2}
        maxLength={12000}
        value={value}
        onChange={(e) => setValue(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter" && !e.shiftKey) {
            e.preventDefault();
            if (value.trim() && !busy) send();
          }
        }}
      />
      <button
        className="send"
        aria-label="Send message"
        disabled={busy || !value.trim()}
      >
        <ArrowUp size={21} />
      </button>
    </form>
  );
}
function WorkspaceCreator({ api, run, onCreated }) {
  const [name, setName] = useState("");
  return (
    <form
      className="inline-form"
      onSubmit={(e) => {
        e.preventDefault();
        run(async () => {
          const b = await api("/api/businesses", {
            method: "POST",
            body: JSON.stringify({ name }),
          });
          onCreated(b);
          setName("");
        }, "Workspace created.");
      }}
    >
      <Field
        label="New business workspace"
        value={name}
        onChange={setName}
        required
      />
      <button className="secondary">Create workspace</button>
    </form>
  );
}
function Invite({ api, base, run, value, setValue }) {
  const [email, setEmail] = useState("");
  return (
    <>
      <form
        className="inline-form"
        onSubmit={(e) => {
          e.preventDefault();
          run(async () => {
            const r = await api(`${base}/invites`, {
              method: "POST",
              body: JSON.stringify({ email }),
            });
            setValue(
              new URL(r.accept_url || `/?invite=${r.token}`, location.origin)
                .href,
            );
          });
        }}
      >
        <Field
          label="Invite a business owner"
          type="email"
          value={email}
          onChange={setEmail}
          required
        />
        <button className="secondary">Create invitation</button>
      </form>
      {value && (
        <div className="invitation">
          <p className="muted">
            Share this single-use invitation privately with the owner.
          </p>
          <input
            aria-label="Invitation link"
            readOnly
            value={value}
            onFocus={(e) => e.target.select()}
          />
        </div>
      )}
    </>
  );
}
function ChannelForm({ api, base, run, refresh }) {
  const [data, setData] = useState({
    kind: "messenger",
    name: "",
    account_id: "",
    api_token: "",
    inbox_id: "",
  });
  const edit = (k, v) => setData((s) => ({ ...s, [k]: v }));
  return (
    <details className="card">
      <summary>Operator: provision channel</summary>
      <form
        onSubmit={(e) => {
          e.preventDefault();
          run(async () => {
            await api(`${base}/channels`, {
              method: "POST",
              body: JSON.stringify({
                ...data,
                account_id: data.account_id.trim(),
                inbox_id: data.inbox_id ? data.inbox_id.trim() : null,
              }),
            });
            edit("api_token", "");
            await refresh();
          }, "Channel provisioned and paused.");
        }}
      >
        <div className="form-grid">
          <label className="field">
            <span>Channel</span>
            <select
              value={data.kind}
              onChange={(e) => edit("kind", e.target.value)}
            >
              {["messenger", "instagram"].map((k) => (
                <option key={k}>{k}</option>
              ))}
            </select>
          </label>
          <Field
            label="Connection name"
            required
            value={data.name}
            onChange={(v) => edit("name", v)}
          />
          <Field
            label="Chatwoot account ID"
            type="number"
            required
            value={data.account_id}
            onChange={(v) => edit("account_id", v)}
          />
          <Field
            label="Existing inbox ID (optional for WhatsApp)"
            type="number"
            value={data.inbox_id}
            onChange={(v) => edit("inbox_id", v)}
          />
          <Field
            label="Chatwoot access token"
            type="password"
            required
            autoComplete="off"
            value={data.api_token}
            onChange={(v) => edit("api_token", v)}
          />
        </div>
        <button className="primary">Provision paused connection</button>
      </form>
    </details>
  );
}
function DestinationSettings({ api, base, run }) {
  const [data, setData] = useState(null),
    [name, setName] = useState("");
  const edit = (name, key, value) =>
    setData((d) => ({
      ...d,
      destinations: {
        ...d.destinations,
        [name]: {
          ...d.destinations[name],
          [key]: value ? Number(value) : null,
        },
      },
    }));
  return (
    <details
      className="destination-settings"
      onToggle={(e) => {
        if (e.currentTarget.open && !data)
          run(async () => setData(await api(`${base}/destinations`)));
      }}
    >
      <summary>Handoff team assignments</summary>
      <p className="muted">
        Match destination labels in your handoff rules to a Chatwoot team or
        agent.
      </p>
      {data && (
        <>
          <div className="inline-form">
            <Field label="Destination label" value={name} onChange={setName} />
            <button
              className="secondary"
              disabled={!name.trim()}
              onClick={() => {
                setData((d) => ({
                  ...d,
                  destinations: { ...d.destinations, [name.trim()]: {} },
                }));
                setName("");
              }}
            >
              Add destination
            </button>
          </div>
          {Object.entries(data.destinations || {}).map(([name, value]) => (
            <div className="rule" key={name}>
              <div className="section-heading">
                <strong>{name}</strong>
                <button
                  className="icon-button"
                  aria-label={`Remove ${name} destination`}
                  onClick={() =>
                    setData((d) => ({
                      ...d,
                      destinations: Object.fromEntries(
                        Object.entries(d.destinations).filter(
                          ([key]) => key !== name,
                        ),
                      ),
                    }))
                  }
                >
                  <Trash2 size={15} />
                </button>
              </div>
              <div className="form-grid">
                <label className="field">
                  <span>Chatwoot team</span>
                  <select
                    value={value.team_id || ""}
                    onChange={(e) => edit(name, "team_id", e.target.value)}
                  >
                    <option value="">Unassigned</option>
                    {(data.teams || []).map((team) => (
                      <option key={team.id} value={team.id}>
                        {team.name}
                      </option>
                    ))}
                  </select>
                </label>
                <label className="field">
                  <span>Chatwoot agent</span>
                  <select
                    value={value.assignee_id || ""}
                    onChange={(e) => edit(name, "assignee_id", e.target.value)}
                  >
                    <option value="">Unassigned</option>
                    {(data.agents || []).map((agent) => (
                      <option key={agent.id} value={agent.id}>
                        {agent.name || agent.email}
                      </option>
                    ))}
                  </select>
                </label>
              </div>
            </div>
          ))}
          <button
            className="secondary"
            onClick={() =>
              run(async () => {
                await api(`${base}/destinations`, {
                  method: "PATCH",
                  body: JSON.stringify({ destinations: data.destinations }),
                });
              }, "Handoff assignments saved.")
            }
          >
            Save assignments
          </button>
        </>
      )}
    </details>
  );
}
function Conversation({ api, base, run, live, published, initialSelected = null }) {
  const [items, setItems] = useState([]),
    [selected, setSelected] = useState(initialSelected),
    [detail, setDetail] = useState(null),
    [text, setText] = useState(""),
    [busy, setBusy] = useState(false),
    [draft, setDraft] = useState(() => crypto.randomUUID()),
    [inspect, setInspect] = useState(false);
  const end = useRef(null);
  const refresh = useCallback(
    async () =>
      setItems(list(await api(`${base}/conversations`), "conversations")),
    [api, base],
  );
  const load = useCallback(
    async (key) => {
      if (key)
        setDetail(
          await api(`${base}/conversations/${encodeURIComponent(key)}`),
        );
    },
    [api, base],
  );
  useEffect(() => {
    refresh().catch(() => {});
  }, [refresh]);
  useEffect(() => {
    setDetail(null);
    if (selected) load(selected).catch((e) => run(() => Promise.reject(e)));
  }, [selected, load]);
  useEffect(() => {
    if (!selected) return;
    const source = new EventSource(
      `${base}/conversations/${encodeURIComponent(selected)}/events`,
      { withCredentials: true },
    );
    source.onmessage = () => {
      load(selected).catch(() => {});
      refresh().catch(() => {});
    };
    const poll = setInterval(() => {
      load(selected).catch(() => {});
      refresh().catch(() => {});
    }, 4000);
    return () => {
      source.close();
      clearInterval(poll);
    };
  }, [selected, load, refresh, base]);
  useEffect(() => {
    end.current?.scrollIntoView({ block: "nearest" });
  }, [detail?.messages?.length]);
  const conv = detail?.conversation;
  const reset = () => {
    setDraft(crypto.randomUUID());
    setSelected(null);
    setDetail(null);
    setText("");
  };
  const send = async () => {
    setBusy(true);
    await run(async () => {
      const response = await api(`${base}/simulator/messages`, {
        method: "POST",
        body: JSON.stringify({
          conversation_id:
            conv?.provider === "simulator" ? conv.external_id : draft,
          customer_id:
            conv?.provider === "simulator"
              ? conv.customer_key.split(":").at(-1)
              : draft,
          message_id: crypto.randomUUID(),
          text,
        }),
      });
      setText("");
      setSelected(response.conversation_key);
      await refresh();
      await load(response.conversation_key);
    });
    setBusy(false);
  };
  const messages = (detail?.messages || []).filter(
    (m) => m.direction !== "internal",
  );
  return (
    <div className={`chat-layout ${inspect ? "inspect-open" : ""}`}>
      <aside className="chat-list">
        <div className="section-heading">
          <strong>{live ? "Customer inbox" : "Test conversations"}</strong>
          {!live && (
            <button
              className="icon-button"
              aria-label="New test conversation"
              onClick={reset}
            >
              <Plus size={18} />
            </button>
          )}
        </div>
        {items
          .filter((c) =>
            live ? c.provider !== "simulator" : c.provider === "simulator",
          )
          .map((c) => (
            <button
              key={c.key}
              className={`chat-item ${selected === c.key ? "selected" : ""}`}
              onClick={() => setSelected(c.key)}
            >
              <MessagesSquare size={17} />
              <div>
                <strong>
                  {c.customer_name || c.channel || "Test customer"}
                </strong>
                <small>{label(c.ownership)}</small>
              </div>
            </button>
          ))}
        <button
          className="secondary"
          onClick={() => refresh().catch((e) => run(() => Promise.reject(e)))}
        >
          <RefreshCw size={14} />
          Refresh
        </button>
      </aside>
      <section className="chat-pane">
        <div className="chat-header">
          <div>
            <strong>
              {live ? "Customer conversation" : "Meet your Salma"}
            </strong>
            <small>
              {conv
                ? label(conv.ownership)
                : `Published configuration${published ? ` v${published}` : " required"}`}
            </small>
          </div>
          <button className="secondary" onClick={() => setInspect(!inspect)}>
            Activity
          </button>
          {!live && (
            <button className="secondary" onClick={reset}>
              New chat
            </button>
          )}
        </div>
        {conv?.opted_out && (
          <div className="handoff-banner">
            Customer opted out. Automated replies stay paused until the customer
            explicitly opts back in.
          </div>
        )}
        {conv?.ownership &&
          conv.ownership !== "AI_ACTIVE" &&
          !conv.opted_out && (
            <div className="handoff-banner">
              <span>
                Salma is paused:{" "}
                {conv.handoff_reason ||
                  conv.handoff?.reason ||
                  label(conv.ownership)}
                .
              </span>
              <button
                className="secondary"
                onClick={() =>
                  run(async () => {
                    await api(
                      `${base}/conversations/${encodeURIComponent(selected)}/ownership`,
                      {
                        method: "POST",
                        body: JSON.stringify({ action: "resume" }),
                      },
                    );
                    await load(selected);
                  }, "Salma resumed.")
                }
              >
                Resume Salma
              </button>
            </div>
          )}
        <div className="customer-messages">
          {!messages.length && (
            <Empty
              title={
                live
                  ? "Select a conversation"
                  : "Your agent, ready for a rehearsal."
              }
              text={
                live
                  ? "Review customer messages, handoff reasons and evidence here."
                  : "Ask about your services, prices or policies. Test the published configuration before going live."
              }
            />
          )}
          {messages.map((m) => (
            <div className={`customer-message ${m.direction}`} key={m.id}>
              <small>
                {m.direction === "incoming"
                  ? "Customer"
                  : m.direction === "human"
                    ? "Staff"
                    : "Salma"}
              </small>
              <p dir="auto">{m.text}</p>
              <small className="muted">
                {label(m.status)}
                {m.config_version
                  ? ` · Configuration v${m.config_version}`
                  : ""}
                {m.created_at || m.timestamp
                  ? ` · ${new Date(m.created_at || m.timestamp).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}`
                  : ""}
              </small>
            </div>
          ))}
          <div ref={end} />
        </div>
        <div className="composer-area">
          {live ? (
            <p className="muted">
              Your team replies to live customers in Chatwoot.
            </p>
          ) : (
            <Composer
              value={text}
              setValue={setText}
              send={send}
              busy={busy}
              placeholder="Write a customer message…"
            />
          )}
          <small className="composer-note">
            <ShieldCheck size={12} />
            Evidence-based answers · Staff confirm commitments
          </small>
        </div>
      </section>
      {inspect && (
        <aside className="chat-inspector">
          <div className="section-heading">
            <h3>Conversation activity</h3>
            <button className="icon-button" onClick={() => setInspect(false)}>
              ×
            </button>
          </div>
          <p className="muted">
            Configuration v
            {conv?.configuration_version || conv?.config_version || "—"}
          </p>
          {conv && (
            <details open>
              <summary>Ownership & lead details</summary>
              <pre>{pretty(conv)}</pre>
            </details>
          )}
          {detail?.customer && (
            <details open>
              <summary>Collected customer details</summary>
              <pre>{pretty(detail.customer.profile || {})}</pre>
            </details>
          )}
          {messages
            .filter((message) => message.decision)
            .map((message) => (
              <details key={`decision-${message.id}`}>
                <summary>Sales decision · message {message.id}</summary>
                <pre>{pretty(message.decision)}</pre>
              </details>
            ))}
          {[...(detail?.events || [])].reverse().map((e) => (
            <details key={e.id}>
              <summary>{label(e.kind || e.type || "Event")}</summary>
              {e.data?.trace_url && <p><a href={e.data.trace_url} target="_blank" rel="noreferrer">Open LangSmith trace ↗</a></p>}
              {(e.data?.stages || []).map((stage, index) => (
                <div className={`flow-stage ${stage.outputs?.supported === false || stage.error ? "rejected" : ""}`} key={index}>
                  <strong>{label(stage.stage)}</strong> · {stage.duration_ms} ms
                  {stage.outputs?.reason && <p>{stage.outputs.reason}</p>}
                  {stage.error && <p className="error">{stage.error}</p>}
                  <details><summary>Inputs and outputs</summary><pre>{pretty(stage)}</pre></details>
                </div>
              ))}
              {(e.data?.reason ||
                e.data?.guard_reason ||
                e.data?.handoff_reason) && (
                <p className="muted">
                  Reason:{" "}
                  {label(
                    e.data.reason ||
                      e.data.guard_reason ||
                      e.data.handoff_reason,
                  )}
                </p>
              )}
              <pre>{pretty(e.data || e.payload || e)}</pre>
            </details>
          ))}
          {(detail?.outbox || []).map((o) => (
            <details key={`out-${o.id}`}>
              <summary>Delivery · {o.status}</summary>
              <pre>{pretty(o)}</pre>
              {["ambiguous", "failed", "blocked"].includes(o.status) && (
                <DeliveryReconciliation
                  item={o}
                  api={api}
                  base={base}
                  run={run}
                  refreshed={() => load(selected)}
                />
              )}
            </details>
          ))}
        </aside>
      )}
    </div>
  );
}
function DeliveryReconciliation({ item, api, base, run, refreshed }) {
  const [outcome, setOutcome] = useState(""),
    [providerId, setProviderId] = useState(""),
    [note, setNote] = useState(""),
    [pending, setPending] = useState(false);
  return (
    <form
      className="reconciliation"
      onSubmit={async (e) => {
        e.preventDefault();
        setPending(true);
        await run(async () => {
          await api(`${base}/delivery/${item.id}/reconcile`, {
            method: "POST",
            body: JSON.stringify({
              outcome,
              provider_id: outcome === "accepted" ? providerId : null,
              note,
            }),
          });
          await refreshed();
        }, "Delivery evidence recorded. Salma remains paused until staff explicitly resume her.");
        setPending(false);
      }}
    >
      <p className="safety-note">
        Inspect this conversation in Chatwoot before recording an outcome.
        Confirm whether this exact message appears in its history. Recording an
        outcome never retries the message automatically.
      </p>
      <label className="field">
        <span>Verified delivery outcome</span>
        <select
          required
          value={outcome}
          onChange={(e) => setOutcome(e.target.value)}
        >
          <option value="">Choose after checking Chatwoot</option>
          <option value="accepted">Message exists in Chatwoot</option>
          <option value="not_sent">Confirmed message was not sent</option>
        </select>
      </label>
      {outcome === "accepted" && (
        <Field
          label="Chatwoot message ID"
          required
          value={providerId}
          onChange={setProviderId}
        />
      )}
      <Field
        label="Evidence and explanation (at least 10 characters)"
        multiline
        required
        minLength={10}
        value={note}
        onChange={setNote}
      />
      <button
        className="secondary"
        disabled={
          pending ||
          !outcome ||
          note.trim().length < 10 ||
          (outcome === "accepted" && !providerId.trim())
        }
      >
        Record verified outcome
      </button>
    </form>
  );
}
createRoot(document.getElementById("root")).render(<App />);
