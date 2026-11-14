"""Pinned PR1924 R2 guard phases retaining both the original failure and the R1 precheck failure."""
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
SOURCE = 'c484b549e8bca017627ce80749c7895198f04bf8'
BASE = '94f6dfb8c1f46bcb7d2dcba6ca0feeea628dc894'
INTENT_COMMIT = '0a93847af8eb3898ae195c58ad88092388d908ad'
DEPLOYMENT = '77e4acf33e8f5432d97bda5d7208983f0fadc599'
TREE = 'b48a0eefb368ba655c8370f6b2e4435e07612790'
RELEASE = 'rg5-c669a417474960a96386ef07047109a8e8cf1bef187873008b650bf00ac23e33'
INTENT_SHA256 = 'fb1ce72783899a9c64b592ce7d39a2c2e73bae6bec500a58f857c7fed132a68d'
IDENTITY_SHA256 = 'd71fc8e889e8d35f09d4b5ec090b5e850422c43be5545444cdc54da3ad910313'
PINS_REVIEWED = True
ORIGINAL = Path('/tmp/mezan-pr1924-original-recovery-jxtvm4fn')
ACTOR = 'codex-campaign-operating-parity-1924'
BASELINE_UI_VERSION = 'ecb9679'
BRANCH = 'hotfix/prod-snap-meta-final'
ORIGIN = 'https://mezansalla.com'
BOOKKEEPING = '.emergent/stripped_env_names'
PRESERVED_COUNT = 120
GUARD = ['python', '-B', 'scripts/production_release_guard.py']
ENV = dict(os.environ, PYTHONDONTWRITEBYTECODE='1', REACT_APP_BACKEND_URL=ORIGIN)
PHASE_ARGV = {'prepare': GUARD + ['prepare', '--actor', ACTOR],
              'prepublish': GUARD + ['prepublish'],
              'verify': GUARD + ['verify', '--url', ORIGIN]}
sys.dont_write_bytecode = True


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


def timestamp(value):
    point = datetime.fromisoformat(value.replace('Z', '+00:00'))
    require(point.tzinfo is not None and point.utcoffset().total_seconds() == 0, 'Timestamp must be UTC')
    return point


def receipt(directory, name, argv, cwd=ROOT):
    metadata = read_json(directory / (name + '.json'))
    require(metadata.get('argv') == argv and metadata.get('cwd') == str(cwd), 'Prior receipt command mismatch')
    require(type(metadata.get('exit_code')) is int and metadata['exit_code'] == 0, 'Prior command did not succeed')
    require(timestamp(metadata['started_at']) <= timestamp(metadata['finished_at']), 'Invalid receipt times')
    for stream in ('stdout', 'stderr'):
        require(sha256(directory / (name + '.' + stream)) == metadata.get(stream + '_sha256'), 'Prior stream checksum mismatch')
    return (directory / (name + '.stdout')).read_bytes(), metadata


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


def append_evidence(directory, prefixes, baseline, witnesses, expected_hashes):
    require(preservation_hashes(directory, prefixes) == expected_hashes, 'Preservation evidence bytes changed')
    for prefix in prefixes:
        snapshot = read_json(directory / (prefix + '-untracked.json'))
        validate_preserved_record(snapshot, baseline)
        proof = read_json(directory / (prefix + '-bookkeeping-prefix-proof.json'))
        require(proof.get('file') == BOOKKEEPING and proof.get('capture_attempts') in (1, 2, 3), 'Invalid bookkeeping proof')
        expected = [('original-build', baseline[BOOKKEEPING]), *witnesses]
        for label, record in expected:
            require({'label': label, 'bytes': record['bytes'], 'sha256': record['sha256']} in proof['required_prefixes'],
                    'Missing prior witnessed prefix evidence: ' + label)
        require(snapshot[BOOKKEEPING]['bytes'] >= max(record['bytes'] for _, record in expected), 'Bookkeeping evidence shrank')
        label = 'recovery-' + prefix if directory == ORIGINAL else directory.name + '-' + prefix
        witnesses.append((label, snapshot[BOOKKEEPING]))


