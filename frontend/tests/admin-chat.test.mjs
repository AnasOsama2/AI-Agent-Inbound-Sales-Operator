import assert from "node:assert/strict";
import { test } from "node:test";
import { requestAdminReply } from "../src/admin-chat.js";

function deferred() {
  let resolve;
  const promise = new Promise((done) => {
    resolve = done;
  });
  return { promise, resolve };
}

test("a clinic reply cannot populate the garage after switching workspaces", async () => {
  const pending = deferred();
  let current = "clinic";
  let updates = 0;
  const calls = [];
  const request = requestAdminReply({
    businessId: "clinic",
    text: "Change tone",
    api: (path) => {
      calls.push(path);
      return pending.promise;
    },
    isCurrent: () => current === "clinic",
    onResult: () => {
      updates++;
    },
  });
  current = "garage";
  pending.resolve({
    proposal: { name: "Clinic", document_ids: ["clinic-doc"] },
  });
  await request;
  assert.equal(updates, 0);
  assert.deepEqual(calls, ["/api/businesses/clinic/admin/messages"]);
});

test("switching during history reload also discards the old workspace result", async () => {
  const pending = deferred();
  let current = "clinic";
  let updates = 0;
  const request = requestAdminReply({
    businessId: "clinic",
    text: "Change tone",
    api: async (_, opts) =>
      opts ? { proposal: { tone: "warm" } } : pending.promise,
    isCurrent: () => current === "clinic",
    onResult: () => {
      updates++;
    },
  });
  await Promise.resolve();
  current = "garage";
  pending.resolve({ messages: [{ role: "assistant", text: "Clinic reply" }] });
  await request;
  assert.equal(updates, 0);
});

test("the current workspace receives its own proposal and history", async () => {
  const result = { proposal: { tone: "warm" } };
  const history = { messages: [{ role: "assistant", text: "Review tone" }] };
  let received;
  await requestAdminReply({
    businessId: "clinic",
    text: "Change tone",
    api: async (_, opts) => (opts ? result : history),
    isCurrent: () => true,
    onResult: (...values) => {
      received = values;
    },
  });
  assert.deepEqual(received, [result, history]);
});
