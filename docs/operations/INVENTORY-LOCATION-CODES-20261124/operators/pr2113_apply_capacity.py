#!/usr/bin/env python3
"""Apply only reviewed owned-output/archive and duplicate-object capacity changes.

Arguments: reviewed pack stage, exact pack receipt SHA256, archive-helper path.
Never builds, publishes, fetches, rolls back, repacks, or prunes unreachable objects.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import stat
import sys
import tempfile
import time
import types

sys.dont_write_bytecode = True
ROOT = Path('/app')
GIT = ROOT / '.git'
D = 'cb98e5660380d362e78f937d60abc7139cd1c462'
J = '20fc28f3432a78996f1d70f3356a38bd78b0309e'
BRANCH = 'hotfix/prod-snap-meta-final'
COMMON_PATH = GIT / 'mezan-pr2113-recovery-operator-jyn9frvb/pr2113_recover_d_rehearsal.py'
COMMON_SHA = '4fe5070533e8f6308fc88516d1af80f8fa12f5523e7ecf8a7777317a4182b798'
ARCHIVE_HELPER_SHA = 'ef26f2b6b6583fea369619b9e7bd685adb37521180fcb38d3e6135e21a5ce18a'
PACK_HELPER_SHA = '33351c1bce52e9c9a0463131806505363290a83b9de6df30440893e2f190270b'
ARCHIVE_STAGE = Path('/tmp/mezan-pr2113-capacity-dgydmx2f')
ARCHIVE_RECEIPT_SHA = 'f72a3ed9744a81d17c2aa383d76a17daae8f822858051b0a663cb8d79dd2352f'
BACKUP_STAGE = GIT / 'mezan-pr2113-recover-d-cm4y9sgc'
BACKUP = BACKUP_STAGE / 'backups/frontend--node_modules'
PARTIAL = ROOT / 'frontend/node_modules'
KNOWN_MANIFEST = BACKUP_STAGE / 'frontend--node_modules-manifest.json'
ARCHIVES = (
    ('original-node-modules-backup', BACKUP, 19991544,
     'f82674d9dc48b59ff06ca2e84a3013caf2e34c10fc4d045348fd565075eb7270'),
    ('failed-install-node-modules', PARTIAL, 84395938,
     '31436b8ca8e5c15737e68458b5975904d30b674a652be3a0a21bafb5e8c2ecbc'),
)
H = C = P = None
DURABLE = None


def require(condition, code):
    if not condition:
        raise RuntimeError(code)


def read_regular(path):
    require(path.resolve(strict=True) == path, 'INPUT_PATH_HAS_SYMLINK')
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(descriptor, 'rb') as stream:
        require(stat.S_ISREG(os.fstat(stream.fileno()).st_mode), 'INPUT_NOT_REGULAR')
        return stream.read()


def checked_module(name, path, expected_sha):
    data = read_regular(path)
    require(hashlib.sha256(data).hexdigest() == expected_sha, 'REVIEWED_HELPER_CHANGED')
    module = types.ModuleType(name)
    module.__file__ = str(path)
    sys.modules[name] = module
    exec(compile(data, str(path), 'exec'), module.__dict__)
    return module


def save_receipt():
    H.save_receipt()
    if DURABLE is not None:
        H.private_json(DURABLE / 'receipt.json', H.RECEIPT)


def evidence(name, value):
    P.save(name, value)


def phase(name):
    H.STEP = name
    H.RECEIPT['phase'] = name
    save_receipt()


def space():
    value = os.statvfs(ROOT)
    return {'available_bytes': value.f_bavail * value.f_frsize,
            'free_bytes': value.f_bfree * value.f_frsize,
            'available_inodes': value.f_favail}


def fingerprint(path):
    record = H.file_fingerprint(path)
    require(record['type'] == 'file', 'EXPECTED_REGULAR_FILE')
    return record


def exact_file(path, size, digest):
    record = fingerprint(path)
    require(record['bytes'] == size and record['sha256'] == digest, 'FILE_IDENTITY_CHANGED')
    return record


def exclusive_copy(source, destination, mode=0o600):
    require(not os.path.lexists(destination), 'COPY_DESTINATION_EXISTS')
    before = fingerprint(source)
    source_fd = os.open(source, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode)
        with os.fdopen(descriptor, 'wb') as output, os.fdopen(source_fd, 'rb') as stream:
            source_fd = None
            shutil.copyfileobj(stream, output, length=1024 * 1024)
            os.fchmod(output.fileno(), mode)
            output.flush()
            os.fsync(output.fileno())
    finally:
        if source_fd is not None:
            os.close(source_fd)
    after = fingerprint(source)
    copied = fingerprint(destination)
    require(before == after and copied['bytes'] == before['bytes']
            and copied['sha256'] == before['sha256'] and copied['mode'] == mode,
            'COPY_VERIFICATION_FAILED')
    H.fsync_directory(destination.parent)
    return copied


def assert_state(guard):
    H.assert_no_lease(guard)
    require(P.git('rev-parse', 'HEAD').decode().strip() == D, 'HEAD_CHANGED')
    require(P.git('symbolic-ref', '--short', 'HEAD').decode().strip() == BRANCH, 'BRANCH_CHANGED')
    require(P.git('rev-parse', 'origin/' + BRANCH).decode().strip() == D, 'PRODUCTION_REF_CHANGED')
    require(not P.git('status', '--porcelain=v1', '--untracked-files=no'), 'TRACKED_WORKTREE_DIRTY')


def set_runtime_identity(before):
    if H.RUNTIME_PATH in before['records']:
        info = (ROOT / H.RUNTIME_PATH).lstat()
        require(stat.S_ISREG(info.st_mode), 'RUNTIME_METADATA_TYPE_INVALID')
        H.RUNTIME_IDENTITY = (info.st_dev, info.st_ino, stat.S_IMODE(info.st_mode))


def compare_snapshot(before, after, additions=None, pruned=False):
    additions = additions or {}
    for key in ('head', 'branch', 'tree', 'logical_objects', 'metadata', 'linked_gitfiles', 'refs', 'worktrees'):
        require(before[key] == after[key], 'GIT_INVARIANT_CHANGED_' + key.upper())
    old, current = before['objects'], after['objects']
    loose = set(P.loose_paths(old))
    empty_parents = set()
    if pruned:
        for name, record in old.items():
            if re.fullmatch(r'[0-9a-f]{2}', name) and record['type'] == 'directory':
                children = {entry for entry in old if entry.startswith(name + '/')}
                if children <= loose:
                    empty_parents.add(name)
    for name, record in old.items():
        if pruned and name in loose:
            require(name not in current, 'REVIEWED_LOOSE_DUPLICATE_REMAINS')
        elif pruned and name in empty_parents and name not in current:
            continue
        else:
            require(current.get(name) == record, 'EXISTING_OBJECT_STORE_ENTRY_CHANGED')
    require(set(current) - set(old) == set(additions), 'UNEXPECTED_OBJECT_STORE_ENTRY')
    for name, record in additions.items():
        require(current.get(name) == record, 'INSTALLED_PACK_ENTRY_CHANGED')
    for name, record in before['existing_packs'].items():
        require(after['existing_packs'].get(name) == record, 'PREEXISTING_PACK_CHANGED')
    H.preserve_foreign(before['foreign'])
    return sorted(empty_parents - set(current))


def remove_owned_tree(source, expected, archive, archive_record):
    require(source in {BACKUP, PARTIAL}, 'UNOWNED_REMOVAL_TARGET')
    C.real_directory(source)
    H.reject_generated_mounts(source)
    exact_file(archive, archive_record['bytes'], archive_record['sha256'])
    require(H.generated_manifest(source) == expected, 'SOURCE_CHANGED_BEFORE_REMOVAL')
    require(shutil.rmtree.avoids_symlink_attacks, 'FD_SAFE_RMTREE_REQUIRED')
    parent_fd = os.open(source.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        shutil.rmtree(source.name, dir_fd=parent_fd)
        os.fsync(parent_fd)
    finally:
        os.close(parent_fd)
    require(not os.path.lexists(source), 'OWNED_REMOVAL_INCOMPLETE')


def reviewed_archives():
    receipt_path = ARCHIVE_STAGE / 'receipt.json'
    raw = read_regular(receipt_path)
    require(hashlib.sha256(raw).hexdigest() == ARCHIVE_RECEIPT_SHA, 'ARCHIVE_PASS_RECEIPT_CHANGED')
    receipt = json.loads(raw)
    require(receipt['status'] == 'PASS' and receipt['task'] == 'PR2113_capacity_archive_preparation'
            and receipt['deployment_sha'] == D and receipt['reviewed_helper_sha256'] == COMMON_SHA,
            'ARCHIVE_PREPARATION_NOT_REVIEWED_PASS')
    require(len(receipt['archives']) == 2 and all(row['status'] == 'PASS' for row in receipt['steps']),
            'ARCHIVE_PREPARATION_STEPS_NOT_PASS')
    known = json.loads(read_regular(KNOWN_MANIFEST))
    known_record = receipt['known_backup_manifest']
    exact_file(KNOWN_MANIFEST, known_record['bytes'], known_record['sha256'])
    result = {}
    for label, source, size, digest in ARCHIVES:
        row = next(item for item in receipt['archives'] if item['label'] == label)
        archive = ARCHIVE_STAGE / (label + '.tar.gz')
        require(row['source'] == str(source) and row['archive'] == str(archive)
                and row['status'] == 'PASS' and row['source_manifest_stable'] is True,
                'ARCHIVE_SOURCE_BINDING_INVALID')
        archive_record = exact_file(archive, size, digest)
        require(row['archive_record'] == archive_record, 'ARCHIVE_RECORD_CHANGED')
        manifest_path = ARCHIVE_STAGE / (label + '-before.json')
        manifest = json.loads(read_regular(manifest_path))
        require(manifest == json.loads(read_regular(ARCHIVE_STAGE / (label + '-after.json'))),
                'ARCHIVE_SOURCE_MANIFESTS_DIFFER')
        verified = C.verify_archive(archive, manifest)
        require(verified['verified_members'] == row['verified_members'] == row['source_entries'],
                'ARCHIVE_MEMBER_COUNT_MISMATCH')
        C.real_directory(source)
        H.reject_generated_mounts(source)
        require(H.generated_manifest(source) == manifest, 'CURRENT_SOURCE_DIFFERS_FROM_ARCHIVE')
        if source == BACKUP:
            require(manifest == known and row['known_backup_manifest_matches'] is True,
                    'ORIGINAL_BACKUP_MANIFEST_MISMATCH')
        result[label] = {'source': source, 'archive': archive, 'archive_record': archive_record,
                         'manifest': manifest, 'manifest_path': manifest_path}
    H.RECEIPT['archive_preparation'] = {'stage': str(ARCHIVE_STAGE), 'receipt_sha256': ARCHIVE_RECEIPT_SHA,
                                      'known_manifest': known_record}
    return result


def load_pack_receipt(stage, expected_sha):
    raw = read_regular(stage / 'receipt.json')
    require(hashlib.sha256(raw).hexdigest() == expected_sha, 'PACK_PASS_RECEIPT_CHANGED')
    receipt = json.loads(raw)
    require(receipt['status'] == 'PASS' and receipt['task'] == 'PR2113_prepare_loose_pack'
            and receipt['deployment_sha'] == D and receipt['source_base_sha'] == J
            and receipt['helper_sha256'] == PACK_HELPER_SHA
            and receipt['common_sha256'] == COMMON_SHA and receipt['stage'] == str(stage),
            'PACK_PREPARATION_NOT_REVIEWED_PASS')
    proof = receipt['isolated_logical_proof']
    require(proof['alternates'] is False and proof['only_new_pack_pair'] is True
            and proof['logical_types_and_sizes_match'] is True, 'PACK_LOGICAL_PROOF_MISSING')
    before_raw = read_regular(stage / 'snapshot-before.json')
    after_raw = read_regular(stage / 'snapshot-after.json')
    require(hashlib.sha256(before_raw).hexdigest() == receipt['before_snapshot_sha256']
            and hashlib.sha256(after_raw).hexdigest() == receipt['after_snapshot_sha256'],
            'PACK_SNAPSHOT_HASH_CHANGED')
    before = json.loads(before_raw)
    require(before == json.loads(after_raw), 'PACK_PREPARATION_CHANGED_APP')
    paths = P.loose_paths(before['objects'])
    oids = [name.replace('/', '') for name in paths]
    require(read_regular(stage / 'loose-oids.txt') == ('\n'.join(oids) + '\n').encode(),
            'REVIEWED_LOOSE_OID_LIST_CHANGED')
    pack = receipt['pack']
    require(re.fullmatch(r'[0-9a-f]{40}', pack['hash']) and len(paths) == P.EXPECTED_LOOSE
            == receipt['object_count'] == pack['object_count'] == proof['object_count'],
            'PACK_OBJECT_COUNT_OR_HASH_INVALID')
    pack_path = stage / ('loose-' + pack['hash'] + '.pack')
    index_path = stage / ('loose-' + pack['hash'] + '.idx')
    require(pack['pack_path'] == str(pack_path) and pack['index_path'] == str(index_path),
            'PACK_PATH_BINDING_INVALID')
    exact_file(pack_path, pack['pack_bytes'], pack['pack_sha256'])
    exact_file(index_path, pack['index_bytes'], pack['index_sha256'])
    require(read_regular(pack_path)[-20:].hex() == pack['hash']
            and read_regular(index_path)[-40:-20].hex() == pack['hash'], 'PACK_INDEX_BINDING_INVALID')
    expected = {oid: before['logical_objects'][oid] for oid in oids}
    verification = P.git('verify-pack', '-v', str(index_path), timeout=600)
    P.verify_pack_listing(verification, expected, pack_path)
    proof_stage = H.STAGE / 'prepared-pack-proof'
    proof_stage.mkdir(mode=0o700)
    previous_stage = P.STAGE
    try:
        P.STAGE = proof_stage
        fresh_proof = P.verify_isolated_logical_objects(pack_path, index_path, expected)
    finally:
        P.STAGE = previous_stage
    H.RECEIPT['pack_preparation'] = {'stage': str(stage), 'receipt_sha256': expected_sha,
                                   'pack': pack, 'fresh_isolated_logical_proof': fresh_proof,
                                   'reviewed_loose_oid_sha256': hashlib.sha256(
                                       read_regular(stage / 'loose-oids.txt')).hexdigest()}
    return receipt, before, paths, expected, pack_path, index_path


def install_pack(pack, pack_path, index_path):
    directory = GIT / 'objects/pack'
    C.real_directory(directory)
    stem = 'pack-' + pack['hash']
    destinations = {suffix: directory / (stem + suffix) for suffix in ('.keep', '.pack', '.idx')}
    require(all(not os.path.lexists(path) for path in destinations.values()), 'PACK_DESTINATION_COLLISION')
    require(space()['available_bytes'] > pack['pack_bytes'] + pack['index_bytes'] + 16 * 1024 * 1024,
            'NEW_PACK_CAPACITY_INSUFFICIENT')
    keep_data = ('PR2113 owned capacity pack; preserve all logical objects; ' + pack['hash'] + '\n').encode()
    descriptor = os.open(destinations['.keep'], os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, 'wb') as stream:
        stream.write(keep_data)
        stream.flush()
        os.fsync(stream.fileno())
    H.fsync_directory(directory)
    additions = {'pack/' + destinations['.keep'].name: fingerprint(destinations['.keep'])}
    for source, suffix, size_key, hash_key in ((pack_path, '.pack', 'pack_bytes', 'pack_sha256'),
                                             (index_path, '.idx', 'index_bytes', 'index_sha256')):
        exact_file(source, pack[size_key], pack[hash_key])
        temporary = DURABLE / (stem + suffix)
        exclusive_copy(source, temporary, mode=0o444)
        exact_file(temporary, pack[size_key], pack[hash_key])
        require(fingerprint(destinations['.keep']) == additions['pack/' + destinations['.keep'].name],
                'OWNED_KEEP_CHANGED')
        # link is atomic and exclusive; the complete fsynced file becomes visible.
        os.link(temporary, destinations[suffix], follow_symlinks=False)
        H.fsync_directory(directory)
        additions['pack/' + destinations[suffix].name] = exact_file(
            destinations[suffix], pack[size_key], pack[hash_key])
    return destinations, additions


def parse_prune_dryrun(raw, expected_paths):
    found = []
    for line in raw.decode('ascii').splitlines():
        match = re.fullmatch(r'rm -f (?:/app/)?\.git/objects/([0-9a-f]{2})/([0-9a-f]{38})', line)
        require(match is not None, 'UNEXPECTED_PRUNE_DRYRUN_OUTPUT')
        found.append(match[1] + '/' + match[2])
    require(len(found) == len(set(found)) and sorted(found) == sorted(expected_paths),
            'PRUNE_DRYRUN_DIFFERS_FROM_REVIEWED_LOOSE_SET')
    return found


def main():
    global H, C, P, DURABLE
    os.umask(0o077)
    try:
        require(len(sys.argv) == 4, 'PACK_STAGE_RECEIPT_SHA_AND_ARCHIVE_HELPER_REQUIRED')
        pack_stage, pack_receipt_sha, archive_helper = Path(sys.argv[1]), sys.argv[2], Path(sys.argv[3])
        require(re.fullmatch(r'[0-9a-f]{64}', pack_receipt_sha), 'PACK_RECEIPT_SHA_INVALID')
        require(pack_stage.parent == Path('/tmp') and pack_stage.name.startswith('mezan-pr2113-pack-'),
                'PACK_STAGE_NOT_OWNED_TMP_PATH')
        require(Path.cwd() == ROOT and ROOT.resolve() == ROOT, 'RUN_FROM_REAL_APP')
        for key in ('GIT_DIR', 'GIT_WORK_TREE', 'GIT_COMMON_DIR', 'GIT_INDEX_FILE',
                    'GIT_OBJECT_DIRECTORY', 'GIT_ALTERNATE_OBJECT_DIRECTORIES',
                    'GIT_CONFIG_COUNT', 'GIT_CONFIG_PARAMETERS'):
            require(key not in os.environ, 'UNSUPPORTED_GIT_ENVIRONMENT')
        H = checked_module('pr2113_apply_common', COMMON_PATH, COMMON_SHA)
        C = checked_module('pr2113_apply_archive_functions', archive_helper, ARCHIVE_HELPER_SHA)
        P = checked_module('pr2113_apply_pack_functions', pack_stage / 'operator-helper.py', PACK_HELPER_SHA)
        C.H = H
        P.COMMON = H
        for path in (ROOT, GIT, GIT / 'objects', GIT / 'objects/pack', ROOT / 'frontend',
                     Path('/tmp'), ARCHIVE_STAGE, pack_stage):
            C.real_directory(path)
        H.reject_generated_mounts(GIT / 'objects')
        H.STAGE = Path(tempfile.mkdtemp(prefix='mezan-pr2113-capacity-apply-', dir='/tmp'))
        H.STAGE.chmod(0o700)
        P.STAGE = H.STAGE
        P.ENV['GIT_NO_LAZY_FETCH'] = '1'
        H.ENV.update(P.ENV)
        H.RECEIPT = {'schema_version': 1, 'task': 'PR2113_apply_reviewed_capacity', 'status': 'RUNNING',
                     'stage': str(H.STAGE), 'deployment_sha': D, 'source_base_sha': J,
                     'operator_sha256': hashlib.sha256(read_regular(Path(__file__).resolve())).hexdigest(),
                     'common_sha256': COMMON_SHA, 'archive_helper_sha256': ARCHIVE_HELPER_SHA,
                     'pack_helper_sha256': PACK_HELPER_SHA, 'steps': [], 'build_started': False,
                     'lease_created': False, 'published': False, 'automatic_rollback': False,
                     'allowed_removals': [str(PARTIAL), str(BACKUP), 'exact reviewed packed loose duplicates'],
                     'pack_preparation_receipt_sha256': pack_receipt_sha, 'space_before': space()}
        save_receipt()
        print('STAGE_PATH=' + str(H.STAGE), flush=True)
        require(P.git('rev-parse', '--show-toplevel').strip() == b'/app'
                and P.git('rev-parse', '--absolute-git-dir').strip() == b'/app/.git', 'GIT_ROOT_CHANGED')
        object_dir = Path(os.fsdecode(P.git('rev-parse', '--git-path', 'objects').strip()))
        require((object_dir if object_dir.is_absolute() else ROOT / object_dir).resolve() == GIT / 'objects',
                'UNEXPECTED_OBJECT_DIRECTORY')
        guard_path = ROOT / 'scripts/production_release_guard.py'
        guard_bytes = read_regular(guard_path)
        require(guard_bytes == P.git('show', J + ':scripts/production_release_guard.py'), 'ORIGINAL_GUARD_CHANGED')
        lock_path = GIT / '.mezan-production-release.lock.operation.lock'
        require(lock_path.is_file() and not lock_path.is_symlink()
                and stat.S_IMODE(lock_path.stat().st_mode) == 0o600, 'EXISTING_OPERATION_LOCK_REQUIRED')
        status = json.loads(P.command('original-status', [sys.executable, '-B', str(guard_path), 'status'], timeout=45))
        require(status.get('active') is False and not status.get('incomplete_lease_directory'), 'LEASE_NOT_INACTIVE')
        guard = checked_module('pr2113_apply_guard', guard_path, hashlib.sha256(guard_bytes).hexdigest())
        require(guard.REPO_ROOT == ROOT and guard.GIT_DIR == GIT, 'GUARD_ROOT_CHANGED')
        for signum in (signal.SIGINT, signal.SIGTERM, signal.SIGALRM):
            signal.signal(signum, H.interrupt)
        H.STEP = 'original-operation-lock'
        signal.alarm(30)
        with guard._release_operation_lock():
            signal.alarm(0)
            assert_state(guard)
            phase('validate-reviewed-pack')
            pack_receipt, prep_snapshot, loose, expected, pack_path, index_path = load_pack_receipt(
                pack_stage, pack_receipt_sha)
            before = P.snapshot(H, P.git)
            set_runtime_identity(before['foreign'])
            compare_snapshot(prep_snapshot, before)
            require(all(record['type'] == 'directory' for name, record in before['objects'].items()
                        if re.fullmatch(r'[0-9a-f]{2}', name)), 'ALIASED_LOOSE_FANOUT_DIRECTORY')
            evidence('snapshot-before.json', before)
            H.private_json(H.STAGE / 'untracked-before.json', before['foreign'])
            phase('validate-reviewed-archives')
            archives = reviewed_archives()
            H.preserve_foreign(before['foreign'])
            assert_state(guard)
            partial = archives['failed-install-node-modules']
            phase('remove-owned-failed-install')
            remove_owned_tree(PARTIAL, partial['manifest'], partial['archive'], partial['archive_record'])
            H.RECEIPT['partial_removal'] = {'complete': True, 'source': str(PARTIAL),
                                           'retained_archive': str(partial['archive']),
                                           'archive_record': partial['archive_record'],
                                           'archive_storage': 'owned /tmp only', 'space_after': space()}
            save_receipt()
            DURABLE = Path(tempfile.mkdtemp(prefix='mezan-pr2113-capacity-applied-', dir=GIT))
            DURABLE.chmod(0o700)
            H.fsync_directory(DURABLE)
            H.fsync_directory(GIT)
            H.RECEIPT['durable_stage'] = str(DURABLE)
            original = archives['original-node-modules-backup']
            phase('persist-original-backup-archive')
            durable_archive = DURABLE / 'original-node-modules-backup.tar.gz'
            archive_record = exclusive_copy(original['archive'], durable_archive)
            exact_file(durable_archive, original['archive_record']['bytes'],
                       original['archive_record']['sha256'])
            durable_manifest = DURABLE / 'original-node-modules-backup-restore-manifest.json'
            manifest_record = exclusive_copy(original['manifest_path'], durable_manifest)
            require(json.loads(read_regular(durable_manifest)) == original['manifest']
                    == json.loads(read_regular(KNOWN_MANIFEST)), 'DURABLE_RESTORE_MANIFEST_MISMATCH')
            exclusive_copy(ARCHIVE_STAGE / 'receipt.json', DURABLE / 'archive-preparation-receipt.json')
            exclusive_copy(pack_stage / 'receipt.json', DURABLE / 'pack-preparation-receipt.json')
            require(fingerprint(DURABLE / 'archive-preparation-receipt.json')['sha256'] == ARCHIVE_RECEIPT_SHA
                    and fingerprint(DURABLE / 'pack-preparation-receipt.json')['sha256'] == pack_receipt_sha,
                    'DURABLE_PREPARATION_RECEIPT_CHANGED')
            H.RECEIPT['original_backup_preservation'] = {
                'archive': str(durable_archive), 'archive_record': archive_record,
                'restore_manifest': str(durable_manifest), 'manifest_record': manifest_record,
                'file_and_directory_fsync_completed': True}
            save_receipt()
            assert_state(guard)
            H.preserve_foreign(before['foreign'])
            phase('remove-owned-raw-original-backup')
            exact_file(durable_manifest, manifest_record['bytes'], manifest_record['sha256'])
            remove_owned_tree(BACKUP, original['manifest'], durable_archive, archive_record)
            H.RECEIPT['original_raw_backup_removed'] = True
            H.RECEIPT['space_after_generated_cleanup'] = space()
            save_receipt()
            assert_state(guard)
            compare_snapshot(before, P.snapshot(H, P.git))
            phase('install-reviewed-owned-pack')
            destinations, additions = install_pack(pack_receipt['pack'], pack_path, index_path)
            H.RECEIPT['installed_pack'] = {'paths': {key: str(path) for key, path in destinations.items()},
                                           'records': additions, 'keep_created_before_index': True}
            save_receipt()
            verified = P.git('verify-pack', '-v', str(destinations['.idx']), timeout=600)
            P.verify_pack_listing(verified, expected, destinations['.pack'])
            phase('review-exact-prune-dryrun')
            compare_snapshot(before, P.snapshot(H, P.git), additions)
            dryrun = P.git('prune-packed', '--dry-run')
            reviewed = parse_prune_dryrun(dryrun, loose)
            evidence('reviewed-prune-dryrun.txt', dryrun)
            H.RECEIPT['prune_dryrun'] = {'count': len(reviewed), 'sha256': hashlib.sha256(dryrun).hexdigest(),
                                        'matches_exact_reviewed_all_loose_set': True}
            compare_snapshot(before, P.snapshot(H, P.git), additions)
            assert_state(guard)
            phase('prune-only-verified-packed-duplicates')
            P.git('prune-packed', timeout=600)
            H.RECEIPT['prune_packed_completed'] = True
            save_receipt()
            after = P.snapshot(H, P.git)
            removed_dirs = compare_snapshot(before, after, additions, pruned=True)
            evidence('snapshot-after.json', after)
            assert_state(guard)
            H.preserve_foreign(before['foreign'])
            exact_file(durable_archive, archive_record['bytes'], archive_record['sha256'])
            exact_file(durable_manifest, manifest_record['bytes'], manifest_record['sha256'])
            H.RECEIPT.update(status='PASS', phase='complete', removed_empty_fanout_directories=removed_dirs,
                             preserved_logical_object_count=len(after['logical_objects']),
                             preserved_foreign_count=432, space_after=space(),
                             source_and_refs_unchanged=True, old_packs_unchanged=True,
                             indices_and_worktree_metadata_unchanged=True)
            save_receipt()
        print('FINAL_STATUS=PASS:CAPACITY_ONLY:NO_BUILD', flush=True)
        print(json.dumps({'durable_stage': str(DURABLE), 'space_after': H.RECEIPT['space_after']}, sort_keys=True), flush=True)
        return 0
    except BaseException as exc:
        signal.alarm(0)
        code = str(exc) if isinstance(exc, RuntimeError) else type(exc).__name__
        if isinstance(exc, OSError):
            code += ':errno=' + str(exc.errno)
        if H is not None and H.STAGE is not None:
            H.RECEIPT.update(status='BLOCKED', failure_step=H.STEP, failure_code=code, automatic_rollback=False)
            try:
                save_receipt()
            except BaseException:
                pass
        print('FINAL_STATUS=BLOCKED:' + code, flush=True)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
