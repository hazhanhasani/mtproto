import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from patch_upstream import (
    PROXY_SECRET_DOWNLOAD, PROXY_CONFIG_DOWNLOAD,
    REPLACEMENT, patch_source
)


class UpstreamPatchTests(unittest.TestCase):
    def test_exact_expected_download_replaced(self):
        upstream = "#!/bin/bash\n" + PROXY_SECRET_DOWNLOAD + "\n" + PROXY_CONFIG_DOWNLOAD + "\necho ready\n"
        patched = patch_source(upstream)
        self.assertIn(REPLACEMENT, patched)
        self.assertNotIn(PROXY_SECRET_DOWNLOAD, patched)
        self.assertNotIn(PROXY_CONFIG_DOWNLOAD, patched)
        self.assertEqual(patch_source(patched), patched)
        self.assertTrue(patched.endswith("echo ready\n"))

    def test_unrecognized_source_rejected_without_changes(self):
        with self.assertRaisesRegex(ValueError, "Unexpected upstream installer"):
            patch_source("#!/bin/bash\nexit 0\n")

    def test_partial_source_rejected(self):
        with self.assertRaises(ValueError):
            patch_source(PROXY_SECRET_DOWNLOAD)


if __name__ == "__main__":
    unittest.main()
