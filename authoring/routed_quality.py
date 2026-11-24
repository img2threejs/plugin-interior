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


def _coplanar(a, b, errors_a, errors_b):
    """Bound orient3d error from the source float32 coordinate intervals.

    This is input uncertainty, not extra geometric contact allowance. In
    particular a tiny nonzero Z value has a tiny ULP, unlike a tilted XYZ point.
    """
    def contains(triangle, points, errors, point_errors):
        u, v = _subtract(triangle[1], triangle[0]), _subtract(triangle[2], triangle[0])
        eu = tuple(x + y for x, y in zip(errors[1], errors[0]))
        ev = tuple(x + y for x, y in zip(errors[2], errors[0]))
        normal = _cross(u, v)
        cross_error = []
        for axis in range(3):
            j, k = (axis + 1) % 3, (axis + 2) % 3
            cross_error.append(abs(u[j]) * ev[k] + abs(v[k]) * eu[j] + eu[j] * ev[k]
                               + abs(u[k]) * ev[j] + abs(v[j]) * eu[k] + eu[k] * ev[j])
        for point, error in zip(points, point_errors):
            w = _subtract(point, triangle[0])
            ew = tuple(x + y for x, y in zip(error, errors[0]))
            bound = sum(abs(n) * e + ne * (abs(value) + e)
                        for n, ne, value, e in zip(normal, cross_error, w, ew))
            bound += math.hypot(*normal) * _PLANE_ROUNDOFF
            if abs(_dot(normal, w)) > bound:
                return False
        return True

    return contains(a, b, errors_a, errors_b) and contains(b, a, errors_b, errors_a)


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


def _interiors_cross(a, b, normal_a, normal_b, errors_a, errors_b):
    if normal_a is None or normal_b is None:
        return False  # Degeneracy is reported separately and fails the gate.
    # Preserve the original per-triangle relative contact policy, but apply it in
    # Python double precision only after the welded BVH has found candidates.
    def inset(triangle, errors):
        center = tuple(sum(point[axis] for point in triangle) / 3 for axis in range(3))
        mean_error = tuple(sum(point[axis] for point in errors) / 3 for axis in range(3))
        points = tuple(tuple(c + (v - c) * (1 - CONTACT_TOLERANCE) for c, v in zip(center, point))
                       for point in triangle)
        uncertainty = tuple(tuple(e * (1 - CONTACT_TOLERANCE) + c * CONTACT_TOLERANCE
                                  for e, c in zip(error, mean_error)) for error in errors)
        return points, uncertainty

    a, errors_a = inset(a, errors_a)
    b, errors_b = inset(b, errors_b)
    if (_coplanar(a, b, errors_a, errors_b)
            and _coplanar_area(a, b, normal_a) > _PLANE_ROUNDOFF ** 2):
        return True
    # An uncertain coplanar classification must never hide a genuine 3D crossing.
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


