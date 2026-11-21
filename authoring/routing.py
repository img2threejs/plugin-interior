# SPDX-License-Identifier: Apache-2.0
"""Local ownership and measured scene handoff; no base/provider imports or calls."""
import hashlib
import json
import math
from pathlib import Path
import re
import struct


def exclusions(value):
    if (not isinstance(value, list) or any(not isinstance(item, str) or not item.strip()
                                         or item != item.strip() for item in value)
            or len(set(value)) != len(value)):
        raise ValueError('excludeComponents must contain unique nonempty exact component identities')
    return tuple(value)


def apply_exclusions(objects, excluded, remove):
    """Remove actual authored objects, including hidden meshes; never merely hide them."""
    removed = []
    for obj in list(objects):
        if obj.get('component') in excluded:
            removed.append({'name': obj.name, 'component': obj['component']})
            remove(obj)
    return removed


def bounds(rows):
    if not rows:
        raise ValueError('Missing measured geometry')
    points = [point for row in rows for point in row['bounds']]
    if not points or any(len(p) != 3 or any(type(v) not in (int, float) or not math.isfinite(v)
                                           for v in p) for p in points):
        raise ValueError('Invalid measured geometry bounds')
    return {'min': [min(p[a] for p in points) for a in range(3)],
            'max': [max(p[a] for p in points) for a in range(3)]}


def room_dimensions(rows, components):
    """Measure the explicitly identified shell envelope, not inferred image dimensions."""
    selected = exclusions(components)
    if not selected:
        raise ValueError('Missing roomMeasurementComponents: identify actual authored shell meshes')
    present = {row['component'] for row in rows}
    if not set(selected) <= present:
        raise ValueError('Missing room measurement geometry: ' + ', '.join(sorted(set(selected) - present)))
    box = bounds([row for row in rows if row['component'] in selected])
    size = [box['max'][a] - box['min'][a] for a in range(3)]
    if any(v <= 0 or not math.isfinite(v) for v in size):
        raise ValueError('Room measurements must have positive finite width, height and depth')
    # Rows use native Blender Z-up; the delivered artifact uses glTF Y-up.
    return {'width': size[0], 'height': size[2], 'depth': size[1]}


def _glb(path):
    data = Path(path).read_bytes()
    if len(data) < 28 or struct.unpack_from('<4sII', data) != (b'glTF', 2, len(data)):
        raise ValueError('Invalid exported GLB header')
    length, kind = struct.unpack_from('<I4s', data, 12)
    if kind != b'JSON' or length % 4 or 20 + length + 8 > len(data):
        raise ValueError('Invalid exported GLB JSON')
    doc = json.loads(data[20:20 + length])
    offset = 20 + length
    binary_length, binary_kind = struct.unpack_from('<I4s', data, offset)
    if binary_kind != b'BIN\x00' or binary_length % 4 or offset + 8 + binary_length != len(data):
        raise ValueError('Expected a self-contained Blender GLB with one BIN chunk')
    if doc.get('asset', {}).get('version') != '2.0' or doc.get('skins') or doc.get('animations'):
        raise ValueError('Interior handoff requires static glTF 2.0 without a skeleton')
    if any('uri' in item for key in ('buffers', 'images') for item in doc.get(key, [])):
        raise ValueError('Composition GLB must embed all buffers and images')
    if any(key in doc for key in ('extensionsRequired',)) and any(
            item not in ('KHR_materials_transmission', 'KHR_materials_volume', 'KHR_materials_ior',
                         'KHR_materials_specular', 'KHR_materials_sheen', 'KHR_materials_clearcoat',
                         'KHR_materials_emissive_strength', 'KHR_materials_unlit')
            for item in doc.get('extensionsRequired', [])):
        raise ValueError('Unsupported required GLB extension for measured handoff')
    return doc, data[offset:]


def normalized_glb(source, origin):
    """Preserve all geometry/material/image bytes; add only a composition parent transform."""
    doc, binary = _glb(source)
    scenes = doc.get('scenes', [])
    scene_index = doc.get('scene')
    if type(scene_index) is not int or not 0 <= scene_index < len(scenes) or len(scenes) != 1:
        raise ValueError('Composition requires one explicit exported scene')
    nodes = doc.get('nodes', [])
    roots = scenes[scene_index].get('nodes', [])
    if not roots or len(set(roots)) != len(roots):
        raise ValueError('Missing or duplicate exported scene roots')
    offset = [-origin[0], -origin[2], origin[1]]
    for node in nodes:
        extras = node.get('extras', {})
        if 'curtain_plane_origin' in extras:
            point = extras['curtain_plane_origin']
            if not isinstance(point, list) or len(point) != 3:
                raise ValueError('Invalid exported curtain origin')
            extras['curtain_plane_origin'] = [point[a] + offset[a] for a in range(3)]
    scenes[scene_index]['nodes'] = [len(nodes)]
    nodes.append({'name': 'InteriorCompositionPivot', 'translation': offset, 'children': roots})
    document = json.dumps(doc, separators=(',', ':'), allow_nan=False).encode()
    document += b' ' * (-len(document) % 4)
    return (struct.pack('<4sII', b'glTF', 2, 20 + len(document) + len(binary))
            + struct.pack('<I4s', len(document), b'JSON') + document + binary)


