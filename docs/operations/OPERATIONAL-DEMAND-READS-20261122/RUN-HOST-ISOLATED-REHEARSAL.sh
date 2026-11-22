cd /app && PYTHONDONTWRITEBYTECODE=1 python -B - <<'PY'
import hashlib
import os
import subprocess
import tempfile
from pathlib import Path
from scripts import production_release_guard as g

J = "9b4fb716b430c32073b5ea7abaeaff62646d9633"
E = "c01f238687e3193f096d55e6e0deb34fa8a57ec5"
H = "475ec8ea80ad8ba69e69f26f940851b379b3e000749d2e76aadab1d51f404768"
P = "docs/operations/OPERATIONAL-DEMAND-READS-20261122/MANUAL-HOST-ISOLATED-REHEARSAL.py"

def call(args):
    try:
        p = subprocess.run(
            args, cwd="/app", capture_output=True, timeout=300,
            stdin=subprocess.DEVNULL,
            env=dict(os.environ, GIT_TERMINAL_PROMPT="0"),
        )
    except (OSError, subprocess.TimeoutExpired):
        raise SystemExit("STOP: rehearsal download could not complete") from None
    if p.returncode:
        raise SystemExit("STOP: rehearsal download exit=" + str(p.returncode))
    return p.stdout

if any(k in os.environ for k in (
    "GITHUB_ACTIONS", "GITHUB_SHA", "GITHUB_WORKSPACE", "GIT_DIR",
    "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_OBJECT_DIRECTORY",
    "GIT_ALTERNATE_OBJECT_DIRECTORIES",
)):
    raise SystemExit("STOP: unexpected CI or Git routing environment")
if g.status() != {"active": False}:
    raise SystemExit("STOP: original guard is not inactive")
if call(["git", "--no-optional-locks", "rev-parse", "HEAD"]).decode().strip() != J:
    raise SystemExit("STOP: shared source changed")
if call(["git", "--no-optional-locks", "status", "--porcelain", "--untracked-files=no"]):
    raise SystemExit("STOP: shared tracked files changed")

runner = Path(tempfile.mkdtemp(prefix="mezan-pr2069-command-", dir="/tmp")) / "repo"
print("LOADING_REVIEWED_COMMAND", flush=True)
call(["git", "clone", "--local", "--no-hardlinks", "--no-checkout", "/app", str(runner)])
url = call(["git", "remote", "get-url", "origin"]).decode().strip()
call(["git", "-C", str(runner), "fetch", "--no-tags", "--no-write-fetch-head",
      "--no-auto-maintenance", url, E])
del url
code = call(["git", "-C", str(runner), "show", E + ":" + P])
if hashlib.sha256(code).hexdigest() != H:
    raise SystemExit("STOP: saved command checksum mismatch")
print("REVIEWED_COMMAND_VERIFIED", flush=True)
exec(compile(code, P, "exec"), globals())
PY
