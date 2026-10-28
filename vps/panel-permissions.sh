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

# The updater runs with umask 077. A new Git checkout/reset can therefore
# leave source directories 0700 and source files 0600. Gunicorn may be able
# to run Python but still fail while importing /opt/blue-mtproxy/vps/app.py
# (PermissionError -> worker boot failure -> HTTP 502).
#
# Grant the *dedicated* panel group read/traverse on the checked-out VPS
# application only. Never make code world-readable/writable, do not recurse
# into .git, and do not touch /etc/blue-mtproxy or persisted client secrets.
chgrp -- "$group" "$root"
chmod g+x -- "$root"
chgrp -R -- "$group" "$root/vps"
chmod -R g+rX "$root/vps"
chmod -R go-w "$root/vps"

# Preserve root ownership of Python packages while granting the same panel
# group read/execute access to the virtual environment.
chgrp -R -- "$group" "$venv"
chmod -R g+rX "$venv"
chmod -R go-w "$venv"

# Verify real unprivileged source reads and imports, not just wheel installs.
# A test-only key permits app import without reading panel.env.
if ! runuser -u bluepanel -- /bin/sh -c '
  cd /opt/blue-mtproxy/vps || exit 1
  export PANEL_SECRET_KEY=permission-check-only
  exec /opt/blue-mtproxy/.venv/bin/python3 -c "
from pathlib import Path
import app
from manager.bot_config import get_config
assert Path(\"templates/manager.html\").is_file()
assert Path(\"templates/manager.html\").read_text(encoding=\"utf-8\")
assert app.app is not None
"
'; then
  echo "bluepanel cannot read the application source, templates or imports." >&2
  exit 1
fi
echo "[ok] Python source and templates are readable by bluepanel."

# Check paths and the Gunicorn module from the perspective of its actual
# systemd user, without needing to read environment secrets.
if ! runuser -u bluepanel -- "$venv/bin/python3" -c 'import flask, gunicorn'; then
  echo "bluepanel still cannot import Flask/Gunicorn. Check parent directory traversal." >&2
  exit 1
fi
echo "[ok] Python venv is now executable by bluepanel (read/execute only)."
