#!/usr/bin/env bash
# Repair the existing WEB relay configuration without reinstalling or rotating
# secrets. Preserve TLS certificates, Caddy config and admin credentials.
set -Eeuo pipefail
[[ "$(id -u)" == "0" ]] || { echo "Run as root with sudo" >&2; exit 1; }
file="/etc/tproxy-server/profiles.json"
[[ -f "$file" && ! -L "$file" ]] || {
  echo "Missing or unsafe relay profile path; refusing automatic repair." >&2
  exit 1
}
id tproxy >/dev/null 2>&1 || {
  echo "tproxy service user does not exist." >&2
  exit 1
}
# Fix a legacy panel bug that wrote group-unreadable 0400 profiles.
chown root:tproxy "$file"
chmod 0640 "$file"
runuser -u tproxy -- test -r "$file"
echo "[ok] Relay profiles are readable by the dedicated service account."

# Apply desired panel configuration from the installed state and ensure
# per-secret MTProxy units are running. This will not regenerate secrets.
if [[ -x /usr/local/sbin/blue-mtproxy-apply ]]; then
  /usr/local/sbin/blue-mtproxy-apply
fi
systemctl restart tproxy-server.service
echo "[ok] Reloaded the WEB relay without changing Caddy TLS certificates."

python3 /opt/blue-mtproxy/vps/proxy-status.py
