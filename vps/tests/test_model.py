import pathlib
import sys
import unittest
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "manager"))
from model import new_secret, validate, web_url, direct_url, relay_profiles
from runner import build_argv


def sample():
    return {
        "version": 1, "hostname": "proxy.example.com",
        "panel_hostname": "panel.example.com", "auto_update": True,
        "profiles": [{
            "id": "main", "slot": 0, "label": "Main", "secret": "a" * 32,
            "ad_tag": "b" * 32, "channel": "@mychannel",
            "enabled": True, "carrier": "websocket",
        }],
    }


class ModelTests(unittest.TestCase):
    def test_valid_config(self):
        state = sample()
        self.assertEqual(validate(state), state)
        self.assertEqual(relay_profiles(state)["profiles"][0]["backend"], "127.0.0.1:10443")
        self.assertIn("webproxy?", web_url("proxy.example.com", "a"*32))
        self.assertIn("10443", direct_url("proxy.example.com", "a"*32, 10443))

    def test_secret_is_random(self):
        self.assertEqual(len(new_secret()), 32)
        self.assertNotEqual(new_secret(), new_secret())

    def test_bad_tag(self):
        state = sample()
        state["profiles"][0]["ad_tag"] = "-P bad; rm /"
        with self.assertRaises(ValueError):
            validate(state)

    def test_no_enabled_profiles(self):
        state = sample()
        state["profiles"][0]["enabled"] = False
        with self.assertRaises(ValueError):
            validate(state)

    def test_duplicate_secret(self):
        state = sample()
        second = dict(state["profiles"][0], id="second", slot=1)
        state["profiles"].append(second)
        with self.assertRaises(ValueError):
            validate(state)

    def test_reject_host_injection(self):
        state = sample()
        state["hostname"] = "proxy.example.com/evil"
        with self.assertRaises(ValueError):
            validate(state)

    def test_command_is_argument_vector(self):
        p = sample()["profiles"][0]
        cmd = build_argv(p, "--nat-info 10.0.0.5:198.51.100.20")
        self.assertIn("-P", cmd)
        self.assertIn("10443", cmd)
        self.assertIn("--nat-info", cmd)
        with self.assertRaises(ValueError):
            build_argv(p, "--nat-info 1.2.3.4; whoami")

    def test_profile_ids_are_safe(self):
        state = sample()
        state["profiles"][0]["id"] = "../../systemd"
        with self.assertRaises(ValueError):
            validate(state)


if __name__ == "__main__":
    unittest.main()
