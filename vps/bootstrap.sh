#!/usr/bin/env bash
set -Eeuo pipefail
if [[ "$(id -u)" != 0 ]]; then
  echo "Run this bootstrap with sudo/root" >&2
  exit 1
fi
command -v apt-get >/dev/null || { echo "Ubuntu/Debian with apt-get required" >&2; exit 1; }
apt-get update
DEBIAN_FRONTEND=noninteractive apt-get install -y ca-certificates git curl
if [[ -e /opt/blue-mtproxy ]]; then
  echo "/opt/blue-mtproxy already exists. Refusing to overwrite an installed system." >&2
  exit 1
fi
git clone --branch main --single-branch https://github.com/hazhanhasani/mtproto.git /opt/blue-mtproxy
exec bash /opt/blue-mtproxy/vps/install.sh "$@"