def original_evidence(directory, audit):
    """Consume one successful continuation plus the unchanged actual failed original."""
    baseline, witnesses, prior_inventory = prior_failure()
    summary, started = read_json(directory / 'result.json'), read_json(directory / 'started.json')
    require(not (directory / 'failed.json').exists(), 'Recovery build recorded a failure')
    expected = {'kind': 'mezan_original_build_continuation_v1', 'source': SOURCE, 'base': BASE,
                'intent_commit': INTENT_COMMIT, 'deployment': DEPLOYMENT, 'tree': TREE, 'release_id': RELEASE,
                'intent_sha256': INTENT_SHA256, 'identity_sha256': IDENTITY_SHA256,
                'failed_original_evidence': FAILED_PROVENANCE['evidence'], 'prior_original_adapter_exit': 1,
                'failed_precheck_evidence': PRECHECK_PROVENANCE['evidence'], 'prior_precheck_exit': 128,
                'initial_head': DEPLOYMENT, 'fast_forward_repeated': False, 'adapter_exit': 0, 'package_proof_exit': 0,
                'preserved_untracked_files': PRESERVED_COUNT, 'exact_original_records': PRESERVED_COUNT - 1,
                'append_only_file': BOOKKEEPING, 'all_witnessed_prefixes_preserved': True,
                'guard_inactive': True, 'evidence': str(directory)}
    require(all(summary.get(key) == value for key, value in expected.items()), 'Recovery result differs from this release')
    require(started.get('kind') == expected['kind'] and started.get('source') == SOURCE
            and started.get('deployment') == DEPLOYMENT
            and started.get('failed_original_evidence') == FAILED_PROVENANCE['evidence']
            and started.get('failed_precheck_evidence') == PRECHECK_PROVENANCE['evidence'], 'Recovery start identity differs')
    require(timestamp(FAILED_PROVENANCE['command']['finished_at']) < timestamp(started['started_at']), 'Recovery predates original failure')
    require(read_json(directory / 'prior-original-before.json') == prior_inventory
            == read_json(directory / 'prior-original-after.json'), 'Original failed evidence inventory changed')
    require(sha256(directory / 'prior-original-before.json') == summary.get('prior_original_inventory_sha256'), 'Original evidence inventory receipt differs')
    require(not any(path.name.startswith('04-fast-forward.') for path in directory.iterdir()),
            'Continuation contains a repeated fast-forward')
    require(sha256(directory / 'quarantine.json') == summary.get('quarantine_receipt_sha256'), 'Quarantine receipt changed')
    quarantine = read_json(directory / 'quarantine.json')
    validate_quarantine(quarantine)
    require(timestamp(started['started_at']) <= timestamp(quarantine['started_at']), 'Quarantine predates recovery claim')
    _, fetched = receipt(directory, '02-recovery-fetch', ['git', 'fetch', 'origin', BRANCH])
    require(timestamp(started['started_at']) <= timestamp(fetched['started_at'])
            <= timestamp(fetched['finished_at']) <= timestamp(quarantine['started_at']),
            'Recovery fetch must finish before quarantine')
    names = ['result.json', 'started.json', 'prior-original-before.json', 'prior-original-after.json',
             'quarantine-started.json', 'quarantine.json']
    names.extend('02-recovery-fetch' + suffix for suffix in ('.started.json', '.json', '.stdout', '.stderr'))
    previous_finish = quarantine['finished_at']
    for name, argv, cwd in (
        ('06-governed-adapter', ['yarn', 'build'], ROOT / 'frontend'),
        ('07-package-proof', ['python', '-B', 'scripts/emergent_deployment_adapter.py', 'verify-packages'], ROOT),
    ):
        _, metadata = receipt(directory, name, argv, cwd)
        require(timestamp(previous_finish) <= timestamp(metadata['started_at'])
                <= timestamp(metadata['finished_at']) <= timestamp(summary['finished_at']), 'Invalid recovery command sequence')
        previous_finish = metadata['finished_at']
        require((directory / (name + '.stdout')).stat().st_size + (directory / (name + '.stderr')).stat().st_size > 0,
                'Missing actual recovery command output: ' + name)
        names.extend(name + suffix for suffix in ('.started.json', '.json', '.stdout', '.stderr'))
    for name in ('01b-recovery-guard-under-lock', '05-recovery-guard-before-build',
                 '08-recovery-guard-after-build', '09-recovery-guard-on-exit'):
        require(read_json(directory / (name + '.json')) == {'active': False, 'under_operation_lock': True},
                'Recovery inactive guard checkpoint missing: ' + name)
        names.append(name + '.json')
    raw, _ = receipt(directory, '01-guard-before', GUARD + ['status'])
    inactive(json.loads(raw))
    names.extend('01-guard-before' + suffix for suffix in ('.started.json', '.json', '.stdout', '.stderr'))
    append_evidence(directory, ('before', 'after-quarantine', 'after'), baseline, witnesses, summary['preservation_sha256'])
    names.extend(summary['preservation_sha256'])
    hashes = {'failed_original': prior_inventory, 'recovery': {name: sha256(directory / name) for name in names}}
    write_json(audit.directory / 'original-evidence.json', hashes)
    return baseline, witnesses, hashes


