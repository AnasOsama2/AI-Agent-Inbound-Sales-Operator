import React, { useEffect, useRef, useState } from "react";
import { AlertCircle, ArrowRight, CheckCircle2, RefreshCw } from "lucide-react";
import "./overview.css";

const words = (value) => String(value || "").replaceAll("_", " ");
const number = (value) => new Intl.NumberFormat().format(value || 0);
const channelColors = ["#174b3e", "#72a488", "#c29a55", "#6b87ad", "#a480a4"];

function age(value) {
  if (!value) return "Time unavailable";
  const minutes = Math.max(
    0,
    Math.floor((Date.now() - new Date(value).getTime()) / 60000),
  );
  if (minutes < 1) return "Just now";
  if (minutes < 60) return `${minutes}m`;
  if (minutes < 1440) return `${Math.floor(minutes / 60)}h ${minutes % 60}m`;
  return `${Math.floor(minutes / 1440)}d ${Math.floor((minutes % 1440) / 60)}h`;
}

function Summary({ title, value, detail, onClick }) {
  return (
    <button className="overview-metric" onClick={onClick}>
      <span className="muted">{title}</span>
      <strong>{number(value)}</strong>
      <span className="overview-metric-detail">
        {detail}
        <ArrowRight size={14} />
      </span>
    </button>
  );
}

