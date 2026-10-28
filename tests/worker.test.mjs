import test from "node:test";
import assert from "node:assert/strict";
import { createHmac } from "node:crypto";
import worker from "../src/index.js";

const host = "proxy.example.com";
const env = { SECRET: "0123456789abcdef0123456789abcdef", BOOT: "b".repeat(64) };
const request = (path, init) => new Request("https://" + host + path, init);

test("fail closed if credentials are missing or weak", async () => {
  for (const e of [{}, { SECRET: env.SECRET, BOOT: "short" }, { SECRET: "abc", BOOT: env.BOOT }]) {
    const res = await worker.fetch(request("/health"), e);
    assert.equal(res.status, 503);
    assert.equal(res.headers.get("Cache-Control"), "no-store");
  }
});
test("health endpoint returns a minimal response", async () => {
  const res = await worker.fetch(request("/health"), env);
  assert.equal(res.status, 200);
  assert.deepEqual(await res.json(), { ok: true });
});
test("unauthenticated homepage stays innocuous", async () => {
  const res = await worker.fetch(request("/"), env);
  assert.equal(res.status, 200);
  assert.equal((await res.text()).includes("TelegramWebProxy"), false);
});
test("authenticated capability yields the isolated WebView bridge", async () => {
  const bridge = createHmac("sha256", Buffer.from(env.SECRET, "hex"))
    .update("tdesktop-web-proxy-bridge-v1\n" + host)
    .digest("base64url");
  const res = await worker.fetch(request("/?bridge=" + bridge), env);
  assert.equal(res.status, 200);
  assert.equal(res.headers.get("Cache-Control"), "no-store");
  assert.match(res.headers.get("Content-Security-Policy") ?? "", /frame-ancestors http:\/\/127\.0\.0\.1:\*/);
  const html = await res.text();
  assert.match(html, /TelegramWebProxy/);
  assert.doesNotMatch(html, new RegExp(env.SECRET));
  assert.doesNotMatch(html, new RegExp(env.BOOT));
});
test("session and WebSocket routes refuse requests without authentication", async () => {
  const session = await worker.fetch(request("/api/v1/session", { method: "POST" }), env);
  assert.equal(session.status, 404);
  const socket = await worker.fetch(request("/api/v1/ws"), env);
  assert.equal(socket.status, 404);
});
test("unmatched routes do not expose proxy internals", async () => {
  const res = await worker.fetch(request("/not-a-route"), env);
  assert.equal(res.status, 404);
});
