#!/usr/bin/env python3
"""Resume PR1674 preparation with task-local caches on /tmp's filesystem.

The previous recovery freed /tmp while /app and /root remained full. This
launcher relocates only this build's cache and temporary files. It does not
move/delete shared caches or change the governed application/release source.
The hash-pinned recovery retains all source, lease, clean-clone and push gates.
"""
from __future__ import annotations

import fcntl
import hashlib
import os
from pathlib import Path
import stat
import sys
import urllib.request

ROOT = Path('/tmp/mezan-release-1674-fg57cbud')
APP = Path('/app')
CACHE = ROOT / 'build-cache-on-tmp-v2'
TEMP = ROOT / 'build-temp-on-tmp-v2'
RECOVERY_SHA = '7c70ebddc96a9c9b5ff286ac2fbd5ad1e53da18a'
RECOVERY_HASH = '2f31ba81a71698a137de85722dabafd32dd9d4127aee4aed6539742cfb93815d'
RECOVERY_URL = ('https://raw.githubusercontent.com/AMASI-SA/AMASI-SA/' +
                RECOVERY_SHA + '/scripts/resume_mezan_release_space_1674.py')
MIN_FREE = 5 * 1024**3


def require(ok: bool, message: str) -> None:
    if not ok:
        raise RuntimeError(message)


def real_directory(path: Path, *, private: bool = False) -> None:
    info = path.lstat()
    require(stat.S_ISDIR(info.st_mode) and not stat.S_ISLNK(info.st_mode)
            and path.resolve(strict=True) == path, 'Unsafe directory: ' + str(path))
    require(info.st_uid == os.geteuid(), 'Unexpected directory owner: ' + str(path))
    if private:
        require(not stat.S_IMODE(info.st_mode) & 0o077,
                'Task cache must be private: ' + str(path))


def check_space(path: Path) -> None:
    info = os.statvfs(path)
    free = info.f_bavail * info.f_frsize
    print('CACHE_FILESYSTEM_FREE_GIB ' + str(path) + ' ' + format(free / 1024**3, '.2f'), flush=True)
    require(free >= MIN_FREE, 'Less than 5 GiB build headroom on ' + str(path))
    require(not info.f_files or info.f_favail >= 100_000,
            'Too few free file slots for build dependencies: ' + str(path))


def verify_yarn_cache(output: str, configured: str) -> None:
    require(configured == str(CACHE), 'Build discarded the reviewed temporary cache setting.')
    lines = [line.strip() for line in output.splitlines() if line.strip().startswith('/')]
    require(len(lines) == 1, 'Cannot prove Yarn cache path; package download not started.')
    actual = Path(lines[0])
    require(actual.is_absolute() and CACHE in actual.parents,
            'Yarn still targets a different filesystem; package download not started.')
    require(actual.resolve(strict=False) == actual, 'Yarn cache uses unexpected symlink indirection.')
    ancestor = actual
    while not ancestor.exists():
        ancestor = ancestor.parent
    require(ancestor.stat().st_dev == CACHE.stat().st_dev != APP.stat().st_dev,
            'Yarn cache is not on the separate temporary filesystem.')
    check_space(CACHE)
    print('VERIFIED_YARN_CACHE_ON_TMP ' + str(actual), flush=True)


def add_cache_probes(program: str) -> str:
    before_install = "        run(toolchain + ['exec', '--', 'yarn', '--cwd', 'frontend', 'install',"
    first_probe = (
        "        cache_output = run(toolchain + ['exec', '--', 'yarn', '--cwd', 'frontend',\n"
        "                                        '--silent', 'cache', 'dir'],\n"
        "                           cwd=stage, env=build_env, capture=True)\n"
        "        _recovery_verify_cache(cache_output, build_env['XDG_CACHE_HOME'])\n"
    )
    require(program.count(before_install) == 1, 'Pinned initial cache probe anchor changed.')
    program = program.replace(before_install, first_probe + before_install, 1)
    before_rehearsal = (
        "        run([sys.executable, '-B', 'scripts/emergent_deployment_adapter.py', 'build'],\n"
        "            cwd=rehearsal, env=build_env)"
    )
    second_probe = (
        "        cache_output = run(toolchain + ['exec', '--', 'yarn', '--cwd', 'frontend',\n"
        "                                        '--silent', 'cache', 'dir'],\n"
        "                           cwd=rehearsal, env=build_env, capture=True)\n"
        "        _recovery_verify_cache(cache_output, build_env['XDG_CACHE_HOME'])\n"
    )
    require(program.count(before_rehearsal) == 1, 'Pinned rehearsal cache probe anchor changed.')
    program = program.replace(before_rehearsal, second_probe + before_rehearsal, 1)
    compile(program, 'reviewed-preparer-with-cache-probes', 'exec')
    return program