def workspace(audit, prefix, baseline, witnesses):
    require(audit.git(prefix + '-branch', 'branch', '--show-current') == BRANCH, 'Wrong branch')
    require(audit.git(prefix + '-head', 'rev-parse', 'HEAD') == DEPLOYMENT, 'Wrong deployment HEAD')
    require(audit.git(prefix + '-origin', 'rev-parse', 'origin/' + BRANCH) == DEPLOYMENT, 'Wrong production tracking ref')
    require(audit.git(prefix + '-tree', 'rev-parse', 'HEAD^{tree}') == TREE, 'Wrong reviewed tree')
    require(audit.git(prefix + '-parents', 'rev-list', '--parents', '-n', '1', DEPLOYMENT).split()[1:] == [BASE, INTENT_COMMIT], 'Wrong merge parents')
    require(audit.git(prefix + '-intent-parent', 'rev-list', '--parents', '-n', '1', INTENT_COMMIT).split()[1:] == [SOURCE], 'Wrong intent parent')
    require(audit.git(prefix + '-intent-diff', 'diff', '--name-only', SOURCE, DEPLOYMENT) == 'release/release-intent-v5.json', 'Governed source changed after A3')
    require(not audit.git(prefix + '-tracked-status', 'status', '--porcelain', '--untracked-files=no'), 'Tracked worktree is dirty')
    validate_quarantine(read_json(ORIGINAL / 'quarantine.json'))
    require(prior_failure()[2] == read_json(ORIGINAL / 'prior-original-before.json'),
            'Original failed evidence changed during the guard phase')
    current = preserved_snapshot(audit, prefix, baseline, witnesses)
    intent_path, identity_path = ROOT / 'release/release-intent-v5.json', ROOT / 'backend/release_identity.json'
    require(sha256(intent_path) == INTENT_SHA256 and sha256(identity_path) == IDENTITY_SHA256, 'Intent or runtime identity bytes differ from reviewed pins')
    intent, identity = read_json(intent_path), read_json(identity_path)
    for payload in (intent, identity):
        for field, expected in {'source_git_sha': SOURCE, 'source_base_git_sha': BASE, 'branch': BRANCH, 'protocol_version': 5}.items():
            require(payload.get(field) == expected, 'Wrong local identity field: ' + field)
    require(identity.get('release_id') == RELEASE and identity == intent.get('runtime_identity'), 'Local runtime identity differs from intent')
    write_json(audit.directory / (prefix + '-identity.json'), {
        'intent_sha256': INTENT_SHA256, 'runtime_identity_sha256': IDENTITY_SHA256,
        'source_git_sha': SOURCE, 'source_base_git_sha': BASE, 'deployment_git_sha': DEPLOYMENT, 'release_id': RELEASE})
    return identity, current


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


def completed(gate, phase, baseline, witnesses, original_hashes):
    directory = gate / phase
    started, result = read_json(directory / 'started.json'), read_json(directory / 'result.json')
    require(not (directory / 'failed.json').exists(), 'Prior phase recorded a failure')
    require(started.get('phase') == phase and result.get('phase') == phase and result.get('success') is True,
            'Prior phase has not completed successfully')
    require(result.get('original_evidence') == str(ORIGINAL) and result.get('preserved_untracked_files') == PRESERVED_COUNT,
            'Prior phase used different original evidence')
    common_identity(result)
    require(read_json(directory / 'original-evidence.json') == original_hashes, 'Original evidence changed since prior phase')
    require(sha256(directory / 'guard.json') == result.get('guard_receipt_sha256'), 'Prior guard receipt bytes changed')
    raw, metadata = receipt(directory, 'guard', PHASE_ARGV[phase])
    require(timestamp(metadata['finished_at']) <= timestamp(result['finished_at']), 'Invalid prior completion time')
    status, _ = receipt(directory, 'status-after', GUARD + ['status'])
    append_evidence(directory, ('before', 'after'), baseline, witnesses, result['preservation_sha256'])
    return result, json.loads(raw), json.loads(status)


