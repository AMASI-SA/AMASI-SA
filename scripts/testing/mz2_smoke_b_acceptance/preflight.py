"""Read-only preparation. Never imports server, starts services or contacts Mongo."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[3]


def git(*args):
    return subprocess.check_output(["git", "-C", str(ROOT), *args], text=True).strip()


def manifest():
    paths = [p for p in git("ls-files", "backend", "scripts/testing/mz2_smoke_b_acceptance").splitlines()
             if (ROOT / p).is_file()]
    # Include this prepared harness even before its first commit.
    paths = sorted(set(paths) | {p.relative_to(ROOT).as_posix() for p in Path(__file__).parent.glob("*.py")}
                   | {p.relative_to(ROOT).as_posix() for p in Path(__file__).parent.glob("*.md")})
    hashes = {p: hashlib.sha256((ROOT / p).read_bytes()).hexdigest() for p in paths}
    return {"head": git("rev-parse", "HEAD"), "tree": git("rev-parse", "HEAD^{tree}"),
            "status": git("status", "--porcelain"), "source_hashes": hashes,
            "source_manifest_sha256": hashlib.sha256(json.dumps(hashes, sort_keys=True).encode()).hexdigest()}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = {"schema": "mz2.smoke_b.acceptance.preflight.v1", "environment": "isolated_acceptance_only",
              "execution": "NOT_EXECUTED", "production_verified": False,
              "source": manifest(), "status": "PREPARED_NOT_EXECUTED_WAITING_FOR_C3",
              "startup_observations": [
                  {"file": "backend/server.py", "function": "_local_startup", "lines": [5293, 5309],
                   "reason": "Always starts Qoyod pipeline, Qoyod automatic sender and Salla token maintenance."},
                  {"file": "backend/integrations/qoyod/worker.py", "function": "start_worker",
                   "reason": "No supported environment opt-out; schedules polling task."},
                  {"file": "backend/integrations/qoyod_manual/auto_send.py", "function": "start_worker",
                   "reason": "No supported environment opt-out; schedules automatic sender."},
                  {"file": "backend/salla_integration/service.py", "function": "salla_token_maintenance_loop",
                   "reason": "Unconditional scheduled token maintenance."}],
              "next_step": "After C3 approval, run full app with supported test identity, empty integrations and test-only loopback network denial. Idle local polling is authorized; no production schedules or guard changes."}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(report["status"])


if __name__ == "__main__":
    main()
