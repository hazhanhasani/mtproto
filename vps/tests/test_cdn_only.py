"""Regression checks for Cloudflare WEB-only public transport mode."""
from pathlib import Path
import copy
import runpy
import unittest

ROOT = Path(__file__).resolve().parents[1]


VERIFY = runpy.run_path(str(ROOT / "verify-cdn-firewall.py"))
is_guarded_ruleset = VERIFY["is_guarded_ruleset"]


def sample_ruleset():
    return {"nftables": [
        {"table": {"family": "inet", "name": "blue_proxy_cdn_only"}},
        {"chain": {"family": "inet", "table": "blue_proxy_cdn_only",
                   "name": "drop_external_mtproxy", "type": "filter",
                   "hook": "input", "prio": -15}},
        {"rule": {
            "family": "inet", "table": "blue_proxy_cdn_only",
            "chain": "drop_external_mtproxy",
            "expr": [
                {"match": {"left": {"meta": {"key": "iifname"}},
                           "op": "!=", "right": "lo"}},
                {"match": {"left": {"payload": {"protocol": "tcp", "field": "dport"}},
                           "op": "==", "right": {"range": [10443, 10474]}}},
                {"drop": None},
            ]
        }},
    ]}


class StructuredFirewallTests(unittest.TestCase):
    def test_nftables_json_real_drop(self):
        self.assertTrue(is_guarded_ruleset(sample_ruleset()))

    def test_rejects_wrong_range(self):
        bad = sample_ruleset()
        bad["nftables"][2]["rule"]["expr"][1]["match"]["right"]["range"] = [10443, 10473]
        self.assertFalse(is_guarded_ruleset(bad))

    def test_rejects_missing_drop_or_wrong_hook(self):
        bad = sample_ruleset()
        bad["nftables"][2]["rule"]["expr"].pop()
        self.assertFalse(is_guarded_ruleset(bad))
        bad = sample_ruleset()
        bad["nftables"][1]["chain"]["hook"] = "output"
        self.assertFalse(is_guarded_ruleset(bad))

    def test_rejects_loopback_block(self):
        bad = sample_ruleset()
        bad["nftables"][2]["rule"]["expr"].pop(0)
        self.assertFalse(is_guarded_ruleset(bad))

    def test_accepts_nested_interval_from_anon_set(self):
        fixture = sample_ruleset()
        fixture["nftables"][2]["rule"]["expr"][1]["match"]["right"] = {
            "set": [{"range": [10443, 10474]}]
        }
        self.assertTrue(is_guarded_ruleset(fixture))

    def test_status_requires_verification_not_table_presence(self):
        shell = (ROOT / "cdn-only.sh").read_text()
        self.assertIn("verify-cdn-firewall.py", shell)
        self.assertNotIn("grep -q '10443-10474'", shell)
        self.assertIn("nft -a list table", shell)

class CDNOnlyTests(unittest.TestCase):
    def test_firewall_is_narrow_and_persistent(self):
        installer = (ROOT / "cdn-only.sh").read_text()
        self.assertIn('iifname != "lo" tcp dport 10443-10474 drop', installer)
        self.assertIn("ExecStart=/usr/sbin/nft -f /etc/blue-mtproxy/cdn-only.nft", installer)
        self.assertIn("PartOf=nftables.service tproxy-firewall.service", installer)
        self.assertIn('systemctl restart "$SERVICE"', installer)
        self.assertIn("ssh", installer.lower())  # SSH is explicitly preserved
        self.assertNotIn("flush ruleset", installer)
        self.assertNotIn("tcp dport 443 drop", installer)
        self.assertNotIn("tcp dport 22 drop", installer)
        self.assertIn("chmod 0644 \"$FLAG\"", installer)

    def test_panel_never_presents_direct_mtproto_as_cdn(self):
        source = (ROOT / "app.py").read_text()
        template = (ROOT / "templates" / "manager.html").read_text()
        self.assertIn("CDN_FLAG.is_file()", source)
        self.assertIn("{% if cdn_only %}", template)
        self.assertIn("حالت CDN-only فعال است", template)
        self.assertIn("پورت داخلی MTProxy", template)
        self.assertIn("لینک WEB کاربر", template)
        self.assertNotIn("https://t.me/proxy?", template)

    def test_enabling_cdn_does_not_rotate_credentials(self):
        text = (ROOT / "cdn-only.sh").read_text()
        for path in ("state.json", "panel.env", "token.key"):
            self.assertNotIn(f"> {path}", text)
        self.assertNotIn("openssl rand", text)


if __name__ == "__main__":
    unittest.main()
