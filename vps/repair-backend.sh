#!/usr/bin/env bash
# Repair MTProxy backend runtime path permissions without reinstalling,
# replacing secrets, altering Caddy, or restarting the management panel.
set -Eeuo pipefail
[[ "$(id -u)" == "0" ]] || { echo "[error] Run with sudo" >&2; exit 1; }

ROOT=/etc/blue-mtproxy
RUNTIME="$ROOT/runtime"
STATE=/var/lib/blue-mtproxy/state.json

[[ -d "$ROOT" && ! -L "$ROOT" && -d "$RUNTIME" && ! -L "$RUNTIME" ]] || {
  echo "[error] The expected runtime directories are missing or unsafe." >&2
  exit 1
}
[[ -f "$STATE" && ! -L "$STATE" ]] || {
  echo "[error] The installation state is missing or unsafe." >&2
  exit 1
}
id mtproxy >/dev/null 2>&1 || { echo "[error] mtproxy user is missing." >&2; exit 1; }
id bluepanel >/dev/null 2>&1 || { echo "[error] bluepanel user is missing." >&2; exit 1; }
[[ "$(stat -c '%U:%G' "$ROOT")" == "root:bluepanel" ]] || {
  echo "[error] Unexpected runtime parent ownership. Refusing to change it." >&2
  exit 1
}

# A verified Git checkout may be 0700/0600 under the updater's umask,
# and panel-permissions.sh assigns code to group bluepanel. MTProxy is
# deliberately NOT a bluepanel group member (panel.env is private).
# Repair only launcher traversal/read access before restarting a backend.
CODE=/opt/blue-mtproxy
LAUNCHER="$CODE/vps/manager/runner.py"
[[ -d "$CODE" && ! -L "$CODE" && -d "$CODE/vps" && ! -L "$CODE/vps" && \
   -d "$CODE/vps/manager" && ! -L "$CODE/vps/manager" && \
   -f "$LAUNCHER" && ! -L "$LAUNCHER" ]] || {
  echo "[error] Expected MTProxy launcher source path is missing or unsafe." >&2
  exit 1
}
[[ "$(stat -c '%U' "$LAUNCHER")" == "root" ]] || {
  echo "[error] MTProxy launcher must be root-owned." >&2
  exit 1
}
chmod o+x "$CODE" "$CODE/vps" "$CODE/vps/manager"
chmod o+r "$LAUNCHER"
if ! runuser -u mtproxy -- /usr/bin/python3 -B -c '
import runpy
runpy.run_path("/opt/blue-mtproxy/vps/manager/runner.py", run_name="permission_probe")
'; then
  echo "[error] MTProxy cannot read the Python launcher. No secrets changed." >&2
  exit 1
fi
echo "[ok] mtproxy can import the root-owned launcher."

# The original installer made ROOT 0750 root:bluepanel, so mtproxy cannot
# open ROOT/runtime/main.json, despite runtime itself being root:mtproxy 0750.
# 0711 allows traversal by mtproxy but never directory listing.
chmod 0711 "$ROOT"
chmod 0750 "$RUNTIME"
chown root:mtproxy "$RUNTIME"

# Enumerate only validated, enabled profile IDs. Secrets never enter stdout.
ids="$(python3 - <<'PY'
import json, sys
from pathlib import Path
sys.path.insert(0, "/opt/blue-mtproxy/vps/manager")
from model import validate
state = validate(json.loads(Path("/var/lib/blue-mtproxy/state.json").read_text()))
print("\n".join(p["id"] for p in state["profiles"] if p["enabled"]))
PY
)"
[[ -n "$ids" ]] || { echo "[error] No enabled backend profiles." >&2; exit 1; }

all_ok=1
while IFS= read -r id; do
  [[ -n "$id" ]] || continue
  profile="$RUNTIME/$id.json"
  [[ -f "$profile" && ! -L "$profile" ]] || {
    echo "[error] Missing managed runtime file for profile $id." >&2
    all_ok=0
    continue
  }
  chown root:mtproxy "$profile"
  chmod 0640 "$profile"
  if ! runuser -u mtproxy -- test -r "$profile"; then
    echo "[error] The mtproxy user cannot read runtime profile $id." >&2
    all_ok=0
    continue
  fi
  echo "[ok] mtproxy can traverse runtime directories and read profile $id."
  unit="blue-mtproxy@$id.service"
  systemctl reset-failed "$unit" >/dev/null 2>&1 || true
  if ! systemctl restart "$unit"; then
    echo "[error] systemd refused to start $unit." >&2
    systemctl show "$unit" -p Result -p ExecMainCode -p ExecMainStatus --no-pager || true
    all_ok=0
    continue
  fi
  sleep 2
  if ! systemctl is-active --quiet "$unit"; then
    echo "[error] $unit terminated after start." >&2
    systemctl show "$unit" -p Result -p ExecMainCode -p ExecMainStatus --no-pager || true
    all_ok=0
    continue
  fi
  port="$(python3 - "$STATE" "$id" <<'PY'
import json, sys
from pathlib import Path
data = json.loads(Path(sys.argv[1]).read_text())
for p in data["profiles"]:
    if p["id"] == sys.argv[2]:
        print(10443 + p["slot"])
        break
PY
)"
  if python3 - "$port" <<'PY'
import socket, sys
try:
    with socket.create_connection(("127.0.0.1", int(sys.argv[1])), timeout=3):
        pass
except OSError:
    sys.exit(1)
PY
  then
    echo "[ok] $unit is active; TCP $port is accepting connections locally."
  else
    echo "[error] $unit is active, but TCP $port has no listener." >&2
    all_ok=0
  fi
done <<< "$ids"

if [[ "$all_ok" != "1" ]]; then
  echo "[error] One or more backends failed; no secrets were changed." >&2
  echo "Check: sudo journalctl -u blue-mtproxy@main.service -n 50 --no-pager" >&2
  exit 1
fi
if curl --noproxy '*' -fsS --max-time 7 http://127.0.0.1:8081/readyz >/dev/null; then
  echo "[ok] WEB relay readiness is healthy."
else
  echo "[warning] MTProxy backend is healthy, but relay readiness still needs checking."
fi
# The /debug page reads a redacted snapshot from a root-only timer.
# Refresh the snapshot immediately after recovery instead of showing stale
# failures until the next periodic timer tick.
if systemctl cat blue-mtproxy-debug.service >/dev/null 2>&1; then
  if systemctl start blue-mtproxy-debug.service; then
    echo "[ok] /debug snapshot refreshed after backend repair."
  else
    echo "[warning] /debug refresh failed; investigate blue-mtproxy-debug.service." >&2
  fi
fi
echo "[done] Backend permissions repaired; SSL, sponsors, secrets and admin credentials unchanged."
