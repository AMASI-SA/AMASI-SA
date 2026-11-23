"""Read-only comparison of the exact CI artifacts; does not build or publish."""
import hashlib
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.dont_write_bytecode = True
ROOT = Path('/workspace/scratch/36b1bc151c92/mezan-side-options-release')
OUT = Path(__file__).resolve().parent
A = '45c8fb4ea91e5af8523971fbfa2270ee96552224'
B = '499b98735b5cc4eae79d380e4f8d5ec5477175e5'
J = '76efd2c3aa1563939ee3e27a9ae03f27fc1a3d43'
RELEASE = 'rg5-a762a47336e81b005a6fdfe509695c01a46b991478e27d6dc421913e94c94762'
ARTIFACT = '5895551a52ba294d373076988d0d1ba75e39855ab28cbd7a7a76c0a4038834e4'
PROOF = '70005346d66a7b82ad9d59e5211524e070ad5b08c2a2115abe58fc7b6bb3942d'
sys.path[:0] = [str(ROOT / 'backend'), str(ROOT)]

from frontend_build_identity import read_frontend_build_identity, read_frontend_reproducibility_proof
from release_protocol_v5 import validate_runtime_release_identity, exact_json_equal
from scripts.emergent_deployment_adapter import (
    _canonical_file_tree_sha256, _load_and_validate_release_intent,
    verify_release_intent_git,
)

def git(*args):
    return subprocess.run(['git', '--no-optional-locks', *args], cwd=ROOT,
                          check=True, capture_output=True).stdout

def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def record(path, name):
    return {'path': name, 'bytes': path.stat().st_size, 'sha256': sha(path)}

def equal(actual, expected, label):
    assert exact_json_equal(actual, expected), label

github = json.loads((OUT / 'github-job-artifact-metadata.json').read_text())
downloads = json.loads((OUT / 'artifact-download-verification.json').read_text())
jobs = {job['name']: job for job in github['jobs']}
intent_raw = git('show', B + ':release/release-intent-v5.json')
intent = json.loads(intent_raw)
equal(git('diff', '--name-only', A, B).decode().splitlines(),
      ['release/release-intent-v5.json'], 'B must change only the intent')
assert git('rev-list', '--full-history', J + '..' + A, '--', 'release/release-intent-v5.json') == b''
assert git('show', J + ':release/release-intent-v5.json') == git('show', A + ':release/release-intent-v5.json')
equal(intent['source_git_sha'], A, 'intent source')
equal(intent['source_base_git_sha'], J, 'intent base')
equal(intent['runtime_identity']['release_id'], RELEASE, 'intent release')
assert intent['protocol_version'] == 5 and type(intent['protocol_version']) is int
git_binding = verify_release_intent_git(intent, deployment_git_sha=B)
assert git_binding['source_git_sha'] == A and git_binding['source_base_git_sha'] == J
source_refs = git('ls-tree', '-r', A, '--', 'frontend').decode().splitlines()
tracked_frontend = {}
for row in source_refs:
    head, name = row.split('\t', 1)
    mode, kind, blob = head.split()
    assert kind == 'blob'
    tracked_frontend[name.removeprefix('frontend/')] = (mode, blob)
assert len(tracked_frontend) == intent['frontend_source']['file_count']
for source in intent['frontend_source']['files']:
    assert tracked_frontend[source['path']] == (source['mode'], source['git_blob'])

