#!/usr/bin/env bash
# Idempotent least-privilege /debug installer on an already-running VPS.
# Does NOT touch TLS, MTProxy listeners, credentials or state.
set -Eeuo pipefail
[[ "$(id -u)" == "0" ]] || { echo "Run with sudo/root" >&2; exit 1; }
[[ -f /opt/blue-mtproxy/vps/manager/debug.py ]] || {
  echo "Update /opt/blue-mtproxy from GitHub before running this script." >&2
  exit 1
}
id bluepanel >/dev/null
install -o root -g root -m 0755 /dev/null /usr/local/sbin/blue-mtproxy-debug
cat > /usr/local/sbin/blue-mtproxy-debug <<'SH'
#!/bin/sh
exec /usr/bin/python3 /opt/blue-mtproxy/vps/manager/debug.py
SH
chmod 0755 /usr/local/sbin/blue-mtproxy-debug
cat > /etc/sudoers.d/blue-mtproxy-debug <<'SUDO'
bluepanel ALL=(root) NOPASSWD: /usr/local/sbin/blue-mtproxy-debug
SUDO
chown root:root /etc/sudoers.d/blue-mtproxy-debug
chmod 0440 /etc/sudoers.d/blue-mtproxy-debug
visudo -cf /etc/sudoers.d/blue-mtproxy-debug >/dev/null
python3 -m py_compile /opt/blue-mtproxy/vps/manager/debug.py
echo "[ok] Protected, read-only /debug helper and sudo policy installed."
if [[ "${BLUE_DEBUG_NO_RESTART:-0}" != "1" ]] && systemctl is-enabled blue-mtproxy-panel.service >/dev/null 2>&1; then
  systemctl restart blue-mtproxy-panel.service
  echo "[ok] Panel restarted. Open https://PANEL_HOSTNAME/debug while signed in."
fi
