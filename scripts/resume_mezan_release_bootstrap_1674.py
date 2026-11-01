#!/usr/bin/env python3
"""Resume the recorded PR1674 preparation with the existing source-bootstrap mode.

The application source and release guards stay byte-for-byte unchanged.
Only the first A/A build uses MEZAN_RELEASE_BOOTSTRAP_INTENT=1; the frozen B
rehearsal must consume the reviewed intent without that variable. Reuse the
successful task-local cache/install. Never merge, publish, start a lease, or
write financial data. This launcher is not part of source commit A.
"""
from __future__ import annotations

import fcntl
import hashlib
import os
from pathlib import Path
import stat
import subprocess
import sys

APP = Path('/app')
ROOT = Path('/tmp/mezan-release-1674-fg57cbud')
STAGE = ROOT / 'source'
CACHE = ROOT / 'build-cache-on-tmp-v2'
TEMP = ROOT / 'build-temp-on-tmp-v2'
SOURCE = '667d94a3b01b7a59f58b753dfbf7d52c80764e02'
BRANCH = 'release/operational-cost-1674-v5-20261101T002214Z-fg57cbud'
PREPARER = 'ec60bdfe00e04b354df552020d48d43831cc5fff'
PREPARER_HASH = '0722b460ba4e9ba4c2af566cea1219cc372d5a88b5d994f86d458bd72c51b698'


def require(ok: bool, message: str) -> None:
    if not ok:
        raise RuntimeError(message)


def directory(path: Path, *, private: bool = False) -> None:
    info = path.lstat()
    require(stat.S_ISDIR(info.st_mode) and not stat.S_ISLNK(info.st_mode)
            and path.resolve(strict=True) == path and info.st_uid == os.geteuid(),
            'Unsafe or unexpected task directory: ' + str(path))
    if private:
        require(not stat.S_IMODE(info.st_mode) & 0o077, 'Task cache is not private.')


