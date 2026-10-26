"""Preview-only switch; default prints a sanitized plan. Never changes a database."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess

ROOT = Path('/root/mezan-preview-supplier-20261026')
CONF = Path('/etc/supervisor/conf.d/supervisord.conf')
EXPECTED_BEFORE = '2633d21f27509783d575c86827f5a0bff46a430ce65c29bba43cd0ca5642ac78'
DEPLOYMENT = '11a52dd933d7269cd80046219dcd93d5a6e62297'
SOURCE = 'b381a5929b6541ec8e243572700c9504290b7274'

def require(value, message):
    if not value:
        raise RuntimeError(message)

def sha(data):
    return hashlib.sha256(data).hexdigest()

def output(*command, cwd=None):
    return subprocess.check_output(command, cwd=cwd).decode().strip()

def guard():
    subprocess.run(['git', 'fetch', 'origin', 'hotfix/prod-snap-meta-final'], cwd='/app', check=True)
    for ref in ('HEAD', 'origin/hotfix/prod-snap-meta-final'):
        require(output('git', 'rev-parse', ref, cwd='/app') == DEPLOYMENT, 'Production changed')
    status = json.loads(output('python', '-B', 'scripts/production_release_guard.py', 'status', cwd='/app'))
    require(status.get('active') is False, 'Foreign release lease active')

def candidate(before):
    section = None
    found = set()
    lines = []
    for line in before.decode().splitlines(keepends=True):
        clean = line.strip()
        if clean.startswith('[') and clean.endswith(']'):
            section = clean[1:-1]
        if section in ('program:backend', 'program:frontend') and '=' in clean:
            key, old = clean.split('=', 1)
            if key in ('command', 'directory'):
                require((section, key) not in found, 'Duplicate runtime setting')
                found.add((section, key))
                name = section.split(':')[1]
                if key == 'command':
                    if name == 'backend':
                        require(old == '/root/.venv/bin/python -B /app/.worktrees/mz2-supplier-editor-fix-20261025/serve.py backend', 'Backend command changed')
                    else:
                        require(old == '/app/.worktrees/preview-runtime-state-20260919/toolcache/mezan-release-toolchains/v1/node-v22.23.2-yarn-1.22.22-linux-arm64/node/bin/node /app/frontend/scripts/start-governed-runtime.cjs', 'Frontend command changed')
                    new = f'/root/.venv/bin/python -B {ROOT}/serve.py {name}'
                else:
                    require(old == ('/app/.worktrees/mz2-supplier-editor-fix-20261025' if name == 'backend' else '/app/frontend'), 'Runtime directory changed')
                    new = str(ROOT)
                newline = '\r\n' if line.endswith('\r\n') else '\n'
                line = key + '=' + new + newline
        lines.append(line)
    require(len(found) == 4, 'Missing runtime setting')
    return ''.join(lines).encode()

def install(data, expected, suffix):
    require(CONF.read_bytes() == expected, 'Supervisor changed concurrently')
    temp = CONF.parent / ('.supervisord.preview-supplier-' + suffix)
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, stat.S_IMODE(CONF.stat().st_mode))
    with os.fdopen(fd, 'wb') as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    require(CONF.read_bytes() == expected, 'Supervisor changed before replacement')
    os.replace(temp, CONF)
    require(CONF.read_bytes() == data, 'Supervisor readback mismatch')

def restart_selected():
    subprocess.run(['supervisorctl', 'reread'], check=True)
    for name in ('backend', 'frontend'):
        subprocess.run(['supervisorctl', 'update', name], check=True)

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group()
    action.add_argument('--apply', action='store_true')
    action.add_argument('--rollback', action='store_true')
    args = parser.parse_args()
    require(os.getuid() == 0 and not CONF.is_symlink(), 'Unsafe supervisor ownership/path')
    require(ROOT.is_dir() and not ROOT.is_symlink(), 'Prepared runtime missing')
    prepared = json.loads((ROOT / 'PREPARED.json').read_text())
    require(prepared['source_git_sha'] == SOURCE and prepared['deployment_git_sha'] == DEPLOYMENT, 'Prepared identity mismatch')
    before = (ROOT / 'supervisor.before').read_bytes()
    require(sha(before) == EXPECTED_BEFORE, 'Backup mismatch')
    after = candidate(before)
    if args.rollback:
        guard()
        install(before, after, 'rollback.tmp')
        restart_selected()
        print(json.dumps({'rolled_back': True, 'database_changed': False}))
        return
    require(CONF.read_bytes() == before, 'Supervisor differs from reviewed backup')
    bundle = ROOT / 'bundle'
    manifest_bytes = (bundle / 'manifest.json').read_bytes()
    require(sha(manifest_bytes) == prepared['manifest_sha256'], 'Prepared manifest changed')
    for name, digest in json.loads(manifest_bytes).items():
        path = bundle / name
        require(path.is_file() and not path.is_symlink() and sha(path.read_bytes()) == digest, 'Prepared file changed')
    require(sha((bundle / 'preview_password_runtime.py').read_bytes()) == (bundle / 'adapter.sha256').read_text().strip(), 'Adapter changed')
    require((bundle / 'preview-session-signing.secret').read_bytes() == Path('/app/.worktrees/mz2-supplier-editor-fix-20261025/bundle/preview-session-signing.secret').read_bytes(), 'Session key changed')
    require((bundle / 'source/backend/.env').read_bytes() == Path('/app/.worktrees/mz2-supplier-editor-fix-20261025/bundle/source/backend/.env').read_bytes(), 'Training config changed')
    guard()
    report = {'source_git_sha': SOURCE, 'before_sha256': sha(before), 'after_sha256': sha(after), 'changed_settings': 4, 'production_publish': False, 'database_changed': False}
    if not args.apply:
        print(json.dumps(dict(report, validated_only=True)))
        return
    private = ROOT / 'supervisor.after'
    with private.open('xb') as stream:
        os.chmod(private, 0o600)
        stream.write(after)
    install(after, before, 'apply.tmp')
    try:
        restart_selected()
    except Exception:
        install(before, after, 'automatic-rollback.tmp')
        restart_selected()
        raise
    (ROOT / 'SWITCHED.json').write_text(json.dumps(dict(report, switched=True), indent=2) + '\n')
    print(json.dumps(dict(report, switched=True)))

if __name__ == '__main__':
    main()
