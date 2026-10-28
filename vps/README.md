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

## One-command installation (including automatic SSL)

First create two **DNS-only** A records for your VPS public IPv4, e.g.:
`proxy.example.com` and `panel.example.com`. Cloudflare orange-cloud **must be disabled** for direct MTProto ports. Port 80/443 must be reachable from the public internet, as the certificate authority needs to validate domain ownership. Provider-level firewall ports may need to be opened in the VPS control panel.

On a **fresh** Ubuntu / Debian x86_64 VPS, run this **single command**:

```bash
sudo bash -c 'set -euo pipefail; curl -fsSL https://raw.githubusercontent.com/hazhanhasani/mtproto/main/vps/bootstrap.sh | bash'
```

The installer then asks for:

1. **Proxy domain** — e.g. `proxy.example.com`.
2. **Admin panel domain** — defaults to `panel.example.com` if the proxy domain is `proxy.example.com`; you can change it.
3. **SSL certificate contact email** — e.g. `admin@example.com`.

Alternatively, pass everything non-interactively with one command:

```bash
sudo bash -c 'set -euo pipefail; curl -fsSL https://raw.githubusercontent.com/hazhanhasani/mtproto/main/vps/bootstrap.sh | bash -s -- --domain proxy.example.com --panel-domain panel.example.com --email admin@example.com'
```

The script automatically installs dependencies, pins and compiles the official Telegram relay and MTProxy, configures the Persian management panel and systemd auto-updater, generates random credentials, enables needed UFW rules **only if UFW is already active**, and installs Caddy. **Caddy obtains and automatically renews publicly trusted HTTPS certificates** for both domains. The installer verifies live TLS with normal trust verification (never `-k`), using SNI to reach the local Caddy listener. It does not print "installed" before both HTTPS checks succeed.

The script refuses to overwrite an existing production webserver. Before changing upstream services, it checks that ports TCP 80 and 443 are locally free, both domains resolve directly to the VPS IPv4 and DNS-only, and the requested hostnames are well formed. If this preflight fails, correct DNS/firewall, then run the **same single command again**; a safe preflight-only rerun is supported. For special NAT ingress/egress topologies, `--skip-dns-check` is possible but not recommended.

The installer prints the panel URL, generated username/password, connection links and stores credentials in `/root/blue-mtproxy-credentials.txt` with permissions `0600`.

**Important limitation:** The installer cannot change your hosting provider's external firewall or Cloudflare DNS records without your account credentials. Those prerequisites must be configured once in their dashboards. If ACME certificate validation fails, the installer exits without claiming success, provides diagnostic commands and retains credentials for recovery.

## Recovery from Telegram 404 during initial install

If compilation finishes with `make: Leaving directory .../MTProxy` and the next line says
`curl: (22) ... 404`, the failure is in the upstream download of public
`getProxySecret` or `getProxyConfig` — **not** the compiler and not Caddy SSL.

