"""Regression checks for source permissions after umask 077 git upgrades."""
from pathlib import Path
import os
import stat
import subprocess
import tempfile
import unittest

VPS = Path(__file__).resolve().parents[1]
SCRIPT = (VPS / "panel-permissions.sh").read_text(encoding="utf-8")


class SourcePermissionTests(unittest.TestCase):
    def test_script_repairs_every_required_source_path(self):
        for line in (
            'chgrp -- "$group" "$root"',
            'chmod g+x -- "$root"',
            'chgrp -R -- "$group" "$root/vps"',
            'chmod -R g+rX "$root/vps"',
            'chmod -R go-w "$root/vps"',
            'runuser -u bluepanel -- /bin/sh -c',
            'import app',
            'templates/manager.html',
        ):
            with self.subTest(line=line):
                self.assertIn(line, SCRIPT)
        self.assertNotIn('chmod -R 777', SCRIPT)
        self.assertNotIn('chmod -R 755 "$root"', SCRIPT)
        self.assertIn('chmod o+x -- "$root" "$root/vps" "$root/vps/manager"', SCRIPT)
        self.assertIn('chmod o+r -- "$root/vps/manager/runner.py"', SCRIPT)
        self.assertIn('runuser -u mtproxy -- /usr/bin/python3 -B', SCRIPT)
        self.assertNotIn("usermod -aG bluepanel mtproxy", SCRIPT)
        self.assertNotIn('chmod -R o+r "$root/vps"', SCRIPT)

    def test_group_read_execute_preserves_non_executable_source(self):
        # Git under umask 077 may check out 0700 directories and 0600 files.
        # g+rX fixes imports without turning ordinary Python modules into
        # executable files. Do not need root or mutate a real installation.
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pkg = root / "vps" / "manager"
            pkg.mkdir(parents=True)
            source = pkg / "app.py"
            source.write_text("ok = True\n", encoding="utf-8")
            script = pkg / "launch.sh"
            script.write_text("#!/bin/sh\n", encoding="utf-8")
            os.chmod(root / "vps", 0o700)
            os.chmod(pkg, 0o700)
            os.chmod(source, 0o600)
            os.chmod(script, 0o700)
            subprocess.run(["chmod", "-R", "g+rX", str(root / "vps")], check=True)
            self.assertEqual(stat.S_IMODE(source.stat().st_mode) & 0o070, 0o040)
            self.assertEqual(stat.S_IMODE(pkg.stat().st_mode) & 0o070, 0o050)
            self.assertEqual(stat.S_IMODE(script.stat().st_mode) & 0o070, 0o050)


    def test_repair_backend_checks_mtproxy_launcher_before_restart(self):
        repair = (VPS / "repair-backend.sh").read_text(encoding="utf-8")
        self.assertIn('chmod o+x "$CODE" "$CODE/vps" "$CODE/vps/manager"', repair)
        self.assertIn('chmod o+r "$LAUNCHER"', repair)
        self.assertIn('runuser -u mtproxy -- /usr/bin/python3 -B', repair)
        self.assertLess(repair.index('chmod o+r "$LAUNCHER"'),
                        repair.index('systemctl restart "$unit"'))

    def test_minimal_other_permissions_do_not_expose_panel_files(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            code = root / "vps"
            manager = code / "manager"
            manager.mkdir(parents=True)
            runner = manager / "runner.py"
            runner.write_text("print('ok')\n", encoding="utf-8")
            private = code / "app.py"
            private.write_text("SECRET = 'not-for-mtproxy'\n", encoding="utf-8")
            for directory in (root, code, manager):
                os.chmod(directory, 0o700)
            for source in (runner, private):
                os.chmod(source, 0o600)
            subprocess.run(["chmod", "o+x", str(root), str(code), str(manager)],
                           check=True)
            subprocess.run(["chmod", "o+r", str(runner)], check=True)
            self.assertEqual(stat.S_IMODE(runner.stat().st_mode) & 0o007, 0o004)
            self.assertEqual(stat.S_IMODE(private.stat().st_mode) & 0o007, 0o000)
            self.assertTrue(all(stat.S_IMODE(d.stat().st_mode) & 0o001 for d in
                                (root, code, manager)))


if __name__ == "__main__":
    unittest.main()
