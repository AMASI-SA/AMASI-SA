"""Pinned PR1924 R2 continuation preserving the original build refusal and the R1 CLI precheck failure."""
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import tempfile
import time

ROOT = Path('/app')
SOURCE = 'c484b549e8bca017627ce80749c7895198f04bf8'
BASE = '94f6dfb8c1f46bcb7d2dcba6ca0feeea628dc894'
INTENT_COMMIT = '0a93847af8eb3898ae195c58ad88092388d908ad'
DEPLOYMENT = '77e4acf33e8f5432d97bda5d7208983f0fadc599'
TREE = 'b48a0eefb368ba655c8370f6b2e4435e07612790'
RELEASE = 'rg5-c669a417474960a96386ef07047109a8e8cf1bef187873008b650bf00ac23e33'
INTENT_SHA256 = 'fb1ce72783899a9c64b592ce7d39a2c2e73bae6bec500a58f857c7fed132a68d'
IDENTITY_SHA256 = 'd71fc8e889e8d35f09d4b5ec090b5e850422c43be5545444cdc54da3ad910313'
PINS_REVIEWED = True
BRANCH = 'hotfix/prod-snap-meta-final'
ORIGIN = 'https://mezansalla.com'
BOOKKEEPING = '.emergent/stripped_env_names'
PRESERVED_COUNT = 120
GUARD = ['python', '-B', 'scripts/production_release_guard.py']
ENV = dict(os.environ, PYTHONDONTWRITEBYTECODE='1', REACT_APP_BACKEND_URL=ORIGIN)
sys.dont_write_bytecode = True


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def require_pins():
    require(PINS_REVIEWED is True, 'Unpinned template: final identity and runner bytes require independent review')
    for value in (SOURCE, BASE, INTENT_COMMIT, DEPLOYMENT, TREE):
        require(re.fullmatch(r'[0-9a-f]{40}', value) is not None, 'Unpinned commit or tree')
    for value in (INTENT_SHA256, IDENTITY_SHA256):
        require(re.fullmatch(r'[0-9a-f]{64}', value) is not None, 'Unpinned artifact checksum')
    require(re.fullmatch(r'rg5-[0-9a-f]{64}', RELEASE) is not None, 'Unpinned release ID')


def now():
    return datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')


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

    def run(self, name, argv, cwd=ROOT):
        record = {'argv': argv, 'cwd': str(cwd), 'started_at': now(), 'exit_code': None}
        write_json(self.directory / (name + '.started.json'), record)
        started = time.monotonic()
        stdout = self.directory / (name + '.stdout')
        stderr = self.directory / (name + '.stderr')
        try:
            with stdout.open('xb') as out, stderr.open('xb') as err:
                result = subprocess.run(argv, cwd=cwd, env=ENV, stdout=out, stderr=err)
            record['exit_code'] = result.returncode
        except BaseException as exc:
            record['exception'] = type(exc).__name__ + ': ' + str(exc)
            raise
        finally:
            record.update(finished_at=now(), seconds=time.monotonic() - started,
                          stdout_sha256=sha256(stdout) if stdout.exists() else None,
                          stderr_sha256=sha256(stderr) if stderr.exists() else None)
            write_json(self.directory / (name + '.json'), record)
        require(record['exit_code'] == 0, name + ' failed; inspect recorded actual exit and private streams')
        return stdout.read_bytes()

    def json(self, name, argv):
        return json.loads(self.run(name, argv))

    def git(self, name, *argv):
        return self.run(name, ['git', *argv]).decode().strip()


def prefix_sha256(handle, count):
    handle.seek(0)
    remaining = count
    digest = hashlib.sha256()
    while remaining:
        chunk = handle.read(min(remaining, 1024 * 1024))
        require(bool(chunk), 'Bookkeeping file was truncated while reading a witnessed prefix')
        digest.update(chunk)
        remaining -= len(chunk)
    return digest.hexdigest()


