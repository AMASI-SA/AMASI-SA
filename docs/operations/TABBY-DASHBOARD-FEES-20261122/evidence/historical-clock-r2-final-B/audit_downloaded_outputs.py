#!/usr/bin/env python3
"""Read-only verification of downloaded CI outputs against frozen B source."""
import base64
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import stat
import subprocess
import sys
import tarfile
import zipfile

ROOT = Path('/workspace/scratch/a719d7c39de9/tabby-release-evidence/docs/operations/TABBY-DASHBOARD-FEES-20261122/evidence/historical-clock-r2-final-B')
SOURCE = Path('/workspace/scratch/a719d7c39de9/mezan-tabby-historical-clock-r2')
A = 'c464eb01f3b4201da58c664312d009617e3f1766'
J = '422fffc8fb9c381165cc76b1ded5ca595773ce8e'
B = 'be0c1c67569e5fb59104ccdd43aaa44169fe0ff5'
INTENT_SHA = 'ff1f5f912fc9d828d12c8aa7766018f21006ad36372c8d70caaee34d693b95f9'
B_TREE = '756a1285dfe837ebdab6c6a4a65750b59952227a'
ARTIFACT_PINS = {
    'release-v5-clean-clone-evidence-host-20.20.2.zip': (63733, 'a3d556d98f115ee47f8ff1053875e4f55a2fdbaef17a65e95cda550112707336'),
    'release-v5-generated-outputs-host-20.20.2.zip': (3848871, 'db87cb9de644501a4d36e8c3a82c1b47aa564068a0d2e3168052949f7dc6b5a5'),
    'release-v5-clean-clone-evidence-host-24.19.0.zip': (63737, 'ab2f86db44dd948ba6526265cd58976bee944d63d67100922461c8609c7d6b97'),
    'release-v5-generated-outputs-host-24.19.0.zip': (3851361, 'fcc2558ee6cefaf41081fffc09e0e6008aee29e72862c5189ee8ebe48cfd970a'),
}
sys.dont_write_bytecode = True
sys.path[:0] = [str(SOURCE / 'backend'), str(SOURCE)]
from frontend_build_identity import read_frontend_build_identity, read_frontend_reproducibility_proof
from release_identity import read_release_identity
from scripts.emergent_deployment_adapter import _canonical_file_tree_sha256, load_release_intent


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def record(path, raw):
    return {'path': path, 'bytes': len(raw), 'sha256': sha(raw)}


def json_objects(text):
    decoder = json.JSONDecoder()
    found = []
    offset = 0
    while offset < len(text):
        start = text.find('{', offset)
        if start < 0:
            break
        try:
            value, length = decoder.raw_decode(text[start:])
        except json.JSONDecodeError:
            offset = start + 1
            continue
        if isinstance(value, dict):
            found.append(value)
        offset = start + length
    return found


def read_zip(path, expected):
    raw = path.read_bytes()
    expected_bytes, expected_digest = ARTIFACT_PINS[path.name]
    assert len(raw) == expected_bytes and sha(raw) == expected_digest
    assert base64.decodebytes(path.with_name(path.name + '.base64').read_bytes()) == raw
    with zipfile.ZipFile(path) as z:
        rows = z.infolist()
        assert len(rows) == len(expected)
        assert {v.filename for v in rows} == set(expected)
        assert sum(v.file_size for v in rows) < 30_000_000
        assert all(stat.S_ISREG(v.external_attr >> 16) and not (v.flag_bits & 1) for v in rows)
        assert z.testzip() is None
        return {v.filename: z.read(v) for v in rows}


