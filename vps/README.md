# Blue Proxy Manager — VPS Installation

## Half-hour Telegram channel post scheduling

The channel bot supports optional scheduled re-posting for individual
inbounds. Sign in to `/bot` or open the inbound detail page and select
**فعال‌سازی ۳۰ دقیقه‌ای** for the desired inbound. Each selected enabled
inbound is sent at minute `00` and `30` of the server's local clock
(48 channel posts per full day per inbound). Disabling the toggle stops
scheduled posting for that inbound only; manual publishing still works.

The systemd `blue-mtproxy-telegram-cron.timer` runs a dedicated
unprivileged oneshot, independent of bot long-polling, using the existing
channel and inbound Secret. It records a successful delivery per
30-minute slot, so re-running the service within that slot won't duplicate
the post. Missing/disabled inbounds are skipped. Network or Telegram errors
remain visible in the timer service journal and will be retried on the
next tick. Existing deployments keep scheduling **off** after upgrade
until the admin explicitly opts an inbound in.

To verify deployment:

```bash
sudo systemctl list-timers blue-mtproxy-telegram-cron.timer --no-pager
sudo systemctl status blue-mtproxy-telegram-cron.timer --no-pager
sudo journalctl -u blue-mtproxy-telegram-cron.service -n 30 --no-pager
```

---

## Telegram channel publishing bot (UI 0.5.0)

The admin panel now has a dedicated **/bot** page. The unprivileged
`blue-mtproxy-telegram-bot.service` performs outbound HTTPS Telegram Bot API
long polling. No webhook, extra public port, firewall rule or Cloudflare
change is required.

**Connect the bot**