def append_snapshot(baseline=None, witnesses=()):
    """Hash only; preserve the fresh baseline and every witnessed complete prefix."""
    required = ([('original-build', baseline)] if baseline is not None else []) + list(witnesses)
    required = [(label, record['bytes'], record['sha256']) for label, record in required]
    path = ROOT / BOOKKEEPING
    witnessed = []
    original_metadata = baseline
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    with os.fdopen(descriptor, 'rb') as handle:
        for attempt in range(1, 4):
            first = os.fstat(handle.fileno())
            require(stat.S_ISREG(first.st_mode), 'Bookkeeping must remain a regular file')
            if original_metadata is None:
                original_metadata = {'mode': first.st_mode, 'inode': first.st_ino}
            require(first.st_mode == original_metadata['mode'] and first.st_ino == original_metadata['inode'],
                    'Bookkeeping file mode or inode changed')
            require(first.st_size >= max((count for _, count, _ in required), default=0),
                    'Bookkeeping file was truncated')
            full_hash = prefix_sha256(handle, first.st_size)
            proofs = []
            for label, count, expected in required:
                observed = prefix_sha256(handle, count)
                require(observed == expected, 'Bookkeeping witnessed prefix changed: ' + label)
                proofs.append({'label': label, 'bytes': count, 'sha256': observed})
            require(prefix_sha256(handle, first.st_size) == full_hash, 'Bookkeeping bytes changed during capture')
            last = os.fstat(handle.fileno())
            current_path = path.lstat()
            require((last.st_dev, last.st_ino, last.st_mode) == (first.st_dev, first.st_ino, first.st_mode)
                    and (current_path.st_dev, current_path.st_ino, current_path.st_mode)
                    == (first.st_dev, first.st_ino, first.st_mode), 'Bookkeeping path was replaced')
            require(last.st_size >= first.st_size and current_path.st_size >= last.st_size,
                    'Bookkeeping file shrank during capture')
            # mtime is a capture-stability signal, not a cross-phase equality gate
            # for this one append-only file. All other records remain exact.
            if (first.st_size, first.st_mtime_ns) == (last.st_size, last.st_mtime_ns) == (current_path.st_size, current_path.st_mtime_ns):
                return ({'bytes': first.st_size, 'sha256': full_hash, 'mode': first.st_mode,
                         'inode': first.st_ino, 'mtime_ns': first.st_mtime_ns},
                        {'file': BOOKKEEPING, 'capture_attempts': attempt, 'required_prefixes': proofs,
                         'intermediate_witnessed_prefixes': witnessed,
                         'attribution': 'Unknown; no writer or publication attribution asserted'})
            required.append(('capture-' + str(attempt), first.st_size, full_hash))
            witnessed.append({'bytes': first.st_size, 'sha256': full_hash})
    raise RuntimeError('Bookkeeping file did not yield a stable bounded snapshot')


def validate_preserved_record(current, baseline):
    require(isinstance(current, dict) and len(current) == PRESERVED_COUNT and set(current) == set(baseline),
            'Untracked paths added or removed')
    require(all(current[name] == baseline[name] for name in baseline if name != BOOKKEEPING),
            'An unapproved preserved file changed')
    record, original = current[BOOKKEEPING], baseline[BOOKKEEPING]
    require(stat.S_ISREG(record['mode']) and record['mode'] == original['mode']
            and record['inode'] == original['inode'], 'Bookkeeping kind, mode or inode differs')
    require(type(record.get('bytes')) is int and record['bytes'] >= original['bytes']
            and re.fullmatch(r'[0-9a-f]{64}', record.get('sha256', '')) is not None,
            'Invalid bookkeeping snapshot record')


def preserved_snapshot(audit, prefix, baseline=None, witnesses=()):
    raw = audit.run(prefix + '-untracked-list', ['git', 'ls-files', '--others', '--exclude-standard', '-z'])
    names = [os.fsdecode(item) for item in raw.split(b'\0') if item]
    require(len(names) == PRESERVED_COUNT and len(set(names)) == PRESERVED_COUNT and BOOKKEEPING in names,
            'Wrong untracked preservation path set or count')
    if baseline is not None:
        require(set(names) == set(baseline), 'Untracked paths added or removed')
    current = {}
    for name in names:
        if name == BOOKKEEPING:
            continue
        path = ROOT / name
        metadata = path.lstat()
        if stat.S_ISLNK(metadata.st_mode):
            payload = {'link': os.readlink(path)}
        else:
            require(stat.S_ISREG(metadata.st_mode), 'Unexpected untracked file kind: ' + name)
            payload = {'bytes': metadata.st_size, 'sha256': sha256(path)}
        current[name] = dict(payload, mode=metadata.st_mode, inode=metadata.st_ino, mtime_ns=metadata.st_mtime_ns)
    record, proof = append_snapshot(baseline[BOOKKEEPING] if baseline is not None else None, witnesses)
    current[BOOKKEEPING] = record
    write_json(audit.directory / (prefix + '-untracked.json'), current)
    write_json(audit.directory / (prefix + '-bookkeeping-prefix-proof.json'), proof)
    if baseline is not None:
        validate_preserved_record(current, baseline)
    return current


