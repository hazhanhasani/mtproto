#!/usr/bin/env python3
"""Read-only, secret-redacted end-to-end WEB relay diagnostics on an installed VPS.

Verifies the configured relay, each MTProxy backend TCP socket, and the
authenticated WEB bridge response over a locally resolved, trusted TLS SNI.
Does not rotate or print any secret/capability; does not modify services.
"""
import base64
import hashlib
import hmac
import http.client
import json
import socket
import ssl
import subprocess
import sys
from pathlib import Path

from manager.model import validate

STATE = Path("/var/lib/blue-mtproxy/state.json")
RELAY = Path("/etc/tproxy-server/profiles.json")


def bridge_capability(host: str, hex_secret: str) -> str:
    message = ("tdesktop-web-proxy-bridge-v1\n" + host).encode()
    digest = hmac.new(bytes.fromhex(hex_secret), message, hashlib.sha256).digest()
    return base64.urlsafe_b64encode(digest).decode().rstrip("=")


class TrustedLocalHTTPS(http.client.HTTPSConnection):
    """Connect to localhost but verify the requested public hostname and CA."""

    def connect(self):
        plain = socket.create_connection(("127.0.0.1", 443), timeout=self.timeout)
        try:
            self.sock = self._context.wrap_socket(plain, server_hostname=self.host)
        except Exception:
            plain.close()
            raise


def web_bridge_status(host: str, hex_secret: str) -> tuple[bool, str]:
    capability = bridge_capability(host, hex_secret)
    conn = TrustedLocalHTTPS(host, timeout=9, context=ssl.create_default_context())
    try:
        conn.request("GET", "/?bridge=" + capability)
        res = conn.getresponse()
        body = res.read(524288)
        if res.status == 200 and b"const relayBase=" in body:
            return True, "authenticated bridge responded over trusted TLS"
        return False, f"bridge unavailable (HTTP {res.status}; possibly wrong secret/profile)"
    except (OSError, ssl.SSLError, http.client.HTTPException) as exc:
        return False, f"bridge transport/TLS error: {type(exc).__name__}"
    finally:
        conn.close()


def tcp_listening(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=3):
            return True
    except OSError:
        return False


def service_state(unit: str) -> str:
    try:
        result = subprocess.run(
            ["systemctl", "is-active", unit],
            capture_output=True, text=True, timeout=5, check=False,
        )
        return result.stdout.strip() or "unknown"
    except (OSError, subprocess.TimeoutExpired):
        return "unknown"


def main():
    if not STATE.is_file() or not RELAY.is_file():
        print("[error] Installation configuration was not found. Do not reinstall.")
        return 2
    state = validate(json.loads(STATE.read_text(encoding="utf-8")))
    profiles = json.loads(RELAY.read_text(encoding="utf-8")).get("profiles", [])
    deployed = {p.get("name"): p for p in profiles}
    host = state["hostname"]
    bad = False
    print("WEB proxy diagnostics (no Secret values are displayed)")
    print(f"Configured hostname: {host}")
    for unit in ("caddy", "tproxy-server"):
        status = service_state(unit + ".service")
        print(f"[service] {unit}: {status}")
        bad |= status != "active"
    try:
        with __import__("urllib.request", fromlist=["urlopen"]).urlopen(
            "http://127.0.0.1:8081/readyz", timeout=4
        ) as response:
            relay_ready = response.status == 200
    except (OSError, ValueError):
        relay_ready = False
    print(f"[relay] readiness: {'OK' if relay_ready else 'FAIL'}")
    bad |= not relay_ready
    for p in state["profiles"]:
        name = p["id"]
        if not p["enabled"]:
            print(f"[{name}] disabled in admin panel")
            continue
        port = 10443 + p["slot"]
        unit_state = service_state(f"blue-mtproxy@{name}.service")
        socket_ok = tcp_listening(port)
        effective = deployed.get(name)
        configured = bool(
            effective
            and effective.get("secret") == p["secret"]
            and effective.get("backend") == f"127.0.0.1:{port}"
        )
        bridge_ok, bridge_message = web_bridge_status(host, p["secret"])
        print(f"[{name}] backend systemd: {unit_state}")
        print(f"[{name}] backend local TCP {port}: {'OK' if socket_ok else 'FAIL'}")
        print(f"[{name}] relay profile matches panel: {'OK' if configured else 'FAIL'}")
        print(f"[{name}] WEB bridge: {'OK' if bridge_ok else 'FAIL'} - {bridge_message}")
        bad |= not (unit_state == "active" and socket_ok and configured and bridge_ok)
    print()
    if bad:
        print("RESULT: At least one server-side WEB component failed.")
        print("Inspect: sudo journalctl -u tproxy-server -u 'blue-mtproxy@main' -n 60 --no-pager")
        return 1
    print("RESULT: Server-side checks passed; client compatibility and a real Telegram connection are still unverified.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
        print(f"[error] {type(exc).__name__}: check configuration integrity; no secrets shown.")
        sys.exit(2)
