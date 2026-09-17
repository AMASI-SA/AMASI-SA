"""Install the tested Excel read overlay in isolated Preview only.

Does not alter /app source or Supervisor configuration. Restart backend after
running. Rollback: restore preview_password_runtime.py.before-excel and restart.
"""
from pathlib import Path
import hashlib
import json
import shutil
import socket
import subprocess

ROOT = Path('/opt/mezan-preview-runtime-20260916')
OVERLAY = Path('/opt/mezan-preview-excel-20260917')
CANDIDATE = Path('/tmp/mezan-preview-excel-20260917/order_engine')
SOURCE_SHA = '428fceb48f284a1158a4f0b243879a9d2f02fa98'
BASE_SHA = '836d36b831c1cd8aa9915f543f159b4e160ea438'


def main():
    assert socket.gethostname() == 'agent-env-f5e6b93a-68a2-4155-84ad-e55b4fa936d3'
    assert json.loads(subprocess.check_output(['python', '/app/scripts/production_release_guard.py', 'status']))['active'] is False
    assert subprocess.check_output(['git', '-C', '/app', 'rev-parse', 'HEAD'], text=True).strip() == BASE_SHA
    assert not OVERLAY.exists(), 'Overlay already exists; inspect rather than overwrite'
    adapter = ROOT / 'preview_password_runtime.py'
    before = adapter.read_text()
    needle = '    sys.path.insert(0, str(BACKEND))\n'
    assert before.count(needle) == 1 and 'MEZAN_PREVIEW_EXCEL_ORDERS' not in before
    shutil.copytree(CANDIDATE, OVERLAY / 'order_engine', ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    manifest = {str(p.relative_to(OVERLAY)): hashlib.sha256(p.read_bytes()).hexdigest()
                for p in sorted(OVERLAY.rglob('*.py'))}
    (OVERLAY / 'manifest.json').write_text(json.dumps({'source_sha': SOURCE_SHA, 'base_sha': BASE_SHA, 'files': manifest}, indent=2))
    addition = '''    # Explicit read-only Preview Excel projection, with runtime integrity checks.
    excel_root = Path('/opt/mezan-preview-excel-20260917')
    excel_manifest = json.loads((excel_root / 'manifest.json').read_text())
    import hashlib
    for relative, digest in excel_manifest['files'].items():
        require(hashlib.sha256((excel_root / relative).read_bytes()).hexdigest() == digest,
                'Preview Excel overlay hash mismatch')
    os.environ['MEZAN_PREVIEW_EXCEL_ORDERS'] = '1'
    sys.path.insert(0, str(excel_root))
'''
    backup = adapter.with_suffix('.py.before-excel')
    assert not backup.exists()
    shutil.copy2(adapter, backup)
    adapter.write_text(before.replace(needle, needle + addition))
    for meta_path in [ROOT / 'frontend/build/preview-meta.json',
                      Path('/opt/mezan-preview-signout-20260916/frontend/build/preview-meta.json')]:
        meta = json.loads(meta_path.read_text())
        assert meta['environment'] == 'preview'
        meta['excel_projection_source_sha'] = SOURCE_SHA
        meta['excel_projection_backend_base_sha'] = BASE_SHA
        meta_path.write_text(json.dumps(meta, indent=2) + '\n')
    print('Preview Excel overlay installed; backend restart required; Production unchanged')


if __name__ == '__main__':
    main()
