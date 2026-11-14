"""Audited, single-attempt official guard phases for reviewed PR1920. Never publishes."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import time

ROOT = Path('/app')
sys.dont_write_bytecode = True
sys.path.insert(0, str(ROOT))
SOURCE = '2cf4d70d7e0c23c11048cad5c610e8c0c60d7eeb'
INTENT_COMMIT = 'dcb97791d6ef8ad394575542eb4048c6c867bbba'
BASE = '4c623c640fb63ac3c138fa4a9cabb20e2b39dca7'
DEPLOYMENT = '29ec1e07eb7d1a23647cadff5bac26149accfa99'
TREE = '14dfe135d6633f72c92cb1cb359f4b9933ac176a'
RELEASE = 'rg5-5c3980b0f83f3859023070ef846486e877849ca7ca848367a181995ca32c1d5e'
BRANCH = 'hotfix/prod-snap-meta-final'
ACTOR = 'codex-campaign-hover-profit-1920'
ORIGIN = 'https://mezansalla.com'
ORIGINAL = Path('/tmp/mezan-pr1920-original-a_3qop92')
BASELINE_UI_VERSION = 'a5eae68'
PRESERVED_COUNT = 120
GUARD = ['python', '-B', 'scripts/production_release_guard.py']
ENV = dict(os.environ, PYTHONDONTWRITEBYTECODE='1', REACT_APP_BACKEND_URL=ORIGIN)
PHASE_ARGV = {
    'prepare': GUARD + ['prepare', '--actor', ACTOR],
    'prepublish': GUARD + ['prepublish'],
    'verify': GUARD + ['verify', '--url', ORIGIN],
}


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def now():
    return datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')


def timestamp(value):
    point = datetime.fromisoformat(value.replace('Z', '+00:00'))
    require(point.tzinfo is not None and point.utcoffset().total_seconds() == 0, 'Timestamp must be UTC')
    return point


def read_json(path):
    return json.loads(path.read_text(encoding='utf-8'))


def write_json(path, value):
    with path.open('x', encoding='utf-8') as handle:
        handle.write(json.dumps(value, ensure_ascii=False, indent=2) + '\n')


def sha256(path):
    digest = hashlib.sha256()
    with path.open('rb') as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


class Audit:
    def __init__(self, directory):
        self.directory = directory

    def run(self, name, argv):
        record = {'argv': argv, 'cwd': str(ROOT), 'started_at': now(), 'exit_code': None}
        write_json(self.directory / (name + '.started.json'), record)
        started = time.monotonic()
        stdout = self.directory / (name + '.stdout')
        stderr = self.directory / (name + '.stderr')
        try:
            with stdout.open('xb') as out, stderr.open('xb') as err:
                result = subprocess.run(argv, cwd=ROOT, env=ENV, stdout=out, stderr=err)
            record['exit_code'] = result.returncode
        except BaseException as exc:
            record['exception'] = type(exc).__name__ + ': ' + str(exc)
            raise
        finally:
            record.update(finished_at=now(), seconds=time.monotonic() - started,
                          stdout_sha256=sha256(stdout) if stdout.exists() else None,
                          stderr_sha256=sha256(stderr) if stderr.exists() else None)
            write_json(self.directory / (name + '.json'), record)
        require(record['exit_code'] == 0, name + ' failed; inspect recorded actual exit and streams')
        return stdout.read_bytes()

    def json(self, name, argv):
        return json.loads(self.run(name, argv))

    def git(self, name, *argv):
        return self.run(name, ['git', *argv]).decode().strip()


def preserved_files(audit, name):
    paths = audit.run(name, ['git', 'ls-files', '--others', '--exclude-standard', '-z'])
    result = {}
    for raw in paths.split(b'\0'):
        if not raw:
            continue
        filename = os.fsdecode(raw)
        path = ROOT / filename
        metadata = path.lstat()
        if stat.S_ISLNK(metadata.st_mode):
            payload = {'link': os.readlink(path)}
        else:
            require(stat.S_ISREG(metadata.st_mode), 'Unexpected untracked file kind: ' + filename)
            payload = {'bytes': metadata.st_size, 'sha256': sha256(path)}
        result[filename] = dict(payload, mode=metadata.st_mode, inode=metadata.st_ino, mtime_ns=metadata.st_mtime_ns)
    return result


def original_evidence(directory, audit):
    summary = read_json(directory / 'result.json')
    expected = {'source': SOURCE, 'deployment': DEPLOYMENT, 'tree': TREE, 'release_id': RELEASE,
                'adapter_exit': 0, 'package_proof_exit': 0, 'preserved_untracked_files': PRESERVED_COUNT,
                'guard_inactive': True, 'evidence': str(directory)}
    require(summary == expected, 'Original build result does not match this reviewed release')
    names = ['result.json', 'untracked-before.json', 'untracked-after.json']
    for name, argv, cwd in [
        ('06-governed-adapter', ['yarn', 'build'], ROOT / 'frontend'),
        ('07-package-proof', ['python', '-B', 'scripts/emergent_deployment_adapter.py', 'verify-packages'], ROOT),
    ]:
        receipt = read_json(directory / (name + '.json'))
        require(type(receipt.get('exit_code')) is int and receipt['exit_code'] == 0,
                'Original actual command did not succeed: ' + name)
        require(receipt.get('argv') == argv and receipt.get('cwd') == str(cwd), 'Wrong original command: ' + name)
        require((directory / (name + '.log')).stat().st_size > 0, 'Missing original command output: ' + name)
        names.extend([name + '.json', name + '.log'])
    for name in ['01b-guard-under-lock', '03-guard-before-fast-forward', '05-guard-before-build', '08-guard-after-build']:
        require(read_json(directory / (name + '.json')) == {'active': False, 'under_operation_lock': True},
                'Original locked guard evidence missing: ' + name)
        names.append(name + '.json')
    baseline = read_json(directory / 'untracked-before.json')
    require(isinstance(baseline, dict) and len(baseline) == PRESERVED_COUNT, 'Wrong preservation baseline count')
    require(read_json(directory / 'untracked-after.json') == baseline, 'Original build changed preserved files')
    write_json(audit.directory / 'original-evidence.json', {name: sha256(directory / name) for name in names})
    return baseline


def workspace(audit, prefix, baseline):
    require(audit.git(prefix + '-branch', 'branch', '--show-current') == BRANCH, 'Wrong branch')
    require(audit.git(prefix + '-head', 'rev-parse', 'HEAD') == DEPLOYMENT, 'Wrong deployment HEAD')
    require(audit.git(prefix + '-origin', 'rev-parse', 'origin/' + BRANCH) == DEPLOYMENT, 'Wrong production tracking ref')
    require(audit.git(prefix + '-tree', 'rev-parse', 'HEAD^{tree}') == TREE, 'Wrong reviewed tree')
    require(audit.git(prefix + '-parents', 'rev-list', '--parents', '-n', '1', DEPLOYMENT).split()[1:] == [BASE, INTENT_COMMIT],
            'Wrong merge parents')
    require(audit.git(prefix + '-intent-parent', 'rev-list', '--parents', '-n', '1', INTENT_COMMIT).split()[1:] == [SOURCE],
            'Wrong intent commit parent')
    require(audit.git(prefix + '-intent-diff', 'diff', '--name-only', SOURCE, DEPLOYMENT) == 'release/release-intent-v5.json',
            'Governed source changed after A')
    require(not audit.git(prefix + '-tracked-status', 'status', '--porcelain', '--untracked-files=no'), 'Tracked worktree is dirty')
    current = preserved_files(audit, prefix + '-untracked-list')
    write_json(audit.directory / (prefix + '-untracked.json'), current)
    require(current == baseline, 'Preservation baseline changed')
    intent_path = ROOT / 'release/release-intent-v5.json'
    identity_path = ROOT / 'backend/release_identity.json'
    intent = read_json(intent_path)
    identity = read_json(identity_path)
    for payload in [intent, identity]:
        for field, expected in {'source_git_sha': SOURCE, 'source_base_git_sha': BASE, 'branch': BRANCH, 'protocol_version': 5}.items():
            require(payload.get(field) == expected, 'Wrong local identity field: ' + field)
    require(identity.get('release_id') == RELEASE and identity == intent.get('runtime_identity'), 'Local runtime identity differs from intent')
    write_json(audit.directory / (prefix + '-identity.json'), {
        'intent_sha256': sha256(intent_path), 'runtime_identity_sha256': sha256(identity_path),
        'source_git_sha': SOURCE, 'source_base_git_sha': BASE, 'deployment_git_sha': DEPLOYMENT, 'release_id': RELEASE,
    })
    return identity


def common_identity(payload):
    for field, expected in {'source_git_sha': SOURCE, 'git_sha': SOURCE, 'deployment_git_sha': DEPLOYMENT,
                            'release_id': RELEASE, 'protocol_version': 5}.items():
        require(payload.get(field) == expected, 'Wrong guard identity field: ' + field)


def own_lease(payload, identity):
    common_identity(payload)
    require(payload.get('actor') == ACTOR and payload.get('branch') == BRANCH, 'Not this actor/branch lease')
    require(payload.get('source_base_git_sha') == BASE and payload.get('runtime_identity') == identity, 'Lease identity differs')
    timestamp(payload['prepared_at'])


def inactive(status):
    require(status.get('active') is False and not status.get('incomplete_lease_directory'), 'Guard is active or incomplete')


def exact_active(status, lease):
    require(status.get('active') is True and {key: value for key, value in status.items() if key != 'active'} == lease,
            'Active lease does not exactly match this prepare receipt')


def receipt(directory, name, argv):
    metadata = read_json(directory / (name + '.json'))
    require(metadata.get('argv') == argv and metadata.get('cwd') == str(ROOT), 'Prior receipt command mismatch')
    require(type(metadata.get('exit_code')) is int and metadata['exit_code'] == 0, 'Prior command did not succeed')
    require(timestamp(metadata['started_at']) <= timestamp(metadata['finished_at']), 'Invalid receipt times')
    for stream in ['stdout', 'stderr']:
        require(sha256(directory / (name + '.' + stream)) == metadata.get(stream + '_sha256'), 'Prior stream checksum mismatch')
    return json.loads((directory / (name + '.stdout')).read_bytes()), metadata


def completed(gate, phase):
    directory = gate / phase
    started = read_json(directory / 'started.json')
    result = read_json(directory / 'result.json')
    require(not (directory / 'failed.json').exists(), 'Prior phase recorded a failure')
    require(started.get('phase') == phase and result.get('phase') == phase and result.get('success') is True,
            'Prior phase has not completed successfully')
    require(result.get('original_evidence') == str(ORIGINAL) and result.get('preserved_untracked_files') == PRESERVED_COUNT,
            'Prior phase used different original evidence')
    common_identity(result)
    payload, metadata = receipt(directory, 'guard', PHASE_ARGV[phase])
    require(timestamp(metadata['finished_at']) <= timestamp(result['finished_at']), 'Invalid prior completion time')
    status, _ = receipt(directory, 'status-after', GUARD + ['status'])
    return result, payload, status


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('phase', choices=tuple(PHASE_ARGV))
    parser.add_argument('original_evidence', type=Path)
    parser.add_argument('--ui-event-text')
    parser.add_argument('--ui-finished-at')
    parser.add_argument('--ui-published-version')
    args = parser.parse_args()
    require(args.original_evidence.resolve(strict=True) == ORIGINAL, 'Explicit original evidence path is not the reviewed run')
    require(args.phase == 'verify' or not any([args.ui_event_text, args.ui_finished_at, args.ui_published_version]),
            'UI event arguments belong only to verify')
    os.umask(0o077)
    gate = ORIGINAL / 'release-gate'
    gate.mkdir(mode=0o700, exist_ok=True)
    directory = gate / args.phase
    directory.mkdir(mode=0o700)  # Exclusive phase claim; never overwrite or retry an existing phase.
    write_json(directory / 'started.json', {
        'phase': args.phase, 'started_at': now(), 'argv': [sys.executable, *sys.argv],
        'cwd': os.getcwd(), 'original_evidence': str(ORIGINAL),
    })
    audit = Audit(directory)
    try:
        baseline = original_evidence(ORIGINAL, audit)
        identity = workspace(audit, 'before', baseline)
        prepared = None
        if args.phase != 'prepare':
            _, prepared, prepare_status = completed(gate, 'prepare')
            own_lease(prepared, identity)
            exact_active(prepare_status, prepared)
        if args.phase == 'verify':
            prior, prepublish, prepublish_status = completed(gate, 'prepublish')
            common_identity(prepublish)
            require(prepublish.get('ready_to_publish') is True, 'No actual successful prepublish receipt')
            exact_active(prepublish_status, prepared)
            require(bool(args.ui_event_text) and args.ui_event_text.startswith('Deployment finished '), 'Actual deployment-finished UI text is required')
            require(bool(args.ui_published_version) and re.fullmatch(r'[A-Za-z0-9_-]{3,128}', args.ui_published_version)
                    and not args.ui_published_version.startswith(BASELINE_UI_VERSION), 'A new current published UI version is required')
            require(bool(args.ui_finished_at), 'Explicit UI completion timestamp is required')
            require(timestamp(prior['finished_at']) < timestamp(args.ui_finished_at) <= timestamp(now()),
                    'UI completion must be newer than completed prepublish and not in the future')
            write_json(directory / 'ui-event.json', {
                'text': args.ui_event_text, 'finished_at': args.ui_finished_at,
                'current_published_version': args.ui_published_version, 'previous_published_version': BASELINE_UI_VERSION,
                'deployment_git_sha': DEPLOYMENT, 'declaration': 'Explicit root observation; official verify supplies runtime proof',
            })
        before_status = audit.json('status-before', GUARD + ['status'])
        if args.phase == 'prepare':
            inactive(before_status)
        else:
            exact_active(before_status, prepared)
        # The official CLI acquires its own operation mutex. Never hold that mutex here.
        try:
            raw = audit.run('guard', PHASE_ARGV[args.phase])
        finally:
            after_identity = workspace(audit, 'after', baseline)
            after_status = audit.json('status-after', GUARD + ['status'])
        require(after_identity == identity, 'Runtime identity changed during the guard phase')
        payload = json.loads(raw)
        common_identity(payload)
        if args.phase == 'prepare':
            own_lease(payload, identity)
            exact_active(after_status, payload)
        elif args.phase == 'prepublish':
            require(payload.get('ready_to_publish') is True, 'Guard did not authorize prepublish')
            exact_active(after_status, prepared)
        else:
            require(payload.get('verified') is True and payload.get('checks') == 3 and payload.get('url') == ORIGIN,
                    'Guard did not complete the three production checks')
            require(payload.get('source_base_git_sha') == BASE, 'Wrong verified source base')
            for field in ['backend_runtime_source', 'release_control_source']:
                require(payload.get(field) == prepared.get(field), 'Wrong verified source binding: ' + field)
            require(payload.get('frontend_artifact_tree_sha256') == identity['frontend_build']['artifact_tree_sha256'],
                    'Wrong verified frontend artifact')
            inactive(after_status)
        result = {
            'success': True, 'phase': args.phase, 'finished_at': now(), 'original_evidence': str(ORIGINAL),
            'source_git_sha': SOURCE, 'git_sha': SOURCE, 'source_base_git_sha': BASE,
            'intent_git_sha': INTENT_COMMIT, 'deployment_git_sha': DEPLOYMENT, 'tree': TREE,
            'release_id': RELEASE, 'protocol_version': 5, 'actor': ACTOR,
            'preserved_untracked_files': len(baseline), 'status_after_active': after_status['active'],
            'guard_receipt_sha256': sha256(directory / 'guard.json'), 'evidence': str(directory),
        }
        write_json(directory / 'result.json', result)
        print(json.dumps(result, indent=2), flush=True)
    except BaseException as exc:
        write_json(directory / 'failed.json', {'phase': args.phase, 'finished_at': now(),
                                             'exception': type(exc).__name__ + ': ' + str(exc)})
        raise


if __name__ == '__main__':
    main()