def preservation_hashes(directory, prefixes):
    return {prefix + suffix: sha256(directory / (prefix + suffix))
            for prefix in prefixes for suffix in ('-untracked.json', '-bookkeeping-prefix-proof.json')}

FAILED_PROVENANCE = {'absent_after_failure': ['07-package-proof.started.json',
                          '07-package-proof.json',
                          '08-guard-after-build.json',
                          'result.json'],
 'cache_directory': {'dev': 66330,
                     'inode': 138593,
                     'mode': 16877,
                     'mtime_ns': '1794676010955506489',
                     'path': '/app/backend/__pycache__'},
 'cache_files': [{'bytes': 39622,
                  'inode': 138597,
                  'mode': 33188,
                  'mtime_ns': '1794676010944506489',
                  'name': 'frontend_build_identity.cpython-311.pyc',
                  'sha256': '489ba93d9df88a1e4f9a7e703f5ab838f93cf60e5f6f60fe10b7eee45361b67b'},
                 {'bytes': 80941,
                  'inode': 138598,
                  'mode': 33188,
                  'mtime_ns': '1794676010955506489',
                  'name': 'release_protocol_v5.cpython-311.pyc',
                  'sha256': 'c1c09df55f54c1692c24af99f4001907d6717ff06f72b95c21ec58209962143d'}],
 'command': {'argv': ['yarn', 'build'],
             'cwd': '/app/frontend',
             'exit_code': 1,
             'finished_at': '2026-11-14T17:24:05.726777Z',
             'seconds': 1.6254009220283478,
             'started_at': '2026-11-14T17:24:04.101246Z',
             'stderr_sha256': '69b3ab66053b42d01223b2ca5f87ccb8c74b8454f952fecfcca8efb5fd4ae470',
             'stdout_sha256': '39e8c99b49f04709f92dd3dab437d25686d350f99bcbfbabb95451a36129ee5b'},
 'evidence': '/tmp/mezan-pr1924-original-a4ssl2aq',
 'evidence_hashes': {'04-fast-forward.json': 'd4b5fae6991a8d91c40d86a1c02abd924d11bd50b9041d91ae63e577c6e90cdb',
                     '04-fast-forward.started.json': 'fb7558076db30fed26dfcc259b55168a09173595cb0262aebd944751ce6db596',
                     '04-fast-forward.stderr': 'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855',
                     '04-fast-forward.stdout': 'f4705d7a5c26fb1df234d827ac1cf9fda09564fadcc228d8a1e380877ba12322',
                     '06-governed-adapter.json': '066d8d48957828b8b60cdfe95544fdbf5e099b6e204bd88dfaa56ebca862629b',
                     '06-governed-adapter.stderr': '69b3ab66053b42d01223b2ca5f87ccb8c74b8454f952fecfcca8efb5fd4ae470',
                     '06-governed-adapter.stdout': '39e8c99b49f04709f92dd3dab437d25686d350f99bcbfbabb95451a36129ee5b',
                     'after-fast-forward-bookkeeping-prefix-proof.json': '2ed7a572cd88f987c2f7e4ba79478d95c315e5ea7291ee75d7ff1751382035e4',
                     'after-fast-forward-untracked.json': 'afa2bb953b571c446703ef1fdd4cb91d811068a09879800a6a5b72fc7f6c51bf',
                     'before-bookkeeping-prefix-proof.json': '76be36839561a0aff35b7d109bc7881afd6b5d2bda12cd34162bc95de8f7fc7c',
                     'before-untracked.json': 'afa2bb953b571c446703ef1fdd4cb91d811068a09879800a6a5b72fc7f6c51bf',
                     'failed.json': '44ccdc9f459a76cadc40d9afdbdfe4bdf02d99e8146ecdfb83d199b461756ce1',
                     'started.json': '0696f9d594578cebd45c4b7e649906c8aaad0590d4cd8ef3fe5046453d86eb70'},
 'failure': 'EMERGENT_DEPLOYMENT_REFUSED: release source manifests are invalid: Backend runtime source contains '
            'prepackaged bytecode: __pycache__',
 'fast_forward_receipt': {'argv': ['git', 'merge', '--ff-only', '77e4acf33e8f5432d97bda5d7208983f0fadc599'],
                          'cwd': '/app',
                          'exit_code': 0,
                          'finished_at': '2026-11-14T17:24:04.066196Z',
                          'seconds': 0.040198670991230756,
                          'started_at': '2026-11-14T17:24:04.025967Z',
                          'stderr_sha256': 'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855',
                          'stdout_sha256': 'f4705d7a5c26fb1df234d827ac1cf9fda09564fadcc228d8a1e380877ba12322'},
 'git_directory': {'dev': 66330,
                   'git_command_output': '/app/.git',
                   'inode': 131136,
                   'mode': 16877,
                   'path': '/app/.git',
                   'resolved': '/app/.git'},
 'guard_inactive_at': '2026-11-14T17:27:04.183387Z',
 'head': '77e4acf33e8f5432d97bda5d7208983f0fadc599',
 'runner': '/tmp/mezan-pr1924-runners-r7me6cho/original-build-1924.py',
 'runner_sha256': '7bef5ecff3a1c2b717c92633d135f94b1d8f44981498f864dbbb1be7405386d9',
 'started_at': '2026-11-14T17:24:03.082090Z',
 'tmp_device': 2097393,
 'tracked_status': ''}


