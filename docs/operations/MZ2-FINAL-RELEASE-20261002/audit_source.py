"""Reproducible read-only native boundary audit for the combined release source."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess

p = argparse.ArgumentParser()
p.add_argument('--root', required=True)
p.add_argument('--output', required=True)
a = p.parse_args()
root = Path(a.root).resolve()
out = Path(a.output).resolve()
baseline = '88cc9131027fd6783a46e2e00fc6aaec4788b8fa'
expected = '57688c6423848165525bbc85265c4bb56e65da1c'
def git(*args):
    return subprocess.check_output(['git', *args], cwd=root).decode().strip()
assert git('rev-parse', 'HEAD') == expected
assert not git('status', '--porcelain')
prior = json.loads((Path(__file__).parent.parent / 'MZ2-FINAL-ACCEPTANCE-20261002/evidence/SSOT-SOURCE-CHECK.json').read_text())
findings = []
pattern = re.compile(r'\b(?:journal_entries|accounting_journals|accounting_ledger|financial_transactions)\b')
for entry in prior['source_findings']:
    path = entry['path']
    data = (root / path).read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    findings.append({'path': path, 'sha256': digest,
                     'matches_prior_verified_source': digest == entry['sha256'],
                     'legacy_sink_name_matches': sorted(set(pattern.findall(data.decode())))})
guards = []
for path in ['backend/accounting_write_control.py', 'backend/accounting_atomic.py',
             'backend/accounting_writer_transition.py', 'scripts/production_release_guard.py']:
    guards.append({'path': path, 'sha256': hashlib.sha256((root / path).read_bytes()).hexdigest(),
                   'matches_approved_1240': git('rev-parse', 'HEAD:' + path) == git('rev-parse', baseline + ':' + path)})
backend_changed = git('diff', '--name-only', baseline, 'HEAD', '--', 'backend').splitlines()
assert not backend_changed
assert all(x['matches_prior_verified_source'] and not x['legacy_sink_name_matches'] for x in findings)
assert all(x['matches_approved_1240'] for x in guards)
out.parent.mkdir(parents=True, exist_ok=True)
out.write_text(json.dumps({'head': expected, 'tree': git('rev-parse', 'HEAD^{tree}'),
    'scope': '14 selected converted native writer/report/evidence modules; not whole historical application',
    'static_result': 'PASS', 'dynamic_result': 'PENDING_CURRENT_REGRESSION',
    'legacy_sink_pattern': pattern.pattern, 'source_findings': findings,
    'guards': guards, 'backend_changed_from_1240': backend_changed,
    'production_financial_writes': 0}, indent=2) + '\n', encoding='utf-8')
print(json.dumps({'static_result': 'PASS', 'modules': len(findings), 'unchanged_guards': len(guards)}))
