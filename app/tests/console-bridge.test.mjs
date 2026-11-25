import assert from "node:assert/strict";
import { test } from "node:test";
import { compileTestModule } from "./load-typescript.mjs";

const {
  consoleChannel, consolePath, dispatchConsoleRequest, incomingConsolePath,
  isBundledWebViewUrl, isConsoleFrameMessage, isExternalConsoleUrl, readConsoleRequest,
} = compileTestModule("console-bridge")();

function request(method, args = []) {
  return { channel: consoleChannel, id: "test-1", method, args };
}

test("only the expected channel and typed message shape reach dispatch", () => {
  const valid = request("bootstrap");
  assert.deepEqual(readConsoleRequest(JSON.stringify(valid)), valid);
  assert.equal(readConsoleRequest({ ...valid, channel: "unrelated" }), null);
  assert.equal(readConsoleRequest({ ...valid, args: {} }), null);
  assert.equal(readConsoleRequest({ ...valid, id: "" }), null);
});

test("legacy and current deep links resolve to the same console route", () => {
  assert.equal(incomingConsolePath("asagent://session/test-1"), "/sessions/test-1");
  assert.equal(incomingConsolePath("exp://192.168.1.2:8081/--/sessions/test-1"), "/sessions/test-1");
  assert.equal(consolePath("#/chat?session=test-1"), "/chat?session=test-1");
  assert.equal(consolePath("//other.example/sessions/test-1"), null);
  assert.equal(incomingConsolePath("https://other.example/sessions/test-1"), null);
});

test("only the bundled document or iframe source can use the bridge", () => {
  const source = {};
  const event = { source, origin: "http://localhost:8082" };
  assert.equal(isConsoleFrameMessage(event, source, event.origin, "about:srcdoc#/kb"), true);
  assert.equal(isConsoleFrameMessage(event, {}, event.origin, "about:srcdoc"), false);
  assert.equal(isConsoleFrameMessage(event, source, "https://other.example", "about:srcdoc"), false);
  assert.equal(isConsoleFrameMessage(event, source, event.origin, `${event.origin}/external`), false);
  assert.equal(isBundledWebViewUrl("https://asagent.mobile.invalid/#/kb"), true);
  assert.equal(isBundledWebViewUrl("https://asagent.mobile.invalid.evil/#/kb"), false);
});

test("native link handling permits HTTP URLs and rejects scheme and user-info escapes", () => {
  assert.equal(isExternalConsoleUrl("https://docs.example/reference"), true);
  assert.equal(isExternalConsoleUrl("http://docs.example/reference"), true);
  for (const url of ["asagent://login", "javascript:alert(1)", "file:///some-file", "https://docs.example@other.example/", "https://asagent.mobile.invalid/new"]) {
    assert.equal(isExternalConsoleUrl(url), false, url);
  }
});

test("route and appearance updates allow original paths and the two themes", async () => {
  const applied = [];
  const services = {
    route: (path, navigation) => applied.push({ path, navigation }),
    theme: (theme) => applied.push({ theme }),
  };
  await dispatchConsoleRequest(request("route", ["/session/record-17", "PUSH"]), services);
  await dispatchConsoleRequest(request("theme", ["light"]), services);
  await dispatchConsoleRequest(request("theme", ["unsupported"]), services);
  await dispatchConsoleRequest(request("route", ["/kb", "INVALID"]), services);
  assert.deepEqual(applied, [{ path: "/sessions/record-17", navigation: "PUSH" }, { theme: "light" }]);
});

test("request dispatch only forwards relative API paths and never passes auth controls", async () => {
  const services = { request: (path, options) => ({ path, ...options }) };
  const accepted = await dispatchConsoleRequest(request("request", [
    "/api/sessions/test-1/approvals?kind=office", { method: "POST", body: { approved: true }, auth: false },
  ]), services);
  assert.deepEqual(accepted.result, {
    path: "/api/sessions/test-1/approvals?kind=office", method: "POST", body: { approved: true },
  });

  for (const path of ["https://other.example/api/sessions", "//other.example/api/sessions",
    "/api/sessions/../auth/refresh", "/api/sessions/%2e%2e/auth/login", "/api/%61uth/login",
    "/api/auth/refresh", "/api/auth/me/", "/api/auth/me#x", "/api/%2561uth/refresh"]) {
    assert.equal((await dispatchConsoleRequest(request("request", [path, {}]), services)).error.code,
      "invalid_request", path);
  }
  const viewer = await dispatchConsoleRequest(request("request", ["/api/auth/me", {}]), services);
  assert.equal(viewer.result.path, "/api/auth/me");
});

test("login and registration return user identity without returning credentials", async () => {
  const user = { id: "test", username: "reader" };
  const tokens = { user, access_token: "secret-1", refresh_token: "secret-2" };
  const services = { login: () => tokens, register: () => tokens };
  for (const [method, args] of [["login", ["reader", "password"]], ["register", ["reader", "password", "code"]]]) {
    assert.deepEqual(await dispatchConsoleRequest(request(method, args), services), {
      channel: consoleChannel, id: "test-1", result: user,
    });
  }
});

test("trace export rejects paths and executable extensions before calling native storage", async () => {
  const services = { exportTrace: (filename) => filename };
  for (const name of ["../trace.json", "./trace.json", "folder/trace.json", "C:\\trace.json", "trace.html"]) {
    assert.equal((await dispatchConsoleRequest(request("exportTrace", [name, "{}"]), services)).error.code,
      "invalid_request", name);
  }
  assert.equal((await dispatchConsoleRequest(request("exportTrace", ["session-1-trace.json", "{}"]), services)).result,
    "session-1-trace.json");
});
