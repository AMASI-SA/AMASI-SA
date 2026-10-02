"""Read and preserve completed Acceptance artifacts, never mutate test/source state."""
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import xml.etree.ElementTree as ET

DOC = Path(__file__).resolve().parents[1]
ROOT = Path('C:/Users/amasi/mz2-late-evidence-linear-20261002')
SETUP = Path('D:/CodexAcceptance/mz2-uat-409-88cc-20261002-a17e6b09')
PROBE = Path('D:/CodexAcceptance/mz2-uat-409-88cc-20261002-b2f907ca')
HEAD = '88cc9131027fd6783a46e2e00fc6aaec4788b8fa'
TREE = '57483f44efa381ca872262f5a8bc61d2a87cae93'
def read(path):
    return json.loads(path.read_text(encoding='utf-8'))
def save(path, value):
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
def git(*args):
    return subprocess.check_output(['git', *args], cwd=ROOT).decode().strip()
assert git('rev-parse', 'HEAD') == HEAD and git('rev-parse', 'HEAD^{tree}') == TREE
assert not git('status', '--porcelain')
for label, directory in [('setup', SETUP), ('probe', PROBE)]:
    result = read(directory / 'result.json')
    assert result['head'] == HEAD and result['tree'] == TREE and result['source_unchanged']
    assert result['mongo_exit_code'] == result['server_exit_code'] == 0
    assert set(read(directory / 'databases-before-shutdown.json')) <= {'admin', 'config', 'local'}
    assert read(directory / 'setup/cleanup-proof.json')['removed']
    before, after = read(directory / 'source-before.json'), read(directory / 'source-after.json')
    assert before == after
    for name, expected in before.items():
        assert hashlib.sha256((ROOT / name).read_bytes()).hexdigest() == expected, name
    destination = DOC / 'evidence/gate409' / label
    destination.mkdir(parents=True, exist_ok=True)
    for path in directory.rglob('*'):
        relative = path.relative_to(directory)
        if not path.is_file() or relative.parts[0] in {'mongo-data', 'dist', 'temp'} or path.name in {'mongo.log', 'stop-server'}:
            continue
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, target)
        assert target.read_bytes() == path.read_bytes()
browser = read(SETUP / 'setup/browser-results.json')
stages = read(SETUP / 'setup/stage-results.json')
assert len(stages) == 16 and browser['passed'] == 16
assert [r['stage'] for r in stages] == list(range(1, 17)) and all(r['status'] == 'PASS' for r in stages)
assert not browser['errors'] and not browser['external']
assert browser['physical_stock_approval'] == browser['opening_post'] == browser['activation'] == 'NOT_EXECUTED'
proof = read(SETUP / 'setup/final-proof.json')
assert proof['non_setup_unchanged'] and proof['no_external_network_attempts']
assert not proof['unexpected_collections'] and not any(proof['financial_counts'].values())
for initial in proof['source']['initial_controls']:
    final = next(c for c in proof['controls'] if c['_id'] == initial['_id'])
    assert {k: v for k, v in initial.items() if k != 'revision'} == {k: v for k, v in final.items() if k != 'revision'}
    if initial['writes_paused']:
        assert initial == final
probe = read(PROBE / 'public-409-probe.json')
assert probe['database_and_controls_unchanged']
assert probe['before']['fingerprints'] == probe['after']['fingerprints']
assert probe['before']['controls'] == probe['after']['controls']
assert [(r['status'], r['response']['detail']['code']) for r in probe['requests']] == [(409, 'opening_onboarding_required'), (409, 'onboarding_activation_locked')]
junit = ET.parse(PROBE / 'focused/physical-api.xml').getroot()
cases = junit.findall('.//testcase')
assert len(cases) == 4 and all(len(c) == 0 for c in cases)
observations = [json.loads(s) for s in (PROBE / 'focused/http-observations.jsonl').read_text(encoding='utf-8').splitlines()]
assert not any('denied_network' in row for row in observations)
approvals = [r for r in observations if r.get('path', '').endswith('/approve') and r['status'] == 200]
assert approvals and all(r['response']['state'] == 'approved' for r in approvals)
assert all(len(r['response']['receipt_ids']) == 5 for r in approvals)
summary = {'head': HEAD, 'tree': TREE, 'setup_stages': {'passed': 16, 'total': 16, 'scope': 'SETUP_ACCEPTANCE_ONLY'},
    'source_files_rehashed': len(before), 'source_unchanged': True,
    'setup_journals_opening_drafts_inventory_initializations': proof['financial_counts'],
    'setup_control_flags_unchanged': True, 'separate_paused_owner_unchanged': True,
    'public_409': [{'path': r['path'], 'status': r['status'], 'code': r['response']['detail']['code']} for r in probe['requests']],
    'public_409_entire_db_and_controls_unchanged': True,
    'physical_api_cases': [{'name': c.attrib['name'], 'seconds': c.attrib.get('time'), 'result': 'PASS'} for c in cases],
    'physical_api_approved_response_count': len(approvals), 'physical_api_http_record_count': len(observations),
    'physical_api_scope': 'Synthetic existing API acceptance with explicitly pre-established verified opening; not public opening or actual counted inventory acceptance',
    'attempt1_orchestration': 'INCOMPLETE after successful16/16browser: new external409probe omitted /financial-provider-apps prefix. Original response not persisted; no product-defect claim. Remaining probe/API checks corrected in fresh fixture; original run retained.',
    'full_business_uat': 'NOT_PASS', 'release_readiness': 'NO', 'production_financial_writes': 0, 'production_verified': False,
    'cleanup': 'Both fixture databases removed; onlyadmin/config/local remain; both owned servers/Mongo processes stopped; ports closed; data retained offline'}
save(DOC / 'evidence/gate409/VERIFIED-SUMMARY.json', summary)
manifest = {}
for path in sorted((DOC / 'evidence').rglob('*')):
    if path.is_file():
        raw = path.read_bytes()
        manifest[path.relative_to(DOC).as_posix()] = {'sha256': hashlib.sha256(raw).hexdigest(), 'bytes': len(raw)}
save(DOC / 'EVIDENCE-MANIFEST.json', manifest)
print(json.dumps({'stages': len(stages), 'physical_api_cases': len(cases), 'physical_approved_responses': len(approvals), 'source_files_rehashed': len(before), 'artifact_count': len(manifest)}))
