import assert from "node:assert/strict";
import { test } from "node:test";
import { compileTestModule } from "./load-typescript.mjs";

const refreshKey = "asagent.mobile.refresh";
const user = {
  id: "u1", username: "tester", role: "user", disabled: false,
  created_at: "2026-11-25T00:00:00Z", last_login_at: null,
};
const tokens = {
  access_token: "access", token_type: "bearer", expires_in: 900, refresh_token: "rotated", user,
};

const stored = new Map();
let deleteFails = false;
const store = {
  async getItemAsync(key) { return stored.get(key) ?? null; },
  async setItemAsync(key, value) { stored.set(key, value); },
  async deleteItemAsync(key) {
    if (deleteFails) throw new Error("Keychain unavailable");
    stored.delete(key);
  },
};

const loadApi = compileTestModule("api");
const config = compileTestModule("config")();
const loadStorage = compileTestModule("storage");

async function configure(refreshToken) {
  await store.deleteItemAsync(refreshKey);
  if (refreshToken) await store.setItemAsync(refreshKey, refreshToken);
  return loadApi({
    "./config": config,
    "./storage": loadStorage({ "react-native": { Platform: { OS: "ios" } }, "expo-secure-store": store }),
  });
}

async function settlesBeforeNextTurn(pending) {
  const settled = await Promise.race([
    pending.then(() => true),
    new Promise((resolve) => setImmediate(() => resolve(false))),
  ]);
  assert.equal(settled, true, "request did not settle when it was interrupted");
}

test("offline startup preserves credentials and restores the account on retry", async (t) => {
  const { bootstrap } = await configure("original");
  let online = false;
  t.mock.method(globalThis, "fetch", async (url) => {
    if (!online) throw new TypeError("connection unavailable");
    return Response.json(url.endsWith("/me") ? user : tokens);
  });

  assert.equal((await bootstrap())?.status, "retry");
  assert.equal(await store.getItemAsync(refreshKey), "original");
  online = true;
  assert.deepEqual(await bootstrap(), { status: "authed", user });
  assert.equal(await store.getItemAsync(refreshKey), "rotated");
});

test("invalid refresh credentials return to sign-in and are removed", async (t) => {
  const { bootstrap } = await configure("expired");
  t.mock.method(globalThis, "fetch", async () =>
    Response.json({ code: "refresh_expired" }, { status: 401 }),
  );

  assert.deepEqual(await bootstrap(), { status: "anon", user: null });
  assert.equal(await store.getItemAsync(refreshKey), null);
});

test("losing network during protected-request refresh does not sign out the user", async (t) => {
  const { login, fetchSessions, setExpiredHandler } = await configure();
  let expired = false;
  setExpiredHandler(() => { expired = true; });
  t.mock.method(globalThis, "fetch", async (url) => {
    if (url.endsWith("/login")) return Response.json(tokens);
    if (url.endsWith("/refresh")) throw new TypeError("connection unavailable");
    return Response.json({ code: "access_expired" }, { status: 401 });
  });

  await login("tester", "password");
  await assert.rejects(fetchSessions(), { code: "network_error" });
  assert.equal(expired, false);
  assert.equal(await store.getItemAsync(refreshKey), "rotated");
});

test("an endpoint rejecting a renewed access token leaves the refresh token intact", async (t) => {
  const { login, fetchSessions, setExpiredHandler } = await configure();
  let expired = false;
  setExpiredHandler(() => { expired = true; });
  t.mock.method(globalThis, "fetch", async (url) => {
    if (url.endsWith("/login")) return Response.json(tokens);
    if (url.endsWith("/refresh")) {
      return Response.json({ ...tokens, access_token: "new-access", refresh_token: "new-refresh" });
    }
    return Response.json({ code: "access_rejected" }, { status: 401 });
  });

  await login("tester", "password");
  await assert.rejects(fetchSessions(), { code: "access_rejected" });
  assert.equal(expired, false);
  assert.equal(await store.getItemAsync(refreshKey), "new-refresh");
});

test("a response with a stalled JSON body reaches the request deadline", async (t) => {
  const { fetchSessions } = await configure();
  t.mock.timers.enable({ apis: ["setTimeout"] });
  t.mock.method(globalThis, "fetch", async () => ({
    ok: true,
    status: 200,
    json: () => new Promise(() => {}),
  }));

  const pending = assert.rejects(fetchSessions(), { code: "request_timeout" });
  await Promise.resolve();
  t.mock.timers.tick(60_000);
  await settlesBeforeNextTurn(pending);
});

test("cancelling a session read leaves authentication untouched", async (t) => {
  const { fetchSession, setExpiredHandler } = await configure("stored");
  let expired = false;
  setExpiredHandler(() => { expired = true; });
  t.mock.method(globalThis, "fetch", () => new Promise(() => {}));

  const controller = new AbortController();
  const pending = assert.rejects(fetchSession("s1", controller.signal), { name: "AbortError" });
  controller.abort();
  await settlesBeforeNextTurn(pending);
  assert.equal(expired, false);
  assert.equal(await store.getItemAsync(refreshKey), "stored");
});

test("a refresh response arriving after logout never restores local credentials", async (t) => {
  const { bootstrap, logout } = await configure("original");
  const started = Promise.withResolvers();
  const response = Promise.withResolvers();
  t.mock.method(globalThis, "fetch", (url) => {
    if (url.endsWith("/refresh")) {
      started.resolve();
      return response.promise;
    }
    if (url.endsWith("/logout")) return Promise.resolve(new Response(null, { status: 204 }));
    return Promise.resolve(Response.json(user));
  });

  const pending = bootstrap();
  await started.promise;
  await logout();
  response.resolve(Response.json(tokens));
  assert.deepEqual(await pending, { status: "anon", user: null });
  assert.equal(await store.getItemAsync(refreshKey), null);
});

test("offline sign-out succeeds when the local credential was removed", async (t) => {
  const { logout } = await configure("original");
  t.mock.method(globalThis, "fetch", async () => { throw new TypeError("connection unavailable"); });

  assert.equal(await logout(), true);
  assert.equal(await store.getItemAsync(refreshKey), null);
});

test("sign-out reports failure when both Keychain deletion and server revocation fail", async (t) => {
  const { logout } = await configure("original");
  deleteFails = true;
  t.after(() => { deleteFails = false; });
  t.mock.method(globalThis, "fetch", async () => { throw new TypeError("connection unavailable"); });

  assert.equal(await logout(), false);
  assert.equal(await store.getItemAsync(refreshKey), "original");
});
