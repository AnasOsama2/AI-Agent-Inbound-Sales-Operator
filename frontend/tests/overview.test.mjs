import assert from "node:assert/strict";
import { test } from "node:test";
import { readFile, writeFile, unlink } from "node:fs/promises";
import { transform } from "esbuild";
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";

// Exercise the real dashboard markup without adding a browser framework.
const source = await readFile(
  new URL("../src/Overview.jsx", import.meta.url),
  "utf8",
);
const compiled = await transform(
  source.replace('import "./overview.css";', ""),
  { loader: "jsx", format: "esm" },
);
const output = new URL(`../.overview-test-${process.pid}.mjs`, import.meta.url);
await writeFile(output, compiled.code);
let OverviewContent;
try {
  ({ OverviewContent } = await import(output.href));
} finally {
  await unlink(output);
}

const data = {
  generated_at: "2026-10-11T10:00:00Z",
  timezone: "Africa/Cairo",
  today: { new_conversations: 0, new_lead_conversations: 0 },
  current: { ai_active: 0, human_active: 0 },
  attention: {
    pending_handoffs: 0,
    delivery_issues: 0,
    documents_to_approve: 0,
    document_failures: 0,
    channel_issues: 0,
  },
  priority_conversations: [],
  leads: [],
  channels: [],
  agent: { published_version: null, unpublished_changes: false },
  resources: { approved_knowledge: 0, draft_services: 0 },
  trend: Array.from({ length: 7 }, (_, i) => ({
    date: `2026-10-${i + 5}`,
    total: 0,
    channels: {},
  })),
};
function render(value, props = {}) {
  return renderToStaticMarkup(
    React.createElement(OverviewContent, {
      data: value,
      draft: {},
      days: 7,
      setDays() {},
      navigate() {},
      openConversation() {},
      refresh() {},
      ...props,
    }),
  );
}

test("an empty business shows useful guidance and zero counts without fake activity", () => {
  const html = render(data);
  assert.match(html, /No outstanding alerts/);
  assert.match(html, /Connect your first channel/);
  assert.match(html, /Lead details will appear/);
  assert.match(html, /Complete your setup/);
  assert.doesNotMatch(html, /overview-chart-stack/);
});

test("attention, lead details and live activation are presented separately from connection state", () => {
  const html = render({
    ...data,
    attention: { ...data.attention, pending_handoffs: 2, delivery_issues: 1 },
    channels: [
      {
        id: "phone",
        name: "Sales phone",
        kind: "whatsapp",
        connection_state: "close",
        live_enabled: true,
      },
    ],
    leads: [
      {
        key: "lead",
        customer: "Customer <script>",
        channel: "whatsapp",
        requirements: "Need 5 items",
        interest: ["Service"],
        ownership: "HANDOFF_PENDING",
        temperature: "hot",
        score: 80,
      },
    ],
    agent: { published_version: 1, unpublished_changes: true },
  });
  assert.match(html, /conversations waiting for staff/);
  assert.match(html, /messages need delivery review/);
  assert.match(html, /Connection: close/);
  assert.match(html, /Live replies on/);
  assert.match(html, /Needs staff follow-up/);
  assert.match(html, /Need 5 items/);
  assert.match(html, /Customer &lt;script&gt;/);
  assert.match(html, /Unpublished changes/);
});

test("channel trends include an accessible daily count table and range controls", () => {
  const html = render({
    ...data,
    trend: [
      ...data.trend.slice(0, 6),
      { date: "2026-10-11", total: 4, channels: { whatsapp: 3, instagram: 1 } },
    ],
  });
  assert.match(html, /4 new customer conversations/);
  assert.match(
    html,
    /<caption>New customer conversations by day and channel<\/caption>/,
  );
  assert.match(html, /aria-label="Activity period"/);
  assert.match(html, /aria-pressed="true"/);
  assert.match(html, /<td>3<\/td>/);
  assert.match(html, /<td>1<\/td>/);
});

test("failed background refresh makes stale data explicit", () => {
  const html = render(data, { error: "Network unavailable" });
  assert.match(html, /role="alert"/);
  assert.match(html, /Showing the last successful update/);
});

test("a failed period change keeps the range label tied to the loaded seven-day data", () => {
  const html = render(data, { days: 30, error: "Network unavailable" });
  assert.match(html, /aria-pressed="true">7 days/);
  assert.match(html, /aria-pressed="false">30 days/);
  assert.match(html, /Showing the last successful update/);
});
