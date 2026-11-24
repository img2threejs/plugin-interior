#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Run full native-enabled discovery; missing runtime or skipped evidence fails."""
import os
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]


def run_required(suite, stream=None):
    stream = stream if stream is not None else sys.stderr
    result = unittest.TextTestRunner(stream=stream, verbosity=2).run(suite)
    if result.skipped or result.testsRun == 0:
        print('Required native validation refuses skipped or absent test evidence.', file=stream)
        return 1
    return 0 if result.wasSuccessful() else 1


def main():
    if not os.environ.get('IMG2_BLENDER_PYTHON'):
        print('Required native validation needs IMG2_BLENDER_PYTHON; no tests were certified.', file=sys.stderr)
        return 2
    suite = unittest.defaultTestLoader.discover(str(ROOT / 'tests'), pattern='test_*.py')
    return run_required(suite)


if __name__ == '__main__':
    sys.exit(main())
