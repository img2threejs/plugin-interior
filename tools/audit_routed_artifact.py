#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Rerun actual program audits and Blender replay; NOT a routed terminal gate."""
from argparse import ArgumentParser
import json
import os
from pathlib import Path
import subprocess
import sys

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from authoring.routed_audit import MISSING_TERMINAL_PRODUCERS, audit, pin


def main():
    parser = ArgumentParser(description=__doc__)
    parser.add_argument('--workspace', required=True)
    parser.add_argument('--artifact', default='artifact.json')
    parser.add_argument('--spec', default='scene-spec.json')
    parser.add_argument('--blender-python', default=os.environ.get('IMG2_BLENDER_PYTHON'))
    args = parser.parse_args()
    evidence = {}
    try:
        workspace = Path(args.workspace).resolve(strict=True)
        evidence['artifact'] = pin(workspace, args.artifact)
        if not args.blender_python:
            raise ValueError('Independent Blender interpreter unavailable: supply --blender-python or IMG2_BLENDER_PYTHON')
        report = audit(workspace, blender_python=args.blender_python,
                       artifact_relative=args.artifact, spec_relative=args.spec)
        code = 0
    except (OSError, ValueError, RuntimeError, KeyError, TypeError, subprocess.SubprocessError) as error:
        report = {'kind': 'img2.interior-routed-audit', 'version': 1, 'status': 'fail',
                  'reasons': [str(error)], 'evidence': evidence, 'terminalAcceptance': False,
                  'missingTerminalProducers': list(MISSING_TERMINAL_PRODUCERS)}
        code = 1
    print(json.dumps(report, allow_nan=False, sort_keys=True))
    return code


if __name__ == '__main__':
    sys.exit(main())
