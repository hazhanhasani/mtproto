"""Offline regression checks for root /debug report and protected panel endpoints."""
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from manager import debug


def config():
    return {
        "version": 1, "hostname": "proxy.example.com",
        "panel_hostname": "panel.example.com", "auto_update": True,
        "profiles": [
            {"id": "main", "slot": 0, "label": "Main",
             "secret": "1234567890abcdef1234567890abcdef",
             "ad_tag": "", "channel": "", "enabled": True,
             "carrier": "websocket"}
        ],
    }


class DiagnosticsTests(unittest.TestCase):
    def test_read_only_redacted_report(self):
        with tempfile.TemporaryDirectory() as d:
            state = Path(d, "state.json")
            relay = Path(d, "profiles.json")
            cfg = config()
            state.write_text(json.dumps(cfg), encoding="utf-8")
            relay.write_text(json.dumps({
                "profiles": [{"name": "main", "secret": cfg["profiles"][0]["secret"],
                              "backend": "127.0.0.1:10443", "carrier_mode": "websocket"}]
            }), encoding="utf-8")
            with patch.object(debug, "STATE_PATH", state), patch.object(debug, "RELAY_PATH", relay), \
                 patch.object(debug, "service_active", return_value=True), \
                 patch.object(debug, "local_socket", return_value=True), \
                 patch.object(debug, "relay_ready", return_value=True), \
                 patch.object(debug, "bridge_status", return_value="ok"), \
                 patch.object(debug, "middle_end_warning", return_value=False):
                report = debug.collect()
            self.assertEqual(report["overall"], "partial")
            self.assertEqual(report["profiles"][0]["mtproxy_tcp"], "ok")
            self.assertEqual(report["profiles"][0]["relay_config"], "ok")
            self.assertEqual(report["profiles"][0]["web_bridge"], "ok")
            self.assertNotIn(cfg["profiles"][0]["secret"], json.dumps(report))
            self.assertNotIn("bridge=", json.dumps(report))
            self.assertNotIn("/var/lib/", json.dumps(report))
            self.assertIn("web_client_support", [r["code"] for r in report["checks"]])

    def test_failure_from_backend(self):
        with tempfile.TemporaryDirectory() as d:
            state = Path(d, "state.json")
            relay = Path(d, "profiles.json")
            cfg = config()
            state.write_text(json.dumps(cfg), encoding="utf-8")
            relay.write_text(json.dumps({"profiles": []}), encoding="utf-8")
            with patch.object(debug, "STATE_PATH", state), patch.object(debug, "RELAY_PATH", relay), \
                 patch.object(debug, "service_active", return_value=False), \
                 patch.object(debug, "local_socket", return_value=False), \
                 patch.object(debug, "relay_ready", return_value=False), \
                 patch.object(debug, "bridge_status", return_value="fail"), \
                 patch.object(debug, "middle_end_warning", return_value=False):
                report = debug.collect()
            self.assertEqual(report["overall"], "fail")
            self.assertEqual(report["profiles"][0]["relay_config"], "fail")

    def test_invalid_config_will_not_leak_secret(self):
        with tempfile.TemporaryDirectory() as d:
            state = Path(d, "state.json")
            state.write_text("a-secret-that-must-not-appear", encoding="utf-8")
            with patch.object(debug, "STATE_PATH", state):
                # CLI wrapper catches invalid config without printing contents.
                from io import StringIO
                with patch("sys.stdout", new_callable=StringIO) as output, \
                     patch.object(sys, "argv", ["debug.py"]):
                    debug.main()
                self.assertNotIn("a-secret-that-must-not-appear", output.getvalue())
                self.assertIn("configuration", output.getvalue())


class PrivatePanelTests(unittest.TestCase):
    def setUp(self):
        os.environ.setdefault("PANEL_SECRET_KEY", "unit-test-key-" + "x" * 36)
        from werkzeug.security import generate_password_hash
        os.environ.setdefault("PANEL_ADMIN_PASSWORD_HASH", generate_password_hash("test-safe-password", method="scrypt"))
        import app
        self.admin = app
        self.admin.app.config["TESTING"] = True
        self.client = self.admin.app.test_client()
        self.url = "https://panel.example.com"

    def test_debug_cannot_be_read_without_login(self):
        for path in ("/debug", "/debug.json"):
            result = self.client.get(path, base_url=self.url)
            self.assertEqual(result.status_code, 302)
            self.assertIn("/login", result.headers["Location"])

    def test_debug_redacts_and_does_not_cache(self):
        self.client.get("/login", base_url=self.url)
        with self.client.session_transaction(base_url=self.url) as sess:
            sess["authenticated"] = True
        payload = {
            "schema": 1, "overall": "partial", "checks": [
                {"code": "relay_ready", "status": "ok", "message": "Relay healthy"}],
            "profiles": [], "note": "Client compatibility unverified",
        }
        with patch.object(self.admin, "collect_diagnostics", return_value=payload):
            res = self.client.get("/debug", base_url=self.url)
            self.assertEqual(res.status_code, 200)
            self.assertIn("Blue Proxy /debug", res.get_data(as_text=True))
            self.assertIn("no-store", res.headers["Cache-Control"])
            result = self.client.get("/debug.json", base_url=self.url)
        self.assertEqual(result.json["overall"], "partial")
        self.assertIn("no-store", result.headers["Cache-Control"])


if __name__ == "__main__":
    unittest.main()
