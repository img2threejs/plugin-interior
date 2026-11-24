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
# Relative geometric contact tolerance, not a quality-policy threshold.
CONTACT_TOLERANCE = 1e-6
# Plane arithmetic uses double precision after normalizing the mesh envelope.
_PLANE_ROUNDOFF = 64 * math.ulp(1.0)


def _subtract(a, b):
    return tuple(x - y for x, y in zip(a, b))


def _dot(a, b):
    return sum(x * y for x, y in zip(a, b))


def _cross(a, b):
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2],
            a[0] * b[1] - a[1] * b[0])


def _distances(triangle, other, normal):
    return [_dot(_subtract(point, other[0]), normal) for point in triangle]


def _coplanar(a, b, normal_a, normal_b):
    return (max(abs(value) for value in _distances(a, b, normal_b)) <= _PLANE_ROUNDOFF
            and max(abs(value) for value in _distances(b, a, normal_a)) <= _PLANE_ROUNDOFF)


def _coplanar_area(a, b, normal):
    """Clip projected triangles; shared edges/points have zero intersection area."""
    axis = max(range(3), key=lambda index: abs(normal[index]))
    project = lambda point: tuple(value for index, value in enumerate(point) if index != axis)
    polygon, clip = list(map(project, a)), list(map(project, b))

    def side(start, end, point):
        return ((end[0] - start[0]) * (point[1] - start[1])
                - (end[1] - start[1]) * (point[0] - start[0]))

    orientation = 1 if side(*clip) > 0 else -1
    for start, end in zip(clip, clip[1:] + clip[:1]):
        output = []
        for previous, current in zip(polygon[-1:] + polygon[:-1], polygon):
            before, after = orientation * side(start, end, previous), orientation * side(start, end, current)
            if (before >= 0) != (after >= 0):
                fraction = before / (before - after)
                output.append(tuple(x + fraction * (y - x) for x, y in zip(previous, current)))
            if after >= 0:
                output.append(current)
        polygon = output
        if not polygon:
            return 0
    origin = polygon[0]
    relative = [(point[0] - origin[0], point[1] - origin[1]) for point in polygon]
    return abs(math.fsum(a[0] * b[1] - a[1] * b[0]
                         for a, b in zip(relative, relative[1:] + relative[:1]))) / 2


def _interiors_cross(a, b, normal_a, normal_b):
    if normal_a is None or normal_b is None:
        return False  # Degeneracy is reported separately and fails the gate.
    # Preserve the original per-triangle relative contact policy, but apply it in
    # Python double precision only after the welded BVH has found candidates.
    def inset(triangle):
        center = tuple(sum(point[axis] for point in triangle) / 3 for axis in range(3))
        return tuple(tuple(c + (v - c) * (1 - CONTACT_TOLERANCE) for c, v in zip(center, point))
                     for point in triangle)

    a, b = inset(a), inset(b)
    if _coplanar(a, b, normal_a, normal_b):
        return _coplanar_area(a, b, normal_a) > _PLANE_ROUNDOFF ** 2
    distances_a, distances_b = _distances(a, b, normal_b), _distances(b, a, normal_a)
    # Both interiors must straddle the other plane: touching along a shared edge
    # or only at one vertex is not a crossing, even when BVH float32 math drifts.
    if any(min(values) >= -_PLANE_ROUNDOFF or max(values) <= _PLANE_ROUNDOFF
           for values in (distances_a, distances_b)):
        return False
    direction = _cross(normal_a, normal_b)
    length = math.hypot(*direction)
    direction = tuple(value / length for value in direction)

    def interval(triangle, distances):
        positions = []
        for index, point in enumerate(triangle):
            following = (index + 1) % 3
            before, after = distances[index], distances[following]
            if before == 0:
                positions.append(_dot(point, direction))
            if before * after < 0:
                fraction = before / (before - after)
                intersection = tuple(x + fraction * (y - x) for x, y in zip(point, triangle[following]))
                positions.append(_dot(intersection, direction))
        return min(positions), max(positions)

    start_a, end_a = interval(a, distances_a)
    start_b, end_b = interval(b, distances_b)
    return min(end_a, end_b) - max(start_a, start_b) > _PLANE_ROUNDOFF


