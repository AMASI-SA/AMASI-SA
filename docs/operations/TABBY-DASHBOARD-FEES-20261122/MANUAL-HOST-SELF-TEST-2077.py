#!/usr/bin/env python3
"""Disposable local tests only; never open /app, build, or call a real guard."""
import ast
from contextlib import contextmanager, redirect_stderr, redirect_stdout
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import sys
import subprocess
import tempfile
from types import ModuleType
from unittest.mock import patch


ROOT = Path(__file__).resolve().parent


def load(name):
    specification = importlib.util.spec_from_file_location(name.replace("-", "_"), ROOT / name)
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


def refuses(call, exception):
    try:
        call()
    except exception:
        return
    raise AssertionError("expected refusal")


def shell_python(name):
    text = (ROOT / name).read_text()
    return text.split("python -B - <<'PY'\n", 1)[1].rsplit("\nPY", 1)[0]


def check_disabled(stage, rehearsal, prepare):
    # The tests set the not-yet-approved deployment pin to None even after a
    # later reviewed pinning commit. No environment value enables these files.
    stage.D = rehearsal.D = None
    prepare.EXPECTED["deployment_git_sha"] = None
    with (patch("subprocess.run", side_effect=AssertionError("host command reached")),
          patch("tempfile.mkdtemp", side_effect=AssertionError("host directory creation reached")),
          redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO())):
        refuses(lambda: stage.main(Path("/not-a-host-receipt")), stage.Stop)
        refuses(rehearsal.main, SystemExit)
        refuses(prepare.main, SystemExit)
        for name in ("RUN-HOST-DEPLOY-2077.sh", "RUN-HOST-VERIFY-2077.sh"):
            tree = ast.parse(shell_python(name), filename=name)
            for node in tree.body:
                if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name):
                    if node.targets[0].id == "D":
                        node.value = ast.Constant(None)
                    if node.targets[0].id == "EXPECTED":
                        value = ast.literal_eval(node.value)
                        value["deployment_git_sha"] = None
                        node.value = ast.parse(repr(value), mode="eval").body
            tree = ast.fix_missing_locations(tree)
            refuses(lambda: exec(compile(tree, name, "exec"), {"__name__": "__main__"}), SystemExit)
    print("DISABLED_PIN_TEST_PASS: all five entry points refuse before a host command")


def check_receipt(stage):
    stage.D = "d" * 40  # Synthetic pin in this imported test module only.
    with (tempfile.TemporaryDirectory(prefix="mezan-pr2077-deploy-", dir="/tmp") as launcher,
          tempfile.TemporaryDirectory(prefix="mezan-pr2077-rehearsal-", dir="/tmp") as directory):
        work = Path(directory)
        candidate = work / "repo"
        candidate.mkdir()
        log = work / "adapter.log"
        log.write_text("synthetic retained log\n")
        receipt = Path(launcher) / "rehearsal-receipt.json"
        record = {"kind": "mezan_pr2077_rehearsal_v1", "actor": stage.ACTOR,
            "base": stage.J, "source": stage.A, "intent_commit": stage.B, "deployment": stage.D,
            "tree": stage.TREE, "release_id": stage.RID, "artifact_tree": stage.ARTIFACT,
            "directory": str(work), "candidate": str(candidate),
            "log_sha256": hashlib.sha256(log.read_bytes()).hexdigest()}

        def write(value):
            if receipt.exists():
                receipt.chmod(0o600)
            receipt.write_text(json.dumps(value))
            receipt.chmod(0o400)

        write(record)
        assert stage.read_receipt(receipt) == candidate
        write({**record, "source": "0" * 40})
        refuses(lambda: stage.read_receipt(receipt), stage.Stop)
        write({**record, "candidate": str(work / "another-repo")})
        refuses(lambda: stage.read_receipt(receipt), stage.Stop)
        write(record)
        receipt.chmod(0o644)
        refuses(lambda: stage.read_receipt(receipt), stage.Stop)
        receipt.chmod(0o400)
        log.write_text("changed retained log\n")
        refuses(lambda: stage.read_receipt(receipt), stage.Stop)
        log.write_text("synthetic retained log\n")
        candidate.rmdir()
        candidate.symlink_to(Path(launcher), target_is_directory=True)
        refuses(lambda: stage.read_receipt(receipt), stage.Stop)
        candidate.unlink()
        candidate.mkdir()
        with tempfile.TemporaryDirectory(prefix="mezan-pr2077-alias-", dir="/tmp") as alias:
            linked = Path(alias) / "launcher"
            linked.symlink_to(Path(launcher), target_is_directory=True)
            refuses(lambda: stage.read_receipt(linked / receipt.name), stage.Stop)
        assert stage.read_receipt(receipt) == candidate
    print("RECEIPT_TEST_PASS: exact release, bounded private path, log digest, mode and symlink refusals")


