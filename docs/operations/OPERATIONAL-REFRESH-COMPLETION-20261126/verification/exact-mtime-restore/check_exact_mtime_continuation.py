"""Bounded local-only checks. No subprocess, host, network, or service access."""
from copy import deepcopy
from hashlib import sha256
import importlib.util
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch


BASE = Path(__file__).resolve().parent
SOURCE = BASE / "restore_exact_mtime_observation.py"
spec = importlib.util.spec_from_file_location("exact_restore_probe", SOURCE)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
source_digest = sha256(SOURCE.read_bytes()).hexdigest()
raw = (BASE / "postpublication-preservation.json").read_bytes()
assert sha256(raw).hexdigest() == module.OBSERVATION_SHA256
saved = json.loads(raw)
results = []


def record(name):
    results.append({"name": name, "passed": True})


def rejected(callback):
    try:
        callback()
    except (RuntimeError, KeyboardInterrupt):
        return
    raise AssertionError("expected refusal")


module.exact_pattern(saved)
assert saved["observation"]["credential_metadata_only_after"]["mtime_ns"] == 1795670237245429582
record("exact_raw_observation_and_integer_nanoseconds")
for name, change in (
    ("changed_credential_inode", lambda o: o["credential_metadata_only_after"].update(inode=1)),
    ("changed_credential_size", lambda o: o["credential_metadata_only_after"].update(size=1)),
    ("credential_digest_forbidden", lambda o: o["credential_metadata_only_after"].update(sha256="0" * 64)),
    ("original_refusal_must_remain", lambda o: o.update(eligible_for_exact_review=True)),
    ("sole_violation_must_remain", lambda o: o.update(violations=[])),
    ("full_root_count", lambda o: o.update(foreign_root_count=58)),
    ("full_untracked_count", lambda o: o.update(untracked_count=431)),
    ("exact_append_size", lambda o: o["eligible_variances"][0].update(appended_bytes=377)),
    ("prefix_proof_required", lambda o: o["eligible_variances"][0].update(original_prefix_preserved=False)),
):
    bad = deepcopy(saved)
    change(bad["observation"])
    rejected(lambda: module.exact_pattern(bad))
    record(name)


with TemporaryDirectory(prefix="exact-restore-local-probes-") as temporary:
    root = Path(temporary)
    fixture = root / "evidence.json"
    fixture.write_bytes(raw)
    assert module.pinned_bytes(fixture, module.OBSERVATION_SHA256) == raw
    fixture.write_bytes(raw + b"\n")
    rejected(lambda: module.pinned_bytes(fixture, module.OBSERVATION_SHA256))
    record("whole_raw_file_digest_required")
    alias = root / "alias.json"
    alias.symlink_to(fixture)
    rejected(lambda: module.pinned_bytes(alias, sha256(fixture.read_bytes()).hexdigest()))
    record("pinned_evidence_symlink_refused")


