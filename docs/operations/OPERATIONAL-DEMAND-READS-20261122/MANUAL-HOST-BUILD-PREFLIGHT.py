import json
import os
import shutil
import subprocess
from pathlib import Path
from scripts import production_release_guard as g
from scripts import emergent_deployment_adapter as a

J = "9b4fb716b430c32073b5ea7abaeaff62646d9633"

def git(*args):
    return subprocess.check_output(
        ["git", "--no-optional-locks", "-C", "/app", *args],
        text=True, timeout=20,
    ).strip()

if git("rev-parse", "HEAD") != J or git("status", "--porcelain", "--untracked-files=no"):
    raise SystemExit("STOP: expected clean current production J")

state = g.status()
print("GUARD", json.dumps({
    "active": state.get("active"),
    "incomplete": state.get("incomplete_lease_directory", False),
}), flush=True)
if state != {"active": False}:
    raise SystemExit("STOP: original guard is not fully inactive")

cache_home = a._toolchain_cache_home(dict(os.environ))
for label, location in (("app", "/app"), ("tmp", "/tmp"), ("toolchain_cache", cache_home)):
    p = Path(location)
    while not p.exists():
        p = p.parent
    usage = shutil.disk_usage(p)
    print("DISK", json.dumps({
        "scope": label, "free_mib": round(usage.free / 1048576, 1),
        "total_mib": round(usage.total / 1048576, 1),
    }), flush=True)

caches, loose = [], []
for name in ("backend", "scripts", ".github/workflows", ".github/actions"):
    if Path(name).is_symlink():
        raise SystemExit("STOP: protected root is a symlink")
    for base, dirs, files in os.walk(name, followlinks=False):
        caches.extend(str(Path(base) / n) for n in dirs if n == "__pycache__")
        dirs[:] = [n for n in dirs if n != "__pycache__"]
        loose.extend(str(Path(base) / n) for n in files if Path(n).suffix.lower() in {".pyc", ".pyo"})

print("CACHES", json.dumps({
    "directories": len(caches), "sample": sorted(caches)[:8],
    "loose_bytecode": len(loose), "loose_sample": sorted(loose)[:8],
}), flush=True)
for name in ("frontend/build", "frontend/node_modules", "frontend/.release", "backend/release_identity.json"):
    p = Path(name)
    print("OWNED_OUTPUT", json.dumps({
        "path": name, "present": os.path.lexists(p), "symlink": p.is_symlink(),
    }), flush=True)

p = Path("frontend/.release")
if p.is_dir() and not p.is_symlink():
    names = sorted(x.name for x in p.iterdir())
    print("RELEASE_ENTRIES", json.dumps({"count": len(names), "names": names[:12]}), flush=True)

try:
    intent = a.load_release_intent()
except Exception as error:
    print("INTENT_PREFLIGHT_FAILED", type(error).__name__, str(error)[:600], flush=True)
    raise SystemExit(1)

print("INTENT_PREFLIGHT_PASSED", json.dumps({
    "head": J, "source": intent["source_git_sha"],
    "release_id": intent["runtime_identity"]["release_id"],
}), flush=True)