def check_complete_package_comparison(stage):
    expected = {
        "schema_version": 1, "kind": "mezan_release_package_boundary_proof_v1",
        "source_git_sha": stage.A, "release_id": stage.RID,
        "frontend": {"file_count": 17, "package_tree_sha256": "a" * 64,
                     "build_meta": {"path": "build/build-meta.json", "bytes": 12, "sha256": "b" * 64}},
        "backend": {"file_count": 19, "package_tree_sha256": "c" * 64,
                    "identity": {"path": "release_identity.json", "bytes": 34, "sha256": "e" * 64}},
    }
    returned = json.loads(json.dumps(expected))
    adapter = ModuleType("scripts.emergent_deployment_adapter")
    adapter.load_release_intent = lambda: {}
    adapter.verify_release_intent_git = lambda value: None
    adapter.verify_package_boundaries = lambda value: returned
    scripts = ModuleType("scripts")
    scripts.emergent_deployment_adapter = adapter

    def original(_label, code, _cwd):
        with redirect_stdout(io.StringIO()):
            exec(code, {})
        return "synthetic original proof"

    with (patch.dict(sys.modules, {"scripts": scripts, "scripts.emergent_deployment_adapter": adapter}),
          patch.object(stage, "original", original), redirect_stdout(io.StringIO())):
        stage.validate_package_proof(Path("/unused-test-path"), {"package_boundary": expected})
        for section, key, replacement in (
            ("frontend", "file_count", 18), ("frontend", "package_tree_sha256", "0" * 64),
            ("frontend", "build_meta", {"path": "index.html"}),
            ("backend", "file_count", 20), ("backend", "package_tree_sha256", "0" * 64),
            ("backend", "identity", {"sha256": "0" * 64}),
        ):
            returned = json.loads(json.dumps(expected))
            returned[section][key] = replacement
            refuses(lambda: stage.validate_package_proof(Path("/unused-test-path"),
                                                         {"package_boundary": expected}), AssertionError)
    print("PACKAGE_PROOF_TEST_PASS: full original proof comparison rejects count, tree, metadata and identity drift")


