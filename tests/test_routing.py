# SPDX-License-Identifier: Apache-2.0
"""Offline contracts plus opt-in real Blender handoff; no visual-quality assertions."""
import copy
import hashlib
import json
import os
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from authoring import Context, bound_module, context_from_args
from authoring.decisions import candidates_for, decision_context
from authoring.routing import apply_exclusions, exclusions, normalized_glb, produce_artifact, room_dimensions
from authoring.routed_audit import validate_artifact


def glb_bytes(document):
    document = json.dumps(document).encode()
    document += b' ' * (-len(document) % 4)
    binary = struct.pack('<I4s', 4, b'BIN\x00') + b'\x00' * 4
    return (struct.pack('<4sII', b'glTF', 2, 20 + len(document) + len(binary))
            + struct.pack('<I4s', len(document), b'JSON') + document + binary)


class Object(dict):
    def __init__(self, name, component):
        super().__init__(component=component)
        self.name = name


class RoutingTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.workspace = Path(temp.name).resolve()
        self.spec = {'source': 'reference.png', 'routing': {'objectId': 'room'},
                     'excludeComponents': ['character'],
                     'authoring': {'sceneBuilder': 'build_scene.py', 'roomMeasurementComponents': ['shell']}}
        intake = self.workspace / '.img2/artifacts/interior/intake.json'
        intake.parent.mkdir(parents=True)
        intake.write_text(json.dumps({'components': ['shell', 'character'], 'excludeComponents': ['character']}))
        (self.workspace / 'state.json').write_text(json.dumps({
            'profile': 'interior',
            'routeChild': {'objectId': 'room', 'ownerRoute': 'plugin:interior@fixture', 'outputKind': '3d-scene', 'excludedObjectIds': ['character']}}))
        self.context = Context(self.workspace, self.spec, self.workspace / 'assets', self.workspace / 'evidence')
        self.document = {'asset': {'version': '2.0'}, 'scene': 0, 'scenes': [{'nodes': [0]}],
                         'nodes': [{'name': 'shell', 'mesh': 0, 'extras': {'component': 'shell'}}],
                         'buffers': [{'byteLength': 4}]}
        self.runtime = self.workspace / 'runtime.glb'
        self.runtime.write_bytes(glb_bytes(self.document))
        self.rows = [{'name': 'shell', 'component': 'shell', 'bounds': [[2, 4, -0.1], [6, 10, 2.9]]}]

    def measure_fixture(self, path):
        # Synthetic measurement boundary only. Real GLB import/export is exercised below.
        if path == self.runtime:
            return copy.deepcopy(self.rows)
        return [{'name': 'shell', 'component': 'shell', 'bounds': [[-2, -3, 0], [2, 3, 3]]}]

    def test_exclusion_removes_actual_owned_objects_not_other_components(self):
        objects = [Object('person.body', 'character'), Object('person.hidden-shoe', 'character'),
                   Object('shell.wall', 'shell'), Object('character-portrait', 'decor')]
        removed = apply_exclusions(objects, exclusions(['character']), objects.remove)
        self.assertEqual([obj.name for obj in objects], ['shell.wall', 'character-portrait'])
        self.assertEqual(len(removed), 2)
        self.assertEqual({item['component'] for item in removed}, {'character'})
        for value in (None, 'character', ['character', 'character'], [''], [' character']):
            with self.subTest(value=value), self.assertRaises(ValueError):
                exclusions(value)

    def test_intake_exclusions_bind_authoring_and_mismatch_blocks(self):
        intake = self.workspace / '.img2/artifacts/interior/intake.json'
        intake.parent.mkdir(parents=True, exist_ok=True)
        intake.write_text(json.dumps({'components': ['shell', 'character'], 'excludeComponents': ['character']}))
        self.assertEqual(self.context.exclude_components, ('character',))
        first = self.context.ownership_binding
        self.assertEqual(first['intakeSha256'], hashlib.sha256(intake.read_bytes()).hexdigest())
        self.spec['excludeComponents'] = []
        with self.assertRaisesRegex(ValueError, 'disagrees'):
            _ = self.context.exclude_components

    def prepare_routed_handoff(self, excluded=None):
        excluded = ['actor-17', 'prop-9'] if excluded is None else excluded
        self.prepare_evidence()
        self.spec['excludeComponents'] = list(excluded)
        self.spec['authoring']['sourceImageSize'] = [4, 3]
        state = {'profile': 'interior',
                 'routeChild': {'objectId': 'room', 'ownerRoute': 'plugin:interior@fixture', 'outputKind': '3d-scene', 'excludedObjectIds': list(excluded)}}
        intake = {'components': ['shell', *excluded], 'excludeComponents': list(excluded)}
        documents = {'state': state, 'intake': intake, 'spec': self.spec}
        self.write_handoff(documents)
        return documents

    def write_handoff(self, documents):
        for name, relative in (('state', 'state.json'), ('spec', 'scene-spec.json'),
                               ('intake', '.img2/artifacts/interior/intake.json')):
            (self.workspace / relative).write_text(json.dumps(documents[name]))

    def prepare_auditable_routed_handoff(self, excluded=None):
        documents = self.prepare_routed_handoff(excluded)
        self.document['cameras'] = [{'type': 'perspective', 'perspective': {'yfov': 0.8, 'znear': 0.1}}]
        self.document['nodes'].append({'camera': 0, 'translation': [8, 8, 10]})
        self.document['scenes'][0]['nodes'].append(1)
        self.runtime.write_bytes(glb_bytes(self.document))
        produce_artifact(self.context, self.runtime, self.measure_fixture)
        return documents

    def test_routed_handoff_uses_exact_object_ids_and_pins_local_state(self):
        self.prepare_auditable_routed_handoff()
        self.assertEqual(self.context.exclude_components, ('actor-17', 'prop-9'))
        artifact = json.loads((self.workspace / 'artifact.json').read_text())
        self.assertEqual(artifact['excludeComponents'], ['actor-17', 'prop-9'])
        self.assertEqual(artifact['ownerRoute'], 'plugin:interior@fixture')
        self.assertEqual(artifact['ownedObjectIds'], ['room'])
        objects = [Object('Human label', 'actor-17'), Object('Chair label', 'prop-9'),
                   Object('actor-17', 'decor'), Object('room', 'shell')]
        apply_exclusions(objects, self.context.exclude_components, objects.remove)
        self.assertEqual([obj.name for obj in objects], ['actor-17', 'room'])
        result = validate_artifact(self.workspace)
        self.assertEqual(result['excludeComponents'], ['actor-17', 'prop-9'])
        self.assertIn({'path': 'state.json',
                       'sha256': hashlib.sha256((self.workspace / 'state.json').read_bytes()).hexdigest()},
                      result['inputs'])
        self.assertNotIn('terminalAcceptance', result)
        self.assertNotIn('accepted', result)

    def test_routed_export_refuses_missing_owner_before_measurement(self):
        state_path = self.workspace / 'state.json'
        state = json.loads(state_path.read_text())
        for value in (None, '', 42):
            with self.subTest(owner=value):
                state['routeChild']['ownerRoute'] = value
                state_path.write_text(json.dumps(state))
                with patch.object(self, 'measure_fixture', side_effect=AssertionError('measured')):
                    with self.assertRaisesRegex(ValueError, 'ownerRoute'):
                        produce_artifact(self.context, self.runtime, self.measure_fixture)

    def test_routed_handoff_rejects_missing_malformed_or_altered_lists_in_authoring_and_audit(self):
        original = self.prepare_auditable_routed_handoff()
        missing = object()
        cases = []
        for target, key in (('child', 'excludedObjectIds'), ('intake', 'excludeComponents'),
                            ('spec', 'excludeComponents')):
            for value in (missing, None, 'actor-17', [], ['actor-17'], ['different-id'],
                          ['actor-17', 'actor-17'], [' actor-17', 'prop-9'],
                          ['prop-9', 'actor-17'], [False], {}):
                cases.append((target, key, value))
        for value in (missing, None, 'actor-17', ['shell', 'Human label', 'Chair label'],
                      ['shell', 'actor-17', 'actor-17'], [False]):
            cases.append(('intake', 'components', value))
        for value in (missing, 'other', None):
            cases.append(('state', 'profile', value))
        for value in (missing, 'rigged-character', None):
            cases.append(('child', 'outputKind', value))
        for value in (None, [], 'interior'):
            cases.append(('state', 'routeChild', value))
        for value in (missing, None, 'other-room'):
            cases.append(('child', 'objectId', value))
        for value in (None, {}, [], 'room', {'objectId': None}, {'objectId': 'other-room'},
                      {'objectId': ''}, {'objectId': False}, {'objectId': 'room/other'}):
            cases.append(('spec', 'routing', value))
        for target, key, value in cases:
            with self.subTest(target=target, key=key, value=value):
                documents = copy.deepcopy(original)
                document = documents['state']['routeChild'] if target == 'child' else documents[target]
                if value is missing:
                    document.pop(key)
                else:
                    document[key] = value
                self.write_handoff(documents)
                self.context.spec = documents['spec']
                with self.assertRaises(ValueError):
                    _ = self.context.exclude_components
                with self.assertRaises(ValueError):
                    validate_artifact(self.workspace)

    def test_matching_intake_and_spec_cannot_override_routed_exclusions(self):
        original = self.prepare_auditable_routed_handoff()
        for excluded in ([], ['actor-17'], ['actor-label', 'prop-label']):
            with self.subTest(excluded=excluded):
                documents = copy.deepcopy(original)
                documents['intake']['excludeComponents'] = excluded
                documents['intake']['components'] = ['shell', *excluded]
                documents['spec']['excludeComponents'] = excluded
                self.write_handoff(documents)
                self.context.spec = documents['spec']
                with self.assertRaisesRegex(ValueError, 'excludedObjectIds'):
                    _ = self.context.exclude_components
                with self.assertRaisesRegex(ValueError, 'excludedObjectIds'):
                    validate_artifact(self.workspace)

    def test_routed_spec_requires_state_and_route_child_before_native_loading_and_audit(self):
        self.prepare_auditable_routed_handoff()
        args = SimpleNamespace(workspace=str(self.workspace), spec='scene-spec.json', run_id='draft')
        state_path = self.workspace / 'state.json'
        for state in (None, {'profile': 'interior'}):
            with self.subTest(state=state):
                if state is None:
                    state_path.unlink()
                else:
                    state_path.write_text(json.dumps(state))
                with self.assertRaisesRegex(ValueError, 'requires state.json with routeChild'):
                    _ = self.context.exclude_components
                with patch('authoring.private_module') as load_native:
                    with self.assertRaisesRegex(ValueError, 'requires state.json with routeChild'):
                        bound_module('pipeline', self.context)
                    load_native.assert_not_called()
                with self.assertRaisesRegex(ValueError, 'requires state.json with routeChild'):
                    context_from_args(args)
                with self.assertRaisesRegex(ValueError, 'requires state.json with routeChild'):
                    validate_artifact(self.workspace)
        self.assertFalse((self.workspace / 'public/assets/interior').exists())
        self.assertFalse((self.workspace / '.img2/artifacts/interior/production').exists())

    def test_route_child_requires_spec_routing_even_if_marker_is_removed(self):
        self.prepare_auditable_routed_handoff()
        self.spec.pop('routing')
        (self.workspace / 'scene-spec.json').write_text(json.dumps(self.spec))
        with self.assertRaisesRegex(ValueError, 'routing.objectId'):
            _ = self.context.exclude_components
        with patch('authoring.private_module') as load_native:
            with self.assertRaisesRegex(ValueError, 'routing.objectId'):
                bound_module('pipeline', self.context)
            load_native.assert_not_called()
        with self.assertRaises(ValueError):
            validate_artifact(self.workspace)

    def test_routed_child_requires_admitted_snapshot_before_native_loading(self):
        self.prepare_routed_handoff()
        (self.workspace / '.img2/artifacts/interior/intake.json').unlink()
        with patch('authoring.private_module') as load_native:
            with self.assertRaisesRegex(ValueError, 'admitted intake'):
                bound_module('pipeline', self.context)
            load_native.assert_not_called()
        args = SimpleNamespace(workspace=str(self.workspace), spec='scene-spec.json', run_id='draft')
        with self.assertRaisesRegex(ValueError, 'admitted intake'):
            context_from_args(args)
        self.assertFalse((self.workspace / 'public/assets/interior').exists())
        self.assertFalse((self.workspace / '.img2/artifacts/interior/production').exists())

    def test_routed_artifact_exclusions_must_match_canonical_handoff(self):
        self.prepare_auditable_routed_handoff()
        artifact_path = self.workspace / 'artifact.json'
        original = json.loads(artifact_path.read_text())
        missing = object()
        for excluded in (missing, None, [], 'actor-17', ['actor-17'], ['different-id'],
                         ['actor-17', 'actor-17'], ['prop-9', 'actor-17'], [False], {}):
            with self.subTest(excluded=excluded):
                artifact = copy.deepcopy(original)
                if excluded is missing:
                    artifact.pop('excludeComponents')
                else:
                    artifact['excludeComponents'] = excluded
                artifact_path.write_text(json.dumps(artifact))
                with self.assertRaises(ValueError):
                    validate_artifact(self.workspace)
        for field, value in (('ownerRoute', None), ('ownerRoute', 'other'),
                             ('ownedObjectIds', []), ('ownedObjectIds', ['other'])):
            with self.subTest(field=field, value=value):
                artifact_path.write_text(json.dumps({**original, field: value}))
                with self.assertRaisesRegex(ValueError, 'ownership'):
                    validate_artifact(self.workspace)

    def test_routed_explicit_empty_lists_are_valid_but_cannot_be_omitted(self):
        documents = self.prepare_auditable_routed_handoff([])
        self.assertEqual(self.context.exclude_components, ())
        self.assertEqual(validate_artifact(self.workspace)['excludeComponents'], [])
        artifact_path = self.workspace / 'artifact.json'
        original = artifact_path.read_text()
        artifact = json.loads(original)
        self.assertEqual(artifact.pop('excludeComponents'), [])
        artifact_path.write_text(json.dumps(artifact))
        with self.assertRaises(ValueError):
            validate_artifact(self.workspace)
        artifact_path.write_text(original)
        for target in ('intake', 'spec'):
            with self.subTest(target=target):
                changed = copy.deepcopy(documents)
                changed[target].pop('excludeComponents')
                self.write_handoff(changed)
                self.context.spec = changed['spec']
                with self.assertRaises(ValueError):
                    _ = self.context.exclude_components
                with self.assertRaises(ValueError):
                    validate_artifact(self.workspace)

    def test_standalone_optional_exclusions_and_order_remain_unchanged(self):
        self.spec.pop('routing')
        (self.workspace / 'state.json').unlink()
        (self.workspace / '.img2/artifacts/interior/intake.json').unlink()
        self.spec.pop('excludeComponents')
        self.assertEqual(self.context.exclude_components, ())
        (self.workspace / 'state.json').write_text(json.dumps({'profile': 'interior'}))
        self.assertEqual(self.context.exclude_components, ())
        intake_path = self.workspace / '.img2/artifacts/interior/intake.json'
        intake_path.parent.mkdir(parents=True, exist_ok=True)
        intake_path.write_text(json.dumps({'components': ['shell', 'character', 'prop'],
                                           'excludeComponents': ['prop', 'character']}))
        self.assertEqual(self.context.exclude_components, ('character', 'prop'))
        self.spec['excludeComponents'] = ['character', 'prop']
        self.assertEqual(self.context.exclude_components, ('character', 'prop'))
        intake_path.write_text(json.dumps({'components': ['shell']}))
        self.spec.pop('excludeComponents')
        self.assertEqual(self.context.exclude_components, ())

    def test_normalization_preserves_binary_and_original_material_data(self):
        self.document['materials'] = [{'name': 'original', 'extras': {'provenance': 'authored'}}]
        self.document['nodes'][0]['extras']['curtain_plane_origin'] = [4, 1, -7]
        original = glb_bytes(self.document)
        self.runtime.write_bytes(original)
        normalized = normalized_glb(self.runtime, [4, 7, -0.1])
        length = struct.unpack_from('<I', normalized, 12)[0]
        doc = json.loads(normalized[20:20 + length])
        self.assertEqual(doc['materials'], self.document['materials'])
        self.assertEqual(doc['nodes'][-1]['translation'], [-4, 0.1, 7])
        self.assertEqual(doc['nodes'][0]['extras']['curtain_plane_origin'], [0, 1.1, 0])
        self.assertEqual(normalized[20 + length:], original[-12:])
        self.assertEqual(self.runtime.read_bytes(), original)

    def test_artifact_hashes_real_files_and_uses_measured_dimensions(self):
        result = produce_artifact(self.context, self.runtime, self.measure_fixture)
        self.assertEqual(result, json.loads((self.workspace / 'artifact.json').read_text()))
        self.assertEqual(result['excludeComponents'], ['character'])
        self.assertEqual(result['roomDimensions'], {'width': 4, 'height': 3, 'depth': 6})
        self.assertEqual(result['scaleEstimate'], 1.0)
        self.assertEqual(result['frame'], {'units': 'm', 'up': '+Y', 'forward': '+Z'})
        self.assertEqual(result['pivot'], 'bottom-centre')
        self.assertIsNone(result['rig'])
        for entry in result['files']:
            path = self.workspace / entry['path']
            self.assertTrue(path.is_file())
            self.assertEqual(entry['sha256'], hashlib.sha256(path.read_bytes()).hexdigest())
        receipt = json.loads((self.workspace / 'evidence/scene-measurements.json').read_text())
        self.assertFalse(receipt['visualAcceptanceEvaluated'])
        self.assertEqual(result['excludeComponents'], receipt['excludeComponents'])
        self.assertEqual(receipt['sourceRuntime']['sha256'], hashlib.sha256(self.runtime.read_bytes()).hexdigest())
        before = (self.workspace / 'artifact.json').read_bytes()
        with self.assertRaisesRegex(ValueError, 'immutable'):
            produce_artifact(self.context, self.runtime, self.measure_fixture)
        self.assertEqual((self.workspace / 'artifact.json').read_bytes(), before)

    def test_missing_measurements_never_emit_artifact(self):
        for components in ([], ['missing'], ['character']):
            self.spec['authoring']['roomMeasurementComponents'] = components
            with self.subTest(components=components), self.assertRaises(ValueError):
                produce_artifact(self.context, self.runtime, self.measure_fixture)
            self.assertFalse((self.workspace / 'artifact.json').exists())
        self.assertFalse((self.workspace / 'evidence/room-composition.glb').exists())
        for rows in ([], [{'component': 'shell', 'bounds': [[0, 0, 0], [4, 6, 0]]}],
                     [{'component': 'shell', 'bounds': [[0, 0, 0], [4, 6, float('nan')]]}]):
            with self.subTest(rows=rows), self.assertRaises(ValueError):
                room_dimensions(rows, ['shell'])

    def test_duplicate_ownership_and_unverified_pivot_block_artifact(self):
        self.rows.append({'name': 'duplicate', 'component': 'character', 'bounds': [[0, 0, 0], [1, 1, 1]]})
        with self.assertRaisesRegex(ValueError, 'separately owned'):
            produce_artifact(self.context, self.runtime, self.measure_fixture)
        self.rows.pop()
        with self.assertRaisesRegex(ValueError, 'pivot'):
            produce_artifact(self.context, self.runtime, lambda path: copy.deepcopy(self.rows))
        self.assertFalse((self.workspace / 'artifact.json').exists())

    def test_undeclared_and_duplicated_physical_identities_block_publication(self):
        self.rows.append({'name': 'unadmitted', 'component': 'human-label',
                          'bounds': [[0, 0, 0], [1, 1, 1]]})
        with self.assertRaisesRegex(ValueError, 'undeclared'):
            produce_artifact(self.context, self.runtime, self.measure_fixture)
        self.assertFalse((self.workspace / 'artifact.json').exists())
        self.rows.pop()
        self.rows.append(copy.deepcopy(self.rows[0]))
        with self.assertRaisesRegex(ValueError, 'duplicated physical'):
            produce_artifact(self.context, self.runtime, self.measure_fixture)
        self.assertFalse((self.workspace / 'artifact.json').exists())


    def test_malformed_external_or_rigged_glb_is_not_a_scene_handoff(self):
        for change in ({'skins': [{}]}, {'animations': [{}]}, {'images': [{'uri': '../external.png'}]},
                       {'scene': None}):
            with self.subTest(change=change):
                self.runtime.write_bytes(glb_bytes({**self.document, **change}))
                with self.assertRaises(ValueError):
                    normalized_glb(self.runtime, [0, 0, 0])
        self.runtime.write_bytes(b'not a GLB')
        with self.assertRaises(ValueError):
            produce_artifact(self.context, self.runtime, self.measure_fixture)
        self.assertFalse((self.workspace / 'artifact.json').exists())

    def prepare_evidence(self):
        for name in ('reference.png', 'build_scene.py', 'camera.json', 'render.png', 'gates.json'):
            (self.workspace / name).write_bytes(('Synthetic contract fixture: ' + name).encode())
        intake = self.workspace / '.img2/artifacts/interior/intake.json'
        intake.parent.mkdir(parents=True, exist_ok=True)
        intake.write_text(json.dumps({'components': ['shell', 'character'], 'excludeComponents': ['character']}))
        (self.workspace / 'scene-spec.json').write_text(json.dumps(self.spec))
        return {'source': 'reference.png', 'spec': 'scene-spec.json', 'builder': 'build_scene.py',
                'intake': '.img2/artifacts/interior/intake.json', 'camera': 'camera.json',
                'render': 'render.png', 'gate-report': 'gates.json'}

    def candidate(self):
        return {'id': 'shell-refine', 'passId': 'geometry', 'operations': ['mesh'], 'components': ['shell'],
                'fieldPaths': ['authoring.sceneBuilder'], 'materialSources': [],
                'dependencies': ['blender'], 'rigHandoff': False}

    def test_strategy_and_repair_candidates_are_bounded_to_owned_scope(self):
        candidate = self.candidate()
        facts = {'available_dependencies': {'blender'}}
        for group in ('build-strategy', 'repair'):
            selected = candidates_for(self.context, group, 'geometry', [candidate], ['shell', 'character'], **facts)
            self.assertEqual(selected, [candidate])
            self.assertEqual(set(selected[0]), {'id', 'passId', 'components', 'operations', 'fieldPaths',
                                               'materialSources', 'dependencies', 'rigHandoff'})
        mutations = ({'components': ['character']}, {'operations': ['execute-code']},
                     {'fieldPaths': ['quality.threshold']}, {'passId': 'materials'},
                     {'dependencies': ['unverified-tool']}, {'dependencies': []},
                     {'rigHandoff': True}, {'rigHandoff': 'false'}, {'id': 'invalid.plan'},
                     {'specFields': ['authoring.sceneBuilder']}, {'expectedCost': 'heavy'},
                     {'hypothesis': 'geometry'}, {'argv': ['anything']})
        for mutation in mutations:
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                candidates_for(self.context, 'build-strategy', 'geometry', [{**candidate, **mutation}],
                               ['shell', 'character'], **facts)
        with self.assertRaises(ValueError):
            candidates_for(self.context, 'build-strategy', 'geometry', [candidate] * 4, ['shell'], **facts)
        with self.assertRaisesRegex(ValueError, 'dependencies'):
            candidates_for(self.context, 'build-strategy', 'geometry', [candidate], ['shell'])

    def test_material_sources_require_actual_bound_bytes_and_separate_authorization(self):
        evidence = self.prepare_evidence()
        material = self.workspace / 'material.png'
        material.write_bytes(b'Local material contract fixture, not an acquired/qualified texture')
        source_hash = hashlib.sha256(material.read_bytes()).hexdigest()
        evidence['material-source'] = 'material.png'
        candidate = {**self.candidate(), 'operations': ['import-model'], 'materialSources': [source_hash]}
        subject = {'runId': 'run', 'objectId': 'room', 'revision': 1, 'passId': 'geometry'}
        kwargs = {'requirements': {'criticalFeatures': [], 'requiredViews': []},
                  'policy_version': 'policy-v1', 'requested_model': 'jev-test',
                  'available_dependencies': {'blender'}}
        with self.assertRaisesRegex(ValueError, 'materialSources'):
            decision_context(self.context, 'build-strategy', subject, [candidate], evidence, ['shell', 'character'],
                             **kwargs)
        kwargs['authorized_sources'] = {source_hash}
        result = decision_context(self.context, 'build-strategy', subject, [candidate], evidence,
                                  ['shell', 'character'], **kwargs)
        self.assertEqual(result['candidates'][0]['materialSources'], [source_hash])
        self.assertIn(source_hash, {pin['sha256'] for pin in result['bindings']})
        unbound = {role: value for role, value in evidence.items() if role != 'material-source'}
        with self.assertRaisesRegex(ValueError, 'materialSources'):
            decision_context(self.context, 'repair', subject, [candidate], unbound, ['shell', 'character'], **kwargs)
        material.write_bytes(b'Changed bytes after the independent authorization')
        with self.assertRaisesRegex(ValueError, 'materialSources'):
            decision_context(self.context, 'build-strategy', subject, [candidate], evidence,
                             ['shell', 'character'], **kwargs)
        with self.assertRaisesRegex(ValueError, 'bound and authorized'):
            candidates_for(self.context, 'repair', 'geometry',
                           [{**candidate, 'materialSources': []}], ['shell'], available_dependencies={'blender'})

    def test_decision_snapshots_bind_current_bytes_without_acceptance(self):
        evidence = self.prepare_evidence()
        subject = {'runId': 'run', 'objectId': 'room', 'revision': 1, 'passId': 'geometry'}
        requirements = {'criticalFeatures': [{'id': 'shell'}], 'requiredViews': ['source']}
        for group in ('build-strategy', 'quality-review', 'repair'):
            candidates = [self.candidate()]
            if group == 'quality-review':
                candidates = []
            # Repair uses the same exact shared plan shape; diagnosis belongs to
            # the base's separate repair requirements/application lifecycle.
            result = decision_context(self.context, group, subject, candidates, evidence, ['shell', 'character'],
                                      requirements=requirements, policy_version='policy-v1', requested_model='jev-test',
                                      available_dependencies={'blender'} if group != 'quality-review' else frozenset())
            self.assertEqual(result['group'], group)
            self.assertNotIn('accepted', result)
            self.assertNotIn('decisionRefs', result)
            for pin in result['bindings']:
                self.assertEqual(pin['sha256'], hashlib.sha256((self.workspace / pin['path']).read_bytes()).hexdigest())
        with self.assertRaisesRegex(ValueError, 'dependencies'):
            decision_context(self.context, 'build-strategy', subject, [self.candidate()], evidence,
                             ['shell', 'character'], requirements=requirements,
                             policy_version='policy-v1', requested_model='jev-test')
        with self.assertRaisesRegex(ValueError, 'required fresh evidence'):
            decision_context(self.context, 'repair', subject, candidates, {'spec': 'scene-spec.json'}, ['shell'],
                             requirements=requirements, policy_version='policy-v1', requested_model='jev-test')

    def test_cli_cannot_assert_construction_authority_but_can_emit_quality_bindings(self):
        evidence = self.prepare_evidence()
        request = {'group': 'build-strategy',
                   'subject': {'runId': 'run', 'objectId': 'room', 'revision': 1, 'passId': 'geometry'},
                   'candidates': [self.candidate()], 'evidence': evidence,
                   'requirements': {'criticalFeatures': [], 'requiredViews': []},
                   'policyVersion': 'policy-v1', 'requestedModel': 'jev-test'}
        request_path = self.workspace / 'decision-request.json'
        command = [sys.executable, str(ROOT / 'tools/interior_adapter.py'),
                   '--workspace', str(self.workspace), '--request', request_path.name]
        for group in ('build-strategy', 'repair'):
            request['group'] = group
            request_path.write_text(json.dumps(request))
            proc = subprocess.run(command, capture_output=True, text=True, timeout=30,
                                  env={**os.environ, 'PYTHONDONTWRITEBYTECODE': '1'})
            self.assertNotEqual(proc.returncode, 0)
            self.assertIn('program-owned caller', proc.stderr)
        request['group'] = 'quality-review'
        request['candidates'] = []
        request_path.write_text(json.dumps(request))
        proc = subprocess.run(command, capture_output=True, text=True, timeout=30,
                              env={**os.environ, 'PYTHONDONTWRITEBYTECODE': '1'})
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertEqual(json.loads(proc.stdout)['candidates'], [])
        self.assertFalse((self.workspace / 'artifact.json').exists())

    def test_route_card_is_shipped_and_excludes_multiroom(self):
        card = json.loads((ROOT / 'route.json').read_text())
        self.assertEqual(card['outputKind'], '3d-scene')
        self.assertEqual(card['requires']['roles'], ['scene-container'])
        self.assertEqual(card['notFor']['observations']['roomCount'], ['>1'])
        self.assertEqual(card['requires']['observations']['roomCount'], [1])
        self.assertIn('route.json', json.loads((ROOT / 'package.json').read_text())['files'])

    def test_routed_profile_is_in_npm_and_installable_archive(self):
        from scripts.package_plugin import package
        self.assertEqual(json.loads((ROOT / 'domain.json').read_text()), {'id': 'interior'})
        self.assertIn('domain.json', json.loads((ROOT / 'package.json').read_text())['files'])
        with tempfile.TemporaryDirectory() as output:
            result = package(ROOT, Path(output))
            with zipfile.ZipFile(result['archive']) as archive:
                self.assertEqual(json.loads(archive.read('img2interior/domain.json')), {'id': 'interior'})


