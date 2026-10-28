# Architecture and security notes

## Intended scope

This is an **experimental, WEB-capable Telegram client** proxy using Cloudflare Workers as a public HTTPS/WSS edge. It is **not** a general SOCKS5 proxy, a vanilla MTProxy server, or a replacement for the official `telegramdesktop/tproxy-server` with identical guarantees. It is not a promise of unblocking in every network or on every Telegram client.

```text
WEB-capable Telegram client
  -> hidden WebView -> HTTPS / WSS (Cloudflare edge)
  -> Cloudflare Worker (authenticated WEB bridge + per-lane relay)
  -> wss://kws{1..5}[-1].web.telegram.org/apiws
  -> Telegram Web K
```

The Worker terminates *transport-layer* MTProto obfuscation and re-wraps packets for KWS. It does **not** decrypt Telegram's inner MTProto message content. Do not claim that all bytes are opaque to the Worker.

## Trust and abuse boundaries

- Inbound clients are not allowed to select arbitrary outbound destinations: upstream KWS destinations are hard-coded.
- `SECRET` is a random 16-byte hex WEB proxy key; `BOOT` is an independent, random bootstrap signing secret. Both are Wrangler **secret bindings**, not committed variables.
- A derived HMAC bridge capability gates the WebView page; signed, expiring tokens gate sessions.
- The public origin returns an ordinary HTML page without the correct bridge capability.
- `.local/` and `.dev.vars` are ignored; never commit credentials, screenshots of a live link, or build artifacts containing them.
- Cloudflare Access / interactive challenges must not be applied to the carrier path; they break automated WebView transport.
- Cloudflare WAF, rate limits, platform quotas, account policy, and egress availability still apply.

## Known limitations

1. **Experimental upstream protocol.** Telegram client and KWS behavior can change.
2. **Worker isolate affinity is not guaranteed.** The vendored relay uses an in-memory `leases` map; it is not a globally consistent session store across isolates or PoPs. Long-lived multi-lane sessions and reconnects must be validated under real traffic. A Durable Object / shared-state redesign would be required for global lease enforcement.
3. **WebSocket lifecycle.** Idle sessions and stream connections are subject to Cloudflare Workers limits and connection termination; there is no reliability SLA.
4. **No measured performance claim.** No throughput, latency, or stability improvement is asserted without end-to-end benchmarks.
5. **Compatibility is client-dependent.** Standard `tg://proxy` MTProto links are not interchangeable with `tg://webproxy` links. There is no general-purpose SOCKS5 endpoint.
6. **Health/probe is only a smoke test.** `npm run probe` cannot verify a real Telegram login or Telegram KWS exchange.

## Production hardening backlog

- Replace process-local lease enforcement with Durable Objects or a rigorously tested stateless admission protocol.
- Add bounded per-session outbound queue and load testing across multiple Cloudflare colos.
- End-to-end Desktop and Android test matrix (including media, reconnects, and long idle periods).
- A load-test harness and memory / CPU telemetry before making commercial availability claims.

## Third-party code

`src/vendor/telegram_webproxy.js` is an unchanged snapshot of
[ToiCF/CF-Workers-TGProxy](https://github.com/ToiCF/CF-Workers-TGProxy)
at upstream blob SHA `d6ee0a98e09b2375f4ed52041f0231331b3ee71f`.
It is licensed under GPL-3.0; this repository retains that license and identifies the upstream authors. This project's wrapper, scripts, and tests were added October 28, 2026.

Reference protocol: [telegramdesktop/tproxy-server](https://github.com/telegramdesktop/tproxy-server).
