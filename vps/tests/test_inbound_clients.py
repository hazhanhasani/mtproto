"""Integration regressions for a real inbound and its user credentials."""
import copy
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from werkzeug.security import generate_password_hash

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ["PANEL_SECRET_KEY"] = "unit-test-" + "d" * 36
os.environ["PANEL_ADMIN_PASSWORD_HASH"] = generate_password_hash("correct-test-password")
import app
from manager.model import validate, relay_profiles, web_url
from manager.runner import build_argv
from manager.telemetry import parse_stats


def fixture():
    return {"version": 1, "hostname": "proxy.example.com",
            "panel_hostname": "panel.example.com", "auto_update": True,
            "profiles": [{
                "id": "main", "slot": 0, "label": "Main",
                "secret": "a" * 32, "ad_tag": "", "channel": "",
                "carrier": "websocket", "enabled": True,
            }]}


class UserSecretTests(unittest.TestCase):
    def test_owner_secret_and_client_secret_have_real_backend_access(self):
        state = fixture()
        state["profiles"][0]["clients"] = [{
            "id": "u01234567", "label": "Alice", "secret": "b" * 32,
            "enabled": True,
        }]
        validate(state)
        relay = relay_profiles(state)["profiles"]
        self.assertEqual([p["name"] for p in relay], ["main", "main--u01234567"])
        self.assertEqual(relay[0]["backend"], relay[1]["backend"])
        self.assertEqual(relay[1]["secret"], "b" * 32)
        args = build_argv(state["profiles"][0])
        self.assertEqual(args.count("-S"), 2)
        self.assertIn("b" * 32, args)

        state["profiles"][0]["clients"][0]["enabled"] = False
        validate(state)
        self.assertNotIn("b" * 32, build_argv(state["profiles"][0]))
        self.assertEqual(len(relay_profiles(state)["profiles"]), 1)

    def test_max_profiles_enforced_by_upstream(self):
        state = fixture()
        state["profiles"][0]["clients"] = [{
            "id": "u" + format(i, "08x"), "label": f"Client{i}",
            "secret": format(i + 1, "032x"), "enabled": True,
        } for i in range(31)]
        validate(state)
        state["profiles"].append({
            "id": "secondary", "slot": 1, "label": "Second",
            "secret": "c" * 32, "ad_tag": "", "channel": "",
            "carrier": "websocket", "enabled": True,
        })
        with self.assertRaisesRegex(ValueError, "۳۲"):
            validate(state)

    def test_duplicate_client_secret_rejected(self):
        state = fixture()
        state["profiles"][0]["clients"] = [{
            "id": "u01234567", "label": "Alice",
            "secret": "a" * 32, "enabled": True
        }]
        with self.assertRaises(ValueError):
            validate(state)

    def test_stats_parser_real_and_missing(self):
        self.assertEqual(
            parse_stats(b"total_special_connections\t4\nconfig_filename\tSECRET\n"),
            {"total_special_connections": 4},
        )
        self.assertEqual(parse_stats(b"hello\t0\n"), {})
        self.assertEqual(parse_stats(b"total_special_connections\t-1\n"), {})


class ManagementUITests(unittest.TestCase):
    def setUp(self):
        auth = patch.dict(os.environ, {
            "PANEL_ADMIN_PASSWORD_HASH": generate_password_hash("correct-test-password"),
        })
        auth.start()
        self.addCleanup(auth.stop)
        app.app.config.update(TESTING=True)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.statefile = Path(self.temp.name, "state.json")
        self.statefile.write_text(json.dumps(fixture()))
        self.patches = [
            patch.object(app, "STATE", self.statefile),
            patch.object(app, "LOCK", Path(self.temp.name, "ui.lock")),
            patch.object(app, "dispatch", return_value=None),
            patch.object(app, "collect_diagnostics", return_value={
                "schema": 1, "overall": "partial",
                "checks": [{"code": "relay_ready", "status": "ok", "message": "Ready"}],
                "profiles": [{"id": "main", "mtproxy_service": "ok",
                              "active_connections": 3}],
            }),
        ]
        self.mocks = [p.start() for p in self.patches]
        self.addCleanup(lambda: [p.stop() for p in reversed(self.patches)])
        self.client = app.app.test_client()
        self.base = "https://panel.example.com"
        self.client.get("/login", base_url=self.base)
        with self.client.session_transaction(base_url=self.base) as session:
            self.token = session["csrf"]
        login = self.client.post("/login", data={
            "_csrf": self.token, "username": "admin", "password": "correct-test-password"
        }, base_url=self.base)
        self.assertEqual(login.status_code, 302)
        with self.client.session_transaction(base_url=self.base) as session:
            self.token = session["csrf"]

    def post(self, route, data):
        return self.client.post(route, base_url=self.base,
                                data={"_csrf": self.token, **data})

    def test_all_pages_and_trustworthy_dashboard(self):
        for url in ("/", "/inbounds", "/inbounds/main", "/users", "/settings"):
            with self.subTest(url=url):
                page = self.client.get(url, base_url=self.base)
                self.assertEqual(page.status_code, 200, page.get_data(as_text=True)[:400])
                self.assertIn("Blue Proxy", page.get_data(as_text=True))
        overview = self.client.get("/", base_url=self.base).get_data(as_text=True)
        self.assertIn("اتصالات فعلی", overview)
        self.assertIn(">3</div>", overview)
        self.assertIn("نیازمند محاسبه‌گر ترافیک مستقل", overview)

    def test_create_disable_rotate_delete_user(self):
        response = self.post("/inbounds/main/users", {"label": "Alice"})
        self.assertEqual(response.status_code, 302)
        state = json.loads(self.statefile.read_text())
        c = state["profiles"][0]["clients"][0]
        self.assertEqual(c["label"], "Alice")
        original = c["secret"]
        self.assertEqual(len(original), 32)
        self.assertEqual(len(relay_profiles(state)["profiles"]), 2)
        self.mocks[2].assert_called_with("apply")

        response = self.post("/inbounds/main/users/" + c["id"],
                             {"label": "Alice (paused)"})
        self.assertEqual(response.status_code, 302)
        state = json.loads(self.statefile.read_text())
        self.assertFalse(state["profiles"][0]["clients"][0]["enabled"])
        self.assertNotIn(original, [p["secret"] for p in relay_profiles(state)["profiles"]])

        response = self.post("/inbounds/main/users/" + c["id"] + "/rotate", {})
        self.assertEqual(response.status_code, 302)
        updated = json.loads(self.statefile.read_text())["profiles"][0]["clients"][0]
        self.assertNotEqual(updated["secret"], original)

        response = self.post("/inbounds/main/users/" + c["id"] + "/delete", {})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(json.loads(self.statefile.read_text())["profiles"][0]["clients"], [])


if __name__ == "__main__":
    unittest.main()
