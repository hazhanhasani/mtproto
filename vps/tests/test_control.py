"""Least-privilege control broker and panel integration regression tests."""
import json
import os
from pathlib import Path
import socket
import sys
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
        with patch.object(control, "reconcile") as reconcile, \
             patch.object(control.subprocess, "run") as run:
            control.run_action("apply")
            reconcile.assert_called_once_with()
            run.assert_not_called()
            control.run_action("update")
            cmd = run.call_args.args[0]
            self.assertEqual(cmd, [
                "/usr/bin/systemctl", "start", "--no-block",
                "blue-mtproxy-update-force.service"
            ])
        with self.assertRaises(ValueError):
            control.run_action("arbitrary")

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
        c, s = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
        with c:
            with patch.object(control, "run_action") as run:
                c.sendall(b'{"version":1,"action":"apply"}\n')
                control.serve_connection(s, os.getuid() + 1)
                answer = json.loads(c.recv(512))
            self.assertEqual(answer["ok"], False)
            run.assert_not_called()


class ClientTests(unittest.TestCase):
    def test_unknown_action_cannot_reach_broker(self):
        with self.assertRaises(ValueError):
            dispatch("systemctl reboot")

    def test_valid_request_and_response_without_sudo(self):
        one, two = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
        with patch("manager.control_client.socket.socket", return_value=one):
            # Preload the response in the other end, then read the sent request.
            two.sendall(b'{"ok":true}\n')
            dispatch("apply", timeout=2)
            self.assertEqual(json.loads(two.recv(512))["action"], "apply")
        two.close()


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
        with patch.object(app, "load_state", return_value=initial), \
             patch.object(app, "validate"), \
             patch.object(app, "write_state") as write, \
             patch.object(app, "dispatch") as command, \
             patch.object(app.subprocess, "run", side_effect=AssertionError("sudo forbidden")), \
             patch.object(app.LOCK, "touch"), \
             patch.object(app.LOCK, "open", create=True):
            app.apply_mutation(lambda state: state["profiles"].append({"id": "sample"}))
        command.assert_called_once_with("apply")
        self.assertEqual(write.call_args.args[0]["profiles"][0]["id"], "sample")

    def test_mutation_rollback_on_broker_failure(self):
        app = self.app_module
        original = {"version": 1, "profiles": []}
        with patch.object(app, "load_state", return_value=original), \
             patch.object(app, "validate"), \
             patch.object(app, "write_state") as write, \
             patch.object(app, "dispatch", side_effect=[RuntimeError("apply failed"), None]) as command, \
             patch.object(app.LOCK, "touch"), \
             patch.object(app.LOCK, "open", create=True):
            with self.assertRaises(RuntimeError):
                app.apply_mutation(lambda st: st["profiles"].append({"id": "sample"}))
        self.assertEqual(command.call_count, 2)
        self.assertEqual(write.call_count, 2)
        self.assertEqual(write.call_args.args[0], original)


if __name__ == "__main__":
    unittest.main()
