import React, { useEffect, useState } from "react";
import "./monitoring.css";

const latency = (ms) => ms == null ? "—" : `${(ms / 1000).toFixed(1)}s`;

export default function Monitoring({ api, base, operator }) {
  const [data, setData] = useState(null);
  const [system, setSystem] = useState(null);
  const [error, setError] = useState("");
  const [refresh, setRefresh] = useState(0);
  useEffect(() => {
    let active = true;
    async function load() {
      try {
        const [workspace, platform] = await Promise.all([
          api(`${base}/monitoring`),
          operator ? api("/api/monitoring/system") : Promise.resolve(null),
        ]);
        if (active) {
          setData(workspace.pipeline);
          setSystem(platform);
          setError("");
        }
      } catch (e) {
        if (active) setError(e.message);
      }
    }
    load();
    const timer = setInterval(load, 15000);
    return () => { active = false; clearInterval(timer); };
  }, [api, base, operator, refresh]);

  return <div className="page-scroll monitoring">
    <section className="card">
      <div className="section-heading">
        <div><h2>Flow monitoring</h2><p className="muted">This workspace · Last 24 hours · Refreshes every 15 seconds</p></div>
        <button className="secondary" onClick={() => setRefresh(v => v + 1)}>Refresh</button>
      </div>
      {error && <p role="alert" className="error">{error}</p>}
      {!data ? <p className="muted">Loading monitoring…</p> : <>
        <div className="monitoring-grid">
          {[
            ["Pending messages", data.pending_messages],
            ["Pending deliveries", data.pending_deliveries],
            ["Average response", latency(data.average_latency_ms)],
            ["95th percentile", latency(data.p95_latency_ms)],
            ["Handoff rate", `${Math.round(data.handoff_rate * 100)}%`],
            ["Agent failures", data.agent_failures],
            ["Failed / ambiguous deliveries", (data.delivery.failed || 0) + (data.delivery.ambiguous || 0)],
            ["Model tokens", data.total_tokens.toLocaleString()],
          ].map(([name, value]) => <div className="monitoring-stat" key={name}>
            <span>{name}</span><strong>{value}</strong>
          </div>)}
        </div>
        <p className="muted">Response time includes retrieval, model calls and validation. Decision metrics use the latest {data.sample_size.toLocaleString()} decisions (limit {data.sample_limit.toLocaleString()}).</p>
        <h3>Handoff reasons</h3>
        {Object.keys(data.handoffs).length ? <ul>{Object.entries(data.handoffs).map(([reason, count]) =>
          <li key={reason}>{reason.replaceAll("_", " ")} <strong>{count}</strong></li>)}</ul>
          : <p className="muted">No handoffs in this period.</p>}
        <p>Open a conversation’s <strong>Activity</strong> panel for its retrieval evidence, draft, validation reason, timing, and LangSmith trace.</p>
      </>}
    </section>
    {operator && system && <section className="card">
      <h2>System health</h2>
      <div className="monitoring-grid">
        {[
          ["Database", system.components.database], ["Redis", system.components.redis],
          ["Worker heartbeat", system.worker.status],
          ["Worker last seen", system.worker.age_seconds == null ? "—" : `${system.worker.age_seconds}s ago`],
          ["API memory", `${system.api.memory_mb} MB`],
          ["API CPU", `${system.api.cpu_percent}%`],
          ["Worker memory", system.worker.resources ? `${system.worker.resources.memory_mb} MB` : "—"],
          ["Worker CPU", system.worker.resources ? `${system.worker.resources.cpu_percent}%` : "—"],
        ].map(([name, value]) => <div className="monitoring-stat" key={name}><span>{name}</span><strong>{value}</strong></div>)}
      </div>
      <p className="muted">CPU and memory describe the API and worker processes. A stale heartbeat means the worker needs attention.</p>
      <h3>LangSmith</h3>
      <p>{system.langsmith.configured ? "Tracing configured" : "Tracing disabled"} · Project: {system.langsmith.project}</p>
      {system.langsmith.project_url && <a href={system.langsmith.project_url} target="_blank" rel="noreferrer">Open LangSmith project ↗</a>}
      <small className="muted">Configuration status does not confirm successful trace delivery.</small>
    </section>}
  </div>;
}
