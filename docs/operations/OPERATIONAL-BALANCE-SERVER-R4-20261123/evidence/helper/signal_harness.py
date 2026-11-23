#!/usr/bin/env python3
"""Bounded, owned-process review of the actual helper's signal cleanup.

Does not call helper.main. The actual build-window AST and actual report
except/finally are exercised with Popen redirected to an owned Python fake.
All created files stay below this script's directory; no /app operation exists.
"""
import ast
import copy
import ctypes
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone
from types import SimpleNamespace

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parent
HELPER = ROOT.parent / "r4-release-evidence/HOST-REHEARSE-2082.py"
EXPECTED_SHA = "6f85c5c53418d5229a4bcb05fdb31516e9191331b53c0bff51a17dcefec7ea0f"
INITIAL_SHA = "278cf1676626bea460c183d25685030c32362a39f70681fef8839d492726f43c"


def write_json(path, value):
    staged = path.with_name(path.name + ".tmp")
    staged.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")
    staged.replace(path)


def state(pid):
    try:
        return Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[0]
    except FileNotFoundError:
        return "absent"


def wait_until(predicate, timeout, message):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError(message)


def fake_descendant(case):
    for signum in (signal.SIGTERM, signal.SIGHUP, signal.SIGINT):
        signal.signal(signum, signal.SIG_IGN)
    write_json(case / "descendant.json", {"pid": os.getpid(), "pgid": os.getpgrp(),
                                        "ignores": ["SIGTERM", "SIGHUP", "SIGINT"]})
    while True:
        time.sleep(1)


def fake_leader(case, mode):
    def on_term(signum, _frame):
        write_json(case / "cleanup-seen.json", {"signal": signal.Signals(signum).name,
                                                "leader_mode": mode})
        if mode == "leader-exits-early":
            time.sleep(0.7)  # Keep cleanup active while repeated signals arrive.
            raise SystemExit(0)

    signal.signal(signal.SIGTERM, on_term)
    signal.signal(signal.SIGHUP, signal.SIG_IGN)
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    child = subprocess.Popen([sys.executable, "-B", str(Path(__file__).resolve()),
                              "descendant", str(case)])
    write_json(case / "leader.json", {"pid": os.getpid(), "pgid": os.getpgrp(),
                                     "descendant_pid": child.pid, "mode": mode})
    wait_until(lambda: (case / "descendant.json").exists(), 5, "fake descendant not ready")
    print("OWNED_FAKE_BUILD_READY", flush=True)
    while True:
        time.sleep(1)


