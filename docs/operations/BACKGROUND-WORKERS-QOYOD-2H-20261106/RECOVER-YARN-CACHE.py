"""Preserve and clear only the old Yarn download cache, then resume v5 gates.

This operational helper is not part of the frozen Production source. It does
not alter worktrees, application files, Node caches, settings or live data.
"""
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import tempfile

DEPLOYMENT = 'a56399edf46f97f8b8e4793dc2619bb2a1b7ac2f'
HELPER = '6f3ab9fc6da61026e8d22a819df77e6b05122534:docs/operations/BACKGROUND-WORKERS-QOYOD-2H-20261106/DEPLOY-EMERGENT.sh'
HELPER_SHA256 = '43c89c3cef10a786d5e5f8070daedf9c3cc247feda046ef9c08e6c008a06d3fc'
CACHE = Path('/root/.cache/yarn')


def snapshot(root):
    records = {}
    pending = [root]
    while pending:
        directory = pending.pop()
        for item in directory.iterdir():
            info = item.lstat()
            relative = item.relative_to(root).as_posix()
            mode = stat.S_IMODE(info.st_mode)
            if stat.S_ISLNK(info.st_mode):
                records[relative] = ('link', os.readlink(item))
            elif stat.S_ISDIR(info.st_mode):
                if os.path.ismount(item):
                    raise RuntimeError('STOP: unexpected nested mount in package cache')
                records[relative] = ('directory', mode)
                pending.append(item)
            elif stat.S_ISREG(info.st_mode):
                digest = hashlib.sha256()
                with item.open('rb') as stream:
                    for block in iter(lambda: stream.read(1024 * 1024), b''):
                        digest.update(block)
                records[relative] = ('file', mode, info.st_size, digest.hexdigest())
            else:
                raise RuntimeError('STOP: unexpected special file in package cache')
    return records


def preserve_cache(source, backup_root, *, allowed_source, before_remove,
                   copier=shutil.copytree):
    source = Path(source).absolute()
    allowed_source = Path(allowed_source).absolute()
    backup_root = Path(backup_root).resolve(strict=True)
    if (source != allowed_source or source.is_symlink() or not source.is_dir()
            or source.resolve(strict=True) != source or os.path.ismount(source)):
        raise RuntimeError('STOP: cache source is not the exact permitted directory')
    if backup_root == source or source in backup_root.parents:
        raise RuntimeError('STOP: backup must be outside the cache')
    original_inode = (source.stat().st_dev, source.stat().st_ino)
    original_mode = stat.S_IMODE(source.stat().st_mode)
    destination = backup_root / 'yarn'
    if destination.exists() or destination.is_symlink():
        raise RuntimeError('STOP: backup destination already exists')
    print('Verifying the old Yarn cache before copying...', flush=True)
    original = snapshot(source)
    size = sum(row[2] for row in original.values() if row[0] == 'file')
    if shutil.disk_usage(backup_root).free < size + 256 * 1024 * 1024:
        raise RuntimeError('STOP: backup filesystem has insufficient capacity')
    print('Copying package cache to:', destination, flush=True)
    copier(source, destination, symlinks=True)
    if snapshot(destination) != original or snapshot(source) != original:
        raise RuntimeError('STOP: cache copy or source changed; original was retained')
    before_remove()
    if (source.is_symlink() or source.resolve(strict=True) != allowed_source
            or (source.stat().st_dev, source.stat().st_ino) != original_inode
            or os.path.ismount(source)):
        raise RuntimeError('STOP: cache path changed; original was retained')
    # The verified backup is on the other filesystem before any cache eviction.
    receipt = {'source': str(source), 'backup': str(destination),
               'entries_verified': len(original), 'logical_bytes': size,
               'stage': 'verified_backup_before_eviction'}
    record = backup_root / 'recovery.json'
    record.write_text(json.dumps(receipt, indent=2) + '\n', encoding='utf-8')
    shutil.rmtree(source)
    source.mkdir(mode=original_mode)
    receipt['stage'] = 'cache_evicted_backup_retained'
    record.write_text(json.dumps(receipt, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(receipt, indent=2), flush=True)
    return receipt


def require_workspace():
    state = json.loads(subprocess.check_output(
        ['python', 'scripts/production_release_guard.py', 'status'], text=True))
    if state.get('active') is not False or state.get('incomplete_lease_directory'):
        raise RuntimeError('STOP: another release lease requires its owner')
    head = subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip()
    if head != DEPLOYMENT:
        raise RuntimeError('STOP: the reviewed /app checkout changed')
    subprocess.run(['git', 'diff', '--quiet'], check=True)
    subprocess.run(['git', 'diff', '--cached', '--quiet'], check=True)


def require_cache_idle():
    prefix = str(CACHE) + '/'
    for process in Path('/proc').iterdir():
        if not process.name.isdigit() or int(process.name) == os.getpid():
            continue
        try:
            links = [process / 'cwd', process / 'exe', *(process / 'fd').iterdir()]
            for link in links:
                try:
                    target = os.readlink(link)
                except OSError:
                    continue
                if target == str(CACHE) or target.startswith(prefix):
                    raise RuntimeError('STOP: Yarn cache is in use by PID ' + process.name)
        except (FileNotFoundError, PermissionError, ProcessLookupError):
            continue


def main():
    os.chdir('/app')
    require_workspace()
    helper = subprocess.check_output(['git', 'show', HELPER])
    if hashlib.sha256(helper).hexdigest() != HELPER_SHA256:
        raise RuntimeError('STOP: reviewed deployment helper hash differs')
    app_device = Path('/app').stat().st_dev
    if Path('/tmp').stat().st_dev == app_device:
        raise RuntimeError('STOP: /tmp must be on the separate spacious filesystem')
    if CACHE.is_symlink():
        raise RuntimeError('STOP: package cache path changed to a symbolic link')
    if CACHE.is_dir() and CACHE.stat().st_dev == app_device:
        require_cache_idle()
        backup = Path(tempfile.mkdtemp(prefix='mezan-qoyod-yarn-preserved-', dir='/tmp'))
        def recheck():
            require_workspace()
            require_cache_idle()
        preserve_cache(CACHE, backup, allowed_source=CACHE, before_remove=recheck)
    free = shutil.disk_usage('/app').free
    print('Available /app space after preserving the old cache:', free, 'bytes', flush=True)
    if free < 768 * 1024 * 1024:
        raise RuntimeError('STOP: less than 768 MiB free; do not touch other worktrees')
    require_workspace()
    environment = os.environ.copy()
    environment['TMPDIR'] = '/tmp'
    environment['XDG_CACHE_HOME'] = '/tmp/mezan-release-v5-cache-0'
    subprocess.run(['bash'], input=helper, env=environment, check=True)


if __name__ == '__main__':
    main()
