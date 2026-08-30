from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import release_identity as release_identity_module

from release_identity import (
    CRITICAL_FILES,
    RELEASE_PROTOCOL_VERSION,
    read_release_identity,
    release_health_payload,
)
from release_protocol_v5 import (
    RELEASE_IDENTITY_KIND,
    RELEASE_IDENTITY_SCHEMA_VERSION,
    ReleaseProtocolV5Error,
    build_runtime_release_identity,
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

    def _identity(self, backend_root: Path):
        frontend_build = self._frontend_build()
        return build_runtime_release_identity(
            source_git_sha=SOURCE_GIT_SHA,
            branch=BRANCH,
            frontend_build=frontend_build,
            frontend_reproducibility=self._frontend_reproducibility(
                frontend_build
            ),
            backend_root=backend_root,
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
        self.assertEqual(result["protocol_version"], 5)
        self.assertEqual(result["identity_kind"], RELEASE_IDENTITY_KIND)
        self.assertEqual(
            result["identity_schema_version"],
            RELEASE_IDENTITY_SCHEMA_VERSION,
        )
        self.assertTrue(result["critical_file_hashes_match"])
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
                    branch=BRANCH,
                    frontend_build=frontend_build,
                    frontend_reproducibility=frontend_proof,
                    backend_root=root,
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
                    branch=BRANCH,
                    frontend_build=frontend_build,
                    frontend_reproducibility=(
                        self._frontend_reproducibility(frontend_build)
                    ),
                    backend_root=root,
                )

            mismatched_build = self._frontend_build("b" * 40)
            with self.assertRaisesRegex(
                ReleaseProtocolV5Error, "not bound to source_git_sha"
            ):
                build_runtime_release_identity(
                    source_git_sha=SOURCE_GIT_SHA,
                    branch=BRANCH,
                    frontend_build=mismatched_build,
                    frontend_reproducibility=(
                        self._frontend_reproducibility(mismatched_build)
                    ),
                    backend_root=root,
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
