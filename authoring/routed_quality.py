# SPDX-License-Identifier: Apache-2.0
"""Independent delivered-GLB geometry and pixel producers; no semantic authority.

Only imported geometry is inspected. Component tags identify the admitted inventory,
but cannot establish that a mesh depicts the corresponding real reference part.
"""
import hashlib
import json
import math
from pathlib import Path

from authoring.routing import bounds, exclusions
from resources_lib.storage import confined
from tools.project_support import digest, read_json, write_json

ANGLES = (0, 90, 180, 270)
RESOLUTION = 256
# Relative geometric tolerance, not a quality-policy threshold. Shrinking triangles
# excludes boundary-only contact (shared edges/vertices) from surface crossings.
CONTACT_TOLERANCE = 1e-6


def inventory_coverage(rows, admitted, excluded):
    required = set(exclusions(admitted)) - set(exclusions(excluded))
    present = {row['component'] for row in rows}
    return {'required': sorted(required), 'present': sorted(present),
            'missing': sorted(required - present), 'unexpected': sorted(present - required),
            'passed': bool(required) and required == present,
            'scope': 'tagged-geometry-inventory-not-reference-part-recognition'}


def mesh_integrity(obj):
    """Inspect every actual triangle, including UV-split duplicates after welding.

    BVH overlap is Blender's triangle/triangle query, not bounding-box overlap.
    Triangle interiors are inset by a declared relative contact tolerance. Results
    establish no intersections above that tolerance, not mathematical exactness.
    Degenerate triangles fail rather than being silently omitted.
    """
    from mathutils.bvhtree import BVHTree
    mesh = obj.data
    mesh.calc_loop_triangles()
    vertices = [obj.matrix_world @ vertex.co for vertex in mesh.vertices]
    if not vertices or any(not math.isfinite(v) for point in vertices for v in point):
        raise ValueError('Missing or nonfinite delivered geometry: ' + obj.name)
    triangles = [tuple(face.vertices) for face in mesh.loop_triangles]
    if not triangles:
        raise ValueError('Delivered mesh has no physical triangles: ' + obj.name)
    box = bounds([{'bounds': [list(point) for point in vertices]}])
    span = max(box['max'][a] - box['min'][a] for a in range(3))
    if span <= 0:
        raise ValueError('Delivered mesh has zero extent: ' + obj.name)
    # Weld only exactly coincident imported positions: UV/material seams are not
    # holes, while small real gaps must remain visible in the topology result.
    welded, identities = {}, []
    for point in vertices:
        identities.append(welded.setdefault(tuple(point), len(welded)))
    edges, inset, faces = {}, [], []
    degenerate = 0
    for triangle in triangles:
        a, b, c = (vertices[index] for index in triangle)
        if (b - a).cross(c - a).length <= (span * CONTACT_TOLERANCE) ** 2:
            degenerate += 1
        ids = [identities[index] for index in triangle]
        for x, y in zip(ids, ids[1:] + ids[:1]):
            key = tuple(sorted((x, y)))
            edges[key] = edges.get(key, 0) + 1
        center = (a + b + c) / 3
        start = len(inset)
        inset.extend(center + (point - center) * (1 - CONTACT_TOLERANCE) for point in (a, b, c))
        faces.append((start, start + 1, start + 2))
    tree = BVHTree.FromPolygons(inset, faces, all_triangles=True, epsilon=0.0)
    intersections = sorted({tuple(sorted((a, b))) for a, b in tree.overlap(tree) if a != b})
    return {'name': obj.get('physical_id', obj.name), 'component': obj['component'],
            'triangles': len(triangles), 'degenerateTriangles': degenerate,
            'boundaryEdges': sum(count == 1 for count in edges.values()),
            'nonManifoldEdges': sum(count > 2 for count in edges.values()),
            'selfIntersectionCount': len(intersections),
            'intersectionPairs': [list(pair) for pair in intersections],
            'contactToleranceRelative': CONTACT_TOLERANCE,
            'scope': 'all-intra-mesh-triangle-interiors-not-inter-object-penetration'}


