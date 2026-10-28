#!/usr/bin/env python3
"""Privileged, read-only Blue Proxy Manager diagnostics.

Prints ONLY structured, redacted JSON. Never emits secrets, bridge capabilities,
full request URLs, environment values, process args, config bytes or journal lines.
Designed to run through an exact no-argument sudoers command from the admin panel.
"""
import base64
from datetime import datetime, timezone
import hashlib
import hmac
import http.client
import json
from pathlib import Path
import socket
import ssl
import subprocess
import sys
from urllib.error import HTTPError, URLError
from urllib.request import urlopen

try:
    from .model import validate
    from .telemetry import inbound_connections
except ImportError:  # direct root-owned command-line entrypoint
    from model import validate
    from telemetry import inbound_connections

STATE_PATH = Path("/var/lib/blue-mtproxy/state.json")
RELAY_PATH = Path("/etc/tproxy-server/profiles.json")
MAX_BRIDGE_PROBES = 6
MAX_CONNECTION_PROBES = 12  # Bound snapshot time under the 65s systemd timeout


def check(code, status, message):
    return {"code": code, "status": status, "message": message}


def service_active(name):
    try:
        result = subprocess.run(
            ["systemctl", "is-active", "--quiet", name],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            check=False, timeout=3,
        )
        return result.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def local_socket(port):
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=1):
            return True
    except OSError:
        return False


def relay_ready():
    try:
        with urlopen("http://127.0.0.1:8081/readyz", timeout=3) as response:
            return response.status == 200
    except (HTTPError, URLError, ValueError, TimeoutError, OSError):
        return False


class LocalHTTPS(http.client.HTTPSConnection):
    """Connect to local Caddy while validating the actual public TLS hostname."""

    def connect(self):
        sock = socket.create_connection(("127.0.0.1", 443), timeout=self.timeout)
        try:
            self.sock = self._context.wrap_socket(sock, server_hostname=self.host)
        except Exception:
            sock.close()
            raise


def bridge_status(host, secret):
    """An authenticated bridge is stronger evidence than the public homepage."""
    context = ("tdesktop-web-proxy-bridge-v1\n" + host).encode("utf-8")
    digest = hmac.new(bytes.fromhex(secret), context, hashlib.sha256).digest()
    capability = base64.urlsafe_b64encode(digest).decode().rstrip("=")
    conn = LocalHTTPS(host, timeout=4, context=ssl.create_default_context())
    try:
        # Capability never appears in logs or returned diagnostics.
        conn.request("GET", "/?bridge=" + capability)
        response = conn.getresponse()
        body = response.read(262144)
        if response.status == 200 and b"const relayBase=" in body:
            return "ok"
        return "fail"
    except (OSError, ssl.SSLError, http.client.HTTPException, TimeoutError):
        return "fail"
    finally:
        conn.close()


def middle_end_warning():
    """Only a bounded count; never return potentially sensitive journal content."""
    try:
        log = subprocess.run(
            ["journalctl", "-u", "blue-mtproxy@main.service",
             "--since", "-10 minutes", "-n", "120", "-o", "cat", "--no-pager"],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            timeout=4, check=False,
        ).stdout[:100_000]
        return log.count(b"Disconnected from RPC Middle-End") >= 3
    except (OSError, subprocess.TimeoutExpired):
        return False