def timestamp(value):
    point = datetime.fromisoformat(value.replace('Z', '+00:00'))
    require(point.tzinfo is not None and point.utcoffset().total_seconds() == 0, 'Timestamp must be UTC')
    return point


def evidence_inventory(directory):
    require(directory.is_dir() and not directory.is_symlink() and directory.resolve(strict=True) == directory,
            'Evidence directory is redirected')
    result = {}
    for path in sorted(directory.iterdir()):
        metadata = path.lstat()
        require(stat.S_ISREG(metadata.st_mode), 'Original evidence must remain flat regular files')
        result[path.name] = {'bytes': metadata.st_size, 'sha256': sha256(path), 'mode': metadata.st_mode,
                             'inode': metadata.st_ino, 'mtime_ns': metadata.st_mtime_ns}
    return result


PRECHECK_PROVENANCE = {'absent': ['quarantine-started.json',
            'quarantine.json',
            '06-governed-adapter.started.json',
            '07-package-proof.started.json',
            'result.json'],
 'cache_source_exists': True,
 'command': {'argv': ['git',
                      'check-ignore',
                      '-z',
                      '--',
                      'backend/__pycache__/frontend_build_identity.cpython-311.pyc',
                      'backend/__pycache__/release_protocol_v5.cpython-311.pyc'],
             'cwd': '/app',
             'exit_code': 128,
             'finished_at': '2026-11-14T17:40:26.480789Z',
             'seconds': 0.0031368930358439684,
             'started_at': '2026-11-14T17:40:26.477587Z',
             'stderr_sha256': '88a0ee28adbccb290edd11de80a0accf81781ed38f603946697dbdd6ca532bb2',
             'stdout_sha256': 'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855'},
 'evidence': '/tmp/mezan-pr1924-original-recovery-wj_jnfqk',
 'exit_code': 128,
 'finished_at': '2026-11-14T17:40:26.481163Z',
 'full_before_snapshot_identical_to_original_baseline': True,
 'guard_inactive_under_lock': True,
 'hashes': {'09-recovery-guard-on-exit.json': '270c941b71cef2ca1197cf65e191b5a53dedb3e4569bbc653b23277dfd0bc38a',
            'before-bookkeeping-prefix-proof.json': '080189dbb1d2bd3c4a171bc7f100c785045a6e31861f799317ba708613acf15e',
            'before-untracked.json': 'afa2bb953b571c446703ef1fdd4cb91d811068a09879800a6a5b72fc7f6c51bf',
            'cache-ignored-files.json': 'a58cee215d7e0073b7de301d35d6eef23d735c1024e91cbfbd545eef90459083',
            'cache-ignored-files.stderr': '88a0ee28adbccb290edd11de80a0accf81781ed38f603946697dbdd6ca532bb2',
            'failed.json': '960e15c04f30f59f8aaec74df65de3708f6314248b344e439567fbde58e0d92a',
            'started.json': 'a87116dbbd6ae504d4a3545a1da74db44d560dbfb099868ddc298d37f5c796bb'},
 'started_at': '2026-11-14T17:40:25.783820Z',
 'stderr': 'fatal: -z only makes sense with --stdin\n'}