def main():
    require_pins()  # Fail before /app access, subprocesses or phase claims.
    require(ORIGINAL.is_absolute() and str(ORIGINAL).startswith('/tmp/mezan-pr1924-original-recovery-'), 'Unpinned original evidence directory')
    require('UNPINNED' not in ACTOR and re.fullmatch(r'[A-Za-z0-9_-]{3,128}', ACTOR) is not None, 'Unpinned actor')
    require('UNPINNED' not in BASELINE_UI_VERSION and re.fullmatch(r'[A-Za-z0-9_-]{3,128}', BASELINE_UI_VERSION) is not None, 'Unpinned current UI version')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('phase', choices=tuple(PHASE_ARGV))
    parser.add_argument('original_evidence', type=Path)
    parser.add_argument('--ui-event-text')
    parser.add_argument('--ui-finished-at')
    parser.add_argument('--ui-published-version')
    args = parser.parse_args()
    require(args.original_evidence.resolve(strict=True) == ORIGINAL and ORIGINAL.resolve(strict=True) == ORIGINAL,
            'Explicit original evidence path is not the reviewed run')
    require(args.phase == 'verify' or not any((args.ui_event_text, args.ui_finished_at, args.ui_published_version)),
            'UI event arguments belong only to verify')
    os.umask(0o077)
    sys.path.insert(0, str(ROOT))
    gate = ORIGINAL / 'release-gate'
    gate.mkdir(mode=0o700, exist_ok=True)
    directory = gate / args.phase
    directory.mkdir(mode=0o700)  # Exclusive phase claim; no overwrite or retry.
    write_json(directory / 'started.json', {'phase': args.phase, 'started_at': now(),
        'argv': [sys.executable, *sys.argv], 'cwd': os.getcwd(), 'original_evidence': str(ORIGINAL)})
    audit = Audit(directory)
    try:
        baseline, witnesses, original_hashes = original_evidence(ORIGINAL, audit)
        prepared = prepare_status = prepublish = prepublish_status = prior = None
        if args.phase != 'prepare':
            _, prepared, prepare_status = completed(gate, 'prepare', baseline, witnesses, original_hashes)
        if args.phase == 'verify':
            prior, prepublish, prepublish_status = completed(gate, 'prepublish', baseline, witnesses, original_hashes)
        identity, before = workspace(audit, 'before', baseline, witnesses)
        if prepared is not None:
            own_lease(prepared, identity)
            exact_active(prepare_status, prepared)
        if args.phase == 'verify':
            common_identity(prepublish)
            require(prepublish.get('ready_to_publish') is True, 'No actual successful prepublish receipt')
            exact_active(prepublish_status, prepared)
            require(bool(args.ui_event_text) and args.ui_event_text.startswith('Deployment finished '), 'Actual deployment-finished UI text is required')
            require(bool(args.ui_published_version) and re.fullmatch(r'[A-Za-z0-9_-]{3,128}', args.ui_published_version)
                    and not args.ui_published_version.startswith(BASELINE_UI_VERSION)
                    and not BASELINE_UI_VERSION.startswith(args.ui_published_version), 'A new current published UI version is required')
            require(bool(args.ui_finished_at) and timestamp(prior['finished_at']) < timestamp(args.ui_finished_at) <= timestamp(now()),
                    'Explicit UI completion must be newer than prepublish and not in the future')
            write_json(directory / 'ui-event.json', {'text': args.ui_event_text, 'finished_at': args.ui_finished_at,
                'current_published_version': args.ui_published_version, 'previous_published_version': BASELINE_UI_VERSION,
                'deployment_git_sha': DEPLOYMENT, 'declaration': 'Explicit root observation; official verify supplies runtime proof'})
        before_status = audit.json('status-before', GUARD + ['status'])
        if args.phase == 'prepare':
            inactive(before_status)
        else:
            exact_active(before_status, prepared)

        # Exactly one official command. Its CLI owns the mutex; no outer lock.
        # Audit.run persists the exit and full streams before any postcheck.
        errors = []
        raw = None
        try:
            raw = audit.run('guard', PHASE_ARGV[args.phase])
        except BaseException as exc:
            errors.append({'check': 'official-' + args.phase, 'exception': type(exc).__name__ + ': ' + str(exc)})
        after_status = after_identity = after = payload = None
        try:
            # Always attempt actual post-status, including after an official failure.
            after_status = audit.json('status-after', GUARD + ['status'])
        except BaseException as exc:
            errors.append({'check': 'status-after', 'exception': type(exc).__name__ + ': ' + str(exc)})
        try:
            after_identity, after = workspace(audit, 'after', baseline, [*witnesses, (args.phase + '-before', before[BOOKKEEPING])])
            require(after_identity == identity, 'Runtime identity changed during the guard phase')
        except BaseException as exc:
            errors.append({'check': 'workspace-after', 'exception': type(exc).__name__ + ': ' + str(exc)})
        try:
            require(raw is not None, 'No successful official command receipt')
            payload = json.loads(raw)
            common_identity(payload)
            require(after_status is not None, 'No successful actual post-status')
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
                for field in ('backend_runtime_source', 'release_control_source'):
                    require(payload.get(field) == prepared.get(field), 'Wrong verified source binding: ' + field)
                require(payload.get('frontend_artifact_tree_sha256') == identity['frontend_build']['artifact_tree_sha256'], 'Wrong verified frontend artifact')
                inactive(after_status)
        except BaseException as exc:
            errors.append({'check': 'official-result-and-status', 'exception': type(exc).__name__ + ': ' + str(exc)})
        guard_path = directory / 'guard.json'
        guard_receipt = read_json(guard_path) if guard_path.exists() else None
        write_json(directory / 'outcomes.json', {
            'phase': args.phase, 'official_guard_started': (directory / 'guard.started.json').exists(),
            'actual_guard_exit_code': guard_receipt.get('exit_code') if guard_receipt else None,
            'guard_receipt_sha256': sha256(guard_path) if guard_receipt else None,
            'guard_reported_verified': payload.get('verified') if isinstance(payload, dict) else None,
            'guard_reported_checks': payload.get('checks') if isinstance(payload, dict) else None,
            'status_after_active': after_status.get('active') if isinstance(after_status, dict) else None,
            'postcheck_errors': errors,
            'instruction': 'Never rerun an official phase because a later preservation or receipt check failed'})
        require(not errors, 'Phase checks failed; inspect actual persisted receipts and outcomes; do not retry the phase')
        result = {'success': True, 'phase': args.phase, 'finished_at': now(), 'original_evidence': str(ORIGINAL),
            'source_git_sha': SOURCE, 'git_sha': SOURCE, 'source_base_git_sha': BASE,
            'intent_git_sha': INTENT_COMMIT, 'deployment_git_sha': DEPLOYMENT, 'tree': TREE,
            'release_id': RELEASE, 'protocol_version': 5, 'actor': ACTOR,
            'preserved_untracked_files': PRESERVED_COUNT, 'exact_original_records': PRESERVED_COUNT - 1,
            'append_only_file': BOOKKEEPING, 'before_bookkeeping_bytes': before[BOOKKEEPING]['bytes'],
            'after_bookkeeping_bytes': after[BOOKKEEPING]['bytes'], 'all_witnessed_prefixes_preserved': True,
            'preservation_sha256': preservation_hashes(directory, ('before', 'after')),
            'status_after_active': after_status['active'], 'guard_receipt_sha256': sha256(guard_path),
            'evidence': str(directory)}
        write_json(directory / 'result.json', result)
        print(json.dumps({key: value for key, value in result.items() if key != 'preservation_sha256'}, indent=2), flush=True)
    except BaseException as exc:
        path = directory / 'guard.json'
        recorded = read_json(path) if path.exists() else None
        write_json(directory / 'failed.json', {'phase': args.phase, 'finished_at': now(),
            'exception': type(exc).__name__ + ': ' + str(exc),
            'official_guard_started': (directory / 'guard.started.json').exists(),
            'actual_guard_exit_code': recorded.get('exit_code') if recorded else None,
            'guard_receipt_sha256': sha256(path) if path.exists() else None,
            'do_not_retry_existing_phase': True})
        raise


if __name__ == '__main__':
    main()