def worker(case, mode):
    # Fail closed if the reviewed helper changed while this harness was prepared.
    source = HELPER.read_bytes()
    assert hashlib.sha256(source).hexdigest() == EXPECTED_SHA
    spec = importlib.util.spec_from_file_location("reviewed_host_helper", HELPER)
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)
    assert helper.ENABLED is False

    # Extract the actual signal installation, build window, save_report and
    # outer except/finally. No other main statement is compiled or executed.
    tree = ast.parse(source, str(HELPER))
    main = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "main")
    install = next(n for n in main.body if isinstance(n, ast.For)
                   and "signal.signal(signum, interrupted)" in ast.unparse(n))
    save = next(n for n in main.body if isinstance(n, ast.FunctionDef) and n.name == "save_report")
    outer = next(n for n in main.body if isinstance(n, ast.Try)
                 and any(isinstance(x, ast.With) and "subprocess.Popen" in ast.unparse(x)
                         for x in n.body))
    build = next(n for n in outer.body if isinstance(n, ast.With)
                 and "subprocess.Popen" in ast.unparse(n))
    assert len(outer.handlers) == 1 and len(outer.finalbody) == 3
    function = ast.FunctionDef(
        name="exercise_actual_build_window",
        args=ast.arguments(posonlyargs=[], args=[], kwonlyargs=[], kw_defaults=[], defaults=[]),
        body=[copy.deepcopy(save), ast.Expr(value=ast.Call(func=ast.Name(id="save_report", ctx=ast.Load()), args=[], keywords=[])),
              ast.Try(body=[copy.deepcopy(build)], handlers=copy.deepcopy(outer.handlers),
                      orelse=[], finalbody=copy.deepcopy(outer.finalbody))],
        decorator_list=[])
    extracted = ast.fix_missing_locations(ast.Module(body=[copy.deepcopy(install), function], type_ignores=[]))
    write_json(case / "extracted-actual-code.json", {
        "helper_sha256": EXPECTED_SHA,
        "signal_installation": ast.unparse(install),
        "actual_build_window": ast.unparse(build),
        "actual_save_report": ast.unparse(save),
        "actual_handlers": [ast.unparse(n) for n in outer.handlers],
        "actual_finally": [ast.unparse(n) for n in outer.finalbody],
        "helper_main_called": False,
    })

    repo = case / "repo"
    (repo / "frontend").mkdir(parents=True)
    report = {"status": "started", "built": False,
              "started_at": datetime.now(timezone.utc).isoformat()}

    def owned_fake_popen(argv, **kwargs):
        assert argv == ["yarn", "build"]
        assert kwargs["start_new_session"] is True
        assert kwargs["cwd"] == repo / "frontend"
        process = subprocess.Popen([sys.executable, "-B", str(Path(__file__).resolve()),
                                    "leader", str(case), mode], **kwargs)
        write_json(case / "spawned-process.json", {"pid": process.pid})
        return process

    # Only the extracted Popen call is redirected. interrupted and
    # stop_owned_build remain the actual imported functions with original globals.
    namespace = dict(vars(helper), repo=repo, log=case / "adapter.log",
                     report_path=case / "result.json", report=report,
                     build_env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"),
                     subprocess=SimpleNamespace(Popen=owned_fake_popen,
                                                DEVNULL=subprocess.DEVNULL,
                                                STDOUT=subprocess.STDOUT))
    exec(compile(extracted, str(HELPER) + "::actual-signal-window", "exec"), namespace)
    write_json(case / "worker.json", {"pid": os.getpid(), "main_executed": False})
    try:
        namespace["exercise_actual_build_window"]()
    except helper.Stop as exc:
        print("CAUGHT_ACTUAL_STOP", str(exc), flush=True)
        return 2
    raise AssertionError("fake infinite build unexpectedly completed")


