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

    def test_no_pam_runuser_inside_reconciler(self):
        content = pathlib.Path(apply.__file__).read_text(encoding="utf-8")
        self.assertNotIn('run("runuser"', content)
        self.assertIn("ensure_group_readable(runtime_path, runtime_group)", content)
        self.assertIn("ensure_group_readable(PROFILES, relay_group)", content)


if __name__ == "__main__":
    unittest.main()