def _candidate_pairs(points, faces, geometry, normals, areas):
    """Compiled BVH broad phase, including otherwise invisible coplanar pairs.

    Each face gets two auxiliary tents with its inset triangle as base. A
    coplanar overlap intersects a tent side of at least one face (containment
    included). Original faces keep welded indices for ordinary 3D crossings.
    Separate trees never query auxiliary against auxiliary: those irrelevant
    overlaps can be quadratic on dense rods. Predicates inspect source faces.
    """
    import numpy as np
    from mathutils.bvhtree import BVHTree
    valid = np.flatnonzero(areas > 0)
    triangles = geometry[valid]
    centers = triangles.mean(axis=1)
    height = np.maximum(np.linalg.norm(triangles[:, 1:] - triangles[:, :1], axis=2).max(axis=1),
                        np.linalg.norm(triangles[:, 2] - triangles[:, 1], axis=1))
    peaks = np.stack((centers + normals[valid] * height[:, None],
                      centers - normals[valid] * height[:, None]), axis=1)
    bases = centers[:, None, :] + (triangles - centers[:, None, :]) * (1 - CONTACT_TOLERANCE)
    base_ids = np.arange(len(valid) * 3).reshape(-1, 3)
    peak_ids = bases.size // 3 + np.arange(len(valid) * 2).reshape(-1, 2)
    sides = np.empty((len(valid), 2, 3, 3), dtype=np.int64)
    sides[:, :, :, 0] = base_ids[:, None, :]
    sides[:, :, :, 1] = np.roll(base_ids, -1, axis=1)[:, None, :]
    sides[:, :, :, 2] = peak_ids[:, :, None]
    originals = BVHTree.FromPolygons(points, faces, all_triangles=True, epsilon=0.0)
    tents = BVHTree.FromPolygons(np.concatenate((bases.reshape(-1, 3), peaks.reshape(-1, 3))),
                                sides.reshape(-1, 3), all_triangles=True, epsilon=0.0)
    pairs = {tuple(sorted((a, b))) for a, b in originals.overlap(originals) if a != b}
    for a, side in originals.overlap(tents):
        b = int(valid[side // 6])
        if a != b:
            pairs.add(tuple(sorted((a, b))))
    # BVH excludes identical indexed faces; duplicates are genuine overlaps.
    _, inverse, counts = np.unique(np.sort(faces, axis=1), axis=0, return_inverse=True, return_counts=True)
    if np.any(counts > 1):
        order = np.argsort(inverse, kind='stable')
        starts = np.concatenate(([0], np.cumsum(counts)))
        for group in np.flatnonzero(counts > 1):
            ids = order[starts[group]:starts[group + 1]]
            for index, a in enumerate(ids):
                pairs.update(tuple(sorted((int(a), int(b)))) for b in ids[index + 1:])
    return pairs


def inventory_coverage(rows, admitted, excluded):
    required = set(exclusions(admitted)) - set(exclusions(excluded))
    present = {row['component'] for row in rows}
    return {'required': sorted(required), 'present': sorted(present),
            'missing': sorted(required - present), 'unexpected': sorted(present - required),
            'passed': bool(required) and required == present,
            'scope': 'tagged-geometry-inventory-not-reference-part-recognition'}


def mesh_integrity(obj):
    """Inspect actual triangle interiors without world-transform float32 drift.

    Invertible affine transforms preserve intersection and centroid insetting, so
    predicates use source-local coordinates. Physical degeneracy still uses
    world geometry. Singular transforms are measured in world space instead.
    """
    import numpy as np
    mesh = obj.data
    mesh.calc_loop_triangles()
    source = np.empty(len(mesh.vertices) * 3, dtype=np.float32)
    mesh.vertices.foreach_get('co', source)
    source = source.reshape(-1, 3)
    matrix = np.asarray(obj.matrix_world, dtype=np.float64)
    world = source.astype(np.float64) @ matrix[:3, :3].T + matrix[:3, 3]
    if not len(source) or not np.isfinite(world).all():
        raise ValueError('Missing or nonfinite delivered geometry: ' + obj.name)
    indices = np.empty(len(mesh.loop_triangles) * 3, dtype=np.int32)
    mesh.loop_triangles.foreach_get('vertices', indices)
    indices = indices.reshape(-1, 3)
    if not len(indices):
        raise ValueError('Delivered mesh has no physical triangles: ' + obj.name)
    world_span = np.ptp(world, axis=0).max()
    if world_span <= 0:
        raise ValueError('Delivered mesh has zero extent: ' + obj.name)
    world_triangles = (world[indices] - world.min(axis=0)) / world_span
    world_area = np.linalg.norm(np.cross(world_triangles[:, 1] - world_triangles[:, 0],
                                         world_triangles[:, 2] - world_triangles[:, 0]), axis=1)
    # Half a float32 ULP per stored coordinate; do not replace this with a larger
    # mesh-wide epsilon, which confuses true small gaps with rounding uncertainty.
    uncertainty = np.abs(np.spacing(source)).astype(np.float64) / 2
    if np.linalg.det(matrix[:3, :3]) == 0:
        coordinates = world
        uncertainty = uncertainty @ np.abs(matrix[:3, :3]).T
    else:
        coordinates = source.astype(np.float64)
    low, high = coordinates.min(axis=0), coordinates.max(axis=0)
    span = (high - low).max()
    coordinates = (coordinates - (low + high) / 2) / span
    uncertainty /= span
    points, identities = np.unique(coordinates, axis=0, return_inverse=True)
    errors = np.zeros_like(points)
    np.maximum.at(errors, identities, uncertainty)
    faces = identities[indices]
    geometry, triangle_errors = points[faces], errors[faces]
    raw_normals = np.cross(geometry[:, 1] - geometry[:, 0], geometry[:, 2] - geometry[:, 0])
    areas = np.linalg.norm(raw_normals, axis=1)
    normals = np.divide(raw_normals, areas[:, None], out=np.zeros_like(raw_normals), where=areas[:, None] != 0)
    edges = np.concatenate((faces[:, [0, 1]], faces[:, [1, 2]], faces[:, [2, 0]]))
    _, edge_counts = np.unique(np.sort(edges, axis=1), axis=0, return_counts=True)
    intersections = []
    for a, b in sorted(_candidate_pairs(points, faces, geometry, normals, areas)):
        if _interiors_cross(geometry[a].tolist(), geometry[b].tolist(),
                            normals[a].tolist() if areas[a] else None,
                            normals[b].tolist() if areas[b] else None,
                            triangle_errors[a].tolist(), triangle_errors[b].tolist()):
            intersections.append((a, b))
    return {'name': obj.get('physical_id', obj.name), 'component': obj['component'],
            'triangles': len(faces), 'degenerateTriangles': int(np.count_nonzero(world_area <= CONTACT_TOLERANCE ** 2)),
            'boundaryEdges': int(np.count_nonzero(edge_counts == 1)),
            'nonManifoldEdges': int(np.count_nonzero(edge_counts > 2)),
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
