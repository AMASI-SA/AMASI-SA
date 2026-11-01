#!/usr/bin/env python3
"""Prepare PR 1674's reviewed v5 intent in isolated clones, never deploy it.

This launcher is deliberately NOT part of source commit A. It may only create
and push a new review branch whose one change after A is the release intent.
It never checks out /app, resets/cleans files, starts a service, accesses a
financial database, creates a production lease, merges, or publishes.
"""
from __future__ import annotations
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from urllib.parse import urlparse

APP = Path('/app')
REPO = 'https://github.com/AMASI-SA/AMASI-SA.git'
TASK = 'feature/operational-open-cost-exposure-20261101'
PRODUCTION = 'hotfix/prod-snap-meta-final'
SOURCE = '667d94a3b01b7a59f58b753dfbf7d52c80764e02'
BASE = '3bac875cfbcbc7f66849edae83c478dd194d9c12'
INTENT = 'release/release-intent-v5.json'
HOST_ENV_KEYS = (
    'ALL_PROXY', 'HTTPS_PROXY', 'HTTP_PROXY', 'LANG', 'LC_ALL', 'NO_PROXY',
    'PATH', 'SSL_CERT_DIR', 'SSL_CERT_FILE', 'TEMP', 'TMP', 'TMPDIR', 'TZ',
    'all_proxy', 'https_proxy', 'http_proxy', 'no_proxy',
)

class PreparationError(RuntimeError):
    pass


def require(condition: bool, reason: str) -> None:
    if not condition:
        raise PreparationError(reason)


def sanitized(text: str) -> str:
    text = re.sub(r'(https?://)[^\s/@]+@', r'\1[REDACTED]@', text)
    return re.sub(r'\b(?:github_pat_[A-Za-z0-9_]+|gh[pousr]_[A-Za-z0-9_]+)\b',
                  '[REDACTED]', text)


