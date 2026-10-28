#!/usr/bin/env bash
# Safe, idempotent recovery for a running Blue Proxy VPS.
# Keep NoNewPrivileges=yes in the web server. Run diagnostics as a separate
# root-owned systemd service and expose ONLY a cached, redacted report.
set -Eeuo pipefail
[[ "$(id -u)" == "0" ]] || { echo "Root access required" >&2; exit 1; }
root=/opt/blue-mtproxy
[[ -f "$root/vps/manager/write_debug_cache.py" ]] || {
  echo "Pull the current GitHub main branch first." >&2
  exit 1
}
id bluepanel >/dev/null
install -d -o root -g bluepanel -m 0750 /var/cache/blue-mtproxy-debug

cat > /etc/systemd/system/blue-mtproxy-debug.service <<'UNIT'
[Unit]
Description=Create redacted Blue Proxy Manager diagnostic snapshot
After=network-online.target tproxy-server.service
Wants=network-online.target
[Service]
Type=oneshot
User=root
Group=root
ExecStart=/usr/bin/python3 /opt/blue-mtproxy/vps/manager/write_debug_cache.py
TimeoutStartSec=65s
NoNewPrivileges=yes
PrivateTmp=yes
PrivateDevices=yes
ProtectHome=yes
ProtectSystem=strict
ReadWritePaths=/var/cache/blue-mtproxy-debug
RestrictAddressFamilies=AF_INET AF_INET6 AF_UNIX
CapabilityBoundingSet=CAP_DAC_OVERRIDE CAP_CHOWN
UMask=0077
UNIT

cat > /etc/systemd/system/blue-mtproxy-debug.timer <<'UNIT'
[Unit]
Description=Refresh Blue Proxy Manager diagnostic snapshot
[Timer]
OnBootSec=15s
OnUnitInactiveSec=90s
RandomizedDelaySec=5s
Persistent=true
Unit=blue-mtproxy-debug.service
[Install]
WantedBy=timers.target
UNIT

# Remove the previous ineffective web-sudo bridge. NoNewPrivileges=yes
# prevents sudo elevation and must remain enabled for the Flask service.
rm -f /etc/sudoers.d/blue-mtproxy-debug
systemctl daemon-reload
systemctl enable --now blue-mtproxy-debug.timer
systemctl start blue-mtproxy-debug.service
runuser -u bluepanel -- test -r /var/cache/blue-mtproxy-debug/report.json || {
  echo "Diagnostic snapshot exists but cannot be read by the panel user." >&2
  exit 1
}
echo "[ok] Root-only diagnostic service generated a read-only report for bluepanel."
echo "[ok] The diagnostic timer refreshes automatically without sudo from Flask."
if [[ "${BLUE_DEBUG_NO_RESTART:-0}" != "1" ]]; then
  systemctl restart blue-mtproxy-panel.service
  echo "[ok] Admin panel reloaded. Open /debug in your authenticated browser."
fi
