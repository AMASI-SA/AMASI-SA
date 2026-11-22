cd /app && PYTHONDONTWRITEBYTECODE=1 python -B - <<'PY'
import hashlib
import os
import subprocess
import tempfile
from pathlib import Path
from scripts import production_release_guard as g

J = "9b4fb716b430c32073b5ea7abaeaff62646d9633"
E = "85372740b2e9ad3db453102413749ce03d807299"
H = "cdd6d5a5393af9f5518907c7b8999c1b8908a3256d3d5da4ca43b91217dc709a"
P = "docs/operations/OPERATIONAL-DEMAND-READS-20261122/MANUAL-HOST-STAGE-2069.py"

def call(args):
    try:
        r = subprocess.run(
            args, cwd="/app", capture_output=True, timeout=300,
            stdin=subprocess.DEVNULL,
            env=dict(os.environ, GIT_TERMINAL_PROMPT="0"),
        )
    except (OSError, subprocess.TimeoutExpired):
        raise SystemExit("STOP: staging command download could not complete") from None
    if r.returncode:
        raise SystemExit("STOP: staging command download exit=" + str(r.returncode))
    return r.stdout

if any(k in os.environ for k in (
    "GITHUB_ACTIONS", "GITHUB_SHA", "GITHUB_WORKSPACE", "GIT_DIR",
    "GIT_WORK_TREE", "GIT_COMMON_DIR", "GIT_INDEX_FILE",
    "GIT_OBJECT_DIRECTORY", "GIT_ALTERNATE_OBJECT_DIRECTORIES",
    "GIT_NAMESPACE", "GIT_CONFIG_PARAMETERS", "GIT_CONFIG_COUNT",
    "GIT_CONFIG", "GIT_CONFIG_SYSTEM", "GIT_CONFIG_GLOBAL",
    "GIT_TRACE", "GIT_TRACE_PACKET", "GIT_TRACE_CURL",
    "GIT_CURL_VERBOSE", "GIT_TRACE2_EVENT", "GIT_TRACE2_PERF",
)):
    raise SystemExit("STOP: unexpected CI or Git routing environment")
if g.REPO_ROOT.resolve() != Path("/app") or g.status() != {"active": False}:
    raise SystemExit("STOP: original shared guard is not inactive")
if call(["git", "--no-optional-locks", "rev-parse", "HEAD"]).decode().strip() != J:
    raise SystemExit("STOP: shared source changed")
if call(["git", "--no-optional-locks", "status", "--porcelain", "--untracked-files=no"]):
    raise SystemExit("STOP: shared tracked files changed")

runner = Path(tempfile.mkdtemp(prefix="mezan-pr2069-stage-command-", dir="/tmp")) / "repo"
print("LOADING_REVIEWED_STAGING_COMMAND", flush=True)
call(["git", "clone", "--local", "--no-hardlinks", "--no-checkout", "/app", str(runner)])
url = call(["git", "remote", "get-url", "origin"]).decode().strip()
call(["git", "-C", str(runner), "fetch", "--no-tags", "--no-write-fetch-head",
      "--no-auto-maintenance", url, E])
del url
code = call(["git", "-C", str(runner), "show", E + ":" + P])
if hashlib.sha256(code).hexdigest() != H:
    raise SystemExit("STOP: saved staging command checksum mismatch")
print("STAGING_COMMAND_VERIFIED", flush=True)
exec(compile(code, P, "exec"), globals())
PY

