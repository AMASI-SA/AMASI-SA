from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import release_identity as release_identity_module
import release_protocol_v5 as release_protocol_module

from release_identity import (
    CRITICAL_FILES,
    RELEASE_PROTOCOL_VERSION,
    read_release_identity,
    release_health_payload,
)
from release_protocol_v5 import (
    BACKEND_RUNTIME_SOURCE_SCOPE,
    RELEASE_CONTROL_SOURCE_SCOPE,
    RELEASE_IDENTITY_KIND,
    RELEASE_IDENTITY_SCHEMA_VERSION,
    ReleaseProtocolV5Error,
    build_runtime_release_identity,
    build_source_manifest,
    canonical_identity_core,
    deterministic_release_id,
    exact_json_equal,
    validate_runtime_release_identity,
)
from frontend_build_identity import (
    RETIREMENT_SERVICE_WORKER_BYTES,
    RETIREMENT_SERVICE_WORKER_SHA256,
)


SOURCE_GIT_SHA = "a" * 40
BRANCH = "hotfix/prod-snap-meta-final"


class ReleaseIdentityTests(unittest.TestCase):
    def test_exact_json_comparison_never_coerces_boolean_or_number_types(self):
        self.assertTrue(exact_json_equal({"value": False}, {"value": False}))
        self.assertFalse(exact_json_equal({"value": False}, {"value": 0}))
        self.assertFalse(exact_json_equal({"value": 1}, {"value": 1.0}))
        self.assertFalse(exact_json_equal([True], [1]))

    @staticmethod
    def _package_root(parent: Path) -> Path:
        root = parent / "backend-package"
        for index, relative in enumerate(CRITICAL_FILES, start=1):
            path = root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(f"packaged-critical-file-{index}\n".encode())
        return root

    @staticmethod
    def _frontend_build(source_git_sha: str = SOURCE_GIT_SHA):
        css = {
            "path": "assets/app.css",
            "bytes": 80,
            "sha256": "2" * 64,
        }
        javascript = {
            "path": "assets/app.js",
            "bytes": 120,
            "sha256": "3" * 64,
        }
        index = {
            "path": "index.html",
            "bytes": 200,
            "sha256": "d" * 64,
        }
        service_worker = {
            "path": "service-worker.js",
            "bytes": RETIREMENT_SERVICE_WORKER_BYTES,
            "sha256": RETIREMENT_SERVICE_WORKER_SHA256,
        }
        short_service_worker = {
            "path": "sw.js",
            "bytes": RETIREMENT_SERVICE_WORKER_BYTES,
            "sha256": RETIREMENT_SERVICE_WORKER_SHA256,
        }
        return {
            "schema_version": 1,
            "git_sha": source_git_sha,
            "source": {
                "scope": "git_head_frontend_tree_v1",
                "git_tree_oid": "b" * 40,
                "file_count": 4,
                "tree_sha256": "c" * 64,
            },
            "toolchain": {
                "node": "22.23.2",
                "yarn": "1.22.22",
                "vite": "7.1.7",
            },
            "environment": {
                "mode": "production",
                "effective": {
                    "NODE_ENV": "production",
                    "VITE_USER_NODE_ENV_present": False,
                    "VITE_prefixed_keys": [],
                },
                "allowed_client_keys": ["REACT_APP_BACKEND_URL"],
                "values": {
                    "REACT_APP_BACKEND_URL": {
                        "present": False,
                        "sha256": None,
                    }
                },
            },
            "index": index,
            "entrypoints": [css, javascript],
            "assets": [css, javascript],
            "public_files": [
                css,
                javascript,
                index,
                service_worker,
                short_service_worker,
            ],
            "artifact_tree_sha256": "f" * 64,
            "build_meta": {
                "path": "build-meta.json",
                "bytes": 300,
                "sha256": "1" * 64,
            },
        }

    @staticmethod
    def _frontend_reproducibility(frontend_build):
        build_pass = {
            "build_meta": frontend_build["build_meta"],
            "artifact_tree_sha256": frontend_build[
                "artifact_tree_sha256"
            ],
        }
        return {
            "schema_version": 1,
            "kind": "frontend_two_clean_builds_v1",
            "git_sha": frontend_build["git_sha"],
            "source": frontend_build["source"],
            "toolchain": frontend_build["toolchain"],
            "environment": frontend_build["environment"],
            "passes": [
                {"ordinal": 1, **build_pass},
                {"ordinal": 2, **build_pass},
            ],
            "retained_pass": 2,
            "proof_file": {
                "path": "frontend/.release/reproducible-build.json",
                "bytes": 100,
                "sha256": "e" * 64,
            },
        }

    @staticmethod
    def _release_control_source():
        control_content = b"governed release control\n"
        record = {
            "path": "scripts/release.py",
            "mode": "100644",
            "git_blob": hashlib.sha1(
                f"blob {len(control_content)}\0".encode() + control_content,
                usedforsecurity=False,
            ).hexdigest(),
            "bytes": len(control_content),
            "sha256": hashlib.sha256(control_content).hexdigest(),
        }
        return build_source_manifest(
            scope=RELEASE_CONTROL_SOURCE_SCOPE,
            source_git_sha=SOURCE_GIT_SHA,
            source_base_git_sha=SOURCE_GIT_SHA,
            source_root_tree_oid="f" * 40,
            source_base_root_tree_oid="f" * 40,
            scope_tree_oid="e" * 40,
            source_base_scope_tree_oid="e" * 40,
            files=[record],
            base_files=[record],
            label="Release control source",
        )

    @staticmethod
    def _backend_runtime_source(backend_root: Path):
        records = []
        for path in sorted(backend_root.rglob("*")):
            if not path.is_file() or path.is_symlink():
                continue
            relative = path.relative_to(backend_root).as_posix()
            if (
                relative in {".env", "release_identity.json"}
                or relative.startswith("tests/")
            ):
                continue
            content = path.read_bytes()
            records.append({
                "path": relative,
                "mode": "100755" if path.stat().st_mode & 0o111 else "100644",
                "git_blob": hashlib.sha1(
                    f"blob {len(content)}\0".encode() + content,
                    usedforsecurity=False,
                ).hexdigest(),
                "bytes": len(content),
                "sha256": hashlib.sha256(content).hexdigest(),
            })
        return build_source_manifest(
            scope=BACKEND_RUNTIME_SOURCE_SCOPE,
            source_git_sha=SOURCE_GIT_SHA,
            source_base_git_sha=SOURCE_GIT_SHA,
            source_root_tree_oid="f" * 40,
            source_base_root_tree_oid="f" * 40,
            scope_tree_oid="d" * 40,
            source_base_scope_tree_oid="d" * 40,
            files=records,
            base_files=records,
            label="Backend runtime source",
        )

    def _identity(self, backend_root: Path):
        frontend_build = self._frontend_build()
        return build_runtime_release_identity(
            source_git_sha=SOURCE_GIT_SHA,
            source_base_git_sha=SOURCE_GIT_SHA,
            branch=BRANCH,
            frontend_build=frontend_build,
            frontend_reproducibility=self._frontend_reproducibility(
                frontend_build
            ),
            backend_root=backend_root,
            backend_runtime_source=self._backend_runtime_source(backend_root),
            release_control_source=self._release_control_source(),
        )

    def test_valid_identity_is_public_exact_and_deterministic(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._package_root(Path(tmp))
            first = self._identity(root)
            second = self._identity(root)
            identity_path = Path(tmp) / "release_identity.json"
            identity_path.write_text(
                json.dumps(first, sort_keys=True), encoding="utf-8"
            )
            result = read_release_identity(
                identity_path, backend_root=root
            )

        self.assertEqual(RELEASE_PROTOCOL_VERSION, 5)
        self.assertEqual(first, second)
        self.assertTrue(result["verified_identity_available"])
        self.assertEqual(result["release_id"], first["release_id"])
        self.assertRegex(result["release_id"], r"^rg5-[0-9a-f]{64}$")
        self.assertEqual(result["git_sha"], SOURCE_GIT_SHA)
        self.assertEqual(result["source_git_sha"], SOURCE_GIT_SHA)
        self.assertEqual(result["source_base_git_sha"], SOURCE_GIT_SHA)
        self.assertEqual(result["protocol_version"], 5)
        self.assertEqual(result["identity_kind"], RELEASE_IDENTITY_KIND)
        self.assertEqual(
            result["identity_schema_version"],
            RELEASE_IDENTITY_SCHEMA_VERSION,
        )
        self.assertTrue(result["critical_file_hashes_match"])
        self.assertTrue(result["backend_runtime_source_verified"])
        self.assertNotIn("files", result["backend_runtime_source"])
        self.assertNotIn("tombstones", result["backend_runtime_source"])
        self.assertNotIn("files", result["release_control_source"])
        self.assertTrue(result["release_control_source_bound"])
        self.assertTrue(result["frontend_build_verified"])
        self.assertEqual(result["frontend_build"], first["frontend_build"])
        self.assertEqual(
            result["frontend_reproducibility"],
            first["frontend_reproducibility"],
        )
        self.assertNotIn("actor", first)
        self.assertNotIn("prepared_at", first)
        self.assertEqual(
            set(first["critical_file_hashes"]),
            {
                "server.py",
                "release_identity.py",
                "release_protocol_v5.py",
                "frontend_build_identity.py",
                "integrations/qoyod_manual/routes.py",
                "integrations/qoyod_manual/send.py",
            },
        )

    def test_health_payload_is_read_only_exact_boot_identity(self):
        boot = {
            "verified_identity_available": True,
            "release_id": "rg5-" + "a" * 64,
        }
        with patch.object(
            release_identity_module,
            "BOOT_RELEASE_IDENTITY",
            boot,
        ):
            self.assertEqual(
                release_health_payload(),
                {"ok": True, "service": "backend", "release": boot},
            )

    def test_release_id_is_sha256_of_canonical_compact_sorted_core(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._package_root(Path(tmp))
            payload = self._identity(root)

        core = canonical_identity_core(payload)
        expected_digest = hashlib.sha256(json.dumps(
            core,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")).hexdigest()
        self.assertEqual(payload["release_id"], f"rg5-{expected_digest}")
        self.assertEqual(
            deterministic_release_id(dict(reversed(list(core.items())))),
            payload["release_id"],
        )

    def test_runtime_validation_needs_no_git_or_sibling_frontend(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._package_root(Path(tmp))
            payload = self._identity(root)
            self.assertFalse((root.parent / ".git").exists())
            self.assertFalse((root.parent / "frontend").exists())

            validated = validate_runtime_release_identity(
                payload, backend_root=root
            )

        self.assertEqual(validated, payload)

    def test_validation_is_idempotent_and_rejects_json_type_coercion(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._package_root(Path(tmp))
            payload = self._identity(root)
            validated = validate_runtime_release_identity(
                payload, backend_root=root
            )
            self.assertEqual(
                validate_runtime_release_identity(validated, backend_root=root),
                validated,
            )
            self.assertEqual(
                deterministic_release_id(canonical_identity_core(validated)),
                validated["release_id"],
            )

            mutations = (
                ("schema_version", True),
                ("schema_version", 1.0),
                ("schema_version", "1"),
                ("protocol_version", True),
                ("protocol_version", 5.0),
                ("protocol_version", "5"),
                ("source_git_sha", int("1" * 40)),
            )
            for field, invalid in mutations:
                changed = {**payload, field: invalid}
                changed["release_id"] = deterministic_release_id(changed)
                with self.subTest(field=field, invalid=invalid), self.assertRaises(
                    ReleaseProtocolV5Error
                ):
                    validate_runtime_release_identity(changed, backend_root=root)

            for invalid in (True, 1.0, "1"):
                changed = {
                    **payload,
                    "frontend_build": {
                        **payload["frontend_build"],
                        "schema_version": invalid,
                    },
                }
                changed["release_id"] = deterministic_release_id(changed)
                with self.subTest(frontend_schema=invalid), self.assertRaisesRegex(
                    ReleaseProtocolV5Error,
                    "frontend build schema",
                ):
                    validate_runtime_release_identity(changed, backend_root=root)

            changed_environment = {
                **payload["frontend_build"]["environment"],
                "effective": {
                    **payload["frontend_build"]["environment"]["effective"],
                    "VITE_USER_NODE_ENV_present": 0,
                },
            }
            changed_build = {
                **payload["frontend_build"],
                "environment": changed_environment,
            }
            changed_proof = {
                **payload["frontend_reproducibility"],
                "environment": changed_environment,
            }
            changed = {
                **payload,
                "frontend_build": changed_build,
                "frontend_reproducibility": changed_proof,
            }
            changed["release_id"] = deterministic_release_id(changed)
            with self.assertRaisesRegex(
                ReleaseProtocolV5Error,
                "governed environment",
            ):
                validate_runtime_release_identity(changed, backend_root=root)

            proof_mutations = (
                ("schema_version", True),
                ("retained_pass", 2.0),
            )
            for field, invalid in proof_mutations:
                changed = {
                    **payload,
                    "frontend_reproducibility": {
                        **payload["frontend_reproducibility"],
                        field: invalid,
                    },
                }
                changed["release_id"] = deterministic_release_id(changed)
                with self.subTest(proof_field=field), self.assertRaisesRegex(
                    ReleaseProtocolV5Error,
                    "reproducibility proof",
                ):
                    validate_runtime_release_identity(changed, backend_root=root)

            changed_passes = [
                dict(build_pass)
                for build_pass in payload["frontend_reproducibility"]["passes"]
            ]
            changed_passes[0]["ordinal"] = True
            changed = {
                **payload,
                "frontend_reproducibility": {
                    **payload["frontend_reproducibility"],
                    "passes": changed_passes,
                },
            }
            changed["release_id"] = deterministic_release_id(changed)
            with self.assertRaisesRegex(
                ReleaseProtocolV5Error,
                "reproducibility proof",
            ):
                validate_runtime_release_identity(changed, backend_root=root)

    def test_changed_packaged_critical_byte_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._package_root(Path(tmp))
            payload = self._identity(root)
            identity_path = Path(tmp) / "release_identity.json"
            identity_path.write_text(json.dumps(payload), encoding="utf-8")
            (root / CRITICAL_FILES[0]).write_bytes(b"tampered\n")

            result = read_release_identity(
                identity_path, backend_root=root
            )

        self.assertFalse(result["verified_identity_available"])
        self.assertFalse(result["critical_file_hashes_match"])
        self.assertFalse(result["frontend_build_verified"])
        self.assertIsNone(result["release_id"])
        self.assertIsNone(result["git_sha"])

    def test_complete_backend_runtime_source_is_bound_to_identity(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._package_root(Path(tmp))
            governed = root / "business_logic.py"
            governed.write_bytes(b"VALUE = 'reviewed'\n")
            governed.chmod(0o755)

            payload = self._identity(root)

        source = payload["backend_runtime_source"]
        self.assertEqual(source["scope"], "backend_runtime_package_v1")
        self.assertEqual(source["file_count"], len(source["files"]))
        records = {record["path"]: record for record in source["files"]}
        self.assertEqual(
            set(records["business_logic.py"]),
            {"path", "mode", "git_blob", "bytes", "sha256"},
        )
        self.assertEqual(records["business_logic.py"]["mode"], "100755")
        self.assertEqual(records["business_logic.py"]["bytes"], 19)
        self.assertRegex(records["business_logic.py"]["git_blob"], r"^[0-9a-f]{40}$")
        self.assertRegex(records["business_logic.py"]["sha256"], r"^[0-9a-f]{64}$")

    def test_unreviewed_backend_addition_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._package_root(Path(tmp))
            (root / "business_logic.py").write_bytes(b"reviewed\n")
            payload = self._identity(root)
            (root / "unreviewed.py").write_bytes(b"not reviewed\n")

            with self.assertRaisesRegex(
                ReleaseProtocolV5Error,
                "Backend runtime source membership",
            ):
                validate_runtime_release_identity(payload, backend_root=root)

    def test_backend_modification_deletion_and_mode_drift_fail_closed(self):
        for mutation in ("content", "delete", "mode"):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as tmp:
                root = self._package_root(Path(tmp))
                governed = root / "business_logic.py"
                governed.write_bytes(b"reviewed\n")
                governed.chmod(0o644)
                payload = self._identity(root)

                if mutation == "content":
                    governed.write_bytes(b"changed\n")
                elif mutation == "delete":
                    governed.unlink()
                else:
                    governed.chmod(0o755)

                with self.assertRaisesRegex(
                    ReleaseProtocolV5Error,
                    "Backend runtime source",
                ):
                    validate_runtime_release_identity(payload, backend_root=root)

    def test_tests_and_identity_are_excluded_but_bytecode_cache_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._package_root(Path(tmp))
            (root / "business_logic.py").write_bytes(b"reviewed\n")
            (root / "tests").mkdir()
            (root / "tests" / "test_fixture.py").write_bytes(b"ignored\n")
            payload = self._identity(root)
            (root / "__pycache__").mkdir()
            (root / "__pycache__" / "business_logic.cpython-311.pyc").write_bytes(
                b"generated\n"
            )
            identity_path = root / "release_identity.json"
            identity_path.write_text(json.dumps(payload), encoding="utf-8")

            result = read_release_identity(identity_path, backend_root=root)

        self.assertFalse(result["verified_identity_available"])
        self.assertFalse(result["backend_runtime_source_verified"])
        source_paths = {
            record["path"] for record in payload["backend_runtime_source"]["files"]
        }
        self.assertNotIn("release_identity.json", source_paths)
        self.assertFalse(any(path.startswith("tests/") for path in source_paths))
        self.assertFalse(any("__pycache__" in path for path in source_paths))

    def test_safe_root_env_sidecar_is_unread_and_outside_identity(self):
        for mode in (0o600, 0o640, 0o644):
            with self.subTest(mode=oct(mode)), tempfile.TemporaryDirectory() as tmp:
                root = self._package_root(Path(tmp))
                sidecar = root / ".env"
                secret = "SIDECAR_SENTINEL_MUST_NOT_BE_READ"
                sidecar.write_text(f"TOKEN={secret}\n", encoding="utf-8")
                sidecar.chmod(mode)
                real_open = os.open
                opened: list[Path] = []

                def guarded_open(path, *args, **kwargs):
                    candidate = Path(path)
                    opened.append(candidate)
                    if candidate == sidecar:
                        raise AssertionError("configuration sidecar was opened")
                    return real_open(path, *args, **kwargs)

                with patch.object(
                    release_protocol_module.os,
                    "open",
                    side_effect=guarded_open,
                ):
                    payload = self._identity(root)
                    identity_path = root / "release_identity.json"
                    identity_path.write_text(json.dumps(payload), encoding="utf-8")
                    result = read_release_identity(
                        identity_path, backend_root=root
                    )

                self.assertTrue(result["verified_identity_available"])
                self.assertNotIn(sidecar, opened)
                serialized = json.dumps(payload)
                self.assertNotIn(secret, serialized)
                self.assertNotIn('"path": ".env"', serialized)

    def test_root_env_sidecar_shape_and_permissions_fail_closed(self):
        mutations = (
            "symlink",
            "fifo",
            "group-write",
            "executable",
            "special",
            "not-owner-readable",
            "suffix",
            "nested",
        )
        for mutation in mutations:
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as tmp:
                temporary = Path(tmp)
                root = self._package_root(temporary)
                payload = self._identity(root)
                sidecar = root / ".env"
                if mutation == "symlink":
                    target = temporary / "outside-env"
                    target.write_text("SECRET=outside\n", encoding="utf-8")
                    sidecar.symlink_to(target)
                elif mutation == "fifo":
                    os.mkfifo(sidecar)
                elif mutation == "suffix":
                    (root / ".env.local").write_text(
                        "SECRET=suffix\n", encoding="utf-8"
                    )
                elif mutation == "nested":
                    nested = root / "config"
                    nested.mkdir()
                    (nested / ".env").write_text(
                        "SECRET=nested\n", encoding="utf-8"
                    )
                else:
                    sidecar.write_text("SECRET=value\n", encoding="utf-8")
                    sidecar.chmod({
                        "group-write": 0o620,
                        "executable": 0o700,
                        "special": 0o4600,
                        "not-owner-readable": 0o200,
                    }[mutation])

                with self.assertRaises(ReleaseProtocolV5Error):
                    validate_runtime_release_identity(
                        payload, backend_root=root
                    )

    def test_restrictive_source_and_directory_modes_preserve_git_category(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._package_root(Path(tmp))
            for path in root.rglob("*"):
                if path.is_dir():
                    path.chmod(0o700)
                else:
                    path.chmod(0o600)
            root.chmod(0o700)
            executable = root / "worker.py"
            executable.write_text("print('worker')\n", encoding="utf-8")
            executable.chmod(0o700)
            payload = self._identity(root)

            validated = validate_runtime_release_identity(
                payload, backend_root=root
            )
            records = {
                row["path"]: row
                for row in validated["backend_runtime_source"]["files"]
            }
            self.assertEqual(records["server.py"]["mode"], "100644")
            self.assertEqual(records["worker.py"]["mode"], "100755")
            executable.chmod(0o600)
            with self.assertRaisesRegex(
                ReleaseProtocolV5Error, "mismatch for worker.py: mode"
            ):
                validate_runtime_release_identity(payload, backend_root=root)

    def test_plain_python_boot_accepts_derived_cache_under_umask_077(self):
        with tempfile.TemporaryDirectory() as tmp:
            temporary = Path(tmp)
            root = temporary / "backend-package"
            source_root = Path(release_identity_module.__file__).resolve().parent
            root.mkdir()
            for relative in (
                "release_identity.py",
                "release_protocol_v5.py",
                "frontend_build_identity.py",
            ):
                shutil.copy2(source_root / relative, root / relative)
            for index, relative in enumerate(
                set(CRITICAL_FILES) - {
                    "release_identity.py",
                    "release_protocol_v5.py",
                    "frontend_build_identity.py",
                },
                start=1,
            ):
                path = root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(f"fixture-{index}\n", encoding="utf-8")
            payload = self._identity(root)
            (root / "release_identity.json").write_text(
                json.dumps(payload), encoding="utf-8"
            )
            environment = dict(os.environ)
            environment.pop("PYTHONDONTWRITEBYTECODE", None)
            result = subprocess.run(
                [
                    sys.executable,
                    "-I",
                    "-c",
                    (
                        "import json,os,sys;"
                        "os.umask(0o077);"
                        f"sys.path.insert(0,{str(root)!r});"
                        "import release_identity;"
                        "print(json.dumps(release_identity.BOOT_RELEASE_IDENTITY))"
                    ),
                ],
                cwd=temporary,
                env=environment,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            boot = json.loads(result.stdout)
            self.assertTrue(boot["verified_identity_available"])
            self.assertTrue(boot["backend_runtime_source_verified"])
            caches = list((root / "__pycache__").glob("*.pyc"))
            self.assertTrue(caches)
            self.assertEqual(
                {stat.S_IMODE(path.stat().st_mode) for path in caches},
                {0o600},
            )
            with self.assertRaisesRegex(
                ReleaseProtocolV5Error, "prepackaged bytecode"
            ):
                validate_runtime_release_identity(
                    payload,
                    backend_root=root,
                    allow_derived_bytecode=False,
                )
            cached = caches[0]
            for unsafe_mode in (0o700, 0o620, 0o4600, 0o200):
                with self.subTest(unsafe_cache_mode=oct(unsafe_mode)):
                    cached.chmod(unsafe_mode)
                    with self.assertRaisesRegex(
                        ReleaseProtocolV5Error,
                        "unsafe derived-cache permissions",
                    ):
                        validate_runtime_release_identity(
                            payload, backend_root=root
                        )
            cached.chmod(0o600)
            cached.write_bytes(b"arbitrary cache bytes\n")
            with self.assertRaisesRegex(
                ReleaseProtocolV5Error, "non-derived bytecode"
            ):
                validate_runtime_release_identity(payload, backend_root=root)

    def test_backend_mode_symlink_and_sourceless_bytecode_fail_closed(self):
        for mutation in (
            "critical-mode", "unsafe-mode", "symlink", "sourceless-bytecode",
        ):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as tmp:
                temporary = Path(tmp)
                root = self._package_root(temporary)
                governed = root / "business_logic.py"
                governed.write_bytes(b"reviewed\n")
                payload = self._identity(root)

                if mutation == "critical-mode":
                    (root / CRITICAL_FILES[0]).chmod(0o755)
                elif mutation == "unsafe-mode":
                    governed.chmod(0o666)
                elif mutation == "symlink":
                    target = temporary / "outside.py"
                    target.write_bytes(governed.read_bytes())
                    governed.unlink()
                    governed.symlink_to(target)
                else:
                    (root / "evil.pyc").write_bytes(b"sourceless code\n")

                with self.assertRaises(ReleaseProtocolV5Error):
                    validate_runtime_release_identity(payload, backend_root=root)

    def test_critical_hash_keys_must_be_exact(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._package_root(Path(tmp))
            payload = self._identity(root)
            payload["critical_file_hashes"]["unexpected.py"] = "0" * 64
            payload["release_id"] = deterministic_release_id(payload)

            with self.assertRaisesRegex(
                ReleaseProtocolV5Error, "exact critical set"
            ):
                validate_runtime_release_identity(
                    payload, backend_root=root
                )

    def test_frontend_proof_mismatch_fails_closed_even_with_recomputed_id(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._package_root(Path(tmp))
            payload = self._identity(root)
            payload["frontend_reproducibility"]["retained_pass"] = 1
            payload["release_id"] = deterministic_release_id(payload)
            identity_path = Path(tmp) / "release_identity.json"
            identity_path.write_text(json.dumps(payload), encoding="utf-8")

            result = read_release_identity(
                identity_path, backend_root=root
            )

        self.assertFalse(result["verified_identity_available"])
        self.assertFalse(result["frontend_build_verified"])
        self.assertIsNone(result["frontend_build"])
        self.assertIsNone(result["frontend_reproducibility"])

    def test_incomplete_frontend_summary_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._package_root(Path(tmp))
            frontend_build = {
                "schema_version": 1,
                "git_sha": SOURCE_GIT_SHA,
                "artifact_tree_sha256": "f" * 64,
            }
            frontend_proof = {
                "schema_version": 1,
                "kind": "frontend_two_clean_builds_v1",
                "git_sha": SOURCE_GIT_SHA,
                "source": None,
                "toolchain": None,
                "environment": None,
                "passes": [
                    {
                        "ordinal": 1,
                        "build_meta": None,
                        "artifact_tree_sha256": "f" * 64,
                    },
                    {
                        "ordinal": 2,
                        "build_meta": None,
                        "artifact_tree_sha256": "f" * 64,
                    },
                ],
                "retained_pass": 2,
                "proof_file": {
                    "path": "frontend/.release/reproducible-build.json",
                    "bytes": 100,
                    "sha256": "e" * 64,
                },
            }

            with self.assertRaisesRegex(
                ReleaseProtocolV5Error,
                "frontend build fields",
            ):
                build_runtime_release_identity(
                    source_git_sha=SOURCE_GIT_SHA,
                    source_base_git_sha=SOURCE_GIT_SHA,
                    branch=BRANCH,
                    frontend_build=frontend_build,
                    frontend_reproducibility=frontend_proof,
                    backend_root=root,
                    backend_runtime_source=self._backend_runtime_source(root),
                    release_control_source=self._release_control_source(),
                )

    def test_release_id_cannot_be_reused_after_core_tampering(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._package_root(Path(tmp))
            payload = self._identity(root)
            payload["branch"] = "different-branch"

            with self.assertRaisesRegex(
                ReleaseProtocolV5Error, "deterministic identity core"
            ):
                validate_runtime_release_identity(
                    payload, backend_root=root
                )

    def test_source_sha_must_be_full_lowercase_and_match_frontend(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._package_root(Path(tmp))
            frontend_build = self._frontend_build("B" * 40)
            with self.assertRaises(ReleaseProtocolV5Error):
                build_runtime_release_identity(
                    source_git_sha="B" * 40,
                    source_base_git_sha=SOURCE_GIT_SHA,
                    branch=BRANCH,
                    frontend_build=frontend_build,
                    frontend_reproducibility=(
                        self._frontend_reproducibility(frontend_build)
                    ),
                    backend_root=root,
                    backend_runtime_source=self._backend_runtime_source(root),
                    release_control_source=self._release_control_source(),
                )

            mismatched_build = self._frontend_build("b" * 40)
            with self.assertRaisesRegex(
                ReleaseProtocolV5Error, "not bound to source_git_sha"
            ):
                build_runtime_release_identity(
                    source_git_sha=SOURCE_GIT_SHA,
                    source_base_git_sha=SOURCE_GIT_SHA,
                    branch=BRANCH,
                    frontend_build=mismatched_build,
                    frontend_reproducibility=(
                        self._frontend_reproducibility(mismatched_build)
                    ),
                    backend_root=root,
                    backend_runtime_source=self._backend_runtime_source(root),
                    release_control_source=self._release_control_source(),
                )

    def test_random_lease_fields_and_v4_payload_are_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._package_root(Path(tmp))
            payload = self._identity(root)
            payload["actor"] = "must-not-enter-runtime-identity"
            payload["prepared_at"] = "2026-08-30T00:00:00Z"
            with self.assertRaisesRegex(
                ReleaseProtocolV5Error, "fields do not match"
            ):
                validate_runtime_release_identity(
                    payload, backend_root=root
                )

            legacy_path = Path(tmp) / "legacy-v4.json"
            legacy_path.write_text(json.dumps({
                "release_id": "9d7a9a23-1f46-44a8-a0d0-851a71e15af6",
                "git_sha": SOURCE_GIT_SHA,
                "branch": BRANCH,
                "prepared_at": "2026-08-12T00:00:00+00:00",
                "protocol_version": 4,
                "critical_file_hashes": {},
            }), encoding="utf-8")
            result = read_release_identity(
                legacy_path, backend_root=root
            )

        self.assertFalse(result["verified_identity_available"])
        self.assertEqual(result["protocol_version"], 5)
        self.assertIsNone(result["release_id"])
        self.assertIsNone(result["source_git_sha"])

    def test_missing_or_non_object_identity_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._package_root(Path(tmp))
            path = Path(tmp) / "missing.json"
            missing = read_release_identity(path, backend_root=root)
            path.write_text("[]", encoding="utf-8")
            non_object = read_release_identity(path, backend_root=root)

        self.assertFalse(missing["verified_identity_available"])
        self.assertFalse(non_object["verified_identity_available"])
        self.assertIsNone(non_object["git_sha"])

    def test_symlink_identity_and_symlink_critical_file_fail_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            temporary = Path(tmp)
            root = self._package_root(temporary)
            payload = self._identity(root)
            regular_identity = temporary / "regular.json"
            regular_identity.write_text(json.dumps(payload), encoding="utf-8")
            identity_link = temporary / "identity-link.json"
            identity_link.symlink_to(regular_identity)

            linked_identity_result = read_release_identity(
                identity_link, backend_root=root
            )
            critical_path = root / CRITICAL_FILES[0]
            critical_copy = temporary / "critical-copy.py"
            critical_copy.write_bytes(critical_path.read_bytes())
            critical_path.unlink()
            critical_path.symlink_to(critical_copy)
            linked_critical_result = read_release_identity(
                regular_identity, backend_root=root
            )

        self.assertFalse(
            linked_identity_result["verified_identity_available"]
        )
        self.assertFalse(
            linked_critical_result["verified_identity_available"]
        )


if __name__ == "__main__":
    unittest.main()