def main() -> None:
    require(APP.is_dir(), '/app was not found. Run this in Emergent Terminal.')
    root = Path(tempfile.mkdtemp(prefix='mezan-release-1674-'))
    log_path = root / 'prepare.log'
    stage = root / 'source'
    rehearsal = root / 'rehearsal'
    home = root / 'build-home'
    home.mkdir(mode=0o700)
    review_branch = ('release/operational-cost-1674-v5-' +
                     datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') +
                     '-' + root.name.rsplit('-', 1)[-1])
    default_env = dict(os.environ, GIT_PAGER='cat', PAGER='cat',
                       GIT_TERMINAL_PROMPT='0', PYTHONDONTWRITEBYTECODE='1')
    for key in ('GIT_DIR', 'GIT_WORK_TREE', 'GIT_INDEX_FILE', 'PYTHONHOME', 'PYTHONPATH'):
        default_env.pop(key, None)
    log = log_path.open('a', encoding='utf-8')

    def emit(message: str) -> None:
        safe = sanitized(message.rstrip('\n'))
        print(safe, flush=True)
        log.write(safe + '\n')
        log.flush()

    def run(args: list[str], *, cwd: Path = APP, capture: bool = False,
            env: dict[str, str] | None = None) -> str:
        process = subprocess.Popen(args, cwd=cwd, env=env or default_env,
                                   stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                   text=True, encoding='utf-8', errors='replace')
        output = []
        assert process.stdout is not None
        for line in process.stdout:
            output.append(line)
            if not capture:
                emit(line)
        code = process.wait()
        if code:
            if capture:
                for line in output[-25:]:
                    emit(line)
            raise PreparationError('Command failed with exit code ' + str(code) +
                                   '; stopped without publishing.')
        return ''.join(output).strip()

    def git(*args: str, cwd: Path = APP) -> str:
        return run(['git', '--no-pager', *args], cwd=cwd, capture=True)

    def app_is_available() -> None:
        status = json.loads(run([sys.executable, '-B', 'scripts/production_release_guard.py',
                                 'status'], capture=True))
        require(status.get('active') is False, 'A production release lease is active; do not change it.')
        require(git('branch', '--show-current') == PRODUCTION, '/app branch changed; review required.')
        require(git('rev-parse', 'HEAD') == BASE, '/app source changed; review required.')
        run(['git', 'diff', '--quiet', 'HEAD', '--'], capture=True)

    def remote_heads_unchanged() -> None:
        text = run(['git', 'ls-remote', '--exit-code', REPO,
                    'refs/heads/' + TASK, 'refs/heads/' + PRODUCTION], capture=True)
        refs = {parts[1]: parts[0] for row in text.splitlines() if len(parts := row.split()) == 2}
        require(refs.get('refs/heads/' + TASK) == SOURCE, 'Remote task source changed; do not reuse this intent.')
        require(refs.get('refs/heads/' + PRODUCTION) == BASE, 'Remote Production base changed; review required.')

    try:
        emit('=== VERIFYING SOURCE AND INACTIVE RELEASE GUARD ===')
        emit('Preparation directory: ' + str(root))
        app_is_available()
        before_status = git('status', '--porcelain=v1', '--untracked-files=all')
        origin = git('remote', 'get-url', 'origin')
        parsed = urlparse(origin)
        origin_ok = (parsed.scheme == 'https' and parsed.hostname == 'github.com'
                     and parsed.path.rstrip('/').removesuffix('.git') == '/AMASI-SA/AMASI-SA')
        origin_ok = origin_ok or origin in {
            'git@github.com:AMASI-SA/AMASI-SA.git', 'git@github.com:AMASI-SA/AMASI-SA'}
        require(origin_ok, 'The /app origin does not match the intended GitHub repository.')
        author_name = git('config', '--get', 'user.name')
        author_email = git('config', '--get', 'user.email')
        require(bool(author_name and author_email), 'Existing Git author configuration is required.')
        remote_heads_unchanged()

        emit('=== ISOLATED SOURCE CLONE — /app FILES STAY UNCHANGED ===')
        run(['git', 'clone', '--no-checkout', '--single-branch', '--branch', TASK, REPO, str(stage)])
        require(git('rev-parse', 'refs/remotes/origin/' + TASK, cwd=stage) == SOURCE,
                'Cloned source differs from the reviewed commit.')
        run(['git', 'fetch', 'origin', 'refs/heads/' + PRODUCTION + ':refs/remotes/origin/' + PRODUCTION], cwd=stage)
        require(git('rev-parse', 'refs/remotes/origin/' + PRODUCTION, cwd=stage) == BASE,
                'Production changed while cloning.')
        run(['git', 'switch', '--create', review_branch, SOURCE], cwd=stage)
        git('config', 'user.name', author_name, cwd=stage)
        git('config', 'user.email', author_email, cwd=stage)
        source_base = run([sys.executable, '-B', '-c',
            'import sys; sys.path.insert(0,"scripts"); '
            'from emergent_deployment_adapter import resolve_candidate_source_base; '
            'print(resolve_candidate_source_base(sys.argv[1],sys.argv[2]))', BASE, SOURCE],
            cwd=stage, capture=True)
        require(re.fullmatch(r'[0-9a-f]{40}', source_base) is not None, 'Cannot resolve reviewed source base.')

        build_env = {k: v for k, v in default_env.items() if k in HOST_ENV_KEYS}
        cache = Path(os.environ.get('XDG_CACHE_HOME') or str(Path.home() / '.cache')).resolve()
        build_env.update(HOME=str(home), XDG_CACHE_HOME=str(cache), CI='false',
                         PYTHONDONTWRITEBYTECODE='1', GIT_TERMINAL_PROMPT='0',
                         REACT_APP_BACKEND_URL='https://mezansalla.com')
        toolchain = [sys.executable, '-B', 'scripts/frontend_release_toolchain.py']
        emit('=== GOVERNED NODE 22 / YARN TOOLCHAIN ===')
        run(toolchain + ['ensure'], cwd=stage, env=build_env)
        run(toolchain + ['exec', '--', 'yarn', '--cwd', 'frontend', 'install',
                         '--frozen-lockfile', '--non-interactive'], cwd=stage, env=build_env)
        emit('=== TWO MATCHING RELEASE BUILDS ===')
        run(toolchain + ['exec', '--', 'yarn', '--cwd', 'frontend', 'build:release'],
            cwd=stage, env=build_env)
        run(toolchain + ['exec', '--', sys.executable, '-B', 'scripts/verify_frontend_build.py',
                         '--expected-git-sha', SOURCE], cwd=stage, env=build_env)
        require(git('status', '--porcelain=v1', '--untracked-files=all', cwd=stage) == '',
                'Source clone changed during the build; intent not frozen.')

        emit('=== FREEZING REVIEWED RELEASE INTENT ===')
        candidate = root / 'release-intent-v5.json'
        run([sys.executable, '-B', 'scripts/emergent_deployment_adapter.py', 'freeze-intent',
             '--source-git-sha', SOURCE, '--source-base-git-sha', source_base,
             '--branch', PRODUCTION, '--output', str(candidate)],
            cwd=stage, env=build_env, capture=True)
        intent = json.loads(candidate.read_text(encoding='utf-8'))
        require(intent.get('source_git_sha') == SOURCE and intent.get('protocol_version') == 5,
                'Frozen intent differs from the approved source.')
        release_id = intent['runtime_identity']['release_id']
        require(re.fullmatch(r'rg5-[0-9a-f]{64}', release_id) is not None, 'Invalid release identity.')
        shutil.copyfile(candidate, stage / INTENT)
        require(git('diff', '--name-only', 'HEAD', '--', cwd=stage) == INTENT,
                'Only the intent file may differ from the tested source.')
        run(['git', 'add', '--', INTENT], cwd=stage)
        require(git('diff', '--cached', '--name-only', cwd=stage) == INTENT, 'Unexpected staged file.')
        run(['git', 'commit', '-m', 'release(v5): freeze reviewed operational cost source 667d94a3'], cwd=stage)
        intent_sha = git('rev-parse', 'HEAD', cwd=stage)
        require(git('diff', '--name-only', SOURCE, intent_sha, '--', cwd=stage) == INTENT,
                'Intent commit includes a source change; promotion refused.')
        run([sys.executable, '-B', '-c',
             'import sys; sys.path.insert(0,"scripts"); '
             'from emergent_deployment_adapter import load_release_intent; '
             'i=load_release_intent(); print("INTENT_CONTRACT_PASS",i["source_git_sha"])'],
            cwd=stage, env=build_env)

        summary = dict(source_git_sha=SOURCE, source_base_git_sha=source_base,
                       intent_git_sha=intent_sha, review_branch=review_branch,
                       release_id=release_id, preparation_directory=str(root),
                       log=str(log_path), production_changed=False, rehearsal_passed=False)
        (root / 'checkpoint.json').write_text(json.dumps(summary, indent=2) + '\n', encoding='utf-8')
        emit('=== SAVING REVIEW BRANCH ONLY — NOT PRODUCTION ===')
        app_is_available()
        remote_heads_unchanged()
        # Import the exact new object, then use the owner's existing /app Git
        # credential configuration. Do not copy or print credentials.
        run(['git', 'fetch', str(stage), intent_sha])
        run(['git', 'push', 'origin', intent_sha + ':refs/heads/' + review_branch])
        saved = git('ls-remote', '--exit-code', 'origin', 'refs/heads/' + review_branch)
        require(saved.split()[0] == intent_sha, 'Remote checkpoint verification failed.')
        emit('Review branch checkpoint saved; it is not a release approval.')
        emit('REVIEW_CHECKPOINT ' + json.dumps(summary, ensure_ascii=False, sort_keys=True))

        emit('=== CLEAN-CLONE DEPLOYMENT REHEARSAL — NO LEASE OR PUBLISH ===')
        run(['git', 'clone', '--no-local', '--no-checkout', '--single-branch',
             '--branch', review_branch, str(stage), str(rehearsal)])
        run(['git', 'checkout', '--detach', intent_sha], cwd=rehearsal)
        run([sys.executable, '-B', 'scripts/emergent_deployment_adapter.py', 'build'],
            cwd=rehearsal, env=build_env)
        require(git('status', '--porcelain=v1', '--untracked-files=all', cwd=rehearsal) == '',
                'Rehearsal changed tracked or untracked source files.')
        app_is_available()
        require(git('status', '--porcelain=v1', '--untracked-files=all') == before_status,
                '/app files changed concurrently; review before proceeding.')
        remote_heads_unchanged()
        summary['rehearsal_passed'] = True
        (root / 'checkpoint.json').write_text(json.dumps(summary, indent=2) + '\n', encoding='utf-8')
        emit('=== RELEASE_CANDIDATE_READY_NOT_DEPLOYED ===')
        emit(json.dumps(summary, ensure_ascii=False, indent=2))
        emit('No merge, production lease, service restart, financial write, or publish was performed.')
    except Exception as exc:
        emit('=== RELEASE_PREPARATION_STOPPED ===')
        emit(str(exc))
        emit('Files and log retained at: ' + str(root))
        emit('Do not publish. Send the error and any review branch checkpoint printed above.')
        raise SystemExit(1)
    finally:
        log.close()


if __name__ == '__main__':
    main()
