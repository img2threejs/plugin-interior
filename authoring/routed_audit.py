# SPDX-License-Identifier: Apache-2.0
"""Mechanical routed-artifact audit, deliberately without terminal acceptance authority.

No editable report is treated as a measurement producer. Existing program audits
are rerun and Blender reopens the actual GLBs in a separate invocation. The
missing visual/geometry-quality/browser producers remain explicit limitations.
"""
import hashlib
import json
import math
from pathlib import Path
import re
import subprocess
import sys

from authoring.routing import _glb, bounds, exclusions, normalized_glb, room_dimensions
from resources_lib.storage import confined
from tools.project_support import digest, read_json

PLUGIN = Path(__file__).resolve().parents[1]
MISSING_TERMINAL_PRODUCERS = (
    'artifact-bound-self-intersection-part-coverage-and-structural-turntable-measurements',
    'artifact-camera-spec-reference-bound-semantic-and-critical-feature-review',
    'composed-runtime-browser-capture-and-atlas-probe-cloth-verification',
)


def canonical_sha(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                     allow_nan=False).encode()).hexdigest()


def pin(workspace, relative):
    path = confined(workspace, relative)
    if not path.is_file() or path.stat().st_size == 0:
        raise ValueError('Missing nonempty regular audit input: ' + relative)
    return {'path': relative, 'sha256': digest(path)}


def checked_pin(workspace, value):
    if (type(value) is not dict or set(value) != {'path', 'sha256'}
            or type(value['sha256']) is not str or not re.fullmatch(r'[0-9a-f]{64}', value['sha256'])):
        raise ValueError('Invalid artifact file binding')
    current = pin(workspace, value['path'])
    if current != value:
        raise ValueError('Stale artifact file binding: ' + value['path'])
    return current


def positive(value):
    return type(value) in (int, float) and math.isfinite(value) and value > 0


