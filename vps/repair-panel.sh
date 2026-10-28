#!/usr/bin/env bash
# Safe recovery after the install reached the SSL validation stage but the
# management-panel upstream is 502. Does not touch Caddy certificates or data.
set -Eeuo pipefail
[[ "$(id -u)" == 0 ]] || { echo "Run with sudo" >&2; exit 1; }
bash /opt/blue-mtproxy/vps/panel-permissions.sh
systemctl reset-failed blue-mtproxy-panel.service || true
systemctl restart blue-mtproxy-panel.service
echo "Checking local panel backend on 127.0.0.1:8750..."
for attempt in 1 2 3 4 5 6 7 8 9 10; do
  if curl --noproxy '*' --fail --silent --show-error --max-time 4 \
    http://127.0.0.1:8750/healthz | grep -q '"ok":true'; then
    echo "[ok] Panel upstream is healthy; existing Caddy HTTPS will serve it."
    bash /opt/blue-mtproxy/vps/ssl-status.sh
    exit 0
  fi
  sleep 1
done
echo "[error] Panel still unavailable. Recent service logs follow:" >&2
systemctl --no-pager --full status blue-mtproxy-panel.service || true
journalctl -u blue-mtproxy-panel.service -n 45 --no-pager || true
exit 1
