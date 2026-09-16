from __future__ import annotations

import hashlib
import io
import json
import os
import subprocess
import stat
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from scripts import emergent_deployment_adapter as adapter
from scripts import verify_frontend_build as verifier


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _git_blob(value: bytes) -> str:
    return hashlib.sha1(
        f"blob {len(value)}\0".encode() + value,
        usedforsecurity=False,
    ).hexdigest()


class EmergentDeploymentAdapterV5Tests(unittest.TestCase):
    @staticmethod
    def _git(root: Path, *args: str) -> str:
        result = subprocess.run(
            ["git", "-C", os.fspath(root), *args],
            check=True,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        return result.stdout.strip()

    def _git_manifest_history(self, root: Path):
        self._git(root, "init", "-q")
        self._git(root, "config", "user.name", "Release Test")
        self._git(root, "config", "user.email", "release@example.test")
        tracked = {
            "backend/server.py": "server-v1\n",
            "backend/release_identity.py": "identity-v1\n",
            "backend/release_protocol_v5.py": "protocol-v1\n",
            "backend/frontend_build_identity.py": "frontend-identity-v1\n",
            "backend/integrations/qoyod_manual/routes.py": "routes-v1\n",
            "backend/integrations/qoyod_manual/send.py": "send-v1\n",
            "backend/keep.py": "keep-v1\n",
            "backend/mode.py": "mode-v1\n",
            "backend/delete.py": "delete-v1\n",
            "backend/requirements.txt": "package==1.0\n",
            "backend/tests/test_fixture.py": "excluded\n",
            "frontend/src.js": "console.log('source');\n",
            "scripts/release.py": "release-v1\n",
            "scripts/release_backend_requirements.lock": "package==1.0\n",
            ".github/workflows/release.yml": "name: release\n",
        }
        for relative, content in tracked.items():
            path = root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
        self._git(root, "add", ".")
        self._git(root, "commit", "-qm", "source P")
        previous_source = self._git(root, "rev-parse", "HEAD")

        intent_path = root / "release" / "release-intent-v5.json"
        intent_path.parent.mkdir()
        intent_path.write_text(json.dumps({
            "schema_version": 1,
            "kind": adapter.INTENT_KIND,
            "protocol_version": 5,
            "source_git_sha": previous_source,
        }), encoding="utf-8")
        self._git(root, "add", "release/release-intent-v5.json")
        self._git(root, "commit", "-qm", "intent J")
        source_base = self._git(root, "rev-parse", "HEAD")

        (root / "backend" / "keep.py").write_text("keep-v2\n", encoding="utf-8")
        (root / "backend" / "delete.py").unlink()
        (root / "backend" / "added.py").write_text("added-v1\n", encoding="utf-8")
        (root / "backend" / "mode.py").chmod(0o755)
        (root / "scripts" / "helper.py").write_text("helper-v1\n", encoding="utf-8")
        self._git(root, "add", "-A")
        self._git(root, "commit", "-qm", "source A")
        source = self._git(root, "rev-parse", "HEAD")
        return previous_source, source_base, source

    def test_resolve_base_after_documentation_merge(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _, base, _ = self._git_manifest_history(root)
            self._git(root, "checkout", "-qb", "docs", base)
            (root / "AGENTS.md").write_text("Mandatory continuity\n")
            self._git(root, "add", "AGENTS.md")
            self._git(root, "commit", "-qm", "continuity docs")
            docs = self._git(root, "rev-parse", "HEAD")
            self._git(root, "checkout", "-qb", "production", base)
            self._git(root, "merge", "--no-ff", "-m", "merge docs", docs)
            production = self._git(root, "rev-parse", "HEAD")
            (root / "frontend" / "src.js").write_text("fixed settings\n")
            self._git(root, "commit", "-qam", "candidate")
            source = self._git(root, "rev-parse", "HEAD")
            with patch.object(adapter, "REPO_ROOT", root):
                self.assertEqual(adapter.resolve_candidate_source_base(production, source), base)
                adapter._assert_candidate_source_transition(source_git_sha=source, source_base_git_sha=base)

    def test_resolve_base_preserves_exact_prior_pair(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _, base, source = self._git_manifest_history(root)
            with patch.object(adapter, "REPO_ROOT", root):
                self.assertEqual(adapter.resolve_candidate_source_base(base, source), base)

    def test_resolve_base_rejects_unreviewed_application_change(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _, _, production = self._git_manifest_history(root)
            (root / "AGENTS.md").write_text("docs\n")
            self._git(root, "add", ".")
            self._git(root, "commit", "-qm", "candidate")
            source = self._git(root, "rev-parse", "HEAD")
            with patch.object(adapter, "REPO_ROOT", root):
                with self.assertRaises(adapter.DeploymentAdapterError):
                    adapter.resolve_candidate_source_base(production, source)

    def test_resolve_base_rejects_intent_edit_and_revert(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _, base, _ = self._git_manifest_history(root)
            self._git(root, "checkout", "-qb", "reverted", base)
            intent = root / "release" / "release-intent-v5.json"
            original = intent.read_text()
            intent.write_text(original + "\n")
            self._git(root, "commit", "-qam", "touch intent")
            intent.write_text(original)
            self._git(root, "commit", "-qam", "revert intent")
            production = self._git(root, "rev-parse", "HEAD")
            (root / "AGENTS.md").write_text("docs\n")
            self._git(root, "add", ".")
            self._git(root, "commit", "-qm", "candidate")
            source = self._git(root, "rev-parse", "HEAD")
            with patch.object(adapter, "REPO_ROOT", root):
                with self.assertRaises(adapter.DeploymentAdapterError):
                    adapter.resolve_candidate_source_base(base, source)

    def _candidate_intent(self, source_base: str, source: str) -> dict:
        from backend.tests.test_release_identity import ReleaseIdentityTests
        from release_protocol_v5 import build_runtime_release_identity

        frontend_path = adapter.FRONTEND_ROOT / "src.js"
        frontend_content = frontend_path.read_bytes()
        frontend_record = {
            "path": "src.js",
            "mode": "100644",
            "git_blob": _git_blob(frontend_content),
            "bytes": len(frontend_content),
            "sha256": _sha256(frontend_content),
        }
        frontend_source = {
            "scope": "git_head_frontend_tree_v1",
            "git_tree_oid": self._git(
                adapter.REPO_ROOT, "rev-parse", f"{source}:frontend"
            ),
            "file_count": 1,
            "files": [frontend_record],
            "tree_sha256": adapter._canonical_source_tree_sha256(
                [frontend_record]
            ),
        }
        frontend_build = ReleaseIdentityTests._frontend_build(source)
        frontend_build["source"] = {
            key: frontend_source[key]
            for key in ("scope", "git_tree_oid", "file_count", "tree_sha256")
        }
        frontend_build["environment"]["values"]["REACT_APP_BACKEND_URL"] = {
            "present": True,
            "sha256": _sha256(b"https://mezansalla.com"),
        }
        frontend_proof = ReleaseIdentityTests._frontend_reproducibility(
            frontend_build
        )
        backend_source = adapter._git_source_manifest(
            scope=adapter.BACKEND_RUNTIME_SOURCE_SCOPE,
            source_git_sha=source,
            source_base_git_sha=source_base,
        )
        control_source = adapter._git_source_manifest(
            scope=adapter.RELEASE_CONTROL_SOURCE_SCOPE,
            source_git_sha=source,
            source_base_git_sha=source_base,
        )
        critical = adapter._critical_hashes()
        runtime_identity = build_runtime_release_identity(
            source_git_sha=source,
            source_base_git_sha=source_base,
            branch="hotfix/prod-snap-meta-final",
            frontend_build=frontend_build,
            frontend_reproducibility=frontend_proof,
            backend_root=adapter.BACKEND_ROOT,
            backend_runtime_source=backend_source,
            release_control_source=control_source,
        )
        return {
            "schema_version": adapter.INTENT_SCHEMA_VERSION,
            "kind": adapter.INTENT_KIND,
            "protocol_version": adapter.PROTOCOL_VERSION,
            "source_git_sha": source,
            "source_base_git_sha": source_base,
            "branch": "hotfix/prod-snap-meta-final",
            "frontend_source": frontend_source,
            "backend_runtime_source": backend_source,
            "release_control_source": control_source,
            "client_environment": {
                "REACT_APP_BACKEND_URL": {
                    "present": True,
                    "value": "https://mezansalla.com",
                }
            },
            "frontend_build": frontend_build,
            "frontend_reproducibility": frontend_proof,
            "critical_file_hashes": critical,
            "runtime_identity": runtime_identity,
        }

    def _roots(self, root: Path):
        frontend = root / "frontend"
        backend = root / "backend"
        scripts = root / "scripts"
        (frontend / ".release").mkdir(parents=True)
        backend.mkdir()
        scripts.mkdir()
        return patch.multiple(
            adapter,
            REPO_ROOT=root,
            FRONTEND_ROOT=frontend,
            BACKEND_ROOT=backend,
            BUILD_ROOT=frontend / "build",
            PROOF_PATH=frontend / ".release" / "reproducible-build.json",
            IDENTITY_PATH=backend / "release_identity.json",
            INTENT_PATH=root / "release" / "release-intent-v5.json",
            TOOLCHAIN_SCRIPT=scripts / "frontend_release_toolchain.py",
            VERIFY_SCRIPT=scripts / "verify_frontend_build.py",
        )

    @staticmethod
    def _source(frontend: Path) -> dict:
        content = b'console.log("reviewed");\n'
        path = frontend / "src.js"
        path.write_bytes(content)
        record = {
            "path": "src.js",
            "mode": "100644",
            "git_blob": _git_blob(content),
            "bytes": len(content),
            "sha256": _sha256(content),
        }
        return {
            "scope": "git_head_frontend_tree_v1",
            "git_tree_oid": "b" * 40,
            "file_count": 1,
            "files": [record],
            "tree_sha256": adapter._canonical_source_tree_sha256([record]),
        }

    def test_governed_build_uses_exact_local_toolchain_commands(self):
        calls = []
        def record(command, **kwargs):
            calls.append((command, kwargs))

        reviewed_environment = {
            "REACT_APP_BACKEND_URL": {
                "present": True,
                "value": "https://mezansalla.com",
            }
        }
        with (
            patch.object(adapter, "_toolchain", side_effect=record),
            patch.dict(
                os.environ,
                {"DB_PASSWORD": "must-not-reach-toolchain"},
                clear=False,
            ),
        ):
            adapter._governed_build(
                "a" * 40,
                client_environment=reviewed_environment,
            )

        self.assertEqual(calls[0][0], ["ensure"])
        self.assertEqual(
            calls[1][0],
            [
                "exec", "--", "yarn", "--cwd", "frontend", "install",
                "--frozen-lockfile", "--non-interactive",
            ],
        )
        self.assertEqual(
            calls[2][0],
            ["exec", "--", "yarn", "--cwd", "frontend", "build:release"],
        )
        self.assertEqual(
            calls[3][0][-3:],
            ["--expected-git-sha", "a" * 40, "--reviewed-intent-v5"],
        )
        for _, kwargs in calls:
            self.assertEqual(
                kwargs["env"]["REACT_APP_BACKEND_URL"],
                "https://mezansalla.com",
            )
            self.assertNotIn("DB_PASSWORD", kwargs["env"])
            self.assertEqual(kwargs["env"]["PYTHONDONTWRITEBYTECODE"], "1")
            self.assertNotEqual(kwargs["env"].get("HOME"), os.environ.get("HOME"))

    def test_toolchain_cache_must_be_absolute_and_outside_worktree(self):
        with tempfile.TemporaryDirectory() as temporary:
            outside = Path(temporary) / "cache"
            self.assertEqual(
                adapter._toolchain_cache_home({"XDG_CACHE_HOME": str(outside)}),
                str(outside),
            )
        with self.assertRaisesRegex(
            adapter.DeploymentAdapterError,
            "absolute path",
        ):
            adapter._toolchain_cache_home({"XDG_CACHE_HOME": "relative/cache"})
        with self.assertRaisesRegex(
            adapter.DeploymentAdapterError,
            "outside the Git worktree",
        ):
            adapter._toolchain_cache_home({
                "XDG_CACHE_HOME": str(adapter.REPO_ROOT / ".cache"),
            })

    def test_verifier_can_use_exact_reviewed_intent_without_git(self):
        frontend_build = {"git_sha": "a" * 40, "artifact_tree_sha256": "b" * 64}
        proof = {"kind": "frontend_two_clean_builds_v1"}
        intent = {
            "source_git_sha": "a" * 40,
            "frontend_build": frontend_build,
            "frontend_reproducibility": proof,
        }
        output = io.StringIO()
        with (
            patch.object(adapter, "load_release_intent", return_value=intent),
            patch.object(
                verifier,
                "read_frontend_build_identity",
                return_value=frontend_build,
            ) as identity_reader,
            patch.object(
                verifier,
                "read_frontend_reproducibility_proof",
                return_value=proof,
            ),
            patch("sys.stdout", output),
        ):
            status = verifier.main([
                "--expected-git-sha",
                "a" * 40,
                "--reviewed-intent-v5",
            ])

        self.assertEqual(status, 0)
        identity_reader.assert_called_once_with(
            expected_git_sha="a" * 40,
            require_git_source=False,
        )
        self.assertEqual(
            json.loads(output.getvalue())["frontend_build"],
            frontend_build,
        )

    def test_verifier_without_reviewed_intent_still_requires_git(self):
        frontend_build = {"git_sha": "a" * 40}
        proof = {"kind": "frontend_two_clean_builds_v1"}
        with (
            patch.object(
                verifier,
                "read_frontend_build_identity",
                return_value=frontend_build,
            ) as identity_reader,
            patch.object(
                verifier,
                "read_frontend_reproducibility_proof",
                return_value=proof,
            ),
            patch("sys.stdout", io.StringIO()),
        ):
            self.assertEqual(
                verifier.main(["--expected-git-sha", "a" * 40]),
                0,
            )
        identity_reader.assert_called_once_with(
            expected_git_sha="a" * 40,
            require_git_source=True,
        )

    def test_build_starts_clean_and_removes_all_outputs_after_failure(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with self._roots(root):
                for path in (
                    adapter.BUILD_ROOT / "stale.js",
                    adapter.PROOF_PATH,
                    adapter.IDENTITY_PATH,
                    adapter.FRONTEND_ROOT / "node_modules" / "stale.js",
                ):
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text("stale", encoding="utf-8")
                with (
                    patch.object(adapter, "cloud_build_evidence", return_value={}),
                    patch.object(
                        adapter,
                        "_materialize_cloud_build_backend_requirements",
                        return_value={"restored": False},
                    ),
                    patch.object(adapter, "load_release_intent", side_effect=adapter.DeploymentAdapterError("bad intent")),
                    self.assertRaisesRegex(adapter.DeploymentAdapterError, "bad intent"),
                ):
                    adapter.build_cloud_release()

                self.assertFalse(adapter.BUILD_ROOT.exists())
                self.assertFalse(adapter.PROOF_PATH.exists())
                self.assertFalse(adapter.IDENTITY_PATH.exists())
                self.assertFalse((adapter.FRONTEND_ROOT / "node_modules").exists())

    def test_build_rejects_symlinked_root_before_cleanup_touches_target(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with self._roots(root):
                real_frontend = root / "outside-frontend"
                adapter.FRONTEND_ROOT.rename(real_frontend)
                sentinel = real_frontend / "build" / "must-survive.txt"
                sentinel.parent.mkdir(parents=True)
                sentinel.write_text("must survive\n", encoding="utf-8")
                adapter.FRONTEND_ROOT.symlink_to(
                    real_frontend,
                    target_is_directory=True,
                )

                with self.assertRaisesRegex(
                    adapter.DeploymentAdapterError,
                    "Frontend source root is not a real directory",
                ):
                    adapter.build_cloud_release()

                self.assertEqual(
                    sentinel.read_text(encoding="utf-8"),
                    "must survive\n",
                )

    def test_cloud_build_restores_manifest_bound_requirements_before_validation(
        self,
    ):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with self._roots(root):
                _, source_base, source = self._git_manifest_history(root)
                intent = self._candidate_intent(source_base, source)
                adapter.INTENT_PATH.write_text(
                    json.dumps(intent),
                    encoding="utf-8",
                )
                (root / ".git").rename(root / "held-git")
                (root / ".github" / "workflows" / "release.yml").unlink()
                (root / ".github" / "workflows").rmdir()
                (root / ".github").rmdir()
                requirements = adapter.BACKEND_ROOT / "requirements.txt"
                reviewed = (
                    adapter.REPO_ROOT / "scripts" / "release_backend_requirements.lock"
                )
                requirements.write_text(
                    "platform-added-package==9.9\n",
                    encoding="utf-8",
                )

                result = adapter._materialize_cloud_build_backend_requirements()

                self.assertTrue(result["restored"])
                self.assertEqual(
                    requirements.read_bytes(),
                    reviewed.read_bytes(),
                )
                adapter._load_and_validate_release_intent(
                    adapter.INTENT_PATH,
                    verify_deployment_git=False,
                )

    def test_cloud_requirements_reject_tampered_reviewed_copy_before_write(
        self,
    ):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with self._roots(root):
                _, source_base, source = self._git_manifest_history(root)
                intent = self._candidate_intent(source_base, source)
                adapter.INTENT_PATH.write_text(
                    json.dumps(intent),
                    encoding="utf-8",
                )
                (root / ".git").rename(root / "held-git")
                requirements = adapter.BACKEND_ROOT / "requirements.txt"
                reviewed = (
                    adapter.REPO_ROOT / "scripts" / "release_backend_requirements.lock"
                )
                platform_bytes = b"platform-added-package==9.9\n"
                requirements.write_bytes(platform_bytes)
                reviewed.write_text("tampered==1.0\n", encoding="utf-8")

                with self.assertRaisesRegex(
                    adapter.DeploymentAdapterError,
                    r"Release control source mismatch.*release_backend_requirements\.lock",
                ):
                    adapter._materialize_cloud_build_backend_requirements()

                self.assertEqual(requirements.read_bytes(), platform_bytes)

    def test_cloud_requirements_reject_symlink_without_touching_target(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with self._roots(root):
                _, source_base, source = self._git_manifest_history(root)
                intent = self._candidate_intent(source_base, source)
                adapter.INTENT_PATH.write_text(
                    json.dumps(intent),
                    encoding="utf-8",
                )
                (root / ".git").rename(root / "held-git")
                requirements = adapter.BACKEND_ROOT / "requirements.txt"
                sentinel = root / "must-survive.txt"
                sentinel.write_text("must survive\n", encoding="utf-8")
                requirements.unlink()
                requirements.symlink_to(sentinel)

                with self.assertRaisesRegex(
                    adapter.DeploymentAdapterError,
                    "Backend requirements must be a regular file",
                ):
                    adapter._materialize_cloud_build_backend_requirements()

                self.assertEqual(
                    sentinel.read_text(encoding="utf-8"),
                    "must survive\n",
                )

    def test_git_workspace_requirements_mismatch_is_not_repaired(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with self._roots(root):
                _, source_base, source = self._git_manifest_history(root)
                intent = self._candidate_intent(source_base, source)
                adapter.INTENT_PATH.write_text(
                    json.dumps(intent),
                    encoding="utf-8",
                )
                requirements = adapter.BACKEND_ROOT / "requirements.txt"
                platform_bytes = b"local-tamper==9.9\n"
                requirements.write_bytes(platform_bytes)

                with self.assertRaisesRegex(
                    adapter.DeploymentAdapterError,
                    "reviewed Backend requirements differ",
                ):
                    adapter._materialize_cloud_build_backend_requirements()

                self.assertEqual(requirements.read_bytes(), platform_bytes)

    def test_repository_reviewed_requirements_match_runtime_source(self):
        result = adapter._materialize_cloud_build_backend_requirements()

        self.assertFalse(result["restored"])
        self.assertEqual(
            result["sha256"],
            _sha256((adapter.BACKEND_ROOT / "requirements.txt").read_bytes()),
        )

    def test_reviewed_source_validates_without_git_and_rejects_tamper(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with self._roots(root):
                source = self._source(adapter.FRONTEND_ROOT)
                self.assertEqual(
                    adapter._validated_frontend_source(source),
                    source,
                )
                self.assertFalse((root / ".git").exists())
                (adapter.FRONTEND_ROOT / "src.js").write_text(
                    "tampered\n", encoding="utf-8"
                )
                with self.assertRaisesRegex(
                    adapter.DeploymentAdapterError,
                    "differs from reviewed intent",
                ):
                    adapter._validated_frontend_source(source)

    def test_reviewed_source_rejects_non_integer_count_and_symlink_root(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with self._roots(root):
                source = self._source(adapter.FRONTEND_ROOT)
                for invalid in (True, 1.0, "1"):
                    with self.subTest(invalid=invalid), self.assertRaisesRegex(
                        adapter.DeploymentAdapterError,
                        "source count is invalid",
                    ):
                        adapter._validated_frontend_source({
                            **source,
                            "file_count": invalid,
                        })

                real_frontend = root / "real-frontend"
                adapter.FRONTEND_ROOT.rename(real_frontend)
                adapter.FRONTEND_ROOT.symlink_to(real_frontend, target_is_directory=True)
                with self.assertRaisesRegex(
                    adapter.DeploymentAdapterError,
                    "Frontend source root is not a real directory",
                ):
                    adapter._validated_frontend_source(source)

    def test_git_manifests_bind_j_to_a_with_exact_delta_and_tombstones(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with self._roots(root):
                _, source_base, source = self._git_manifest_history(root)
                sidecar = root / "backend" / ".env"
                sidecar.write_text("RUNTIME_ONLY=value\n", encoding="utf-8")
                sidecar.chmod(0o600)

                adapter._assert_freeze_git_state(
                    source_git_sha=source,
                    source_base_git_sha=source_base,
                )
                adapter._assert_candidate_source_transition(
                    source_git_sha=source,
                    source_base_git_sha=source_base,
                )
                backend = adapter._git_source_manifest(
                    scope=adapter.BACKEND_RUNTIME_SOURCE_SCOPE,
                    source_git_sha=source,
                    source_base_git_sha=source_base,
                )
                control = adapter._git_source_manifest(
                    scope=adapter.RELEASE_CONTROL_SOURCE_SCOPE,
                    source_git_sha=source,
                    source_base_git_sha=source_base,
                )

                self.assertEqual(backend["source_git_sha"], source)
                self.assertEqual(backend["source_base_git_sha"], source_base)
                self.assertEqual(
                    backend["source_root_tree_oid"],
                    self._git(root, "rev-parse", f"{source}^{{tree}}"),
                )
                self.assertEqual(
                    backend["scope_tree_oid"],
                    self._git(root, "rev-parse", f"{source}:backend"),
                )
                self.assertEqual(backend["added_count"], 1)
                self.assertEqual(backend["modified_count"], 2)
                self.assertEqual(backend["deleted_count"], 1)
                self.assertEqual(
                    [row["path"] for row in backend["tombstones"]],
                    ["delete.py"],
                )
                self.assertEqual(
                    next(
                        row["mode"] for row in backend["files"]
                        if row["path"] == "mode.py"
                    ),
                    "100755",
                )
                self.assertFalse(any(
                    row["path"].startswith("tests/")
                    for row in backend["files"]
                ))
                self.assertEqual(control["added_count"], 1)
                self.assertRegex(backend["manifest_sha256"], r"^[0-9a-f]{64}$")
                validated = adapter.validate_backend_runtime_source_manifest(
                    backend,
                    backend_root=adapter.BACKEND_ROOT,
                    source_git_sha=source,
                    source_base_git_sha=source_base,
                    allow_derived_bytecode=False,
                )
                self.assertFalse(any(
                    row["path"] == ".env" for row in validated["files"]
                ))

    def test_git_review_rederives_manifests_and_rejects_oid_tampering(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with self._roots(root):
                _, source_base, source = self._git_manifest_history(root)
                backend = adapter._git_source_manifest(
                    scope=adapter.BACKEND_RUNTIME_SOURCE_SCOPE,
                    source_git_sha=source,
                    source_base_git_sha=source_base,
                )
                control = adapter._git_source_manifest(
                    scope=adapter.RELEASE_CONTROL_SOURCE_SCOPE,
                    source_git_sha=source,
                    source_base_git_sha=source_base,
                )
                intent = {
                    "source_git_sha": source,
                    "source_base_git_sha": source_base,
                    "backend_runtime_source": backend,
                    "release_control_source": control,
                }
                intent_path = root / "release" / "release-intent-v5.json"
                intent_path.write_text(json.dumps(intent), encoding="utf-8")
                self._git(root, "add", "release/release-intent-v5.json")
                self._git(root, "commit", "-qm", "intent B")
                deployed = self._git(root, "rev-parse", "HEAD")

                proof = adapter.verify_release_intent_git(intent)
                self.assertEqual(proof["deployment_git_sha"], deployed)
                tampered = json.loads(json.dumps(intent))
                tampered["backend_runtime_source"]["scope_tree_oid"] = "f" * 40
                with self.assertRaisesRegex(
                    adapter.DeploymentAdapterError,
                    "differs from Git J-to-A",
                ):
                    adapter.verify_release_intent_git(tampered)

    def test_git_collector_rejects_secret_before_blob_read_and_excluded_symlink(self):
        for entry in ("secret", "excluded-symlink"):
            with self.subTest(entry=entry), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                with self._roots(root):
                    self._git(root, "init", "-q")
                    self._git(root, "config", "user.name", "Release Test")
                    self._git(root, "config", "user.email", "release@example.test")
                    if entry == "secret":
                        path = root / "backend" / ".env"
                        path.write_text("DO_NOT_PRINT=this-value\n", encoding="utf-8")
                    else:
                        path = root / "backend" / "tests" / "linked.py"
                        path.parent.mkdir(parents=True, exist_ok=True)
                        path.symlink_to(root / "backend" / "server.py")
                    self._git(root, "add", ".")
                    self._git(root, "commit", "-qm", "unsafe source")
                    source = self._git(root, "rev-parse", "HEAD")
                    with (
                        patch.object(adapter, "_git_blob_contents") as blob_reader,
                        self.assertRaises(adapter.DeploymentAdapterError) as raised,
                    ):
                        adapter._git_scope_records(
                            source, scope=adapter.BACKEND_RUNTIME_SOURCE_SCOPE
                        )
                    blob_reader.assert_not_called()
                    self.assertNotIn("DO_NOT_PRINT", str(raised.exception))

    def test_freeze_preflight_rejects_dirty_untracked_without_output(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            output = root.parent / f"{root.name}-intent.json"
            with self._roots(root):
                _, source_base, source = self._git_manifest_history(root)
                (root / "backend" / "unreviewed.py").write_text(
                    "unreviewed\n", encoding="utf-8"
                )
                with self.assertRaisesRegex(
                    adapter.DeploymentAdapterError,
                    "clean governed source worktree",
                ):
                    adapter.freeze_intent(
                        source,
                        source_base,
                        "hotfix/prod-snap-meta-final",
                        output,
                    )
                self.assertFalse(output.exists())

    def test_candidate_rejects_intent_touched_then_restored_and_in_tree_output(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with self._roots(root):
                _, source_base, _ = self._git_manifest_history(root)
                intent_path = root / "release" / "release-intent-v5.json"
                original = intent_path.read_bytes()
                intent_path.write_text("{}\n", encoding="utf-8")
                self._git(root, "add", "release/release-intent-v5.json")
                self._git(root, "commit", "-qm", "touch intent")
                intent_path.write_bytes(original)
                self._git(root, "add", "release/release-intent-v5.json")
                self._git(root, "commit", "-qm", "restore intent")
                source = self._git(root, "rev-parse", "HEAD")

                with self.assertRaisesRegex(
                    adapter.DeploymentAdapterError,
                    "without changing intent",
                ):
                    adapter._assert_candidate_source_transition(
                        source_git_sha=source,
                        source_base_git_sha=source_base,
                    )
                with self.assertRaisesRegex(
                    adapter.DeploymentAdapterError,
                    "outside the Git worktree",
                ):
                    adapter._freeze_output_path(root / "intent.json")

    def test_candidate_loader_accepts_a_but_tracked_loader_still_requires_b(self):
        with tempfile.TemporaryDirectory() as temporary:
            outer = Path(temporary)
            root = outer / "repo"
            root.mkdir()
            candidate_path = outer / "candidate.json"
            with self._roots(root):
                _, source_base, source = self._git_manifest_history(root)
                candidate = self._candidate_intent(source_base, source)
                candidate_path.write_text(
                    json.dumps(candidate), encoding="utf-8"
                )

                loaded = adapter.load_candidate_release_intent(
                    candidate_path,
                    source_git_sha=source,
                    source_base_git_sha=source_base,
                )
                self.assertEqual(loaded, candidate)
                with self.assertRaisesRegex(
                    adapter.DeploymentAdapterError,
                    "not exact intent-only B",
                ):
                    adapter.load_release_intent(candidate_path)
                with self.assertRaises(adapter.DeploymentAdapterError):
                    adapter.load_candidate_release_intent(
                        candidate_path,
                        source_git_sha="f" * 40,
                        source_base_git_sha=source_base,
                    )
                with self.assertRaisesRegex(
                    adapter.DeploymentAdapterError,
                    "outside the Git worktree",
                ):
                    adapter.load_candidate_release_intent(
                        root / "candidate.json",
                        source_git_sha=source,
                        source_base_git_sha=source_base,
                    )
                (root / "backend" / "dirty.py").write_text(
                    "dirty\n", encoding="utf-8"
                )
                with self.assertRaisesRegex(
                    adapter.DeploymentAdapterError,
                    "clean governed source worktree",
                ):
                    adapter.load_candidate_release_intent(
                        candidate_path,
                        source_git_sha=source,
                        source_base_git_sha=source_base,
                    )

    def test_release_intent_requires_exact_fields_and_branch_mirror(self):
        source_summary = {
            "scope": "git_head_frontend_tree_v1",
            "git_tree_oid": "b" * 40,
            "file_count": 1,
            "tree_sha256": "c" * 64,
        }
        frontend_source = {**source_summary, "files": [{}]}
        frontend_build = {"source": source_summary}
        proof = {"kind": "proof"}
        critical = {"server.py": "d" * 64}
        backend_source = {"scope": "backend-runtime"}
        control_source = {"scope": "release-control", "files": []}
        control_summary = {"scope": "release-control"}
        runtime = {
            "source_git_sha": "a" * 40,
            "source_base_git_sha": "b" * 40,
            "branch": "hotfix/prod-snap-meta-final",
            "backend_runtime_source": backend_source,
            "release_control_source": control_summary,
            "frontend_build": frontend_build,
            "frontend_reproducibility": proof,
            "critical_file_hashes": critical,
        }
        payload = {
            "schema_version": 2,
            "kind": adapter.INTENT_KIND,
            "protocol_version": 5,
            "source_git_sha": "a" * 40,
            "source_base_git_sha": "b" * 40,
            "branch": "hotfix/prod-snap-meta-final",
            "frontend_source": frontend_source,
            "backend_runtime_source": backend_source,
            "release_control_source": control_source,
            "client_environment": {
                "REACT_APP_BACKEND_URL": {
                    "present": True,
                    "value": "https://mezansalla.com",
                }
            },
            "frontend_build": frontend_build,
            "frontend_reproducibility": proof,
            "critical_file_hashes": critical,
            "runtime_identity": runtime,
        }
        with patch.object(adapter, "_load_json", return_value={**payload, "extra": True}):
            with self.assertRaisesRegex(
                adapter.DeploymentAdapterError,
                "fields are not canonical",
            ):
                adapter.load_release_intent()

        for field, invalid_values in (
            ("schema_version", (True, 2.0, "2")),
            ("protocol_version", (True, 5.0, "5")),
            ("source_git_sha", (int("1" * 40),)),
        ):
            for invalid in invalid_values:
                with (
                    self.subTest(field=field, invalid=invalid),
                    patch.object(
                        adapter,
                        "_load_json",
                        return_value={**payload, field: invalid},
                    ),
                    self.assertRaises(adapter.DeploymentAdapterError),
                ):
                    adapter.load_release_intent()

        with (
            patch.object(
                adapter,
                "_load_json",
                return_value={**payload, "branch": "wrong-branch"},
            ),
            patch.object(
                adapter,
                "_validated_frontend_source",
                return_value=frontend_source,
            ),
            patch.object(
                adapter,
                "_reviewed_client_environment",
                return_value=payload["client_environment"],
            ),
            patch.object(adapter, "_critical_hashes", return_value=critical),
            patch.object(
                adapter,
                "validate_backend_runtime_source_manifest",
                return_value=backend_source,
            ),
            patch.object(
                adapter,
                "validate_release_control_source_manifest",
                return_value=control_source,
            ),
            patch.object(
                adapter,
                "source_manifest_summary",
                return_value=control_summary,
            ),
            patch.object(adapter, "_validate_runtime_identity", return_value=runtime),
            self.assertRaisesRegex(
                adapter.DeploymentAdapterError,
                "branch differs",
            ),
        ):
            adapter.load_release_intent()

        strict_runtime = {
            **runtime,
            "frontend_build": {
                **frontend_build,
                "environment": {"VITE_USER_NODE_ENV_present": False},
            },
        }
        strict_payload = {
            **payload,
            "frontend_build": {
                **frontend_build,
                "environment": {"VITE_USER_NODE_ENV_present": 0},
            },
            "runtime_identity": strict_runtime,
        }
        with (
            patch.object(adapter, "_load_json", return_value=strict_payload),
            patch.object(
                adapter,
                "_validated_frontend_source",
                return_value=frontend_source,
            ),
            patch.object(
                adapter,
                "_reviewed_client_environment",
                return_value=payload["client_environment"],
            ),
            patch.object(adapter, "_critical_hashes", return_value=critical),
            patch.object(
                adapter,
                "validate_backend_runtime_source_manifest",
                return_value=backend_source,
            ),
            patch.object(
                adapter,
                "validate_release_control_source_manifest",
                return_value=control_source,
            ),
            patch.object(
                adapter,
                "source_manifest_summary",
                return_value=control_summary,
            ),
            patch.object(
                adapter,
                "_validate_runtime_identity",
                return_value=strict_runtime,
            ),
            self.assertRaisesRegex(
                adapter.DeploymentAdapterError,
                "frontend_build differs",
            ),
        ):
            adapter.load_release_intent()

    def test_materialization_is_atomic_and_exact(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with self._roots(root):
                expected_build = {"git_sha": "a" * 40, "artifact_tree_sha256": "c" * 64}
                expected_proof = {"proof_file": {"sha256": "d" * 64}}
                expected_identity = {
                    "protocol_version": 5,
                    "source_git_sha": "a" * 40,
                    "release_id": "rg5-" + "e" * 64,
                }
                intent = {
                    "source_git_sha": "a" * 40,
                    "frontend_build": expected_build,
                    "frontend_reproducibility": expected_proof,
                    "runtime_identity": expected_identity,
                }
                with (
                    patch.object(
                        adapter,
                        "_read_frontend_evidence",
                        return_value=(expected_build, expected_proof),
                    ),
                    patch.object(
                        adapter,
                        "_validate_runtime_identity",
                        return_value=expected_identity,
                    ),
                ):
                    result = adapter.materialize_identity(intent)
                self.assertEqual(result, expected_identity)
                self.assertEqual(
                    json.loads(adapter.IDENTITY_PATH.read_text(encoding="utf-8")),
                    expected_identity,
                )
                self.assertEqual(
                    stat.S_IMODE(adapter.IDENTITY_PATH.stat().st_mode),
                    0o644,
                )
                self.assertEqual(
                    list(adapter.BACKEND_ROOT.glob(".release_identity.json.*.tmp")),
                    [],
                )

    def test_generated_release_symlink_is_unlinked_without_touching_target(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            outside = root / "outside"
            outside.mkdir()
            sentinel = outside / "reproducible-build.json"
            sentinel.write_text("must survive\n", encoding="utf-8")
            with self._roots(root):
                adapter.PROOF_PATH.parent.rmdir()
                adapter.PROOF_PATH.parent.symlink_to(outside, target_is_directory=True)
                adapter.clean_generated_state(remove_dependencies=False)
                self.assertFalse(adapter.PROOF_PATH.parent.exists())
                self.assertEqual(
                    sentinel.read_text(encoding="utf-8"),
                    "must survive\n",
                )

    def test_materialization_refuses_artifact_not_in_reviewed_intent(self):
        intent = {
            "source_git_sha": "a" * 40,
            "frontend_build": {"artifact_tree_sha256": "b" * 64},
            "frontend_reproducibility": {},
            "runtime_identity": {},
        }
        with (
            patch.object(
                adapter,
                "_read_frontend_evidence",
                return_value=({"artifact_tree_sha256": "c" * 64}, {}),
            ),
            self.assertRaisesRegex(
                adapter.DeploymentAdapterError,
                "differs from reviewed release intent",
            ),
        ):
            adapter.materialize_identity(intent)

    def test_cloud_evidence_exposes_only_sanitized_build_contract(self):
        with (
            patch.object(adapter, "_version", side_effect=["v20.20.2", "1.22.22"]),
            patch.object(adapter.platform, "system", return_value="Linux"),
            patch.object(adapter.platform, "machine", return_value="aarch64"),
            patch.dict(os.environ, {"DB_PASSWORD": "never-log-this"}),
        ):
            payload = adapter.cloud_build_evidence()
        serialized = json.dumps(payload)
        self.assertEqual(payload["host_node"], "v20.20.2")
        self.assertEqual(payload["host_yarn"], "1.22.22")
        self.assertEqual(payload["architecture"], "aarch64")
        self.assertTrue(payload["source_roots_declared_co_parented"])
        self.assertFalse(payload["platform_snapshot_workspace_shared_observed"])
        self.assertFalse(payload["outer_install_command_observed"])
        self.assertIsNone(payload["outer_install_command"])
        self.assertFalse(payload["outer_build_command_observed"])
        self.assertEqual(
            payload["configured_package_build_command"],
            "cd frontend && yarn build",
        )
        self.assertNotIn("never-log-this", serialized)
        self.assertNotIn("DB_PASSWORD", serialized)

    def test_isolated_backend_probe_allows_normal_bytecode_generation(self):
        environment = adapter._isolated_backend_probe_environment()
        self.assertNotIn("PYTHONDONTWRITEBYTECODE", environment)

    def test_package_record_scan_rejects_special_nodes(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            os.mkfifo(root / "unexpected.fifo")
            with self.assertRaisesRegex(
                adapter.DeploymentAdapterError,
                "unsupported entry",
            ):
                adapter._file_records(root)

    def test_frontend_public_env_requires_explicit_exact_allowlist(self):
        content = b"# Public Emergent package comments only.\n"
        for permissions in (0o600, 0o644):
            with self.subTest(permissions=oct(permissions)):
                with tempfile.TemporaryDirectory() as temporary:
                    root = Path(temporary)
                    public_env = root / ".env"
                    public_env.write_bytes(content)
                    public_env.chmod(permissions)

                    with self.assertRaisesRegex(
                        adapter.DeploymentAdapterError,
                        "forbidden sensitive path",
                    ):
                        adapter._file_records(root)

                    self.assertEqual(
                        adapter._file_records(
                            root,
                            allowed_public_paths=(
                                adapter.FRONTEND_PUBLIC_PACKAGE_PATHS
                            ),
                        ),
                        [{
                            "path": ".env",
                            "bytes": len(content),
                            "sha256": _sha256(content),
                        }],
                    )

    def test_frontend_public_env_allowlist_rejects_variants_and_unsafe_nodes(self):
        for relative in (".env.local", "nested/.env"):
            with self.subTest(relative=relative):
                with tempfile.TemporaryDirectory() as temporary:
                    root = Path(temporary)
                    path = root / relative
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text("not public\n", encoding="utf-8")
                    with self.assertRaisesRegex(
                        adapter.DeploymentAdapterError,
                        "forbidden sensitive path",
                    ):
                        adapter._file_records(
                            root,
                            allowed_public_paths=(
                                adapter.FRONTEND_PUBLIC_PACKAGE_PATHS
                            ),
                        )

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = root / "public-comments"
            target.write_text("not reached\n", encoding="utf-8")
            (root / ".env").symlink_to(target)
            with self.assertRaisesRegex(
                adapter.DeploymentAdapterError,
                "must be a regular file",
            ):
                adapter._file_records(
                    root,
                    allowed_public_paths=adapter.FRONTEND_PUBLIC_PACKAGE_PATHS,
                )

        for permissions, message in (
            (0o700, "must be non-executable"),
            (0o666, "unsafe file permissions"),
        ):
            with self.subTest(permissions=oct(permissions)):
                with tempfile.TemporaryDirectory() as temporary:
                    root = Path(temporary)
                    public_env = root / ".env"
                    public_env.write_text("public comments\n", encoding="utf-8")
                    public_env.chmod(permissions)
                    with self.assertRaisesRegex(
                        adapter.DeploymentAdapterError,
                        message,
                    ):
                        adapter._file_records(
                            root,
                            allowed_public_paths=(
                                adapter.FRONTEND_PUBLIC_PACKAGE_PATHS
                            ),
                        )

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / ".env").write_text("public comments\n", encoding="utf-8")
            with self.assertRaisesRegex(
                adapter.DeploymentAdapterError,
                "public-path allowlist is invalid",
            ):
                adapter._file_records(
                    root,
                    allowed_public_paths=frozenset({".env.local"}),
                )

    def test_reviewed_client_environment_is_bound_without_logging_secrets(self):
        reviewed = adapter._reviewed_client_environment({
            "REACT_APP_BACKEND_URL": {
                "present": True,
                "value": "https://mezansalla.com",
            }
        })
        build = {
            "environment": {
                "values": {
                    "REACT_APP_BACKEND_URL": {
                        "present": True,
                        "sha256": _sha256(b"https://mezansalla.com"),
                    }
                }
            }
        }
        adapter._assert_client_environment_binding(
            reviewed=reviewed,
            frontend_build=build,
        )
        with self.assertRaisesRegex(
            adapter.DeploymentAdapterError,
            "not approved",
        ):
            adapter._reviewed_client_environment({
                "REACT_APP_BACKEND_URL": {
                    "present": True,
                    "value": "https://user:secret@example.test",
                }
            })
        for rejected in (
            "http://mezansalla.com",
            "https://evil.example.test",
            "https://mezansalla.com/api",
        ):
            with self.subTest(rejected=rejected), self.assertRaisesRegex(
                adapter.DeploymentAdapterError,
                "not approved",
            ):
                adapter._reviewed_client_environment({
                    "REACT_APP_BACKEND_URL": {
                        "present": True,
                        "value": rejected,
                    }
                })

    def test_package_boundary_rejects_workspace_only_metadata(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with self._roots(root):
                adapter.BUILD_ROOT.mkdir(parents=True)
                (adapter.BUILD_ROOT / "index.html").write_text(
                    "<html></html>", encoding="utf-8"
                )
                (adapter.BACKEND_ROOT / "release_identity.json").write_text(
                    "{}\n", encoding="utf-8"
                )
                intent = {
                    "source_git_sha": "a" * 40,
                    "runtime_identity": {
                        "release_id": "rg5-" + "b" * 64,
                        "critical_file_hashes": {},
                    },
                    "frontend_build": {
                        "build_meta": {
                            "path": "build-meta.json",
                            "bytes": 2,
                            "sha256": _sha256(b"{}"),
                        },
                        "public_files": [],
                        "artifact_tree_sha256": "c" * 64,
                    },
                }
                with self.assertRaisesRegex(
                    adapter.DeploymentAdapterError,
                    "missing exact build-meta",
                ):
                    adapter.verify_package_boundaries(intent)

    def test_package_boundary_never_dereferences_frontend_symlink(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            outside = root / "outside.txt"
            outside.write_text("outside\n", encoding="utf-8")
            with self._roots(root):
                adapter.BUILD_ROOT.mkdir(parents=True)
                (adapter.BUILD_ROOT / "index.html").write_text(
                    "<html></html>", encoding="utf-8"
                )
                (adapter.BUILD_ROOT / "leak.txt").symlink_to(outside)
                (adapter.BACKEND_ROOT / "release_identity.json").write_text(
                    "{}\n", encoding="utf-8"
                )
                intent = {
                    "source_git_sha": "a" * 40,
                    "runtime_identity": {
                        "release_id": "rg5-" + "b" * 64,
                        "critical_file_hashes": {},
                    },
                    "frontend_build": {
                        "build_meta": {
                            "path": "build-meta.json",
                            "bytes": 2,
                            "sha256": _sha256(b"{}"),
                        },
                        "public_files": [],
                        "artifact_tree_sha256": "c" * 64,
                    },
                }
                with self.assertRaisesRegex(
                    adapter.DeploymentAdapterError,
                    "contains symlink",
                ):
                    adapter.verify_package_boundaries(intent)

    def test_package_boundary_requires_build_meta_route_middleware(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with self._roots(root):
                adapter.BUILD_ROOT.mkdir(parents=True)
                index = b"<html>governed</html>\n"
                metadata = b"{}"
                (adapter.BUILD_ROOT / "index.html").write_bytes(index)
                (adapter.BUILD_ROOT / "build-meta.json").write_bytes(metadata)
                (adapter.FRONTEND_ROOT / "scripts").mkdir()
                for relative in (
                    "package.json",
                    "scripts/start-governed-runtime.cjs",
                    "vite.config.js",
                    "yarn.lock",
                ):
                    target = adapter.FRONTEND_ROOT / relative
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_text("fixture\n", encoding="utf-8")
                (adapter.BACKEND_ROOT / "release_identity.json").write_text(
                    "{}\n", encoding="utf-8"
                )
                intent = {
                    "source_git_sha": "a" * 40,
                    "runtime_identity": {
                        "release_id": "rg5-" + "b" * 64,
                        "critical_file_hashes": {},
                    },
                    "frontend_build": {
                        "build_meta": {
                            "path": "build-meta.json",
                            "bytes": len(metadata),
                            "sha256": _sha256(metadata),
                        },
                        "public_files": [{
                            "path": "index.html",
                            "bytes": len(index),
                            "sha256": _sha256(index),
                        }],
                        "artifact_tree_sha256": "c" * 64,
                    },
                }
                with self.assertRaisesRegex(
                    adapter.DeploymentAdapterError,
                    "lost runtime file: scripts/governed-preview.cjs",
                ):
                    adapter.verify_package_boundaries(intent)

    def test_isolated_backend_health_allows_only_boot_timestamp_enrichment(self):
        isolated = {
            "verified_identity_available": True,
            "release_id": "rg5-" + "a" * 64,
            "source_git_sha": "b" * 40,
            "critical_file_hashes_match": True,
        }
        health = {
            "ok": True,
            "service": "backend",
            "release": {
                **isolated,
                "boot_started_at": "2026-08-30T03:29:14+00:00",
            },
        }
        adapter._assert_isolated_backend_health(isolated, health)

        for label, drifted in (
            (
                "missing timestamp",
                {**health, "release": dict(isolated)},
            ),
            (
                "identity drift",
                {
                    **health,
                    "release": {
                        **health["release"],
                        "release_id": "rg5-" + "c" * 64,
                    },
                },
            ),
            (
                "unexpected field",
                {
                    **health,
                    "release": {**health["release"], "extra": True},
                },
            ),
            (
                "unexpected top-level field",
                {**health, "extra": True},
            ),
        ):
            with self.subTest(label=label), self.assertRaisesRegex(
                adapter.DeploymentAdapterError,
                "health payload differs",
            ):
                adapter._assert_isolated_backend_health(isolated, drifted)


if __name__ == "__main__":
    unittest.main()
