"""Reviewed rehearsal launcher; confirms the predecessor's UI success first."""
import hashlib
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile

sys.dont_write_bytecode = True
N = "24270ebdfdebb83282389308a2349cdb27e2b8e4"
E = None  # Immutable evidence commit containing the enabled, reviewed helper.
H = None  # SHA-256 of that exact helper; never pin the disabled draft.
P = "docs/operations/OPERATIONAL-DEMAND-LATENCY-20261122/MANUAL-HOST-REHEARSAL-2076.py"


def main():
    if not (isinstance(E, str) and re.fullmatch(r"[0-9a-f]{40}", E)
            and isinstance(H, str) and re.fullmatch(r"[0-9a-f]{64}", H)):
        raise SystemExit("STOP: launcher disabled; reviewed evidence and helper checksum are required")
    rejected = (
        "GITHUB_ACTIONS", "GITHUB_SHA", "GITHUB_WORKSPACE", "GIT_DIR",
        "GIT_WORK_TREE", "GIT_COMMON_DIR", "GIT_INDEX_FILE", "GIT_OBJECT_DIRECTORY",
        "GIT_ALTERNATE_OBJECT_DIRECTORIES", "GIT_SHALLOW_FILE",
        "GIT_CONFIG_COUNT", "GIT_CONFIG_PARAMETERS", "GIT_NAMESPACE",
        "GIT_CONFIG", "GIT_CONFIG_SYSTEM", "GIT_CONFIG_GLOBAL", "GIT_CURL_VERBOSE",
    )
    if any(name in os.environ for name in rejected) or any(
            name.startswith("GIT_TRACE") for name in os.environ):
        raise SystemExit("STOP: unexpected CI, Git routing or tracing environment")
    try:
        with open("/dev/tty", "r", encoding="utf-8") as terminal_in, \
                open("/dev/tty", "w", encoding="utf-8") as terminal_out:
            terminal_out.write("هل ظهرت Deployment Succeeded الجديدة لتحديث العملاء والمخزون #2074 قبل تشغيل أمر التحقق الناجح؟\n"
                           "بعد رؤيتها اكتب 2074 OK للمتابعة، أو اضغط Enter للتوقف:\n> ")
            terminal_out.flush()
            confirmed = terminal_in.readline().strip() == "2074 OK"
    except OSError:
        raise SystemExit("STOP: interactive confirmation of the previous UI success is required") from None
    if not confirmed:
        raise SystemExit("STOP: previous Deployment Succeeded was not confirmed; no host change")
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", GIT_TERMINAL_PROMPT="0")

    def call(label, argv, *, cwd="/app", timeout=300):
        try:
            result = subprocess.run(argv, cwd=cwd, env=env, capture_output=True,
                stdin=subprocess.DEVNULL, timeout=timeout)
        except (OSError, subprocess.TimeoutExpired):
            raise SystemExit("STOP: " + label + " could not complete") from None
        if result.returncode:
            raise SystemExit("STOP: " + label + " exit=" + str(result.returncode))
        return result.stdout

    def git(*args, cwd="/app", timeout=300):
        return call("git " + args[0], ["git", "--no-optional-locks",
            "-c", "maintenance.auto=false", "-c", "gc.auto=0", *args],
            cwd=cwd, timeout=timeout)

    check = """
from pathlib import Path
from scripts import production_release_guard as guard
if guard.REPO_ROOT.resolve() != Path('/app'):
    raise SystemExit(2)
state = guard.status()
if state != {'active': False} or state.get('active') is not False:
    raise SystemExit(3)
print('INACTIVE')
"""
    if call("original guard status within five seconds",
            [sys.executable, "-B", "-c", check], timeout=5).strip() != b"INACTIVE":
        raise SystemExit("STOP: original guard status was not validated")
    if git("rev-parse", "HEAD", timeout=20).decode().strip() != N or git(
            "status", "--porcelain", "--untracked-files=no", timeout=20):
        raise SystemExit("STOP: expected clean shared source at reviewed N")
    runner = Path(tempfile.mkdtemp(prefix="mezan-pr2076-command-", dir="/tmp")) / "repo"
    print("LOADING_REVIEWED_REHEARSAL_COMMAND", flush=True)
    git("clone", "--local", "--no-hardlinks", "--no-checkout", "/app", str(runner))
    remote = git("remote", "get-url", "origin", timeout=20).decode().strip()
    git("fetch", "--no-tags", "--no-write-fetch-head", "--no-auto-maintenance",
        remote, E, cwd=runner)
    del remote
    code = git("show", E + ":" + P, cwd=runner, timeout=20)
    if hashlib.sha256(code).hexdigest() != H:
        raise SystemExit("STOP: reviewed rehearsal command checksum mismatch")
    print("REVIEWED_REHEARSAL_COMMAND_VERIFIED", flush=True)
    sys.argv = [P, "--diagnostic", "--base-ui-success-confirmed"]
    exec(compile(code, P, "exec"), globals())


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        raise SystemExit("STOP: launcher could not complete (" + type(error).__name__
                         + "); any isolated command directory is retained") from None
