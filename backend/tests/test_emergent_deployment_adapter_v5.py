from __future__ import annotations

import hashlib
import io
import json
import os
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
        runtime = {
            "source_git_sha": "a" * 40,
            "branch": "hotfix/prod-snap-meta-final",
            "frontend_build": frontend_build,
            "frontend_reproducibility": proof,
            "critical_file_hashes": critical,
        }
        payload = {
            "schema_version": 1,
            "kind": adapter.INTENT_KIND,
            "protocol_version": 5,
            "source_git_sha": "a" * 40,
            "branch": "hotfix/prod-snap-meta-final",
            "frontend_source": frontend_source,
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
            ("schema_version", (True, 1.0, "1")),
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
