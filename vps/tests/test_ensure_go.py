"""Integration regression: sourcing the Go bootstrap must not terminate the installer."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / "ensure-go.sh"


class GoBootstrapTests(unittest.TestCase):
    def test_sourced_script_keeps_parent_installer_running(self):
        with tempfile.TemporaryDirectory() as tmp:
            executable = Path(tmp, "go")
            executable.write_text(
                "#!/bin/sh\n"
                'if [ "$1" = "env" ] && [ "$2" = "GOVERSION" ]; then\n'
                "  echo go1.26.5\n"
                'elif [ "$1" = "version" ]; then\n'
                "  echo 'go version go1.26.5 linux/amd64'\n"
                "else\n"
                "  exit 2\n"
                "fi\n", encoding="utf-8"
            )
            executable.chmod(0o755)
            env = os.environ.copy()
            env["PATH"] = tmp + os.pathsep + "/usr/bin:/bin"
            p = subprocess.run(
                ["/bin/bash", "-c", f'source "{SCRIPT}"; echo PARENT_CONTINUES'],
                capture_output=True, text=True, check=False, env=env, timeout=15
            )
            self.assertEqual(p.returncode, 0, p.stderr)
            self.assertIn("Compatible toolchain already installed", p.stdout)
            self.assertIn("PARENT_CONTINUES", p.stdout)


if __name__ == "__main__":
    unittest.main()
