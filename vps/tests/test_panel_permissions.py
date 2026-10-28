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


if __name__ == "__main__":
    unittest.main()
