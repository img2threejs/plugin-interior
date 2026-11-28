#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Print a pinned quality snapshot; construction snapshots require trusted host scope."""
from argparse import ArgumentParser
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from authoring import Context
from authoring.decisions import decision_context
from tools.project_support import read_json


def main():
    parser = ArgumentParser(description=__doc__)
    parser.add_argument('--workspace', required=True)
    parser.add_argument('--spec', default='scene-spec.json')
    parser.add_argument('--request', required=True, help='Workspace-relative local candidate/evidence request JSON')
    args = parser.parse_args()
    workspace = Path(args.workspace).resolve(strict=True)
    context = Context(workspace, {}, workspace / 'public/assets/interior',
                      workspace / '.img2/artifacts/interior')
    context.spec = read_json(context.resolve(args.spec))
    request = read_json(context.resolve(args.request))
    if set(request) != {'group', 'subject', 'candidates', 'evidence', 'requirements', 'policyVersion', 'requestedModel'}:
        raise ValueError('Unsupported local snapshot request fields')
    if request['group'] in ('build-strategy', 'repair'):
        raise ValueError('Construction snapshots require a program-owned caller supplying independently '
                         'verified dependency availability and source authorization; CLI request data is not authority')
    intake = read_json(context.resolve('.img2/artifacts/interior/intake.json'))
    result = decision_context(context, request['group'], request['subject'], request['candidates'],
                              request['evidence'], intake['components'],
                              requirements=request['requirements'], policy_version=request['policyVersion'],
                              requested_model=request['requestedModel'])
    print(json.dumps(result, allow_nan=False, sort_keys=True))


if __name__ == '__main__':
    main()
