#!/usr/bin/env python3
"""Verify PR1950 once, only after the operator observes newer platform success.

This wrapper neither publishes nor clears a lease. Only the official successful
verifier may close its lease. Failure retains every receipt for review; no retry.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys

ROOT = Path("/app")
EVIDENCE = Path("/tmp/provider1950-publication-20261115")
PREPARE = EVIDENCE / "prepare.json"
GUARD = ROOT / "scripts/production_release_guard.py"
GUARD_SHA256 = "4e56f1f60f5f79931658b8f95a0aeb2269471025ca84f7e5b651cd284806349a"
SOURCE = "1bd44b6f186de8b40877fde686b5d2d7aab52805"
BASE = "c626af288d3823694b4f1d0efcc3670e4ee74d81"
MERGE = "236a428364bc8163a684795ed1be87e237476f18"
RELEASE = "rg5-58ace8c534fca5cd31d89a58bc9fd7746a79e25681660f8c32b3eded55a74117"
ACTOR = "chatgpt-provider1950-20261115-root"
PREPARED_AT = "2026-11-15T20:59:27.875265+00:00"
BRANCH = "hotfix/prod-snap-meta-final"
ORIGIN = "https://mezansalla.com"
BASELINE_VERSION = "1f931e6"


def require(condition, reason):
    if not condition:
        raise RuntimeError(reason)


def now():
    return datetime.now(timezone.utc).isoformat()


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def write_once(name, value):
    with (EVIDENCE / name).open("x", encoding="utf-8") as output:
        json.dump(value, output, ensure_ascii=False, sort_keys=True, indent=2)
        output.write("\n")


def official(name, arguments):
    command = [sys.executable, "-B", "scripts/production_release_guard.py", *arguments]
    started = now()
    write_once(name + "-start.json", {
        "args": command, "started_at": started, "wrapper_pid": os.getpid(),
        "wrapper_parent_pid": os.getppid(), "cwd": str(ROOT),
    })
    stdout, stderr = EVIDENCE / (name + ".stdout"), EVIDENCE / (name + ".stderr")
    with stdout.open("xb") as out, stderr.open("xb") as err:
        process = subprocess.Popen(command, cwd=ROOT, stdin=subprocess.DEVNULL,
            stdout=out, stderr=err, env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"))
        write_once(name + "-process.json", {"child_pid": process.pid, "wrapper_pid": os.getpid()})
        code = process.wait()  # No timeout, cancellation, retry, or second invocation.
    receipt = {"exit_code": code, "started_at": started, "finished_at": now(),
        "child_pid": process.pid, "stdout_sha256": sha256(stdout.read_bytes()),
        "stderr_sha256": sha256(stderr.read_bytes()),
        "stdout_bytes": stdout.stat().st_size, "stderr_bytes": stderr.stat().st_size}
    write_once(name + "-exit.json", receipt)
    require(code == 0, name + " failed; inspect private receipts without retrying")
    value = json.loads(stdout.read_text(encoding="utf-8"))
    require(isinstance(value, dict), name + " did not return a JSON object")
    return value, receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--platform-version", required=True)
    parser.add_argument("--platform-completed-at", required=True,
        help="Timezone-aware timestamp of explicitly observed successful platform completion")
    args = parser.parse_args()
    require(re.fullmatch(r"[0-9a-f]{7}", args.platform_version) is not None
        and args.platform_version != BASELINE_VERSION, "new successful platform version is required")
    completed = datetime.fromisoformat(args.platform_completed_at.replace("Z", "+00:00"))
    require(completed.tzinfo is not None
        and datetime.fromisoformat(PREPARED_AT) < completed <= datetime.now(timezone.utc),
        "platform completion timestamp must be observed after prepare and not in the future")
    require(ROOT not in Path(__file__).resolve().parents, "wrapper must remain outside /app")
    require(EVIDENCE.resolve(strict=True) == EVIDENCE
        and stat.S_ISDIR(EVIDENCE.lstat().st_mode), "receipt directory must be a real existing directory")
    require(PREPARE.resolve(strict=True) == PREPARE
        and stat.S_ISREG(PREPARE.lstat().st_mode), "official prepare receipt must be a regular file")
    require(stat.S_ISREG(GUARD.lstat().st_mode)
        and sha256(GUARD.read_bytes()) == GUARD_SHA256, "official guard bytes differ from reviewed control")
    prepare_bytes = PREPARE.read_bytes()
    prepared = json.loads(prepare_bytes)
    expected = {"actor": ACTOR, "prepared_at": PREPARED_AT, "source_git_sha": SOURCE,
        "git_sha": SOURCE, "source_base_git_sha": BASE, "deployment_git_sha": MERGE,
        "release_id": RELEASE, "branch": BRANCH, "protocol_version": 5}
    require(isinstance(prepared, dict) and "active" not in prepared
        and all(prepared.get(key) == value for key, value in expected.items()),
        "official prepare receipt is not the exact owned PR1950 lease")
    identity = prepared.get("runtime_identity")
    require(isinstance(identity, dict) and all(identity.get(key) == expected[key]
        for key in ("source_git_sha", "source_base_git_sha", "release_id", "branch", "protocol_version")),
        "prepared runtime identity differs")
    os.umask(0o077)
    started = now()
    # Exclusive fixed name claims this attempt before any official command runs.
    # An existing receipt blocks a second invocation, including after failure.
    write_once("official-verify-wrapper-start.json", {
        **expected, "started_at": started, "wrapper_pid": os.getpid(),
        "wrapper_parent_pid": os.getppid(), "platform_version": args.platform_version,
        "platform_completed_at": args.platform_completed_at,
        "platform_completion_provenance": "operator observed successful platform UI",
        "prepare_sha256": sha256(prepare_bytes), "wrapper_sha256": sha256(Path(__file__).read_bytes()),
    })
    try:
        before, before_receipt = official("official-verify-status-before", ["status"])
        require(before == {"active": True, **prepared}, "active lease differs from exact owned prepare receipt")
        result, verification = official("official-verify", ["verify", "--url", ORIGIN])
        require(result.get("verified") is True and result.get("checks") == 3
            and all(result.get(key) == expected[key] for key in
                ("source_git_sha", "git_sha", "source_base_git_sha", "deployment_git_sha", "release_id", "protocol_version"))
            and result.get("url") == ORIGIN, "official verification result is not exact PR1950 success")
        after, after_receipt = official("official-verify-status-after", ["status"])
        require(after == {"active": False}, "official status is not cleanly inactive after verification")
        summary = {"ok": True, "verified": True, "checks": 3, "source_git_sha": SOURCE,
            "deployment_git_sha": MERGE, "release_id": RELEASE, "actor": ACTOR,
            "prepared_at": PREPARED_AT, "platform_version": args.platform_version,
            "platform_completed_at": args.platform_completed_at, "wrapper_started_at": started,
            "verify_started_at": verification["started_at"], "verify_finished_at": verification["finished_at"],
            "inactive_observed_at": after_receipt["finished_at"], "finished_at": now(),
            "prepare_sha256": sha256(prepare_bytes),
            "status_before_sha256": before_receipt["stdout_sha256"],
            "verify_stdout_sha256": verification["stdout_sha256"],
            "verify_stderr_sha256": verification["stderr_sha256"],
            "status_after_sha256": after_receipt["stdout_sha256"],
            "boot_started_at_observations": result.get("boot_started_at_observations"),
            "active_lease": False, "evidence_directory": str(EVIDENCE)}
        write_once("official-verify-final.json", summary)
        print(json.dumps(summary, sort_keys=True), flush=True)
    except Exception as error:
        failure = {"ok": False, "error_type": type(error).__name__, "reason": str(error),
            "observed_at": now(), "evidence_directory": str(EVIDENCE)}
        write_once("official-verify-wrapper-failed.json", failure)
        print(json.dumps(failure, sort_keys=True), flush=True)
        raise


if __name__ == "__main__":
    main()

