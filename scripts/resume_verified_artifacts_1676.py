#!/usr/bin/env python3
"""Resume PR1676 from the already-built recovery; never reinstall or rebuild.

Correct only the OPS helper's artifact filesystem check. Linux OverlayFS may
report different st_dev for regular files and their directory on one mount.
Use kernel mnt_id from opened descriptors instead, rejecting real nested mounts,
symlinks, special files, path traversal and changes during reads. All original
frozen-intent, byte/hash, source, capacity and Release Guard checks still run.
No application/release source is edited; no Publish or financial write.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import stat
import types
import urllib.request

RECOVERY_REVISION = '244b34dcbe23f7a2285fef087c1f025aff25c2a5'
RECOVERY_HASH = '9d45c96a98baa4eb0160d91eb5b7e068d014a02273dda21bcdb34b6b76c5309d'
RECOVERY_URL = ('https://raw.githubusercontent.com/AMASI-SA/AMASI-SA/' +
                RECOVERY_REVISION + '/scripts/recover_missing_release_1676.py')
SECURITY_PATH = 'frontend/build/.well-known/security.txt'
SECURITY_HASH = 'e22bb861fea6e29f935fc8a61bf8037cc76924311b50c8beda4c42452f83c876'
SECURITY_BYTES = 252
GENERATED = ('frontend/build', 'frontend/.release/reproducible-build.json',
             'backend/release_identity.json')


def require(ok, message):
    if not ok:
        raise RuntimeError(message)


def mount_id(fd):
    """The kernel mount ID, not the underlying inode's device number."""
    data = Path('/proc/self/fdinfo/' + str(fd)).read_text(encoding='ascii')
    values = [line.split(':', 1)[1].strip() for line in data.splitlines()
              if line.startswith('mnt_id:')]
    require(len(values) == 1 and values[0].isdigit(),
            'MOUNT_ID_UNAVAILABLE: refusing to weaken artifact checks.')
    return int(values[0])


def signature(info):
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns,
            info.st_ctime_ns, info.st_mode)


def open_child(parent_fd, name, label):
    before = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    require(not stat.S_ISLNK(before.st_mode), 'SYMLINK_BLOCKED: ' + str(label))
    require(stat.S_ISREG(before.st_mode) or stat.S_ISDIR(before.st_mode),
            'SPECIAL_FILE_BLOCKED: ' + str(label))
    flags = os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK
    if stat.S_ISDIR(before.st_mode):
        flags |= os.O_DIRECTORY
    fd = os.open(name, flags, dir_fd=parent_fd)
    try:
        require(signature(before) == signature(os.fstat(fd)),
                'PATH_CHANGED_WHILE_OPENING: ' + str(label))
        return fd
    except BaseException:
        os.close(fd)
        raise


