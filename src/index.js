// Cloudflare Telegram WEB proxy integration layer.
// The protocol relay is vendored from ToiCF/CF-Workers-TGProxy (GPL-3.0).
import relay from "./vendor/telegram_webproxy.js";

const validSecret = (value) => typeof value === "string" && /^[0-9a-f]{32}$/i.test(value.trim());
const validBoot = (value) => typeof value === "string" && value.trim().length >= 32;

export default {
  async fetch(request, env, context) {
    // Refuse to operate without strong, explicitly provisioned credentials.
    // Using plain 16-byte secrets also matches the widest WEB client support.
    if (!validSecret(env?.SECRET) || !validBoot(env?.BOOT)) {
      return new Response("Service unavailable", {
        status: 503,
        headers: {
          "Cache-Control": "no-store",
          "X-Content-Type-Options": "nosniff",
        },
      });
    }
    try {
      return await relay.fetch(request, env, context);
    } catch {
      console.error("[web-proxy] Unhandled relay failure");
      return new Response("Service unavailable", {
        status: 503,
        headers: { "Cache-Control": "no-store" },
      });
    }
  },
};
