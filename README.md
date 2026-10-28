# Telegram Proxies: VPS Manager and Cloudflare Worker

## VPS — Blue Proxy Manager (WEB + MTProto + Sponsors)

**Start here:** [VPS installation and management](vps/README.md).

The VPS stack supports multiple independent secrets, sponsor Ad Tags, RTL Persian admin panel, Caddy TLS, automatic GitHub checks and a quick install on a **fresh** Debian/Ubuntu VPS.

```bash
curl -fsSL https://raw.githubusercontent.com/hazhanhasani/mtproto/main/vps/bootstrap.sh -o /tmp/blue-proxy-bootstrap.sh
sudo bash /tmp/blue-proxy-bootstrap.sh --domain proxy.example.com --panel-domain panel.example.com --email admin@example.com
```

The two DNS-only domains must point to your VPS. Open TCP 80/443 and the MTProto ports 10443–10474 (or just the ports you use). **Do not execute on an existing production webserver.** WEB proxy support remains experimental.

---

## Cloudflare Worker (separate experimental WEB option)

> **Status: experimental / proof of concept.** Deployable Cloudflare Worker, not an officially supported Telegram relay. Live Telegram connectivity has **not** been verified for this repository.

Cloudflare CDN edge + WebSocket transport for Telegram clients that implement **WEB** proxy support, without a separate VPS. The backend relay is a pinned GPL-3.0 snapshot of [ToiCF/CF-Workers-TGProxy](https://github.com/ToiCF/CF-Workers-TGProxy). It connects only to Telegram Web K KWS endpoints and is **not** a generic open TCP proxy.

## Requirements

- A Cloudflare account and a DNS zone you control (recommended); or a `*.workers.dev` hostname for testing.
- Node.js 22+.
- A Telegram client supporting the experimental `WEB` proxy transport. Ordinary MTProto/SOCKS5 clients are **not** compatible.

## Deploy

```bash
git clone https://github.com/hazhanhasani/mtproto.git
cd mtproto
npm install
npx wrangler login
npm run setup -- proxy.example.com
npm run deploy
npm run publish-secrets
```

Replace `proxy.example.com` with **your actual future Worker hostname**. The `setup` command generates two random independent credentials locally, creates a private `.local/credentials.json` and `.dev.vars`, and prints a WEB client link. **Never commit these files.**

Then open the [Cloudflare Workers dashboard](https://dash.cloudflare.com/), select **blue-telegram-webproxy**, and use **Settings > Domains & Routes > Add > Custom Domain**. Add the exact hostname passed to setup. Cloudflare manages the HTTPS certificate and DNS record for a valid zone. No separate orange-cloud A/AAAA origin is needed for a Custom Domain.

Finally:

```bash
npm run probe
npm test
```

`probe` checks the live HTTPS health route, bridge capability, and unauthenticated camouflage; **it does not test a Telegram connection**. Test a real WEB-capable client afterward. For local development, run `npm run dev`.

## Connecting Telegram

Use the `tg://webproxy?server=...&secret=...` URL printed by setup, or enter:

- Proxy type: **WEB**
- Server: your exact lowercase Worker domain, without `https://`, port or slash
- Secret: the 32-digit hexadecimal `SECRET` from your local credentials

This is **not** a `tg://proxy` link. The `https://t.me/webproxy` share URL printed by the script may not be recognized by the public t.me frontend on every client; direct input may be necessary.

## Operations

| Action | Command |
| --- | --- |
| Run tests | `npm test` |
| Local preview | `npm run dev` |
| Publish code | `npm run deploy` |
| Update Cloudflare secret bindings | `npm run publish-secrets` |
| Verify HTTPS bridge | `npm run probe` |
| **Intentionally rotate** credentials | `npm run setup -- proxy.example.com --rotate` then `npm run publish-secrets` |

**WARNING:** Rotation immediately invalidates old WEB proxy links. Keep the same hostname when using existing credentials. Secret changes do not automatically update users.

## Security and stability

- Both secrets are stored only in Cloudflare encrypted bindings and local gitignored files.
- Fail-closed behavior: missing or weak secret bindings return HTTP 503.
- An authenticated HMAC capability is required to serve the WEB bridge.
- Only hard-coded Telegram KWS hosts are contacted; this is not an open proxy.
- The upstream uses process-local lease tracking: **Cloudflare isolates may change**, so multi-lane session enforcement and stability have not been proven. Do not treat this as audited production software.
- Cloudflare quotas, platform policy, the evolving Telegram WEB protocol, and availability of KWS endpoints may affect service.

Read [Architecture and security](docs/ARCHITECTURE.md) for the trust model and current backlog.

## License and attribution

GNU GPL-3.0. Upstream relay code: [ToiCF/CF-Workers-TGProxy](https://github.com/ToiCF/CF-Workers-TGProxy), pinned blob `d6ee0a98e09b2375f4ed52041f0231331b3ee71f`. See [LICENSE](LICENSE) and [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

> No production API keys or generated client secrets are stored in this repository.
