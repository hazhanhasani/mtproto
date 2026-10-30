#!/usr/bin/env bash
# Undo accidental same-host node enrollment without touching proxy state.
set -Eeuo pipefail
umask 077
[[ "$(id -u)" == 0 ]] || { echo "Run with sudo" >&2; exit 1; }
STATE=/var/lib/blue-mtproxy/state.json
NODE=/etc/blue-mtproxy/node.json
[[ -f "$STATE" && -f /etc/blue-mtproxy/panel.env ]] || {
  echo "[error] This VPS is not an installed Blue Proxy controller." >&2
  exit 1
}

if [[ -f "$NODE" ]]; then
  if ! python3 - <<'PY'
import json
from pathlib import Path
from urllib.parse import urlsplit
node = json.loads(Path("/etc/blue-mtproxy/node.json").read_text())
state = json.loads(Path("/var/lib/blue-mtproxy/state.json").read_text())
controller = urlsplit(node["controller"]).hostname
if not controller or controller.lower().rstrip(".") != state["panel_hostname"].lower().rstrip("."):
    raise SystemExit(1)
PY
  then
    echo "[error] This is a remote node, not the controller; no service was changed." >&2
    echo "Check: sudo journalctl -u blue-mtproxy-node.service -n 50 --no-pager" >&2
    exit 1
  fi
  echo "[warn] Central controller was mistakenly registered as its own remote node."
  systemctl disable --now blue-mtproxy-node.timer 2>/dev/null || true
  systemctl stop blue-mtproxy-node.service 2>/dev/null || true
  backup="/etc/blue-mtproxy/node.controller-mispaired.$(date -u +%Y%m%dT%H%M%SZ).json"
  mv -- "$NODE" "$backup"
  chmod 0600 "$backup"
  echo "[ok] Node token preserved in root-only backup, active config disarmed."
fi

# Do not rewrite state.json, backend configs, bot credentials or Caddyfiles.
bash /opt/blue-mtproxy/vps/panel-permissions.sh
systemctl reset-failed blue-mtproxy-panel.service || true
systemctl enable --now blue-mtproxy-panel.service
if systemctl cat blue-mtproxy-telegram-bot.service >/dev/null 2>&1; then
  systemctl enable --now blue-mtproxy-telegram-bot.service || \
    echo "[warn] Optional Telegram bot is not ready; panel recovery continues."
fi
if systemctl cat blue-mtproxy-telegram-cron.timer >/dev/null 2>&1; then
  systemctl enable --now blue-mtproxy-telegram-cron.timer || \
    echo "[warn] Optional channel timer could not be enabled."
fi

for attempt in 1 2 3 4 5 6 7 8 9 10; do
  if curl --noproxy '*' --fail --silent --max-time 3 http://127.0.0.1:8750/healthz | grep -q '"ok":true'; then
    echo "[ok] Controller panel is healthy at 127.0.0.1:8750."
    echo "Check public HTTPS with: sudo bash /opt/blue-mtproxy/vps/diagnose-login.sh"
    exit 0
  fi
  sleep 1
done
echo "[error] Panel still unhealthy; inspect journalctl -u blue-mtproxy-panel.service" >&2
exit 1