def produce_quality(workspace, snapshot, output_relative):
    """Reimport current bytes and create fresh geometry/capture evidence with bpy.

    The caller owns interpreter/source authenticity. This producer never executes
    the workspace builder or reads an editable quality-status report.
    """
    import bpy
    from mathutils import Vector
    workspace = Path(workspace).resolve(strict=True)
    output = confined(workspace, output_relative)
    if output.exists():
        raise ValueError('Quality captures are immutable; choose a fresh --quality-output directory')
    output.mkdir(parents=True)
    previous = bpy.context.window.scene
    scene = bpy.data.scenes.new('InteriorIndependentQuality')
    created_material = camera_data = None
    imported = []
    try:
        bpy.context.window.scene = scene
        bpy.ops.import_scene.gltf(filepath=str(confined(workspace, snapshot['model']['path'])))
        imported = list(scene.objects)
        bpy.context.view_layer.update()
        objects = [obj for obj in imported if obj.type == 'MESH']
        rows, integrity = [], []
        names = set()
        for obj in objects:
            name, component = obj.get('physical_id', obj.name), obj.get('component')
            if type(name) is not str or not name or name in names:
                raise ValueError('Missing or duplicate physical mesh identity')
            names.add(name)
            exclusions([component])
            points = [list(obj.matrix_world @ vertex.co) for vertex in obj.data.vertices]
            rows.append({'name': name, 'component': component, 'bounds': points})
            integrity.append(mesh_integrity(obj))
            # All delivered physical meshes participate, even if imported hidden.
            obj.hide_render = False
        box = bounds(rows)
        size = [box['max'][a] - box['min'][a] for a in range(3)]
        if any(value <= 0 for value in size):
            raise ValueError('Delivered scene has no volumetric envelope')
        intake = read_json(confined(workspace, snapshot['intake']['path']))
        coverage = inventory_coverage(rows, intake['components'], snapshot['excludeComponents'])
        # Render actual silhouettes with an opaque emission override, eliminating
        # lighting/texture/alpha as a way to hide absent structural geometry.
        scene.render.engine = 'CYCLES'
        scene.cycles.device = 'CPU'
        scene.cycles.samples = 1
        scene.cycles.use_denoising = False
        scene.cycles.seed = 17
        scene.render.resolution_x = scene.render.resolution_y = RESOLUTION
        scene.render.resolution_percentage = 100
        scene.render.film_transparent = True
        scene.render.image_settings.file_format = 'PNG'
        scene.render.image_settings.color_mode = 'RGBA'
        scene.render.image_settings.color_depth = '8'
        scene.render.use_file_extension = True
        created_material = bpy.data.materials.new('IndependentOpaqueSilhouette')
        created_material.use_nodes = True
        nodes = created_material.node_tree.nodes
        nodes.clear()
        shader = nodes.new('ShaderNodeEmission')
        shader.inputs['Color'].default_value = (1, 1, 1, 1)
        target = nodes.new('ShaderNodeOutputMaterial')
        created_material.node_tree.links.new(shader.outputs[0], target.inputs['Surface'])
        scene.view_layers[0].material_override = created_material
        camera_data = bpy.data.cameras.new('IndependentOrthographicCamera')
        camera = bpy.data.objects.new('IndependentOrthographicCamera', camera_data)
        scene.collection.objects.link(camera)
        scene.camera = camera
        camera_data.type = 'ORTHO'
        camera_data.ortho_scale = max(size) * 1.25
        camera_data.clip_start = max(size) * 0.001
        camera_data.clip_end = max(size) * 10
        center = Vector([(box['min'][a] + box['max'][a]) / 2 for a in range(3)])
        captures = []
        for angle in ANGLES:
            radians = math.radians(angle)
            # Native -Y is canonical glTF +Z; orbit around native +Z (glTF +Y).
            camera.location = center + Vector((math.sin(radians), -math.cos(radians), 0)) * max(size) * 3
            camera.rotation_euler = (center - camera.location).to_track_quat('-Z', 'Y').to_euler()
            bpy.context.view_layer.update()
            path = output / ('turntable-' + str(angle) + '.png')
            scene.render.filepath = str(path)
            bpy.ops.render.render(write_still=True)
            # Decode persisted PNG pixels, not a candidate-supplied mask or area.
            image = bpy.data.images.load(str(path), check_existing=False)
            try:
                pixels = list(image.pixels)
                area = sum(value >= 0.5 for value in pixels[3::4])
                if len(pixels) != RESOLUTION * RESOLUTION * 4 or area == 0:
                    raise ValueError('Empty or invalid actual silhouette capture')
            finally:
                bpy.data.images.remove(image)
            captures.append({'angle': angle, 'path': path.relative_to(workspace).as_posix(),
                             'sha256': digest(path), 'foregroundPixels': area,
                             'positionNativeZUp': list(camera.location),
                             'rotationNativeXYZ': list(camera.rotation_euler)})
        areas = {capture['angle']: capture['foregroundPixels'] for capture in captures}
        measurements = {'width': size[0], 'height': size[2], 'depth': size[1],
                        'frontArea': areas[0], 'sideArea': min(areas[90], areas[270]),
                        'selfIntersectionCount': sum(row['selfIntersectionCount'] for row in integrity)}
        checks = {
            'depth': measurements['depth'] / max(measurements['width'], measurements['height']) >= 0.15,
            'sideArea': measurements['sideArea'] / measurements['frontArea'] >= 0.25,
            'selfIntersection': measurements['selfIntersectionCount'] == 0,
            'nondegenerate': all(row['degenerateTriangles'] == 0 for row in integrity),
            'componentInventory': coverage['passed'],
        }
        setup = {'resolution': RESOLUTION, 'samples': 1, 'engine': 'CYCLES',
                 'camera': 'ORTHO', 'orthoScale': camera_data.ortho_scale,
                 'mask': 'persisted-png-alpha>=0.5', 'material': 'opaque-white-emission'}
        view_set_sha = hashlib.sha256(json.dumps({'captures': captures, 'setup': setup},
                                                sort_keys=True, separators=(',', ':'),
                                                allow_nan=False).encode()).hexdigest()
        report = {'kind': 'img2.interior-routed-structure', 'version': 1,
                  'status': 'pass' if all(checks.values()) else 'fail',
                  'scope': 'volumetric-geometry-and-opaque-orthographic-silhouettes',
                  'artifact': snapshot['artifact'], 'inputs': snapshot['inputs'],
                  'cameraSha256': snapshot['cameraSha256'], 'blenderVersion': bpy.app.version_string,
                  'evidence': {'artifactSha256': snapshot['artifact']['sha256'],
                               'referenceSha256': snapshot['reference']['sha256'],
                               'specSha256': snapshot['spec']['sha256'],
                               'sourceRuntimeSha256': snapshot['sourceRuntime']['sha256'],
                               'modelSha256': snapshot['model']['sha256'],
                               'cameraSha256': snapshot['cameraSha256'], 'viewSetSha256': view_set_sha},
                  'measurements': measurements, 'checks': checks, 'meshes': integrity,
                  'componentCoverage': coverage, 'captures': captures,
                  'captureSetup': setup,
                  'partCoverageStatus': 'unevaluated', 'semanticReviewStatus': 'unevaluated',
                  'terminalAcceptance': False}
        write_json(output / 'structure.json', report)
        return report
    finally:
        bpy.context.window.scene = previous
        bpy.data.batch_remove(list(scene.objects))
        bpy.data.scenes.remove(scene)
        if created_material is not None:
            bpy.data.materials.remove(created_material)
        if camera_data is not None:
            bpy.data.cameras.remove(camera_data)
