#!/usr/bin/env bash
# Read-only, non-secret login routing diagnostic for Blue Proxy Manager.
set -Eeuo pipefail
[[ "$(id -u)" == 0 ]] || { echo "Run as root" >&2; exit 1; }
[[ -r /var/lib/blue-mtproxy/state.json ]] || { echo "Panel state missing" >&2; exit 1; }
panel_host="$(python3 - <<'PY'
import json
from pathlib import Path
print(json.loads(Path("/var/lib/blue-mtproxy/state.json").read_text())["panel_hostname"])
PY
)"
[[ "$panel_host" =~ ^[a-z0-9.-]+$ && "$panel_host" == *.* ]] || { echo "Invalid panel hostname" >&2; exit 1; }

status() {
  local name="$1" url="$2" resolve="$3" code
  if [[ -n "$resolve" ]]; then
    code="$(curl --noproxy '*' --silent --show-error --max-time 8 \
      --resolve "$resolve" -o /dev/null -w '%{http_code}' "$url")" || code=000
  else
    code="$(curl --noproxy '*' --silent --show-error --max-time 8 \
      -o /dev/null -w '%{http_code}' "$url")" || code=000
  fi
  echo "$name: HTTP $code" >&2
  [[ "$code" == 200 ]]
}

echo "Configured panel domain: $panel_host"
echo "Panel service: $(systemctl is-active blue-mtproxy-panel.service 2>/dev/null || true)"
echo "Caddy service: $(systemctl is-active caddy.service 2>/dev/null || true)"
backend_ok=0
origin_ok=0
status "Flask GET /login" "http://127.0.0.1:8750/login" "" && backend_ok=1 || true
status "Caddy HTTPS GET /login" "https://$panel_host/login" "$panel_host:443:127.0.0.1" && origin_ok=1 || true

if [[ "$backend_ok" == 1 && "$origin_ok" == 1 ]]; then
  echo "[ok] Both local Flask and Caddy HTTPS routing serve /login successfully."
  echo "[hint] A remaining public 403 may be a stale form, browser cookie, CDN rule or external route."
elif [[ "$backend_ok" == 1 ]]; then
  echo "[error] Flask login works, but Caddy HTTPS route does not."
  echo "Inspect Caddy hostname and reverse_proxy 127.0.0.1:8750."
  exit 1
else
  echo "[error] Flask login is not healthy. Repair panel permissions/services before inspecting DNS."
  echo "Run: sudo bash /opt/blue-mtproxy/vps/repair-panel.sh"
  exit 1
fi
