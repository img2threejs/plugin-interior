# SPDX-License-Identifier: Apache-2.0
"""Explicit, workspace-bound Blender authoring. Importing this package is inert."""
from dataclasses import dataclass
from pathlib import Path
import importlib.util
import json
import re
import uuid


def private_module(path):
    """Load without sys.modules caching: each workspace gets independent bindings."""
    spec = importlib.util.spec_from_file_location('_img2_' + uuid.uuid4().hex, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _routed_exclusions(workspace, spec, intake):
    """Check the local handoff, not independent parent authorization."""
    from authoring.routing import exclusions
    from tools.project_support import confined, read_json
    routed_spec = 'routing' in spec
    state_path = confined(workspace, 'state.json')
    if not state_path.exists():
        if routed_spec:
            raise ValueError('Routed scene spec requires state.json with routeChild')
        return None
    state = read_json(state_path)
    if type(state) is not dict:
        raise ValueError('Routed child state must be an object')
    if 'routeChild' not in state:
        if routed_spec:
            raise ValueError('Routed scene spec requires state.json with routeChild')
        return None
    child = state['routeChild']
    if (state.get('profile') != 'interior' or type(child) is not dict
            or child.get('outputKind') != '3d-scene'):
        raise ValueError('Routed child requires profile interior and outputKind 3d-scene')
    routing = spec.get('routing')
    if (type(routing) is not dict or type(routing.get('objectId')) is not str
            or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.:-]{0,159}', routing['objectId'])
            or routing['objectId'] != child.get('objectId')):
        raise ValueError('Routed scene routing.objectId must match routeChild.objectId')
    expected = exclusions(child.get('excludedObjectIds'))
    if (list(expected) != sorted(expected)
            or any(not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.:-]{0,159}', item) for item in expected)):
        raise ValueError('Routed excludedObjectIds must be sorted exact objectIds')
    if type(intake) is not dict:
        raise ValueError('Routed child requires the admitted intake snapshot')
    admitted = exclusions(intake.get('excludeComponents'))
    requested = exclusions(spec.get('excludeComponents'))
    if admitted != expected or requested != expected:
        raise ValueError('Routed intake/spec excludeComponents disagrees with excludedObjectIds')
    components = exclusions(intake.get('components'))
    if not set(expected) <= set(components):
        raise ValueError('Routed exclusions must name declared intake component identities')
    return expected


@dataclass
class Context:
    workspace: Path
    spec: dict
    assets: Path
    evidence: Path

    def __post_init__(self):
        self.workspace = Path(self.workspace).resolve()
        installed = Path(__file__).resolve().parents[1]
        if self.workspace.is_relative_to(installed):
            raise ValueError('Authored workspace must be outside the installed plugin tree')
        for name in ('assets', 'evidence'):
            path = Path(getattr(self, name)).resolve()
            if not path.is_relative_to(self.workspace) or path.is_relative_to(installed):
                raise ValueError(f'{name} must remain inside the authored workspace and outside the installed plugin')
            setattr(self, name, path)

    def resolve(self, relative):
        path = Path(relative)
        if path.is_absolute():
            raise ValueError(f'Expected workspace-relative path: {relative}')
        result = (self.workspace / path).resolve()
        if not result.is_relative_to(self.workspace):
            raise ValueError(f'Path escapes workspace: {relative}')
        if result.is_relative_to(Path(__file__).resolve().parents[1]):
            raise ValueError('Workspace paths must not resolve into the installed plugin tree')
        return result

    @property
    def ownership_binding(self):
        from authoring.routing import exclusions
        from tools.project_support import digest, read_json
        declared = self.spec.get('excludeComponents')
        snapshot = self.resolve('.img2/artifacts/interior/intake.json')
        intake = read_json(snapshot) if snapshot.is_file() else None
        _routed_exclusions(self.workspace, self.spec, intake)
        admitted = exclusions(intake.get('excludeComponents', [])) if intake else None
        requested = exclusions(declared) if declared is not None else admitted or ()
        if admitted is not None and set(requested) != set(admitted):
            raise ValueError('Scene excludeComponents disagrees with the admitted intake; start a new child revision')
        return {'excludeComponents': sorted(requested),
                'intakeSha256': digest(snapshot) if intake is not None else None}

    @property
    def exclude_components(self):
        return tuple(self.ownership_binding['excludeComponents'])

    @property
    def helpers(self):
        if not hasattr(self, '_helpers'):
            module = private_module(Path(__file__).with_name('geometry.py'))
            module.ROOT = self.workspace
            module.CONTEXT = self
            # Public ergonomic names for the reusable geometry primitives.
            for name in ('activate', 'tag', 'image', 'material', 'project_uv', 'mesh', 'box',
                         'tube', 'lathe', 'cushion', 'bounds', 'import_model', 'light'):
                setattr(module, name, getattr(module, '_' + name))
            module.curtain = module.curtain_panel
            self._helpers = module
        return self._helpers


def context_from_args(args):
    workspace = Path(args.workspace).resolve(strict=True)
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,95}', args.run_id):
        raise ValueError('run-id must be a simple alphanumeric slug')
    context = Context(workspace, {}, workspace / 'public/assets/interior',
                      workspace / '.img2/artifacts/interior/production' / args.run_id)
    context.spec = json.loads(context.resolve(args.spec).read_text())
    # Refuse an inconsistent handoff before stage outputs or native code loading.
    _ = context.ownership_binding
    dimensions = context.spec['authoring']['sourceImageSize']
    if len(dimensions) != 2 or any(not isinstance(v, int) or v <= 0 for v in dimensions):
        raise ValueError('sourceImageSize must contain two positive integer pixel dimensions')
    context.resolve(context.spec['source']).resolve(strict=True)
    for path in (context.assets, context.evidence):
        context.resolve(path.relative_to(workspace)).mkdir(parents=True, exist_ok=True)
    return context


def bound_module(name, context):
    _ = context.ownership_binding
    module = private_module(Path(__file__).with_name(name + '.py'))
    module.CONTEXT = context
    module.ROOT = context.workspace
    module.ASSETS = context.assets
    module.EVIDENCE = context.evidence
    module.WORK = context.evidence
    module.MASTER = context.assets / 'room-master.blend'
    module.CONTRACT = context.spec
    return module
