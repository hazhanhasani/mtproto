#!/usr/bin/env bash
# Read-only diagnosis of the existing VPS installation. Never modifies services.
set -Eeuo pipefail
QUIET=0
if [[ "${1:-}" == "--quiet" ]]; then QUIET=1; shift; fi
if [[ "$#" -ne 0 ]]; then
  echo "Usage: ssl-status.sh [--quiet]" >&2
  exit 2
fi
STATE=/var/lib/blue-mtproxy/state.json
if [[ ! -r "$STATE" ]]; then
  echo "Cannot read $STATE (run with sudo on an installed VPS)." >&2
  exit 1
fi
if ! hosts="$(python3 - <<'PY'
import json
from pathlib import Path
v = json.loads(Path("/var/lib/blue-mtproxy/state.json").read_text())
for key in ("hostname", "panel_hostname"):
    hostname = v[key]
    if not isinstance(hostname, str) or not hostname or '/' in hostname:
        raise SystemExit("invalid hostname in installation state")
    print(hostname)
PY
)"; then
  echo "Cannot read configured domains." >&2
  exit 1
fi
mapfile -t NAMES <<< "$hosts"
if [[ "${#NAMES[@]}" -ne 2 ]]; then
  echo "Installation state must contain two hostnames." >&2
  exit 1
fi
ready=1
for i in 0 1; do
  name="${NAMES[$i]}"
  uri="/"
  if [[ "$i" -eq 1 ]]; then uri="/login"; fi
  # Ignore the HTTP response code while checking certificates: HTTP 404 or
  # 503 can accompany a correctly-issued and trusted TLS certificate.
  # curl without -k still checks the full X.509 trust chain and hostname.
  if result="$(curl --noproxy '*' --silent --show-error \
    --connect-timeout 5 --max-time 14 \
    --resolve "$name:443:127.0.0.1" \
    --output /dev/null --write-out '%{http_code}' \
    "https://$name$uri" 2>&1)"; then
    if [[ "$QUIET" -eq 0 ]]; then
      echo "[TLS OK] $name -> trusted certificate; HTTP status $result"
    fi
  else
    ready=0
    if [[ "$QUIET" -eq 0 ]]; then
      echo "[TLS FAIL] $name -> $result"
    fi
  fi
done
if [[ "$QUIET" -eq 0 ]]; then
  echo ""
  echo "Systemd services (informational):"
  systemctl is-active caddy tproxy-server blue-mtproxy-panel 2>/dev/null || true
  if [[ "$ready" -ne 1 ]]; then
    echo ""
    echo "Recent certificate-related Caddy diagnostics:"
    journalctl -u caddy --since '30 minutes ago' --no-pager \
      | grep -Ei 'error|warn|acme|challenge|certificate|issuer|authorization|timeout|refused' \
      | tail -50 || true
    echo ""
    echo "Check A/AAAA and CAA DNS records and external TCP 80/443 access."
    echo "Do not use curl -k, remove TLS, or reinstall the existing VPS."
  fi
fi
[[ "$ready" -eq 1 ]]