def measure_export(path):
    """Reopen the real exported GLB with Blender and measure its actual world vertices."""
    import bpy
    _glb(path)
    previous = bpy.context.window.scene
    temporary = bpy.data.scenes.new('InteriorHandoffMeasurement')
    try:
        bpy.context.window.scene = temporary
        bpy.ops.import_scene.gltf(filepath=str(path))
        bpy.context.view_layer.update()
        rows = []
        for obj in temporary.objects:
            if obj.type != 'MESH':
                continue
            component = obj.get('component')
            if not component:
                raise ValueError('Exported mesh lost its component identity: ' + obj.name)
            points = [list(obj.matrix_world @ vertex.co) for vertex in obj.data.vertices]
            row = {'name': obj.get('physical_id', obj.name), 'component': component, 'bounds': points}
            box = bounds([row])
            rows.append({**row, 'bounds': [box['min'], box['max']], 'vertices': len(points)})
        bounds(rows)
        return rows
    finally:
        bpy.context.window.scene = previous
        bpy.data.batch_remove(list(temporary.objects))
        bpy.data.scenes.remove(temporary)


def produce_artifact(context, runtime, measure=measure_export):
    """Emit D18 only after current exported bytes and shell measurements are available.

    `measure` is a local measurement boundary for offline contract tests. Production
    always uses Blender's actual GLB importer; this is not a gate/visual acceptance.
    """
    from tools.project_support import digest, read_json, write_bytes, write_json
    routing = context.spec.get('routing')
    if not isinstance(routing, dict) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.:-]{0,159}',
                                                       str(routing.get('objectId', ''))):
        raise ValueError('Routed export requires routing.objectId')
    excluded = set(context.exclude_components)
    child = read_json(context.resolve('state.json'))['routeChild']
    owner = child.get('ownerRoute')
    if type(owner) is not str or not owner:
        raise ValueError('Routed export requires the recorded ownerRoute')
    runtime = context.resolve(Path(runtime).relative_to(context.workspace))
    source_hash = digest(runtime)
    rows = measure(runtime)
    if any(row['component'] in excluded for row in rows):
        raise ValueError('Exported scene still contains separately owned components')
    components = context.spec['authoring'].get('roomMeasurementComponents', [])
    dimensions = room_dimensions(rows, components)
    box = bounds(rows)
    origin = [(box['min'][0] + box['max'][0]) / 2,
              (box['min'][1] + box['max'][1]) / 2, box['min'][2]]
    output = context.resolve(context.evidence.relative_to(context.workspace) / 'room-composition.glb')
    receipt_path = context.resolve(context.evidence.relative_to(context.workspace) / 'scene-measurements.json')
    artifact_path = context.resolve('artifact.json')
    if any(p.exists() for p in (output, receipt_path, artifact_path)):
        raise ValueError('Routed artifacts are immutable; use a new child revision/workspace')
    payload = normalized_glb(runtime, origin)
    if digest(runtime) != source_hash:
        raise ValueError('Runtime GLB changed during handoff measurement')
    write_bytes(output, payload)
    output_hash = digest(output)
    measured = measure(output)
    delivered = bounds(measured)
    pivot = [(delivered['min'][0] + delivered['max'][0]) / 2,
             (delivered['min'][1] + delivered['max'][1]) / 2, delivered['min'][2]]
    actual_dimensions = room_dimensions(measured, components)
    if (any(abs(v) > 1e-5 for v in pivot)
            or any(not math.isclose(dimensions[k], actual_dimensions[k], rel_tol=1e-5, abs_tol=1e-5)
                   for k in dimensions)):
        raise ValueError('Exported composition pivot or room measurements failed verification')
    if digest(output) != output_hash or digest(runtime) != source_hash:
        raise ValueError('Exported bytes changed during handoff verification')
    receipt = {'schemaVersion': 1, 'basis': 'exported-glb-world-vertices',
               'roomDimensionBasis': 'explicit-authored-shell-envelope',
               'roomMeasurementComponents': list(components), 'roomDimensions': actual_dimensions,
               'sourceRuntime': {'path': str(runtime.relative_to(context.workspace)), 'sha256': source_hash},
               'compositionSha256': digest(output), 'nativeOriginBeforeNormalization': origin,
               'boundsNativeZUp': delivered, 'meshes': measured,
               'excludeComponents': sorted(excluded), 'visualAcceptanceEvaluated': False}
    write_json(receipt_path, receipt)
    artifact = {'objectId': routing['objectId'], 'outputKind': '3d-scene',
                'ownerRoute': owner, 'ownedObjectIds': [routing['objectId']],
                'excludeComponents': receipt['excludeComponents'],
                'files': [{'path': str(p.relative_to(context.workspace)), 'sha256': digest(p)}
                          for p in (output, receipt_path)],
                'frame': {'units': 'm', 'up': '+Y', 'forward': '+Z'}, 'pivot': 'bottom-centre',
                'scaleEstimate': 1.0, 'rig': None, 'roomDimensions': actual_dimensions}
    write_json(artifact_path, artifact)
    return artifact