def open_absolute_directory(path):
    """Open each ancestor without following any symbolic link."""
    path = Path(path)
    require(path.is_absolute() and '..' not in path.parts, 'Unsafe absolute directory.')
    fd = os.open('/', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        current = Path('/')
        for part in path.parts[1:]:
            current /= part
            child = open_child(fd, part, current)
            try:
                require(stat.S_ISDIR(os.fstat(child).st_mode),
                        'PARENT_NOT_DIRECTORY: ' + str(current))
            except BaseException:
                os.close(child)
                raise
            os.close(fd)
            fd = child
        return fd
    except BaseException:
        os.close(fd)
        raise


def same_mount(parent_mount, child_fd, label):
    actual = mount_id(child_fd)
    require(actual == parent_mount,
            'NESTED_MOUNT_BLOCKED: ' + str(label) +
            ' root_mnt_id=' + str(parent_mount) + ' entry_mnt_id=' + str(actual))


def safe_path(root, relative, allow_missing=False):
    root = Path(root)
    parts = Path(relative).parts
    require(parts and not Path(relative).is_absolute() and '..' not in parts,
            'Unsafe relative artifact path.')
    fd = open_absolute_directory(root)
    try:
        expected_mount = mount_id(fd)
        current = root
        for index, part in enumerate(parts):
            current /= part
            try:
                child = open_child(fd, part, current)
            except FileNotFoundError:
                require(allow_missing, 'Missing artifact: ' + str(current))
                return root.joinpath(*parts)
            try:
                same_mount(expected_mount, child, current)
                if index < len(parts) - 1:
                    require(stat.S_ISDIR(os.fstat(child).st_mode),
                            'PARENT_NOT_DIRECTORY: ' + str(current))
            except BaseException:
                os.close(child)
                raise
            os.close(fd)
            fd = child
        return current
    finally:
        os.close(fd)


def snapshot(path):
    """Compatible records for the original copy/hash checks, with fd-based reads."""
    path = Path(path)
    parent = open_absolute_directory(path.parent)
    records = {}
    try:
        expected_mount = mount_id(parent)
        fd = open_child(parent, path.name, path)
        try:
            same_mount(expected_mount, fd, path)

            def visit(entry_fd, relative, label):
                same_mount(expected_mount, entry_fd, label)
                before = os.fstat(entry_fd)
                if stat.S_ISDIR(before.st_mode):
                    records[relative] = ('directory',)
                    names = sorted(os.listdir(entry_fd))
                    for name in names:
                        child = open_child(entry_fd, name, str(label) + '/' + name)
                        try:
                            child_before = signature(os.fstat(child))
                            visit(child, relative + '/' + name, str(label) + '/' + name)
                            require(child_before == signature(os.stat(
                                name, dir_fd=entry_fd, follow_symlinks=False)),
                                'ENTRY_CHANGED_WHILE_READING: ' + str(label) + '/' + name)
                        finally:
                            os.close(child)
                    require(names == sorted(os.listdir(entry_fd)),
                            'DIRECTORY_MEMBERSHIP_CHANGED: ' + str(label))
                else:
                    require(stat.S_ISREG(before.st_mode), 'SPECIAL_FILE_BLOCKED: ' + str(label))
                    digest = hashlib.sha256()
                    count = 0
                    while True:
                        chunk = os.read(entry_fd, 1024 * 1024)
                        if not chunk:
                            break
                        digest.update(chunk)
                        count += len(chunk)
                    require(count == before.st_size, 'FILE_SIZE_CHANGED: ' + str(label))
                    records[relative] = ('file', count, digest.hexdigest(), stat.S_IMODE(before.st_mode))
                require(signature(before) == signature(os.fstat(entry_fd)),
                        'ARTIFACT_CHANGED_WHILE_READING: ' + str(label))

            before = signature(os.fstat(fd))
            visit(fd, '.', path)
            require(before == signature(os.stat(path.name, dir_fd=parent, follow_symlinks=False)),
                    'ARTIFACT_ROOT_CHANGED: ' + str(path))
        finally:
            os.close(fd)
    finally:
        os.close(parent)
    return records


def security_probe(review):
    """Reject a real redirection or wrong bytes; report evidence, not a guess."""
    build = safe_path(review, 'frontend/build')
    target = safe_path(review, SECURITY_PATH)
    record = snapshot(target)['.']
    require(record[0] == 'file' and record[1] == SECURITY_BYTES and record[2] == SECURITY_HASH,
            'SECURITY_TXT_HASH_MISMATCH: preserved without copying.')
    directory_fd = open_absolute_directory(target.parent)
    try:
        file_fd = open_child(directory_fd, target.name, target)
        try:
            same_mount(mount_id(directory_fd), file_fd, target)
            info = os.fstat(file_fd)
            details = {'regular_file': stat.S_ISREG(info.st_mode),
                       'build_st_dev': build.lstat().st_dev, 'file_st_dev': info.st_dev,
                       'same_kernel_mount': True, 'mnt_id': mount_id(file_fd),
                       'bytes': record[1], 'sha256_match': True}
        finally:
            os.close(file_fd)
    finally:
        os.close(directory_fd)
    print('ARTIFACT_PATH_PROBE ' + json.dumps(details, sort_keys=True), flush=True)
    print('SECURITY_TXT_VERIFIED_NO_REDIRECTION', flush=True)


def existing_review_only(recovery, h, state, state_file):
    """Never clone/rebuild during this narrowly-scoped continuation."""
    root_text = state.get('build_root')
    require(isinstance(root_text, str), 'Recorded build directory is missing; nothing rebuilt.')
    root = Path(root_text)
    require(root.parent == Path('/tmp') and root.name.startswith('mezan-1676-recovery-'),
            'Unexpected recorded recovery directory.')
    recovery.real_dir(root, private=True)
    review = root / 'rehearsal'
    recovery.real_dir(review)
    for relative in GENERATED:
        safe_path(review, relative)
    # Check the tracked source and frozen intent BEFORE accepting any generated bytes.
    recovery.verify_source(h, review)
    security_probe(review)
    print('REUSING_COMPLETED_BUILD_NO_REINSTALL', str(review), flush=True)
    return root, review


def no_rebuild(*args, **kwargs):
    raise RuntimeError('REBUILD_DISABLED: existing outputs incomplete; preserved for review.')


def configure_recovery(recovery):
    original_loader = recovery.load_handoff

    def patched_loader(raw):
        h = original_loader(raw)  # Original helper SHA256 and release pins are mandatory.
        h.safe_path = safe_path
        h.snapshot = snapshot
        return h

    recovery.load_handoff = patched_loader
    recovery.ensure_review = lambda h, state, state_file: existing_review_only(
        recovery, h, state, state_file)
    recovery.stream_command = no_rebuild
    return recovery


def main():
    for name in ('O_NOFOLLOW', 'O_DIRECTORY', 'O_CLOEXEC', 'O_NONBLOCK'):
        require(hasattr(os, name), 'Required Linux file safety primitive missing: ' + name)
    with urllib.request.urlopen(RECOVERY_URL, timeout=45) as response:
        raw = response.read(200001)
    require(hashlib.sha256(raw).hexdigest() == RECOVERY_HASH, 'Recovery helper checksum mismatch.')
    recovery = types.ModuleType('hash_verified_recovery_1676')
    exec(compile(raw, 'hash-verified-recovery-1676', 'exec'), recovery.__dict__)
    configure_recovery(recovery)
    print('=== VERIFIED ARTIFACT RESUME — NO REBUILD OR INSTALL ===', flush=True)
    recovery.main()


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print('ARTIFACT_RESUME_STOPPED:', str(error), flush=True)
        print('No automatic Publish. Preserve recovery records and backups; do not reset.', flush=True)
        raise SystemExit(1)