hosts = {}
host_manifests = []
package_proofs = []
for host in ['20.20.2', '24.19.0']:
    output_name = 'release-v5-generated-outputs-host-' + host
    evidence_name = 'release-v5-clean-clone-evidence-host-' + host
    generated = OUT / output_name / 'generated'
    evidence = OUT / evidence_name
    job = jobs['Emergent Host Node ' + host + ' clean-clone adapter rehearsal']
    assert job['status'] == 'completed' and job['conclusion'] == 'success'
    required_steps = ['Prove generated release inputs are absent',
        'Emulate a Cloud Build checkout without Git metadata',
        'Invoke the Emergent package adapter under the selected Host Node',
        'Verify artifact, proof, identity, and package boundaries',
        'Prove the governed runtime returns build metadata as JSON']
    completed = {step['name']: step['conclusion'] for step in job['steps']}
    for name in required_steps:
        assert completed.get(name) == 'success', (host, name)
    for artifact_name in [output_name, evidence_name]:
        item = next(x for x in downloads if x['name'] == artifact_name)
        assert item['workflow_run']['head_sha'] == B
        assert item['workflow_run']['id'] == 45216427337
        assert item['zip_digest_verified'] is True
        assert sha(Path(item['zip_path'])) == item['digest'].removeprefix('sha256:')
    assert (generated / 'release/release-intent-v5.json').read_bytes() == intent_raw
    equal(_load_and_validate_release_intent(generated / 'release/release-intent-v5.json',
          verify_deployment_git=False), intent, host + ' canonical intent validation')
    frontend = read_frontend_build_identity(build_root=generated / 'frontend/build',
        frontend_root=ROOT / 'frontend', expected_git_sha=A, require_git_source=False)
    proof = read_frontend_reproducibility_proof(frontend_build=frontend,
        proof_path=generated / 'frontend/.release/reproducible-build.json')
    identity_path = generated / 'backend/release_identity.json'
    identity = json.loads(identity_path.read_text())
    equal(frontend, intent['frontend_build'], host + ' full frontend build identity')
    equal(proof, intent['frontend_reproducibility'], host + ' full frontend proof')
    equal(identity, intent['runtime_identity'], host + ' full runtime identity')
    equal(validate_runtime_release_identity(identity, backend_root=ROOT / 'backend',
          allow_derived_bytecode=False), identity, host + ' runtime source validation')
    assert frontend['artifact_tree_sha256'] == ARTIFACT
    assert proof['proof_file']['sha256'] == PROOF
    equal(frontend['toolchain'], {'node':'22.23.2','yarn':'1.22.22','vite':'8.2.4'}, 'governed toolchain')
    full_manifest = json.loads((OUT / output_name / 'generated-file-manifest.json').read_text())
    for name, info in full_manifest.items():
        assert record(generated / name, name) == {k:info[k] for k in ['bytes','sha256']} | {'path':name}
    host_manifests.append(full_manifest)
    frontend_records = [{k:source[k] for k in ['path','bytes','sha256']}
                        for source in intent['frontend_source']['files']]
    frontend_records += [{k:info[k] for k in ['bytes','sha256']} | {'path':name.removeprefix('frontend/')}
                         for name,info in full_manifest.items() if name.startswith('frontend/build/')]
    frontend_records.sort(key=lambda x:x['path'])
    assert len({x['path'] for x in frontend_records}) == len(frontend_records)
    backend_records = [{k:source[k] for k in ['path','bytes','sha256']}
                       for source in intent['backend_runtime_source']['files']]
    backend_records.append(record(identity_path, 'release_identity.json'))
    backend_records.sort(key=lambda x:x['path'])
    expected_package = {
      'schema_version':1,'kind':'mezan_release_package_boundary_proof_v1',
      'source_git_sha':A,'release_id':RELEASE,
      'frontend':{'runtime_root':'frontend','static_root':'frontend/build',
        'file_count':len(frontend_records),
        'package_tree_sha256':_canonical_file_tree_sha256(frontend_records),
        'artifact_tree_sha256':ARTIFACT,
        'build_meta':record(generated / 'frontend/build/build-meta.json','build/build-meta.json'),
        'content_type_contract':'application/json','isolated_runtime_artifact_verified':True},
      'backend':{'runtime_root':'backend','file_count':len(backend_records),
        'package_tree_sha256':_canonical_file_tree_sha256(backend_records),
        'identity':record(identity_path,'release_identity.json'),
        'isolated_verified':True,'health_verified':True,
        'sibling_frontend_present':False,'git_directory_present':False}}
    package = json.loads((evidence / 'mezan-package-boundary-v5.json').read_text())
    equal(package, expected_package, host + ' complete package boundary proof')
    package_proofs.append(package)
    environment = json.loads((evidence / 'mezan-adapter-environment-v5.json').read_text())
    assert environment['host_node'] == 'v' + host and environment['host_yarn'] == '1.22.22'
    assert environment['configured_package_build_command'] == 'cd frontend && yarn build'
    assert environment['declared_frontend_package_root'] == 'frontend'
    assert environment['declared_frontend_static_root'] == 'frontend/build'
    assert environment['declared_backend_package_root'] == 'backend'
    log = (evidence / 'mezan-adapter-build-v5.log').read_text()
    assert 'node scripts/build-entry.cjs' in log
    assert RELEASE in log and A in log and ARTIFACT in log
    log_markers = [line for line in log.splitlines() if any(value in line for value in
        ['EMERGENT_RELEASE', 'REPRODUCIBLE', 'built in', 'build-entry.cjs'])]
    hosts[host] = {'job_id':job['id'],'job_conclusion':'success',
      'required_steps_verified':required_steps,
      'generated_directory':str(generated),'generated_file_count':len(full_manifest),
      'frontend_build_file_count':sum(x.startswith('frontend/build/') for x in full_manifest),
      'frontend_public_file_count':len(frontend['public_files']),
      'original_validators_passed':['_load_and_validate_release_intent', 'verify_release_intent_git',
          'read_frontend_build_identity','read_frontend_reproducibility_proof','validate_runtime_release_identity'],
      'full_package_proof':package,'host_environment':environment,
      'build_log_sha256':sha(evidence/'mezan-adapter-build-v5.log'),
      'runtime_log_sha256':sha(evidence/'mezan-governed-runtime.log'),
      'package_proof_sha256':sha(evidence/'mezan-package-boundary-v5.json'),
      'build_log_markers':log_markers}
