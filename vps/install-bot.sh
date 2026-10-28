#!/usr/bin/env bash
# Install/reconcile Telegram publisher without touching proxies, firewall or Caddy.
set -Eeuo pipefail
[[ "$(id -u)" == 0 ]] || { echo "Run as root" >&2; exit 1; }
[[ -f /etc/blue-mtproxy/panel.env ]] || { echo "Blue Proxy panel not installed" >&2; exit 1; }
id bluepanel >/dev/null

cat > /etc/systemd/system/blue-mtproxy-telegram-bot.service <<'UNIT'
[Unit]
Description=Blue Proxy Manager Telegram channel publisher
After=network-online.target blue-mtproxy-panel.service
Wants=network-online.target
[Service]
Type=simple
User=bluepanel
Group=bluepanel
WorkingDirectory=/opt/blue-mtproxy/vps
EnvironmentFile=/etc/blue-mtproxy/panel.env
ExecStart=/opt/blue-mtproxy/.venv/bin/python -m manager.telegram_bot
Restart=always
RestartSec=7
NoNewPrivileges=yes
PrivateTmp=yes
PrivateDevices=yes
ProtectHome=yes
ProtectSystem=strict
ReadWritePaths=/var/lib/blue-mtproxy
RestrictAddressFamilies=AF_INET AF_INET6 AF_UNIX
RestrictNamespaces=yes
RestrictRealtime=yes
CapabilityBoundingSet=
UMask=0077
[Install]
WantedBy=multi-user.target
UNIT

# The bot runs as the panel's existing unprivileged UID. Never create a
# world-readable token file or insert bot secrets into EnvironmentFile.
install -d -m 0700 -o bluepanel -g bluepanel /var/lib/blue-mtproxy
systemctl daemon-reload
systemctl enable blue-mtproxy-telegram-bot.service
systemctl restart blue-mtproxy-telegram-bot.service
echo "Telegram bot unit installed (configure token and channel in /bot)"