def check_staging_mutex():
    for foreign in (None, "lease", "incomplete"):
        stage = load("MANUAL-HOST-STAGE-2077.py")
        stage.D = "d" * 40
        with (tempfile.TemporaryDirectory(prefix="pr2077-staging-mutex-test-") as temporary,
              tempfile.TemporaryDirectory(prefix="mezan-pr2077-deploy-", dir="/tmp") as launcher,
              tempfile.TemporaryDirectory(prefix="mezan-pr2077-rehearsal-", dir="/tmp") as rehearsal):
            root, candidate = Path(temporary), Path(rehearsal) / "repo"
            app = root / "app"
            for base in (app, candidate):
                for relative in (".git", "frontend/build", "frontend/.release", "backend"):
                    (base / relative).mkdir(parents=True, exist_ok=True)
                (base / "frontend/build/asset.js").write_text("new" if base == candidate else "old")
                (base / "frontend/.release/reproducible-build.json").write_text('{"fixture": true}\n')
                (base / "backend/release_identity.json").write_text('{"fixture": true}\n')
            (app / "frontend/node_modules").mkdir()
            (app / stage.CACHE).mkdir()
            (app / stage.CACHE / "known.cpython-311.pyc").write_bytes(b"preserved cache")
            identity_digest = stage.digest(candidate / "backend/release_identity.json")
            proof = {"built": True, "protocol_version": 5, "source_git_sha": stage.A,
                "release_id": stage.RID, "frontend_artifact_tree_sha256": stage.ARTIFACT,
                "package_boundary": {"kind": "mezan_release_package_boundary_proof_v1",
                    "source_git_sha": stage.A, "release_id": stage.RID,
                    "frontend": {"isolated_runtime_artifact_verified": True,
                        "content_type_contract": "application/json", "artifact_tree_sha256": stage.ARTIFACT},
                    "backend": {"isolated_verified": True, "health_verified": True,
                        "sibling_frontend_present": False, "git_directory_present": False,
                        "identity": {"sha256": identity_digest}}}}
            log = Path(rehearsal) / "adapter.log"
            log.write_text(json.dumps(proof) + "\n")
            receipt = Path(launcher) / "rehearsal-receipt.json"
            receipt.write_text(json.dumps({"kind": "mezan_pr2077_rehearsal_v1", "actor": stage.ACTOR,
                "base": stage.J, "source": stage.A, "intent_commit": stage.B, "deployment": stage.D,
                "tree": stage.TREE, "release_id": stage.RID, "artifact_tree": stage.ARTIFACT,
                "directory": rehearsal, "candidate": str(candidate),
                "log_sha256": stage.digest(log)}))
            receipt.chmod(0o400)
            state = {"locked": False, "head": stage.J, "moves": 0, "copies": 0,
                     "fast_forward": False, "shared_proof": False, "final_check": False}
            guard = ModuleType("scripts.production_release_guard")
            guard.REPO_ROOT, guard.__file__, guard.PROTOCOL_VERSION = app, str(app / "scripts/production_release_guard.py"), 5
            guard.LEASE_DIR = app / ".git/mezan-production-release.lock"
            guard.LEASE_PATH = guard.LEASE_DIR / "lease.json"

            @contextmanager
            def operation_lock():
                assert not state["locked"]
                state["locked"] = True
                try:
                    # Simulate another owner winning between preflight and
                    # mutex acquisition. Neither lease shape may be touched.
                    if foreign:
                        guard.LEASE_DIR.mkdir()
                        if foreign == "lease":
                            guard.LEASE_PATH.write_text('{"actor": "foreign"}\n')
                    yield
                finally:
                    state["locked"] = False

            def forbidden_status():
                raise AssertionError("status() must not nest the held operation lock")

            guard._release_operation_lock, guard.status = operation_lock, forbidden_status
            scripts = ModuleType("scripts")
            scripts.production_release_guard = guard

            def git(*args, cwd=None, raw=False, **_options):
                if args[0] == "rev-parse":
                    if args[1] == "HEAD":
                        return stage.B if cwd == candidate else state["head"]
                    return stage.TREE
                if args[0] == "branch":
                    return stage.BRANCH
                if args[0] == "ls-remote":
                    return stage.D + "\trefs/heads/" + stage.BRANCH
                if args[0] == "rev-list":
                    return " ".join((stage.D, stage.J, stage.B))
                if args[0] == "remote":
                    return "synthetic-local-remote"
                if args[0] in {"status", "ls-files", "check-ignore", "fetch", "checkout"}:
                    return b"" if raw else ""
                raise AssertionError("unexpected Git fixture operation")

            def original(label, _code, cwd=None):
                if label in {"guard-status", "candidate-proof", "candidate-package-proof"}:
                    assert not state["locked"], label
                else:
                    assert state["locked"], label
                if label == "shared-proof":
                    state["shared_proof"] = True
                return '{"active": false}'

            def run(label, argv, **_options):
                assert state["locked"], label
                if label == "git-fast-forward":
                    assert "--no-overwrite-ignore" in argv
                    state["head"] = stage.D
                    state["fast_forward"] = True
                elif label != "original-frontend-verifier":
                    raise AssertionError("unexpected host fixture operation")
                return ""

            copy, move, checkpoint, mkdtemp = stage.copy_verified, stage.move_verified, stage.checkpoint, tempfile.mkdtemp

            def copy_checked(*args):
                assert state["locked"]
                state["copies"] += 1
                return copy(*args)

            def move_checked(*args):
                assert state["locked"]
                state["moves"] += 1
                return move(*args)

            def checkpoint_checked(phase, **fields):
                assert state["locked"]
                if phase == "staging-verified":
                    assert state["shared_proof"] and not os.path.lexists(guard.LEASE_DIR)
                    state["final_check"] = True
                return checkpoint(phase, **fields)

            def private_directory(*args, **kwargs):
                assert state["locked"]
                if kwargs.get("prefix") == "mezan-pr2077-preserved-":
                    kwargs["dir"] = root
                return mkdtemp(*args, **kwargs)

            with (patch.object(stage, "APP", app), patch.object(stage, "git", git),
                  patch.object(stage, "original", original), patch.object(stage, "run", run),
                  patch.object(stage, "copy_verified", copy_checked), patch.object(stage, "move_verified", move_checked),
                  patch.object(stage, "checkpoint", checkpoint_checked), patch("tempfile.mkdtemp", private_directory),
                  patch.dict(sys.modules, {"scripts": scripts, "scripts.production_release_guard": guard}),
                  patch.object(sys, "path", list(sys.path)), redirect_stdout(io.StringIO())):
                if foreign:
                    refuses(lambda: stage.main(receipt), stage.Stop)
                else:
                    stage.main(receipt)
            assert state["locked"] is False and stage.LOCKED_GUARD is None
            if foreign:
                assert state["moves"] == state["copies"] == 0 and not state["fast_forward"]
                assert stage.RECOVERY is stage.STAGE is None and guard.LEASE_DIR.exists()
                if foreign == "lease":
                    assert guard.LEASE_PATH.read_text() == '{"actor": "foreign"}\n'
                assert (app / "frontend/build/asset.js").read_text() == "old"
                assert (app / stage.CACHE / "known.cpython-311.pyc").read_bytes() == b"preserved cache"
            else:
                assert state["moves"] == 7 and state["fast_forward"] and state["final_check"]
                assert (app / "frontend/build/asset.js").read_text() == "new"
                assert (stage.RECOVERY / "old/frontend/build/asset.js").read_text() == "old"
                assert (stage.STAGE / "retired" / stage.CACHE / "known.cpython-311.pyc").is_file()
    print("STAGING_MUTEX_TEST_PASS: all shared mutations/proof/final check hold the original mutex; foreign/incomplete leases preserve all bytes")