@unittest.skipUnless(os.environ.get('IMG2_BLENDER_PYTHON'), 'Set IMG2_BLENDER_PYTHON for actual native handoff')
class NativeHandoffTests(unittest.TestCase):
    def test_draft_removes_components_before_saving_and_rejects_ownership_changes(self):
        with tempfile.TemporaryDirectory() as directory:
            script = r'''
import json, sys
from pathlib import Path
from types import SimpleNamespace
import bpy
sys.path.insert(0, sys.argv[1])
from authoring import Context, bound_module
root = Path(sys.argv[2]).resolve()
assets, evidence = root/'assets', root/'evidence'
assets.mkdir()
evidence.mkdir()
(root/'builder.py').write_text("""
import bpy
from mathutils import Vector
def build_scene(context):
    h = context.helpers
    mat = h.material('fixture', (0.5, 0.5, 0.5))
    h.box('shell', (0,0,1), (4,6,2), mat, 'shell')
    h.box('separate-prop', (0,0,0.5), (1,1,1), mat, 'prop')
    data = bpy.data.cameras.new('source')
    camera = bpy.data.objects.new('source', data)
    bpy.context.scene.collection.objects.link(camera)
    camera.location = (8,-10,8)
    camera.rotation_euler = (Vector((0,0,1))-camera.location).to_track_quat('-Z','Y').to_euler()
    bpy.context.scene.camera = camera
    return {'roomBounds': {'min':[-2,-3,0], 'max':[2,3,2]}}
""")
spec = {'excludeComponents':['prop'], 'authoring': {'sceneBuilder':'builder.py',
        'sourceImageSize':[4,3], 'inspectionViews':{}}}
context = Context(root, spec, assets, evidence)
pipeline = bound_module('pipeline', context)
args = SimpleNamespace(width=16, samples=1, view='source')
pipeline.stage_draft(args)
assert {obj.get('component') for obj in bpy.context.scene.objects if obj.type=='MESH'} == {'shell'}
summary = json.loads((evidence/'authoring.json').read_text())
assert set(summary['builder']['components']) == {'shell'}
assert (assets/'room-master.blend').is_file()
spec['excludeComponents'] = []
try:
    pipeline.stage_render(args)
except ValueError as error:
    assert 'Ownership/intake changed' in str(error), error
else:
    raise AssertionError('Changed ownership was not rejected')
print('NATIVE_DRAFT_EXCLUSION_OBSERVED')
'''
            proc = subprocess.run([os.environ['IMG2_BLENDER_PYTHON'], '-c', script, str(ROOT), directory],
                                  capture_output=True, text=True, timeout=120,
                                  env={**os.environ, 'PYTHONDONTWRITEBYTECODE': '1'})
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            self.assertIn('NATIVE_DRAFT_EXCLUSION_OBSERVED', proc.stdout)

    def test_export_reimport_measurement_and_actual_exclusion(self):
        with tempfile.TemporaryDirectory() as directory:
            script = r'''
import json, sys
from pathlib import Path
import bpy
sys.path.insert(0, sys.argv[1])
from authoring import Context
from authoring.routing import apply_exclusions, measure_export, produce_artifact
root = Path(sys.argv[2]).resolve()
spec = {'routing': {'objectId': 'room'}, 'excludeComponents': ['prop'],
        'authoring': {'roomMeasurementComponents': ['shell']}}
(root/'state.json').write_text(json.dumps({'profile':'interior',
    'routeChild':{'objectId':'room', 'ownerRoute':'plugin:interior@fixture', 'outputKind':'3d-scene', 'excludedObjectIds':['prop']}}))
intake = root/'.img2/artifacts/interior/intake.json'
intake.parent.mkdir(parents=True)
intake.write_text(json.dumps({'components':['shell','prop'], 'excludeComponents':['prop']}))
context = Context(root, spec, root/'assets', root/'evidence')
bpy.ops.wm.read_factory_settings(use_empty=True)
bpy.ops.mesh.primitive_cube_add(size=1, location=(4, 7, 1.5))
shell = bpy.context.object
shell.dimensions = (4, 6, 3)
shell['component'] = 'shell'
shell['physical_id'] = 'room-shell'
bpy.ops.mesh.primitive_cube_add(size=1, location=(4, 7, 1))
bpy.context.object['component'] = 'prop'
apply_exclusions(bpy.context.scene.objects, context.exclude_components,
                 lambda obj: bpy.data.objects.remove(obj, do_unlink=True))
runtime = root/'runtime.glb'
bpy.ops.export_scene.gltf(filepath=str(runtime), export_format='GLB', export_yup=True, export_extras=True)
assert {row['component'] for row in measure_export(runtime)} == {'shell'}
artifact = produce_artifact(context, runtime)
assert all(abs(artifact['roomDimensions'][k] - v) < 1e-5
           for k, v in {'width': 4.0, 'height': 3.0, 'depth': 6.0}.items()), artifact
assert (root/'artifact.json').is_file()
print('NATIVE_HANDOFF_OBSERVED', json.dumps(artifact))
'''
            proc = subprocess.run([os.environ['IMG2_BLENDER_PYTHON'], '-c', script, str(ROOT), directory],
                                  capture_output=True, text=True, timeout=120,
                                  env={**os.environ, 'PYTHONDONTWRITEBYTECODE': '1'})
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            self.assertIn('NATIVE_HANDOFF_OBSERVED', proc.stdout)


if __name__ == '__main__':
    unittest.main()