def prior_failure():
    """Validate the actual failed original; never write into its directory."""
    directory = Path(FAILED_PROVENANCE['evidence'])
    inventory = evidence_inventory(directory)
    for name, digest in FAILED_PROVENANCE['evidence_hashes'].items():
        require(inventory.get(name, {}).get('sha256') == digest, 'Pinned original evidence differs: ' + name)
    for name in FAILED_PROVENANCE['absent_after_failure']:
        require(name not in inventory, 'Original run progressed beyond the reviewed failure: ' + name)
    require(not any(name.startswith('07-package-proof.') or name.startswith('08-guard-after-build.')
                    for name in inventory), 'Original package proof or final guard was attempted')
    started = read_json(directory / 'started.json')
    require(started.get('started_at') == FAILED_PROVENANCE['started_at']
            and started.get('source') == SOURCE and started.get('deployment') == DEPLOYMENT,
            'Original run identity differs')
    for name, expected in (('04-fast-forward', FAILED_PROVENANCE['fast_forward_receipt']),
                           ('06-governed-adapter', FAILED_PROVENANCE['command'])):
        actual = read_json(directory / (name + '.json'))
        require(actual == expected, 'Original actual command receipt differs: ' + name)
        require(timestamp(actual['started_at']) <= timestamp(actual['finished_at']), 'Invalid original command times')
        for stream in ('stdout', 'stderr'):
            require(inventory[name + '.' + stream]['sha256'] == actual[stream + '_sha256'], 'Original command stream differs')
    require(FAILED_PROVENANCE['fast_forward_receipt']['exit_code'] == 0
            and FAILED_PROVENANCE['command']['exit_code'] == 1, 'Wrong original phase outcomes')
    combined = (directory / '06-governed-adapter.stdout').read_bytes() + (directory / '06-governed-adapter.stderr').read_bytes()
    require(FAILED_PROVENANCE['failure'].encode() in combined, 'Reviewed refusal is absent from original logs')
    for name in ('01b-guard-under-lock', '03-guard-before-fast-forward', '05-guard-before-build'):
        require(read_json(directory / (name + '.json')) == {'active': False, 'under_operation_lock': True},
                'Original inactive lock checkpoint differs: ' + name)
    baseline = read_json(directory / 'before-untracked.json')
    require(isinstance(baseline, dict) and len(baseline) == PRESERVED_COUNT and BOOKKEEPING in baseline,
            'Wrong original preservation baseline')
    forwarded = read_json(directory / 'after-fast-forward-untracked.json')
    validate_preserved_record(forwarded, baseline)
    proof = read_json(directory / 'after-fast-forward-bookkeeping-prefix-proof.json')
    original = baseline[BOOKKEEPING]
    require({'label': 'original-build', 'bytes': original['bytes'], 'sha256': original['sha256']}
            in proof.get('required_prefixes', []), 'Original fast-forward lost its baseline prefix')
    precheck_directory = Path(PRECHECK_PROVENANCE['evidence'])
    precheck_inventory = evidence_inventory(precheck_directory)
    for name, digest in PRECHECK_PROVENANCE['hashes'].items():
        require(precheck_inventory.get(name, {}).get('sha256') == digest, 'Pinned R1 precheck evidence differs: ' + name)
    for name in PRECHECK_PROVENANCE['absent']:
        require(name not in precheck_inventory, 'R1 progressed beyond the reviewed precheck failure: ' + name)
    require(not any(name.startswith(('06-governed-adapter.', '07-package-proof.')) for name in precheck_inventory),
            'R1 attempted another build or package proof')
    precheck_started = read_json(precheck_directory / 'started.json')
    require(precheck_started.get('started_at') == PRECHECK_PROVENANCE['started_at']
            and precheck_started.get('source') == SOURCE and precheck_started.get('deployment') == DEPLOYMENT
            and precheck_started.get('failed_original_evidence') == FAILED_PROVENANCE['evidence'],
            'R1 precheck attempt identity differs')
    actual_precheck = read_json(precheck_directory / 'cache-ignored-files.json')
    require(actual_precheck == PRECHECK_PROVENANCE['command'] and type(actual_precheck.get('exit_code')) is int
            and actual_precheck['exit_code'] == 128, 'R1 actual precheck receipt differs')
    require(timestamp(precheck_started['started_at']) <= timestamp(actual_precheck['started_at'])
            <= timestamp(actual_precheck['finished_at']) <= timestamp(PRECHECK_PROVENANCE['finished_at']),
            'Invalid R1 precheck times')
    for stream in ('stdout', 'stderr'):
        require(precheck_inventory['cache-ignored-files.' + stream]['sha256'] == actual_precheck[stream + '_sha256'],
                'R1 precheck stream differs')
    require((precheck_directory / 'cache-ignored-files.stderr').read_bytes() == PRECHECK_PROVENANCE['stderr'].encode(),
            'R1 did not record the reviewed CLI syntax failure')
    require(read_json(precheck_directory / '09-recovery-guard-on-exit.json')
            == {'active': False, 'under_operation_lock': True}, 'R1 did not retain its inactive guard checkpoint')
    require(precheck_inventory['before-untracked.json']['sha256']
            == FAILED_PROVENANCE['evidence_hashes']['before-untracked.json']
            and read_json(precheck_directory / 'before-untracked.json') == baseline,
            'R1 preservation snapshot differs from the original baseline')
    combined_inventory = {'original': inventory, 'failed_precheck': precheck_inventory}
    return baseline, [('failed-original-after-fast-forward', forwarded[BOOKKEEPING])], combined_inventory


