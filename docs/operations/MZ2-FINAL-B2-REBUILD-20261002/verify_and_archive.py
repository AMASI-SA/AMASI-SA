"""Validate completed isolated B evidence and copy reviewable outputs only."""
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import xml.etree.ElementTree as ET

ROOT = Path('C:/Users/amasi/mz2-final-b2-rebuild-20261002')
RAW = Path('D:/CodexAcceptance/mz2-final-b2-d6c0553a-20261002')
OUT = Path(__file__).parent
B = 'd6c0553ae6a99a85d52b03871b7bf64024180514'
TREE = '806e935c8ecc3268f98096cd2b477050d235c99e'
A = '79f307f6ff69ba658958ebcdce03341b39a55939'

def read(path):
    return json.loads(path.read_text(encoding='utf-8'))

def save(path, value):
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')

def git(*args):
    return subprocess.check_output(['git', *args], cwd=ROOT, text=True).strip()

assert git('rev-parse', 'HEAD') == B and git('rev-parse', 'HEAD^{tree}') == TREE
assert git('rev-parse', 'HEAD^') == A and not git('status', '--porcelain')
assert git('diff', '--name-only', A, B) == 'release/release-intent-v5.json'
assert not git('rev-list', '--full-history',
               '83363097d48e034dc7140a60c290efc684e1ffde..' + A,
               '--', 'release/release-intent-v5.json')
matrix = read(OUT / 'CI-MATRIX.json')
assert len(matrix) == 41
assert all(r['head'] == B and r['tree'] == TREE and r['conclusion'] == 'success' for r in matrix)
checks = read(OUT / 'CHECKS.json')
assert len(checks) == 73 and all(c['head'] == B for c in checks)
assert sum(c['conclusion'] == 'success' for c in checks) == 69
assert sum(c['conclusion'] == 'skipped' for c in checks) == 4
codeql_check = next(c for c in checks if c['app'] == 'github-advanced-security')
assert codeql_check['conclusion'] == 'success' and codeql_check['output']['annotations_count'] == 0
codeql = read(OUT / 'B2-codeql-proof.json')
assert codeql['head'] == B and codeql['tree'] == codeql['test_merge_tree'] == TREE
assert codeql['selected_rule_file_results'] == 0 and not codeql['all_results_in_file']
assert not codeql['original_four_open_on_new_pr'] and codeql['alert_dismissals_performed'] == 0
host_job = next(j for r in matrix for j in r['jobs']
                if j['name'] == 'Emergent Host Node 20 clean-clone adapter rehearsal')
assert host_job['conclusion'] == 'success'
archive = (OUT / 'B2-rehearsal.zip').read_bytes()
assert hashlib.sha256(archive).hexdigest() == 'bf92bdaa82453aa40ac6f1add816408d4924f167dc5d8f6002f9f9be1b401903'
import io, zipfile
with zipfile.ZipFile(io.BytesIO(archive)) as z:
    package = json.loads(z.read('mezan-package-boundary-v5.json'))
intent = read(ROOT / 'release/release-intent-v5.json')
assert package['source_git_sha'] == A and package['release_id'] == intent['runtime_identity']['release_id']
assert package['backend']['isolated_verified'] and package['frontend']['isolated_runtime_artifact_verified']
finished = read(RAW / 'finished.json')
assert finished['status'] == 'TECHNICAL_CHECKS_PASS_ACCEPTANCE_ONLY'
assert not finished['errors']
assert finished['head'] == B and finished['tree'] == TREE and finished['source_unchanged'] is True
assert len(finished['cleanup']) == 2
assert all(c['exit_code'] == 0 and c['error'] is None
           and set(c['remaining_databases']) <= {'admin', 'config', 'local'}
           for c in finished['cleanup'])
