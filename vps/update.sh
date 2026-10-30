#!/usr/bin/env bash
# Run from a root-owned checkout only. Uses a Git fast-forward policy and local rollback.
set -Eeuo pipefail
umask 077
[[ "$(id -u)" == "0" ]] || { echo "Root required" >&2; exit 1; }
[[ "${1:-}" == "" || "${1:-}" == "--force" ]] || { echo "Unknown option" >&2; exit 2; }
force="${1:-}"
repo=/opt/blue-mtproxy
exec 9>/run/lock/blue-mtproxy-update.lock
flock -n 9 || { echo "Update already running"; exit 0; }
if [[ "$force" != "--force" ]]; then
  enabled="$(python3 - <<'PY'
import json
with open('/var/lib/blue-mtproxy/state.json') as f:
    print(json.load(f).get('auto_update') is True)
PY
)"
  [[ "$enabled" == "True" ]] || { echo "Automatic updates disabled"; exit 0; }
fi
[[ "$(git -C "$repo" remote get-url origin)" == "https://github.com/hazhanhasani/mtproto.git" ]] || {
  echo "Unexpected Git origin; update aborted" >&2; exit 1;
}
old="$(git -C "$repo" rev-parse HEAD)"
git -C "$repo" fetch --quiet origin main
target="$(git -C "$repo" rev-parse FETCH_HEAD)"
[[ "$old" != "$target" ]] || { echo "Already up to date: $old"; exit 0; }
git -C "$repo" merge-base --is-ancestor "$old" "$target" || {
  echo "Non-fast-forward update blocked" >&2; exit 1;
}
rollback() {
  echo "Update failed, rolling back to $old" >&2
  git -C "$repo" reset --hard "$old"
  "$repo/.venv/bin/pip" install -q -r "$repo/vps/requirements.txt" || true
  bash "$repo/vps/panel-permissions.sh" || true
  if [[ ! -f /etc/blue-mtproxy/node.json ]]; then
    systemctl restart blue-mtproxy-panel.service || true
    systemctl restart blue-mtproxy-telegram-bot.service || true
  fi
}
trap rollback ERR
git -C "$repo" reset --hard "$target"
bash -n "$repo/vps/install.sh" "$repo/vps/update.sh" "$repo/vps/bootstrap.sh" "$repo/vps/panel-permissions.sh" "$repo/vps/repair-panel.sh" "$repo/vps/repair-backend.sh" "$repo/vps/install-debug.sh" "$repo/vps/install-control.sh" "$repo/vps/install-bot.sh" "$repo/vps/cdn-only.sh"
"$repo/.venv/bin/pip" install -q -r "$repo/vps/requirements.txt"
bash "$repo/vps/panel-permissions.sh"
BLUE_DEBUG_NO_RESTART=1 bash "$repo/vps/install-debug.sh"
"$repo/.venv/bin/python3" -m unittest discover -s "$repo/vps/tests" -v
# The control broker is running older imported Python code after a Git
# update; restart it before the panel starts sending new operations.
BLUE_CONTROL_NO_RESTART=1 bash "$repo/vps/install-control.sh"
if [[ -f /etc/blue-mtproxy/node.json ]]; then
  # Keep the remote admin and publishing bot closed. The controller remains
  # the only write surface; the node timer picks up the new agent code.
  systemctl stop blue-mtproxy-panel.service blue-mtproxy-telegram-bot.service 2>/dev/null || true
  systemctl restart blue-mtproxy-node.timer
  trap - ERR
  echo "Node updated to $target"
  exit 0
fi
systemctl restart blue-mtproxy-panel.service
bash "$repo/vps/install-bot.sh"
for i in 1 2 3 4 5 6 7 8 9 10; do
  if curl --silent --fail --max-time 2 http://127.0.0.1:8750/healthz | grep -q '"ok":true'; then
    trap - ERR
    echo "Updated to $target"
    exit 0
  fi
  sleep 1
done
false