def run_recovery(raw: bytes) -> None:
    require(hashlib.sha256(raw).hexdigest() == RECOVERY_HASH, 'Recovery hash differs. Nothing executed.')
    text = raw.decode('utf-8')
    # The old recovery executes the preparer in a new namespace. Supply only
    # this additional location check, without removing or weakening its guards.
    anchor = "        '_recovery_space_guard': space_guard,\n"
    require(text.count(anchor) == 1, 'Recovery namespace anchor changed.')
    text = text.replace(anchor, anchor + "        '_recovery_verify_cache': verify_yarn_cache,\n", 1)
    ns = {'__name__': 'verified_recovery_module', 'verify_yarn_cache': verify_yarn_cache}
    exec(compile(text, 'hash-verified-space-recovery', 'exec'), ns)
    old_patch = ns['patched_preparer']
    ns['patched_preparer'] = lambda source: add_cache_probes(old_patch(source))
    old_guard = ns['space_guard']

    def cache_guard() -> None:
        old_guard()
        for path in (CACHE, TEMP, ROOT / 'source' / 'frontend'):
            require(path.stat().st_dev == CACHE.stat().st_dev,
                    'Build path is not on the checked temporary filesystem: ' + str(path))
            check_space(path)

    ns['space_guard'] = cache_guard
    ns['main']()


def main() -> None:
    real_directory(ROOT)
    require(not (ROOT / 'checkpoint.json').exists() and not (ROOT / 'rehearsal').exists(),
            'A checkpoint already exists. Do not repeat preparation; review the saved result.')
    require(ROOT.stat().st_dev != APP.stat().st_dev,
            'Preparation is not on a filesystem separate from the full /app volume.')
    check_space(ROOT)
    # An exclusive task-only lock prevents overlapping retries of this launcher.
    flags = os.O_CREAT | os.O_RDWR | getattr(os, 'O_NOFOLLOW', 0)
    fd = os.open(ROOT / 'cache-relocation-v2.lock', flags, 0o600)
    try:
        info = os.fstat(fd)
        require(stat.S_ISREG(info.st_mode) and info.st_uid == os.geteuid()
                and info.st_nlink == 1, 'Unsafe recovery coordination file.')
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        for path in (CACHE, TEMP):
            path.mkdir(mode=0o700, exist_ok=True)
            real_directory(path, private=True)
            require(path.stat().st_dev == ROOT.stat().st_dev,
                    'Cache or temporary directory is on a different filesystem.')
        # These variables are allowlisted by the unchanged governed adapter;
        # a YARN_CACHE_FOLDER-only override would not survive its sanitization.
        os.environ.update(XDG_CACHE_HOME=str(CACHE), TMPDIR=str(TEMP),
                          TMP=str(TEMP), TEMP=str(TEMP))
        print('=== BUILD CACHE RELOCATED FOR THIS PROCESS ONLY ===', flush=True)
        print('XDG_CACHE_HOME=' + str(CACHE), flush=True)
        print('Temporary downloads and toolchain will use /tmp; shared caches stay untouched.', flush=True)
        with urllib.request.urlopen(RECOVERY_URL, timeout=45) as response:
            raw = response.read(200001)
        run_recovery(raw)
    finally:
        os.close(fd)


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print('CACHE_RECOVERY_STOPPED:', exc, flush=True)
        print('No automatic merge or publication was performed. Keep the logs.', flush=True)
        raise SystemExit(1)