stages = read(RAW / 'stage-results.json')
assert {s['stage'] for s in stages} == {'frontend', 'ordinary-build', 'backend', 'smoke-b'}
assert all(s['exit_code'] == 0 for s in stages)
before, after = read(RAW / 'source-before.json'), read(RAW / 'source-after.json')
assert before == after
assert all(hashlib.sha256((ROOT / p).read_bytes()).hexdigest() == h for p, h in before.items())
front = read(RAW / 'frontend-summary.json')
assert front['numPassedTests'] > 0 and front['numPassedTestSuites'] > 0
assert front['numFailedTests'] == front['numPendingTests'] == 0
xml = ET.parse(RAW / 'backend.xml')
cases = list(xml.iter('testcase'))
assert cases and all(c.find(s) is None for c in cases for s in ('failure', 'error', 'skipped'))
selected = read(RAW / 'backend-selection.json')
assert len(selected) == len(set(selected)) == 154
log = (RAW / 'backend.log').read_text(encoding='utf-8')
executed = set(re.findall(r'(backend/tests/[^\s:]+\.py)::', log))
assert set(selected) <= executed, sorted(set(selected) - executed)
smoke = read(RAW / 'smoke-b/result.json')
assert smoke['status'] == 'ACCEPTANCE_PROBE_PASS' and smoke['production_verified'] is False
assert smoke['source'] == smoke['source_after']
assert smoke['source']['head'] == B and smoke['source']['tree'] == TREE
assert smoke['cleanup']['task_pid_stopped'] and smoke['cleanup']['database_absent']
probe = smoke['probe']
assert probe['database_before'] == probe['database_after']
assert probe['canonical_control_before'] == probe['canonical_control_after']
assert probe['canonical_control_before']['paused'] is True

records = []
for file in sorted(RAW.rglob('*')):
    if not file.is_file():
        continue
    relative = file.relative_to(RAW)
    if any(p in {'replica-data', 'standalone-data', 'temp', 'dist'} for p in relative.parts):
        continue
    if file.name.endswith('-mongo.log'):
        continue
    target = OUT / 'local-verification' / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(file, target)
    records.append({'path': target.relative_to(OUT).as_posix(), 'bytes': target.stat().st_size,
                    'sha256': hashlib.sha256(target.read_bytes()).hexdigest()})
save(OUT / 'LOCAL-VERIFICATION-MANIFEST.json', records)
ssot = read(OUT / 'SSOT-SOURCE-CHECK.json')
assert ssot['head'] == B and ssot['tree'] == TREE
assert ssot['static_SSOT'] == 'PASS_SCOPED_14_NATIVE_MODULES'
assert ssot['source_composition'] == ssot['A2_to_B2_invariant'] == 'PASS'
assert ssot['capture_semantics_AST_unchanged_except_result_log']
assert all(g['unchanged'] for g in ssot['guards'])
ssot['dynamic_result'] = 'PASS_EXACT_B_DECLARED_154_FILE_NATIVE_OPERATIONAL_REGRESSION'
ssot['dynamic_evidence'] = 'local-verification/backend.xml'
ssot['source_fingerprints_unchanged'] = True
save(OUT / 'SSOT-SOURCE-CHECK.json', ssot)
summary = {'head': B, 'tree': TREE, 'source_A': A, 'source_unchanged': True,
           'ci_workflows': '41/41 SUCCESS', 'checks': {'success': 69, 'conditional_skips': 4},
           'codeql_analysis': codeql['python_analysis_id'], 'codeql_selected_file_results': 0,
           'host_node20': 'PASS_EXECUTED_EXACT_B2', 'intent_runtime_id': package['release_id'],
           'selected_backend_files': len(selected), 'executed_backend_files': len(executed),
           'backend_parent_cases': len(cases), 'backend_suite_totals': [s.attrib for s in xml.iter('testsuite')],
           'backend_rejected': [], 'frontend': front, 'stages': stages,
           'smoke_b': 'PASS_ACCEPTANCE_ONLY', 'production_verified': False,
           'whole_database_and_write_control_unchanged': True,
           'production_financial_writes_by_this_task': 0,
           'production_access': 'None: fresh loopback databases; credentials/environment isolated; SmokeB denies non-loopback sockets',
           'full_business_uat': 'NOT_PASS', 'opening': 'NO', 'activation': 'NO',
           'inventory_initialization': 'NO', 'financial_schedules': 'NO', 'backfill': 'NO',
           'prepare': 'NO', 'prepublish': 'NO', 'lease': 'NO', 'merge': 'NO', 'deploy': 'NO'}
save(OUT / 'VERIFIED-RESULTS.json', summary)
print(json.dumps({'result': 'PASS_DECLARED_TECHNICAL_SCOPE', 'head': B,
                  'backend_parent_cases': len(cases), 'frontend': front,
                  'archived_files': len(records), 'smoke_b': summary['smoke_b']}))
