#!/usr/bin/env bash
# One-shot installer for a clean Debian 12+ / Ubuntu 22.04+ x86_64 VPS.
set -Eeuo pipefail
umask 077

DOMAIN="" PANEL="" EMAIL="" SKIP_DNS=0
while [[ "$#" -gt 0 ]]; do
  case "$1" in
    --domain) DOMAIN="${2:?}"; shift 2 ;;
    --panel-domain) PANEL="${2:?}"; shift 2 ;;
    --email) EMAIL="${2:?}"; shift 2 ;;
    --skip-dns-check) SKIP_DNS=1; shift ;;
    *) echo "Unknown argument: $1" >&2; exit 2 ;;
  esac
done
if [[ "$(id -u)" != 0 ]]; then echo "Run with sudo" >&2; exit 1; fi
if [[ "$(uname -m)" != "x86_64" ]]; then echo "x86_64 VPS required" >&2; exit 1; fi
case "$(. /etc/os-release; echo "$ID")" in ubuntu|debian) ;; *) echo "Debian/Ubuntu required" >&2; exit 1;; esac
# Interactive inputs use /dev/tty because curl | sudo bash occupies stdin.
if [[ -z "$DOMAIN" || -z "$PANEL" || -z "$EMAIL" ]]; then
  [[ -r /dev/tty ]] || {
    echo "Interactive terminal required, or provide --domain --panel-domain --email." >&2
    exit 2
  }
fi
if [[ -z "$DOMAIN" ]]; then
  read -r -p "Proxy hostname (e.g. proxy.example.com): " DOMAIN < /dev/tty
fi
if [[ -z "$PANEL" ]]; then
  # Keep the registered domain intact for two-label hosts (example.com).
  # For normal subdomains, suggest panel on the same parent zone.
  if [[ "$DOMAIN" == *.*.* ]]; then
    DEFAULT_PANEL="panel.${DOMAIN#*.}"
  else
    DEFAULT_PANEL="panel.${DOMAIN}"
  fi
  read -r -p "Admin panel hostname [$DEFAULT_PANEL]: " PANEL < /dev/tty
  PANEL="${PANEL:-$DEFAULT_PANEL}"
fi
if [[ -z "$EMAIL" ]]; then
  read -r -p "Email address for SSL certificate issuer: " EMAIL < /dev/tty
fi
re='^[a-z0-9]([a-z0-9.-]*[a-z0-9])?\.[a-z0-9.-]+$'
[[ "$DOMAIN" =~ $re && "$PANEL" =~ $re && "$DOMAIN" != "$PANEL" ]] || { echo "Specify distinct, lowercase DNS hostnames." >&2; exit 2; }
[[ "$EMAIL" =~ ^[^[:space:]@]+@[^[:space:]@]+\.[^[:space:]@]+$ ]] || { echo "Invalid email" >&2; exit 2; }
[[ ! -e /etc/blue-mtproxy/panel.env ]] || { echo "Existing installation detected. Use the updater." >&2; exit 1; }
[[ -d /opt/blue-mtproxy/.git ]] || { echo "Clone repo to /opt/blue-mtproxy first" >&2; exit 1; }

echo "Installing pinned official Telegram WEB reference server..."
apt-get update
DEBIAN_FRONTEND=noninteractive apt-get install -y ca-certificates git curl python3 python3-venv python3-pip sudo nftables iproute2
# Preflight runs before upstream installation, Caddy edits, and systemd changes.
PREFLIGHT_ARGS=(--domain "$DOMAIN" --panel-domain "$PANEL")
if [[ "$SKIP_DNS" == 1 ]]; then PREFLIGHT_ARGS+=(--skip-dns-check); fi
python3 /opt/blue-mtproxy/vps/preflight.py "${PREFLIGHT_ARGS[@]}"
if command -v ufw >/dev/null && ufw status 2>/dev/null | grep -q '^Status: active'; then
  ufw allow 80/tcp
  ufw allow 443/tcp
  ufw allow 10443:10474/tcp
