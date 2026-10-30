import copy
import json
from pathlib import Path
import re
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[1]


def workflow(name):
    return json.loads((ROOT / 'workflows' / (name + '.json')).read_text())


def execute(code, context):
    script = "const ctx=" + json.dumps(context) + ";const $=()=>({first:()=>({json:{snapshot_json:JSON.stringify(ctx)}})});const $input={all:()=>ctx};const result=new Function('$','$input'," + json.dumps(code) + ")($,$input);console.log(JSON.stringify(result));"
    return subprocess.run(['node', '-e', script], text=True, capture_output=True)


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.sample = json.loads((ROOT / 'data/samples/sample-roster.json').read_text())
        self.validator = next(n['parameters']['jsCode'] for n in workflow('import')['nodes'] if n['name'] == 'Validate Snapshot')

    def test_valid_synthetic_snapshot(self):
        result = execute(self.validator, self.sample)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)[0]['json']['is_sample'], True)

    def test_rejects_invalid_team_and_slot(self):
        for field, value in [('team', 'INVALID'), ('slot', 'WR')]:
            sample = copy.deepcopy(self.sample)
            sample['roster'][0][field] = value
            self.assertNotEqual(execute(self.validator, sample).returncode, 0)

    def test_research_pack_preserves_constraints(self):
        code = next(n['parameters']['jsCode'] for n in workflow('research-pack')['nodes'] if n['name'] == 'Build Research Pack')
        result = execute(code, [{'json': {'id': 1, 'snapshot_json': json.dumps(self.sample)}}])
        self.assertEqual(result.returncode, 0, result.stderr)
        pack = json.loads(result.stdout)[0]['json']
        self.assertTrue(pack['constraints']['advisory_only'])
        self.assertFalse(pack['constraints']['automatic_transactions'])
        self.assertFalse(pack['constraints']['paid_ai_apis'])
        self.assertIsNone(pack['matchups'][0]['opponent'])
        self.assertTrue(pack['warnings'])

    def test_exports_have_no_runtime_or_auth_fields(self):
        forbidden = {'credentials', 'pinData', 'staticData', 'authorization', 'access_token', 'refresh_token', 'password', 'httpHeaderAuth', 'headerParameters'}
        def inspect(obj):
            if isinstance(obj, dict):
                for key, value in obj.items():
                    if key == 'pinData':
                        self.assertEqual(value, {})
                    else:
                        self.assertNotIn(key, forbidden)
                    inspect(value)
            elif isinstance(obj, list):
                for value in obj:
                    inspect(value)
        for name, count in [('import', 4), ('research-pack', 3)]:
            w = workflow(name)
            self.assertFalse(w['active'])
            self.assertEqual(len(w['nodes']), count)
            inspect(w)
            for n in w['nodes']:
                self.assertNotIn('webhookId', n)
        self.assertTrue(self.sample['is_sample'])
        self.assertEqual(self.sample['league_id'], 'sample-league')
        for p in self.sample['roster'] + self.sample['waiver_players']:
            self.assertTrue(p['name'].startswith('Sample '))

    def test_tracked_files_have_no_common_secret_formats(self):
        paths = subprocess.check_output(['git', 'ls-files', '-z'], cwd=ROOT).decode().split('\0')
        pattern = re.compile(r'(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|sk-[A-Za-z0-9]{20,}|-----BEGIN (?:RSA |OPENSSH |EC )?PRIVATE KEY-----|eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,})')
        for path in filter(None, paths):
            self.assertIsNone(pattern.search((ROOT / path).read_text()), path)

    def test_public_templates_require_local_table_selection(self):
        expected = {
            'Preserve Raw Submission': 'REPLACE_WITH_SUBMISSIONS_TABLE_ID',
            'Save Snapshot': 'REPLACE_WITH_SNAPSHOTS_TABLE_ID',
            'Latest Snapshot': 'REPLACE_WITH_SNAPSHOTS_TABLE_ID',
        }
        selectors = {}
        for name in ('import', 'research-pack'):
            for node in workflow(name)['nodes']:
                table = node['parameters'].get('dataTableId')
                if table is not None:
                    selectors[node['name']] = table['value']
        self.assertEqual(selectors, expected)
        for name in ('import', 'research-pack'):
            source = (ROOT / 'workflows/reference' / (name + '.ts')).read_text()
            values = re.findall(r'"dataTableId":\{"__rl":true,"mode":"list","value":"([^"]+)"', source)
            self.assertEqual(len(values), 2 if name == 'import' else 1)
            self.assertTrue(set(values) <= set(expected.values()))

    def test_rejects_invalid_snapshot_payloads(self):
        for field, value in [('current_week', None), ('captured_at', 'invalid'),
                             ('roster', []), ('league_id', '')]:
            sample = copy.deepcopy(self.sample)
            sample[field] = value
            self.assertNotEqual(execute(self.validator, sample).returncode, 0)
