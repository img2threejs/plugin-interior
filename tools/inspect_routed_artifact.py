#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Independently reopen routed GLBs; no render, browser or visual acceptance claim."""
from argparse import ArgumentParser
import json
from pathlib import Path
import sys

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from authoring.routed_audit import replay_measurements


def main():
    parser = ArgumentParser(description=__doc__)
    parser.add_argument('--workspace', required=True)
    parser.add_argument('--artifact', default='artifact.json')
    parser.add_argument('--spec', default='scene-spec.json')
    parser.add_argument('--quality-output', help='Fresh workspace-relative directory for actual structural captures')
    args = parser.parse_args()
    report = replay_measurements(Path(args.workspace).resolve(strict=True), args.artifact, args.spec,
                                 quality_output=args.quality_output)
    print('IMG2_ROUTED_MEASUREMENTS ' + json.dumps(report, allow_nan=False, sort_keys=True), flush=True)


if __name__ == '__main__':
    main()
