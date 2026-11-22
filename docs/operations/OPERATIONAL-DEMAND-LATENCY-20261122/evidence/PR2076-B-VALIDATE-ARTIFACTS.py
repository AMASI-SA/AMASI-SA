"""Read-only validation of the four exact-B GitHub Actions artifacts."""
import sys
sys.dont_write_bytecode = True

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parent
REPO = Path('/workspace/scratch/e3ebab9db878/mezan-latency-r3')
sys.path.insert(0, str(REPO / 'backend'))
from frontend_build_identity import read_frontend_build_identity, read_frontend_reproducibility_proof
from release_protocol_v5 import exact_json_equal, validate_runtime_release_identity

A = '108024c00b7f367fd1d6b15874d7a73d55de2620'
N = '24270ebdfdebb83282389308a2349cdb27e2b8e4'
B = '5bb05036d8095a87c6635c0a431c2c19d24d8f78'
B_TREE = '8f915a681de43fed9da1ec98685a56f87a4c8bcb'
RID = 'rg5-e4e41b98edc9f49b8e74adad2a905d362b7113b5b1a1fa83436a4c1ba8c4b918'
ARTIFACT = 'dcaa1f2b805c44427be8df2f5b466b85d7860bade43a3799009156300397300b'
PROOF = '30bcd91b489c6cb585c4b560937b4cd22f21b02f8ce78bab6e417b17b224eac3'
INTENT = '3a56ff073fcb3b7e362500a3be1966e2c513194f4679d5bb81a139a338c866c6'
INTENT_BLOB = '3f1b3c7101dd125593f4f38970b079ae37fc2bf4'
RUN = 45062567651


def read(path):
    return json.loads(path.read_text())


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def object_sha(data):
    return hashlib.sha1(b'blob ' + str(len(data)).encode() + b'\0' + data,
                        usedforsecurity=False).hexdigest()


def records(log):
    content = log.read_text()
    result = []
    for match in re.finditer(r'(?m)^\{', content):
        try:
            value, _ = json.JSONDecoder().raw_decode(content[match.start():])
        except ValueError:
            continue
        if isinstance(value, dict):
            result.append(value)
    return result


