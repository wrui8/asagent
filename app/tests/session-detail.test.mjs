import assert from "node:assert/strict";
import test from "node:test";
import { setImmediate } from "node:timers/promises";
import { compileTestModule } from "./load-typescript.mjs";

const { approvalArgsPreview, reconcileApproval, startSessionPolling } = compileTestModule("session-detail")({
  "./events": compileTestModule("events")(),
  "./format": compileTestModule("format")(),
});

function event(seq, type, payload = {}) {
  return { seq, type, payload, session_id: "session", ts: "2026-11-25T12:00:00" };
}

test("a terminal status drains events after the parallel read and stops polling", async (t) => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  const seenCursors = [];
  const updated = Promise.withResolvers();
  const stop = startSessionPolling({
    initialCursor: 4,
    loadSession: async () => ({ status: "done" }),
    loadApprovals: async () => [],
    async loadEvents(cursor) {
      seenCursors.push(cursor);
      return cursor === 4
        ? [event(5, "tool_result")]
        : [event(5, "tool_result"), event(6, "final")];
    },
    onUpdate: updated.resolve,
    onError: updated.reject,
  });
  try {
    const result = await updated.promise;
    t.mock.timers.tick(1500);
    await setImmediate();
    assert.deepEqual(seenCursors, [4, 5]);
    assert.deepEqual(result.events.map(({ seq, type }) => [seq, type]), [[5, "tool_result"], [6, "final"]]);
    assert.equal(result.cursor, 6);
  } finally {
    stop();
  }
});

test("stopping a poll discards in-flight data, and restarting uses the last applied cursor", async () => {
  const delayed = Promise.withResolvers();
  const seenUpdates = [];
  const seenErrors = [];
  const stop = startSessionPolling({
    initialCursor: 4,
    loadSession: async () => ({ status: "running" }),
    loadApprovals: async () => [],
    loadEvents: (_cursor, signal) => {
      signal.addEventListener("abort", () => delayed.resolve([event(5, "tool_result")]));
      return delayed.promise;
    },
    onUpdate: (value) => seenUpdates.push(value),
    onError: (error) => seenErrors.push(error),
  });
  stop();
  await setImmediate();
  assert.deepEqual(seenUpdates, []);
  assert.deepEqual(seenErrors, []);

  const updated = Promise.withResolvers();
  const stopRestarted = startSessionPolling({
    initialCursor: 4,
    loadSession: async () => ({ status: "done" }),
    loadApprovals: async () => [],
    loadEvents: async (cursor) => cursor === 4 ? [event(5, "tool_result"), event(6, "final")] : [],
    onUpdate: updated.resolve,
    onError: updated.reject,
  });
  try {
    assert.deepEqual((await updated.promise).events.map(({ seq }) => seq), [5, 6]);
  } finally {
    stopRestarted();
  }
});

test("a failed read retries from the cursor of the last successful read", async (t) => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  const cursors = [];
  const errors = [];
  const updates = [];
  let loads = 0;
  const stop = startSessionPolling({
    initialCursor: 4,
    loadSession: async () => ({ status: "running" }),
    loadApprovals: async () => [],
    async loadEvents(cursor) {
      cursors.push(cursor);
      loads += 1;
      if (loads === 2) throw new Error("connection interrupted");
      return [event(cursor + 1, "tool_result")];
    },
    onUpdate: (update) => updates.push(update.cursor),
    onError: (error) => errors.push(error.message),
  });
  try {
    await setImmediate();
    t.mock.timers.tick(1500);
    await setImmediate();
    t.mock.timers.tick(4000);
    await setImmediate();
    assert.deepEqual(updates, [5, 6]);
    assert.deepEqual(cursors, [4, 5, 5]);
    assert.deepEqual(errors, ["connection interrupted"]);
  } finally {
    stop();
  }
});

test("a decision confirmed by the server event takes priority over a stale pending card", () => {
  const snapshot = {
    approvals: [{ call_id: "test-write", status: "pending" }],
    events: [event(10, "approval_decided", { call_id: "test-write", approved: true })],
  };
  assert.equal(reconcileApproval("test-write", true, snapshot), "confirmed");
  assert.equal(reconcileApproval("test-write", false, snapshot), "other-decision");
});

test("an ambiguous client response permits retry only for a confirmed pending approval", () => {
  assert.equal(reconcileApproval("test-write", true, {
    approvals: [{ call_id: "test-write", status: "pending" }], events: [],
  }), "pending");
  assert.equal(reconcileApproval("test-write", true, {
    approvals: [], events: [event(10, "approval_decided", { call_id: "another-call", approved: true })],
  }), "unconfirmed");
  assert.equal(reconcileApproval("test-write", false, {
    approvals: [{ call_id: "test-write", status: "denied" }], events: [],
  }), "confirmed");
});

test("long approval previews keep the full document and short previews stay unchanged", () => {
  const content = `${"x".repeat(850)}end-of-document`;
  const long = approvalArgsPreview({ path: "test.md", content });
  assert.equal(JSON.parse(long.full).content, content);
  assert.match(long.preview, /…$/);
  assert.equal(long.preview.includes("end-of-document"), false);
  const manyLines = approvalArgsPreview(Object.fromEntries(
    Array.from({ length: 11 }, (_, i) => [`key${i}`, i]),
  ));
  assert.match(manyLines.preview, /…$/);
  const short = approvalArgsPreview({ path: "test.md" });
  assert.equal(short.preview, short.full);
});
