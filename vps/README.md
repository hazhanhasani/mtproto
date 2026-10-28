# Blue Proxy Manager — VPS Installation

> **Experimental** WEB relay, official MTProto backend. Supported on clean x86_64 Debian 12+ / Ubuntu 22.04+. The upstream installer takes ownership of Caddy's public ports 80/443. Do not run on a busy VPS hosting other websites.

## Architecture

WEB-capable Telegram client -> HTTPS 443 -> Caddy -> Telegram Desktop tproxy-server -> official MTProxy backend -> Telegram

Standard Telegram client -> TCP 10443 + profile slot -> official MTProxy -> Telegram

Admin -> HTTPS panel domain -> Caddy -> Flask/Gunicorn (127.0.0.1:8750) -> restricted root config reconciler

## Prerequisites

- A VPS with SSH/root, public IPv4, free ports TCP 80/443, enough resources to compile MTProxy and a Go relay.
- Two **DNS-only** A records pointing directly to your VPS: `proxy.example.com` and `panel.example.com`.
- Open TCP 80, 443 and **10443-10474** in the provider firewall; if UFW is active, allow ports used by enabled profiles. Preserve SSH access.
- Do **not** enable Cloudflare orange-cloud CDN on these proxy domains. It does not forward arbitrary MTProto TCP connections and the initial official WEB relay rollout is intended for direct-to-VPS connections.

## One-command installation

Review the bootstrap before executing, then:

```bash
curl -fsSL https://raw.githubusercontent.com/hazhanhasani/mtproto/main/vps/bootstrap.sh -o /tmp/blue-proxy-bootstrap.sh
sudo bash /tmp/blue-proxy-bootstrap.sh \
  --domain proxy.example.com \
  --panel-domain panel.example.com \
  --email admin@example.com
```

The installer fetches Telegram Desktop's reference WEB relay at pinned commit
`c8adb8b7c6b7fc46c12ae3acb68be9070c26a8e8`, runs its installer
and builds official MTProxy. It generates a random WEB/MTProto secret and admin password, then enables the unprivileged admin panel, per-secret MTProxy systemd units, firewall rules and GitHub updater timer.

**Retrieve credentials:** the installer prints them once and writes `/root/blue-mtproxy-credentials.txt` mode `0600`.

Panel username: `admin`, password: auto-generated. Panel URL: `https://panel.example.com`.

## Managing sponsors

Each profile has an independent 16-byte secret, public TCP MTProto port (10443 + slot), backend process, optional channel reference, official 32-hex `Ad Tag`, WEB carrier and enable toggle. The channel reference is internal metadata; a promoted channel **must** be configured with Telegram using [@MTProxyBot](https://t.me/MTProxyBot).

1. In the panel, create a proxy or use the initial profile. Copy its `t.me/proxy` direct link (port and secret).
2. Register that endpoint with `@MTProxyBot` using `/newproxy`. The public TCP port must be reachable.
3. Configure the promoted channel in Telegram's bot flow and retrieve the 32-character hexadecimal `Ad Tag`.
4. Paste the tag into that profile and save; the corresponding MTProxy process restarts.
5. Verify channel display in a real Telegram client. Telegram controls whether and when promotions appear.

Each profile also exposes an experimental `t.me/webproxy` link. **WEB client support is not universal**; ordinary MTProto links are a compatible fallback.

## Update and operations

The root-owned checkout `/opt/blue-mtproxy` tracks the repository's `main` branch via a systemd timer approximately every six hours. The updater blocks non-fast-forward history rewrites, runs tests, reinstalls pinned Python dependencies, restarts the panel and rolls back the Git revision on failed panel health checks. Trigger manually from the panel or use:

```bash
sudo systemctl start blue-mtproxy-update-force.service
sudo systemctl status blue-mtproxy-panel caddy tproxy-server
sudo systemctl status 'blue-mtproxy@main'
sudo systemctl list-timers | grep blue-mtproxy
sudo journalctl -u blue-mtproxy-update.service -n 100 --no-pager
curl -f http://127.0.0.1:8081/healthz
curl -f http://127.0.0.1:8750/healthz
```

The updater updates **the control plane only**, not the upstream Telegram/Caddy binaries, OS, or systemd units. These require a separate reviewed maintenance procedure.

## Safety and limits

- VPS installs have **not been smoke-tested on a live VM** yet. CI covers Python validation and shell syntax; do not assume production readiness without end-to-end connectivity, long connection tests and backup/restore drills.
- The panel runs as `bluepanel`; privilege escalation is limited to a validated fixed-argument reconciler and an updater systemd unit. Session cookies are HTTP-only, Secure, SameSite Strict, with CSRF tokens, strict CSP and scrypt password hashing.
- The reconciler validates profile IDs, secrets, tags, ports and carrier modes before applying configs. Old secrets stop working upon rotation; connected sessions may disconnect during changes.
- Protect your SSH access and backups. Sensitive data is stored in `/var/lib/blue-mtproxy/state.json` (mode `0600`) and `/etc/blue-mtproxy/panel.env`. Back up these files, `/etc/tproxy-server/token.key` and `/etc/tproxy-server/config.json` securely off-host.
- The WEB relay is based on a **proof of concept**: [telegramdesktop/tproxy-server](https://github.com/telegramdesktop/tproxy-server). MTProxy is built from [TelegramMessenger/MTProxy](https://github.com/TelegramMessenger/MTProxy). The Cloudflare Worker directory is a separate experimental alternative.
- Cloudflare standard CDN cannot forward MTProxy TCP. Keep proxy DNS-only; Cloudflare Spectrum is a separate option subject to its own availability and conditions.
- Use additional MFA/SSO or a restricted admin IP policy for a high-value public panel. Login throttling is basic, process-local.

## Development

```bash
python3 -m unittest discover -s vps/tests -v
bash -n vps/bootstrap.sh vps/install.sh vps/update.sh
python3 -m compileall -q vps
```