def main():
    run = read(ROOT / 'run-status.json')
    assert run['id'] == RUN and run['status'] == 'completed' and run['conclusion'] == 'success'
    artifacts = {row['id']: row for row in read(ROOT / 'artifact-metadata.json')}
    downloads = {row['artifact_id']: row for row in read(ROOT / 'download-verification.json')}
    jobs = read(ROOT / 'job-steps.json')
    workflow_path = '.github/workflows/mezan-production-release.yml'
    workflow = (REPO / workflow_path).read_bytes()
    assert workflow == subprocess.check_output(['git', '--no-optional-locks', '-C', str(REPO),
        'show', A + ':' + workflow_path])
    required_steps = (
        'Create a clean tracked-source clone',
        'Prove generated release inputs are absent',
        'Rehearse a conservative outer Host frozen install',
        'Emulate a Cloud Build checkout without Git metadata',
        'Invoke the Emergent package adapter under the selected Host Node',
        'Verify artifact, proof, identity, and package boundaries',
        'Prove the governed runtime returns build metadata as JSON',
        'Archive verified generated release outputs',
        'Retain verified generated release outputs',
        'Retain sanitized rehearsal evidence',
    )
    hosts = []
    identities, frontends, boundaries = [], [], []
    for node, output_id, evidence_id, job_id in (
        ('20.20.2', 14770660595, 14769624502, 137331818078),
        ('24.19.0', 14769589848, 14770446205, 137331818090),
    ):
        directory = ROOT / ('host-' + node)
        generated = directory / 'generated'
        job, = [item for item in jobs if item['id'] == job_id]
        assert job['run_id'] == RUN and job['status'] == 'completed' and job['conclusion'] == 'success'
        steps = {step['name']: step for step in job['steps']}
        assert all(steps[name]['status'] == 'completed' and steps[name]['conclusion'] == 'success'
                   for name in required_steps)
        for artifact_id in (output_id, evidence_id):
            metadata = artifacts[artifact_id]
            assert metadata['workflow_run']['head_sha'] == B and metadata['workflow_run']['id'] == RUN
            archive = ROOT / (str(artifact_id) + '.zip')
            assert archive.stat().st_size == metadata['size'] == downloads[artifact_id]['bytes']
            assert 'sha256:' + sha(archive) == metadata['digest']
        raw_intent = (generated / 'release/release-intent-v5.json').read_bytes()
        assert hashlib.sha256(raw_intent).hexdigest() == INTENT
        assert object_sha(raw_intent) == INTENT_BLOB
        intent = json.loads(raw_intent)
        assert intent['source_git_sha'] == A and intent['source_base_git_sha'] == N
        assert intent['protocol_version'] == 5 and intent['runtime_identity']['release_id'] == RID
        frontend = read_frontend_build_identity(build_root=generated / 'frontend/build',
            frontend_root=REPO / 'frontend', repo_root=REPO,
            expected_git_sha=A, require_git_source=True)
        proof = read_frontend_reproducibility_proof(frontend_build=frontend,
            proof_path=generated / 'frontend/.release/reproducible-build.json')
        assert exact_json_equal(frontend, intent['frontend_build'])
        assert exact_json_equal(proof, intent['frontend_reproducibility'])
        assert frontend['artifact_tree_sha256'] == ARTIFACT
        assert proof['proof_file']['sha256'] == PROOF
        assert frontend['toolchain']['node'] == '22.23.2' and frontend['toolchain']['yarn'] == '1.22.22'
        identity_path = generated / 'backend/release_identity.json'
        identity = read(identity_path)
        assert exact_json_equal(identity, intent['runtime_identity'])
        assert exact_json_equal(validate_runtime_release_identity(identity,
            backend_root=REPO / 'backend', allow_derived_bytecode=False), identity)

        environment = read(directory / 'mezan-adapter-environment-v5.json')
        assert environment['host_node'] == 'v' + node and environment['host_yarn'] == '1.22.22'
        assert environment['git_directory_present'] is True
        assert environment['configured_package_build_command'] == 'cd frontend && yarn build'
        emitted = records(directory / 'mezan-adapter-build-v5.log')
        adapter, = [item for item in emitted if item.get('built') is True]
        assert adapter['protocol_version'] == 5 and adapter['source_git_sha'] == A
        assert adapter['release_id'] == RID and adapter['frontend_artifact_tree_sha256'] == ARTIFACT
        cloud, = [item for item in emitted if item.get('kind') == 'emergent_cloud_build_evidence_v1']
        assert cloud['host_node'] == 'v' + node and cloud['git_directory_present'] is False
        boundary = read(directory / 'mezan-package-boundary-v5.json')
        assert exact_json_equal(boundary, adapter['package_boundary'])
        assert boundary['kind'] == 'mezan_release_package_boundary_proof_v1'
        assert boundary['source_git_sha'] == A and boundary['release_id'] == RID
        front, back = boundary['frontend'], boundary['backend']
        assert front['isolated_runtime_artifact_verified'] is True
        assert front['artifact_tree_sha256'] == ARTIFACT and front['content_type_contract'] == 'application/json'
        assert front['runtime_root'] == 'frontend' and front['static_root'] == 'frontend/build'
        assert front['build_meta'] == {**frontend['build_meta'], 'path': 'build/build-meta.json'}
        assert back['isolated_verified'] is True and back['health_verified'] is True
        assert back['git_directory_present'] is False and back['sibling_frontend_present'] is False
        assert back['runtime_root'] == 'backend'
        assert back['identity'] == {'path': 'release_identity.json',
            'bytes': identity_path.stat().st_size, 'sha256': sha(identity_path)}
        runtime_log = directory / 'mezan-governed-runtime.log'
        assert '$ node scripts/start-governed-runtime.cjs' in runtime_log.read_text()
        identities.append(identity)
        frontends.append(frontend)
        boundaries.append(boundary)
        hosts.append({
            'host_node': node, 'host_yarn': environment['host_yarn'], 'job_id': job_id,
            'job_conclusion': job['conclusion'], 'required_ci_steps_passed': list(required_steps),
            'outputs_artifact': artifacts[output_id], 'evidence_artifact': artifacts[evidence_id],
            'generated_tar_sha256': sha(directory / 'mezan-generated-release-v5.tar.gz'),
            'original_frontend_byte_and_git_source_validation': True,
            'original_reproducibility_proof_validation': True,
            'original_backend_identity_and_source_validation': True,
            'gitless_adapter_build_verified': True,
            'independent_package_proof_matches_adapter_proof': True,
            'package_boundary': boundary,
            'http_runtime_acceptance': {
                'evidence_type': 'successful exact-source CI step plus inspected workflow assertions',
                'step_name': required_steps[6], 'step_number': steps[required_steps[6]]['number'],
                'canonical_and_cache_busted_metadata': True,
                'application_json_and_wildcard_accept': True,
                'production_and_preview_host_headers': True,
                'metadata_exact_bytes_and_json_not_spa': True,
                'hashed_asset_immutable_cache_and_spa_shell_no_store': True,
                'runtime_log_sha256': sha(runtime_log),
            },
        })
    assert exact_json_equal(identities[0], identities[1])
    assert exact_json_equal(frontends[0], frontends[1])
    assert exact_json_equal(boundaries[0], boundaries[1])
    receipt = {
        'schema_version': 1, 'kind': 'pr2076_exact_b_readiness_artifact_review', 'result': 'PASS',
        'reviewed_at': datetime.now(timezone.utc).isoformat(), 'repository': 'AMASI-SA/AMASI-SA',
        'pr': 2076, 'workflow': run, 'deployment_git_sha': B, 'deployment_git_tree': B_TREE,
        'source_git_sha': A, 'source_base_git_sha': N, 'release_id': RID,
        'frontend_artifact_tree_sha256': ARTIFACT, 'reproducibility_proof_sha256': PROOF,
        'intent_sha256': INTENT, 'intent_git_blob': INTENT_BLOB,
        'workflow_source_git_sha': A, 'workflow_source_sha256': hashlib.sha256(workflow).hexdigest(),
        'both_hosts_have_identical_frontend_identity_and_package_proof': True, 'hosts': hosts,
        'production_probed': False, 'shared_app_accessed': False, 'lease_action_performed': False,
        'foreign_completion_evidence_pin': None,
        'scope': 'CI artifact/contract evidence; no claim of shared-host or Production readiness',
    }
    destination = ROOT / 'readiness-B-independent-validation.json'
    destination.write_text(json.dumps(receipt, indent=2, sort_keys=True) + '\n')
    print(json.dumps({'result': 'PASS', 'hosts': [host['host_node'] for host in hosts],
        'B': B, 'artifact_tree': ARTIFACT, 'proof_sha256': PROOF,
        'receipt': str(destination), 'receipt_sha256': sha(destination)}, sort_keys=True))


if __name__ == '__main__':
    main()
