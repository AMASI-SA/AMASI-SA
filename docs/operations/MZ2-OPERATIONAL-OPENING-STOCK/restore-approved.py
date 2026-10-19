import hashlib, json, os, shutil, subprocess, sys, tarfile, tempfile
from pathlib import Path, PurePosixPath

root = Path('/app')
os.chdir(root)
sys.path.insert(0, str(root))
from scripts import production_release_guard as guard

HEAD = '56919d2a8c48ca5fe85ee5543e7d86563d4c982f'
base = root / '.git/mz2-artifact-56919d2a'
targets = ('backend/release_identity.json', 'frontend/build',
           'frontend/.release/reproducible-build.json')
def git(*args):
    return subprocess.check_output(['git', *args], text=True).strip()
def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()
def safe(path):
    for part in (path, *path.parents):
        assert not part.is_symlink(), 'STOP: symlink ' + str(part)
def gate():
    assert guard.status().get('active') is False, 'STOP: active lease'
    assert git('rev-parse', 'HEAD') == HEAD, 'STOP: changed HEAD'
    assert git('branch', '--show-current') == 'hotfix/prod-snap-meta-final'
    assert not git('status', '--porcelain', '--untracked-files=no'), 'STOP: tracked changes'
    remote = git('ls-remote', 'origin', 'refs/heads/hotfix/prod-snap-meta-final').split()
    assert remote and remote[0] == HEAD, 'STOP: changed Production'

gate()
safe(base)
archive, manifest = base/'mz2-generated-release.tar.gz', base/'manifest.json'
safe(archive)
safe(manifest)
assert digest(archive) == 'd7432b24e772ac1d740949bec650f16580b2f719304970035879878554001072'
assert digest(manifest) == '925826682939c3849c0a45bbdc385c67971f0168e828a71995df1b4d0b511f05'
m = json.loads(manifest.read_text())
intent_path = root/'release/release-intent-v5.json'
intent = json.loads(intent_path.read_text())
assert m['deployment_sha'] == HEAD
assert digest(intent_path) == m['reviewed_intent_sha256']
assert m['source_sha'] == intent['source_git_sha'] == 'fdf719e67c0385fd73bea3d1931f32e4595822fc'
assert m['release_id'] == intent['runtime_identity']['release_id'] == 'rg5-4161b4475169f735ef9df7e52b2a8afbd6ca1799acc2f8bd22b14f3e3be32e99'
records = {r['path']: r for r in m['files']}
assert len(records) == len(m['files']) == 21
for name, record in records.items():
    parts = PurePosixPath(name)
    assert not parts.is_absolute() and '..' not in parts.parts and '\\' not in name
    assert name in (targets[0], targets[2]) or name.startswith('frontend/build/')
    assert record['mode'] == 0o644
for name in targets:
    safe(root/name)
    assert not git('ls-files', '--', name), 'STOP: tracked target'
    if (root/name).exists():
        assert (root/name).is_dir() if name == 'frontend/build' else (root/name).is_file()
required = 2 * sum(r['bytes'] for r in records.values()) + 128*1024*1024
assert shutil.disk_usage(root).free > required, 'STOP: insufficient disk space'
evidence = Path(tempfile.mkdtemp(prefix='restore-', dir=base))
stage = evidence/'staged'
with tarfile.open(archive, 'r:gz') as tf:
    members = tf.getmembers()
    assert len(members) == len(records) and {t.name for t in members} == set(records)
    for member in members:
        record = records[member.name]
        assert member.isfile() and member.size == record['bytes']
        content = tf.extractfile(member).read()
        assert hashlib.sha256(content).hexdigest() == record['sha256']
        p = stage/member.name
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open('xb') as output:
            output.write(content)
        p.chmod(record['mode'])
assert json.loads((stage/targets[0]).read_text()) == intent['runtime_identity']
gate()
print('RESTORE_EVIDENCE=' + str(evidence), flush=True)
for name in targets:
    destination = root/name
    safe(destination)
    if destination.exists():
        backup = evidence/'previous'/name
        backup.parent.mkdir(parents=True, exist_ok=True)
        destination.rename(backup)
    destination.parent.mkdir(parents=True, exist_ok=True)
    (stage/name).rename(destination)
for name, record in records.items():
    assert digest(root/name) == record['sha256']
identity = guard._read_runtime_identity()
assert guard.exact_json_equal(identity, guard._read_reviewed_runtime_identity())
guard._assert_local_identity_binding(identity, expected_git_sha=m['source_sha'])
with (evidence/'verify-packages.log').open('w') as log:
    subprocess.run([sys.executable, '-B', 'scripts/emergent_deployment_adapter.py',
                    'verify-packages'], stdout=log, stderr=subprocess.STDOUT, check=True)
gate()
print('RESTORE_SHA256=PASS; IDENTITY=PASS; PACKAGE_VERIFICATION=PASS')
print('DEPLOYMENT_SHA=' + HEAD)
print('RELEASE_ID=' + m['release_id'])
print('NO_PREPARE_OR_PUBLISH_PERFORMED')
