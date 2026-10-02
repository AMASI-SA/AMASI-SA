import json,socket,time
from pathlib import Path
from pymongo import MongoClient
from pymongo.errors import AutoReconnect,ConnectionFailure
from bson.codec_options import CodecOptions
root=Path('C:/Users/amasi/mz2-c-evidence-20261001')
rows=json.loads((root/'final-fixture-cleanup-before.json').read_text(encoding='utf-8-sig'))
expected={4972:(27130,'/mz2-c-fixtures-20261001/replica','2026-10-01T23:07:46.'),25320:(27131,'/mz2-c-fixtures-20261001/standalone','2026-10-02T00:20:13.'),20732:(27132,'/mz2-c-fixtures-20261001/final-70221be96ca74a279b89ad22d826f6d8/replica','2026-10-02T00:52:40.'),13692:(27133,'/mz2-c-fixtures-20261001/final-70221be96ca74a279b89ad22d826f6d8/standalone','2026-10-02T00:52:43.')}
assert len(rows)==4
assert json.loads((root/'full-regression-final/finished.json').read_text())['exit_code']==0
report=[]
for row in rows:
    pid=row['ProcessId']; port,path,created=expected[pid]
    assert row['CreationDate'].startswith(created)
    assert row['ExecutablePath'].replace('\\','/').endswith('/mongodb-win32-x86_64-windows-8.0.12/bin/mongod.exe')
    cmd=row['CommandLine'].replace('\\','/')
    assert path in cmd and f'--port {port}' in cmd and '--bind_ip 127.0.0.1' in cmd
    client=MongoClient(f'mongodb://127.0.0.1:{port}/?directConnection=true',serverSelectionTimeoutMS=3000)
    # Windows serverStatus can contain non-UTF8 host diagnostic strings. Only
    # the integer PID is used; no business BSON is read with this codec.
    diagnostic_admin=client.get_database('admin',codec_options=CodecOptions(unicode_decode_error_handler='replace'))
    assert diagnostic_admin.command('serverStatus',codec_options=CodecOptions(unicode_decode_error_handler='replace'))['pid']==pid
    try:
        client.admin.command({'shutdown':1,'force':True,'timeoutSecs':5})
    except (AutoReconnect,ConnectionFailure):
        pass
    finally:
        client.close()
    for attempt in range(30):
        with socket.socket() as s:
            s.settimeout(.1)
            stopped=s.connect_ex(('127.0.0.1',port))!=0
        if stopped: break
        time.sleep(.1)
    assert stopped
    report.append({'pid':pid,'port':port,'creation_identity_verified':True,'server_pid_verified':True,'loopback_shutdown':True,'port_closed':True,'data_directory_preserved':True})
(root/'final-fixture-cleanup.json').write_text(json.dumps({'result':'PASS','fixtures':report,'production_access':False,'production_writes':0},indent=2),encoding='utf-8')
print(json.dumps(report))
