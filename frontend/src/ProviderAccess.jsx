import React, { useEffect, useRef, useState } from "react";
import { ExternalLink, RefreshCw } from "lucide-react";

export function ProviderTools({ api, base }) {
  const [tools, setTools] = useState(null);
  const [error, setError] = useState("");
  useEffect(() => {
    let active = true;
    api(`${base}/provider-tools`).then(
      (value) => { if (active) setTools(value); },
      () => { if (active) setError("Provider shortcuts are unavailable. Reload to retry."); },
    );
    return () => { active = false; };
  }, [api, base]);
  return <section className="card">
    <h2>Connection tools</h2>
    <p className="muted">Open your support inbox or check the services behind your channel. Each service uses its own sign-in.</p>
    {error && <p role="alert" className="error">{error}</p>}
    <div className="button-row">
      {tools?.chatwoot.map((account) => <a key={account.account_id} className="secondary" href={account.url} target="_blank" rel="noopener noreferrer">
        <ExternalLink size={16} /> Open Chatwoot{tools.chatwoot.length > 1 ? ` · Account ${account.account_id}` : ""}
      </a>)}
      {tools?.evolution_url && <a className="secondary" href={tools.evolution_url} target="_blank" rel="noopener noreferrer"><ExternalLink size={16} /> Open Evolution Manager</a>}
    </div>
    {tools && !tools.chatwoot.length && <p className="muted">Chatwoot is optional for WhatsApp. Your operator can assign an account for Messenger and Instagram.</p>}
    {tools && !tools.evolution_url && <p className="muted">Evolution Manager is for the platform operator. Use Pair WhatsApp below to connect your business number.</p>}
  </section>;
}

export function ChannelAccess({ api, base, channelId }) {
  const [data, setData] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const sequence = useRef(0);
  async function check() {
    const request = ++sequence.current;
    setBusy(true);
    setError("");
    try {
      const value = await api(`${base}/channels/${channelId}/access`);
      if (request === sequence.current) setData(value);
    } catch {
      if (request === sequence.current) setError("Connection check failed. Retry or ask your operator to check the services.");
    } finally {
      if (request === sequence.current) setBusy(false);
    }
  }
  useEffect(() => {
    check();
    return () => { sequence.current++; };
  }, [api, base, channelId]);
  return <div className="provider-status-panel">
    <div className="button-row">
      <button className="secondary" disabled={busy} onClick={check}><RefreshCw size={15} /> {busy ? "Checking services…" : "Check provider connections"}</button>
      {data?.checked_at && <small className="muted">Checked {new Date(data.checked_at).toLocaleTimeString()}</small>}
    </div>
    {error && <p className="error" role="alert">{error}</p>}
    {data && <div className="provider-status-grid">
      {[["chatwoot", "Chatwoot"], ["evolution", "Evolution API"]].map(([key, name]) => <div className="provider-status-item" key={key}>
        <strong>{name}</strong>
        <span className={`status-chip ${data[key].status === "connected" ? "good" : ""}`}>{data[key].status.replaceAll("_", " ")}</span>
        <p className="muted">{data[key].detail}</p>
      </div>)}
    </div>}
    <p className="muted">Pairing and service connectivity are separate from activating Salma’s live replies.</p>
  </div>;
}

export function SocialConnections({ api, base, refresh }) {
  const [data, setData] = useState(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const sequence = useRef(0);
  async function load() {
    const request = ++sequence.current;
    setBusy(true); setError("");
    try {
      const value = await api(`${base}/social-inboxes`);
      if (request === sequence.current) setData(value);
    } catch (e) {
      if (request === sequence.current) setError(e.message || "Could not load Chatwoot inboxes.");
    } finally {
      if (request === sequence.current) setBusy(false);
    }
  }
  useEffect(() => { load(); return () => { sequence.current++; }; }, [api, base]);
  async function link(inbox) {
    const request = ++sequence.current;
    setBusy(true); setError("");
    try {
      await api(`${base}/social-channels`, { method: "POST", body: JSON.stringify({ account_id: inbox.account_id, inbox_id: inbox.inbox_id }) });
      if (request !== sequence.current) return;
      await refresh();
      if (request === sequence.current) await load();
    } catch (e) {
      if (request === sequence.current) setError(e.message || "Could not link this inbox.");
    } finally {
      if (request === sequence.current) setBusy(false);
    }
  }
  return <section className="card">
    <h2>Connect Messenger & Instagram</h2>
    <p className="muted">1. Connect your Facebook Page or Instagram professional account in Chatwoot. Sign in to Meta there and complete the inbox setup.</p>
    {data?.accounts.map((account) => <div key={account.account_id}>
      <div className="button-row">
        <a className="secondary" href={`${account.setup_url}/facebook`} target="_blank" rel="noopener noreferrer"><ExternalLink size={16} /> Connect Messenger{data.accounts.length > 1 ? ` · Account ${account.account_id}` : ""}</a>
        <a className="secondary" href={`${account.setup_url}/instagram`} target="_blank" rel="noopener noreferrer"><ExternalLink size={16} /> Connect Instagram{data.accounts.length > 1 ? ` · Account ${account.account_id}` : ""}</a>
      </div>
      {account.error && <p className="error" role="alert">{account.error}</p>}
    </div>)}
    {data && !data.accounts.length && <p className="muted">Your operator must first assign a separate Chatwoot account to this workspace. Social inboxes will appear here afterward.</p>}
    <p className="muted">2. Return here, refresh the inbox list, and link your page or account to Salma.</p>
    <button className="secondary" disabled={busy} onClick={load}><RefreshCw size={16} /> {busy ? "Checking inboxes…" : "Refresh Chatwoot inboxes"}</button>
    {error && <p className="error" role="alert">{error}</p>}
    {data?.inboxes.map((inbox) => <div className="provider-status-item" key={`${inbox.account_id}/${inbox.inbox_id}`}>
      <strong>{inbox.name}</strong><span className="muted">{inbox.kind === "messenger" ? "Messenger" : "Instagram"} · Inbox {inbox.inbox_id}</span>
      <button className="secondary" disabled={busy || inbox.linked} onClick={() => link(inbox)}>{inbox.linked ? "Linked to Salma" : "Link to Salma"}</button>
    </div>)}
    {data?.accounts.length > 0 && !data.inboxes.length && !data.accounts.some((a) => a.error) && <p className="muted">No Messenger or Instagram inboxes found yet. Complete step 1 in Chatwoot, then refresh.</p>}
    <p className="safety-note">3. Publish your agent and activate the new channel below to start replies. Your existing WhatsApp connection is separate.</p>
    <details><summary>Connection help</summary>
      <p className="muted">Messenger connects to a Facebook Page. Instagram uses an eligible professional account. If Chatwoot says the channel is unavailable, your operator must configure the Meta app and public HTTPS callback URLs for this self-hosted installation first.</p>
      <div className="button-row">
        <a href="https://app.chatwoot.com/hc/user-guide/articles/1677778588-how-to-setup-a-facebook-channel" target="_blank" rel="noopener noreferrer">Messenger guide</a>
        <a href="https://developers.chatwoot.com/self-hosted/configuration/features/integrations/instagram-via-instagram-business-login/" target="_blank" rel="noopener noreferrer">Instagram setup guide</a>
      </div>
    </details>
  </section>;
}
