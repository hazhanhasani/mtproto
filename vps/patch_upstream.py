"""Narrow, idempotent patch to the pinned upstream installer.

Replaces only the two official MTProxy config fetches. Upstream build,
checksums, service settings, and remaining commands are left intact.
"""
from pathlib import Path
import sys

PROXY_SECRET_DOWNLOAD = """curl --fail --silent --show-error --location --proto '=https' --proto-redir '=https' --tlsv1.2 \\
\t--output "$secret_temp" https://core.telegram.org/getProxySecret"""
PROXY_CONFIG_DOWNLOAD = """curl --fail --silent --show-error --location --proto '=https' --proto-redir '=https' --tlsv1.2 \\
\t--output "$config_temp" https://core.telegram.org/getProxyConfig"""
REPLACEMENT = (
    'bash /opt/blue-mtproxy/vps/fetch-telegram-config.sh '
    '"$secret_temp" "$config_temp"'
)


def patch_source(original):
    if REPLACEMENT in original:
        return original
    if original.count(PROXY_SECRET_DOWNLOAD) != 1 or original.count(PROXY_CONFIG_DOWNLOAD) != 1:
        raise ValueError("Unexpected upstream installer layout. Refusing unsafe modifications.")
    # Do not risk a partially patched upstream script.
    first = original.replace(PROXY_SECRET_DOWNLOAD, "")
    return first.replace(PROXY_CONFIG_DOWNLOAD, REPLACEMENT)


def main():
    if len(sys.argv) != 2:
        raise SystemExit("Usage: patch_upstream.py /path/to/pinned/upstream/deploy/install-mtproxy.sh")
    target = Path(sys.argv[1])
    original = target.read_text(encoding="utf-8")
    modified = patch_source(original)
    if modified != original:
        target.write_text(modified, encoding="utf-8")
        print("[ok] Pinned upstream download step patched with official-first Telegram fallback")
    else:
        print("[ok] Pinned upstream downloader already patched")


if __name__ == "__main__":
    main()