def _coplanar_pairs(triangles, normals, axis):
    """Supplement BVH's coplanar/shared-edge exclusions with an AABB sweep."""
    boxes = [(tuple(min(point[a] for point in triangle) for a in range(3)),
              tuple(max(point[a] for point in triangle) for a in range(3))) for triangle in triangles]
    other_axes = tuple(a for a in range(3) if a != axis)
    active = []
    for index in sorted(range(len(boxes)), key=lambda index: boxes[index][0][axis]):
        low, high = boxes[index]
        active = [other for other in active if boxes[other][1][axis] >= low[axis]]
        for other in active:
            normal_a, normal_b = normals[other], normals[index]
            if (any(boxes[other][1][a] < low[a] or high[a] < boxes[other][0][a] for a in other_axes)
                    or normal_a is None or normal_b is None
                    or abs(_dot(normal_a, normal_b)) < 1 - _PLANE_ROUNDOFF):
                continue
            if _coplanar(triangles[other], triangles[index], normal_a, normal_b):
                yield tuple(sorted((other, index)))
        active.append(index)


def inventory_coverage(rows, admitted, excluded):
    required = set(exclusions(admitted)) - set(exclusions(excluded))
    present = {row['component'] for row in rows}
    return {'required': sorted(required), 'present': sorted(present),
            'missing': sorted(required - present), 'unexpected': sorted(present - required),
            'passed': bool(required) and required == present,
            'scope': 'tagged-geometry-inventory-not-reference-part-recognition'}


def mesh_integrity(obj):
    """Inspect every actual triangle, welding only exactly equal imported vertices.

    A welded BVH finds noncoplanar candidates without losing shared topology.
    Double-precision interior predicates reject boundary-only contact; a coplanar
    sweep retains fold/duplicate/overlap detection that BVH itself excludes.
    Geometry is normalized before predicates, keeping the declared relative
    tolerance independent of the mesh's world translation or scale.
    """
    from mathutils.bvhtree import BVHTree
    mesh = obj.data
    mesh.calc_loop_triangles()
    vertices = [tuple(obj.matrix_world @ vertex.co) for vertex in mesh.vertices]
    if not vertices or any(not math.isfinite(v) for point in vertices for v in point):
        raise ValueError('Missing or nonfinite delivered geometry: ' + obj.name)
    triangles = [tuple(face.vertices) for face in mesh.loop_triangles]
    if not triangles:
        raise ValueError('Delivered mesh has no physical triangles: ' + obj.name)
    box = bounds([{'bounds': [list(point) for point in vertices]}])
    axis = max(range(3), key=lambda a: box['max'][a] - box['min'][a])
    span = box['max'][axis] - box['min'][axis]
    if span <= 0:
        raise ValueError('Delivered mesh has zero extent: ' + obj.name)
    welded, identities = {}, []
    for point in vertices:
        identities.append(welded.setdefault(point, len(welded)))
    points = [tuple((point[a] - box['min'][a]) / span for a in range(3)) for point in welded]
    faces = [tuple(identities[index] for index in triangle) for triangle in triangles]
    geometry, normals, edges = [], [], {}
    degenerate = 0
    for face in faces:
        triangle = tuple(points[index] for index in face)
        normal = _cross(_subtract(triangle[1], triangle[0]), _subtract(triangle[2], triangle[0]))
        area = math.hypot(*normal)
        if area <= CONTACT_TOLERANCE ** 2:
            degenerate += 1
        geometry.append(triangle)
        normals.append(tuple(value / area for value in normal) if area else None)
        for x, y in zip(face, face[1:] + face[:1]):
            key = tuple(sorted((x, y)))
            edges[key] = edges.get(key, 0) + 1
    tree = BVHTree.FromPolygons(points, faces, all_triangles=True, epsilon=0.0)
    candidates = {tuple(sorted((a, b))) for a, b in tree.overlap(tree) if a != b}
    candidates.update(_coplanar_pairs(geometry, normals, axis))
    intersections = sorted((a, b) for a, b in candidates
                           if _interiors_cross(geometry[a], geometry[b], normals[a], normals[b]))
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
