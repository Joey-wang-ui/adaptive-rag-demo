import assert from "node:assert/strict";
import test from "node:test";
import plugin, { buildIncoming, makeHandler } from "../plugin/index.mjs";

const event = { channel: "openclaw-weixin", accountId: "bot", senderId: "alice", messageId: "m1", content: "订单", isGroup: false, media: [{ path: "C:/media/receipt.png", contentType: "image/png" }] };
const ctx = { channelId: "openclaw-weixin", accountId: "bot", senderId: "alice", messageId: "m1" };

test("registers the real inbound hook", () => {
  let registration;
  plugin.register({ on: (...args) => { registration = args; } });
  assert.equal(registration[0], "inbound_claim");
  assert.equal(typeof registration[1], "function");
});

test("preserves host identity and staged image paths", () => {
  const incoming = buildIncoming(event, ctx);
  assert.equal(incoming.sender_id, "alice");
  assert.equal(incoming.attachments[0].path, "C:/media/receipt.png");
});

test("posts to loopback and returns only the audit reply", async () => {
  const handler = makeHandler({ url: "http://127.0.0.1:18840", token: "private", fetchImpl: async (url, options) => {
    assert.equal(url.hostname, "127.0.0.1");
    assert.equal(options.headers.authorization, "Bearer private");
    assert.equal(JSON.parse(options.body).account_id, "bot");
    return new Response(JSON.stringify({ reply: "数量不符" }), { status: 200 });
  } });
  assert.deepEqual(await handler(event, ctx), { handled: true, reply: { text: "数量不符" } });
});

test("backend failure is claimed and cannot fall through to another agent", async () => {
  const handler = makeHandler({ url: "http://127.0.0.1:18840", token: "private", fetchImpl: async () => { throw Error("private-secret"); } });
  const result = await handler(event, ctx);
  assert.equal(result.handled, true);
  assert.ok(!result.reply.text.includes("private-secret"));
  assert.ok(result.reply.text.includes("不代表审核通过"));
});

test("other channels are unaffected and groups are not accepted", async () => {
  const handler = makeHandler({ url: "http://127.0.0.1:18840", token: "private" });
  assert.equal(await handler({ ...event, channel: "other" }, { channelId: "other" }), undefined);
  assert.equal((await handler({ ...event, isGroup: true }, ctx)).handled, true);
});

test("unstaged media is never used as a local file", async () => {
  const handler = makeHandler({ url: "http://127.0.0.1:18840", token: "private" });
  assert.ok((await handler({ ...event, mediaStagingPending: true }, ctx)).reply.text.includes("尚未下载"));
});

test("bridge cannot be configured to send documents to an arbitrary server", () => {
  assert.throws(() => makeHandler({ url: "https://example.com", token: "private" }));
});
