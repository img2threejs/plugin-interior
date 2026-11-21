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


if __name__ == '__main__':
    unittest.main()