def controller():
    # Adopt only this controller's orphaned descendants, so even killed fake
    # descendants can be reaped instead of being left to the container's init.
    libc = ctypes.CDLL(None, use_errno=True)
    assert libc.prctl(36, 1, 0, 0, 0) == 0, "PR_SET_CHILD_SUBREAPER failed"
    before = hashlib.sha256(HELPER.read_bytes()).hexdigest()
    assert before == EXPECTED_SHA
    cases = []
    for signame in ("SIGTERM", "SIGHUP"):
        for mode in ("leader-exits-early", "leader-ignores-term"):
            case = ROOT / (signame.lower() + "-" + mode)
            case.mkdir(exist_ok=False)
            result = {"name": case.name, "initial_signal": signame, "leader_mode": mode,
                      "status": "running"}
            proc = None
            pids = []
            leader_pid = None
            try:
                with (case / "worker.log").open("wb") as output:
                    proc = subprocess.Popen([sys.executable, "-B", str(Path(__file__).resolve()),
                                             "worker", str(case), mode],
                                            stdin=subprocess.DEVNULL, stdout=output,
                                            stderr=subprocess.STDOUT, start_new_session=True,
                                            env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"))
                    wait_until(lambda: (case / "descendant.json").exists()
                               and (case / "leader.json").exists(), 8, "fake build not ready")
                    leader = json.loads((case / "leader.json").read_text())
                    child = json.loads((case / "descendant.json").read_text())
                    leader_pid = leader["pid"]
                    pids = [leader_pid, child["pid"]]
                    assert leader["pgid"] == leader_pid == child["pgid"]
                    assert leader["descendant_pid"] == child["pid"]
                    result["owned_pids"] = pids
                    wait_until(lambda: "OWNED_FAKE_BUILD_READY" in (case / "adapter.log").read_text(),
                               3, "fake build readiness log missing")
                    start = time.monotonic()
                    os.kill(proc.pid, getattr(signal, signame))
                    wait_until(lambda: (case / "cleanup-seen.json").exists(), 3,
                               "actual stop_owned_build did not TERM its own group")
                    repeated = []
                    for repeated_name in ("SIGTERM", "SIGHUP", "SIGINT") * 3:
                        os.kill(proc.pid, getattr(signal, repeated_name))
                        repeated.append(repeated_name)
                        time.sleep(0.03)
                    result["repeated_signals_during_cleanup"] = repeated
                    result["exit_code"] = proc.wait(timeout=22)
                    result["elapsed_seconds"] = round(time.monotonic() - start, 4)
                assert result["exit_code"] == 2
                assert result["elapsed_seconds"] < 22
                receipt = json.loads((case / "result.json").read_text())
                result["retained_receipt"] = receipt
                assert receipt["status"] == "failed_or_interrupted" and receipt["built"] is False
                assert receipt["failure_type"] == "Stop"
                assert receipt["failure"] == "isolated rehearsal interrupted by " + signame
                assert receipt["finished_at"] >= receipt["started_at"]
                assert "RETAINED_RESULT" in (case / "worker.log").read_text()
                assert "OWNED_FAKE_BUILD_READY" in (case / "adapter.log").read_text()
                wait_until(lambda: all(state(pid) in {"absent", "Z"} for pid in pids),
                           2, "owned fake descendant survived cleanup")
                result["states_before_reaping"] = {str(pid): state(pid) for pid in pids}
                reaped = []
                for pid in pids:
                    try:
                        waited_pid, status = os.waitpid(pid, os.WNOHANG)
                        if waited_pid:
                            reaped.append({"pid": waited_pid, "wait_status": status})
                    except ChildProcessError:
                        pass
                result["reaped_descendants"] = reaped
                result["states_after_reaping"] = {str(pid): state(pid) for pid in pids}
                assert all(value == "absent" for value in result["states_after_reaping"].values())
                result["status"] = "passed"
            except BaseException as exc:
                result.update(status="failed", failure_type=type(exc).__name__, failure=str(exc))
            finally:
                # Safety cleanup is limited to known groups created by this test.
                if leader_pid is None and (case / "spawned-process.json").exists():
                    leader_pid = json.loads((case / "spawned-process.json").read_text())["pid"]
                    pids.append(leader_pid)
                if (case / "descendant.json").exists():
                    child_pid = json.loads((case / "descendant.json").read_text())["pid"]
                    if child_pid not in pids:
                        pids.append(child_pid)
                if leader_pid is not None and any(state(pid) not in {"absent", "Z"} for pid in pids):
                    try:
                        os.killpg(leader_pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    result["harness_emergency_cleanup"] = True
                if proc is not None and proc.poll() is None:
                    proc.kill()
                    proc.wait(timeout=5)
                for pid in pids:
                    try:
                        os.waitpid(pid, os.WNOHANG)
                    except ChildProcessError:
                        pass
                write_json(case / "harness-case.json", result)
                cases.append(result)
                print(json.dumps(result, sort_keys=True), flush=True)
    after = hashlib.sha256(HELPER.read_bytes()).hexdigest()
    summary = {
        "helper_path": str(HELPER), "helper_sha256_before": before, "helper_sha256_after": after,
        "initial_finding": {"helper_sha256": INITIAL_SHA,
            "severity": "medium", "finding": "SIGTERM/SIGHUP bypassed cleanup/finally and could leave the isolated build process group running with a started receipt."},
        "main_executed": False, "app_accessed": False, "source_modified": False,
        "host_or_lease_modified": False, "actual_build_started": False,
        "scope": "Actual imported handlers/cleanup plus original build-window and receipt AST; owned fake process groups only.",
        "cases": cases, "passed": sum(case["status"] == "passed" for case in cases),
        "failed": sum(case["status"] != "passed" for case in cases),
        "helper_unchanged": before == after,
    }
    write_json(ROOT / "signal-review.json", summary)
    print("SIGNAL_REVIEW", json.dumps({"passed": summary["passed"], "failed": summary["failed"],
                                        "helper_unchanged": before == after}), flush=True)
    return 0 if summary["failed"] == 0 and before == after else 1


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "controller"
    if mode == "descendant":
        fake_descendant(Path(sys.argv[2]))
    elif mode == "leader":
        fake_leader(Path(sys.argv[2]), sys.argv[3])
    elif mode == "worker":
        raise SystemExit(worker(Path(sys.argv[2]), sys.argv[3]))
    else:
        raise SystemExit(controller())
