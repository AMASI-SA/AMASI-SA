#!/usr/bin/env python3
"""Restart only the existing Preview backend after its release identity exists."""
import configparser
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import urllib.error
import urllib.request
from urllib.parse import urlsplit

ROOT = Path("/opt/mezan-preview-runtime-20260916")
CONF = Path("/etc/supervisor/conf.d/supervisord.conf")
EXPECTED = "/root/.venv/bin/uvicorn server:app --host 0.0.0.0 --port 8001 --workers 1 --reload"
PATTERN = re.compile(r"(?ms)^\[program:backend\][^\n]*\n.*?(?=^\[|\Z)")

def main():
    assert ROOT.is_dir(), "Preview frontend must be prepared first"
    assert Path("/app/backend/release_identity.json").is_file(), "Release identity is still absent"
    original=CONF.read_text()
    config=configparser.RawConfigParser(strict=False);config.read_string(original)
    assert config.get("program:backend","command")==EXPECTED, "Inspect changed backend command"
    pid=int(subprocess.check_output(["supervisorctl","pid","backend"]))
    runtime=dict(x.split("=",1) for x in Path(f"/proc/{pid}/environ").read_text().split("\0") if "=" in x)
    values={}
    for line in Path("/app/backend/.env").read_text().splitlines():
        k,sep,v=line.partition("=")
        if sep and k.strip() in {"MONGO_URL","MONGODB_URI"}:
            values[k.strip()]=v.strip().strip('"').strip("'")
    values.update(runtime)
    mongo=values.get("MONGO_URL") or values.get("MONGODB_URI") or ""
    assert urlsplit(mongo).hostname in {"localhost","127.0.0.1","::1"}, "Expected isolated local Mongo"
    try:
        response=urllib.request.urlopen("http://127.0.0.1:8001/api/ready",timeout=10)
    except urllib.error.HTTPError as exc:
        response=exc
    status=json.loads(response.read())
    assert response.status==503 and status.get("phase")=="initialization_failed", "Do not restart a different runtime state"
    matches=list(PATTERN.finditer(original))
    assert len(matches)==1
    match=matches[0];before=match.group()
    command="/usr/bin/env PYTHONDONTWRITEBYTECODE=1 "+EXPECTED
    after,count=re.subn(r"(?m)^command\s*=.*$","command="+command,before)
    assert count==1
    backup=ROOT/"backend-section-before.txt"
    assert not backup.exists(), "Inspect prior restart before changing service again"
    backup.write_text(before)
    (ROOT/"backend-section-after.txt").write_text(after)
    updated=original[:match.start()]+after+original[match.end():]
    temporary=CONF.with_name(CONF.name+".preview-backend.tmp")
    temporary.write_text(updated);shutil.copymode(CONF,temporary);os.replace(temporary,CONF)
    subprocess.run(["supervisorctl","reread"],check=True)
    subprocess.run(["supervisorctl","update","backend"],check=True)
    print(json.dumps({"preview_backend_restarted":True,"release_validation_preserved":True,"python_bytecode_writes_disabled":True,"source_modified":False}))

if __name__=="__main__":
    main()