fi
# Prefer Ubuntu/Debian Go packages and avoid go.dev redirect errors (HTTP 404).
# Sourcing preserves PATH if a checksum-verified alternate toolchain is used.
source /opt/blue-mtproxy/vps/ensure-go.sh
UPSTREAM=/opt/telegram-tproxy-server
PIN=c8adb8b7c6b7fc46c12ae3acb68be9070c26a8e8
if [[ ! -d "$UPSTREAM/.git" ]]; then
  git clone https://github.com/telegramdesktop/tproxy-server.git "$UPSTREAM"
fi
git -C "$UPSTREAM" fetch origin "$PIN"
git -C "$UPSTREAM" checkout --detach "$PIN"
python3 /opt/blue-mtproxy/vps/patch_upstream.py "$UPSTREAM/deploy/install-mtproxy.sh"
SECRET="$(openssl rand -hex 16)"
ADMIN_PASSWORD="$(openssl rand -base64 27 | tr -d '\n')"

# The pinned official installer prompts for the secret on stdin rather than argv.
# The proxy hostname must resolve DIRECTLY to this VPS, not via Cloudflare HTTP CDN.
printf '%s\n' "$SECRET" | bash "$UPSTREAM/deploy/install.sh" \
  --hostname "$DOMAIN" --email "$EMAIL" --base-path none --site-dir /opt/blue-mtproxy/vps/site

id mtproxy >/dev/null
id tproxy >/dev/null
id bluepanel >/dev/null 2>&1 || useradd --system --create-home \
  --home-dir /var/lib/blue-mtproxy --shell /usr/sbin/nologin bluepanel
# mtproxy needs to traverse this parent to read runtime/<id>.json.
# 0711 allows path traversal, NOT directory listing or reading panel.env.
install -d -m 0711 -o root -g bluepanel /etc/blue-mtproxy
install -d -m 0750 -o root -g mtproxy /etc/blue-mtproxy/runtime
install -d -m 0700 -o bluepanel -g bluepanel /var/lib/blue-mtproxy

python3 -m venv /opt/blue-mtproxy/.venv
/opt/blue-mtproxy/.venv/bin/pip install --disable-pip-version-check -r /opt/blue-mtproxy/vps/requirements.txt
# The installer uses umask 077; bluepanel cannot run Gunicorn until the
# root-owned venv is readable/executable by the service account.
bash /opt/blue-mtproxy/vps/panel-permissions.sh
HASH="$(PANEL_PASSWORD="$ADMIN_PASSWORD" /opt/blue-mtproxy/.venv/bin/python -c 'import os; from werkzeug.security import generate_password_hash; print(generate_password_hash(os.environ["PANEL_PASSWORD"],method="scrypt"))')"
PANEL_KEY="$(openssl rand -hex 32)"
printf 'PANEL_ADMIN_PASSWORD_HASH="%s"\nPANEL_SECRET_KEY=%s\n' "$HASH" "$PANEL_KEY" \
  > /etc/blue-mtproxy/panel.env
chown root:bluepanel /etc/blue-mtproxy/panel.env
chmod 0640 /etc/blue-mtproxy/panel.env

DOMAIN="$DOMAIN" PANEL="$PANEL" SECRET="$SECRET" python3 - <<'PY'
import json, os
from pathlib import Path
state = {
  "version": 1,
  "hostname": os.environ["DOMAIN"],
  "panel_hostname": os.environ["PANEL"],
  "auto_update": True,
  "profiles": [{
    "id": "main", "slot": 0, "label": "Main Proxy",
    "secret": os.environ["SECRET"], "ad_tag": "",
    "channel": "", "enabled": True, "carrier": "websocket"
  }]
}
path = Path("/var/lib/blue-mtproxy/state.json")
path.write_text(json.dumps(state, indent=2) + "\n")
path.chmod(0o600)
PY
chown bluepanel:bluepanel /var/lib/blue-mtproxy/state.json

