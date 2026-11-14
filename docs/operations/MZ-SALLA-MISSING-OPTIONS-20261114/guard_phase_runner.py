"""Disabled, pinned wrapper for one explicit official Release Guard phase.

Root must pin the original rehearsal directory and the exact SHA-256 of its
summary.json and preserved-before.json before enabling this file. Each command
writes a private receipt before any result validation or postcheck. A successful
guard followed by a failed postcheck is reported separately; never rerun a
stateful phase merely because this wrapper reports failure.

Usage after review: python -B guard_phase_runner.py {prepare,prepublish,status}
Verification additionally requires --ui-receipt PATH --ui-receipt-sha256 SHA256.
The root-authored UI JSON must contain kind=mezan_pr1923_ui_receipt_v1, our
source_git_sha/deployment_git_sha/release_id, publish_count=1,
observed_status="Deployment Succeeded", root_observed_newer=true,
prepublish_immediately_before_publish=true, publish_clicked_at and
success_observed_at (timezone-aware ISO timestamps), distinct nonempty
previous_success_evidence/new_success_evidence references, and the path/hash
of the completed prepublish summary as prepublish_summary_path/sha256.
This wrapper validates that supplied evidence; it does not observe or publish UI.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import signal
import stat
import subprocess
import sys
import tempfile
import time

PINS_REVIEWED = True
SOURCE = "7944a567fef519b3a1f9eb933fc463fbdd0c8575"
BASE = "77e4acf33e8f5432d97bda5d7208983f0fadc599"
INTENT_COMMIT = "ece0b24b0978c5064c4e605b577ad2722158f56a"
MERGE = "af9caabb3119aeb9cf450af0a8b89d376cef8c74"
TREE = "b5aba4c6ec2755fb27fe084c2b74f87f9d8fddf6"
INTENT_SHA256 = "a7d098c77d88b64250a937b58664085bd235a45ab994c34c37161d11c1fe260b"
RELEASE_ID = "rg5-d2259549478abdd0e0879d65d1de0defce94f7545b1f62f473799779c9151c1c"
ORIGINAL_EVIDENCE_DIR = "/tmp/mezan-pr1923-original-5a1jqt_2"
ORIGINAL_SUMMARY_SHA256 = "a5e9d3b3d037c92e815c23a6f9fe48414e58702b1910fd5fe7b2ae854362b1fa"
ORIGINAL_BEFORE_SHA256 = "115e2ced7f5947496ea4190f4f14b9d9a4581cdfdeceea39115032bccd3b37bd"
EXPECTED_PRESERVED_COUNT = 120
BRANCH = "hotfix/prod-snap-meta-final"
ACTOR = "codex-salla-missing-options-1923"
ROOT = Path("/app")
ENV = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", REACT_APP_BACKEND_URL="https://mezansalla.com")


def require(condition, reason):
    if not condition:
        raise RuntimeError(reason)


def sha(value):
    return hashlib.sha256(value).hexdigest()


def full_hex(value, length):
    return isinstance(value, str) and len(value) == length and all(c in "0123456789abcdef" for c in value)


def now():
    return datetime.now(timezone.utc).isoformat()


def timestamp(value):
    require(isinstance(value, str), "invalid_timestamp")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    require(parsed.tzinfo is not None, "timestamp_requires_timezone")
    return parsed.astimezone(timezone.utc)


def outside_workspace(path):
    return path.is_absolute() and path.resolve() == path and path != ROOT and ROOT not in path.parents


def pinned_json(path, expected):
    path = Path(path)
    require(outside_workspace(path) and full_hex(expected, 64), "invalid_evidence_pin")
    require(stat.S_ISREG(path.lstat().st_mode), "evidence_must_be_regular")
    data = path.read_bytes()
    require(sha(data) == expected, "evidence_digest_mismatch")
    return json.loads(data), data


def write_json(path, value):
    descriptor, name = tempfile.mkstemp(prefix="." + path.name + "-", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write((json.dumps(value, sort_keys=True, indent=2) + "\n").encode())
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        temporary.unlink(missing_ok=True)


class RequestedSignal(BaseException):
    pass


class Recorder:
    def __init__(self, evidence):
        self.evidence = evidence
        self.sequence = 0
        self.last_result = None
        self.last_receipt_durable = False
        self.deferred_signals = []

    def run(self, argv, label, timeout=600):
        self.sequence += 1
        prefix = self.evidence / f"{self.sequence:02d}-{label}"
        started_at, started = now(), time.monotonic()
        process = None
        code = termination = cleanup_error = None
        self.last_result = None
        self.last_receipt_durable = False
        self.deferred_signals = []

        def defer(signum, _frame):
            if signum not in self.deferred_signals:
                self.deferred_signals.append(signum)

        handlers = {sig: signal.signal(sig, defer) for sig in (signal.SIGINT, signal.SIGTERM)}
        try:
            with prefix.with_suffix(".stdout").open("wb") as out, prefix.with_suffix(".stderr").open("wb") as err:
                try:
                    if self.deferred_signals:
                        raise RequestedSignal()
                    process = subprocess.Popen(argv, cwd=ROOT, env=ENV, stdout=out, stderr=err,
                                               start_new_session=True)
                    deadline = time.monotonic() + timeout
                    while True:
                        if self.deferred_signals:
                            raise RequestedSignal()
                        remaining = deadline - time.monotonic()
                        if remaining <= 0:
                            raise subprocess.TimeoutExpired(argv, timeout)
                        try:
                            code = process.wait(timeout=min(0.25, remaining))
                            break
                        except subprocess.TimeoutExpired:
                            continue
                except BaseException as exc:
                    termination = (signal.Signals(self.deferred_signals[0]).name
                                   if isinstance(exc, RequestedSignal) else type(exc).__name__)
                    try:
                        if process is not None:
                            try:
                                os.killpg(process.pid, signal.SIGTERM)
                            except ProcessLookupError:
                                pass
                            try:
                                process.wait(timeout=10)
                            except subprocess.TimeoutExpired:
                                pass
                            try:
                                os.killpg(process.pid, signal.SIGKILL)
                            except ProcessLookupError:
                                pass
                            code = process.wait(timeout=10)
                    except BaseException as exc:
                        cleanup_error = type(exc).__name__
                    finally:
                        if process is not None:
                            code = process.poll()
                out.flush()
                err.flush()
                os.fsync(out.fileno())
                os.fsync(err.fileno())
            output, errors = prefix.with_suffix(".stdout").read_bytes(), prefix.with_suffix(".stderr").read_bytes()
            receipt = {"argv": argv, "cwd": str(ROOT), "exit_code": code,
                       "termination": termination, "cleanup_error": cleanup_error,
                       "signals_observed_before_receipt": [signal.Signals(sig).name for sig in self.deferred_signals],
                       "started_at": started_at, "completed_at": now(),
                       "elapsed_seconds": round(time.monotonic() - started, 3),
                       "stdout_sha256": sha(output), "stderr_sha256": sha(errors)}
            receipt_path = prefix.with_suffix(".json")
            self.last_result = receipt, output, receipt_path
            write_json(receipt_path, receipt)
            self.last_receipt_durable = True
            print(json.dumps({"step": label, "exit_code": code, "termination": termination,
                              "cleanup_error": cleanup_error}), flush=True)
        finally:
            for sig, handler in handlers.items():
                signal.signal(sig, handler)
        if self.deferred_signals:
            signum = self.deferred_signals[0]
            if callable(handlers[signum]):
                handlers[signum](signum, None)
            raise SystemExit(128 + signum)
        return receipt, output, receipt_path

    def checked(self, argv, label):
        receipt, output, _ = self.run(argv, label)
        require(receipt["exit_code"] == 0 and receipt["termination"] is None
                and receipt["cleanup_error"] is None, "command_failed_see_direct_receipt")
        return output.decode("utf-8")

    def git(self, *args):
        return self.checked(["git", *args], "git-" + args[0]).strip()

    def status(self):
        return json.loads(self.checked(guard_argv("status"), "guard-status"))


def guard_argv(phase):
    argv = [sys.executable, "-B", "scripts/production_release_guard.py", phase]
    if phase == "prepare":
        argv += ["--actor", ACTOR]
    if phase == "verify":
        argv += ["--url", "https://mezansalla.com"]
    return argv


def release_matches(value):
    require(isinstance(value, dict), "guard_result_is_not_object")
    for key, expected in {"source_git_sha": SOURCE, "git_sha": SOURCE,
                          "deployment_git_sha": MERGE, "release_id": RELEASE_ID,
                          "protocol_version": 5}.items():
        require(value.get(key) == expected, "guard_release_identity_mismatch")


def lease_matches(value, active):
    require(isinstance(value, dict) and value.get("active") is active
            and not value.get("incomplete_lease_directory"), "unexpected_lease_state")
    if active:
        release_matches(value)
        require(value.get("actor") == ACTOR and value.get("source_base_git_sha") == BASE
                and value.get("branch") == BRANCH, "lease_is_not_this_release")
        timestamp(value.get("prepared_at"))


def original_evidence():
    directory = Path(ORIGINAL_EVIDENCE_DIR)
    require(outside_workspace(directory) and directory.is_dir(), "invalid_original_evidence_directory")
    summary, summary_bytes = pinned_json(directory / "summary.json", ORIGINAL_SUMMARY_SHA256)
    records, record_bytes = pinned_json(directory / "preserved-before.json", ORIGINAL_BEFORE_SHA256)
    expected = {"rehearsal_passed": True, "source_git_sha": SOURCE, "source_base_git_sha": BASE,
                "deployment_git_sha": MERGE, "tree": TREE, "intent_sha256": INTENT_SHA256,
                "release_id": RELEASE_ID, "preserved_count": EXPECTED_PRESERVED_COUNT,
                "preserved_unchanged": True, "lease_created": False, "published": False}
    require(isinstance(summary, dict) and all(summary.get(k) == v for k, v in expected.items()),
            "original_rehearsal_is_not_exact_success")
    require(isinstance(records, list) and len(records) == EXPECTED_PRESERVED_COUNT,
            "invalid_original_preservation_manifest")
    require(summary.get("preserved_manifest_sha256") == sha(json.dumps(records, sort_keys=True).encode()),
            "original_preservation_binding_mismatch")
    built = summary.get("build_result") or {}
    require(built.get("built") is True and built.get("source_git_sha") == SOURCE
            and built.get("release_id") == RELEASE_ID, "original_adapter_result_mismatch")
    return records, summary_bytes, record_bytes


def inventory(recorder):
    paths = recorder.checked(["git", "ls-files", "--others", "--exclude-standard", "-z"], "preserved-paths")
    records = []
    for relative in sorted(p for p in paths.split("\0") if p):
        path = ROOT / relative
        info = path.lstat()
        if stat.S_ISREG(info.st_mode):
            data, kind = path.read_bytes(), "file"
        elif stat.S_ISLNK(info.st_mode):
            data, kind = os.readlink(path).encode(), "symlink"
        else:
            raise RuntimeError("unsupported_preserved_entry")
        records.append({"path": relative, "kind": kind, "mode": stat.S_IMODE(info.st_mode),
                        "bytes": len(data), "sha256": sha(data)})
    return records


def workspace_matches(recorder, original):
    require(recorder.git("branch", "--show-current") == BRANCH, "workspace_branch_changed")
    require(recorder.git("rev-parse", "HEAD") == MERGE
            and recorder.git("rev-parse", "HEAD^{tree}") == TREE, "workspace_head_or_tree_changed")
    require(recorder.git("status", "--porcelain", "--untracked-files=no") == "", "tracked_workspace_changed")
    require(sha((ROOT / "release/release-intent-v5.json").read_bytes()) == INTENT_SHA256, "intent_changed")
    require(inventory(recorder) == original, "preserved_files_changed")
    original_evidence()


def ui_evidence(args, lease, evidence):
    require(args.ui_receipt and args.ui_receipt_sha256, "verification_requires_pinned_ui_receipt")
    ui, data = pinned_json(args.ui_receipt, args.ui_receipt_sha256)
    required = {"kind": "mezan_pr1923_ui_receipt_v1", "source_git_sha": SOURCE,
                "deployment_git_sha": MERGE, "release_id": RELEASE_ID,
                "observed_status": "Deployment Succeeded", "root_observed_newer": True,
                "prepublish_immediately_before_publish": True}
    require(isinstance(ui, dict) and all(ui.get(k) == v for k, v in required.items()), "ui_receipt_mismatch")
    require(type(ui.get("publish_count")) is int and ui["publish_count"] == 1, "ui_publish_count_unproven")
    refs = [ui.get(k) for k in ("previous_success_evidence", "new_success_evidence")]
    require(all(isinstance(ref, str) and ref.strip() for ref in refs) and refs[0] != refs[1],
            "newer_ui_observation_unproven")
    prior, prior_bytes = pinned_json(ui["prepublish_summary_path"], ui["prepublish_summary_sha256"])
    prior_expected = {"phase": "prepublish", "phase_completed": True, "guard_exit_success": True,
                      "guard_result_validated": True, "postchecks_passed": True,
                      "source_git_sha": SOURCE, "deployment_git_sha": MERGE, "release_id": RELEASE_ID,
                      "original_summary_sha256": ORIGINAL_SUMMARY_SHA256,
                      "original_before_sha256": ORIGINAL_BEFORE_SHA256}
    require(isinstance(prior, dict) and all(prior.get(k) == v for k, v in prior_expected.items()),
            "successful_prepublish_evidence_missing")
    require(timestamp(lease["prepared_at"]) <= timestamp(prior["completed_at"])
            <= timestamp(ui["publish_clicked_at"]) < timestamp(ui["success_observed_at"]),
            "ui_observation_does_not_follow_this_lease_and_prepublish")
    (evidence / "root-ui-receipt.json").write_bytes(data)
    (evidence / "pinned-prepublish-summary.json").write_bytes(prior_bytes)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=("prepare", "prepublish", "verify", "status"))
    parser.add_argument("--ui-receipt")
    parser.add_argument("--ui-receipt-sha256")
    args = parser.parse_args()
    require(PINS_REVIEWED and full_hex(MERGE, 40), "deployment_pins_are_not_reviewed")
    require(args.phase == "verify" or not (args.ui_receipt or args.ui_receipt_sha256), "unexpected_ui_receipt")
    require(ROOT.is_dir() and not ROOT.is_symlink(), "unexpected_workspace_root")
    original, summary_bytes, record_bytes = original_evidence()

    def terminate_requested(signum, _frame):
        raise SystemExit(f"Runner interrupted by signal {signum}")
    signal.signal(signal.SIGTERM, terminate_requested)
    evidence = Path(tempfile.mkdtemp(prefix=f"mezan-pr1923-{args.phase}-", dir=Path(ORIGINAL_EVIDENCE_DIR).parent))
    (evidence / "pinned-original-summary.json").write_bytes(summary_bytes)
    (evidence / "pinned-original-before.json").write_bytes(record_bytes)
    recorder = Recorder(evidence)
    state = {"phase": args.phase, "source_git_sha": SOURCE, "source_base_git_sha": BASE,
             "intent_commit": INTENT_COMMIT, "deployment_git_sha": MERGE, "tree": TREE,
             "release_id": RELEASE_ID, "original_summary_sha256": ORIGINAL_SUMMARY_SHA256,
             "original_before_sha256": ORIGINAL_BEFORE_SHA256, "guard_invoked": False,
             "guard_exit_success": False, "guard_result_validated": False,
             "postchecks_passed": False, "phase_completed": False, "started_at": now()}
    print(json.dumps({"phase": args.phase, "evidence_directory": str(evidence)}), flush=True)
    stage = "prechecks"
    try:
        workspace_matches(recorder, original)
        before = None
        if args.phase != "status":
            before = recorder.status()
            lease_matches(before, args.phase != "prepare")
        if args.phase == "verify":
            ui_evidence(args, before, evidence)
            state.update(ui_receipt_sha256=args.ui_receipt_sha256, ui_evidence_source="root_provided")
        stage = "official_guard"
        state["guard_invoked"] = True
        try:
            receipt, output, path = recorder.run(guard_argv(args.phase), "official-" + args.phase,
                                                timeout=1800 if args.phase == "verify" else 600)
        finally:
            if recorder.last_result is not None:
                receipt, output, path = recorder.last_result
                state.update(guard_exit_code=receipt["exit_code"], guard_exit_success=receipt["exit_code"] == 0,
                             guard_receipt_path=str(path), guard_receipt_durable=recorder.last_receipt_durable,
                             guard_receipt_sha256=sha(path.read_bytes()) if recorder.last_receipt_durable else None,
                             guard_termination=receipt["termination"], guard_cleanup_error=receipt["cleanup_error"],
                             runner_deferred_signals=[signal.Signals(sig).name for sig in recorder.deferred_signals])
        require(receipt["termination"] is None and receipt["cleanup_error"] is None, "guard_interrupted_see_receipt")
        guard_error = None
        try:
            require(receipt["exit_code"] == 0, "guard_nonzero_exit")
            result = json.loads(output)
            if args.phase == "status":
                require(type(result.get("active")) is bool, "invalid_guard_status")
                lease_matches(result, result["active"])
            else:
                release_matches(result)
                if args.phase == "prepare":
                    lease_matches({"active": True, **result}, True)
                elif args.phase == "prepublish":
                    require(result.get("ready_to_publish") is True, "guard_not_ready_to_publish")
                else:
                    require(result.get("verified") is True and result.get("checks") == 3, "guard_verification_incomplete")
            state["guard_result_validated"] = True
        except Exception as exc:
            guard_error = type(exc).__name__
        stage = "postchecks"
        workspace_matches(recorder, original)
        after = (result if state["guard_result_validated"] else None) if args.phase == "status" else recorder.status()
        state["lease_active_after"] = after.get("active") if isinstance(after, dict) else None
        if state["guard_result_validated"]:
            if args.phase in ("prepare", "prepublish"):
                lease_matches(after, True)
                require(after == ({"active": True, **result} if args.phase == "prepare" else before),
                        "lease_changed_during_phase")
            elif args.phase == "verify":
                lease_matches(after, False)
        state.update(postchecks_passed=True, preserved_count=len(original), preserved_unchanged=True)
        stage = "official_guard_result"
        require(guard_error is None, "guard_failed_see_direct_receipt")
        state["phase_completed"] = True
    except BaseException as exc:
        state.update(failure_stage=stage, failure_type=type(exc).__name__)
        raise
    finally:
        state["completed_at"] = now()
        write_json(evidence / "summary.json", state)
        print(json.dumps(state, sort_keys=True), flush=True)


if __name__ == "__main__":
    main()
