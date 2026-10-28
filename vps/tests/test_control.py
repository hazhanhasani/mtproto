"""Least-privilege control broker and panel integration regression tests."""
import json
import os
from pathlib import Path
import socket
import struct
import sys
import tempfile
import unittest
from unittest.mock import call, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from manager import control
from manager.control_client import dispatch


class ProtocolTests(unittest.TestCase):
    def test_only_approved_exact_commands(self):
        for action in ("apply", "update"):
            payload = json.dumps({"version": 1, "action": action}).encode() + b"\n"
            self.assertEqual(control.decode_request(payload), action)

        for payload in (
            b'{"version":1,"action":"delete"}\n',
            b'{"version":1,"action":"apply","command":"rm -rf /"}\n',
            b'{"version":true,"action":"apply"}\n',
            b'{"version":1,"action":"apply"}',
            b'{"version":1,"action":"apply"}\n' + b"x" * 300,
            b"hello\n",
            b"",
        ):
            with self.subTest(payload=payload[:48]):
                with self.assertRaises(ValueError):
                    control.decode_request(payload)

    def test_peer_credentials_required(self):
        self.assertTrue(control.authorize_peer(123, 123))
        self.assertTrue(control.authorize_peer(0, 123))
        self.assertFalse(control.authorize_peer(124, 123))

    def test_only_explicit_systemd_action(self):
        with patch.object(control.subprocess, "run") as run:
            control.run_action("ping")
            run.assert_not_called()
            control.run_action("apply")
            self.assertEqual(run.call_args.args[0], [
                "/usr/bin/systemctl", "start", "blue-mtproxy-apply.service"
            ])
            self.assertTrue(run.call_args.kwargs["check"])
            control.run_action("update")
            self.assertEqual(run.call_args.args[0], [
                "/usr/bin/systemctl", "start", "--no-block",
                "blue-mtproxy-update-force.service"
            ])
        with self.assertRaises(ValueError):
            control.run_action("arbitrary")

    def test_broker_does_not_run_reconciler_directly(self):
        source = (ROOT / "manager" / "control.py").read_text(encoding="utf-8")
        self.assertNotIn("reconcile()", source)
        self.assertIn('blue-mtproxy-apply.service', source)
        installer = (ROOT / "install-control.sh").read_text(encoding="utf-8")
        self.assertIn("Type=oneshot", installer)
        self.assertIn("ExecStart=/usr/local/sbin/blue-mtproxy-apply", installer)
        self.assertIn('dispatch("apply", timeout=118)', installer)

    def test_socket_peer_and_response(self):
        c, s = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
        with c:
            with patch.object(control, "run_action") as run:
                c.sendall(b'{"version":1,"action":"apply"}\n')
                control.serve_connection(s, os.getuid())
                answer = json.loads(c.recv(512))
            self.assertEqual(answer, {"ok": True})
            run.assert_called_once_with("apply")

    def test_reject_wrong_uid(self):
        # Running this test as root must not turn the negative case into
        # an allowed root peer. The broker intentionally authorizes UID 0;
        # inject a synthetic non-root SO_PEERCRED response instead.
        class PeerSocket:
            def __init__(self, sock, uid):
                self.sock = sock
                self.uid = uid

            def __enter__(self):
                return self

            def __exit__(self, *args):
                self.sock.close()

            def getsockopt(self, level, option, length):
                if (level, option, length) == (socket.SOL_SOCKET, socket.SO_PEERCRED, 12):
                    return struct.pack("3i", os.getpid(), self.uid, os.getgid())
                return self.sock.getsockopt(level, option, length)

            def __getattr__(self, name):
                return getattr(self.sock, name)

        c, s = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
        with c:
            with patch.object(control, "run_action") as run:
                c.sendall(b'{"version":1,"action":"apply"}\n')
                control.serve_connection(PeerSocket(s, 1002), panel_uid=1001)
                answer = json.loads(c.recv(512))
            self.assertEqual(answer, {"ok": False, "error": "operation_failed"})
            run.assert_not_called()


class ClientTests(unittest.TestCase):
    def test_unknown_action_cannot_reach_broker(self):
        with self.assertRaises(ValueError):
            dispatch("systemctl reboot")

    def test_valid_request_and_response_without_sudo(self):
        class FakeSocket:
            sent = b""
            connected = None

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def settimeout(self, timeout):
                pass

            def connect(self, path):
                self.connected = path

            def sendall(self, data):
                self.sent = data

            def recv(self, max_bytes):
                return (json.dumps({"ok": True}) + "\n").encode()

        fake = FakeSocket()
        with patch("manager.control_client.socket.socket", return_value=fake):
            dispatch("apply", timeout=2)
        self.assertEqual(json.loads(fake.sent), {"version": 1, "action": "apply"})
        self.assertTrue(fake.connected.endswith("command.sock"))

class PanelOperationsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from werkzeug.security import generate_password_hash
        os.environ.setdefault("PANEL_SECRET_KEY", "unit-test-secret-" + "a" * 40)
        os.environ.setdefault("PANEL_ADMIN_PASSWORD_HASH",
                              generate_password_hash("test-only-password", method="scrypt"))
        import app
        cls.app_module = app

    def test_mutate_applies_via_broker_not_sudo(self):
        app = self.app_module
        initial = {"version": 1, "profiles": []}
        with tempfile.TemporaryDirectory() as d, \
             patch.object(app, "LOCK", Path(d, "ui.lock")), \
             patch.object(app, "load_state", return_value=initial), \
             patch.object(app, "validate"), \
             patch.object(app, "write_state") as write, \
             patch.object(app, "dispatch") as command, \
             patch.object(app.subprocess, "run", side_effect=AssertionError("sudo forbidden")):
            app.apply_mutation(lambda state: state["profiles"].append({"id": "sample"}))
        command.assert_called_once_with("apply")
        self.assertEqual(write.call_args.args[0]["profiles"][0]["id"], "sample")

    def test_mutation_rollback_on_broker_failure(self):
        app = self.app_module
        original = {"version": 1, "profiles": []}
        with tempfile.TemporaryDirectory() as d, \
             patch.object(app, "LOCK", Path(d, "ui.lock")), \
             patch.object(app, "load_state", return_value=original), \
             patch.object(app, "validate"), \
             patch.object(app, "write_state") as write, \
             patch.object(app, "dispatch", side_effect=[RuntimeError("apply failed"), None]) as command:
            with self.assertRaises(RuntimeError):
                app.apply_mutation(lambda st: st["profiles"].append({"id": "sample"}))
        self.assertEqual(command.call_count, 2)
        self.assertEqual(write.call_count, 2)
        self.assertEqual(write.call_args.args[0], original)


if __name__ == "__main__":
    unittest.main()