cat > /etc/systemd/system/blue-mtproxy@.service <<'UNIT'
[Unit]
Description=Blue MTProxy backend %i
After=network-online.target tproxy-firewall.service
Wants=network-online.target
Requires=tproxy-firewall.service
[Service]
Type=simple
User=mtproxy
Group=mtproxy
EnvironmentFile=-/etc/mtproxy/mtproxy.env
ExecStart=/usr/bin/python3 /opt/blue-mtproxy/vps/manager/runner.py %i
Restart=on-failure
RestartSec=3
LimitNOFILE=1048576
NoNewPrivileges=yes
PrivateTmp=yes
PrivateDevices=yes
ProtectHome=yes
ProtectSystem=strict
ProtectProc=invisible
RestrictAddressFamilies=AF_INET AF_INET6
RestrictNamespaces=yes
RestrictRealtime=yes
CapabilityBoundingSet=
[Install]
WantedBy=multi-user.target
UNIT

cat > /etc/systemd/system/blue-mtproxy-panel.service <<'UNIT'
[Unit]
Description=Blue Proxy Manager admin panel
After=network-online.target tproxy-server.service
Wants=network-online.target
[Service]
Type=simple
User=bluepanel
Group=bluepanel
WorkingDirectory=/opt/blue-mtproxy/vps
EnvironmentFile=/etc/blue-mtproxy/panel.env
ExecStart=/opt/blue-mtproxy/.venv/bin/gunicorn --bind 127.0.0.1:8750 --workers 1 --threads 4 --timeout 45 app:app
Restart=on-failure
RestartSec=3
NoNewPrivileges=yes
PrivateTmp=yes
ProtectHome=yes
ProtectSystem=strict
ReadWritePaths=/var/lib/blue-mtproxy
RestrictAddressFamilies=AF_INET AF_INET6 AF_UNIX
UMask=0077
[Install]
WantedBy=multi-user.target
UNIT

# Firewall MTProxy's publicly binding backend sockets; only :80/:443 are public.
cat > /etc/tproxy-server/firewall.nft <<'NFT'
table inet tproxy_backend {
  chain local_backend {
    type filter hook input priority -10; policy accept;
    iifname != "lo" tcp dport 2398-2429 drop
    iifname != "lo" tcp dport 8888-8919 drop
  }
}
NFT

cat > /usr/local/sbin/blue-mtproxy-apply <<'SH'
#!/bin/sh
exec /usr/bin/python3 /opt/blue-mtproxy/vps/manager/apply.py
SH
cat > /usr/local/sbin/blue-mtproxy-update <<'SH'
#!/bin/sh
exec /bin/bash /opt/blue-mtproxy/vps/update.sh "$@"
SH
chmod 0755 /usr/local/sbin/blue-mtproxy-apply /usr/local/sbin/blue-mtproxy-update

cat > /etc/sudoers.d/blue-mtproxy <<'SUDO'
bluepanel ALL=(root) NOPASSWD: /usr/local/sbin/blue-mtproxy-apply, /usr/bin/systemctl start --no-block blue-mtproxy-update-force.service
SUDO
chmod 0440 /etc/sudoers.d/blue-mtproxy
visudo -cf /etc/sudoers.d/blue-mtproxy

# Keep the official proxy domain routing untouched; the panel gets its own HTTPS vhost.
if ! grep -Fq "$PANEL {" /etc/caddy/Caddyfile; then
  cp -a /etc/caddy/Caddyfile "/etc/caddy/Caddyfile.before-blue-$(date +%Y%m%d%H%M%S)"
  cat >> /etc/caddy/Caddyfile <<EOF

$PANEL {
  encode gzip
  header {
    Strict-Transport-Security "max-age=31536000"
    X-Content-Type-Options "nosniff"
  }
  reverse_proxy 127.0.0.1:8750
}
EOF
fi