def collect():
    state = validate(json.loads(STATE_PATH.read_text(encoding="utf-8")))
    deployed = json.loads(RELAY_PATH.read_text(encoding="utf-8"))
    relay_profiles = {p["name"]: p for p in deployed["profiles"]}
    checks = []
    profiles = []

    for name in ("caddy.service", "tproxy-server.service", "blue-mtproxy-panel.service", "blue-mtproxy-control.service"):
        healthy = service_active(name)
        checks.append(check("service_" + name.split(".")[0].replace("-", "_"),
                            "ok" if healthy else "fail",
                            "سرویس فعال است" if healthy else "سرویس اجرا نمی‌شود"))
    # This is the privileged *apply* job, distinct from the healthy socket
    # broker. Only fixed systemd properties are read; no raw logs or args.
    try:
        job = subprocess.run(
            ["systemctl", "show", "blue-mtproxy-apply.service",
             "--property=Result", "--property=ExecMainStatus", "--property=ActiveState"],
            capture_output=True, text=True, timeout=4, check=False,
        )
        props = dict(line.split("=", 1) for line in job.stdout.splitlines()
                     if "=" in line)
        apply_result = props.get("Result", "unknown")
        if job.returncode != 0 or apply_result not in ("success", "exit-code", "timeout", "signal"):
            checks.append(check("control_apply", "warn",
                                "نتیجه عملیات ذخیره مشخص نیست؛ سرویس apply را بررسی کنید"))
        elif apply_result == "success":
            checks.append(check("control_apply", "ok",
                                "آخرین عملیات سرویس اعمال تنظیمات بدون خطا پایان یافته است"))
        else:
            checks.append(check("control_apply", "fail",
                                "عملیات اعمال تنظیمات با خطا متوقف شده؛ گزارش blue-mtproxy-apply را بررسی کنید"))
    except (OSError, subprocess.TimeoutExpired, ValueError):
        checks.append(check("control_apply", "warn",
                            "وضعیت سرویس اعمال تنظیمات قابل خواندن نیست"))

    ready = relay_ready()
    checks.append(check("relay_ready", "ok" if ready else "fail",
                        "Relay آماده اتصال است" if ready else "Relay آماده نیست"))

    eligible = [p for p in state["profiles"] if p["enabled"]]
    for index, p in enumerate(eligible):
        port = 10443 + p["slot"]
        current = relay_profiles.get(p["id"], {})
        consistent = (
            current.get("secret") == p["secret"]
            and current.get("backend") == f"127.0.0.1:{port}"
            and current.get("carrier_mode") == p.get("carrier", "websocket")
        )
        unit_ok = service_active("blue-mtproxy@" + p["id"] + ".service")
        socket_ok = local_socket(port)
        # Bounded live HTTP probes; 32 profiles would otherwise exceed the
        # Gunicorn request timeout and lock up the admin interface.
        bridge = bridge_status(state["hostname"], p["secret"]) if index < MAX_BRIDGE_PROBES else "skipped"
        profiles.append({
            "id": p["id"],
            "port": port,
            # Aggregate inbound value, never per-user or guessed traffic.
            "active_connections": (
                inbound_connections(p["slot"], timeout=1.2)
                if socket_ok and index < MAX_CONNECTION_PROBES else None
            ),
            "carrier": p.get("carrier", "websocket"),
            "mtproxy_service": "ok" if unit_ok else "fail",
            "mtproxy_tcp": "ok" if socket_ok else "fail",
            "relay_config": "ok" if consistent else "fail",
            "web_bridge": bridge,
            "ad_tag_configured": bool(p["ad_tag"]),
        })
    if len(eligible) > MAX_BRIDGE_PROBES:
        checks.append(check("probe_budget", "warn",
                            "آزمون زنده WEB فقط برای شش اینباند نخست اجرا شد"))
    if len(eligible) > MAX_CONNECTION_PROBES:
        checks.append(check("stats_budget", "warn",
                            "شمارنده اتصال فقط برای ۱۲ اینباند نخست بررسی شد؛ سایر موارد ناموجود هستند"))
    if middle_end_warning():
        checks.append(check("nat_middle_end", "warn",
                            "قطع مکرر اتصال Middle-End مشاهده شد؛ NAT و --nat-info را بررسی کنید"))
    # A verified server bridge cannot establish client feature support or
    # Telegram-session connectivity. Do not label client readiness as OK.
    checks.append(check("web_client_support", "warn",
                        "WEB آزمایشی است؛ اتصال کلاینت تلگرام و نمایش اسپانسر تأیید نشده"))
    checks.append(check("mtproto_external_port", "warn",
                        "دسترسی بیرونی به پورت MTProto باید از شبکه دیگری بررسی شود"))

    failures = sum(v["status"] == "fail" for v in checks)
    failures += sum(v[k] == "fail" for v in profiles
                    for k in ("mtproxy_service", "mtproxy_tcp", "relay_config", "web_bridge"))
    return {
        "schema": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "overall": "fail" if failures else "partial",
        "checks": checks,
        "profiles": profiles,
        "note": "هیچ تستی جایگزین اتصال واقعی با کلاینت سازگار WEB یا MTProto نیست.",
    }


def main():
    if len(sys.argv) != 1:
        raise SystemExit(2)
    try:
        result = collect()
    except (OSError, ValueError, KeyError, json.JSONDecodeError, TypeError):
        result = {
            "schema": 1, "overall": "fail",
            "checks": [check("configuration", "fail", "خواندن تنظیمات معتبر نبود؛ گزارش سرویس‌ها را بررسی کنید")],
            "profiles": [],
            "note": "تنظیمات نامعتبر است؛ اطلاعات محرمانه نمایش داده نشده است.",
        }
    print(json.dumps(result, ensure_ascii=False, separators=(",", ":")))


if __name__ == "__main__":
    main()