def check_ignored_collision():
    with tempfile.TemporaryDirectory(prefix="pr2077-ignored-collision-test-") as temporary:
        root = Path(temporary)

        def git(*args, succeeds=True):
            result = subprocess.run(["git", "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
                "-c", "commit.gpgsign=false", *args], cwd=root, stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=15)
            assert (result.returncode == 0) is succeeds
            return result.stdout.decode().strip()

        git("init", "-q")
        (root / ".gitignore").write_text("local-only.txt\n")
        git("add", ".gitignore")
        git("commit", "-qm", "synthetic base")
        base = git("rev-parse", "HEAD")
        git("checkout", "-qb", "candidate")
        (root / "local-only.txt").write_text("candidate source bytes")
        git("add", "-f", "local-only.txt")
        git("commit", "-qm", "synthetic candidate")
        candidate = git("rev-parse", "HEAD")
        git("checkout", "-q", "--detach", base)
        (root / "local-only.txt").write_text("preserve host-only bytes")
        assert not git("ls-files", "--others", "--exclude-standard")
        git("merge", "--ff-only", "--no-overwrite-ignore", candidate, succeeds=False)
        assert git("rev-parse", "HEAD") == base
        assert (root / "local-only.txt").read_text() == "preserve host-only bytes"
    print("IGNORED_COLLISION_TEST_PASS: ordinary fast-forward refuses and preserves an ignored host-only collision")


def main():
    for path in sorted(ROOT.glob("*-2077.py")):
        compile(path.read_text(), str(path), "exec")
    for name in ("RUN-HOST-DEPLOY-2077.sh", "RUN-HOST-VERIFY-2077.sh"):
        compile(shell_python(name), name, "exec")
    stage = load("MANUAL-HOST-STAGE-2077.py")
    rehearsal = load("MANUAL-HOST-ISOLATED-REHEARSAL-2077.py")
    prepare = load("MANUAL-HOST-PREPARE-PREPUBLISH-2077.py")
    stage.self_test()
    check_disabled(stage, rehearsal, prepare)
    check_receipt(stage)
    check_complete_package_comparison(stage)
    check_staging_mutex()
    check_ignored_collision()
    print("BUNDLE_SELF_TEST_PASS: syntax and bounded local safety tests; no host action executed")


if __name__ == "__main__":
    main()
