"""Restricted IPC transport to the root-owned Blue Proxy control service."""
import json
import socket

CONTROL_SOCKET = "/run/blue-mtproxy-control/command.sock"
ALLOWED_ACTIONS = frozenset({"apply", "update", "ping"})


def dispatch(action, timeout=120):
    if action not in ALLOWED_ACTIONS:
        raise ValueError("عملیات مدیریتی ناشناخته است")
    request = (json.dumps({"version": 1, "action": action},
                          separators=(",", ":")) + "\n").encode()
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as conn:
            conn.settimeout(timeout)
            conn.connect(CONTROL_SOCKET)
            conn.sendall(request)
            data = bytearray()
            while b"\n" not in data and len(data) < 512:
                part = conn.recv(512 - len(data))
                if not part:
                    break
                data.extend(part)
        if not data.endswith(b"\n") or len(data) > 512:
            raise ValueError("invalid control response")
        response = json.loads(data)
        if response == {"ok": True}:
            return
    except (OSError, ValueError) as exc:
        raise RuntimeError(
            "ارتباط با سرویس مدیریت برقرار نشد؛ وضعیت blue-mtproxy-control را بررسی کنید"
        ) from None
    raise RuntimeError(
        "اعمال تنظیمات در سرویس مدیریت ناموفق بود؛ گزارش journalctl سرویس کنترل را بررسی کنید"
    )
