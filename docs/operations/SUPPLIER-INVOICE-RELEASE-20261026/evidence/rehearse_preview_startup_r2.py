"""Construct Preview ASGI app without starting lifespan, network or subprocesses."""
import hashlib
import json
import runpy
import sys
from pathlib import Path

ROOT = Path('/root/mezan-preview-supplier-r2-20261026')


def deny_side_effects(event, args):
    if event in {'socket.connect', 'socket.connect_ex', 'socket.sendto',
                 'subprocess.Popen', 'os.system', 'os.posix_spawn'}:
        raise RuntimeError('Preview rehearsal forbids network and child processes')


def main():
    prepared = json.loads((ROOT / 'PREPARED.json').read_text())
    adapter = ROOT / 'bundle/preview_password_runtime.py'
    assert hashlib.sha256(adapter.read_bytes()).hexdigest() == prepared['adapter_sha256']
    sys.dont_write_bytecode = True
    sys.addaudithook(deny_side_effects)
    namespace = runpy.run_path(str(adapter), run_name='preview_rehearsal')
    wrapped = namespace['create_app']()
    app = wrapped.app
    assert len(app.router.on_startup) == 1
    assert app.router.on_startup[0].__name__ == 'isolated_startup'
    assert getattr(app.state, 'readiness') == 'starting'
    print(json.dumps({'rehearsal_passed': True,
                      'source_git_sha': prepared['source_git_sha'],
                      'startup_handlers': ['isolated_startup'],
                      'lifespan_started': False,
                      'network_allowed': False,
                      'subprocesses_allowed': False}))


if __name__ == '__main__':
    main()
