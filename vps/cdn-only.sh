#!/usr/bin/env bash
# Cloudflare HTTP/WebSocket CDN transport only.
# Expose the existing Caddy HTTPS origin on 443. Keep MTProxy backend TCP
# listeners on 10443-10474 strictly local (even if bound to 0.0.0.0).
# Does NOT change DNS or Cloudflare account settings. Never blocks SSH.
set -Eeuo pipefail
umask 077
if [[ "$(id -u)" != 0 ]]; then echo "Run with sudo/root" >&2; exit 1; fi
case "${1:-}" in enable|status|disable) ;; *) echo "Usage: cdn-only.sh enable|status|disable" >&2; exit 2;; esac

SERVICE=blue-mtproxy-cdn-only.service
TABLE=blue_proxy_cdn_only
FLAG=/etc/blue-mtproxy/cdn-only
CONFIG=/etc/blue-mtproxy/cdn-only.nft

verify_guard() {
  python3 /opt/blue-mtproxy/vps/verify-cdn-firewall.py
}

status() {
  if systemctl is-active --quiet "$SERVICE" && verify_guard; then
    echo "[ok] CDN-only firewall active: external TCP 10443-10474 blocked; HTTPS 443 unaffected."
  else
    echo "[error] CDN-only firewall cannot be confirmed. Read-only diagnostic:"
    systemctl is-active "$SERVICE" || true
    /usr/sbin/nft -a list table inet "$TABLE" || true
    return 1
  fi
  if [[ -f "$FLAG" ]]; then
    echo "[info] Admin panel labels this host as WEB/CDN-only."
  fi
}
if [[ "$1" == status ]]; then status; exit 0; fi
if [[ "$1" == disable ]]; then
  systemctl disable --now "$SERVICE" 2>/dev/null || true
  /usr/sbin/nft delete table inet "$TABLE" 2>/dev/null || true
  rm -f "$FLAG"
  echo "[ok] CDN-only restriction disabled. No other firewall rules were changed."
  echo "[warning] Public MTProto ports may now be reachable if provider firewall permits."
  exit 0
fi

[[ -f /var/lib/blue-mtproxy/state.json && -d /etc/blue-mtproxy ]] || {
  echo "[error] Existing installed Blue Proxy Manager was not found." >&2
  exit 1
}
[[ -x /usr/sbin/nft ]] || {
  echo "[error] nftables command is missing; refusing to mark CDN-only mode." >&2
  exit 1
}
systemctl is-active --quiet caddy.service && systemctl is-active --quiet tproxy-server.service || {
  echo "[error] HTTPS origin/WEB relay must be active before enabling CDN-only." >&2
  exit 1
}
# Public 80 for ACME and 443 for HTTPS; do not close SSH or touch TLS here.
if ! bash /opt/blue-mtproxy/vps/ssl-status.sh --quiet; then
  echo "[error] Origin certificates have not passed local SNI verification." >&2
  exit 1
fi

cat > "$CONFIG" <<'NFT'
table inet blue_proxy_cdn_only {
  chain drop_external_mtproxy {
    type filter hook input priority -15; policy accept;
    iifname != "lo" tcp dport 10443-10474 drop
  }
}
NFT
chown root:root "$CONFIG"
chmod 0600 "$CONFIG"

cat > "/etc/systemd/system/$SERVICE" <<'UNIT'
[Unit]
Description=Blue Proxy CDN-only guard (deny direct public MTProxy backend ports)
After=network-online.target nftables.service tproxy-firewall.service
PartOf=nftables.service tproxy-firewall.service
Wants=network-online.target
[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=-/usr/sbin/nft delete table inet blue_proxy_cdn_only
ExecStart=/usr/sbin/nft -f /etc/blue-mtproxy/cdn-only.nft
ExecReload=-/usr/sbin/nft delete table inet blue_proxy_cdn_only
ExecReload=/usr/sbin/nft -f /etc/blue-mtproxy/cdn-only.nft
ExecStop=-/usr/sbin/nft delete table inet blue_proxy_cdn_only
[Install]
WantedBy=multi-user.target
UNIT
systemctl daemon-reload
systemctl enable "$SERVICE" >/dev/null
if ! systemctl restart "$SERVICE"; then
  echo "[error] CDN-only firewall systemd service did not start." >&2
  journalctl -u "$SERVICE" -n 24 --no-pager >&2 || true
  exit 1
fi
if ! verify_guard; then
  echo "[error] Rule verification failed; here is the ACTUAL nftables table for diagnosis:" >&2
  /usr/sbin/nft -a list table inet "$TABLE" >&2 || true
  echo "[error] Do not enable Cloudflare orange-cloud until the guard is confirmed." >&2
  exit 1
fi
printf '%s\n' 'WEB_CDN_ONLY=1' > "$FLAG"
chown root:root "$FLAG"
chmod 0644 "$FLAG"
echo "[ok] CDN-only firewall installed persistently, public MTProxy ports blocked."
echo "[ok] SSH and existing HTTPS/SSL configurations were not touched."
echo "[next] Cloudflare DNS: enable Proxied (orange cloud) for the WEB proxy hostname."
echo "[next] Cloudflare SSL/TLS: Full (strict); Network: WebSockets On."
echo "[note] Ordinary Telegram MTProto t.me/proxy is NOT compatible with HTTP CDN."
