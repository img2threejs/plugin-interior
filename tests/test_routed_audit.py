# SPDX-License-Identifier: Apache-2.0
"""Metadata negatives and opt-in real program-audit/Blender replay, never terminal QA."""
import copy
import hashlib
import json
import os
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from authoring.routed_audit import MISSING_TERMINAL_PRODUCERS, canonical_sha, validate_artifact
from authoring.routing import normalized_glb


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def box_glb(path):
    # An actual eight-vertex cuboid in glTF Y-up, not image/quality evidence.
    native = [(x, y, z) for z in (-0.1, 2.9) for y in (4, 10) for x in (2, 6)]
    points = [(x, z, -y) for x, y, z in native]
    faces = (0, 2, 3, 0, 3, 1, 4, 5, 7, 4, 7, 6,
             0, 1, 5, 0, 5, 4, 2, 6, 7, 2, 7, 3,
             0, 4, 6, 0, 6, 2, 1, 3, 7, 1, 7, 5)
    positions = struct.pack('<24f', *(v for point in points for v in point))
    indices = struct.pack('<36H', *faces)
    binary = positions + indices
    doc = {'asset': {'version': '2.0'}, 'scene': 0, 'scenes': [{'nodes': [0, 1]}],
           'nodes': [{'mesh': 0, 'extras': {'component': 'shell', 'physical_id': 'shell'}},
                     {'camera': 0, 'translation': [8, 8, 10]}],
           'cameras': [{'type': 'perspective', 'perspective': {'yfov': 0.8, 'znear': 0.1}}],
           'buffers': [{'byteLength': len(binary)}],
           'bufferViews': [{'buffer': 0, 'byteLength': len(positions)},
                           {'buffer': 0, 'byteOffset': len(positions), 'byteLength': len(indices)}],
           'accessors': [{'bufferView': 0, 'componentType': 5126, 'count': 8, 'type': 'VEC3',
                          'min': [min(p[a] for p in points) for a in range(3)],
                          'max': [max(p[a] for p in points) for a in range(3)]},
                         {'bufferView': 1, 'componentType': 5123, 'count': 36, 'type': 'SCALAR'}],
           'meshes': [{'primitives': [{'attributes': {'POSITION': 0}, 'indices': 1}]}]}
    encoded = json.dumps(doc).encode()
    encoded += b' ' * (-len(encoded) % 4)
    chunks = struct.pack('<I4s', len(encoded), b'JSON') + encoded + struct.pack('<I4s', len(binary), b'BIN\x00') + binary
    path.write_bytes(struct.pack('<4sII', b'glTF', 2, 12 + len(chunks)) + chunks)


class RoutedAuditContractTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.workspace = Path(temporary.name).resolve()
        self.spec = {'routing': {'objectId': 'room'}, 'source': 'reference.png',
                     'excludeComponents': ['prop'],
                     'authoring': {'sceneBuilder': 'builder.py', 'roomMeasurementComponents': ['shell']}}
        write_json(self.workspace / 'scene-spec.json', self.spec)
        write_json(self.workspace / '.img2/artifacts/interior/intake.json',
                   {'components': ['shell', 'prop'], 'excludeComponents': ['prop']})
        write_json(self.workspace / 'state.json',
                   {'profile': 'interior', 'routeChild': {'objectId': 'room', 'ownerRoute': 'plugin:interior@fixture',
                                                         'outputKind': '3d-scene', 'excludedObjectIds': ['prop']}})
        (self.workspace / 'reference.png').write_bytes(b'Explicit hash-only source fixture; not visual evidence')
        (self.workspace / 'builder.py').write_text('# Provenance: geometry authored by this test fixture.\n')
        self.source = self.workspace / 'runtime.glb'
        box_glb(self.source)
        self.model = self.workspace / 'room-composition.glb'
        self.model.write_bytes(normalized_glb(self.source, [4, 7, -0.1]))
        self.dimensions = {'width': 4, 'height': 3, 'depth': 6}
        self.receipt = {'schemaVersion': 1, 'basis': 'exported-glb-world-vertices',
                        'roomDimensionBasis': 'explicit-authored-shell-envelope',
                        'compositionSha256': sha(self.model), 'roomDimensions': self.dimensions,
                        'roomMeasurementComponents': ['shell'], 'excludeComponents': ['prop'],
                        'sourceRuntime': {'path': 'runtime.glb', 'sha256': sha(self.source)}}
        self.receipt_path = self.workspace / 'scene-measurements.json'
        write_json(self.receipt_path, self.receipt)
        self.artifact = {'objectId': 'room', 'outputKind': '3d-scene',
                         'frame': {'units': 'm', 'up': '+Y', 'forward': '+Z'},
                         'pivot': 'bottom-centre', 'scaleEstimate': 1, 'rig': None,
                         'roomDimensions': self.dimensions, 'excludeComponents': ['prop'],
                         'ownerRoute': 'plugin:interior@fixture', 'ownedObjectIds': ['room'], 'files': []}
        self.save_artifact()

    def save_artifact(self):
        self.artifact['files'] = [{'path': path.name, 'sha256': sha(path)} for path in (self.model, self.receipt_path)]
        write_json(self.workspace / 'artifact.json', self.artifact)

    def test_metadata_scope_binds_actual_artifact_and_inputs_but_does_not_accept_geometry(self):
        result = validate_artifact(self.workspace)
        self.assertEqual(result['artifact'], {'path': 'artifact.json', 'sha256': sha(self.workspace / 'artifact.json')})
        self.assertEqual(result['roomDimensions'], self.dimensions)
        self.assertEqual(result['excludeComponents'], ['prop'])
        self.assertEqual(len(result['cameraSha256']), 64)
        self.assertNotIn('status', result)
        self.assertNotIn('terminalAcceptance', result)
        self.assertNotIn('geometryPassed', result)

    def test_wrong_frame_dimensions_hash_and_ownership_are_rejected(self):
        original = copy.deepcopy(self.artifact)
        for mutation in ({'frame': {'units': 'cm', 'up': '+Z', 'forward': '+Y'}},
                         {'pivot': 'centre'}, {'scaleEstimate': True}, {'scaleEstimate': 2}, {'rig': {}},
                         {'roomDimensions': {'width': 4, 'height': 0, 'depth': 6}}, {'objectId': 'other'}):
            with self.subTest(mutation=mutation):
                self.artifact = {**copy.deepcopy(original), **mutation}
                self.save_artifact()
                with self.assertRaises(ValueError):
                    validate_artifact(self.workspace)
        self.artifact = original
        self.save_artifact()
        self.model.write_bytes(self.model.read_bytes() + b'changed')
        with self.assertRaisesRegex(ValueError, 'Stale'):
            validate_artifact(self.workspace)

    def test_exclusion_provenance_and_source_runtime_must_stay_current(self):
        self.receipt['excludeComponents'] = []
        write_json(self.receipt_path, self.receipt)
        self.save_artifact()
        with self.assertRaisesRegex(ValueError, 'exclusion provenance'):
            validate_artifact(self.workspace)
        self.receipt['excludeComponents'] = ['prop']
        write_json(self.receipt_path, self.receipt)
        self.save_artifact()
        self.source.write_bytes(self.source.read_bytes() + b'changed')
        with self.assertRaisesRegex(ValueError, 'Stale'):
            validate_artifact(self.workspace)

    def test_artifact_binding_cannot_follow_symlinks(self):
        target = self.workspace / 'copy.glb'
        target.write_bytes(self.model.read_bytes())
        self.model.unlink()
        self.model.symlink_to(target.name)
        with self.assertRaisesRegex(ValueError, 'Symlink'):
            validate_artifact(self.workspace)

    def test_editable_success_claims_cannot_replace_program_producers(self):
        write_json(self.workspace / 'terminal-evidence.json',
                   {'passed': True, 'geometry': 'pass', 'visualReview': 'pass', 'browser': 'pass'})
        command = [sys.executable, str(ROOT / 'tools/audit_routed_artifact.py'),
                   '--workspace', str(self.workspace)]
        env = {**os.environ, 'PYTHONDONTWRITEBYTECODE': '1'}
        env.pop('IMG2_BLENDER_PYTHON', None)
        result = subprocess.run(command, capture_output=True, text=True, env=env, timeout=30)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(report['status'], 'fail')
        self.assertFalse(report['terminalAcceptance'])
        self.assertEqual(report['evidence']['artifact']['sha256'], sha(self.workspace / 'artifact.json'))
        self.assertEqual(report['missingTerminalProducers'], list(MISSING_TERMINAL_PRODUCERS))
        self.assertNotEqual(report['kind'], 'img2.gate-verdict')

    def test_structural_gate_cannot_silently_fall_back_to_mechanical_replay(self):
        result = subprocess.run([sys.executable, str(ROOT / 'tools/audit_routed_artifact.py'),
                                 '--workspace', str(self.workspace), '--gate'],
                                capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(report['kind'], 'img2.gate-verdict')
        self.assertEqual(report['gate'], 'interior-routed-structure')
        self.assertEqual(report['status'], 'fail')
        self.assertIn('--quality-output', report['reasons'][0])
        self.assertFalse(report['terminalAcceptance'])

    def test_audit_is_not_registered_as_a_terminal_or_legacy_gate(self):
        gates = json.loads((ROOT / 'gates.json').read_text())
        self.assertEqual({gate['id'] for gate in gates},
                         {'interior-materials', 'interior-delivery', 'interior-workflow'})


@unittest.skipUnless(os.environ.get('IMG2_BLENDER_PYTHON'), 'Set IMG2_BLENDER_PYTHON for real Blender replay')
class NativeRoutedAuditTests(unittest.TestCase):
    def test_real_material_delivery_and_independent_reimport_can_pass_only_mechanical_scope(self):
        harness = Path(os.environ.get('IMG2_HARNESS_SOURCE', str(Path.home() / '.img2/harness'))).expanduser().resolve()
        if not (harness / 'img2_core/gate_runner.py').is_file():
            self.skipTest('Real img2 harness is required; set IMG2_HARNESS_SOURCE')
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            workspace = root / 'child'
            workspace.mkdir()
            home = root / 'home'
            home.mkdir()
            (home / 'harness').symlink_to(harness, target_is_directory=True)
            env = {**os.environ, 'IMG2_HOME': str(home), 'PYTHONDONTWRITEBYTECODE': '1'}
            script = r'''
import json, sys, hashlib
from pathlib import Path
import bpy
sys.path.insert(0, sys.argv[1])
from authoring import Context
from authoring.routing import produce_artifact
root = Path(sys.argv[2]).resolve()
bpy.ops.wm.read_factory_settings(use_empty=True)
image = bpy.data.images.new('SyntheticReferenceFixture', width=2, height=2)
image.filepath_raw = str(root/'reference.png')
image.file_format = 'PNG'
image.save()
bpy.ops.mesh.primitive_cube_add(size=1, location=(4,7,1.5))
shell = bpy.context.object
shell.dimensions = (4,6,3)
shell['component'], shell['physical_id'] = 'shell', 'shell'
material = bpy.data.materials.new('RoomMaterial')
material.use_nodes = True
shader = material.node_tree.nodes.get('Principled BSDF')
shader.inputs['Roughness'].default_value = 0.9
shader.inputs['Metallic'].default_value = 0
shell.data.materials.append(material)
data = bpy.data.cameras.new('SourceCamera')
camera = bpy.data.objects.new('SourceCamera', data)
bpy.context.scene.collection.objects.link(camera)
camera.location = (8,-10,8)
bpy.context.scene.camera = camera
spec = {'routing': {'objectId':'room'}, 'source':'reference.png', 'excludeComponents':[],
        'authoring': {'sceneBuilder':'setup_native.py', 'roomMeasurementComponents':['shell']}}
(root/'scene-spec.json').write_text(json.dumps(spec))
intake = root/'.img2/artifacts/interior/intake.json'
intake.parent.mkdir(parents=True)
intake.write_text(json.dumps({'components':['shell'], 'excludeComponents':[]}))
(root/'state.json').write_text(json.dumps({'profile':'interior',
    'routeChild':{'objectId':'room', 'ownerRoute':'plugin:interior@fixture', 'outputKind':'3d-scene', 'excludedObjectIds':[]}}))
runtime = root/'runtime.glb'
bpy.ops.export_scene.gltf(filepath=str(runtime), export_format='GLB', export_yup=True,
                          export_extras=True, export_cameras=True)
shell.name = 'DistinctPortableObject'
bpy.ops.export_scene.gltf(filepath=str(root/'portable.glb'), export_format='GLB', export_yup=True,
                          export_extras=True, export_cameras=True)
bpy.ops.wm.save_as_mainfile(filepath=str(root/'master.blend'))
context = Context(root, spec, root/'assets', root/'evidence')
produce_artifact(context, runtime)
def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()
policy = {'schemaVersion':1, 'materials':[{'name':'RoomMaterial', 'minRoughnessFactor':0.8,
                                         'maxMetallicFactor':0.1}]}
(root/'policy.json').write_text(json.dumps(policy))
assets = [{'id':role,'role':role,'path':name,'sha256':sha(root/name)}
          for role,name in [('source','reference.png'),('runtime','runtime.glb'),
                            ('portable','portable.glb'),('native','master.blend')]]
contract = {'schemaVersion':1, 'root':str(root), 'assets':assets,
            'runtime':{'asset':'runtime','sidecars':[]},
            'portable':{'asset':'portable','limitations':['Static synthetic cuboid fixture only.']},
            'native':{'asset':'native'}, 'limitations':['No reconstruction or visual QA claim.']}
(root/'delivery.json').write_text(json.dumps(contract))
'''
            setup = workspace / 'setup_native.py'
            setup.write_text(script)
            result = subprocess.run([os.environ['IMG2_BLENDER_PYTHON'], str(setup), str(ROOT), str(workspace)],
                                    capture_output=True, text=True, env=env, timeout=120)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            for script_name, arguments in (
                    ('audit_materials.py', ['--glb', str(workspace / 'runtime.glb'), '--policy', str(workspace / 'policy.json')]),
                    ('audit_delivery.py', ['--contract', str(workspace / 'delivery.json')])):
                result = subprocess.run([sys.executable, str(ROOT/'tools'/script_name), '--workspace', str(workspace), *arguments],
                                        capture_output=True, text=True, env=env, timeout=60)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            result = subprocess.run([sys.executable, str(ROOT/'tools/audit_routed_artifact.py'),
                                     '--workspace', str(workspace), '--blender-python', os.environ['IMG2_BLENDER_PYTHON']],
                                    capture_output=True, text=True, env=env, timeout=300)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            report = json.loads(result.stdout)
            self.assertEqual(report['status'], 'pass')
            self.assertFalse(report['terminalAcceptance'])
            self.assertEqual(report['evidence']['artifact'], {'path':'artifact.json','sha256':sha(workspace/'artifact.json')})
            self.assertEqual(report['evidence']['measurements']['artifact'], report['evidence']['artifact'])
            self.assertTrue(report['missingTerminalProducers'])
            # A real independent structural gate produces pixels, not an editable
            # success claim. This synthetic cube is NOT reference-fidelity proof.
            result = subprocess.run([sys.executable, str(ROOT/'tools/audit_routed_artifact.py'),
                                     '--workspace', str(workspace), '--blender-python', os.environ['IMG2_BLENDER_PYTHON'],
                                     '--quality-output', 'quality-r1', '--gate'],
                                    capture_output=True, text=True, env=env, timeout=300)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            gate = json.loads(result.stdout)
            self.assertEqual(gate['gate'], 'interior-routed-structure')
            self.assertFalse(gate['terminalAcceptance'])
            structure = gate['evidence']['evidence']['measurements']['structure']
            self.assertEqual(structure['measurements']['selfIntersectionCount'], 0)
            self.assertEqual([row['angle'] for row in structure['captures']], [0, 90, 180, 270])
            self.assertEqual(structure['partCoverageStatus'], 'unevaluated')
            self.assertEqual(structure['semanticReviewStatus'], 'unevaluated')
            self.assertNotIn('partCoveragePassed', structure['measurements'])
            self.assertEqual(structure['evidence']['artifactSha256'], sha(workspace / 'artifact.json'))
            self.assertEqual(structure['evidence']['referenceSha256'], sha(workspace / 'reference.png'))
            self.assertEqual(structure['evidence']['sourceRuntimeSha256'], sha(workspace / 'runtime.glb'))
            self.assertEqual(structure['evidence']['specSha256'], sha(workspace / 'scene-spec.json'))
            self.assertEqual(structure['evidence']['viewSetSha256'],
                             canonical_sha({'captures': structure['captures'], 'setup': structure['captureSetup']}))
            for row in structure['captures']:
                self.assertGreater(row['foregroundPixels'], 0)
                self.assertEqual(row['sha256'], sha(workspace / row['path']))
            result = subprocess.run([sys.executable, str(ROOT/'tools/audit_routed_artifact.py'),
                                     '--workspace', str(workspace), '--blender-python', os.environ['IMG2_BLENDER_PYTHON'],
                                     '--quality-output', 'quality-r1', '--gate'],
                                    capture_output=True, text=True, env=env, timeout=300)
            self.assertEqual(result.returncode, 1)
            self.assertIn('immutable', json.loads(result.stdout)['reasons'][0])
            runtime = workspace / 'runtime.glb'
            runtime.write_bytes(runtime.read_bytes() + b'changed after audited export')
            result = subprocess.run([sys.executable, str(ROOT/'tools/audit_routed_artifact.py'),
                                     '--workspace', str(workspace), '--blender-python', os.environ['IMG2_BLENDER_PYTHON']],
                                    capture_output=True, text=True, env=env, timeout=60)
            self.assertEqual(result.returncode, 1)
            self.assertFalse(json.loads(result.stdout)['terminalAcceptance'])


if __name__ == '__main__':
    unittest.main()
