"""Run the unchanged official verify3 with progress; restore Preview only on success."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path("/app")
PROOF_PARENT = Path("/root")
SOURCE = "ebb69057d25d6578ae0b28eb4e6aab1ab71f44e3"
DEPLOYMENT = "3bd31d56a93ce30f43d514008f545eb2ca3563c3"
RELEASE = "rg5-03f70cc373f6f22c5b7b904ac6de8278d736978c30b6ce74a8094d4dba9ff96c"
ACTOR = "android75-selected-groups-20261107"


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def main():
    os.chdir(ROOT.resolve(strict=True))
    environment = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
    guard = [sys.executable, "-B", "scripts/production_release_guard.py"]

    def state():
        return json.loads(subprocess.check_output([*guard, "status"], text=True, env=environment))

    lease = state()
    require(lease.get("active") is True and lease.get("actor") == ACTOR and
            (lease.get("source_git_sha"), lease.get("deployment_git_sha"), lease.get("release_id"))
            == (SOURCE, DEPLOYMENT, RELEASE), "Expected own release lease is absent; do not alter another lease")
    directory = Path(tempfile.mkdtemp(prefix="android75-verified-", dir=PROOF_PARENT))
    output = directory / "official-verification.json"
    errors = directory / "verification-stderr.log"
    print("Official verification proof:", directory, flush=True)
    with output.open("w", encoding="utf-8") as stdout, errors.open("w", encoding="utf-8") as stderr:
        process = subprocess.Popen([*guard, "verify", "--url", "https://mezansalla.com"],
                                   stdout=stdout, stderr=stderr, env=environment)
        while True:
            try:
                code = process.wait(timeout=15)
                break
            except subprocess.TimeoutExpired:
                print("Official three-round verification is running; proof:", output, flush=True)
    if code:
        print((errors.read_text(encoding="utf-8", errors="replace") +
               output.read_text(encoding="utf-8", errors="replace"))[-6000:], flush=True)
        raise RuntimeError("Official verification did not complete successfully; Preview restart skipped; inspect lease before retry")

    proof = json.loads(output.read_text(encoding="utf-8"))
    require(proof.get("verified") is True and proof.get("checks") == 3 and
            (proof.get("source_git_sha"), proof.get("deployment_git_sha"), proof.get("release_id"))
            == (SOURCE, DEPLOYMENT, RELEASE), "Official proof differs from the expected release")
    print(json.dumps({key: proof.get(key) for key in
                      ("verified", "checks", "release_id", "source_git_sha", "deployment_git_sha",
                       "protocol_version", "frontend_artifact_tree_sha256")}, indent=2), flush=True)
    current = state()
    print(json.dumps({key: current.get(key) for key in ("active", "actor", "release_id")}, indent=2), flush=True)
    require(current.get("active") is False, "A release lease is active; Preview restart deferred")
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    require(head == DEPLOYMENT, "Preview checkout changed; inspect before restarting it")
    status = subprocess.run(["supervisorctl", "status", "backend"],
                            capture_output=True, text=True, env=environment)
    fields = status.stdout.split()
    require(len(fields) >= 2 and fields[0] == "backend", "Inspect Preview service state")
    if fields[1] in {"STOPPED", "EXITED", "FATAL"}:
        subprocess.run(["supervisorctl", "start", "backend"], check=True, env=environment)
    else:
        require(fields[1] == "RUNNING", "Unexpected Preview state; inspect before restarting")
    subprocess.run(["supervisorctl", "status", "backend"], check=True, env=environment)


if __name__ == "__main__":
    main()