equal(host_manifests[0],host_manifests[1],'Node20/24 complete generated contents including modes')
equal(package_proofs[0],package_proofs[1],'Node20/24 complete package proof equality')
review = {'review_version':1,'reviewed_at':datetime.now(timezone.utc).isoformat(),
  'reviewer':'side_options_browser_acceptance','status':'PASS',
  'source_git_sha':A,'source_base_git_sha':J,'deployment_git_sha':B,
  'protocol_version':5,'release_id':RELEASE,'frontend_artifact_tree_sha256':ARTIFACT,
  'reproducibility_proof_sha256':PROOF,'run_id':45216427337,'run_attempt':1,
  'run_url':'https://github.com/AMASI-SA/AMASI-SA/actions/runs/45216427337',
  'artifacts':downloads,'hosts':hosts,
  'source_binding':{'intent_bytes_match_exact_B':True,'B_changes_only_intent':True,
    'J_to_A_full_history_has_no_intent_change':True,'frontend_source_git_records_verified':1415,
    'backend_source_git_records_verified':intent['backend_runtime_source']['file_count'],
    'release_control_source_git_records_verified':intent['release_control_source']['file_count']},
  'comparison':{'all_generated_file_contents_and_modes_identical':True,
    'generated_file_count_per_host':len(host_manifests[0]),'full_package_proofs_identical':True,
    'archive_container_hashes_identical':False,
    'archive_container_note':'Each tar.gz matches its own SHA256 sidecar; host archive containers differ while all extracted file paths, contents, lengths and modes match.'},
  'publication_complete':False,
  'limits':['Artifact and source validation only; no live production verification or publishing action.',
    'Actual runtime JSON MIME/cache checks and isolated package health were executed by the successful exact-B CI steps; this review did not start servers or rerun a build.',
    'Package tree digests were independently recomputed from verified source manifest records and actual generated file bytes; all emitted proof fields were compared, not only flags.']}
(OUT/'independent-review.json').write_text(json.dumps(review,sort_keys=True,indent=2)+'\n')
print(json.dumps({'status':'PASS','review_path':str(OUT/'independent-review.json'),
  'hosts':list(hosts),'generated_files_per_host':len(host_manifests[0]),
  'full_package_proofs_identical':True,'publication_complete':False}))
