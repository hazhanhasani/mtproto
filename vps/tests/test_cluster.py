"""Multi-node control plane regressions: auth, isolation, TLS and desired state."""
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("PANEL_SECRET_KEY", "cluster-test-session-key-" + "z" * 24)
from werkzeug.security import generate_password_hash
os.environ.setdefault("PANEL_ADMIN_PASSWORD_HASH", generate_password_hash("cluster-test-password", method="scrypt"))

from manager import cluster
from manager import node_agent
import app as admin


class ClusterTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        path = Path(self.temp.name)
        a = patch.object(cluster, "PATH", path / "nodes.json")
        b = patch.object(cluster, "LOCK", path / "nodes.lock")
        a.start()
        b.start()
        self.addCleanup(a.stop)
        self.addCleanup(b.stop)
        env = patch.dict(os.environ, {"PANEL_ADMIN_PASSWORD_HASH": generate_password_hash("cluster-test-password", method="scrypt")})
        env.start()
        self.addCleanup(env.stop)
        admin.app.config["TESTING"] = True
        self.client = admin.app.test_client()
        self.base = "https://panel.example.com"

    def make_node(self):
        return cluster.mutate(lambda doc: cluster.add_node(
            doc, "Frankfurt node", "Germany / Frankfurt", "de1.example.com"))

    def login(self):
        self.client.get("/login", base_url=self.base)
        with self.client.session_transaction(base_url=self.base) as session:
            csrf = session["csrf"]
        response = self.client.post("/login", base_url=self.base,
                                    data={"username": "admin",
                                          "password": "cluster-test-password",
                                          "_csrf": csrf})
        self.assertEqual(response.status_code, 302)
        with self.client.session_transaction(base_url=self.base) as session:
            return session["csrf"]

    def test_hashed_one_time_token_and_cross_node_isolation(self):
        node_id, token = self.make_node()
        doc = cluster.read()
        self.assertNotIn(token, json.dumps(doc))
        self.assertTrue(cluster.authorize(node_id, token))
        self.assertFalse(cluster.authorize(node_id, token + "a"))
        other_id, other_token = cluster.mutate(
            lambda d: cluster.add_node(d, "Paris node", "France / Paris", "fr1.example.com"))
        self.assertFalse(cluster.authorize(other_id, token))
        self.assertFalse(cluster.authorize(node_id, other_token))
        self.assertNotEqual(node_id, other_id)

    def test_domain_inbound_client_capacity_and_revision(self):
        node_id, _ = self.make_node()
        def add(doc):
            n = cluster.get_node(doc, node_id)
            n["domains"].append("alt.example.com")
            cluster.new_profile(n, "Test inbound", "alt.example.com")
        cluster.mutate(add)
        node = cluster.get_node(cluster.read(), node_id)
        self.assertEqual(node["revision"], 2)
        self.assertEqual(node["profiles"][0]["hostname"], "alt.example.com")
        self.assertEqual(node["profiles"][0]["slot"], 0)
        self.assertRaises(ValueError, cluster.new_profile, node, "bad", "elsewhere.example.com")
        for _ in range(31):
            node["profiles"][0]["clients"].append({
                "id": "u" + os.urandom(5).hex(), "label": "user",
                "enabled": True, "secret": os.urandom(16).hex(),
            })
        cluster.validate_node_profiles(node)
        node["profiles"][0]["clients"].append({
            "id": "uextra", "label": "over", "enabled": True,
            "secret": os.urandom(16).hex(),
        })
        with self.assertRaises(ValueError):
            cluster.validate_node_profiles(node)

    def test_node_api_rejects_unauthenticated_clients_and_csrf(self):
        node_id, token = self.make_node()
        url = "/api/nodes/" + node_id + "/desired"
        self.assertEqual(self.client.get(url, base_url=self.base).status_code, 401)
        self.assertEqual(self.client.get(url, base_url=self.base,
                                         headers={"Authorization": "Bearer wrong"}).status_code, 401)
        response = self.client.get(url, base_url=self.base,
                                   headers={"Authorization": "Bearer " + token})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["node_id"], node_id)
        self.assertEqual(response.json["profiles"], [])
        report = "/api/nodes/" + node_id + "/report"
        self.assertEqual(self.client.post(report, base_url=self.base,
                                         json={"status": "ok", "revision": 1}).status_code, 401)
        self.assertEqual(self.client.post(report, base_url=self.base,
                                         headers={"Authorization": "Bearer " + token},
                                         json={"status": "ok", "revision": 1}).status_code, 200)
        self.assertEqual(cluster.get_node(cluster.read(), node_id)["applied_revision"], 1)

    def test_admin_session_and_csrf_required_for_node_mutations(self):
        self.assertEqual(self.client.post("/nodes", base_url=self.base,
                                         data={"name": "X"}).status_code, 403)
        csrf = self.login()
        res = self.client.post("/nodes", base_url=self.base, data={
            "_csrf": csrf, "name": "Berlin", "location": "Germany",
            "hostname": "de2.example.com",
        })
        self.assertEqual(res.status_code, 200)
        self.assertIn("کلید محرمانه اتصال", res.get_data(as_text=True))
        self.assertIn("de2.example.com", res.get_data(as_text=True))
        self.assertEqual(len(cluster.read()["nodes"]), 1)

    def test_agent_rejects_cross_node_and_invalid_domains(self):
        nid, _ = self.make_node()
        node = cluster.get_node(cluster.read(), nid)
        proposed = cluster.desired(node)
        old = {"version": 1, "hostname": "de1.example.com",
               "panel_hostname": "admin.de1.example.com", "auto_update": True,
               "profiles": [{"id": "main", "slot": 0, "label": "Main",
                             "secret": "a"*32, "ad_tag": "", "channel": "",
                             "enabled": True, "carrier": "websocket"}]}
        settings = {"id": nid}
        self.assertIsNone(node_agent.verify_desired(proposed, settings, old))
        proposed["node_id"] = "nffffffffffff"
        with self.assertRaises(ValueError):
            node_agent.verify_desired(proposed, settings, old)
        proposed["node_id"] = nid
        proposed["domains"].append("admin.de1.example.com")
        with self.assertRaises(ValueError):
            node_agent.verify_desired(proposed, settings, old)

    def test_caddy_aliases_are_separate_from_panel(self):
        proposed = {"domains": ["de1.example.com", "alt.example.com"]}
        content = node_agent.caddy_config(proposed, "de1.example.com", "panel.de1.example.com")
        self.assertNotIn(b"de1.example.com {", content)
        self.assertIn(b"alt.example.com {", content)
        self.assertIn(b"127.0.0.1:9120", content)
        proposed["domains"].append("panel.de1.example.com")
        with self.assertRaises(ValueError):
            node_agent.caddy_config(proposed, "de1.example.com", "panel.de1.example.com")


if __name__ == "__main__":
    unittest.main()
