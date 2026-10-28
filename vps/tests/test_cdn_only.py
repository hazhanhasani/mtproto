"""Regression checks for Cloudflare WEB-only public transport mode."""
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]


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
        self.assertIn("CDN_FLAG.is_file()", source)
        self.assertIn("{% if cdn_only %}", source)
        self.assertIn("لینک مستقیم MTProto (خارج از CDN)", source)
        self.assertIn("پورت داخلی MTProxy", source)
        self.assertIn("لینک WEB آزمایشی از مسیر Cloudflare", source)

    def test_enabling_cdn_does_not_rotate_credentials(self):
        text = (ROOT / "cdn-only.sh").read_text()
        for path in ("state.json", "panel.env", "token.key"):
            self.assertNotIn(f"> {path}", text)
        self.assertNotIn("openssl rand", text)


if __name__ == "__main__":
    unittest.main()
