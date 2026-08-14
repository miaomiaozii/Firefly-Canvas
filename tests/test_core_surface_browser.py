import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests import core_surface_browser


class CoreSurfaceBrowserFailureEvidenceTests(unittest.TestCase):
    def test_setup_failure_references_existing_diagnostic_artifacts(self):
        with tempfile.TemporaryDirectory(prefix="firefly-native-failure-") as root:
            run_root = Path(root) / "run"
            artifacts_root = Path(root) / "artifacts"
            artifacts_root.mkdir()

            with mock.patch.object(core_surface_browser, "seed_store", side_effect=RuntimeError("seed failed")):
                with self.assertRaises(RuntimeError) as raised:
                    core_surface_browser.run_viewport(
                        Path("chrome.exe"),
                        (800, 600),
                        run_root,
                        artifacts_root,
                    )

            failure = json.loads(str(raised.exception))
            self.assertEqual(failure["reason"], "seed failed")
            for field in ("operations", "console_errors", "requests"):
                self.assertTrue((artifacts_root / failure[field]).is_file(), field)


if __name__ == "__main__":
    unittest.main()
