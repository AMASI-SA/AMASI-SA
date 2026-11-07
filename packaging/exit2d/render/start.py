"""Render PID 1: migration barrier, Web proxy supervision and signal draining."""
import asyncio
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

sys.path.insert(0, '/opt/mezan/backend')


async def barrier(role):
    import independent_runtime as runtime
    server = runtime.load_server(role)
    try:
        key = runtime.release_key(server)
        for _ in range(90):
            try:
                if await asyncio.wait_for(runtime.migration_complete(server, key), 3):
                    return
            except Exception:
                pass
            await asyncio.sleep(2)
        raise RuntimeError('migration barrier timed out; service remains inactive')
    finally:
        server.client.close()


def supervise(commands):
    children = []
    stopping = False
    def terminate(signum=None, frame=None):
        nonlocal stopping
        stopping = True
        for child in children:
            if child.poll() is None:
                child.terminate()
    signal.signal(signal.SIGTERM, terminate)
    signal.signal(signal.SIGINT, terminate)
    result = 0
    try:
        for command in commands:
            if stopping:
                break
            children.append(subprocess.Popen(command))
        while not stopping:
            if any(child.poll() is not None for child in children):
                result = 1  # A daemon ending, even exit 0, is a failed service.
                break
            time.sleep(.1)
    finally:
        terminate()
        deadline = time.monotonic() + 20
        for child in children:
            try:
                child.wait(timeout=max(.1, deadline-time.monotonic()))
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait()
                result = 1
    return result


def main():
    role = sys.argv[1] if len(sys.argv) == 2 else ''
    # Do not retain the imported server graph in the Web supervisor. The
    # readiness probe exits completely BEFORE the real server is started.
    if role.startswith('barrier-'):
        target = role.removeprefix('barrier-')
        if target not in {'web', 'worker-reports', 'worker-sync', 'worker-accounting'}:
            raise RuntimeError('explicit hosting barrier role required')
        if os.environ.get('APP_ENV') != 'staging':
            raise RuntimeError('this image is synthetic hosting only')
        asyncio.run(barrier(target))
        return 0
    if role not in {'web', 'worker-reports', 'worker-sync', 'worker-accounting', 'migration'}:
        raise RuntimeError('explicit hosting role required')
    if os.environ.get('APP_ENV') != 'staging':
        raise RuntimeError('this image is synthetic hosting only; Production requires V5 packaging')
    if role == 'web':
        address = os.environ.get('MEZAN_REPORT_WORKER_ADDRESS', '')
        if os.environ.get('MEZAN_REPORT_WORKER_HOST'):
            address = os.environ['MEZAN_REPORT_WORKER_HOST'] + ':8000'
        if not address or '/' in address or '@' in address:
            raise RuntimeError('private report address required')
        os.environ['MEZAN_REPORT_WORKER_URL'] = 'http://' + address
    command = [sys.executable, '/opt/mezan/backend/independent_runtime.py', role]
    if role == 'migration':
        from independent_staging import validate
        validate()  # Before any connection, including replica-set initialization.
        from pymongo import MongoClient
        from pymongo.errors import OperationFailure
        client = MongoClient(os.environ['MONGO_URL'], directConnection=True,
                             serverSelectionTimeoutMS=3000)
        try:
            try:
                client.admin.command('replSetGetStatus')
            except OperationFailure as error:
                if error.code != 94:
                    raise
                client.admin.command('replSetInitiate', {'_id': 'rs0', 'members': [
                    {'_id': 0, 'host': os.environ['MEZAN_STAGING_MONGO_HOST'] + ':27017'}]})
            for _ in range(60):
                if client.admin.command('hello').get('isWritablePrimary'):
                    break
                time.sleep(1)
            else:
                raise RuntimeError('staging replica set not primary')
        finally:
            client.close()
        os.execv(sys.executable, command)
    subprocess.run([sys.executable, __file__, 'barrier-' + role], check=True)
    if role == 'web':
        for name in ('nginx-client', 'nginx-proxy', 'nginx-fastcgi', 'nginx-uwsgi', 'nginx-scgi'):
            Path('/tmp', name).mkdir(exist_ok=True)
        return supervise([[sys.executable, '/opt/render/smtp_capture.py'], command + ['--port', '8000'],
            ['nginx', '-c', '/opt/render/nginx.conf', '-g', 'daemon off;']])
    if role == 'worker-reports':
        command += ['--port', '8000']
    os.execv(sys.executable, command)


if __name__ == '__main__':
    sys.exit(main())