def scenario(name):
    with TemporaryDirectory(prefix="exact-restore-flow-") as temporary:
        root = Path(temporary)
        stage = root / ".git/stage"
        stage.mkdir(parents=True)
        starts, inspections, states = [], [], []
        cfg = SimpleNamespace(REPO=root, STAGE_REL=".git/stage", SOURCE=module.SOURCE,
                              INTENT=module.INTENT, DEPLOYMENT=module.DEPLOYMENT,
                              TREE=module.TREE, RELEASE_ID=module.RELEASE_ID)
        helper = SimpleNamespace(validate_configuration=lambda: None,
            real_directory=lambda p: p.is_dir(), utc_now=lambda: "2026-11-26T05:30:00+00:00",
            private_json=lambda p, value: p.write_text(json.dumps(value), encoding="utf-8"),
            git_read=lambda _repo, *args: module.DEPLOYMENT if args == ("rev-parse", "HEAD")
                     else module.TREE if args == ("rev-parse", "HEAD^{tree}") else "")
        def inspect(*_):
            inspections.append(1)
            value = deepcopy(saved["observation"])
            if name == "fresh_observation_differs" or (name == "post_start_observation_differs" and len(inspections) == 2):
                value["credential_metadata_only_after"]["mtime_ns"] += 1
            return value
        preservation = SimpleNamespace(inspect_after_publication=inspect)
        def fake_command(_helper, argv, *, allowed=(0,), timeout=45):
            if "production_release_guard.py" in " ".join(argv):
                return json.dumps({"active": name == "guard_active"}), 0
            if argv == ["supervisorctl", "start", "backend"]:
                starts.append(1)
                if name == "start_failure":
                    raise RuntimeError("synthetic start failure")
                if name == "start_interruption":
                    raise KeyboardInterrupt("synthetic cancellation")
                return "backend: started", 0
            assert argv == ["supervisorctl", "status", "backend"]
            state = "RUNNING" if starts or name == "already_running" else "STOPPED"
            code = 0 if state == "RUNNING" else 3
            states.append(state)
            module.require(code in allowed, "synthetic service state differs")
            return "backend " + state, code
        def forbid_process(*_args, **_kwargs):
            raise AssertionError("real subprocess is forbidden")
        with patch.object(module, "REPO", root), patch.object(module, "STAGE", stage), \
             patch.object(module, "load_frozen_helpers", return_value=(cfg, helper, preservation)), \
             patch.object(module, "pinned_bundle", return_value={}), \
             patch.object(module, "pinned_chain", return_value=({"synthetic": True}, b"immutable", saved["observation"])), \
             patch.object(module, "command", side_effect=fake_command), \
             patch.object(module.subprocess, "Popen", side_effect=forbid_process):
            if name in ("success_once", "replay_refused"):
                result = module.run(module.OBSERVATION_SHA256)
                assert result["phase"] == "production_verified_local_backend_restored" and len(starts) == 1
                if name == "replay_refused":
                    rejected(lambda: module.run(module.OBSERVATION_SHA256))
                    assert len(starts) == 1
            else:
                rejected(lambda: module.run(module.OBSERVATION_SHA256))
            output = stage / module.JOURNAL_FILE
            if name == "guard_active":
                assert not output.exists() and not starts
            else:
                journal = json.loads(output.read_text())
                assert journal["original_eligibility_and_violation_retained"] is True
                assert journal["exact_saved_observation"] == saved["observation"]
                assert journal["credential_content_equality_verified"] is False
                if name in ("fresh_observation_differs", "already_running"):
                    assert journal["phase"] == "restore_requires_inspection" and not starts
                if name in ("start_failure", "start_interruption", "post_start_observation_differs"):
                    assert journal["phase"] == "restore_requires_inspection" and len(starts) == 1
    record(name)


for name in ("success_once", "replay_refused", "fresh_observation_differs", "already_running",
             "guard_active", "start_failure", "start_interruption", "post_start_observation_differs"):
    scenario(name)


for kind in ("timeout", "interrupt"):
    stopped = []
    process = SimpleNamespace(returncode=None)
    def communicate(*, timeout):
        if kind == "timeout":
            raise module.subprocess.TimeoutExpired(["synthetic"], timeout)
        raise KeyboardInterrupt("synthetic child interruption")
    process.communicate = communicate
    helper = SimpleNamespace(host_environment=lambda: {}, stop_owned_process=lambda child: stopped.append(child))
    with patch.object(module.subprocess, "Popen", return_value=process) as popen:
        try:
            module.command(helper, ["synthetic"])
        except (module.subprocess.TimeoutExpired, KeyboardInterrupt):
            pass
        else:
            raise AssertionError("expected child cancellation")
        assert stopped == [process] and popen.call_args.kwargs["start_new_session"] is True
    record("owned_child_cleanup_on_" + kind)

assert sha256(SOURCE.read_bytes()).hexdigest() == source_digest
assert (BASE / "postpublication-preservation.json").read_bytes() == raw
result = {"source_sha256": source_digest, "checks": results, "passed": len(results),
          "real_subprocesses": 0, "host_actions": 0, "services_started": 0,
          "credential_contents_accessed": False, "original_observation_unchanged": True,
          "scope": "Pure exact-pattern/raw-byte checks and mocked restoration/control-flow checks; no production execution."}
path = BASE / "exact-mtime-continuation-checks.json"
with path.open("x", encoding="utf-8") as output:
    json.dump(result, output, ensure_ascii=False, indent=2)
    output.write("\n")
print(json.dumps({"passed": len(results), "result": str(path), "source_sha256": source_digest,
                  "result_sha256": sha256(path.read_bytes()).hexdigest(), "real_subprocesses": 0}))
