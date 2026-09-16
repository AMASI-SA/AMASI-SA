#!/usr/bin/env python3
"""Repair only the isolated Emergent preview frontend; never mutate /app."""
import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess
import sys
import tarfile

SOURCE = Path("/app")
SHA = "9f8b16998f62cbdb08dc9361bc2e443223d27d17"
ROOT = Path("/opt/mezan-preview-runtime-20260916")
ORIGIN = "https://salla-analytics.preview.emergentagent.com"
CONF = Path("/etc/supervisor/conf.d/supervisord.conf")
SECTION = re.compile(r"(?ms)^\[program:frontend\][^\n]*\n.*?(?=^\[|\Z)")

def run(argv, **kwargs):
    return subprocess.run(argv, check=True, **kwargs)

def env():
    return {"PATH":"/usr/local/bin:/usr/bin:/bin", "HOME":str(ROOT),
            "LANG":"C.UTF-8", "NODE_ENV":"production", "REACT_APP_BACKEND_URL":ORIGIN}

def section(text):
    matches = list(SECTION.finditer(text))
    if len(matches) != 1:
        raise RuntimeError("Expected exactly one frontend service")
    return matches[0]

def set_value(text, name, value):
    result, count = re.subn(r"(?m)^" + re.escape(name) + r"\s*=.*$", name + "=" + value, text)
    if count != 1:
        raise RuntimeError("Expected exactly one " + name)
    return result

def prepare():
    if ROOT.exists():
        raise RuntimeError("Preview workspace exists; inspect before reusing")
    current = run(["git","-C",str(SOURCE),"rev-parse","HEAD"],capture_output=True,text=True).stdout.strip()
    if current != SHA:
        raise RuntimeError("Source moved; review before preparing preview")
    ROOT.mkdir(mode=0o700)
    archive = run(["git","-C",str(SOURCE),"archive",SHA,"frontend"],capture_output=True).stdout
    count = 0
    with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
        for member in tar:
            p = Path(member.name)
            if p.is_absolute() or ".." in p.parts or not p.parts or p.parts[0] != "frontend":
                raise RuntimeError("Unsafe archive member")
            if any(part == ".env" or part.startswith(".env.") for part in p.parts):
                continue
            if member.isdir():
                (ROOT/p).mkdir(parents=True,exist_ok=True)
            elif member.isfile():
                target = ROOT/p
                target.parent.mkdir(parents=True,exist_ok=True)
                target.write_bytes(tar.extractfile(member).read())
                target.chmod(member.mode & 0o777)
                count += 1
            else:
                raise RuntimeError("Unexpected link or special source entry")
    run(["cp","-a","--reflink=auto","--",str(SOURCE/"frontend/node_modules"),str(ROOT/"frontend/node_modules")])
    node = shutil.which("node")
    if not node:
        raise RuntimeError("Node not available")
    vite = ROOT/"frontend/node_modules/vite/bin/vite.js"
    with (ROOT/"build.log").open("w") as log:
        result = subprocess.run([node,str(vite),"build","--mode","preview"],cwd=ROOT/"frontend",env=env(),stdout=log,stderr=subprocess.STDOUT)
    if result.returncode:
        raise RuntimeError("Preview build failed; see isolated build.log")
    index = ROOT/"frontend/build/index.html"
    if not index.is_file() or not index.stat().st_size:
        raise RuntimeError("Missing preview index")
    metadata = {"environment":"preview","source_git_sha":SHA,"api_origin":ORIGIN,
                "index_sha256":hashlib.sha256(index.read_bytes()).hexdigest(),"tracked_frontend_files":count,
                "contains_production_release_identity":False}
    (ROOT/"frontend/build/preview-meta.json").write_text(json.dumps(metadata,indent=2)+"\n")
    if (ROOT/"frontend/build/build-meta.json").exists():
        raise RuntimeError("Preview must not claim a governed production identity")
    launcher = """import hashlib,json,os
from pathlib import Path
root=Path(__file__).resolve().parent
m=json.loads((root/'frontend/build/preview-meta.json').read_text())
assert m['environment']=='preview' and m['api_origin']==ORIGIN
assert hashlib.sha256((root/'frontend/build/index.html').read_bytes()).hexdigest()==m['index_sha256']
os.chdir(root/'frontend')
os.execve(NODE,[NODE,str(root/'frontend/node_modules/vite/bin/vite.js'),'preview','--host','0.0.0.0','--port','3000','--strictPort','--mode','preview'],ENV)
"""
    launcher = "ORIGIN="+repr(ORIGIN)+"\nNODE="+repr(node)+"\nENV="+repr(env())+"\n"+launcher
    (ROOT/"start.py").write_text(launcher)
    (ROOT/"repair.py").write_bytes(Path(__file__).read_bytes())
    print(json.dumps({"prepared":True,**metadata},indent=2),flush=True)

def activate():
    assert (ROOT/"start.py").is_file(), "Prepare first"
    original = CONF.read_text()
    old = section(original).group()
    if not re.search(r"(?m)^command\s*=\s*yarn start\s*$",old) or not re.search(r"(?m)^directory\s*=\s*/app/frontend\s*$",old):
        raise RuntimeError("Frontend configuration changed; inspect before activation")
    probe = socket.socket()
    try:
        probe.bind(("0.0.0.0",3000))
    finally:
        probe.close()
    new = set_value(old,"command",sys.executable+" "+str(ROOT/"start.py"))
    new = set_value(new,"directory",str(ROOT/"frontend"))
    updated = original[:section(original).start()] + new + original[section(original).end():]
    if (ROOT/"supervisor-before.conf").exists():
        raise RuntimeError("Backup already exists; inspect rather than overwrite")
    (ROOT/"supervisor-before.conf").write_text(original)
    (ROOT/"frontend-section-before.txt").write_text(old)
    (ROOT/"frontend-section-after.txt").write_text(new)
    tmp=CONF.with_name(CONF.name+".preview-repair.tmp")
    tmp.write_text(updated); shutil.copymode(CONF,tmp); os.replace(tmp,CONF)
    run(["supervisorctl","reread"])
    run(["supervisorctl","update","frontend"])
    run(["supervisorctl","status","frontend"])
    print(json.dumps({"activated":True,"only_supervisor_frontend_changed":True,"app_modified":False}))

def rollback():
    current=CONF.read_text()
    match=section(current)
    expected=(ROOT/"frontend-section-after.txt").read_text()
    if match.group()!=expected:
        raise RuntimeError("Frontend service changed after repair; inspect before rollback")
    restored=current[:match.start()]+(ROOT/"frontend-section-before.txt").read_text()+current[match.end():]
    tmp=CONF.with_name(CONF.name+".preview-rollback.tmp")
    tmp.write_text(restored);shutil.copymode(CONF,tmp);os.replace(tmp,CONF)
    run(["supervisorctl","reread"])
    run(["supervisorctl","update","frontend"])
    print(json.dumps({"rolled_back":True,"app_modified":False}))

if __name__ == "__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("action",choices=["prepare","activate","rollback"])
    args=parser.parse_args()
    {"prepare":prepare,"activate":activate,"rollback":rollback}[args.action]()