def cache_snapshot(directory):
    """Inspect only the pinned directory and its two known regular bytecode files."""
    expected_directory = FAILED_PROVENANCE['cache_directory']
    expected_files = {row['name']: row for row in FAILED_PROVENANCE['cache_files']}
    first = directory.lstat()
    require(stat.S_ISDIR(first.st_mode) and not directory.is_symlink(), 'Cache directory is not a real directory')
    metadata = {'mode': first.st_mode, 'inode': first.st_ino, 'dev': first.st_dev, 'mtime_ns': str(first.st_mtime_ns)}
    require(metadata == {key: expected_directory[key] for key in metadata}, 'Pinned cache directory metadata differs')
    require({path.name for path in directory.iterdir()} == set(expected_files), 'Cache directory has unexpected members')
    observed = []
    for name in sorted(expected_files):
        path = directory / name
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
        with os.fdopen(descriptor, 'rb') as handle:
            before = os.fstat(handle.fileno())
            require(stat.S_ISREG(before.st_mode) and before.st_dev == first.st_dev, 'Cache member kind or device differs')
            digest = hashlib.sha256()
            for chunk in iter(lambda: handle.read(1024 * 1024), b''):
                digest.update(chunk)
            after = os.fstat(handle.fileno())
            current = path.lstat()
            record = {'name': name, 'bytes': before.st_size, 'mode': before.st_mode, 'inode': before.st_ino,
                      'mtime_ns': str(before.st_mtime_ns), 'sha256': digest.hexdigest()}
            require(record == expected_files[name], 'Pinned cache member differs: ' + name)
            stable = lambda item: (item.st_dev, item.st_ino, item.st_mode, item.st_size, item.st_mtime_ns)
            require(stable(before) == stable(after) == stable(current), 'Cache member changed during inspection')
            observed.append(record)
    last = directory.lstat()
    require((first.st_dev, first.st_ino, first.st_mode, first.st_mtime_ns)
            == (last.st_dev, last.st_ino, last.st_mode, last.st_mtime_ns)
            and {path.name for path in directory.iterdir()} == set(expected_files), 'Cache directory changed during inspection')
    return {'directory': metadata, 'files': observed}


def verified_git_directory(audit):
    expected = FAILED_PROVENANCE['git_directory']
    gitdir = ROOT / '.git'
    require(audit.git('quarantine-git-directory', 'rev-parse', '--absolute-git-dir') == expected['git_command_output'] == str(gitdir),
            'Unexpected Git directory')
    metadata = gitdir.lstat()
    require(stat.S_ISDIR(metadata.st_mode) and not gitdir.is_symlink()
            and str(gitdir.resolve(strict=True)) == expected['resolved'] == str(gitdir), 'Git directory is redirected')
    require((metadata.st_mode, metadata.st_ino, metadata.st_dev) == (expected['mode'], expected['inode'], expected['dev']),
            'Git directory identity differs')
    return gitdir