cat > /etc/systemd/system/blue-mtproxy-update.service <<'UNIT'
[Unit]
Description=Blue Proxy Manager verified GitHub update
After=network-online.target
Wants=network-online.target
[Service]
Type=oneshot
ExecStart=/usr/local/sbin/blue-mtproxy-update
TimeoutStartSec=240
UNIT

cat > /etc/systemd/system/blue-mtproxy-update.timer <<'UNIT'
[Unit]
Description=Check Blue Proxy Manager GitHub updates periodically
[Timer]
OnBootSec=15min
OnUnitActiveSec=6h
RandomizedDelaySec=10min
Persistent=true
Unit=blue-mtproxy-update.service
[Install]
WantedBy=timers.target
UNIT

cat > /etc/systemd/system/blue-mtproxy-update-force.service <<'UNIT'
[Unit]
Description=Blue Proxy Manager manual GitHub update
After=network-online.target
Wants=network-online.target
[Service]
Type=oneshot
ExecStart=/usr/local/sbin/blue-mtproxy-update --force
TimeoutStartSec=240
UNIT

systemctl daemon-reload
systemctl restart tproxy-firewall.service
/usr/local/sbin/blue-mtproxy-apply
systemctl enable --now blue-mtproxy-panel.service
BLUE_DEBUG_NO_RESTART=1 bash /opt/blue-mtproxy/vps/install-debug.sh
systemctl enable --now blue-mtproxy-update.timer
TPROXY_HOSTNAME="$DOMAIN" TPROXY_SITE_ROOT=/srv/tproxy-site ACME_EMAIL="$EMAIL" \
  /usr/local/bin/caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile
systemctl restart caddy.service

install -m 0600 /dev/null /root/blue-mtproxy-credentials.txt
cat > /root/blue-mtproxy-credentials.txt <<EOF
Panel URL: https://$PANEL
Username: admin
Password: $ADMIN_PASSWORD
Proxy domain: $DOMAIN
Initial WEB Secret: $SECRET
EOF
chmod 0600 /root/blue-mtproxy-credentials.txt

# Automatic ACME certificate issuance by Caddy, then independent verification.
# The --resolve argument tests SNI + publicly trusted certificates locally;
# no dependence on the provider's NAT hairpin support.
echo "Verifying automatic HTTPS certificates from Caddy..."
SSL_READY=0
for attempt in {1..24}; do
  if bash /opt/blue-mtproxy/vps/ssl-status.sh --quiet; then
    SSL_READY=1
    break
  fi
  if (( attempt % 4 == 0 )); then
    echo "Waiting for trusted SSL certificates (attempt $attempt/24)."
  fi
  sleep 5
done
if [[ "$SSL_READY" != 1 ]]; then
  echo "ERROR: Services are installed, but trusted HTTPS for both domains has not been verified." >&2
  echo "Diagnostics (does not modify the VPS):" >&2
  bash /opt/blue-mtproxy/vps/ssl-status.sh || true
  echo "Check DNS and provider firewall TCP 80/443; do NOT re-run the installer." >&2
  echo "Credentials: /root/blue-mtproxy-credentials.txt" >&2
  exit 1
fi
echo "[ok] Publicly trusted HTTPS certificates verified for both hostnames."

echo ""
echo "========================================"
echo "Blue Proxy Manager installed."
echo "Panel: https://$PANEL"
echo "Username: admin"
echo "Password: $ADMIN_PASSWORD"
echo "Credentials: /root/blue-mtproxy-credentials.txt"
echo "WEB proxy: https://t.me/webproxy?server=$DOMAIN&secret=$SECRET"
echo "MTProto proxy: https://t.me/proxy?server=$DOMAIN&port=10443&secret=$SECRET"
echo "========================================"
echo "Important: Register EACH secret with @MTProxyBot and paste its 32-hex Ad Tag into the panel."
echo "Use DNS-only A records to this VPS for BOTH hostnames, with ports TCP 80/443 and MTProto 10443-10474 open."
