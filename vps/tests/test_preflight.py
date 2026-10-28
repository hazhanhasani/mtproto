import errno
import pathlib
import socket
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import preflight


class PreflightTests(unittest.TestCase):
    def test_hostname_validation(self):
        for host in ("proxy.example.com", "panel.example.co.uk"):
            self.assertTrue(preflight.valid_host(host))
        for host in ("", "example", "https://example.com", "a..b.com",
                     "-bad.example.com", "bad-.example.com", "UPPER.example.com",
                     "p.example.com.", "foo.example.1"):
            self.assertFalse(preflight.valid_host(host), host)

    def test_dns_matches_vps_ip(self):
        with patch.object(preflight, "get_public_ipv4", return_value="203.0.113.9"), \
             patch.object(preflight, "get_ipv4", return_value={"203.0.113.9"}):
            preflight.check_dns("proxy.example.com", "panel.example.com")
        with patch.object(preflight, "get_public_ipv4", return_value="203.0.113.9"), \
             patch.object(preflight, "get_ipv4", return_value={"104.16.0.2"}):
            with self.assertRaisesRegex(RuntimeError, "DNS mismatch"):
                preflight.check_dns("proxy.example.com", "panel.example.com")

    def test_dns_not_resolvable(self):
        with patch.object(preflight, "get_public_ipv4", return_value="203.0.113.9"), \
             patch.object(preflight, "get_ipv4", side_effect=socket.gaierror(-2, "not found")):
            with self.assertRaisesRegex(RuntimeError, "DNS missing"):
                preflight.check_dns("proxy.example.com", "panel.example.com")


if __name__ == "__main__":
    unittest.main()
