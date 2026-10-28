"""Regression tests for isolated root debug publisher and Flask cache reader."""
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from werkzeug.security import generate_password_hash
os.environ.setdefault("PANEL_SECRET_KEY", "local-only-debug-test-key-" + "z" * 24)
os.environ.setdefault("PANEL_ADMIN_PASSWORD_HASH", generate_password_hash("local-only-password", method="scrypt"))
import app
from manager import write_debug_cache


def report():
    return {
        "schema": 1, "generated_at": "2026-10-28T08:00:00+00:00",
        "overall": "partial",
        "checks": [{"code": "relay_ready", "status": "ok", "message": "healthy"}],
        "profiles": [{"id": "main", "port": 10443, "carrier": "websocket",
                      "mtproxy_tcp": "ok", "mtproxy_service": "ok",
                      "relay_config": "ok", "web_bridge": "ok", "ad_tag_configured": False}],
        "note": "No actual Telegram client connection verified",
    }


class CacheReaderTests(unittest.TestCase):
    def test_reads_a_fresh_redacted_report_with_no_sudo(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d, "report.json")
            path.write_text(json.dumps(report()), encoding="utf-8")
            with patch.object(app, "DEBUG_CACHE", path), \
                 patch.object(app, "current_time", return_value=path.stat().st_mtime + 60), \
                 patch.object(app.subprocess, "run", side_effect=AssertionError("Web process must not run privileged processes")):
                response = app.collect_diagnostics()
            self.assertEqual(response["checks"][0]["status"], "ok")
            self.assertEqual(response["profiles"][0]["web_bridge"], "ok")

    def test_missing_cache_has_precise_error(self):
        with tempfile.TemporaryDirectory() as d:
            with patch.object(app, "DEBUG_CACHE", Path(d, "not-found.json")):
                self.assertEqual(app.collect_diagnostics()["checks"][0]["code"], "cache_missing")

    def test_stale_cache_has_precise_error(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d, "report.json")
            path.write_text(json.dumps(report()), encoding="utf-8")
            with patch.object(app, "DEBUG_CACHE", path), \
                 patch.object(app, "current_time", return_value=path.stat().st_mtime + 301):
                self.assertEqual(app.collect_diagnostics()["checks"][0]["code"], "cache_stale")

    def test_invalid_json_is_safe(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d, "report.json")
            path.write_text("secret=must-not-leak", encoding="utf-8")
            with patch.object(app, "DEBUG_CACHE", path):
                response = app.collect_diagnostics()
            self.assertEqual(response["checks"][0]["code"], "cache_invalid")
            self.assertNotIn("must-not-leak", json.dumps(response))


class PublisherTests(unittest.TestCase):
    def test_failure_snapshot_is_redacted(self):
        snapshot = write_debug_cache.sanitized_error()
        serialized = json.dumps(snapshot)
        self.assertIn("collector", serialized)
        self.assertNotIn("secret", serialized.lower())

    def test_publisher_uses_safe_permissions_and_atomic_file(self):
        with tempfile.TemporaryDirectory() as d, \
             patch.object(write_debug_cache.os, "geteuid", return_value=0), \
             patch.object(write_debug_cache.os, "chown"), \
             patch("grp.getgrnam", return_value=type("G", (), {"gr_gid": 123})()):
            write_debug_cache.publish(report(), directory=Path(d, "root-cache"))
            dest = Path(d, "root-cache", "report.json")
            self.assertEqual(dest.stat().st_mode & 0o777, 0o640)
            self.assertEqual(json.loads(dest.read_text())["overall"], "partial")


if __name__ == "__main__":
    unittest.main()