def audit(host):
    ev_zip = ROOT / f'release-v5-clean-clone-evidence-host-{host}.zip'
    gen_zip = ROOT / f'release-v5-generated-outputs-host-{host}.zip'
    evidence = read_zip(ev_zip, [
        'mezan-adapter-build-v5.log', 'mezan-adapter-environment-v5.json',
        'mezan-package-boundary-v5.json', 'mezan-governed-runtime.log',
    ])
    generated = read_zip(gen_zip, [
        'mezan-generated-release-v5.tar.gz', 'mezan-generated-release-v5.sha256',
    ])
    tar_bytes = generated['mezan-generated-release-v5.tar.gz']
    expected_line = f"{sha(tar_bytes)}  mezan-generated-release-v5.tar.gz\n".encode()
    assert generated['mezan-generated-release-v5.sha256'] == expected_line
    out = ROOT / f'host-{host}'
    out.mkdir(mode=0o700, exist_ok=True)
    tar_records = []
    mode_records = []
    with tarfile.open(fileobj=io.BytesIO(tar_bytes), mode='r:gz') as t:
        members = t.getmembers()
        assert len(members) == len({v.name for v in members})
        assert sum(v.size for v in members) < 50_000_000
        for member in members:
            p = PurePosixPath(member.name)
            assert not p.is_absolute() and '..' not in p.parts and str(p) == member.name
            assert member.isdir() or member.isfile()
            assert member.mode == (0o755 if member.isdir() else 0o644)
            mode_records.append({'path': member.name, 'kind': 'directory' if member.isdir() else 'file', 'mode': oct(member.mode)})
            if member.isdir():
                assert member.name == 'frontend/build' or member.name.startswith('frontend/build/')
                continue
            assert member.name.startswith('frontend/build/') or member.name in {
                'frontend/.release/reproducible-build.json',
                'backend/release_identity.json', 'release/release-intent-v5.json',
            }
            raw = t.extractfile(member).read()
            assert len(raw) == member.size
            target = out / member.name
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists():
                assert not target.is_symlink() and target.read_bytes() == raw
                assert stat.S_IMODE(target.stat().st_mode) == member.mode
            else:
                with target.open('xb') as f:
                    f.write(raw)
                target.chmod(member.mode)
            tar_records.append(record(member.name, raw))

    intent_path = out / 'release/release-intent-v5.json'
    intent_raw = intent_path.read_bytes()
    assert intent_raw == (SOURCE / 'release/release-intent-v5.json').read_bytes()
    assert sha(intent_raw) == INTENT_SHA
    intent = json.loads(intent_raw)
    assert intent['source_git_sha'] == A and intent['source_base_git_sha'] == J
    metadata = json.loads((out / 'frontend/build/build-meta.json').read_bytes())
    expected_names = {'frontend/build/' + v['path'] for v in metadata['files']} | {
        'frontend/build/build-meta.json', 'frontend/.release/reproducible-build.json',
        'backend/release_identity.json', 'release/release-intent-v5.json',
    }
    assert {v['path'] for v in tar_records} == expected_names
    assert len(tar_records) == len(expected_names)
    fb = read_frontend_build_identity(
        build_root=out / 'frontend/build', frontend_root=SOURCE / 'frontend',
        repo_root=SOURCE, expected_git_sha=A, require_git_source=False,
    )
    assert fb == intent['frontend_build']
    proof = read_frontend_reproducibility_proof(
        frontend_build=fb, proof_path=out / 'frontend/.release/reproducible-build.json',
    )
    assert proof == intent['frontend_reproducibility']
    identity_path = out / 'backend/release_identity.json'
    assert json.loads(identity_path.read_bytes()) == intent['runtime_identity']
    health = read_release_identity(identity_path, backend_root=SOURCE / 'backend')
    for key in ('verified_identity_available', 'backend_runtime_source_verified',
                'release_control_source_bound', 'critical_file_hashes_match', 'frontend_build_verified'):
        assert health[key] is True, key
    assert health['release_id'] == intent['runtime_identity']['release_id']
    assert health['source_git_sha'] == A and health['source_base_git_sha'] == J

    package = json.loads(evidence['mezan-package-boundary-v5.json'])
    # Recompute complete package membership from frozen source plus generated bytes.
    front_records = [{k: v[k] for k in ('path', 'bytes', 'sha256')}
                     for v in intent['frontend_source']['files']]
    front_records.extend({**v, 'path': v['path'].removeprefix('frontend/')}
                         for v in tar_records if v['path'].startswith('frontend/build/'))
    back_records = [{k: v[k] for k in ('path', 'bytes', 'sha256')}
                    for v in intent['backend_runtime_source']['files']]
    identity_record = record('release_identity.json', identity_path.read_bytes())
    back_records.append(identity_record)
    front_records.sort(key=lambda row: row['path'])
    back_records.sort(key=lambda row: row['path'])
    tar_records.sort(key=lambda row: row['path'])
    for section, rows in [('frontend', front_records), ('backend', back_records)]:
        assert len(rows) == len({v['path'] for v in rows})
        assert package[section]['file_count'] == len(rows)
        assert package[section]['package_tree_sha256'] == _canonical_file_tree_sha256(rows)
    expected_package = {
        'schema_version': 1, 'kind': 'mezan_release_package_boundary_proof_v1',
        'source_git_sha': A, 'release_id': health['release_id'],
        'frontend': {
            'runtime_root': 'frontend', 'static_root': 'frontend/build',
            'file_count': len(front_records),
            'package_tree_sha256': _canonical_file_tree_sha256(front_records),
            'artifact_tree_sha256': fb['artifact_tree_sha256'],
            'build_meta': {**fb['build_meta'], 'path': 'build/build-meta.json'},
            'content_type_contract': 'application/json',
            'isolated_runtime_artifact_verified': True,
        },
        'backend': {
            'runtime_root': 'backend', 'file_count': len(back_records),
            'package_tree_sha256': _canonical_file_tree_sha256(back_records),
            'identity': identity_record, 'isolated_verified': True,
            'health_verified': True, 'sibling_frontend_present': False,
            'git_directory_present': False,
        },
    }
    assert package == expected_package
    environment = json.loads(evidence['mezan-adapter-environment-v5.json'])
    assert environment['host_node'] == 'v' + host
    assert environment['host_yarn'] == '1.22.22'
    assert environment['git_directory_present'] is True
    assert environment['configured_package_build_command'] == 'cd frontend && yarn build'
    assert environment['governed_install_command'] == 'governed-toolchain exec -- yarn --cwd frontend install --frozen-lockfile --non-interactive'
    assert environment['governed_build_command'] == 'governed-toolchain exec -- yarn --cwd frontend build:release'
    assert environment['outer_build_command_observed'] is False
    assert environment['outer_install_command_observed'] is False
    assert environment['platform_snapshot_workspace_shared_observed'] is False
    objects = json_objects(evidence['mezan-adapter-build-v5.log'].decode())
    outcomes = [v for v in objects if v.get('built') is True]
    assert len(outcomes) == 1
    outcome = outcomes[0]
    assert outcome == {
        'built': True, 'protocol_version': 5,
        'source_git_sha': A, 'release_id': health['release_id'],
        'frontend_artifact_tree_sha256': fb['artifact_tree_sha256'],
        'package_boundary': expected_package,
    }
    envs = [v for v in objects if v.get('kind') == 'emergent_cloud_build_evidence_v1']
    assert len(envs) == 1
    assert envs[0]['host_node'] == 'v' + host
    assert envs[0]['git_directory_present'] is False
    assert envs[0]['host_yarn'] == '1.22.22'
    assert fb['toolchain'] == {'node': '22.23.2', 'yarn': '1.22.22', 'vite': '8.2.4'}
    assert intent['client_environment'] == {'REACT_APP_BACKEND_URL': {'present': True, 'value': 'https://mezansalla.com'}}
    assert fb['environment']['values'] == {'REACT_APP_BACKEND_URL': {'present': True, 'sha256': sha(b'https://mezansalla.com')}}
    assert fb['environment']['effective'] == {'NODE_ENV': 'production', 'VITE_USER_NODE_ENV_present': False, 'VITE_prefixed_keys': []}
    assert b'node scripts/start-governed-runtime.cjs' in evidence['mezan-governed-runtime.log']
    installs = [v for v in objects if v.get('node_version') == 'v22.23.2']
    assert installs and all(v['yarn_version'] == '1.22.22' for v in installs)
    report = {
        'host_node': host, 'generated_zip_sha256': sha(gen_zip.read_bytes()),
        'evidence_zip_sha256': sha(ev_zip.read_bytes()), 'tar_sha256': sha(tar_bytes),
        'tar_members': len(members), 'generated_files': len(tar_records),
        'member_modes': sorted(mode_records, key=lambda row: row['path']),
        'public_frontend_files': len(fb['public_files']),
        'frontend_package_members': len(front_records), 'backend_package_members': len(back_records),
        'official_zip_digests_and_base64_round_trip_passed': True,
        'preflight_git_present': True, 'adapter_git_present': False,
        'approved_environment_exact_match': True,
        'generated_file_tree_sha256': _canonical_file_tree_sha256(tar_records),
        'intent_sha256': sha(intent_raw), 'package_boundary': expected_package,
        'build_meta': fb['build_meta'], 'proof_file': proof['proof_file'],
        'toolchain': fb['toolchain'], 'original_readers_passed': True,
        'adapter_built_without_git': True, 'source_sha': A, 'base_sha': J, 'deployment_sha': B,
    }
    (ROOT / f'audit-host-{host}.json').write_text(json.dumps(report, indent=2, sort_keys=True) + '\n')
    return report


