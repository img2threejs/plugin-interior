# SPDX-License-Identifier: Apache-2.0
"""Inventory negatives and opt-in actual native triangle-intersection producers."""
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from authoring.routed_quality import inventory_coverage


class InventoryCoverageTests(unittest.TestCase):
    def test_tagged_geometry_cannot_be_confused_with_semantic_part_review(self):
        result = inventory_coverage([{'component': 'shell'}], ['shell', 'actor-17'], ['actor-17'])
        self.assertTrue(result['passed'])
        self.assertIn('not-reference-part-recognition', result['scope'])
        self.assertNotIn('partCoveragePassed', result)

    def test_missing_unexpected_and_empty_inventory_fail(self):
        for rows, admitted, excluded in (([], ['shell'], []),
                                         ([{'component': 'label'}], ['shell'], []),
                                         ([{'component': 'shell'}, {'component': 'actor-17'}],
                                          ['shell', 'actor-17'], ['actor-17']),
                                         ([], ['actor-17'], ['actor-17'])):
            with self.subTest(rows=rows):
                self.assertFalse(inventory_coverage(rows, admitted, excluded)['passed'])


@unittest.skipUnless(os.environ.get('IMG2_BLENDER_PYTHON'), 'Set IMG2_BLENDER_PYTHON for native geometry')
class NativeTriangleIntegrityTests(unittest.TestCase):
    def test_actual_crossings_adjacency_and_degeneracy(self):
        script = r'''
import json, sys
import bpy
sys.path.insert(0, sys.argv[1])
from authoring.routed_quality import mesh_integrity
bpy.ops.wm.read_factory_settings(use_empty=True)
def inspect(name, points, faces):
    data = bpy.data.meshes.new(name)
    data.from_pydata(points, [], faces)
    data.update()
    obj = bpy.data.objects.new(name, data)
    bpy.context.scene.collection.objects.link(obj)
    obj['component'] = 'shell'
    return mesh_integrity(obj)
# The interiors cross; there is no shared topological vertex or edge.
crossing = inspect('crossing', [(-1,-1,0),(1,-1,0),(0,1,0),
                                 (0,-0.5,-1),(0,-0.5,1),(0,0.5,0)], [(0,1,2),(3,4,5)])
assert crossing['selfIntersectionCount'] == 1, crossing
# Coplanar neighbouring triangles and UV-duplicated edge contact are not folds.
adjacent = inspect('adjacent', [(0,0,0),(1,0,0),(1,1,0),
                               (0,0,0),(1,1,0),(0,1,0)], [(0,1,2),(3,4,5)])
assert adjacent['selfIntersectionCount'] == 0, adjacent
assert adjacent['boundaryEdges'] == 4, adjacent
flat = inspect('degenerate', [(0,0,0),(1,0,0),(2,0,0)], [(0,1,2)])
assert flat['degenerateTriangles'] == 1, flat
print('NATIVE_TRIANGLE_INTEGRITY ' + json.dumps({'crossing': crossing, 'adjacent': adjacent, 'degenerate': flat}))
'''
        result = subprocess.run([os.environ['IMG2_BLENDER_PYTHON'], '-I', '-B', '-c', script, str(ROOT)],
                                capture_output=True, text=True, timeout=90)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        rows = [line.removeprefix('NATIVE_TRIANGLE_INTEGRITY ') for line in result.stdout.splitlines()
                if line.startswith('NATIVE_TRIANGLE_INTEGRITY ')]
        self.assertEqual(len(rows), 1)
        self.assertEqual(json.loads(rows[0])['crossing']['selfIntersectionCount'], 1)


    def geometry_results(self, fixture):
        script = r'''
import json, sys
import bpy
sys.path.insert(0, sys.argv[1])
from authoring.routed_quality import mesh_integrity
bpy.ops.wm.read_factory_settings(use_empty=True)
def inspect(name, points, faces):
    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata(points, [], faces)
    mesh.update()
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.scene.collection.objects.link(obj)
    obj['component'] = 'shell'
    return mesh_integrity(obj)
''' + fixture + "\nprint('GEOMETRY_RESULTS ' + json.dumps(results))"
        process = subprocess.run([os.environ['IMG2_BLENDER_PYTHON'], '-I', '-B', '-c',
                                  script, str(ROOT)], capture_output=True, text=True, timeout=90)
        self.assertEqual(process.returncode, 0, process.stdout + process.stderr)
        records = [line.removeprefix('GEOMETRY_RESULTS ') for line in process.stdout.splitlines()
                   if line.startswith('GEOMETRY_RESULTS ')]
        self.assertEqual(len(records), 1, process.stdout)
        return json.loads(records[0])

    def test_dense_and_off_origin_spheres_have_no_crossings(self):
        results = self.geometry_results(r'''
results = []
for x in (0, 5):
    for segments in (32, 128):
        for radius in (1, 0.3):
            bpy.ops.mesh.primitive_uv_sphere_add(segments=segments, ring_count=segments // 2,
                                               radius=radius, location=(x, 0, 0))
            obj = bpy.context.object
            obj['component'] = 'shell'
            bpy.context.view_layer.update()
            report = mesh_integrity(obj)
            results.append({'x': x, 'segments': segments, 'radius': radius,
                            'crossings': report['selfIntersectionCount'],
                            'degenerate': report['degenerateTriangles'],
                            'boundary': report['boundaryEdges'],
                            'nonManifold': report['nonManifoldEdges']})
            bpy.data.objects.remove(obj, do_unlink=True)
''')
        for row in results:
            with self.subTest(x=row['x'], segments=row['segments'], radius=row['radius']):
                self.assertEqual(row['crossings'], 0, row)
                self.assertEqual(row['degenerate'], 0, row)
                self.assertEqual(row['boundary'], 0, row)
                self.assertEqual(row['nonManifold'], 0, row)

    def test_shared_topology_does_not_hide_folded_or_crossing_interiors(self):
        results = self.geometry_results(r'''
results = {}
cases = {
    'point-contact': ([(0,0,0),(2,0,0),(0,2,0),(-1,0,1),(0,-1,1)],
                      [(0,1,2),(0,3,4)]),
    'point-crossing': ([(0,0,0),(2,0,0),(0,2,0),(1,1,-1),(1,1,1)],
                       [(0,1,2),(0,3,4)]),
    'coplanar-point-overlap': ([(0,0,0),(2,0,0),(0,2,0),(1,0.2,0),(0.2,1,0)],
                               [(0,1,2),(0,3,4)]),
    'coplanar-disconnected-overlap': ([(0,0,0),(2,0,0),(0,2,0),
                                      (0.2,0.2,0),(1.5,0.2,0),(0.2,1.5,0)],
                                     [(0,1,2),(3,4,5)]),
    'near-coplanar-edge-contact': ([(0,0,0),(2,0,0),(0,2,0),(1,1,0.0000001)],
                                  [(0,1,2),(1,0,3)]),
    'parallel-gap': ([(0,0,0),(2,0,0),(0,2,0),
                      (0,0,0.00001),(2,0,0.00001),(0,2,0.00001)],
                     [(0,1,2),(3,4,5)]),
    'folded-edge': ([(0,0,0),(2,0,0),(0,2,0),(1,1,0)], [(0,1,2),(1,0,3)]),
    'duplicate-face': ([(0,0,0),(2,0,0),(0,2,0)], [(0,1,2),(2,1,0)]),
    'small-crossing-large-envelope': ([(0,0,0),(2,0,0),(0,2,0),
                                       (1,1,-0.00001),(1,1,0.00001),
                                       (100,100,100),(101,100,100),(100,101,100)],
                                      [(0,1,2),(0,3,4),(5,6,7)]),
    'folded-edge-x-plane': ([(0,0,0),(0,2,0),(0,0,2),(0,1,1)], [(0,1,2),(1,0,3)]),
    'folded-edge-y-plane': ([(0,0,0),(2,0,0),(0,0,2),(1,0,1)], [(0,1,2),(1,0,3)]),
    'non-manifold': ([(0,0,0),(2,0,0),(0,2,0),(0,-2,0),(0,0,2)],
                     [(0,1,2),(1,0,3),(0,1,4)]),
}
for name, (points, faces) in cases.items():
    report = inspect(name, points, faces)
    results[name] = {'crossings': report['selfIntersectionCount'],
                     'nonManifold': report['nonManifoldEdges']}
''')
        self.assertEqual(results['point-contact']['crossings'], 0)
        self.assertEqual(results['near-coplanar-edge-contact']['crossings'], 0)
        self.assertEqual(results['parallel-gap']['crossings'], 0)
        for name in ('point-crossing', 'coplanar-point-overlap', 'coplanar-disconnected-overlap',
                     'folded-edge', 'duplicate-face', 'small-crossing-large-envelope',
                     'folded-edge-x-plane', 'folded-edge-y-plane'):
            with self.subTest(name=name):
                self.assertEqual(results[name]['crossings'], 1, results[name])
        self.assertEqual(results['non-manifold']['nonManifold'], 1)


    def test_tilted_coplanar_folds_and_disconnected_overlap_are_rejected(self):
        results = self.geometry_results(r'''
import math
from mathutils import Euler, Matrix
rotation = Euler(tuple(math.radians(value) for value in (31, 47, 13))).to_matrix()
cases = {
    'fold': ([(0,0,0),(2,0,0),(0,2,0),(1,1,0)], [(0,1,2),(1,0,3)], 1),
    'shared-vertex': ([(0,0,0),(2,0,0),(0,2,0),(1,0.2,0),(0.2,1,0)],
                       [(0,1,2),(0,3,4)], 1),
    'disconnected': ([(0,0,0),(2,0,0),(0,2,0),(0.2,0.2,0),(1.5,0.2,0),(0.2,1.5,0)],
                      [(0,1,2),(3,4,5)], 1),
    'adjacent': ([(0,0,0),(2,0,0),(0,2,0),(2,2,0)], [(0,1,2),(1,3,2)], 0),
    'crossing': ([(-1,-1,0),(1,-1,0),(0,1,0),(0,-0.5,-1),(0,-0.5,1),(0,0.5,0)],
                  [(0,1,2),(3,4,5)], 1),
    'parallel-gap': ([(0,0,0),(2,0,0),(0,2,0),(0,0,0.00001),(2,0,0.00001),(0,2,0.00001)],
                      [(0,1,2),(3,4,5)], 0),
}
results = []
for mode in ('object-transform', 'baked-float32'):
    for scale in ((1,1,1), (3,0.02,1.1)):
        transform = rotation @ Matrix.Diagonal(scale)
        for name, (points, faces, expected) in cases.items():
            translation = (3,1,2) if name == 'disconnected' else (3.1,1.7,0.9)
            if mode == 'baked-float32':
                points = [tuple(sum(float(transform[a][b]) * point[b] for b in range(3))
                                + translation[a] for a in range(3)) for point in points]
            mesh = bpy.data.meshes.new(name)
            mesh.from_pydata(points, [], faces)
            mesh.update()
            obj = bpy.data.objects.new(name, mesh)
            bpy.context.scene.collection.objects.link(obj)
            obj['component'] = 'shell'
            if mode == 'object-transform':
                obj.rotation_euler = tuple(math.radians(value) for value in (31,47,13))
                obj.scale = scale
                obj.location = translation
            bpy.context.view_layer.update()
            report = mesh_integrity(obj)
            results.append({'case': name, 'mode': mode, 'scale': scale,
                            'expected': expected, 'crossings': report['selfIntersectionCount']})
            bpy.data.objects.remove(obj, do_unlink=True)
# Independently authored tilted data, not a transformed axis-aligned fixture.
a, b, c = (0.3,0.1,0.6), (1.1,-0.4,0.3), (0.2,0.9,-0.1)
d = tuple((x+y)/2 * 0.75 + z * 0.25 for x,y,z in zip(a,b,c))
report = inspect('tilted-authored-fold', [a,b,c,d], [(0,1,2),(1,0,3)])
results.append({'case': 'tilted-authored-fold', 'crossings': report['selfIntersectionCount'], 'expected': 1})
''')
        for row in results:
            with self.subTest(row=row):
                self.assertEqual(row['crossings'], row['expected'], row)


    def test_large_planar_grid_and_radial_fan_remain_valid(self):
        results = self.geometry_results(r'''
import math
results = []
count = 4096
points = [(0,0,0)] + [(math.cos(2*math.pi*i/count), math.sin(2*math.pi*i/count), 0)
                      for i in range(count)]
report = inspect('radial-fan', points, [(0, i+1, (i+1)%count+1) for i in range(count)])
results.append({'expectedTriangles': count, 'expectedBoundary': count, 'report': report})
bpy.ops.mesh.primitive_grid_add(x_subdivisions=200, y_subdivisions=200, size=6)
obj = bpy.context.object
obj['component'] = 'shell'
bpy.context.view_layer.update()
results.append({'expectedTriangles': 80000, 'expectedBoundary': 800, 'report': mesh_integrity(obj)})
''')
        for row in results:
            with self.subTest(name=row['report']['name']):
                report = row['report']
                self.assertEqual(report['triangles'], row['expectedTriangles'])
                self.assertEqual(report['boundaryEdges'], row['expectedBoundary'])
                self.assertEqual(report['selfIntersectionCount'], 0)
                self.assertEqual(report['degenerateTriangles'], 0)
                self.assertEqual(report['nonManifoldEdges'], 0)

    def test_dense_thin_rods_bound_broadphase_work_and_keep_real_crossings(self):
        results = self.geometry_results(r'''
import math
import mathutils.bvhtree as native_bvh
actual_bvh = native_bvh.BVHTree
overlap_results = 0
# Profile actual native output, without replacing geometry or filtering results.
class MeasuredTree:
    def __init__(self, tree):
        self.tree = tree
    def overlap(self, other):
        global overlap_results
        pairs = self.tree.overlap(other.tree)
        overlap_results += len(pairs)
        return pairs
class MeasuredBVH:
    @staticmethod
    def FromPolygons(*args, **kwargs):
        return MeasuredTree(actual_bvh.FromPolygons(*args, **kwargs))
native_bvh.BVHTree = MeasuredBVH
count = 1024
bpy.ops.mesh.primitive_cylinder_add(vertices=count, radius=0.02, depth=2,
    end_fill_type='NOTHING', location=(3,1,1),
    rotation=tuple(math.radians(v) for v in (31,47,13)))
obj = bpy.context.object
obj['component'] = 'shell'
bpy.context.view_layer.update()
report = mesh_integrity(obj)
results = [{'pierced': False, 'report': report, 'overlapResults': overlap_results}]
points = [tuple(vertex.co) for vertex in obj.data.vertices]
faces = [tuple(face.vertices) for face in obj.data.polygons]
start = len(points)
points.extend(((0,0.003,-0.5), (0.03,0.003,0), (0,0.003,0.5)))
faces.append((start, start+1, start+2))
overlap_results = 0
report = inspect('pierced-thin-rod', points, faces)
results.append({'pierced': True, 'report': report, 'overlapResults': overlap_results})
native_bvh.BVHTree = actual_bvh
''')
        for row in results:
            with self.subTest(pierced=row['pierced']):
                report = row['report']
                self.assertEqual(report['degenerateTriangles'], 0)
                self.assertEqual(report['nonManifoldEdges'], 0)
                self.assertEqual(report['triangles'], 2048 + int(row['pierced']))
                if row['pierced']:
                    self.assertEqual(report['selfIntersectionCount'], 2)
                else:
                    self.assertEqual(report['selfIntersectionCount'], 0)
                    self.assertEqual(report['boundaryEdges'], 2048)
                # Bound materialized broadphase work, not machine-dependent time.
                self.assertLessEqual(row['overlapResults'], 32 * report['triangles'], row)

    def test_near_singular_transform_preserves_resolvable_source_gaps(self):
        results = self.geometry_results(r'''
results = []
for scale, expected in ((1e-9, 0), (1e-12, 0), (0, 1)):
    mesh = bpy.data.meshes.new('near-singular-gap')
    mesh.from_pydata([(0,0,0),(2,0,0),(0,2,0),(0,0,1),(2,0,1),(0,2,1)],
                     [], [(0,1,2),(3,4,5)])
    mesh.update()
    obj = bpy.data.objects.new('near-singular-gap', mesh)
    bpy.context.scene.collection.objects.link(obj)
    obj['component'] = 'shell'
    obj.scale.z = scale
    bpy.context.view_layer.update()
    results.append({'scale': scale, 'expected': expected, 'report': mesh_integrity(obj)})
''')
        for row in results:
            with self.subTest(scale=row['scale']):
                self.assertEqual(row['report']['selfIntersectionCount'], row['expected'])
                self.assertEqual(row['report']['degenerateTriangles'], 0)

    def test_singular_transform_uses_actual_world_geometry(self):
        results = self.geometry_results(r'''
results = {}
for name, points, faces in (
    ('collapsed-overlap', [(0,0,0),(2,0,0),(0,2,0),(0,0,1),(2,0,1),(0,2,1)],
                          [(0,1,2),(3,4,5)]),
    ('flat-floor', [(0,0,0),(2,0,0),(0,2,0)], [(0,1,2)]),
):
    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata(points, [], faces)
    mesh.update()
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.scene.collection.objects.link(obj)
    obj['component'] = 'shell'
    obj.scale.z = 0
    bpy.context.view_layer.update()
    results[name] = mesh_integrity(obj)
''')
        self.assertEqual(results['collapsed-overlap']['selfIntersectionCount'], 1)
        self.assertEqual(results['flat-floor']['selfIntersectionCount'], 0)
        self.assertEqual(results['flat-floor']['degenerateTriangles'], 0)


if __name__ == '__main__':
    unittest.main()
