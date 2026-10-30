"""Node enrollment preflight and failed-handshake regression tests."""
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError, URLError

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from manager import enrollment, node_agent


class NodeEnrollmentTests(unittest.TestCase):
    def setUp(self):
        self.state = {
            "version": 1, "hostname": "node.example.com",
            "panel_hostname": "panel.node.example.com",
            "auto_update": True, "profiles": [
                {"id": "main", "slot": 0, "label": "Main", "secret": "a" * 32,
                 "enabled": True, "ad_tag": "", "channel": "",
                 "carrier": "websocket"}
            ]
        }
        self.desired = {
            "version": 1, "node_id": "nabcdef123456", "revision": 1,
            "hostname": "node.example.com",
            "domains": ["node.example.com"], "profiles": []
        }

    def test_http_502_hint_uses_central_server_without_leaking_tokens(self):
        problem = ValueError("Controller rejected node enrollment: HTTP 502")
        hint = enrollment.enrollment_hint(problem)
        self.assertIn("CENTRAL", hint)
        self.assertIn("recover-controller.sh", hint)
        self.assertNotIn("Bearer", hint)
        self.assertIn("node ID", enrollment.enrollment_hint(
            ValueError("Controller rejected node enrollment: HTTP 401")))

    def test_reject_controller_loopback_identity_before_contact(self):
        self.state["panel_hostname"] = "mde.blluepanel.ir"
        contacted = []
        with self.assertRaisesRegex(ValueError, "central panel"):
            enrollment.preflight("https://mde.blluepanel.ir",
                                 "nabcdef123456", "testtoken" * 5,
                                 self.state, lambda *args: contacted.append(args))
        self.assertEqual(contacted, [])

    def test_https_origin_restrictions(self):
        for address in ("http://controller.example.com", "https://controller.example.com/panel",
                        "https://controller.example.com:8443",
                        "https://user:pass@controller.example.com"):
            with self.subTest(address=address), self.assertRaises(ValueError):
                enrollment.controller_is_local(address, self.state)

    def test_reject_401_before_disabling_panel(self):
        def denied(*args):
            raise HTTPError("https://controller.example.com", 401, "unauthorized", None, None)
        with self.assertRaisesRegex(ValueError, "HTTP 401"):
            enrollment.preflight("https://controller.example.com",
                                 "nabcdef123456", "token" * 10,
                                 self.state, denied)

    def test_remote_controller_unreachable(self):
        def offline(*args):
            raise URLError("offline")
        with self.assertRaisesRegex(ValueError, "unreachable"):
            enrollment.preflight("https://controller.example.com",
                                 "nabcdef123456", "token" * 10,
                                 self.state, offline)

    def test_invalid_hostname_rejected(self):
        data = dict(self.desired, hostname="different.example.com")
        with self.assertRaisesRegex(ValueError, "hostname"):
            enrollment.preflight("https://controller.example.com",
                                 "nabcdef123456", "token" * 10,
                                 self.state, lambda *_: data)

    def test_valid_pairing_returns_identical_origin_and_node(self):
        credentials = enrollment.preflight(
            "https://controller.example.com", "nabcdef123456",
            "token" * 10, self.state, lambda *_: self.desired)
        self.assertEqual(credentials["id"], "nabcdef123456")
        self.assertEqual(credentials["controller"], "https://controller.example.com")

    def test_installer_proves_sync_before_turning_off_admin(self):
        script = (ROOT / "install-node.sh").read_text()
        self.assertIn("python3 -m manager.enrollment", script)
        self.assertLess(script.index("python3 -m manager.enrollment"),
                        script.index("systemctl disable --now blue-mtproxy-panel.service"))
        self.assertLess(script.index("if ! systemctl start blue-mtproxy-node.service"),
                        script.index("systemctl disable --now blue-mtproxy-panel.service"))
        self.assertNotIn("systemctl start blue-mtproxy-node.service || true", script)

    def test_agent_does_not_claim_success_if_report_fails(self):
        with tempfile.TemporaryDirectory() as temp:
            missing = Path(temp) / "revision"
            settings = {
                "controller": "https://controller.example.com",
                "id": "nabcdef123456", "token": "token" * 10,
                "original_hostname": "node.example.com"
            }
            def transport(_settings, method, route, body=None):
                if method == "GET":
                    return self.desired
                raise HTTPError("https://controller.example.com", 403, "blocked", None, None)
            with patch.object(node_agent.os, "geteuid", return_value=0), \
                 patch.object(node_agent, "REVISION", missing), \
                 patch.object(node_agent, "load_settings", return_value=settings), \
                 patch.object(node_agent, "request_controller", side_effect=transport), \
                 patch.object(node_agent, "reconcile", return_value="pending"):
                self.assertEqual(node_agent.main(), 1)


if __name__ == "__main__":
    unittest.main()