def validate_artifact(workspace, artifact_relative='artifact.json', spec_relative='scene-spec.json'):
    """Validate current bytes/provenance only; recorded dimensions still need replay."""
    workspace = Path(workspace).resolve(strict=True)
    artifact_pin, spec_pin = pin(workspace, artifact_relative), pin(workspace, spec_relative)
    artifact = read_json(confined(workspace, artifact_relative))
    spec = read_json(confined(workspace, spec_relative))
    if type(spec) is not dict or type(spec.get('routing')) is not dict:
        raise ValueError('Routed audit requires the actual native routed scene specification')
    if (type(artifact) is not dict or artifact.get('outputKind') != '3d-scene'
            or type(artifact.get('objectId')) is not str
            or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.:-]{0,159}', artifact['objectId'])
            or spec.get('routing', {}).get('objectId') != artifact['objectId']):
        raise ValueError('Routed artifact object/kind does not match the native spec')
    if (artifact.get('frame') != {'units': 'm', 'up': '+Y', 'forward': '+Z'}
            or artifact.get('pivot') != 'bottom-centre' or artifact.get('rig', 'missing') is not None
            or not positive(artifact.get('scaleEstimate'))):
        raise ValueError('Noncanonical routed frame, pivot, scale or rig')
    dimensions = artifact.get('roomDimensions')
    if (type(dimensions) is not dict or set(dimensions) != {'width', 'height', 'depth'}
            or not all(positive(value) for value in dimensions.values())):
        raise ValueError('Missing positive finite room dimensions')
    files = artifact.get('files')
    if type(files) is not list or not files:
        raise ValueError('Missing actual artifact files')
    pins = [artifact_pin, spec_pin]
    seen = {artifact_relative, spec_relative}
    models, receipts = [], []
    for binding in files:
        checked_pin(workspace, binding)
        relative = binding['path']
        if relative in seen:
            raise ValueError('Duplicate or recursive artifact file binding')
        seen.add(relative)
        pins.append(binding)
        if relative.lower().endswith('.glb'):
            models.append(binding)
            _glb(confined(workspace, relative))
        if Path(relative).name == 'scene-measurements.json':
            receipts.append(binding)
    if len(models) != 1 or len(receipts) != 1:
        raise ValueError('Expected one actual composition GLB and its scene-measurements receipt')
    model = models[0]
    receipt = read_json(confined(workspace, receipts[0]['path']))
    if (type(receipt) is not dict or receipt.get('schemaVersion') != 1 or receipt.get('basis') != 'exported-glb-world-vertices'
            or receipt.get('roomDimensionBasis') != 'explicit-authored-shell-envelope'
            or receipt.get('compositionSha256') != model['sha256']
            or receipt.get('roomDimensions') != dimensions):
        raise ValueError('Artifact and measurement provenance disagree')
    source_runtime = checked_pin(workspace, receipt.get('sourceRuntime'))
    if source_runtime['path'] == model['path']:
        raise ValueError('Composition cannot masquerade as its original runtime GLB')
    source_doc, _ = _glb(confined(workspace, source_runtime['path']))
    camera_nodes = [node for node in source_doc.get('nodes', []) if 'camera' in node]
    if not source_doc.get('cameras') or not camera_nodes:
        raise ValueError('Actual exported reference camera is unavailable')
    # Bind the complete exported transform graph, not a guessed cameraEstimate.
    camera_sha = canonical_sha({key: source_doc.get(key) for key in ('cameras', 'nodes', 'scenes', 'scene')})
    intake_pin = pin(workspace, '.img2/artifacts/interior/intake.json')
    intake = read_json(confined(workspace, intake_pin['path']))
    excluded = exclusions(intake.get('excludeComponents', []))
    if not set(excluded) <= set(intake.get('components', [])):
        raise ValueError('Exclusions are not admitted component identities')
    if set(exclusions(spec.get('excludeComponents', list(excluded)))) != set(excluded):
        raise ValueError('Current spec and admitted exclusion ownership disagree')
    if set(exclusions(receipt.get('excludeComponents', []))) != set(excluded):
        raise ValueError('Exported exclusion provenance is stale')
    components = exclusions(spec.get('authoring', {}).get('roomMeasurementComponents', []))
    if (not components or set(components) & set(excluded)
            or list(components) != receipt.get('roomMeasurementComponents')):
        raise ValueError('Missing or stale room measurement component selection')
    reference_pin = pin(workspace, spec['source'])
    builder_pin = pin(workspace, spec['authoring']['sceneBuilder'])
    pins += [source_runtime, intake_pin, reference_pin, builder_pin]
    for binding in pins:
        checked_pin(workspace, binding)
    return {'artifact': artifact_pin, 'spec': spec_pin, 'intake': intake_pin,
            'reference': reference_pin, 'builder': builder_pin, 'model': model,
            'sourceRuntime': source_runtime, 'cameraSha256': camera_sha,
            'roomDimensions': dimensions, 'roomMeasurementComponents': list(components),
            'excludeComponents': sorted(excluded), 'measurementReceipt': receipts[0], 'inputs': pins}


