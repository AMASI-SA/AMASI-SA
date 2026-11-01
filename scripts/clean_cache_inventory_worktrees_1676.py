#!/usr/bin/env python3
"""Clean exactly two download caches; inventory old worktrees without deleting them.

No source, installed application packages, worktree, release artifact, Git
history, service, financial database, or release lease is changed. Nested
symlinks are unlinked, never followed. Refuse mount boundaries and active
package operations. This helper is NOT part of frozen release A or B.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys

APP = Path('/app')
BASE = '3bac875cfbcbc7f66849edae83c478dd194d9c12'
CACHES = (Path('/root/.cache/yarn/v6'), Path('/root/.cache/pip'))
WORKTREES = APP / '.worktrees'
ENV = dict(os.environ, GIT_OPTIONAL_LOCKS='0', GIT_PAGER='cat', PAGER='cat',
           PYTHONDONTWRITEBYTECODE='1', GIT_TERMINAL_PROMPT='0')
for _key in ('GIT_DIR', 'GIT_WORK_TREE', 'GIT_INDEX_FILE', 'PYTHONHOME', 'PYTHONPATH'):
    ENV.pop(_key, None)


def require(ok, message):
    if not ok:
        raise RuntimeError(message)


def run(args, cwd=APP):
    result = subprocess.run(args, cwd=cwd, env=ENV, text=True,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    require(result.returncode == 0, 'Command failed: ' + args[0] + ' (exit ' + str(result.returncode) + ')')
    return result.stdout


def inside(path, root):
    return path == root or root in path.parents


def mount_points():
    decode = lambda text: re.sub(r'\\([0-7]{3})', lambda m: chr(int(m[1], 8)), text)
    return {Path(decode(line.split()[4])) for line in
            Path('/proc/self/mountinfo').read_text().splitlines() if len(line.split()) > 5}


def validate_cache(root, device, mounts):
    """Read only. Do not dereference symlinks even when their target is missing."""
    try:
        info = root.lstat()
    except FileNotFoundError:
        return None
    require(stat.S_ISDIR(info.st_mode) and root.resolve() == root,
            'Cache root is redirected or is not a directory: ' + str(root))
    require(info.st_dev == device and info.st_uid == os.geteuid(), 'Cache device/owner changed: ' + str(root))
    require(not any(inside(point, root) for point in mounts), 'Mount boundary inside cache: ' + str(root))
    links = 0
    def fail(error):
        raise error
    for current, directories, files in os.walk(root, topdown=True, followlinks=False, onerror=fail):
        for name in directories + files:
            child = Path(current) / name
            item = child.lstat()
            require(item.st_dev == device, 'Cross-device entry preserved: ' + str(child))
            if stat.S_ISLNK(item.st_mode):
                links += 1
            else:
                require(stat.S_ISREG(item.st_mode) or stat.S_ISDIR(item.st_mode),
                        'Special cache entry preserved: ' + str(child))
    return (info.st_dev, info.st_ino, links)


def empty_cache(root, expected):
    """FD-relative deletion with Python's symlink-attack-resistant implementation."""
    require(shutil.rmtree.avoids_symlink_attacks, 'Safe fd-relative deletion is unavailable.')
    require(root.resolve() == root, 'Cache root changed before deletion.')
    fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        opened = os.fstat(fd)
        require((opened.st_dev, opened.st_ino) == expected[:2], 'Cache root identity changed; preserved.')
        for name in os.listdir(fd):
            info = os.stat(name, dir_fd=fd, follow_symlinks=False)
            if stat.S_ISDIR(info.st_mode):
                shutil.rmtree(name, dir_fd=fd)
            else:
                # unlink removes the link itself, not its target.
                os.unlink(name, dir_fd=fd)
    finally:
        os.close(fd)


def processes():
    result = []
    for proc in Path('/proc').iterdir():
        if not proc.name.isdigit() or int(proc.name) == os.getpid():
            continue
        try:
            args = (proc / 'cmdline').read_bytes().decode('utf-8', 'replace').split('\0')
            cwd = Path(os.readlink(proc / 'cwd'))
        except (FileNotFoundError, ProcessLookupError):
            continue
        except PermissionError:
            try:
                require(proc.stat().st_uid != os.geteuid(), 'Cannot inspect same-user process ' + proc.name)
            except (FileNotFoundError, ProcessLookupError):
                pass
            continue
        result.append((proc.name, cwd, args))
    return result


def check_cache_idle(snapshot):
    managers = {'yarn', 'yarn.js', 'npm', 'npm-cli.js', 'pnpm', 'pip', 'pip3',
                'pip3.11', 'corepack', 'corepack.js', 'uv', 'poetry'}
    operations = {'install', 'add', 'upgrade', 'update', 'ci', 'download', 'wheel',
                  'uninstall', 'remove', 'cache', 'prepare', 'sync'}
    for pid, cwd, args in snapshot:
        require(not (any(Path(arg).name in managers for arg in args[:6]) and operations.intersection(args)),
                'Package operation active (PID ' + pid + '); no cleanup allowed.')
        for root in CACHES:
            referenced = inside(cwd, root) or any(arg == str(root) or arg.startswith(str(root) + '/') for arg in args)
            require(not referenced, 'Cache is used by process ' + pid + '; preserved.')


