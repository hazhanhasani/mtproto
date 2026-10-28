#!/usr/bin/env bash
# One-command idempotent migration from Flask sudo to a fixed-action root broker.
# This does NOT alter TLS, proxy state, MTProxy units, or client Secrets.
set -Eeuo pipefail
[[ "$(id -u)" == 0 ]] || { echo "Run as root (sudo)" >&2; exit 1; }
root=/opt/blue-mtproxy
[[ -f "$root/vps/manager/control.py" && -f "$root/vps/manager/control_client.py" ]] || {
  echo "Update the main GitHub branch before running this script." >&2
  exit 1
}
id bluepanel >/dev/null 2>&1 || { echo "bluepanel service account missing." >&2; exit 1; }
python3 -m py_compile "$root/vps/manager/control.py" "$root/vps/manager/control_client.py"
install -d -m 0755 -o root -g root /etc/systemd/system
cat > /etc/systemd/system/blue-mtproxy-control.service <<'UNIT'
[Unit]
Description=Blue Proxy Manager privileged operations broker
After=network-online.target
Wants=network-online.target
[Service]
Type=simple
User=root
Group=root
RuntimeDirectory=blue-mtproxy-control
RuntimeDirectoryMode=0755
ExecStart=/usr/bin/python3 /opt/blue-mtproxy/vps/manager/control.py
Restart=on-failure
RestartSec=2s
TimeoutStopSec=10s
NoNewPrivileges=yes
PrivateTmp=yes
PrivateDevices=yes
ProtectHome=yes
ProtectSystem=strict
ReadWritePaths=/run/blue-mtproxy-control /run/lock /etc/blue-mtproxy /etc/tproxy-server /var/lib/blue-mtproxy
RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6
CapabilityBoundingSet=CAP_CHOWN CAP_DAC_OVERRIDE CAP_FOWNER
UMask=0077
[Install]
WantedBy=multi-user.target
UNIT

systemctl daemon-reload
systemctl enable --now blue-mtproxy-control.service
systemctl restart blue-mtproxy-control.service
for attempt in 1 2 3 4 5; do
  if runuser -u bluepanel -- test -w /run/blue-mtproxy-control/command.sock; then
    echo "[ok] Blue Proxy privileged control socket is accessible to bluepanel."
    break
  fi
  if [[ "$attempt" == 5 ]]; then
    echo "[error] Control socket never became available." >&2
    systemctl show blue-mtproxy-control.service -p Result -p ExecMainStatus --no-pager || true
    exit 1
  fi
  sleep 1
done

# Exercise the socket with the actual web service UID, without changing any
# proxy settings. This tests permissions and IPC framing, not just the path.
runuser -u bluepanel -- /usr/bin/python3 -c '
import sys
sys.path.insert(0, "/opt/blue-mtproxy/vps")
from manager.control_client import dispatch
dispatch("ping", timeout=8)
' || {
  echo "[error] The control broker did not answer an unprivileged ping." >&2
  exit 1
}
echo "[ok] Verified a round-trip broker ping from bluepanel."

# Remove obsolete privilege escalation, which was incompatible with the
# panel's NoNewPrivileges=yes and never worked. Keep the web sandbox enabled.
rm -f /etc/sudoers.d/blue-mtproxy
echo "[ok] Web sudo policy retired; NoNewPrivileges stays enabled."

if [[ "${BLUE_CONTROL_NO_RESTART:-0}" != "1" ]]; then
  systemctl restart blue-mtproxy-panel.service
  echo "[ok] Panel reloaded. Config changes now use the restricted socket broker."
fi
