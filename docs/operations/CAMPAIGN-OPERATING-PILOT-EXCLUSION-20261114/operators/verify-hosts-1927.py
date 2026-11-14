"""Read-only A5/B5 archive acceptance; no extraction, imports from archives or network.

Inputs JSON supplies intent_commit, workflow_run_id, jobs_json (the raw GitHub
jobs response including steps), and hosts keyed by 20.20.2 / 24.19.0. Each host
has job_id and generated/evidence objects containing path and expected sha256.
Prints a compact acceptance record; the caller may retain stdout as evidence.
This checks supplied CI evidence and does not prove a production deployment.
"""
import argparse
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import re
import sys
import tarfile
import zipfile


SOURCE = '3545ef861c17f1411d6258efb5db4a5aaf52f577'
BASE = 'af9caabb3119aeb9cf450af0a8b89d376cef8c74'
INTENT_SHA256 = 'e5223937d883ce8ce2d02e524e079d78658be47b289ddc2c174d452c5ca1aebf'
HOSTS = ('20.20.2', '24.19.0')
TAR_NAME = 'mezan-generated-release-v5.tar.gz'
ROUTE_STEP = 'Prove the governed runtime returns build metadata as JSON'
BUILD_STEP = 'Invoke the Emergent package adapter under the selected Host Node'
PACKAGE_STEP = 'Verify artifact, proof, identity, and package boundaries'


def require(condition, message):
    if not condition:
        raise ValueError(message)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def read_zip(spec):
    data = Path(spec['path']).read_bytes()
    require(re.fullmatch(r'[0-9a-f]{64}', spec['sha256']) is not None,
            'Supply the observed outer ZIP SHA256')
    require(digest(data) == spec['sha256'], 'Outer ZIP checksum differs: ' + spec['path'])
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        names = archive.namelist()
        require(len(names) == len(set(names)), 'Duplicate ZIP member')
        return {name: archive.read(name) for name in names}


def check_record(data, record, label):
    require(len(data) == record['bytes'] and digest(data) == record['sha256'],
            'Pinned content record differs: ' + label)


def generated(spec, intent_bytes, intent):
    archive = read_zip(spec)
    require(set(archive) == {TAR_NAME, 'mezan-generated-release-v5.sha256'},
            'Unexpected generated ZIP members')
    checksum = archive['mezan-generated-release-v5.sha256'].decode('ascii').split()
    require(checksum == [digest(archive[TAR_NAME]), TAR_NAME], 'Inner TAR checksum differs')
    files, modes = {}, {}
    with tarfile.open(fileobj=io.BytesIO(archive[TAR_NAME]), mode='r:gz') as bundle:
        for member in bundle.getmembers():
            name = member.name
            require(name not in modes and not PurePosixPath(name).is_absolute()
                    and '..' not in PurePosixPath(name).parts, 'Ambiguous TAR path')
            require(member.isfile() or member.isdir(), 'Unexpected TAR member type')
            modes[name] = (member.mode, 'file' if member.isfile() else 'directory')
            if member.isfile():
                files[name] = bundle.extractfile(member).read()
    require(len(files) == 293, 'Generated file count differs from the reviewed 293 files')
    require(files['release/release-intent-v5.json'] == intent_bytes, 'Intent bytes differ from A5')
    identity_bytes = files['backend/release_identity.json']
    identity = json.loads(identity_bytes)
    require(identity == intent['runtime_identity'], 'Runtime identity differs from A5 intent')
    build = intent['frontend_build']
    meta_bytes = files['frontend/build/build-meta.json']
    check_record(meta_bytes, build['build_meta'], 'build-meta.json')
    meta = json.loads(meta_bytes)
    for key, value in build.items():
        if key not in ('build_meta', 'source'):
            require(meta[key] == value, 'Frontend metadata field differs: ' + key)
    require(meta['source'] == intent['frontend_source'], 'Full frontend source manifest differs')
    expected = {'release/release-intent-v5.json', 'backend/release_identity.json',
                'frontend/.release/reproducible-build.json', 'frontend/build/build-meta.json'}
    content_paths = set()
    for record in meta['files']:
        path = 'frontend/build/' + record['path']
        require(path not in content_paths, 'Duplicate frontend content record')
        content_paths.add(path)
        check_record(files[path], record, path)
    require(len(content_paths) == 289 and set(files) == expected | content_paths,
            'Frontend content or generated path set differs')
    proof = files['frontend/.release/reproducible-build.json']
    check_record(proof, intent['frontend_reproducibility']['proof_file'], 'reproducibility proof')
    require(json.loads(proof) == {key: value for key, value in intent['frontend_reproducibility'].items()
                                if key != 'proof_file'}, 'Reproducibility proof payload differs')
    return files, modes, {'inner_tar_sha256': checksum[0], 'generated_files': len(files),
        'frontend_content_records': len(content_paths), 'identity_sha256': digest(identity_bytes),
        'identity_bytes': len(identity_bytes), 'build_meta_sha256': digest(meta_bytes),
        'proof_sha256': digest(proof)}


