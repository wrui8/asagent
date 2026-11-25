import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { afterEach, test } from "node:test";
import { transformWithEsbuild } from "vite";

const channel = "asagent.mobile.v1";
let sequence = 0;

async function frame(href = "about:srcdoc") {
  const messages = [];
  const page = new EventTarget();
  page.origin = "https://preview.example";
  page.location = new URL(href);
  page.history = {
    state: { session: "test" },
    replaceState(state, _, next) {
      if (page.location.origin === "null") throw new Error("Cannot rewrite an about:srcdoc URL");
      assert.equal(state, page.history.state);
      page.location = new URL(next);
    },
  };
  page.parent = {
    postMessage: (data, origin) => messages.push({ data, origin }),
  };
  page.setTimeout = setTimeout;
  page.clearTimeout = clearTimeout;
  globalThis.window = page;
  const source = await readFile(new URL("../src/lib/mobile-host.ts", import.meta.url), "utf8");
  const { code } = await transformWithEsbuild(source, "mobile-host.ts", {
    define: { "import.meta.env.MODE": '"mobile"' },
  });
  const transport = await import(`data:text/javascript;base64,${Buffer.from(code).toString("base64")}#${++sequence}`);
  function reply(data, source = page.parent, origin = page.origin) {
    const event = new Event("message");
    Object.assign(event, { data: { channel, ...data }, source, origin });
    page.dispatchEvent(event);
  }
  return { page, messages, reply, transport, ...transport };
}

afterEach(() => { delete globalThis.window; });

test("iframe request responses are matched by id and restricted to the parent origin and window", async () => {
  const bridge = await frame();
  const first = bridge.callMobile("request", "/api/sessions", { method: "GET" });
  const second = bridge.callMobile("request", "/api/kb/docs", { method: "GET" });
  const [a, b] = bridge.messages;
  assert.equal(a.origin, bridge.page.origin);
  assert.notEqual(a.data.id, b.data.id);
  bridge.reply({ id: a.data.id, result: "forged" }, {});
  bridge.reply({ id: a.data.id, result: "forged" }, bridge.page.parent, "https://other.example");
  bridge.reply({ id: b.data.id, result: ["docs"] });
  bridge.reply({ id: a.data.id, result: ["sessions"] });
  assert.deepEqual(await Promise.all([first, second]), [["sessions"], ["docs"]]);
});

test("native host errors keep their API status and credentials never pass back with login", async () => {
  const bridge = await frame();
  const outbound = [];
  bridge.page.ReactNativeWebView = { postMessage: (raw) => outbound.push(JSON.parse(raw)) };
  const login = bridge.callMobile("login", "test-user", "fixture");
  assert.equal(outbound[0].method, "login");
  bridge.page.__asagentReceive({ channel, id: outbound[0].id, result: { id: "u1" } });
  assert.deepEqual(await login, { id: "u1" });
  const failure = bridge.callMobile("request", "/api/audit", {});
  bridge.page.__asagentReceive({ channel, id: outbound[1].id, error: { status: 403, code: "forbidden", message: "无权限" } });
  await assert.rejects(failure, { message: "无权限", code: "forbidden", status: 403 });
});

test("an aborted read ignores even a late success from the host", async () => {
  const bridge = await frame();
  const controller = new AbortController();
  const read = bridge.requestMobile("/api/sessions", { method: "GET" }, controller.signal);
  controller.abort();
  bridge.reply({ id: bridge.messages[0].data.id, result: "late" });
  await assert.rejects(read, { name: "AbortError" });
});

test("inline preview restores a deep link in router memory without rewriting about:srcdoc", async () => {
  const { transport, page } = await frame();
  assert.equal(transport.hasDocumentHistory(), false);
  transport.initializeMobileHistory("/audit?filter=approval");
  assert.equal(transport.mobileEntryPath, "/audit?filter=approval");
  assert.equal(page.location.href, "about:srcdoc");
});

test("native document restores internal hash routes and safely defaults invalid external links", async () => {
  const { transport, page } = await frame("https://asagent.mobile.invalid/");
  assert.equal(transport.hasDocumentHistory(), true);
  transport.initializeMobileHistory("/sessions/example?tab=timeline");
  assert.equal(page.location.href, "https://asagent.mobile.invalid/#/sessions/example?tab=timeline");
  transport.initializeMobileHistory("//other.example/");
  assert.equal(page.location.href, "https://asagent.mobile.invalid/#/");
});

test("links received while restoring a session are retained until the first router mount", async () => {
  const { transport, reply } = await frame();
  transport.initializeMobileHistory("/");
  reply({ event: "navigate", path: "/sessions/during-bootstrap" });
  assert.equal(transport.mobileEntryPath, "/sessions/during-bootstrap");
});

test("only the host can deliver application lifecycle and deep-link events to the router", async () => {
  const bridge = await frame();
  const events = [];
  for (const name of ["active", "inactive", "logout", "navigate"]) {
    bridge.page.addEventListener(`asagent:${name}`, (event) => events.push([name, event.detail]));
  }
  bridge.reply({ event: "active" }, {});
  bridge.reply({ event: "navigate", path: "https://external.example/" });
  bridge.reply({ event: "inactive" });
  bridge.reply({ event: "active" });
  bridge.reply({ event: "navigate", path: "/audit" });
  bridge.reply({ event: "expired" });
  assert.deepEqual(events, [
    ["inactive", undefined], ["active", undefined], ["navigate", "/audit"], ["logout", undefined],
  ]);
});
