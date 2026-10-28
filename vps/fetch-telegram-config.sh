#!/usr/bin/env bash
# Get public Telegram MTProxy backend configuration with a validated GitHub
# mirror as failover for VPS networks that receive 404 from core.telegram.org.
set -Eeuo pipefail
umask 077
if [[ "$#" != 2 ]]; then
  echo "Usage: fetch-telegram-config.sh SECRET_DEST CONFIG_DEST" >&2
  exit 2
fi
out_secret="$1"
out_config="$2"
case "$out_secret" in
  /etc/mtproxy/proxy-secret.*) ;;
  *) echo "Unexpected secret temp destination" >&2; exit 2 ;;
esac
case "$out_config" in
  /etc/mtproxy/proxy-multi.conf.*) ;;
  *) echo "Unexpected config temp destination" >&2; exit 2 ;;
esac

mirror="https://raw.githubusercontent.com/hazhanhasani/mtproto/main/.telegram-cache"
fetch() {
  local name="$1" origin="$2" dest="$3" stage="$(mktemp /tmp/blue-telegram-config.XXXXXX)"
  local remote=""
  trap 'rm -f "$stage"' RETURN
  for remote in "$origin" "$mirror/$name"; do
    echo "[Telegram config] Fetching $name from ${remote%%\?*}"
    if ! curl --fail --silent --show-error --location \
      --retry 2 --retry-delay 2 --connect-timeout 10 --max-time 45 \
      --proto '=https' --proto-redir '=https' --tlsv1.2 \
      --output "$stage" "$remote"; then
      echo "[Telegram config] Unavailable: $remote" >&2
      continue
    fi
    if [[ "$name" == "proxy-secret" ]]; then
      [[ "$(wc -c < "$stage")" -eq 128 ]] || {
        echo "[Telegram config] Rejected malformed proxy-secret" >&2
        continue
      }
    else
      [[ "$(wc -c < "$stage")" -ge 100 ]] && \
      grep -q '^default ' "$stage" && grep -q '^proxy_for ' "$stage" || {
        echo "[Telegram config] Rejected malformed proxy-multi.conf" >&2
        continue
      }
    fi
    # Preserve permissions from the original mktemp destination. Caller later
    # moves files into their final root:mtproxy directory and adjusts mode.
    cat "$stage" > "$dest"
    echo "[Telegram config] Verified $name ($(wc -c < "$stage") bytes)"
    return 0
  done
  echo "[Telegram config] ERROR: Neither official Telegram nor verified GitHub mirror provided a usable $name." >&2
  return 1
}
fetch proxy-secret "https://core.telegram.org/getProxySecret" "$out_secret"
fetch proxy-multi.conf "https://core.telegram.org/getProxyConfig" "$out_config"
