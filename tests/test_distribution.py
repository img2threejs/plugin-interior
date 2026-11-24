# SPDX-License-Identifier: Apache-2.0
"""Consumer source-archive version identity and scoped license grants."""
import json
from pathlib import Path
import re
import sys
import tempfile
import unittest
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.package_plugin import package


class DistributionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='interior-source-distribution-')
        self.addCleanup(temporary.cleanup)
        result = package(ROOT, Path(temporary.name))
        archive = zipfile.ZipFile(result['archive'])
        self.addCleanup(archive.close)
        self.files = {name.removeprefix('img2interior/'): archive.read(name)
                      for name in archive.namelist()}

    def test_routed_distribution_has_a_distinct_consistent_version(self):
        plugin = json.loads(self.files['plugin.json'])
        npm = json.loads(self.files['package.json'])
        skill = re.search(r'^version: (\S+)$', self.files['SKILL.md'].decode(), re.MULTILINE)
        self.assertIsNotNone(skill)
        self.assertEqual(plugin['version'], npm['version'])
        self.assertEqual(plugin['version'], skill[1])
        # Published 0.3.1 predates route.json and cannot identify this adapter.
        self.assertGreater(tuple(map(int, plugin['version'].split('.'))), (0, 3, 1))

    def test_every_shipped_apache_header_has_a_scoped_component_grant(self):
        notices = self.files['THIRD_PARTY_NOTICES.md'].decode()
        section = notices.split('## Apache-2.0 components\n', 1)[1].split('\n## ', 1)[0]
        grants = re.findall(r'^- `([^`]+)`', section, re.MULTILINE)
        uncovered = []
        for name, payload in self.files.items():
            if not name.endswith('.py'):
                continue
            header = payload.decode().splitlines()[:3]
            if '# SPDX-License-Identifier: Apache-2.0' not in header:
                continue
            if not any(name == grant or (grant.endswith('/') and name.startswith(grant))
                       for grant in grants):
                uncovered.append(name)
        self.assertEqual(sorted(uncovered), [], 'Packaged Apache headers lack component grants')
        self.assertEqual(json.loads(self.files['package.json'])['license'], 'SEE LICENSE IN LICENSE')


if __name__ == '__main__':
    unittest.main()