The installer now checks Telegram's official endpoints first, then uses
`.telegram-cache/proxy-secret` and `.telegram-cache/proxy-multi.conf`
from this repository if needed. A dedicated [GitHub Actions workflow](https://github.com/hazhanhasani/mtproto/actions/workflows/telegram-config-cache.yml)
downloads the official data in GitHub's network, validates exact secret length
and expected config fields, and refreshes the public mirror daily.

On a fresh VPS where the initial run stopped exactly at this 404, simply run
the **same one-line command again**. The bootstrap validates that the previous
installation has not yet configured Caddy or the relay, pulls the updated code
and reuses the compiled MTProxy binary.

If a later stage already wrote `/etc/caddy/Caddyfile` or
`/etc/tproxy-server/config.json`, the bootstrap refuses automatic recovery;
inspect `sudo systemctl status caddy tproxy-server` and preserve existing
files. Do not delete the VPS or reinstall blindly.

The HTTP response code of each official download can be diagnosed without
printing file contents:

```bash
for path in getProxySecret getProxyConfig; do
  printf '%s: ' "$path"
  curl -sSL --connect-timeout 10 --max-time 15 -o /dev/null \
    -w '%{http_code}\n' "https://core.telegram.org/$path"
done
```

## Recovery from Go toolchain HTTP 404 on Ubuntu resolute (26.04)

If the log ends with:

```text
[Telegram config] Verified proxy-secret (128 bytes)
[Telegram config] Verified proxy-multi.conf (821 bytes)
curl: (22) The requested URL returned error: 404
```

the **Telegram configuration downloads are already working**. The next step in
the pinned upstream installer attempts to retrieve the Go compiler archive
`go1.26.5.linux-amd64.tar.gz` from `go.dev/dl/` (which redirects to a Google
download endpoint). On some VPS networks the redirect may produce HTTP 404.
The Go release file itself is published and has a documented SHA-256 checksum.

The fixed installer now runs `vps/ensure-go.sh` **before** the upstream build.
It first accepts an installed Go >= 1.20, then tries the distro's
`golang-go` package, and only if that is unavailable or too old, attempts
multiple HTTPS download origins with the official Go 1.26.5 SHA-256 check.

In Ubuntu **resolute (26.04)**, `golang-go` is packaged as Go 1.26,
so the normal solution is entirely through `apt`.

Rerun the same single-command bootstrap, without deleting
`/opt/MTProxy` or `/etc/mtproxy`. It pulls the new code and retains
the already compiled MTProxy binary on a pre-configuration partial install.

To confirm just the runtime version on your server:

```bash
go version || true
apt-cache policy golang-go
```

If this does not advance, inspect the exact failing URL via
`bash -x /opt/telegram-tproxy-server/deploy/install.sh ...` **only after reviewing
its arguments**. Do not print passwords, secrets or session information in
public logs.

## Recovery after a complete install stops on SSL validation

When the upstream installer says `Installed for https://...`, the panel's Python
dependencies install successfully and `blue-mtproxy-panel.service` is enabled,
the system is **already installed**. **Do not rerun the bootstrap**, because the
installer intentionally refuses to overwrite an existing control plane.

On the existing VPS, inspect the state without changing services:

```bash
sudo git -C /opt/blue-mtproxy pull --ff-only origin main
sudo bash /opt/blue-mtproxy/vps/ssl-status.sh
```

This script verifies each hostname's actual CA-signed TLS certificate and
SNI via localhost, separately from the HTTP response code. It also prints
certificate-related `journalctl -u caddy` lines if verification fails.
A response of HTTP 404/502 is **not** a TLS certificate failure; earlier
versions of the installer incorrectly treated it as one.

Before expecting a trusted certificate, verify:

- Both public domains resolve directly to the VPS IPv4; **no Cloudflare proxy**.
- No stray IPv6 `AAAA` record points at a different/non-working server.
- The VPS provider firewall allows *inbound* TCP 80 and 443; UFW opening
  alone does not modify the provider firewall.
- Caddy is active with access to its persistent certificate store.
- If CAA DNS records exist, they permit at least one ACME issuer supported by Caddy.
- DNS changes have propagated to public authoritative resolvers.

Caddy itself retries ACME issuance in the background. Avoid rapid restarts
or repeated new certificate requests because certificate authorities impose
rate limits. Once DNS/firewall errors are corrected, repeat the read-only
`ssl-status.sh` command. Credentials from the initial installation remain
at `/root/blue-mtproxy-credentials.txt` (root-only, mode 0600).

## Repair a healthy HTTPS certificate with an HTTP 502 panel

If `ssl-status.sh` shows:

```text
[TLS OK] proxy.example.com -> trusted certificate; HTTP status 200
[TLS OK] panel.example.com -> trusted certificate; HTTP status 502
```

**Both TLS certificates are valid; the remaining failure is the admin panel
upstream.** The initial installer used `umask 077` to build
`/opt/blue-mtproxy/.venv` as root, leaving `0700` directories and
executable scripts inaccessible to the `bluepanel` service user. Caddy thus
cannot reach Gunicorn, even if `systemctl is-active` briefly prints `active`
during a restart loop.

Do **not** rerun the installer, delete certificates or reset the VPS. On the
already-installed VPS run:

```bash
sudo git -C /opt/blue-mtproxy pull --ff-only origin main && sudo bash /opt/blue-mtproxy/vps/repair-panel.sh
```

The read/write separation is preserved: the helper keeps the Python virtualenv
**root-owned**, grants its service group only read/execute access, restarts only
the panel systemd unit, checks `http://127.0.0.1:8750/healthz` and reports
both domains' TLS status. It never modifies the state database, user secrets,
Caddy certificates or proxy services. The main installer and updater now
maintain these permissions automatically after installation/dependency updates.

If the panel still fails, share this **non-sensitive** diagnostic output, not
the credentials file or your Secret:

```bash
sudo systemctl --no-pager --full status blue-mtproxy-panel
sudo journalctl -u blue-mtproxy-panel -n 45 --no-pager
curl --noproxy '*' -i --max-time 4 http://127.0.0.1:8750/healthz
```

## WEB proxy reports "Unavailable" in Telegram

A green TLS status and a normal public website do **not** test the WEB
transport. A WEB-compatible client must derive an authenticated HMAC bridge
capability, open the secret-matched bridge page over real HTTPS, obtain a relay
session, and reach its configured local MTProxy backend.

A legacy panel deployment may have created
`/etc/tproxy-server/profiles.json` with group-unreadable `0400`
permissions. This has been corrected to `0640`, with a runtime user-access
check. **This is a defensive correction, not a proven explanation of every
connection failure**: the upstream systemd unit also uses `LoadCredential`,
which may read the source file as root. Verify actual status before
attributing the problem.

On an existing installed VPS, do **not** rerun the initial bootstrap or rotate
secrets. Fetch the fix and run a safe reconciliation:

```bash
sudo git -C /opt/blue-mtproxy pull --ff-only origin main && sudo bash /opt/blue-mtproxy/vps/repair-web.sh
```

This preserves SSL, panel credentials and secrets. It makes the relay profile
accessible to the `tproxy` group, reapplies valid managed profiles, restarts
the relay, and performs read-only health checks. It does not disclose the
client secret or derived bridge capability.

To diagnose without modifying the VPS:

```bash
sudo python3 /opt/blue-mtproxy/vps/proxy-status.py
```

The output reports `caddy` and `tproxy-server` systemd status,
`/readyz`, each `blue-mtproxy@<profile>` unit, local backend TCP reachability,
on-disk relay/profile correspondence, and an authenticated WEB bridge GET
over CA-validated localhost SNI (not just an unauthenticated HTTP 200).
This **does not guarantee a successful session from Telegram**: the client
may not implement the experimental WEB carrier, its WebView may fail, or
the user's network may block the hostname.

A hostname containing `/path` is a separate WEB address with its own
profile/encoded link secret. Do not copy the secret from a root-hostname
proxy into a different path proxy. See the upstream
[WEB link/base-path specification](https://github.com/telegramdesktop/tproxy-server/blob/master/BASE_PATH.md).

## Administrator /debug: root-cause diagnostics without exposing secrets

A standard Telegram client may report **WEB unavailable** even while Caddy
HTTPS, the WEB bridge, and the MTProxy backend are healthy. The upstream
[tproxy-server reference](https://github.com/telegramdesktop/tproxy-server)
documents WEB as a proof of concept with specific Desktop/Android test clients,
and warns that the public `t.me/webproxy` route is not generally registered.
A normal `t.me/proxy` MTProto link is the preferred client-compatibility
baseline; it is on TCP `10443+slot` and must be reachable in the VPS provider
firewall. Standard Cloudflare HTTP proxying cannot forward arbitrary MTProto TCP;
the separate Cloudflare Spectrum product is designed for L4 proxying.

**After initial installation**, run this idempotent one-line updater on the
existing VPS (it does not touch TLS, the state database or proxy secrets):

```bash
sudo git -C /opt/blue-mtproxy pull --ff-only origin main && sudo bash /opt/blue-mtproxy/vps/install-debug.sh
```

Sign into the admin panel and open `https://YOUR-PANEL-DOMAIN/debug`. The
navbar also contains a **/debug** shortcut. For sharing a result, open
`/debug.json` on the **panel** hostname. Both endpoints require an authenticated
admin session and add `Cache-Control: no-store`. A fixed-argument, privileged,
read-only helper collects the result; no Secret, Ad Tag, credentials, bridge
capability, origin file contents or raw request URL is returned.

Each diagnostic report includes:

- local systemd readiness of Caddy, WEB Relay, and admin panel;
- WEB Relay `/readyz` status;
- each enabled official MTProxy backend's systemd status and local TCP port;
- matching panel/relay backend configuration (without printing the secret);
- an authenticated WEB bridge request over SNI-validated HTTPS (not just the
  decoy homepage);
- counts of recurring MTProxy Middle-End disconnections as a NAT warning;
- explicit warnings that client compatibility and *external* TCP reachability
  are not proven by server-side checks.

The probe budget limits expensive authenticated bridge checks to the first
six enabled profiles. For more profiles, the JSON report marks untested ones as
`skipped` instead of falsely reporting `ok`.

You can also request the JSON report locally:

```bash
sudo /usr/local/sbin/blue-mtproxy-debug
```

If server-side checks all pass but the Telegram client still says unavailable,
test the **standard MTProto** link from a mobile data connection and verify the
VPS provider firewall allows inbound TCP `10443` (or the profile's shown port).
Do not change SSL or rotate secrets to diagnose client-level WEB support.
The /debug collector cannot test a real Telegram session from the user's phone;
a successful authenticated bridge response proves only the server bridge stage.

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