def app_guard():
    require(run(['git', 'rev-parse', 'HEAD']).strip() == BASE, '/app source changed; do not repeat cleanup.')
    require(run(['git', 'branch', '--show-current']).strip() == 'hotfix/prod-snap-meta-final', '/app branch changed.')
    require(not run(['git', 'diff', '--name-only', 'HEAD', '--']).strip(), '/app has source changes; preserved.')
    state = json.loads(run([sys.executable, '-B', 'scripts/production_release_guard.py', 'status']))
    require(state.get('active') is False, 'Active production release lease; cleanup refused.')


def measured_kib(path):
    if not path.is_dir() or path.is_symlink() or path.resolve() != path:
        return None
    output = run(['du', '-P', '-x', '-s', '-k', '--', str(path)])
    return int(output.split('\t', 1)[0])


def worktree_inventory():
    print('=== WORKTREES READ ONLY — NOTHING REMOVED ===', flush=True)
    if not WORKTREES.is_dir() or WORKTREES.resolve() != WORKTREES:
        print('Worktree directory absent or redirected; preserved.')
        return
    records = {}
    for block in run(['git', 'worktree', 'list', '--porcelain', '-z']).split('\0\0'):
        fields = block.split('\0')
        first = next((value[9:] for value in fields if value.startswith('worktree ')), None)
        if first:
            records[first] = fields
    rows = []
    for path in WORKTREES.iterdir():
        if path.is_dir() and not path.is_symlink():
            size = measured_kib(path)
            if size is not None:
                rows.append((size, path))
    snapshot = processes()
    for size, path in sorted(rows, key=lambda item: item[0], reverse=True)[:12]:
        info = {'folder': str(path), 'total_MiB': round(size / 1024, 1)}
        registry = records.get(str(path))
        info['registered_worktree'] = bool(registry)
        if registry:
            info['locked'] = any(field == 'locked' or field.startswith('locked ') for field in registry)
            info['head'] = next((field[5:] for field in registry if field.startswith('HEAD ')), '')
            try:
                status = run(['git', '-c', 'core.fsmonitor=false', 'status', '--porcelain=v1', '-z',
                              '--untracked-files=all'], cwd=path)
                info['source_status'] = 'changes_present' if status else 'clean'
            except RuntimeError:
                info['source_status'] = 'unreadable_preserved'
        observed = [pid for pid, cwd, args in snapshot if inside(cwd, path) or
                    any(arg == str(path) or arg.startswith(str(path) + '/') for arg in args)]
        info['observed_processes'] = observed[:8]
        for relative in ('frontend/node_modules', 'node_modules', 'frontend/build'):
            value = measured_kib(path / relative)
            if value is not None:
                info[relative + '_MiB'] = round(value / 1024, 1)
        print(json.dumps(info, ensure_ascii=False), flush=True)
    print('A clean status or no observed process alone does NOT authorize deleting a worktree.', flush=True)


def main():
    require(sys.version_info >= (3, 11), 'Python 3.11+ required.')
    require(shutil.rmtree.avoids_symlink_attacks, 'Safe cache deletion unavailable.')
    app_guard()
    check_cache_idle(processes())
    mounts = mount_points()
    plans = [(root, validate_cache(root, APP.stat().st_dev, mounts)) for root in CACHES]
    print('=== FREE SPACE BEFORE ===', flush=True)
    print(run(['df', '-h', '/app', '/root', '/tmp']), flush=True)
    before = shutil.disk_usage(APP).free
    for root, identity in plans:
        if identity is None:
            print('CACHE_ABSENT', str(root), flush=True)
            continue
        app_guard()
        check_cache_idle(processes())
        current = validate_cache(root, APP.stat().st_dev, mount_points())
        require(current is not None and current[:2] == identity[:2], 'Cache root changed; preserved.')
        print('CLEANING_DOWNLOAD_CACHE_ONLY', str(root), 'symlinks_unlinked_not_followed=', current[2], flush=True)
        empty_cache(root, current)
    after = shutil.disk_usage(APP).free
    print('=== FREE SPACE AFTER ===', flush=True)
    print(run(['df', '-h', '/app', '/root', '/tmp']), flush=True)
    print('OBSERVED_FREE_SPACE_CHANGE_MiB', round((after - before) / 1024**2, 1), flush=True)
    print('DOWNLOAD_CACHES_CLEANED — installed dependencies and worktrees preserved.', flush=True)
    worktree_inventory()
    app_guard()
    print('=== CACHE_CLEAN_AND_WORKTREE_REPORT_COMPLETE ===', flush=True)
    print('No merge, deployment, worktree removal, source edit, or financial write.', flush=True)


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print('STOPPED:', str(error), flush=True)
        print('No deployment. A cache may be partially cleaned if an error occurred after cleanup began.', flush=True)
        raise SystemExit(1)