def replay_measurements(workspace, artifact_relative='artifact.json', spec_relative='scene-spec.json'):
    """Use actual Blender imports to independently replay the mechanical handoff."""
    import bpy
    from authoring.routing import measure_export
    if bpy.app.version < (4, 3, 0):
        raise ValueError('Blender >=4.3 is required for the independent GLB replay')
    snapshot = validate_artifact(workspace, artifact_relative, spec_relative)
    source = confined(workspace, snapshot['sourceRuntime']['path'])
    delivered = confined(workspace, snapshot['model']['path'])
    original_rows, rows = measure_export(source), measure_export(delivered)
    if any(row['component'] in snapshot['excludeComponents'] for row in original_rows + rows):
        raise ValueError('Separately owned geometry remains in an actual exported GLB')
    source_bounds, measured_bounds = bounds(original_rows), bounds(rows)
    origin = [(source_bounds['min'][0] + source_bounds['max'][0]) / 2,
              (source_bounds['min'][1] + source_bounds['max'][1]) / 2, source_bounds['min'][2]]
    if hashlib.sha256(normalized_glb(source, origin)).hexdigest() != snapshot['model']['sha256']:
        raise ValueError('Composition geometry/material bytes are not the normalized original runtime')
    pivot = [(measured_bounds['min'][0] + measured_bounds['max'][0]) / 2,
             (measured_bounds['min'][1] + measured_bounds['max'][1]) / 2, measured_bounds['min'][2]]
    if any(abs(value) > 1e-5 for value in pivot):
        raise ValueError('Actual GLB geometry has a noncanonical bottom-centre pivot')
    measured_dimensions = room_dimensions(rows, snapshot['roomMeasurementComponents'])
    original_dimensions = room_dimensions(original_rows, snapshot['roomMeasurementComponents'])
    for key, value in measured_dimensions.items():
        if (not math.isclose(value, snapshot['roomDimensions'][key], rel_tol=1e-5, abs_tol=1e-5)
                or not math.isclose(value, original_dimensions[key], rel_tol=1e-5, abs_tol=1e-5)):
            raise ValueError('Declared room dimensions disagree with independently imported vertices')
    receipt = read_json(confined(workspace, snapshot['measurementReceipt']['path']))
    expected = {row['name']: row for row in receipt.get('meshes', [])}
    actual = {row['name']: row for row in rows}
    if len(expected) != len(receipt.get('meshes', [])) or set(expected) != set(actual):
        raise ValueError('Exported physical mesh inventory changed')
    for name, row in actual.items():
        old = expected[name]
        if old.get('component') != row['component'] or old.get('vertices') != row['vertices']:
            raise ValueError('Exported component/vertex inventory changed: ' + name)
        if (type(old.get('bounds')) is not list or len(old['bounds']) != 2
                or any(type(point) is not list or len(point) != 3
                       or any(type(value) not in (int, float) or not math.isfinite(value) for value in point)
                       for point in old['bounds'])):
            raise ValueError('Malformed recorded geometry bounds: ' + name)
        if any(not math.isclose(a, b, rel_tol=1e-5, abs_tol=1e-5)
               for old_point, point in zip(old['bounds'], row['bounds']) for a, b in zip(old_point, point)):
            raise ValueError('Recorded vertex bounds disagree with the exported geometry: ' + name)
    for binding in snapshot['inputs']:
        checked_pin(workspace, binding)
    return {'artifact': snapshot['artifact'], 'inputs': snapshot['inputs'],
            'cameraSha256': snapshot['cameraSha256'], 'blenderVersion': bpy.app.version_string,
            'roomDimensions': measured_dimensions, 'boundsNativeZUp': measured_bounds,
            'meshes': rows, 'terminalAcceptance': False,
            'scope': 'independent-glb-reimport-dimensions-pivot-ownership-byte-preservation'}


def run_audit(workspace, name):
    command = [sys.executable, '-E', '-s', '-S', '-B', str(PLUGIN / 'tools' / ('audit_' + name + '.py')),
               '--workspace', str(workspace), '--gate']
    result = subprocess.run(command, cwd=workspace, capture_output=True, text=True, timeout=180)
    try:
        verdict = json.loads(result.stdout)
    except (ValueError, UnicodeError) as exc:
        raise ValueError('Real interior-' + name + ' audit did not produce a valid verdict') from exc
    if (result.returncode != 0 or verdict.get('kind') != 'img2.gate-verdict'
            or verdict.get('version') != 1 or verdict.get('plugin') != 'interior'
            or verdict.get('gate') != 'interior-' + name or verdict.get('status') != 'pass'
            or verdict.get('reasons') != []):
        raise ValueError('Real interior-' + name + ' audit failed: ' + json.dumps(verdict.get('reasons', [])))
    return verdict['evidence']


