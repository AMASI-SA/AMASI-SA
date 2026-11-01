#!/usr/bin/env python3
"""Recover the recorded PR1674 ENOSPC preparation, never deploy.

Delete only ignored node_modules in four exact task-owned, SHA-checked clones.
Keep all Git files, source changes, logs, Python envs, shared caches and /app.
Resume the hash-pinned preparer in memory, freeing its duplicate dependencies
before the independent clean-clone rehearsal. No guards/builds are bypassed.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

APP = Path('/app')
ROOT = Path('/tmp/mezan-release-1674-fg57cbud')
SOURCE = '667d94a3b01b7a59f58b753dfbf7d52c80764e02'
BASE = '3bac875cfbcbc7f66849edae83c478dd194d9c12'
HELPER = 'ec60bdfe00e04b354df552020d48d43831cc5fff'
HELPER_HASH = '0722b460ba4e9ba4c2af566cea1219cc372d5a88b5d994f86d458bd72c51b698'
BRANCH = 'release/operational-cost-1674-v5-20261101T002214Z-fg57cbud'
REVIEW_ROOTS = {
    Path('/tmp/mezan-alerts-review-1673'): '74383e3c78d263d5e005fe20ca277a315b995e22',
    Path('/tmp/mezan-alerts-security-1673'): 'b64b2ba3c5d612b6ffab95565050f6126196bbfe',
    Path('/tmp/mezan-open-cost-1674.pwfktc'): SOURCE,
    ROOT / 'source': SOURCE,
}
ENV = dict(os.environ, GIT_OPTIONAL_LOCKS='0', GIT_PAGER='cat', PAGER='cat',
           PYTHONDONTWRITEBYTECODE='1', GIT_TERMINAL_PROMPT='0')
for key in ('GIT_DIR', 'GIT_WORK_TREE', 'GIT_INDEX_FILE', 'PYTHONHOME', 'PYTHONPATH'):
    ENV.pop(key, None)


def require(ok: bool, reason: str) -> None:
    if not ok:
        raise RuntimeError(reason)


def command(args: list[str], cwd: Path = APP) -> str:
    result = subprocess.run(args, cwd=cwd, env=ENV, text=True,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    # Do not echo arbitrary command diagnostics or credentials.
    require(result.returncode == 0, 'Read/check failed for ' + args[0] + ' (exit ' + str(result.returncode) + ')')
    return result.stdout.strip()


def git(root: Path, *args: str) -> str:
    return command(['git', '--no-pager', '-C', str(root), *args])


def path_inside(path: Path, root: Path) -> bool:
    return path == root or root in path.parents


def assert_idle(root: Path) -> None:
    """Do not remove dependencies used by an active task process."""
    for proc in Path('/proc').iterdir():
        if not proc.name.isdigit() or int(proc.name) == os.getpid():
            continue
        try:
            cwd = Path(os.readlink(proc / 'cwd'))
            args = (proc / 'cmdline').read_bytes().decode('utf-8', 'replace').split('\0')
        except FileNotFoundError:
            continue
        except ProcessLookupError:
            continue
        except PermissionError:
            # These task clones and installs were created by the current user.
            # Container isolation may hide unrelated service users' cwd links.
            try:
                if proc.stat().st_uid != os.geteuid():
                    continue
            except (FileNotFoundError, ProcessLookupError):
                continue
            raise RuntimeError('Cannot check same-user task processes; no cleanup permitted.')
        require(not path_inside(cwd, root), 'Review is in use by process ' + proc.name + ': ' + str(root))
        require(not any(arg.startswith(str(root) + '/') or arg == str(root) for arg in args),
                'Review is referenced by process ' + proc.name + ': ' + str(root))


def validate_generated(root: Path, expected: str | None = None) -> Path | None:
    if not root.exists():
        return None
    require(root.resolve() == root and root.is_dir(), 'Unsafe review root: ' + str(root))
    require(git(root, 'rev-parse', '--show-toplevel') == str(root), 'Wrong repository root.')
    if expected:
        require(git(root, 'rev-parse', 'HEAD') == expected, 'Review source changed; preserved: ' + str(root))
    target = root / 'frontend' / 'node_modules'
    if not target.exists() and not target.is_symlink():
        return None
    require(target.resolve() == target and target.is_dir(), 'Unsafe generated dependency directory.')
    require(not git(root, 'ls-files', '--', 'frontend/node_modules'), 'Tracked dependencies must not be removed.')
    checked = subprocess.run(['git', '-C', str(root), 'check-ignore', '-q', '--', 'frontend/node_modules'],
                             env=ENV, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    require(checked.returncode == 0, 'Dependencies are not Git-ignored; preserved.')
    require(shutil.rmtree.avoids_symlink_attacks, 'This Python cannot safely remove generated directories.')
    assert_idle(root)
    # Do not cross bind mounts; symlinks inside node_modules are only unlinked.
    for current, dirs, _ in os.walk(target, followlinks=False):
        require(not os.path.ismount(current), 'Mount found in generated directory; preserved.')
        for name in dirs:
            child = Path(current) / name
            if not child.is_symlink():
                require(not child.is_mount(), 'Nested mount found; preserved.')
    return target


def drop_generated(root: Path) -> None:
    target = validate_generated(root)
    if target:
        print('Removing ONLY generated test dependencies: ' + str(target), flush=True)
        shutil.rmtree(target)


def disk_report() -> tuple[int, int, int]:
    print('=== FREE SPACE AND FILE SLOTS ===', flush=True)
    subprocess.run(['df', '-h', '/app', '/tmp', '/root'], check=True)
    subprocess.run(['df', '-i', '/app', '/tmp', '/root'], check=True)
    stats = os.statvfs(ROOT)
    return stats.f_bavail * stats.f_frsize, stats.f_favail, stats.f_files


def space_guard() -> None:
    free, slots, total_slots = disk_report()
    # Conservative preparation headroom, not a measured exact build-size claim.
    require(free >= 3 * 1024**3,
            'Not enough preparation headroom (less than 3 GiB free). No build started; share the disk report.')
    require(not total_slots or slots >= 100_000,
            'Too few free file slots (inodes) for a package install. No build started; share the disk report.')


def replacement(text: str, old: str, new: str) -> str:
    require(text.count(old) == 1, 'Pinned preparer patch did not match exactly.')
    return text.replace(old, new, 1)


def patched_preparer(raw: bytes) -> str:
    require(hashlib.sha256(raw).hexdigest() == HELPER_HASH, 'Original preparer hash mismatch.')
    text = raw.decode('utf-8')
    text = replacement(text,
        "root = Path(tempfile.mkdtemp(prefix='mezan-release-1674-'))",
        "root = Path('/tmp/mezan-release-1674-fg57cbud')")
    text = replacement(text, 'home.mkdir(mode=0o700)', 'home.mkdir(mode=0o700, exist_ok=True)')
    text = replacement(text,
        "review_branch = ('release/operational-cost-1674-v5-' +\n"
        "                     datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') +\n"
        "                     '-' + root.name.rsplit('-', 1)[-1])",
        'review_branch = ' + repr(BRANCH))
    start = text.index("        emit('=== ISOLATED SOURCE CLONE")
    end = text.index("        git('config', 'user.name'", start)
    text = text[:start] + (
        "        emit('=== RESUMING EXISTING SOURCE CLONE — /app UNCHANGED ===')\n"
        "        require(git('rev-parse', 'HEAD', cwd=stage) == SOURCE, 'Existing source changed.')\n"
        "        require(git('branch', '--show-current', cwd=stage) == review_branch, 'Review branch changed.')\n"
        "        require(git('status', '--porcelain=v1', '--untracked-files=all', cwd=stage) == '', 'Existing clone is dirty.')\n"
        ) + text[end:]
    text = replacement(text,
        "        emit('=== CLEAN-CLONE DEPLOYMENT REHEARSAL — NO LEASE OR PUBLISH ===')",
        "        emit('=== RELEASING SOURCE DEPENDENCIES BEFORE INDEPENDENT REHEARSAL ===')\n"
        "        _recovery_drop_generated(stage)\n"
        "        _recovery_space_guard()\n"
        "        emit('=== CLEAN-CLONE DEPLOYMENT REHEARSAL — NO LEASE OR PUBLISH ===')")
    compile(text, 'verified-prepare-mezan-1674', 'exec')
    return text


def main() -> None:
    require(ROOT.is_dir() and ROOT.resolve() == ROOT, 'Recorded failed preparation folder is missing or redirected.')
    require((ROOT / 'build-home').is_dir() and (ROOT / 'build-home').resolve() == ROOT / 'build-home',
            'Build home is missing or redirected.')
    require(not (ROOT / 'checkpoint.json').exists() and not (ROOT / 'rehearsal').exists(),
            'Preparation has progressed beyond the failed install. Preserve it; do not repeat.')
    require(git(APP, 'rev-parse', 'HEAD') == BASE, '/app source changed; stopped safely.')
    require(git(APP, 'branch', '--show-current') == 'hotfix/prod-snap-meta-final', '/app branch changed.')
    require(not git(APP, 'diff', '--name-only', 'HEAD', '--'), '/app has tracked changes; preserved.')
    state = json.loads(command([sys.executable, '-B', 'scripts/production_release_guard.py', 'status']))
    require(state.get('active') is False, 'Active release lease; no cleanup or preparation allowed.')
    stage = ROOT / 'source'
    require(git(stage, 'rev-parse', 'HEAD') == SOURCE, 'Failed clone no longer contains the reviewed source.')
    require(git(stage, 'branch', '--show-current') == BRANCH, 'Failed clone branch changed.')
    require(not git(stage, 'status', '--porcelain=v1', '--untracked-files=all'), 'Source clone contains changes; preserved.')
    raw = subprocess.run(['git', '-C', '/app', 'show', HELPER + ':scripts/prepare_mezan_release_1674.py'],
                         env=ENV, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True).stdout
    program = patched_preparer(raw)
    targets = []
    # Validate every candidate BEFORE removing the first directory.
    for root, expected in REVIEW_ROOTS.items():
        target = validate_generated(root, expected)
        if target:
            targets.append((root, target))
    disk_report()
    print('=== TASK-ONLY GENERATED DEPENDENCY CLEANUP ===', flush=True)
    for root, target in targets:
        assert_idle(root)
        print('Removing ONLY: ' + str(target), flush=True)
        shutil.rmtree(target)
    space_guard()
    print('=== RESUMING PINNED PREPARATION — NO NEW SOURCE CLONE ===', flush=True)
    exec(compile(program, 'verified-prepare-mezan-1674', 'exec'), {
        '__name__': '__main__',
        '_recovery_drop_generated': drop_generated,
        '_recovery_space_guard': space_guard,
    })


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print('RECOVERY_STOPPED:', exc, flush=True)
        print('No merge or deployment was performed. Keep all source/log files.', flush=True)
        raise SystemExit(1)
