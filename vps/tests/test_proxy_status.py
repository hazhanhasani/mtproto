"""Regression checks for WEB bridge proof and config permissions."""
import base64
import hashlib
import hmac
import pathlib
import runpy
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
HELPERS = runpy.run_path(str(ROOT / "proxy-status.py"))
bridge_capability = HELPERS["bridge_capability"]


class BridgeStatusTests(unittest.TestCase):
    def test_hmac_bridge_matches_server_protocol(self):
        secret = "000102030405060708090a0b0c0d0e0f"
        hostname = "proxy.example.com"
        actual = bridge_capability(hostname, secret)
        expected = base64.urlsafe_b64encode(
            hmac.new(
                bytes.fromhex(secret),
                b"tdesktop-web-proxy-bridge-v1\nproxy.example.com",
                hashlib.sha256
            ).digest()
        ).decode().rstrip("=")
        self.assertEqual(actual, expected)
        self.assertEqual(len(actual), 43)
        self.assertNotIn(secret, actual)

    def test_bridge_is_hostname_bound(self):
        secret = "000102030405060708090a0b0c0d0e0f"
        self.assertNotEqual(
            bridge_capability("proxy.example.com", secret),
            bridge_capability("other.example.com", secret),
        )


if __name__ == "__main__":
    unittest.main()
