"""Verify exact CI archive and full Git source before extracting private staging."""
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import subprocess
import tarfile
import zipfile

ROOT = Path('/root/mezan-preview-upload-owner-r2-20261026')
SOURCE = 'd1976b99e35c684843c7a6e69d365dd2af0eb2e8'


def main():
    status = json.loads(subprocess.check_output(['python', '-B', 'scripts/production_release_guard.py', 'status'], cwd='/app'))
    assert status['active'] is False
    data = (ROOT / 'preview.zip').read_bytes()
    assert len(data) == 30295164
    assert hashlib.sha256(data).hexdigest() == 'a7cc88d5a524df89973165d7bae53bdbc982118a7d392b0de91b060c8dca6f17'
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        assert set(z.namelist()) == {'preview-supplier-d1976b99e.tar.gz', 'SHA256SUMS'}
        tar = z.read('preview-supplier-d1976b99e.tar.gz')
        assert hashlib.sha256(tar).hexdigest() == z.read('SHA256SUMS').decode().split()[0]
    files = {}
    with tarfile.open(fileobj=io.BytesIO(tar), mode='r:gz') as package:
        for member in package:
            name = member.name.removeprefix('./')
            path = PurePosixPath(name)
            assert not path.is_absolute() and '..' not in path.parts and '\\' not in name
            assert member.isfile() or member.isdir()
            if member.isfile():
                assert name not in files
                files[name] = package.extractfile(member).read()
    manifest_data = files.pop('manifest.json')
    manifest = json.loads(manifest_data)
    assert set(files) == set(manifest)
    assert all(hashlib.sha256(value).hexdigest() == manifest[name] for name, value in files.items())
    actual = {name.removeprefix('source/'): hashlib.sha1(b'blob ' + str(len(value)).encode() + b'\0' + value).hexdigest()
              for name, value in files.items() if name.startswith('source/')}
    expected = {}
    for row in subprocess.check_output(['git', '-C', '/app', 'ls-tree', '-rz', SOURCE]).split(b'\0'):
        if not row:
            continue
        info, name = row.split(b'\t', 1)
        mode, kind, oid = info.split()
        assert kind == b'blob' and mode in {b'100644', b'100755'}
        expected[name.decode()] = oid.decode()
    assert actual == expected
    target = ROOT / 'verified-bundle'
    target.mkdir(mode=0o700)
    files['manifest.json'] = manifest_data
    for name, value in files.items():
        p = target / name
        p.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with p.open('xb') as out:
            p.chmod(0o600)
            out.write(value)
    print(json.dumps({'verified': True, 'source_git_sha': SOURCE,
                      'source_files': len(actual), 'files': len(manifest),
                      'tar_sha256': hashlib.sha256(tar).hexdigest(),
                      'manifest_sha256': hashlib.sha256(manifest_data).hexdigest()}))


if __name__ == '__main__':
    main()