def json_objects(text):
    decoder, position = json.JSONDecoder(), 0
    while (start := text.find('{', position)) >= 0:
        try:
            value, position = decoder.raw_decode(text, start)
        except json.JSONDecodeError:
            position = start + 1
            continue
        if isinstance(value, dict):
            yield value


def evidence(spec, host, intent, files):
    archive = read_zip(spec)
    require(set(archive) == {'mezan-adapter-environment-v5.json', 'mezan-adapter-build-v5.log',
        'mezan-package-boundary-v5.json', 'mezan-governed-runtime.log'}, 'Unexpected evidence ZIP members')
    environment = json.loads(archive['mezan-adapter-environment-v5.json'])
    require(environment['host_node'] == 'v' + host and environment['host_yarn'] == '1.22.22',
            'Wrong host toolchain evidence')
    for key, expected in {'declared_backend_package_root': 'backend',
            'declared_frontend_package_root': 'frontend', 'declared_frontend_static_root': 'frontend/build',
            'configured_package_build_command': 'cd frontend && yarn build',
            'governed_install_command': 'governed-toolchain exec -- yarn --cwd frontend install --frozen-lockfile --non-interactive',
            'governed_build_command': 'governed-toolchain exec -- yarn --cwd frontend build:release'}.items():
        require(environment[key] == expected, 'Adapter environment contract differs: ' + key)
    objects = list(json_objects(archive['mezan-adapter-build-v5.log'].decode('utf-8')))
    toolchains = [value for value in objects if 'cache_state' in value]
    require(any(value.get('node_version') == 'v22.23.2' and value.get('yarn_version') == '1.22.22'
                for value in toolchains), 'No actual governed toolchain evidence')
    require(any(value.get('frontend_build') == intent['frontend_build']
                and value.get('frontend_reproducibility') == intent['frontend_reproducibility']
                for value in objects), 'Build output lacks the exact frontend/proof verification')
    completed = [value for value in objects if value.get('built') is True]
    require(len(completed) == 1, 'Missing or ambiguous actual adapter completion')
    built = completed[0]
    require(built['source_git_sha'] == SOURCE and built['protocol_version'] == 5
            and built['release_id'] == intent['runtime_identity']['release_id']
            and built['frontend_artifact_tree_sha256'] == intent['frontend_build']['artifact_tree_sha256'],
            'Adapter completion identity differs')
    boundary_bytes = archive['mezan-package-boundary-v5.json']
    boundary = json.loads(boundary_bytes)
    require(boundary == built['package_boundary'], 'Package proof differs from actual build completion')
    require(boundary['source_git_sha'] == SOURCE and boundary['release_id'] == built['release_id'],
            'Package boundary identity differs')
    backend, frontend = boundary['backend'], boundary['frontend']
    require(backend['isolated_verified'] is True and backend['health_verified'] is True
            and backend['git_directory_present'] is False and backend['sibling_frontend_present'] is False
            and backend['runtime_root'] == 'backend', 'Backend package isolation proof failed')
    require(frontend['isolated_runtime_artifact_verified'] is True
            and frontend['content_type_contract'] == 'application/json'
            and frontend['runtime_root'] == 'frontend' and frontend['static_root'] == 'frontend/build'
            and frontend['artifact_tree_sha256'] == intent['frontend_build']['artifact_tree_sha256'],
            'Frontend package proof failed')
    check_record(files['backend/release_identity.json'], backend['identity'], 'package backend identity')
    check_record(files['frontend/build/build-meta.json'], frontend['build_meta'], 'package frontend metadata')
    require(backend['identity']['path'] == 'release_identity.json'
            and frontend['build_meta']['path'] == 'build/build-meta.json', 'Package member path differs')
    for package in (backend, frontend):
        require(type(package['file_count']) is int and package['file_count'] > 0
                and re.fullmatch(r'[0-9a-f]{64}', package['package_tree_sha256']) is not None,
                'Invalid package summary')
    require(bool(archive['mezan-governed-runtime.log']), 'Missing runtime log; CI route receipts are also required')
    return boundary, {'package_boundary_sha256': digest(boundary_bytes),
        'backend_package_files': backend['file_count'], 'frontend_package_files': frontend['file_count'],
        'backend_package_tree_sha256': backend['package_tree_sha256'],
        'frontend_package_tree_sha256': frontend['package_tree_sha256'],
        'governed_node': '22.23.2', 'governed_yarn': '1.22.22', 'actual_adapter_built': True}


