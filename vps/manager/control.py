"""Minimal root-owned UNIX socket broker for privileged proxy operations.

Flask always runs with NoNewPrivileges=yes and cannot use sudo. Only the
bluepanel UNIX UID can request two fixed operations via a group-only socket.
No shell, arbitrary paths, flags, secrets, or input configuration are accepted.
"""
import grp
import json
import os
from pathlib import Path
import pwd
import signal
import socket
import stat
import struct
import subprocess
import sys

from apply import reconcile

SOCKET_PATH = Path("/run/blue-mtproxy-control/command.sock")
MAX_REQUEST = 256
MAX_RESPONSE = 512
ACTIONS = frozenset(("apply", "update"))


def decode_request(data):
    if len(data) > MAX_REQUEST or not data.endswith(b"\n"):
        raise ValueError("invalid request framing")
    try:
        request = json.loads(data)
    except (UnicodeDecodeError, ValueError) as exc:
        raise ValueError("invalid JSON") from exc
    if (type(request) is not dict or set(request) != {"version", "action"}
            or type(request["version"]) is not int or request["version"] != 1
            or type(request["action"]) is not str
            or request["action"] not in ACTIONS):
        raise ValueError("unsupported operation")
    return request["action"]


def authorize_peer(peer_uid, panel_uid):
    # Exact UID check, not group membership or user-supplied tokens.
    return peer_uid == panel_uid or peer_uid == 0


def run_action(action):
    if action == "apply":
        reconcile()
    elif action == "update":
        subprocess.run(
            ["/usr/bin/systemctl", "start", "--no-block",
             "blue-mtproxy-update-force.service"],
            check=True, timeout=12, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    else:
        raise ValueError("unsupported operation")


def serve_connection(conn, panel_uid):
    with conn:
        conn.settimeout(4)
        try:
            creds = conn.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12)
            _pid, peer_uid, _gid = struct.unpack("3i", creds)
            if not authorize_peer(peer_uid, panel_uid):
                raise PermissionError("invalid peer")
            # Read only a single tiny, newline-framed, fixed-shape command.
            data = bytearray()
            while b"\n" not in data and len(data) <= MAX_REQUEST:
                chunk = conn.recv(MAX_REQUEST + 1 - len(data))
                if not chunk:
                    break
                data.extend(chunk)
            action = decode_request(bytes(data))
            conn.settimeout(120)
            run_action(action)
            result = {"ok": True}
        except (OSError, PermissionError, ValueError, subprocess.CalledProcessError,
                subprocess.TimeoutExpired) as exc:
            # No stderr/traceback/commands returned to browser: logs may
            # contain privileged service configuration or secret parameters.
            print("Control command failed: " + type(exc).__name__, file=sys.stderr)
            result = {"ok": False, "error": "operation_failed"}
        try:
            conn.sendall((json.dumps(result, separators=(",", ":")) + "\n").encode())
        except OSError:
            pass


def serve_forever():
    if os.geteuid() != 0:
        raise PermissionError("control broker must run as root")
    uid = pwd.getpwnam("bluepanel").pw_uid
    gid = grp.getgrnam("bluepanel").gr_gid
    directory = SOCKET_PATH.parent
    if not directory.is_dir() or directory.is_symlink():
        raise RuntimeError("unsafe control socket directory")
    if SOCKET_PATH.exists() or SOCKET_PATH.is_symlink():
        info = SOCKET_PATH.lstat()
        if not stat.S_ISSOCK(info.st_mode) or info.st_uid != 0:
            raise RuntimeError("unsafe existing socket path")
        SOCKET_PATH.unlink()

    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        server.bind(str(SOCKET_PATH))
        os.chown(SOCKET_PATH, 0, gid)
        os.chmod(SOCKET_PATH, 0o660)
        server.listen(16)
        server.settimeout(1)
        interrupted = False

        def stop(_signum, _frame):
            nonlocal interrupted
            interrupted = True

        signal.signal(signal.SIGTERM, stop)
        signal.signal(signal.SIGINT, stop)
        while not interrupted:
            try:
                conn, _addr = server.accept()
            except socket.timeout:
                continue
            serve_connection(conn, uid)
    finally:
        server.close()
        try:
            if SOCKET_PATH.is_socket():
                SOCKET_PATH.unlink()
        except OSError:
            pass


if __name__ == "__main__":
    serve_forever()