function Trend({ trend, setDays }) {
  const days = trend.length;
  const channels = [
    ...new Set(trend.flatMap((day) => Object.keys(day.channels))),
  ].sort();
  const max = Math.max(1, ...trend.map((day) => day.total));
  const total = trend.reduce((sum, day) => sum + day.total, 0);
  return (
    <section className="card overview-trend">
      <div className="section-heading overview-heading">
        <div>
          <h2>Conversation activity</h2>
          <p className="muted">
            {number(total)} new customer conversations · Test chats excluded
          </p>
        </div>
        <div
          className="overview-period"
          role="group"
          aria-label="Activity period"
        >
          {[7, 30].map((period) => (
            <button
              key={period}
              aria-pressed={days === period}
              onClick={() => setDays(period)}
            >
              {period} days
            </button>
          ))}
        </div>
      </div>
      {total === 0 ? (
        <p className="overview-empty">
          Your activity trend will appear when customers start conversations.
        </p>
      ) : (
        <>
          <div className="overview-chart" aria-hidden="true">
            {trend.map((day, index) => (
              <div
                className="overview-chart-day"
                key={day.date}
                title={`${day.date}: ${day.total} conversations`}
              >
                <div className="overview-chart-stack">
                  {channels.map((channel, color) => (
                    <div
                      key={channel}
                      style={{
                        height: `${((day.channels[channel] || 0) / max) * 100}%`,
                        background: channelColors[color % channelColors.length],
                      }}
                    />
                  ))}
                </div>
                <span>
                  {days === 7 || index % 5 === 0 || index === trend.length - 1
                    ? day.date.slice(5).replace("-", "/")
                    : ""}
                </span>
              </div>
            ))}
          </div>
          <div className="overview-legend">
            {channels.map((channel, i) => (
              <span key={channel}>
                <i
                  style={{
                    background: channelColors[i % channelColors.length],
                  }}
                />
                {words(channel)}
              </span>
            ))}
          </div>
          <details className="overview-chart-data">
            <summary>View daily counts</summary>
            <div className="overview-table-wrap">
              <table>
                <caption>New customer conversations by day and channel</caption>
                <thead>
                  <tr>
                    <th>Date</th>
                    {channels.map((channel) => (
                      <th key={channel}>{words(channel)}</th>
                    ))}
                    <th>Total</th>
                  </tr>
                </thead>
                <tbody>
                  {trend.map((day) => (
                    <tr key={day.date}>
                      <th scope="row">{day.date}</th>
                      {channels.map((channel) => (
                        <td key={channel}>{number(day.channels[channel])}</td>
                      ))}
                      <td>{number(day.total)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </details>
        </>
      )}
    </section>
  );
}

export function OverviewContent({
  data,
  setDays,
  navigate,
  openConversation,
  draft,
  refreshing,
  refresh,
  error,
}) {
  const attention = [
    [
      data.attention.pending_handoffs,
      "conversations waiting for staff",
      "Conversations",
    ],
    [
      data.attention.delivery_issues,
      "messages need delivery review",
      "Conversations",
    ],
    [
      data.attention.documents_to_approve,
      "knowledge sources awaiting approval",
      "Knowledge",
    ],
    [
      data.attention.document_failures,
      "knowledge sources failed to process",
      "Knowledge",
    ],
    [data.attention.channel_issues, "channels need attention", "Channels"],
  ].filter(([count]) => count > 0);
  const steps = [
    ["Describe your business", !!draft.description, "Setup Assistant"],
    [
      "Approve business knowledge",
      data.resources.approved_knowledge > 0,
      "Knowledge",
    ],
    [
      "Review services or products",
      !!draft.catalog?.length || !!draft.product_catalog?.enabled,
      "Products & Services",
    ],
    ["Set handoff rules", !!draft.handoff_rules?.length, "Agent Settings"],
    ["Publish your agent", !!data.agent.published_version, "Agent Settings"],
    [
      "Activate a channel",
      data.channels.some((channel) => channel.live_enabled),
      "Channels",
    ],
  ];
  const incomplete = steps.filter(([, done]) => !done);
  return (
    <>
      <div className="overview-title">
        <div>
          <p className="eyebrow">YOUR DAILY WORKSPACE</p>
          <h2>Business overview</h2>
          <p className="muted">
            Today in {data.timezone} · Updated{" "}
            {new Date(data.generated_at).toLocaleTimeString([], {
              hour: "2-digit",
              minute: "2-digit",
            })}
          </p>
        </div>
        <button className="secondary" onClick={refresh} disabled={refreshing}>
          <RefreshCw size={15} />
          {refreshing ? "Refreshing…" : "Refresh"}
        </button>
      </div>
      {error && (
        <p className="overview-fetch-error" role="alert">
          Refresh failed. Showing the last successful update. {error}
        </p>
      )}
      <section
        className={`card overview-attention ${attention.length ? "has-attention" : ""}`}
        aria-labelledby="attention-title"
      >
        <div className="overview-attention-title">
          {attention.length ? (
            <AlertCircle size={20} />
          ) : (
            <CheckCircle2 size={20} />
          )}
          <h2 id="attention-title">
            {attention.length ? "Needs attention" : "No outstanding alerts"}
          </h2>
        </div>
        {attention.length ? (
          <div className="overview-alerts">
            {attention.map(([count, text, view]) => (
              <button key={text} onClick={() => navigate(view)}>
                <strong>{number(count)}</strong>
                <span>{text}</span>
                <ArrowRight size={15} />
              </button>
            ))}
          </div>
        ) : (
          <p className="muted">
            No pending handoffs, delivery issues, or setup alerts in the
            recorded data.
          </p>
        )}
      </section>
      <div className="overview-metrics">
        <Summary
          title="New conversations today"
          value={data.today.new_conversations}
          detail="Customer chats started today"
          onClick={() => navigate("Conversations")}
        />
        <Summary
          title="With Salma now"
          value={data.current.ai_active}
          detail="Open, AI-active conversations"
          onClick={() => navigate("Conversations")}
        />
        <Summary
          title="With your team now"
          value={data.current.human_active}
          detail="Open, staff-owned conversations"
          onClick={() => navigate("Conversations")}
        />
        <Summary
          title="New chats with leads"
          value={data.today.new_lead_conversations}
          detail="Started today · Active lead details"
          onClick={() =>
            document
              .getElementById("overview-leads")
              ?.scrollIntoView({ behavior: "smooth", block: "nearest" })
          }
        />
      </div>
      <div className="overview-columns">
        <section className="card">
          <div className="section-heading overview-heading">
            <div>
              <h2>Priority conversations</h2>
              <p className="muted">
                Oldest pending handoffs and delivery issues · Up to 6
              </p>
            </div>
            <button
              className="overview-text-button"
              onClick={() => navigate("Conversations")}
            >
              View inbox
              <ArrowRight size={14} />
            </button>
          </div>
          {data.priority_conversations.length === 0 ? (
            <p className="overview-empty">
              No customer conversations need intervention right now.
            </p>
          ) : (
            <div className="overview-rows">
              {data.priority_conversations.map((row) => (
                <button
                  className="overview-conversation"
                  key={row.key}
                  onClick={() => openConversation(row.key)}
                >
                  <div className="overview-row-main">
                    <strong dir="auto">{row.customer}</strong>
                    <span className="overview-badge">{words(row.channel)}</span>
                  </div>
                  <p>
                    {words(row.reason) || words(row.ownership)}
                    {row.delivery_issues > 0
                      ? ` · ${row.delivery_issues} delivery issues`
                      : ""}
                  </p>
                  <span className="muted">
                    {row.pending_since
                      ? `Waiting ${age(row.pending_since)}`
                      : `Last customer/staff activity ${age(row.last_activity)} ago`}
                  </span>
                  <span className="overview-row-action">
                    Open conversation
                    <ArrowRight size={14} />
                  </span>
                </button>
              ))}
            </div>
          )}
        </section>
        <section className="card" id="overview-leads">
          <div className="section-heading overview-heading">
            <div>
              <h2>Recent sales opportunities</h2>
              <p className="muted">
                Open conversations with captured lead details · Up to 6
              </p>
            </div>
          </div>
          {data.leads.length === 0 ? (
            <p className="overview-empty">
              Lead details will appear as Salma captures customer interest and
              requirements.
            </p>
          ) : (
            <div className="overview-rows">
              {data.leads.map((row) => (
                <button
                  className="overview-conversation"
                  key={row.key}
                  onClick={() => openConversation(row.key)}
                >
                  <div className="overview-row-main">
                    <strong dir="auto">{row.customer}</strong>
                    {row.temperature && (
                      <span
                        className={`overview-badge temperature-${row.temperature}`}
                      >
                        {words(row.temperature)}
                        {row.score != null ? ` · ${row.score}` : ""}
                      </span>
                    )}
                  </div>
                  <p dir="auto">
                    {row.interest.join(", ") || "Interest being qualified"}
                  </p>
                  {row.requirements && (
                    <p className="overview-requirements" dir="auto">
                      {row.requirements}
                    </p>
                  )}
                  <span className="muted">
                    {words(row.channel)} ·{" "}
                    {row.ownership === "HANDOFF_PENDING"
                      ? "Needs staff follow-up"
                      : row.ownership === "HUMAN_ACTIVE"
                        ? "With your team"
                        : "Salma is qualifying"}
                  </span>
                  <span className="overview-row-action">
                    Review lead
                    <ArrowRight size={14} />
                  </span>
                </button>
              ))}
            </div>
          )}
        </section>
      </div>
      <Trend trend={data.trend} setDays={setDays} />
      <div className="overview-columns">
        <section className="card overview-agent">
          <div className="section-heading overview-heading">
            <h2>Agent & knowledge</h2>
            <button
              className="overview-text-button"
              onClick={() => navigate("Agent Settings")}
            >
              Manage
              <ArrowRight size={14} />
            </button>
          </div>
          <dl>
            <div>
              <dt>Published configuration</dt>
              <dd>
                {data.agent.published_version
                  ? `Version ${data.agent.published_version}`
                  : "Not published"}
              </dd>
            </div>
            <div>
              <dt>Saved draft</dt>
              <dd>
                {!data.agent.published_version
                  ? "Ready for review"
                  : data.agent.unpublished_changes
                    ? "Unpublished changes"
                    : "Matches published version"}
              </dd>
            </div>
            <div>
              <dt>Approved knowledge sources</dt>
              <dd>{number(data.resources.approved_knowledge)}</dd>
            </div>
            <div>
              <dt>Services in saved draft</dt>
              <dd>{number(data.resources.draft_services)}</dd>
            </div>
          </dl>
          <p className="muted">
            Publishing makes saved changes available to customer conversations.
          </p>
        </section>
        <section className="card">
          <div className="section-heading overview-heading">
            <h2>Channels</h2>
            <button
              className="overview-text-button"
              onClick={() => navigate("Channels")}
            >
              Manage
              <ArrowRight size={14} />
            </button>
          </div>
          <p className="muted overview-channel-note">
            Recorded connection state and live replies are separate. Open
            Channels to verify a connection.
          </p>
          {data.channels.length === 0 ? (
            <p className="overview-empty">
              Connect your first channel to start receiving customers.
            </p>
          ) : (
            <div className="overview-channel-list">
              {data.channels.map((channel) => (
                <button key={channel.id} onClick={() => navigate("Channels")}>
                  <div>
                    <strong>{channel.name}</strong>
                    <small className="muted">
                      {words(channel.kind)} ·{" "}
                      {channel.connection_state
                        ? `Connection: ${words(channel.connection_state)}`
                        : `Status: ${words(channel.status)}`}
                    </small>
                  </div>
                  <span
                    className={`overview-badge ${channel.live_enabled ? "replies-on" : ""}`}
                  >
                    Live replies {channel.live_enabled ? "on" : "off"}
                  </span>
                </button>
              ))}
            </div>
          )}
        </section>
      </div>
      {incomplete.length > 0 && (
        <details
          className="card overview-setup"
          open={!data.agent.published_version}
        >
          <summary>
            Complete your setup{" "}
            <span className="muted">
              {steps.length - incomplete.length} of {steps.length} steps
              complete
            </span>
          </summary>
          <div className="checklist">
            {steps.map(([title, done, view], index) => (
              <button key={title} onClick={() => navigate(view)}>
                <span className={`step ${done ? "done" : ""}`}>
                  {done ? <CheckCircle2 size={15} /> : index + 1}
                </span>
                <span>{title}</span>
                <ArrowRight size={15} />
              </button>
            ))}
          </div>
        </details>
      )}
    </>
  );
}

export default function Overview({
  api,
  base,
  navigate,
  openConversation,
  draft,
}) {
  const [days, setDays] = useState(7);
  const [data, setData] = useState(null);
  const [error, setError] = useState("");
  const [refreshing, setRefreshing] = useState(false);
  const [reload, setReload] = useState(0);
  const request = useRef(0);
  useEffect(() => {
    let active = true;
    async function load() {
      const id = ++request.current;
      setRefreshing(true);
      try {
        const result = await api(`${base}/overview?days=${days}`);
        if (active && id === request.current) {
          setData(result);
          setError("");
        }
      } catch (failure) {
        if (active && id === request.current) setError(failure.message);
      } finally {
        if (active && id === request.current) setRefreshing(false);
      }
    }
    load();
    const interval = setInterval(() => {
      if (!document.hidden) load();
    }, 30000);
    return () => {
      active = false;
      clearInterval(interval);
    };
  }, [api, base, days, reload]);
  if (!data)
    return (
      <section className="card" aria-busy={!error}>
        {error ? (
          <>
            <h2>Overview unavailable</h2>
            <p className="error" role="alert">
              {error}
            </p>
            <button
              className="secondary"
              onClick={() => setReload((value) => value + 1)}
            >
              Try again
            </button>
          </>
        ) : (
          <p className="muted" role="status">
            Loading your business overview…
          </p>
        )}
      </section>
    );
  return (
    <div aria-busy={refreshing}>
      <OverviewContent
        data={data}
        setDays={setDays}
        navigate={navigate}
        openConversation={openConversation}
        draft={draft}
        refreshing={refreshing}
        refresh={() => setReload((value) => value + 1)}
        error={error}
      />
    </div>
  );
}
