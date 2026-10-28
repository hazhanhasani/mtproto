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
ReadWritePaths=/run/blue-mtproxy-control
RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6
# This service only authenticates peers and invokes fixed systemd units.
CapabilityBoundingSet=CAP_CHOWN
UMask=0077
[Install]
WantedBy=multi-user.target
UNIT

# The small UNIX broker stays heavily sandboxed. Reconciliation is instead
# an independently audited systemd oneshot with narrowly scoped write paths.
# systemctl start waits for a oneshot result; a failed reconcile propagates to
# the panel and can be diagnosed via this unit's journal.
cat > /etc/systemd/system/blue-mtproxy-apply.service <<'UNIT'
[Unit]
Description=Blue Proxy Manager apply validated proxy configuration
After=network-online.target
Wants=network-online.target
[Service]
Type=oneshot
User=root
Group=root
ExecStart=/usr/local/sbin/blue-mtproxy-apply
TimeoutStartSec=95s
NoNewPrivileges=yes
PrivateTmp=yes
PrivateDevices=yes
ProtectHome=yes
ProtectSystem=strict
ReadWritePaths=/etc/blue-mtproxy /etc/tproxy-server /etc/systemd/system /var/lib/blue-mtproxy /run/lock
RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6
UMask=0077
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

# Verify the ENTIRE route (panel UID -> broker -> root systemd oneshot).
# Existing desired state is re-applied, without rotating secrets. Fail closed
# rather than declaring the installation healthy after a ping-only check.
if ! runuser -u bluepanel -- /usr/bin/python3 -c '
import sys
sys.path.insert(0, "/opt/blue-mtproxy/vps")
from manager.control_client import dispatch
dispatch("apply", timeout=118)
'; then
  echo "[error] End-to-end apply failed. No client secrets were regenerated." >&2
  echo "[diag] sudo journalctl -u blue-mtproxy-apply.service -n 45 --no-pager" >&2
  echo "[diag] sudo systemctl show blue-mtproxy-apply.service -p Result -p ExecMainStatus" >&2
  exit 1
fi
echo "[ok] Verified end-to-end apply through the dedicated systemd reconciler."

# Remove obsolete privilege escalation, which was incompatible with the
# panel's NoNewPrivileges=yes and never worked. Keep the web sandbox enabled.
rm -f /etc/sudoers.d/blue-mtproxy
echo "[ok] Web sudo policy retired; NoNewPrivileges stays enabled."
# Publish a fresh report so /debug immediately reflects the new service.
if systemctl cat blue-mtproxy-debug.service >/dev/null 2>&1; then
  systemctl start blue-mtproxy-debug.service || true
fi

if [[ "${BLUE_CONTROL_NO_RESTART:-0}" != "1" ]]; then
  systemctl restart blue-mtproxy-panel.service
  panel_ready=0
  for attempt in 1 2 3 4 5 6 7 8 9 10; do
    if curl --noproxy '*' --silent --fail --max-time 3 \
        http://127.0.0.1:8750/healthz | grep -q '"ok":true'; then
      panel_ready=1
      break
    fi
    sleep 1
  done
  if [[ "$panel_ready" != 1 ]]; then
    echo "[error] Admin panel did not become healthy after restart." >&2
    echo "Inspect: sudo systemctl status blue-mtproxy-panel --no-pager" >&2
    exit 1
  fi
  echo "[ok] Panel health verified; config changes now use the restricted socket broker."
fi