1. Create a **new dedicated bot** via [@BotFather](https://t.me/BotFather).
2. Add it as **Administrator** to your destination Telegram channel and
   enable **Post Messages**. Channel usernames (`@YourChannel`) and
   private channel IDs (`-100...`) are supported.
3. Sign in to `https://<panel-hostname>/bot`, enter the BotFather token,
   your numeric Telegram **user ID** (not the bot's ID), and the channel.
   Leave the token input blank on future edits to retain it.
4. Save settings, then message the bot privately with `/start`.
   Only the configured Telegram user ID is permitted to issue commands;
   bot commands in groups are ignored.
5. Run `/new Your Proxy Name` to create an inbound from Telegram, then
   tap **Publish**, or run `/list` and select an existing inbound. The
   **Publish to channel** button also appears in panel inbound details.
   The bot additionally supports `/publish main` and `/channel`.

Enable **Auto Publish** on the `/bot` page to automatically publish future
new inbounds. Existing inbounds are marked as already known when enabling
auto publish or changing destination, avoiding an unintentional flood.
Manual re-posting is always possible. If Telegram denies posting, check
that the bot remains a channel administrator with Post Messages permission.
There is no automatic creation of Telegram channels or bots.

**Cloudflare CDN-only compatibility:** Only the WEB HTTPS:443 transport is
public. The bot publishes `t.me/webproxy` with a clearly marked WEB
experimental button in this mode. This link is **not** a standard MTProto
`t.me/proxy` connection: ordinary Telegram clients do not currently
support the WEB protocol, and the public t.me WEB route may not open in
an unmodified client. A regular **Connect** button can be provided only
when a **direct TCP MTProto listener is externally reachable**, which
is deliberately disabled in CDN-only mode. Do not claim that the
standard MTProto link works through Cloudflare's normal HTTP CDN.

**Installation / upgrade:** Once the code is merged to main, the
existing GitHub updater will run `vps/install-bot.sh` automatically.
For a manual check after updating:

```bash
sudo systemctl status blue-mtproxy-telegram-bot.service --no-pager
sudo journalctl -u blue-mtproxy-telegram-bot.service -n 30 --no-pager
```

Bot settings, update cursor and auto-publication history are stored in
`/var/lib/blue-mtproxy/telegram-bot.json` as `0600` owned by the
unprivileged panel user, outside the Git checkout. The token is not
exposed on the admin page, sent to the bot chat, or included in debug
reports. Rotate a leaked bot token in BotFather before reconfiguring it.

---


## Visible Add User controls (UI 0.4.0)

The dashboard and the inbound list provide a **+ افزودن کاربر** shortcut.
The **/users** page now has a real form at the top, with a user label
and an inbound selector. Submitting it creates an independent random
32-hex user Secret via the existing authenticated CSRF-protected
management reconciler, then redirects to that inbound's detail view
so the dedicated WEB link can be copied. The global Users table also
has an immediate Enable/Disable control. The inbound detail page has
its own Add User button and user edit cards.

If a running VPS still shows the older Users list without an Add
button, the source was updated in GitHub but the Gunicorn web process
was not reloaded. For a **web-panel-only refresh** that does not
reapply the proxy configuration or touch active connections:

```bash
sudo git -C /opt/blue-mtproxy pull --ff-only origin main && sudo systemctl restart blue-mtproxy-panel.service
```

Then open `https://<your-panel-domain>/users` while signed in.
The authenticated `/version` route returns `{"ui_version":"0.4.0"}`
alongside installed feature names. A logged-out client is redirected
to the sign-in page; no version details or private Secret are exposed
by an unauthenticated request.

## 2026 Inbound-centric control center (Xray-inspired interface)

The new authenticated Persian/RTL panel includes distinct pages:

- `/` — responsive dashboard, inbound counts, managed-user counts, live
  **aggregate** active connection counters where the official backend stats
  endpoint responds, read-only service health, and actual **global WEB traffic** since the relay process started.
- `/inbounds` — all inbound profiles, local backend ports, sponsor status
  and live connection counters. Each inbound remains an independent official
  MTProxy process and Ad Tag.
- `/inbounds/<id>` — an inbound's users, create/rename/enable/disable,
  generate new user Secret, revoke a user, WEB connection links,
  and all inbound settings and sponsor credentials.
- `/users` — cross-inbound user register.
- `/settings` — Cloudflare CDN-only state, hostnames and GitHub updates.
- `/debug` — redacted diagnostics for system and inbound readiness.

**Actual user access control:** The official MTProxy binary supports
repeated `-S <secret>` arguments on the **same** backend. The WEB relay
also accepts an independent profile/capability for each enabled user
secret, pointing to that inbound's fixed backend port. Removing/disabling
a user removes the Secret from the WEB relay and MTProxy at reconcile time,
so future connections using that Secret stop working; active sessions
can also be interrupted by the restart. Existing inbound/owner Secret
remains for backward compatibility (and must not be shared as a user's
dedicated credential). User accounts survive GitHub updates because
they live inside the existing version-1 state file with an **optional**
`clients` array; old installations without clients migrate lazily.

**Critical capacity bound:** The upstream WEB relay default is
`max_profiles: 32`. The controller rejects operations when the sum of
enabled inbounds (owner profiles) and enabled users would exceed **32**
WEB profiles. This is a strict validation rule, not a user quota.

**What these metrics mean:**

| Feature | Current implementation |
| --- | --- |
| User labels, isolated Secret and WEB link | Real and managed |
| Enable, disable, revoke or rotate individual user | Enforced through MTProxy -S and relay profile reconciliation |
| Inbound backend active connections | Read-only from official `127.0.0.1:8888+slot/stats` `total_special_connections` when available |
| Per-user active connections / online | **Not available** in stock MTProxy/WEB relay |
| Total WEB relay bytes | Actual `tproxy_bytes_up_total` + `tproxy_bytes_down_total` from upstream loopback `/metrics`, **all inbounds combined and reset when relay restarts** |
| Accurate bytes by specific inbound or user | **Not available** in stock backend, displayed as — rather than invented data |
| Per-user traffic quota / expiry enforcement | **Not implemented**, do not bill clients or promise caps based on this panel |
| WEB client compatibility | Still experimental; requires a supported custom Telegram client |
| CDN-only | Preserved. Only HTTPS/WSS public 443, internal MTProxy ports remain blocked externally |

For real per-user throughput accounting and hard quotas, the next
backend phase requires a **per-user metered relay or a dedicated backend
listener per user** and a persistent traffic ledger with restart-safe
counter deltas. Do not infer byte usage from buffer-allocation counters
or from inbound totals and divide it across users.

### Applying the UI and user-management upgrade to an existing VPS

The existing state file and credentials are preserved. Do **not** run the
first-time installer on an active server. Pull code and reconfigure the
control service in one command:

```bash
sudo git -C /opt/blue-mtproxy pull --ff-only origin main && sudo bash /opt/blue-mtproxy/vps/install-control.sh
```

The installer performs a real apply with the previously configured
inbound profiles before restarting the web panel, so failure cannot be
silently reported as success. It does not issue replacement Secrets or
change the public Cloudflare DNS settings. For a no-impact UI-only preview,
run the new templates in a local test environment rather than replacing
the production service.



> **Experimental** WEB relay, official MTProto backend. Supported on clean x86_64 Debian 12+ / Ubuntu 22.04+. The upstream installer takes ownership of Caddy's public ports 80/443. Do not run on a busy VPS hosting other websites.

## Architecture

WEB-capable Telegram client -> HTTPS 443 -> Caddy -> Telegram Desktop tproxy-server -> official MTProxy backend -> Telegram

Standard Telegram client -> TCP 10443 + profile slot -> official MTProxy -> Telegram

Admin -> HTTPS panel -> Caddy -> isolated Flask (NoNewPrivileges=yes) -> group-only UNIX control socket -> root-only validator/reconciler

## Prerequisites

- A VPS with SSH/root, public IPv4, free ports TCP 80/443, enough resources to compile MTProxy and a Go relay.
- Two **DNS-only** A records pointing directly to your VPS: `proxy.example.com` and `panel.example.com`.
- Open TCP 80, 443 and **10443-10474** in the provider firewall; if UFW is active, allow ports used by enabled profiles. Preserve SSH access.
- Do **not** enable Cloudflare orange-cloud CDN on these proxy domains. It does not forward arbitrary MTProto TCP connections and the initial official WEB relay rollout is intended for direct-to-VPS connections.

## Cloudflare CDN-only WEB mode (recommended when only CDN ports are allowed)

**Public path:** WEB-capable Telegram client → Cloudflare **Proxied** (orange-cloud)
DNS over HTTPS/WebSocket **TCP 443** → Caddy → localhost WEB relay →
local official MTProxy backend (TCP 10443 + slot) → Telegram.

**The MTProxy backend port does not need to be a Cloudflare-supported
port**, because it is a local connection on the VPS. It MUST NOT be
exposed to the internet in CDN-only mode. Changing a standard MTProto
link to TCP 443 does not make it pass through Cloudflare's HTTP proxy.
Regular Telegram MTProto clients are not compatible with this WEB/CDN
transport; the upstream WEB reference is experimental and needs a
specially WEB-capable client.

On the existing VPS, first verify Caddy and the WEB relay are working.
Then run this command, which installs a **persistent nftables drop rule
for public TCP 10443–10474** while allowing loopback (the relay backend),
leaving TCP 443/80 and SSH untouched:

```bash
sudo git -C /opt/blue-mtproxy pull --ff-only origin main && sudo bash /opt/blue-mtproxy/vps/cdn-only.sh enable
```

This does **not** modify the Cloudflare account or its DNS and does not
reissue TLS certificates, replace secrets, or change sponsor Ad Tags.
It verifies the existing trusted origin SSL certificates, creates an
isolated nftables table, and persists it through systemd (including
nftables/tproxy-firewall restarts). The panel then hides the public
direct MTProto link and labels the WEB transport accurately.

In your Cloudflare dashboard (outside the VPS):

1. DNS → `md.blluepanel.ir` (or your actual configured WEB hostname):
   turn **Proxy status → Proxied (orange cloud)**. Ensure the A record
   targets the existing origin VPS and avoid an unconfigured AAAA record.
2. SSL/TLS → set **Full (strict)**. Caddy already holds a CA-valid origin
   certificate; do not select Flexible TLS.
3. Network → **WebSockets: On**. WAF/challenge rules must not interrupt
   the proxy carrier and authenticated bridge, but do not disable all WAF.
4. Verify HTTPS through Cloudflare from a client network and test a
   compatible Telegram WEB client. Ordinary Telegram clients may still
   show WEB as unavailable regardless of server health.

The origin still accepts HTTPS connections on 443 directly if its IP is
known. Strict origin access limited exclusively to Cloudflare source IPs
would need an additional, separately audited allow-list (with SSH,
certificate issuance, trusted probe and ingress behavior accounted for).
The CDN-only rule here specifically prevents **public MTProxy TCP
bypass**, not every direct origin HTTPS request.

For verification:

```bash
sudo bash /opt/blue-mtproxy/vps/cdn-only.sh status
sudo nft list table inet blue_proxy_cdn_only
sudo systemctl start blue-mtproxy-debug.service
```

To deliberately restore direct standard MTProto access, run
`sudo bash /opt/blue-mtproxy/vps/cdn-only.sh disable` and change
the DNS-only/direct routing and provider firewall settings as needed.
Disabling exposes direct ports if upstream or provider firewall permits;
it is **not** a Telegram WEB compatibility fix.

**Cloudflare port facts:** Ordinary Cloudflare HTTP proxy supports
HTTPS 443, 2053, 2083, 2087, 2096 and 8443. We use only 443 to avoid
nonstandard ports and client/WebView incompatibility. Unsupported raw
TCP requires a separate product such as Cloudflare Spectrum (plan and
policy limitations apply).

References:

- https://developers.cloudflare.com/fundamentals/reference/network-ports/
- https://developers.cloudflare.com/network/websockets/
- https://developers.cloudflare.com/dns/proxy-status/use-cases/
- https://developers.cloudflare.com/ssl/origin-configuration/ssl-modes/
- https://github.com/telegramdesktop/tproxy-server

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

The upstream WEB relay requires the `/etc/tproxy-server/profiles.json`
**source** file to have zero group/other permission bits. This project
enforces `0400 root:root`. The `tproxy` service receives the secret
file through systemd `LoadCredential`, so granting the tproxy Unix group
read access to the source is both unnecessary and rejected by the
relay's direct `-check` configuration validation.

On an existing installed VPS, do **not** rerun the initial bootstrap or rotate
secrets. Fetch the fix and run a safe reconciliation:

```bash
sudo git -C /opt/blue-mtproxy pull --ff-only origin main && sudo bash /opt/blue-mtproxy/vps/repair-web.sh
```

This preserves SSL, panel credentials and secrets. It makes the relay profile
**root-only** (0400), reapplies valid managed profiles, restarts the relay,
and performs read-only health checks. It does not disclose the client secret
or derived bridge capability.

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

## Repair MTProxy backend startup when /debug reports TCP and readiness failures

If `/debug` shows `service_caddy=ok`, `service_tproxy_server=ok`,
`web_bridge=ok` and `relay_config=ok`, but for the main profile reports
`mtproxy_service=fail`, `mtproxy_tcp=fail`, and `relay_ready=fail`,
the WEB front door is healthy while its MTProxy destination is unavailable.

**Root-cause bug fixed in the installer:** it previously made
`/etc/blue-mtproxy` mode `0750` owned by `root:bluepanel`. However,
the per-secret service runs as Unix user `mtproxy`. That user cannot traverse
the parent path, even though `runtime/` and `runtime/main.json` have
the correct `root:mtproxy` ownership. The backend process consequently
fails to load its own profile. The installer and root reconciler now set
the parent to `0711` (traversal only, no public directory listing), keep
`runtime/` at `0750 root:mtproxy`, and profile JSON files at
`0640 root:mtproxy`. The panel's `panel.env` stays `0640
root:bluepanel`, and all secrets remain unchanged.

**Safe repair for an existing VPS (do not reinstall):**

```bash
sudo git -C /opt/blue-mtproxy pull --ff-only origin main && sudo bash /opt/blue-mtproxy/vps/repair-backend.sh
```

The repair validates that existing paths are genuine directories and have
the expected owner, fixes permissions, verifies that the actual `mtproxy`
service user can read each active profile, restarts the affected backend
process(es), and probes the local MTProto TCP listener. It does not alter
TLS certificates, secret values, sponsor Ad Tags or panel credentials.

On success, `/debug` should show `mtproxy_service=ok`,
`mtproxy_tcp=ok` and `relay_ready=ok`. The report is produced on a timer,
so allow for the next cache refresh before checking.

If the backend still fails, inspect only redacted failure details (never send
the full secret-containing `systemctl show -p ExecStart`):

```bash
sudo systemctl show blue-mtproxy@main.service -p Result -p ExecMainCode -p ExecMainStatus --no-pager
sudo journalctl -u blue-mtproxy@main.service -n 60 --no-pager
```

Redact any client Secrets or server credentials before sharing logs.
A healthy local backend does not prove that the *external* TCP port is
reachable from the phone's network or that the experimental WEB transport
is supported by the phone's Telegram client.

## Repair "sudo: The no new privileges flag is set" in the admin panel

On older installations, the root-only backend and `/debug` may be fully
healthy while clicking **Save**, **Create Proxy**, **Rotate Secret**, **Delete**,
**Auto Update toggle**, or **Check GitHub Updates** returns:

```text
sudo: The "no new privileges" flag is set
```

The web service intentionally runs with `NoNewPrivileges=yes`, so a direct
`sudo` from Flask can never succeed. **Do not turn this sandbox off.**
The control-plane now sends only exact, authorized commands across a UNIX socket
(`/run/blue-mtproxy-control/command.sock`, root:bluepanel mode 0660) to a
dedicated root-owned systemd service. Peer credentials are verified with
Linux `SO_PEERCRED`; only the `bluepanel` UID and root can use it.
Only `ping`, `apply`, and `update` are accepted, with no arbitrary
shell, file, or command arguments. The existing strict state validator and
root reconciler continue to protect the backend.

**Apply the fix on the existing VPS without reinstalling or rotating Secrets:**

```bash
sudo git -C /opt/blue-mtproxy pull --ff-only origin main && sudo bash /opt/blue-mtproxy/vps/install-control.sh
```

The installer creates and enables `blue-mtproxy-control.service` and
`blue-mtproxy-apply.service`, checks both a `ping` and a complete real
apply using the `bluepanel` UID, removes the ineffective web sudoers
allowance, and restarts **only the panel service** when the apply succeeds.
It does not regenerate certificates, SSH accounts, client links, sponsor tags,
or existing proxy instances. The GitHub auto-updater also maintains the
control broker after future code changes.

**Functional verification:** With a logged-in admin session, change only the
label of the existing profile and press **Save**; the change should persist
without the sudo error. Then test creating a second profile and saving its
optional channel reference. Do not rotate an existing Secret merely to test
the panel, as rotation intentionally invalidates old links.

For read-only service diagnostics:

```bash
sudo systemctl is-active blue-mtproxy-control.service
sudo journalctl -u blue-mtproxy-control.service -n 30 --no-pager
sudo -u bluepanel test -w /run/blue-mtproxy-control/command.sock && echo socket-permissions-ok
sudo systemctl start blue-mtproxy-debug.service
```

The control broker has a fixed action vocabulary and returns generic
errors; its journal contains error categories without client Secrets. A
successful server `/debug` still does not prove the user's Telegram WEB
client supports the experimental protocol. For broad compatibility, use
the standard `t.me/proxy` MTProto link and independently verify external TCP
port `10443 + slot`.

## Repair `runuser mtproxy test -r runtime/main.json` in the apply service

If the `blue-mtproxy-control` socket responds to `ping` but an end-to-end
apply stops with:

```text
Unable to reconcile proxy services:
Command ['runuser', '-u', 'mtproxy', '--', 'test', '-r',
'/etc/blue-mtproxy/runtime/main.json'] returned non-zero exit status 1
```

the failure is a **runtime profile readability probe within the restricted
systemd apply process**, not a network, certificate, or broker failure.
This specific check behaved differently from the previous successful
`repair-backend.sh` access test executed directly on the host.
Do not disable `NoNewPrivileges` or weaken the admin panel.

The reconciler now validates service files via OS metadata. **Two different
permission policies apply**: runtime backend files are `0640 root:mtproxy`
so the per-profile MTProxy service can read them; the WEB Relay source
`/etc/tproxy-server/profiles.json` is **0400 root:root**, as required
by the upstream relay and systemd `LoadCredential`. Both reject symlinks
and unexpected owners. Unlike calling `runuser` through PAM inside
the sandboxed oneshot, metadata checks are deterministic; service/socket
health is independently monitored in `/debug`.

To apply without reinstalling or altering client secrets:

```bash
sudo git -C /opt/blue-mtproxy pull --ff-only origin main && sudo bash /opt/blue-mtproxy/vps/install-control.sh
```

A passing run prints `Verified end-to-end apply through the dedicated systemd
reconciler`. If the apply still fails, **do not guess or reinstall**:
retrieve its actual systemd error before further changes:

```bash
sudo systemctl show blue-mtproxy-apply.service -p Result -p ExecMainCode -p ExecMainStatus --no-pager
sudo journalctl -u blue-mtproxy-apply.service -n 40 --no-pager
```

The real Telegram WEB/MTProto connection from a remote phone remains a
separate acceptance test. Changing the profile label is a safe panel
functional test; rotating the secret is not.

## Repair strict WEB relay profile permissions error

If `blue-mtproxy-apply.service` shows:

```text
configuration error: profiles_file must not be readable or writable by
any group or other
```

the source file `/etc/tproxy-server/profiles.json` has inappropriate
group/other read bits. The reference Go relay explicitly rejects
`os.Stat(path).Mode().Perm() & 0077 != 0` for a regular file read by
the command-line `-check` operation. The official unit securely obtains
the root-only file via `LoadCredential`.

Earlier control-plane code incorrectly shared the MTProxy backend
runtime-file permission rule (`0640 root:mtproxy`) with this sensitive
WEB relay source (`0640 root:tproxy`), and its rollback reproduced
the same error. The corrected code enforces `0400 root:root` both
when applying new profiles and when restoring previous data. The
separate `repair-web.sh` helper also keeps the strict upstream mode.

**Repair a previously installed VPS without reinstalling:**

```bash
sudo git -C /opt/blue-mtproxy pull --ff-only origin main && sudo bash /opt/blue-mtproxy/vps/install-control.sh
```

The installer checks `ping` as the actual `bluepanel` user then runs a
full `apply` using the isolated systemd oneshot. The reconciler normalizes
file permissions, validates the Go relay configuration, and applies only
necessary changes. No client Secret, sponsor tag, TLS credential, SSH setting,
or admin password is regenerated.

Do **not** use `chmod 644` or `chmod 640` on
`/etc/tproxy-server/profiles.json`, and do not disable
`NoNewPrivileges` to address this error.

## Repair `operation_failed` after the control socket becomes healthy

If `/debug` shows all of Caddy, MTProxy, WEB relay and
`service_blue_mtproxy_control` as `ok` but saving an existing label shows
"اعمال تنظیمات در سرویس مدیریت ناموفق بود", the panel has authenticated to
the socket successfully but the **privileged apply action** failed.

The first broker version attempted to execute `reconcile()` *inside its own
heavily sandboxed systemd service*. This can fail on system operations required
by the reconciler. Rather than widening the socket broker's filesystem and
capabilities, the updated broker delegates only that fixed action to the
separate root-owned `blue-mtproxy-apply.service` with
`Type=oneshot`. systemd waits for the action to complete and propagates
failure. The original validator remains in effect and the socket broker's
write access is restricted to its own socket directory.

On the **existing VPS**:

```bash
sudo git -C /opt/blue-mtproxy pull --ff-only origin main && sudo bash /opt/blue-mtproxy/vps/install-control.sh
```

This installer now checks both `ping` and **a complete, real apply on the
existing desired state** from the unprivileged `bluepanel` account before
claiming success. It does not issue new Secrets, reissue SSL certificates,
overwrite the state, or reinstall the backend. An existing state may be
reconciled and an unhealthy proxy service restarted.

If the end-to-end apply fails, the installer stops with the correct journal
hint. To see the precise stage without sharing secrets:

```bash
sudo systemctl show blue-mtproxy-apply.service -p Result -p ExecMainCode -p ExecMainStatus --no-pager
sudo journalctl -u blue-mtproxy-apply.service -n 45 --no-pager
```

Do not share full command lines, `state.json`, `panel.env`, or full
credentials files. The panel's `/debug` now includes a `control_apply`
row indicating whether the last systemd apply job succeeded or failed.

For a functional UI test, **change only the label** of the existing profile
and press Save. Verify that the label persists after refresh. Avoid rotating
Secret merely as a test, because that invalidates old links.

## Administrator /debug — safe root-cause diagnostics

**Cause of initial `helper: fail`:** `blue-mtproxy-panel.service` uses
`NoNewPrivileges=yes`, which prevents setuid `sudo` escalation even with a
permissive `sudoers` rule. The initial diagnostic helper tried to invoke sudo
from Flask and therefore could not generate a report. The sandbox was behaving
as designed; disabling `NoNewPrivileges` would weaken isolation.

The corrected design uses a **root-owned one-shot systemd service and timer**.
The service performs bounded read-only tests and publishes only redacted JSON
to `/var/cache/blue-mtproxy-debug/report.json` owned by root with group
`bluepanel` (mode 0640; parent mode 0750). Flask does not use sudo or
execute a root command. The snapshot refreshes about every 90 seconds.
An administrator can refresh it manually via
`sudo systemctl start blue-mtproxy-debug.service`.

On an existing VPS, repair `/debug` **without reinstalling**:

```bash
sudo git -C /opt/blue-mtproxy pull --ff-only origin main && sudo bash /opt/blue-mtproxy/vps/install-debug.sh
```

Sign in to the panel at `https://YOUR-PANEL-DOMAIN/debug`, or read the
redacted JSON via `/debug.json`. Both endpoints require a logged-in
admin session and return `Cache-Control: no-store`. They never expose raw
credentials, Secret, Ad Tag, bridge HMAC, or unredacted log lines.

Checks include Caddy and relay systemd activity, loopback readiness,
per-profile MTProxy process and local TCP port, exact panel/relay profile
agreement, and an authenticated WEB bridge request over publicly trusted TLS.
It also shows NAT Middle-End warnings and warns that actual client WEB
compatibility and external TCP accessibility **remain unverified**.

The bridge probe budget is limited to six profiles per refresh. The web
reader rejects stale reports older than five minutes and distinguishes
`cache_missing`, `cache_stale`, `cache_permission`, and `cache_invalid`
rather than returning a generic `helper` error.

**If the snapshot still fails**, run these read-only service diagnostics:

```bash
sudo systemctl --no-pager --full status blue-mtproxy-debug.service
sudo journalctl -u blue-mtproxy-debug.service -n 45 --no-pager
sudo -u bluepanel test -r /var/cache/blue-mtproxy-debug/report.json && echo "panel can read report"
```

For immediate **root-only** debugging without relying on the panel, run
`sudo python3 /opt/blue-mtproxy/vps/manager/debug.py` directly. This
returns a redacted JSON snapshot.

If all the server checks pass but your phone still marks the WEB proxy as
unavailable, test the **standard MTProto** connection link from the panel
(`t.me/proxy`), which requires external TCP port `10443 + slot`. The
reference WEB protocol is a proof of concept with limited client support;
its server tests cannot prove real Telegram-client compatibility or successful
sponsored-channel display.

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
- The panel runs as `bluepanel` with NoNewPrivileges enabled. A root-owned UNIX socket service authorizes the bluepanel UID for only a fixed set of actions, validates all state, and delegates applying changes to the root reconciler. Session cookies are HTTP-only, Secure, SameSite Strict, with CSRF tokens, strict CSP and scrypt password hashing.
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
