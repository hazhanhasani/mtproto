#!/usr/bin/env python3
"""Root-only /debug snapshot publisher.

Executes existing read-only probes in a transient systemd service, then writes
a minimal, sanitized report to a root-owned, bluepanel-readable cache. The Flask
web server never calls sudo or any other privileged program.
"""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
import tempfile

try:
    from .debug import collect, check
except ImportError:  # root systemd script is invoked by file path
    from debug import collect, check

CACHE_DIR = Path("/var/cache/blue-mtproxy-debug")
CACHE = CACHE_DIR / "report.json"


def sanitized_error():
    return {
        "schema": 1,
        "overall": "fail",
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "checks": [check("collector", "fail",
                         "عیب‌یاب نتوانست تنظیمات سرور را بررسی کند؛ ژورنال سرویس را بررسی کنید")],
        "profiles": [],
        "note": "گزارش بدون اطلاعات محرمانه تولید شد. اجرای مستقیم debug.py ممکن است کد خروجی مفید بدهد.",
    }


def publish(snapshot, directory=CACHE_DIR):
    import grp
    if os.geteuid() != 0:
        raise PermissionError("debug publisher is a root-only systemd task")
    # Never store the raw state, process output, exception details or secrets.
    if snapshot.get("schema") != 1 or not isinstance(snapshot.get("checks"), list):
        raise ValueError("unexpected diagnostic schema")
    expected_keys = {"schema", "generated_at", "overall", "checks", "profiles", "note"}
    if not set(snapshot).issubset(expected_keys):
        raise ValueError("unexpected diagnostic fields")
    directory.mkdir(mode=0o750, parents=True, exist_ok=True)
    gid = grp.getgrnam("bluepanel").gr_gid
    os.chown(directory, 0, gid)
    os.chmod(directory, 0o750)
    payload = (json.dumps(snapshot, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
    if len(payload) > 32768:
        raise ValueError("diagnostic report too large")
    fd, pending = tempfile.mkstemp(prefix=".debug-", dir=directory)
    try:
        with os.fdopen(fd, "wb") as out:
            out.write(payload)
            out.flush()
            os.fsync(out.fileno())
        os.chown(pending, 0, gid)
        os.chmod(pending, 0o640)
        os.replace(pending, directory / "report.json")
    finally:
        if os.path.exists(pending):
            os.unlink(pending)


def main():
    if len(sys.argv) != 1:
        return 2
    try:
        snapshot = collect()
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
        snapshot = sanitized_error()
    try:
        publish(snapshot)
    except (OSError, ValueError, KeyError) as exc:
        # Keep logs free of potentially sensitive paths and exception details.
        print("Failed to write sanitized debug report", file=sys.stderr)
        return 1
    print("Updated secure Blue Proxy diagnostics cache")
    return 0


if __name__ == "__main__":
    sys.exit(main())
