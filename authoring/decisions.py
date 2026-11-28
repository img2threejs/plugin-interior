# SPDX-License-Identifier: Apache-2.0
"""Evidence/candidate adapter only; the base owns consultation and application authority."""
import re

from tools.project_support import digest, read_json

GROUPS = {'build-strategy': ('strategy-questions-v1', 'img2.build-strategy-context'),
          'quality-review': ('quality-review-questions-v1', 'img2.quality-review-context'),
          'repair': ('repair-decision-questions-v1', 'img2.repair-context')}
OPERATIONS = {'camera': {'match-camera'},
              'geometry': {'box', 'mesh', 'tube', 'lathe', 'cushion', 'curtain', 'import-model'},
              'materials': {'bind-pbr', 'refresh-material'},
              'lighting': {'calibrate', 'bake', 'probe'},
              'interaction': {'configure-views', 'configure-variants'}}
FIELDS = {'camera': {'authoring.cameraEstimate', 'authoring.inspectionViews'},
          'geometry': {'authoring.sceneBuilder'},
          'materials': {'authoring.resourceManifest'},
          'lighting': {'authoring.probePosition', 'runtime.glassTransmission'},
          'interaction': {'runtime.variants', 'runtime.finishTarget'}}


def identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.:-]{0,159}', value):
        raise ValueError('Expected a stable decision identifier')
    return value


def candidates_for(context, group, pass_id, candidates, components, *,
                   authorized_sources=frozenset(), bound_sources=frozenset(),
                   available_dependencies=frozenset()):
    """Validate shared plans against independently supplied trusted facts.

    Source authorization and dependency availability come from the program-owned
    caller, never the candidate, its evidence text, or a CLI request. An absent
    fact remains unavailable. This adapter supports no skeleton handoff.
    """
    for values in (authorized_sources, bound_sources, available_dependencies):
        if type(values) not in (set, frozenset):
            raise ValueError('Trusted source/dependency facts must be sets')
    if any(type(value) is not str or not re.fullmatch(r'[0-9a-f]{64}', value)
           for value in authorized_sources | bound_sources):
        raise ValueError('Trusted source facts must be SHA-256 digests')
    if any(type(value) is not str or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,95}', value)
           for value in available_dependencies):
        raise ValueError('Trusted dependency facts must be controlled identifiers')
    if group == 'quality-review':
        if candidates != []:
            raise ValueError('Quality review supplies findings, not construction candidates')
        return []
    if group not in ('build-strategy', 'repair'):
        raise ValueError('Unsupported interior decision group')
    if pass_id not in OPERATIONS or type(candidates) is not list or not 1 <= len(candidates) <= 3:
        raise ValueError('Strategy/repair requires an unlocked supported pass and one to three candidates')
    available = set(components) - set(context.exclude_components)
    seen = set()
    result = []
    for candidate in candidates:
        required = {'id', 'passId', 'components', 'operations', 'fieldPaths',
                    'materialSources', 'dependencies', 'rigHandoff'}
        if type(candidate) is not dict or set(candidate) != required:
            raise ValueError('Candidate contains missing/unsupported fields')
        cid = candidate['id']
        if type(cid) is not str or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,95}', cid):
            raise ValueError('Expected a shared plan identifier')
        if cid in seen:
            raise ValueError('Duplicate candidate identity')
        seen.add(cid)
        if candidate['passId'] != pass_id:
            raise ValueError('Candidate targets a different pass')
        for key, allowed in (('operations', OPERATIONS[pass_id]), ('components', available),
                             ('fieldPaths', FIELDS[pass_id]), ('dependencies', available_dependencies),
                             ('materialSources', authorized_sources & bound_sources)):
            values = candidate[key]
            if (type(values) is not list or any(type(v) is not str or not v for v in values)
                    or len(set(values)) != len(values) or not set(values) <= allowed):
                raise ValueError('Candidate escapes declared ' + key)
        if not candidate['operations'] or not candidate['components'] or not candidate['fieldPaths']:
            raise ValueError('Candidate requires operations, exact owned components and bounded fields')
        if 'blender' not in candidate['dependencies']:
            raise ValueError('Native interior plans require independently verified blender availability')
        if candidate['rigHandoff'] is not False:
            raise ValueError('Interior does not provide a verified rig handoff')
        if set(candidate['operations']) & {'import-model', 'bind-pbr'} and not candidate['materialSources']:
            raise ValueError('Resource operations require bound and authorized material source bytes')
        result.append({key: list(value) if type(value) is list else value
                       for key, value in candidate.items()})
    return result


def decision_context(context, group, subject, candidates, evidence, components, *,
                     policy_version, requested_model, requirements,
                     authorized_sources=frozenset(), available_dependencies=frozenset()):
    """Return the frozen four-group envelope; never call Jev or advance a stage.

    Evidence maps roles to workspace-relative files (source/spec/builder/camera,
    renders, gates, provenance). Their bytes are hashed now; the base controller
    rechecks freshness, admission, locks, budget and protected checks before use.
    """
    if group not in GROUPS:
        raise ValueError('Unsupported interior decision group')
    if set(subject) != {'runId', 'objectId', 'revision', 'passId'}:
        raise ValueError('Missing exact decision subject')
    for key in ('runId', 'objectId', 'passId'):
        identifier(subject[key])
    revision = subject['revision']
    if not (type(revision) is int and revision >= 0):
        identifier(revision)
    required_roles = {'source', 'spec', 'builder', 'intake'}
    if group in ('quality-review', 'repair'):
        required_roles |= {'gate-report', 'camera', 'render'}
    if not isinstance(evidence, dict) or not required_roles <= set(evidence):
        raise ValueError('Missing required fresh evidence bindings')
    intake = read_json(context.resolve('.img2/artifacts/interior/intake.json'))
    if set(components) != set(intake['components']):
        raise ValueError('Candidate scope must match the admitted intake component identities')
    if read_json(context.resolve(evidence['spec'])) != context.spec:
        raise ValueError('Spec evidence does not match the current scene specification')
    pins = []
    for role, relative in sorted(evidence.items()):
        identifier(role)
        path = context.resolve(relative)
        if not path.is_file() or path.stat().st_size == 0:
            raise ValueError('Missing evidence: ' + role)
        pins.append({'role': role, 'path': str(path.relative_to(context.workspace)), 'sha256': digest(path)})
    by_role = {pin['role']: pin for pin in pins}
    expected = {'source': context.spec['source'], 'builder': context.spec['authoring']['sceneBuilder'],
                'intake': '.img2/artifacts/interior/intake.json'}
    for role, relative in expected.items():
        if context.resolve(by_role[role]['path']) != context.resolve(relative):
            raise ValueError('Evidence does not bind the current ' + role)
    selected = candidates_for(context, group, subject['passId'], candidates, components,
                              authorized_sources=authorized_sources,
                              bound_sources={pin['sha256'] for pin in pins},
                              available_dependencies=available_dependencies)
    if not isinstance(requirements, dict) or not {'criticalFeatures', 'requiredViews'} <= set(requirements):
        raise ValueError('Critical feature and required view contracts must be supplied')
    version, kind = GROUPS[group]
    return {'kind': kind, 'version': 1, 'group': group, 'subject': dict(subject),
            'bindings': pins, 'candidates': selected,
            'observations': {'excludeComponents': list(context.exclude_components),
                             'ownedComponents': sorted(set(components) - set(context.exclude_components))},
            'requirements': requirements, 'policyVersion': identifier(policy_version),
            'questionSetVersion': version, 'requestedModel': identifier(requested_model)}