def quarantine_cache(audit):
    source = Path(FAILED_PROVENANCE['cache_directory']['path'])
    require(source == ROOT / 'backend/__pycache__' and source.parent.resolve(strict=True) == source.parent,
            'Cache path is redirected')
    before = cache_snapshot(source)
    require(not audit.git('cache-tracked-files', 'ls-files', '--', 'backend/__pycache__'), 'Cache contains tracked content')
    paths = ['backend/__pycache__/' + row['name'] for row in FAILED_PROVENANCE['cache_files']]
    ignored = audit.run('cache-ignored-files', ['git', 'check-ignore', '--', *paths])
    require(os.fsdecode(ignored).splitlines() == paths, 'Cache files are not all ignored in the expected order')
    gitdir = verified_git_directory(audit)
    require(gitdir.lstat().st_dev == before['directory']['dev'], 'Atomic quarantine requires the same filesystem')
    require(cache_snapshot(source) == before, 'Cache changed before quarantine')
    private = Path(tempfile.mkdtemp(prefix='mezan-pr1924-bytecode-quarantine-', dir=gitdir))
    destination = private / 'backend-__pycache__'
    require(not destination.exists() and not destination.is_symlink(), 'Quarantine target already exists')
    record = {'operation': 'os.rename', 'source': str(source), 'destination': str(destination),
              'started_at': now(), 'same_filesystem': True, 'before': before}
    write_json(audit.directory / 'quarantine-started.json', record)
    os.rename(source, destination)  # One atomic rename; no overwrite, copy/delete fallback or cleanup.
    record.update(finished_at=now(), after=cache_snapshot(destination), source_absent=not source.exists() and not source.is_symlink())
    require(record['source_absent'] and record['after'] == before, 'Quarantine did not preserve the entire directory')
    write_json(audit.directory / 'quarantine.json', record)
    return record


def validate_quarantine(record):
    require(record.get('operation') == 'os.rename' and record.get('same_filesystem') is True
            and record.get('source_absent') is True, 'Wrong quarantine operation receipt')
    require(record.get('source') == FAILED_PROVENANCE['cache_directory']['path'], 'Wrong quarantine source')
    destination = Path(record['destination'])
    require(destination.name == 'backend-__pycache__' and destination.parent.parent == ROOT / '.git'
            and destination.parent.name.startswith('mezan-pr1924-bytecode-quarantine-')
            and destination.resolve(strict=True) == destination, 'Quarantine target is redirected')
    require(timestamp(record['started_at']) <= timestamp(record['finished_at']), 'Invalid quarantine times')
    require(record['before'] == record['after'] == cache_snapshot(destination), 'Retained quarantine content changed')
    # Future derived caches in the source path are governed by the official validators.


def recovery_workspace(audit, prefix):
    require(audit.git(prefix + '-branch', 'branch', '--show-current') == BRANCH, 'Wrong branch')
    require(audit.git(prefix + '-head', 'rev-parse', 'HEAD') == DEPLOYMENT, 'Recovery requires exact current M3')
    require(audit.git(prefix + '-origin', 'rev-parse', 'origin/' + BRANCH) == DEPLOYMENT, 'Wrong production tracking ref')
    require(audit.git(prefix + '-tree', 'rev-parse', 'HEAD^{tree}') == TREE, 'Wrong reviewed tree')
    require(audit.git(prefix + '-parents', 'rev-list', '--parents', '-n', '1', DEPLOYMENT).split()[1:] == [BASE, INTENT_COMMIT], 'Wrong ordered merge parents')
    require(audit.git(prefix + '-intent-parent', 'rev-list', '--parents', '-n', '1', INTENT_COMMIT).split()[1:] == [SOURCE], 'Wrong intent parent')
    require(audit.git(prefix + '-intent-diff', 'diff', '--name-only', SOURCE, DEPLOYMENT) == 'release/release-intent-v5.json', 'Governed source changed after A3')
    require(not audit.git(prefix + '-tracked-status', 'status', '--porcelain', '--untracked-files=no'), 'Tracked worktree is dirty')
    require(sha256(ROOT / 'release/release-intent-v5.json') == INTENT_SHA256, 'Pinned intent bytes differ')


