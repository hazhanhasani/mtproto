#!/usr/bin/env bash
# One-time enrollment of an already installed Blue Proxy VPS.
set -Eeuo pipefail
umask 077
CONTROLLER="" NODE_ID=""
while (( $# )); do
  case "$1" in
    --controller) CONTROLLER="$2"; shift 2 ;;
    --node-id) NODE_ID="$2"; shift 2 ;;
    *) echo "Unknown flag" >&2; exit 2 ;;
  esac
done
[[ "$(id -u)" == 0 ]] || { echo "Root required" >&2; exit 1; }
[[ "$NODE_ID" =~ ^n[0-9a-f]{12}$ ]] || { echo "Invalid node ID" >&2; exit 2; }
[[ "$CONTROLLER" =~ ^https://[a-zA-Z0-9.-]+(:[0-9]+)?/?$ ]] || {
  echo "Controller must be an HTTPS origin" >&2; exit 2;
}
[[ -f /var/lib/blue-mtproxy/state.json && -x /usr/local/sbin/blue-mtproxy-apply ]] || {
  echo "Install Blue Proxy on this VPS before enrolling" >&2; exit 1;
}
[[ -f /opt/blue-mtproxy/vps/manager/node_agent.py ]] || {
  echo "Update the Blue Proxy Git checkout before enrollment" >&2; exit 1;
}
[[ -r /dev/tty ]] || { echo "A terminal is needed for one-time pairing" >&2; exit 1; }
read -r -s -p "Paste your one-time node token: " TOKEN </dev/tty
echo
[[ "$(printf %s "$TOKEN" | wc -c)" -ge 32 ]] || { echo "Invalid token" >&2; exit 2; }
EXISTING_NODE_CONFIG=0
[[ -f /etc/blue-mtproxy/node.json ]] && EXISTING_NODE_CONFIG=1
export NODE_CONTROLLER="$CONTROLLER" NODE_ID="$NODE_ID" NODE_TOKEN="$TOKEN"
# Check the actual /api/nodes/.../desired endpoint BEFORE changing local
# services. Prevent registering the central dashboard as a remote node.
cd /opt/blue-mtproxy/vps
python3 -m manager.enrollment || {
  echo "[error] Enrollment preflight rejected. No services were disabled." >&2
  exit 1
}
python3 - <<'PY'
import json, os
from pathlib import Path
s = json.loads(Path("/var/lib/blue-mtproxy/state.json").read_text())
path = Path("/etc/blue-mtproxy/node.json")
if path.exists():
    current = json.loads(path.read_text())
    if current.get("id") != os.environ["NODE_ID"]:
        raise SystemExit("This VPS is already enrolled in a different node")
    original = current["original_hostname"]
else:
    original = s["hostname"]
record = {
    "controller": os.environ["NODE_CONTROLLER"].rstrip("/"),
    "id": os.environ["NODE_ID"],
    "token": os.environ["NODE_TOKEN"],
    "original_hostname": original,
}
path.write_text(json.dumps(record, indent=2) + "\n")
path.chmod(0o600)
PY
unset TOKEN NODE_TOKEN
CADDYFILE=/etc/caddy/Caddyfile
SNIPPET=/etc/caddy/blue-mtproxy-domains.caddy
[[ -f "$CADDYFILE" ]] || { echo "Caddyfile missing" >&2; exit 1; }
[[ -f "$SNIPPET" ]] || printf '# Managed extra relay domains\n' > "$SNIPPET"
chmod 0644 "$SNIPPET"
if ! grep -Fxq 'import /etc/caddy/blue-mtproxy-domains.caddy' "$CADDYFILE"; then
  cp -p "$CADDYFILE" "$CADDYFILE.before-blue-node"
  printf '\n# Blue Proxy managed additional TLS hostnames\nimport /etc/caddy/blue-mtproxy-domains.caddy\n' >> "$CADDYFILE"
fi
ORIGINAL_HOST="$(python3 -c 'import json;print(json.load(open("/etc/blue-mtproxy/node.json"))["original_hostname"])')"
TPROXY_HOSTNAME="$ORIGINAL_HOST" TPROXY_SITE_ROOT=/srv/tproxy-site \
ACME_EMAIL="certificates@$ORIGINAL_HOST" \
 /usr/local/bin/caddy validate --config "$CADDYFILE" --adapter caddyfile

cat >/etc/systemd/system/blue-mtproxy-node.service <<'UNIT'
[Unit]
Description=Blue Proxy secure outbound node synchronizer
After=network-online.target
Wants=network-online.target
[Service]
Type=oneshot
User=root
ExecStart=/usr/bin/python3 /opt/blue-mtproxy/vps/manager/node_agent.py
TimeoutStartSec=180
NoNewPrivileges=yes
PrivateTmp=yes
PrivateDevices=yes
ProtectHome=yes
ProtectSystem=strict
ReadWritePaths=/etc/blue-mtproxy /etc/caddy /var/lib/blue-mtproxy /etc/tproxy-server /etc/systemd/system /run/lock
RestrictAddressFamilies=AF_INET AF_INET6 AF_UNIX
UMask=0077
UNIT
cat >/etc/systemd/system/blue-mtproxy-node.timer <<'UNIT'
[Unit]
Description=Poll Blue Proxy central control plane securely
[Timer]
OnBootSec=1min
OnUnitInactiveSec=1min
RandomizedDelaySec=10s
Persistent=true
Unit=blue-mtproxy-node.service
[Install]
WantedBy=timers.target
UNIT
cat >/etc/systemd/system/blue-mtproxy-relay@.service <<'UNIT'
[Unit]
Description=Blue Proxy hostname-specific WEB relay %i
After=network-online.target tproxy-firewall.service
Wants=network-online.target
Requires=tproxy-firewall.service
[Service]
Type=simple
User=tproxy
Group=tproxy
LoadCredential=profiles.json:/etc/tproxy-server/profiles.json
ExecStart=/usr/local/bin/tproxy-server -config /etc/tproxy-server/domains/%i.json
Restart=on-failure
RestartSec=3
LimitNOFILE=1048576
NoNewPrivileges=yes
PrivateDevices=yes
PrivateTmp=yes
ProtectHome=yes
ProtectSystem=strict
RestrictAddressFamilies=AF_INET AF_INET6
CapabilityBoundingSet=
IPAddressDeny=any
IPAddressAllow=localhost
UMask=0077
[Install]
WantedBy=multi-user.target
UNIT
systemctl daemon-reload
# Pair before disabling local UI; a failed agent cannot strand the server.
if ! systemctl start blue-mtproxy-node.service; then
  echo "[error] Node synchronization failed; local admin is still enabled." >&2
  echo "Check: sudo journalctl -u blue-mtproxy-node.service -n 35 --no-pager" >&2
  # Avoid the updater ever classifying a failed first-time enrollment as an
  # agent-managed remote node. Preserve the key in a root-only backup.
  if [[ "$EXISTING_NODE_CONFIG" == "0" && -f /etc/blue-mtproxy/node.json ]]; then
    mv /etc/blue-mtproxy/node.json /etc/blue-mtproxy/node-failed-pairing.json
    chmod 0600 /etc/blue-mtproxy/node-failed-pairing.json
  fi
  exit 1
fi
systemctl enable --now blue-mtproxy-node.timer
systemctl disable --now blue-mtproxy-panel.service 2>/dev/null || true
systemctl disable --now blue-mtproxy-telegram-bot.service 2>/dev/null || true
systemctl disable --now blue-mtproxy-telegram-cron.timer 2>/dev/null || true
echo "[ok] Verified successful node synchronization; remote panel retired."
echo "[info] Automatic TLS requires matching DNS and public TCP 80/443."
