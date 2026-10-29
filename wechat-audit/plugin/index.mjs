/** Official Weixin transport handles login/media. This plugin only claims shipment messages. */

export function buildIncoming(event, ctx) {
  const account = ctx.accountId || event.accountId;
  const sender = ctx.senderId || event.senderId;
  const message = ctx.messageId || event.messageId || event.providerUpdate?.id || ctx.runId;
  if (!account || !sender || !message) return null;
  let media = Array.isArray(event.media) ? event.media : undefined;
  // Released host versions still expose these documented compatibility aliases.
  if (!Array.isArray(media) && !event.mediaStagingPending) {
    const meta = event.metadata || {};
    const paths = Array.isArray(meta.mediaPaths) ? meta.mediaPaths : (meta.mediaPath ? [meta.mediaPath] : []);
    const types = Array.isArray(meta.mediaTypes) ? meta.mediaTypes : (meta.mediaType ? [meta.mediaType] : []);
    media = paths.map((path, index) => ({ path, contentType: types[index] || "" }));
  }
  return {
    account_id: String(account),
    sender_id: String(sender),
    message_id: String(message),
    text: String(event.body ?? event.content ?? ""),
    attachments: (media || [])
      .filter((item) => item && typeof item.path === "string")
      .map((item) => ({ path: item.path, content_type: item.contentType || "" })),
  };
}

export function makeHandler({ url, token, fetchImpl = fetch }) {
  if (url) {
    const parsed = new URL(url);
    if (parsed.protocol !== "http:" || parsed.hostname !== "127.0.0.1") {
      throw new Error("Shipment bridge must use HTTP on 127.0.0.1");
    }
  }
  return async (event, ctx) => {
    if ((ctx.channelId || event.channel) !== "openclaw-weixin") return;
    // Claim every Weixin event, including failures, so a general agent cannot bypass this workflow.
    const answer = (text) => ({ handled: true, reply: { text } });
    if (event.isGroup) return answer("本审单助手仅支持已授权的微信私聊。");
    if (!url || !token) return answer("本机审单服务未配置。请双击 start-wechat-audit.cmd 启动；未给出发货许可。");
    if (event.mediaStagingPending) return answer("图片尚未下载完成，请稍后重新发送原图。未进行审核。");
    const incoming = buildIncoming(event, ctx);
    if (!incoming) return answer("缺少可验证的消息身份，未处理本次单据。请重新发送。");
    try {
      const response = await fetchImpl(new URL("/message", url), {
        method: "POST",
        headers: { "content-type": "application/json", authorization: `Bearer ${token}` },
        body: JSON.stringify(incoming),
        signal: AbortSignal.timeout(150_000),
      });
      if (!response.ok) return answer(`本机审单服务返回 HTTP ${response.status}，本次未完成，未给出发货许可。`);
      const result = await response.json();
      if (typeof result.reply !== "string" || !result.reply.trim()) throw new Error("Empty reply");
      return answer(result.reply);
    } catch {
      return answer("本机审单服务未响应或识别超时。请检查启动窗口后重试；不代表审核通过。");
    }
  };
}

export default {
  id: "jiahui-shipment",
  name: "嘉惠发货前审单",
  register(api) {
    api.on("inbound_claim", makeHandler({
      url: process.env.SHIPMENT_BRIDGE_URL,
      token: process.env.SHIPMENT_BRIDGE_TOKEN,
    }), { priority: 1000, timeoutMs: 180_000 });
  },
};
