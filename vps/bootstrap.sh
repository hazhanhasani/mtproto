#!/usr/bin/env bash
# Zero-argument, interactive quick installer. Safe to pipe to sudo bash.
set -Eeuo pipefail
if [[ "$(id -u)" != 0 ]]; then
  echo "Run with sudo/root, e.g. curl -fsSL ... | sudo bash" >&2
  exit 1
fi
command -v apt-get >/dev/null || {
  echo "Only Debian/Ubuntu with apt-get is supported." >&2
  exit 1
}
apt-get update
DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends ca-certificates git curl
REPO=/opt/blue-mtproxy
if [[ -e /etc/blue-mtproxy/panel.env ]]; then
  echo "An installation already exists. Use the GitHub updater instead." >&2
  exit 1
fi
if [[ -e "$REPO" ]]; then
  # A safe retry is allowed if a prior attempt stopped at DNS/port preflight,
  # before the upstream installer wrote any host configuration.
  if [[ ! -d "$REPO/.git" || -e /etc/tproxy-server/config.json || -e /etc/caddy/Caddyfile ]]; then
    echo "Previous installation is incomplete or a webserver is present." >&2
    echo "Inspect the existing system; refusing to overwrite or reset it." >&2
    exit 1
  fi
  remote="$(git -C "$REPO" remote get-url origin)"
  [[ "$remote" == "https://github.com/hazhanhasani/mtproto.git" ]] || {
    echo "Unexpected Git origin. Aborting." >&2
    exit 1
  }
  if [[ -n "$(git -C "$REPO" status --porcelain)" ]]; then
    echo "Local repository modifications detected. Aborting." >&2
    exit 1
  fi
  git -C "$REPO" pull --ff-only origin main
else
  git clone --branch main --single-branch https://github.com/hazhanhasani/mtproto.git "$REPO"
fi
exec bash "$REPO/vps/install.sh" "$@"