def job_evidence(jobs, host, spec, config):
    selected = [job for job in jobs if job.get('id') == spec['job_id']]
    require(len(selected) == 1, 'Missing or duplicate selected CI job')
    job = selected[0]
    require(job['name'] == 'Emergent Host Node ' + host + ' clean-clone adapter rehearsal'
            and job['head_sha'] == config['intent_commit'] and job['run_id'] == config['workflow_run_id'],
            'CI job is not the requested B5 host/run')
    require(job['status'] == 'completed' and job['conclusion'] == 'success', 'Host CI job did not pass')
    receipts = []
    for name in (BUILD_STEP, PACKAGE_STEP, ROUTE_STEP):
        matching = [step for step in job['steps'] if step['name'] == name]
        require(len(matching) == 1, 'Missing or duplicate CI step: ' + name)
        step = matching[0]
        require(step['status'] == 'completed' and step['conclusion'] == 'success'
                and step.get('started_at') and step.get('completed_at'), 'CI step did not actually pass: ' + name)
        receipts.append({key: step[key] for key in ('name', 'number', 'started_at', 'completed_at', 'conclusion')})
    return {'job_id': job['id'], 'run_id': job['run_id'], 'head_sha': job['head_sha'], 'steps': receipts}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--intent', required=True, type=Path)
    parser.add_argument('--inputs', required=True, type=Path)
    args = parser.parse_args()
    intent_bytes = args.intent.read_bytes()
    require(digest(intent_bytes) == INTENT_SHA256, 'This comparator only accepts the reviewed A5 intent bytes')
    intent = json.loads(intent_bytes)
    require(intent['source_git_sha'] == SOURCE and intent['source_base_git_sha'] == BASE
            and intent['protocol_version'] == 5 and intent['schema_version'] == 2, 'Wrong A5 identity binding')
    require(intent['client_environment'] == {'REACT_APP_BACKEND_URL': {
        'present': True, 'value': 'https://mezansalla.com'}}, 'Wrong reviewed client origin')
    config = json.loads(args.inputs.read_bytes())
    require(re.fullmatch(r'[0-9a-f]{40}', config['intent_commit']) is not None
            and config['intent_commit'] != SOURCE, 'Supply the actual B5 SHA')
    require(type(config['workflow_run_id']) is int and config['workflow_run_id'] > 0
            and set(config['hosts']) == set(HOSTS), 'Supply the actual workflow run and both hosts')
    jobs_bytes = Path(config['jobs_json']).read_bytes()
    jobs = json.loads(jobs_bytes)['jobs']
    results, contents, modes, boundaries = {}, {}, {}, {}
    for host in HOSTS:
        spec = config['hosts'][host]
        files, member_modes, generated_summary = generated(spec['generated'], intent_bytes, intent)
        boundary, evidence_summary = evidence(spec['evidence'], host, intent, files)
        results[host] = {**generated_summary, **evidence_summary,
            'generated_zip_sha256': spec['generated']['sha256'], 'evidence_zip_sha256': spec['evidence']['sha256'],
            'ci': job_evidence(jobs, host, spec, config)}
        contents[host], modes[host], boundaries[host] = files, member_modes, boundary
    left, right = HOSTS
    require(contents[left] == contents[right], 'Generated path set or bytes differ between hosts')
    require(modes[left] == modes[right], 'Generated member modes/types differ between hosts')
    require(boundaries[left] == boundaries[right], 'Package boundary summaries differ between hosts')
    print(json.dumps({'accepted': True, 'source': SOURCE, 'base': BASE, 'intent_commit': config['intent_commit'],
        'intent_sha256': INTENT_SHA256, 'runtime_release': intent['runtime_identity']['release_id'],
        'identity_sha256': results[left]['identity_sha256'], 'jobs_json_sha256': digest(jobs_bytes),
        'generated_paths_bytes_modes_identical': True, 'json_route_job_steps_passed': 2, 'hosts': results}, indent=2))


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print(json.dumps({'accepted': False, 'error': str(error)}), file=sys.stderr)
        raise SystemExit(1) from error
