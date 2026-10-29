"""Regression tests for sandbox-safe MTProxy and WEB relay config checks."""
import pathlib
import stat
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "manager"))
import apply


class AccessTests(unittest.TestCase):
    def test_backend_listener_accepts_real_tcp_success(self):
        with patch.object(apply.socket, "create_connection") as dial:
            apply.wait_for_backend(10444, timeout=0)
            dial.assert_called_once_with(("127.0.0.1", 10444), timeout=0.6)

    def test_backend_listener_fails_when_unit_active_but_socket_closed(self):
        with patch.object(apply.socket, "create_connection", side_effect=ConnectionRefusedError):
            with self.assertRaisesRegex(RuntimeError, "10444"):
                apply.wait_for_backend(10444, timeout=0)

    def test_backend_listener_rejects_unexpected_ports(self):
        with self.assertRaises(ValueError):
            apply.wait_for_backend(443, timeout=0)

    def test_reconciler_checks_listener_for_every_enabled_inbound(self):
        source = pathlib.Path(apply.__file__).read_text(encoding="utf-8")
        self.assertIn("wait_for_backend(10443 + slot)", source)
        self.assertIn('run("systemctl", "restart", UNIT.format(profile_id))', source)

    def test_accepts_root_owned_group_readable_regular_file(self):
        st = SimpleNamespace(st_mode=stat.S_IFREG | 0o640, st_uid=0, st_gid=4321)
        with patch.object(apply.os, "lstat", return_value=st), \
             patch.object(apply.os, "chown") as chown, \
             patch.object(apply.os, "chmod") as chmod:
            apply.ensure_group_readable("/etc/example/profile.json", 4321)
        chown.assert_not_called()
        chmod.assert_not_called()

    def test_normalizes_group_and_mode(self):
        old = SimpleNamespace(st_mode=stat.S_IFREG | 0o600, st_uid=0, st_gid=17)
        new = SimpleNamespace(st_mode=stat.S_IFREG | 0o640, st_uid=0, st_gid=4321)
        with patch.object(apply.os, "lstat", side_effect=[old, new]), \
             patch.object(apply.os, "chown") as chown, \
             patch.object(apply.os, "chmod") as chmod:
            apply.ensure_group_readable("/etc/example/profile.json", 4321)
        chown.assert_called_once_with("/etc/example/profile.json", 0, 4321)
        chmod.assert_called_once_with("/etc/example/profile.json", 0o640)

    def test_rejects_symlink_or_nonroot_file(self):
        for st in (
            SimpleNamespace(st_mode=stat.S_IFLNK | 0o777, st_uid=0, st_gid=4321),
            SimpleNamespace(st_mode=stat.S_IFREG | 0o640, st_uid=999, st_gid=4321),
        ):
            with self.subTest(st=st.st_mode), patch.object(apply.os, "lstat", return_value=st):
                with self.assertRaises(PermissionError):
                    apply.ensure_group_readable("/etc/example/profile.json", 4321)

    def test_private_web_profiles_source_for_relay_check(self):
        """Go relay rejects any group/other access on its direct source."""
        old = SimpleNamespace(st_mode=stat.S_IFREG | 0o640, st_uid=0, st_gid=4321)
        new = SimpleNamespace(st_mode=stat.S_IFREG | 0o400, st_uid=0, st_gid=0)
        with patch.object(apply.os, "lstat", side_effect=[old, new]), \
             patch.object(apply.os, "chown") as chown, \
             patch.object(apply.os, "chmod") as chmod:
            apply.ensure_private_profiles("/etc/tproxy-server/profiles.json")
        chown.assert_called_once_with("/etc/tproxy-server/profiles.json", 0, 0)
        chmod.assert_called_once_with("/etc/tproxy-server/profiles.json", 0o400)

    def test_private_web_profiles_never_accept_symlinks(self):
        for st in (
            SimpleNamespace(st_mode=stat.S_IFLNK | 0o777, st_uid=0, st_gid=0),
            SimpleNamespace(st_mode=stat.S_IFREG | 0o400, st_uid=1234, st_gid=0),
        ):
            with self.subTest(st=st.st_mode), patch.object(apply.os, "lstat", return_value=st):
                with self.assertRaises(PermissionError):
                    apply.ensure_private_profiles("/etc/tproxy-server/profiles.json")

    def test_private_web_profiles_stay_unmodified_when_secure(self):
        good = SimpleNamespace(st_mode=stat.S_IFREG | 0o400, st_uid=0, st_gid=0)
        with patch.object(apply.os, "lstat", return_value=good), \
             patch.object(apply.os, "chown") as chown, \
             patch.object(apply.os, "chmod") as chmod:
            apply.ensure_private_profiles("/etc/tproxy-server/profiles.json")
        chown.assert_not_called()
        chmod.assert_not_called()

    def test_repair_script_preserves_upstream_privacy(self):
        repair = (pathlib.Path(__file__).resolve().parents[1] / "repair-web.sh").read_text()
        self.assertIn('chown root:root "$file"', repair)
        self.assertIn('chmod 0400 "$file"', repair)
        self.assertNotIn('chmod 0640 "$file"', repair)

    def test_no_pam_runuser_inside_reconciler(self):
        content = pathlib.Path(apply.__file__).read_text(encoding="utf-8")
        self.assertNotIn('run("runuser"', content)
        self.assertIn("ensure_group_readable(runtime_path, runtime_group)", content)
        self.assertIn("ensure_private_profiles(PROFILES)", content)
        self.assertIn("atomic(PROFILES, candidate, 0o400)", content)
        self.assertIn("atomic(PROFILES, previous, 0o400)", content)


if __name__ == "__main__":
    unittest.main()
