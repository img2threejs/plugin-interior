# SPDX-License-Identifier: Apache-2.0
"""Required native validation must not turn skipped or absent evidence green."""
import io
import os
from pathlib import Path
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.require_native_tests import run_required


class RequiredNativeValidationTests(unittest.TestCase):
    def test_missing_selector_fails_before_optional_native_discovery(self):
        env = {**os.environ, 'PYTHONDONTWRITEBYTECODE': '1'}
        env.pop('IMG2_BLENDER_PYTHON', None)
        result = subprocess.run([sys.executable, '-B', str(ROOT / 'scripts/require_native_tests.py')],
                                capture_output=True, text=True, env=env, timeout=10)
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)

    def test_skipped_native_evidence_cannot_report_success(self):
        class UnavailableNative(unittest.TestCase):
            def runTest(self):
                self.skipTest('Native fixture unavailable')
        suite = unittest.TestSuite([UnavailableNative()])
        self.assertEqual(run_required(suite, io.StringIO()), 1)

    def test_empty_discovery_cannot_certify_native_validation(self):
        self.assertEqual(run_required(unittest.TestSuite(), io.StringIO()), 1)


if __name__ == '__main__':
    unittest.main()
