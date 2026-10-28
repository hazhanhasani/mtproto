#!/usr/bin/env bash
# Grant the unprivileged Gunicorn service read/execute access to root-managed
# Python packages. This does NOT change ownership, secrets or panel credentials.
set -Eeuo pipefail
[[ "$(id -u)" == "0" ]] || { echo "Root required" >&2; exit 1; }

root=/opt/blue-mtproxy
venv="$root/.venv"
[[ -d "$root/.git" && -f "$root/vps/app.py" && -d "$venv/bin" && -f "$venv/pyvenv.cfg" ]] || {
  echo "Expected installed Blue Proxy Manager environment was not found." >&2
  exit 1
}
id bluepanel >/dev/null 2>&1 || {
  echo "bluepanel service user is missing; refusing to modify permissions." >&2
  exit 1
}
# The installer uses umask 077. Python venv therefore contains 0700
# directories and 0700 scripts, inaccessible to User=bluepanel. Keep root
# ownership and deny write access to any non-root process.
group="$(id -gn bluepanel)"
chgrp -R -- "$group" "$venv"
chmod -R g+rX "$venv"
chmod -R go-w "$venv"

# Check paths and the Gunicorn module from the perspective of its actual
# systemd user, without needing to read environment secrets.
if ! runuser -u bluepanel -- "$venv/bin/python3" -c 'import flask, gunicorn'; then
  echo "bluepanel still cannot import Flask/Gunicorn. Check parent directory traversal." >&2
  exit 1
fi
echo "[ok] Python venv is now executable by bluepanel (read/execute only)."