def main():
    require_pins()
    require(len(sys.argv) == 2 and sys.argv[1] == DEPLOYMENT, 'Pass the exact reviewed M3 deployment SHA')
    os.umask(0o077)
    evidence = Path(tempfile.mkdtemp(prefix='mezan-pr1924-original-recovery-', dir='/tmp'))
    audit = Audit(evidence)
    write_json(evidence / 'started.json', {'kind': 'mezan_original_build_continuation_v1', 'started_at': now(),
        'argv': [sys.executable, *sys.argv], 'cwd': os.getcwd(), 'source': SOURCE, 'deployment': DEPLOYMENT,
        'failed_original_evidence': FAILED_PROVENANCE['evidence'],
        'failed_precheck_evidence': PRECHECK_PROVENANCE['evidence']})
    print('EVIDENCE=' + str(evidence), flush=True)
    sys.path.insert(0, str(ROOT))
    from scripts.production_release_guard import _release_operation_lock, LEASE_DIR, LEASE_PATH

    def locked_inactive(name):
        require(not LEASE_PATH.exists() and not LEASE_DIR.exists(), 'Active or incomplete release lease')
        write_json(evidence / (name + '.json'), {'active': False, 'under_operation_lock': True})

    prior_inventory = None
    try:
        baseline, witnesses, prior_inventory = prior_failure()
        write_json(evidence / 'prior-original-before.json', prior_inventory)
        status = audit.json('01-guard-before', GUARD + ['status'])
        require(status.get('active') is False and not status.get('incomplete_lease_directory'), 'Guard is active or incomplete')
        with _release_operation_lock():
            locked_inactive('01b-recovery-guard-under-lock')
            try:
                audit.run('02-recovery-fetch', ['git', 'fetch', 'origin', BRANCH])
                recovery_workspace(audit, 'before')
                before = preserved_snapshot(audit, 'before', baseline, witnesses)
                witnesses.append(('recovery-before', before[BOOKKEEPING]))
                quarantine = quarantine_cache(audit)
                quarantined = preserved_snapshot(audit, 'after-quarantine', baseline, witnesses)
                witnesses.append(('recovery-after-quarantine', quarantined[BOOKKEEPING]))
                locked_inactive('05-recovery-guard-before-build')
                audit.run('06-governed-adapter', ['yarn', 'build'], ROOT / 'frontend')
                audit.run('07-package-proof', ['python', '-B', 'scripts/emergent_deployment_adapter.py', 'verify-packages'])
                recovery_workspace(audit, 'after')
                intent_path, identity_path = ROOT / 'release/release-intent-v5.json', ROOT / 'backend/release_identity.json'
                require(sha256(identity_path) == IDENTITY_SHA256, 'Runtime identity bytes differ from reviewed artifact')
                intent, identity = read_json(intent_path), read_json(identity_path)
                require(identity == intent.get('runtime_identity') and identity.get('release_id') == RELEASE, 'Runtime identity differs from intent')
                for payload in (intent, identity):
                    for field, value in {'source_git_sha': SOURCE, 'source_base_git_sha': BASE, 'branch': BRANCH, 'protocol_version': 5}.items():
                        require(payload.get(field) == value, 'Wrong local identity field: ' + field)
                after = preserved_snapshot(audit, 'after', baseline, witnesses)
                validate_quarantine(quarantine)
                after_prior = prior_failure()[2]
                write_json(evidence / 'prior-original-after.json', after_prior)
                require(after_prior == prior_inventory, 'Original failed evidence changed')
                locked_inactive('08-recovery-guard-after-build')
            finally:
                locked_inactive('09-recovery-guard-on-exit')
        result = {'kind': 'mezan_original_build_continuation_v1', 'source': SOURCE, 'base': BASE,
            'intent_commit': INTENT_COMMIT, 'deployment': DEPLOYMENT, 'tree': TREE, 'release_id': RELEASE,
            'intent_sha256': INTENT_SHA256, 'identity_sha256': IDENTITY_SHA256,
            'failed_original_evidence': FAILED_PROVENANCE['evidence'], 'prior_original_adapter_exit': 1,
            'failed_precheck_evidence': PRECHECK_PROVENANCE['evidence'], 'prior_precheck_exit': 128,
            'initial_head': DEPLOYMENT, 'fast_forward_repeated': False, 'adapter_exit': 0, 'package_proof_exit': 0,
            'preserved_untracked_files': PRESERVED_COUNT, 'exact_original_records': PRESERVED_COUNT - 1,
            'append_only_file': BOOKKEEPING, 'before_bookkeeping_bytes': before[BOOKKEEPING]['bytes'],
            'after_bookkeeping_bytes': after[BOOKKEEPING]['bytes'], 'all_witnessed_prefixes_preserved': True,
            'preservation_sha256': preservation_hashes(evidence, ('before', 'after-quarantine', 'after')),
            'prior_original_inventory_sha256': sha256(evidence / 'prior-original-before.json'),
            'quarantine_receipt_sha256': sha256(evidence / 'quarantine.json'),
            'guard_inactive': True, 'finished_at': now(), 'evidence': str(evidence)}
        write_json(evidence / 'result.json', result)
        print(json.dumps({key: value for key, value in result.items() if key != 'preservation_sha256'}, indent=2), flush=True)
    except BaseException as exc:
        write_json(evidence / 'failed.json', {'kind': 'mezan_original_build_continuation_v1', 'finished_at': now(),
            'exception': type(exc).__name__ + ': ' + str(exc), 'failed_original_evidence': FAILED_PROVENANCE['evidence'],
            'do_not_retry_existing_attempt': True})
        raise


if __name__ == '__main__':
    main()