def audit(workspace, *, blender_python, artifact_relative='artifact.json', spec_relative='scene-spec.json'):
    """Pass only the named mechanical scope, never a terminal/visual/browser gate."""
    workspace = Path(workspace).resolve(strict=True)
    snapshot = validate_artifact(workspace, artifact_relative, spec_relative)
    materials, delivery = run_audit(workspace, 'materials'), run_audit(workspace, 'delivery')
    expected_source = confined(workspace, snapshot['sourceRuntime']['path'])
    material_glb = materials.get('inputs', {}).get('glb', {})
    if (Path(material_glb.get('path', '')).resolve() != expected_source
            or material_glb.get('sha256') != snapshot['sourceRuntime']['sha256']):
        raise ValueError('Material audit is not bound to this artifact runtime GLB')
    program_pins = []
    for report in (materials, delivery):
        for binding in report.get('inputs', {}).values():
            path = Path(binding['path'])
            if not path.is_absolute() or not path.is_relative_to(workspace):
                raise ValueError('Program audit input is outside this exact child workspace')
            program_pins.append(checked_pin(workspace, {
                'path': path.relative_to(workspace).as_posix(), 'sha256': binding['sha256']}))
    if not Path(delivery['root']).is_relative_to(workspace):
        raise ValueError('Delivery root is outside this exact child workspace')
    asset_by_id = {row['id']: row for row in delivery['assets']}
    runtime = asset_by_id[delivery['runtime']['asset']]
    if (Path(runtime['resolvedPath']) != expected_source or runtime['sha256'] != snapshot['sourceRuntime']['sha256']):
        raise ValueError('Delivery audit runtime does not match the artifact provenance')
    reference_assets = [row for row in delivery['assets'] if row['role'] == 'source']
    if (len(reference_assets) != 1 or Path(reference_assets[0]['resolvedPath']) != confined(workspace, snapshot['reference']['path'])
            or reference_assets[0]['sha256'] != snapshot['reference']['sha256']):
        raise ValueError('Delivery audit reference does not match this child source')
    for row in delivery['assets']:
        path = Path(row['resolvedPath'])
        if not path.is_relative_to(workspace):
            raise ValueError('Delivery asset is outside this exact child workspace')
        program_pins.append(checked_pin(workspace, {
            'path': path.relative_to(workspace).as_posix(), 'sha256': row['sha256']}))
        if path.suffix.lower() == '.glb':
            _glb(path)
    command = [str(blender_python), '-E', '-s', '-B', str(PLUGIN / 'tools' / 'inspect_routed_artifact.py'),
               '--workspace', str(workspace), '--artifact', artifact_relative, '--spec', spec_relative]
    result = subprocess.run(command, cwd=workspace, capture_output=True, text=True, timeout=300)
    records = [line[len('IMG2_ROUTED_MEASUREMENTS '):] for line in result.stdout.splitlines()
               if line.startswith('IMG2_ROUTED_MEASUREMENTS ')]
    if result.returncode != 0 or len(records) != 1:
        raise ValueError('Independent Blender measurement producer is unavailable or failed')
    measurements = json.loads(records[0])
    if (measurements.get('artifact') != snapshot['artifact'] or measurements.get('inputs') != snapshot['inputs']
            or measurements.get('cameraSha256') != snapshot['cameraSha256']):
        raise ValueError('Independent measurements are stale or foreign to this artifact/camera/spec')
    for binding in snapshot['inputs'] + program_pins:
        checked_pin(workspace, binding)
    return {'kind': 'img2.interior-routed-audit', 'version': 1, 'status': 'pass',
            'scope': 'current-material-delivery-audits-and-independent-glb-handoff-replay',
            'evidence': {'artifact': snapshot['artifact'], 'inputs': snapshot['inputs'], 'auditInputs': program_pins,
                         'cameraSha256': snapshot['cameraSha256'], 'measurements': measurements,
                         'materialAudit': materials, 'deliveryAudit': delivery},
            'terminalAcceptance': False, 'missingTerminalProducers': list(MISSING_TERMINAL_PRODUCERS)}