before = subprocess.check_output(['git', '--no-optional-locks', '-C', str(SOURCE), 'status', '--porcelain=v1', '--untracked-files=all'])
assert not before
assert subprocess.check_output(['git', '--no-optional-locks', '-C', str(SOURCE), 'rev-parse', 'HEAD'], text=True).strip() == B
assert subprocess.check_output(['git', '--no-optional-locks', '-C', str(SOURCE), 'rev-parse', 'HEAD^{tree}'], text=True).strip() == B_TREE
reviewed_intent = load_release_intent()
assert reviewed_intent['source_git_sha'] == A and reviewed_intent['source_base_git_sha'] == J
assert sha((SOURCE / 'release/release-intent-v5.json').read_bytes()) == INTENT_SHA
assert all(host in ('20.20.2', '24.19.0') for host in sys.argv[1:]) and sys.argv[1:]
reports = [audit(host) for host in sys.argv[1:]]
if len(reports) == 2:
    assert reports[0]['package_boundary'] == reports[1]['package_boundary']
    assert reports[0]['generated_file_tree_sha256'] == reports[1]['generated_file_tree_sha256']
    assert reports[0]['member_modes'] == reports[1]['member_modes']
    print('BOTH_HOSTS_IDENTICAL_PACKAGES_AND_GENERATED_BYTES')
assert subprocess.check_output(['git', '--no-optional-locks', '-C', str(SOURCE), 'status', '--porcelain=v1', '--untracked-files=all']) == before
print(json.dumps(reports, indent=2, sort_keys=True))