def free_space(path: Path, minimum: int = 5 * 1024**3) -> None:
    info = os.statvfs(path)
    available = info.f_bavail * info.f_frsize
    print('FREE_MIB', str(path), available // 1024**2, flush=True)
    require(available >= minimum, 'Insufficient headroom on ' + str(path))
    require(not info.f_files or info.f_favail >= 1000, 'Too few file slots.')


def idle_stage() -> None:
    for proc in Path('/proc').iterdir():
        if not proc.name.isdigit() or int(proc.name) == os.getpid():
            continue
        try:
            cwd = Path(os.readlink(proc / 'cwd'))
            args = (proc / 'cmdline').read_bytes().decode('utf-8', 'replace').split('\0')
        except (FileNotFoundError, ProcessLookupError):
            continue
        except PermissionError:
            try:
                if proc.stat().st_uid != os.geteuid():
                    continue
            except (FileNotFoundError, ProcessLookupError):
                continue
            raise RuntimeError('Cannot verify task process isolation; stopped.')
        require(cwd != STAGE and STAGE not in cwd.parents,
                'Source clone is in use by process ' + proc.name)
        require(not any(a == str(STAGE) or a.startswith(str(STAGE) + '/') for a in args),
                'Source clone is referenced by process ' + proc.name)


def verify_cache(output: str, configured: str) -> None:
    require(configured == str(CACHE), 'Build lost the task cache setting.')
    paths = [line.strip() for line in output.splitlines() if line.strip().startswith('/')]
    require(len(paths) == 1, 'Could not establish the actual Yarn cache path.')
    actual = Path(paths[0])
    require(CACHE in actual.parents and actual.resolve(strict=False) == actual,
            'Yarn cache is outside the reviewed task directory.')
    ancestor = actual
    while not ancestor.exists():
        ancestor = ancestor.parent
    require(ancestor.stat().st_dev == CACHE.stat().st_dev != APP.stat().st_dev,
            'Yarn cache is not on the separate temporary filesystem.')
    free_space(CACHE)
    print('VERIFIED_YARN_CACHE_ON_TMP', actual, flush=True)


def prepare_program(raw: bytes) -> str:
    require(hashlib.sha256(raw).hexdigest() == PREPARER_HASH,
            'Pinned preparer hash mismatch; nothing executed.')
    text = raw.decode('utf-8')

    def replace(old: str, new: str) -> None:
        nonlocal text
        require(text.count(old) == 1, 'Pinned preparation anchor mismatch.')
        text = text.replace(old, new, 1)

    replace("root = Path(tempfile.mkdtemp(prefix='mezan-release-1674-'))",
            'root = Path(' + repr(str(ROOT)) + ')')
    replace('home.mkdir(mode=0o700)', 'home.mkdir(mode=0o700, exist_ok=True)')
    replace("review_branch = ('release/operational-cost-1674-v5-' +\n"
            "                     datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') +\n"
            "                     '-' + root.name.rsplit('-', 1)[-1])",
            'review_branch = ' + repr(BRANCH))
    start_marker = "        emit('=== ISOLATED SOURCE CLONE"
    end_marker = "        git('config', 'user.name'"
    require(text.count(start_marker) == 1 and text.count(end_marker) == 1,
            'Pinned source-clone anchors changed.')
    start = text.index(start_marker)
    end = text.index(end_marker, start)
    text = text[:start] + (
        "        emit('=== REUSING VERIFIED SOURCE AND INSTALLED DEPENDENCIES ===')\n"
        "        require(git('rev-parse', 'HEAD', cwd=stage) == SOURCE, 'Existing source changed.')\n"
        "        require(git('branch', '--show-current', cwd=stage) == review_branch, 'Review branch changed.')\n"
        "        require(git('status', '--porcelain=v1', '--untracked-files=all', cwd=stage) == '', 'Source clone is dirty.')\n"
    ) + text[end:]

    # Existing supported mode: a clean pinned Git A must establish its own
    # manifest before B exists. Never apply this environment to the B build.
    replace("        emit('=== TWO MATCHING RELEASE BUILDS ===')\n"
            "        run(toolchain + ['exec', '--', 'yarn', '--cwd', 'frontend', 'build:release'],\n"
            "            cwd=stage, env=build_env)",
            "        require('MEZAN_RELEASE_BOOTSTRAP_INTENT' not in build_env and\n"
            "                'MEZAN_RELEASE_USE_REVIEWED_INTENT' not in build_env, 'Build modes leaked into common environment.')\n"
            "        bootstrap_env = dict(build_env, MEZAN_RELEASE_BOOTSTRAP_INTENT='1')\n"
            "        emit('=== SOURCE A BOOTSTRAP — EXACT CLEAN GIT SOURCE ===')\n"
            "        run(toolchain + ['exec', '--', 'node', '-e',\n"
            "            'const x=require(\"./frontend/scripts/write-build-meta.cjs\").captureSourceIdentity();'\n"
            "            'if(x.git_sha!==process.argv[1])throw Error(\"Source SHA mismatch\");'\n"
            "            'console.log(\"SOURCE_BOOTSTRAP_IDENTITY_PASS\",x.git_sha,x.source.file_count);', SOURCE],\n"
            "            cwd=stage, env=bootstrap_env)\n"
            "        emit('=== TWO MATCHING RELEASE BUILDS ===')\n"
            "        run(toolchain + ['exec', '--', 'yarn', '--cwd', 'frontend', 'build:release'],\n"
            "            cwd=stage, env=bootstrap_env)\n"
            "        del bootstrap_env")
    replace("        run(toolchain + ['exec', '--', sys.executable, '-B', 'scripts/verify_frontend_build.py',\n"
            "                         '--expected-git-sha', SOURCE], cwd=stage, env=build_env)",
            "        run(toolchain + ['exec', '--', sys.executable, '-B', 'scripts/verify_frontend_build.py',\n"
            "                         '--expected-git-sha', SOURCE], cwd=stage, env=build_env, capture=True)\n"
            "        emit('SOURCE_A_TWO_BUILD_VERIFICATION_PASS')")
    before_install = "        run(toolchain + ['exec', '--', 'yarn', '--cwd', 'frontend', 'install',"
    replace(before_install,
            "        cache_output = run(toolchain + ['exec', '--', 'yarn', '--cwd', 'frontend',\n"
            "                           '--silent', 'cache', 'dir'], cwd=stage, env=build_env, capture=True)\n"
            "        _verify_task_cache(cache_output, build_env['XDG_CACHE_HOME'])\n" + before_install)
    before_rehearsal = (
        "        run([sys.executable, '-B', 'scripts/emergent_deployment_adapter.py', 'build'],\n"
        "            cwd=rehearsal, env=build_env)")
    replace(before_rehearsal,
            "        require('MEZAN_RELEASE_BOOTSTRAP_INTENT' not in build_env, 'Bootstrap must not reach rehearsal.')\n"
            "        cache_output = run(toolchain + ['exec', '--', 'yarn', '--cwd', 'frontend',\n"
            "                           '--silent', 'cache', 'dir'], cwd=rehearsal, env=build_env, capture=True)\n"
            "        _verify_task_cache(cache_output, build_env['XDG_CACHE_HOME'])\n"
            "        emit('=== REHEARSAL USES FROZEN REVIEWED INTENT — NO BOOTSTRAP ===')\n" + before_rehearsal)
    replace("        run(['git', 'fetch', str(stage), intent_sha])",
            "        _check_git_headroom(stage / INTENT)\n"
            "        run(['git', 'fetch', str(stage), intent_sha])")
    compile(text, 'verified-pr1674-source-bootstrap-preparer', 'exec')
    return text


def git_headroom(intent_path: Path) -> None:
    # /app was nearly full: fail safely before importing the small intent object.
    free_space(APP, max(8 * 1024**2, 4 * intent_path.stat().st_size))


def main() -> None:
    for item in (ROOT, STAGE, STAGE / 'frontend', ROOT / 'build-home'):
        directory(item)
    require(not (ROOT / 'checkpoint.json').exists() and not (ROOT / 'rehearsal').exists(),
            'Preparation already progressed. Preserve the saved checkpoint; do not repeat.')
    require(ROOT.stat().st_dev != APP.stat().st_dev, 'Task directory shares the full /app filesystem.')
    free_space(ROOT)
    fd = os.open(ROOT / 'cache-relocation-v2.lock',
                 os.O_CREAT | os.O_RDWR | getattr(os, 'O_NOFOLLOW', 0), 0o600)
    try:
        info = os.fstat(fd)
        require(stat.S_ISREG(info.st_mode) and info.st_uid == os.geteuid()
                and info.st_nlink == 1, 'Unsafe task coordination lock.')
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        idle_stage()
        for item in (CACHE, TEMP):
            directory(item, private=True)
            require(item.stat().st_dev == ROOT.stat().st_dev, 'Task cache filesystem changed.')
        for name in ('MEZAN_RELEASE_BOOTSTRAP_INTENT', 'MEZAN_RELEASE_USE_REVIEWED_INTENT',
                     'GIT_DIR', 'GIT_WORK_TREE', 'GIT_INDEX_FILE', 'PYTHONHOME', 'PYTHONPATH'):
            os.environ.pop(name, None)
        os.environ.update(XDG_CACHE_HOME=str(CACHE), TMPDIR=str(TEMP),
                          TMP=str(TEMP), TEMP=str(TEMP), PYTHONDONTWRITEBYTECODE='1',
                          GIT_OPTIONAL_LOCKS='0', GIT_PAGER='cat', PAGER='cat')
        result = subprocess.run(
            ['git', '-C', str(APP), 'show', PREPARER + ':scripts/prepare_mezan_release_1674.py'],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
        require(result.returncode == 0, 'Pinned preparer not available in /app Git objects.')
        program = prepare_program(result.stdout)
        print('=== VERIFIED BOOTSTRAP FIX — APPLICATION SOURCE UNCHANGED ===', flush=True)
        print('Reusing task cache and installed packages; no manual deletion.', flush=True)
        exec(compile(program, 'verified-pr1674-source-bootstrap-preparer', 'exec'), {
            '__name__': '__main__',
            '_verify_task_cache': verify_cache,
            '_check_git_headroom': git_headroom,
        })
    finally:
        os.close(fd)


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print('BOOTSTRAP_RECOVERY_STOPPED:', exc, flush=True)
        print('Do not publish. Preserve source files, cache and preparation log.', flush=True)
        raise SystemExit(1)
