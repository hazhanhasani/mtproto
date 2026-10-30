"""HTTP and session regression tests for the administration surface."""
import os
import pathlib
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import patch

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from werkzeug.security import generate_password_hash
os.environ["PANEL_SECRET_KEY"] = "test-only-session-key-" + "z" * 24
os.environ["PANEL_ADMIN_PASSWORD_HASH"] = generate_password_hash("test-safe-password", method="scrypt")
import app as admin


def state():
    return {"version": 1, "hostname": "proxy.example.com",
            "panel_hostname": "panel.example.com", "auto_update": True,
            "profiles": [{"id": "main", "slot": 0, "label": "Main", "secret": "a"*32,
                          "ad_tag": "", "channel": "", "enabled": True, "carrier": "websocket"}]}


class PanelTests(unittest.TestCase):
    def setUp(self):
        admin.app.config.update(TESTING=True)
        self.client = admin.app.test_client()
        self.url = "https://panel.example.com"

    def csrf(self):
        with self.client.session_transaction(base_url=self.url) as sess:
            return sess.get("csrf")

    def test_health_and_security_headers(self):
        response = self.client.get("/healthz", base_url=self.url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json, {"ok": True})
        self.assertIn("frame-ancestors 'none'", response.headers["Content-Security-Policy"])

    def test_unauthenticated_dashboard_redirect(self):
        response = self.client.get("/", base_url=self.url)
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login", response.headers["Location"])

    def test_unavailable_telegram_settings_do_not_break_panel(self):
        self.client.get("/login", base_url=self.url)
        auth = self.client.post("/login", base_url=self.url, data={
            "username": "admin", "password": "test-safe-password",
            "_csrf": self.csrf(),
        })
        self.assertEqual(auth.status_code, 302)
        for failure in (ValueError("invalid bot file"), PermissionError("token path")):
            with self.subTest(failure=type(failure).__name__), patch.object(
                admin, "load_state", return_value=state()
            ), patch.object(
                admin, "get_bot_config", side_effect=failure
            ), patch.object(
                admin, "collect_diagnostics", return_value={"profiles": [], "checks": []}
            ):
                for url in ("/", "/inbounds", "/inbounds/main", "/users", "/settings", "/bot"):
                    response = self.client.get(url, base_url=self.url)
                    self.assertEqual(response.status_code, 200, url)
                    body = response.get_data(as_text=True)
                    self.assertIn("تنظیمات ربات کانال قابل خواندن نیست", body)
                    self.assertNotIn("token path", body)
                    self.assertNotIn("invalid bot file", body)

    def test_login_page_remains_accessible_after_expired_csrf(self):
        initial = self.client.get("/login", base_url=self.url)
        self.assertEqual(initial.status_code, 200)
        self.assertIn("ورود به مدیریت", initial.get_data(as_text=True))
        old_csrf = self.csrf()
        with self.client.session_transaction(base_url=self.url) as sess:
            sess["csrf"] = "simulated-new-csrf-" + "b" * 28
        expired = self.client.post("/login", base_url=self.url,
                                   data={"_csrf": old_csrf, "username": "admin",
                                         "password": "test-safe-password"})
        self.assertEqual(expired.status_code, 303)
        self.assertTrue(expired.headers["Location"].endswith("/login"))
        refreshed = self.client.get("/login", base_url=self.url)
        self.assertEqual(refreshed.status_code, 200)
        self.assertIn("نشست ورود منقضی شده است", refreshed.get_data(as_text=True))
        self.assertNotEqual(self.csrf(), old_csrf)
        with self.client.session_transaction(base_url=self.url) as sess:
            self.assertFalse(sess.get("authenticated"))
        # Mutating requests outside the login route must keep strict CSRF.
        denied = self.client.post("/profiles", base_url=self.url,
                                  data={"label": "must-not-be-created", "_csrf": old_csrf})
        self.assertEqual(denied.status_code, 403)
        self.assertNotIn("Location", denied.headers)
        success = self.client.post("/login", base_url=self.url, data={
            "username": "admin", "password": "test-safe-password",
            "_csrf": self.csrf()
        })
        self.assertEqual(success.status_code, 302)

    def test_login_csrf_and_logout(self):
        first = self.client.post("/login", data={"username": "admin"},
                                 base_url=self.url)
        self.assertEqual(first.status_code, 303)
        self.assertTrue(first.headers["Location"].endswith("/login"))
        self.client.get("/login", base_url=self.url)
        token = self.csrf()
        self.assertTrue(token)
        bad = self.client.post("/login", base_url=self.url,
                               data={"username": "admin", "password": "bad", "_csrf": token})
        self.assertEqual(bad.status_code, 200)
        good = self.client.post("/login", base_url=self.url, data={
            "username": "admin", "password": "test-safe-password", "_csrf": token
        })
        self.assertEqual(good.status_code, 302)
        self.assertIn("Secure", good.headers["Set-Cookie"])
        with patch.object(admin, "load_state", return_value=state()), patch.object(
            admin.subprocess, "run", return_value=SimpleNamespace(stdout="active\n")
        ):
            page = self.client.get("/", base_url=self.url)
        self.assertEqual(page.status_code, 200)
        self.assertIn("Blue Proxy Manager", page.get_data(as_text=True))
        new_token = self.csrf()
        self.assertEqual(self.client.post("/logout", data={"_csrf": "incorrect"},
                                          base_url=self.url).status_code, 403)
        self.assertEqual(self.client.post("/logout", data={"_csrf": new_token},
                                          base_url=self.url).status_code, 302)
        self.assertEqual(self.client.get("/", base_url=self.url).status_code, 302)


if __name__ == "__main__":
    unittest.main()
