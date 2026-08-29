from __future__ import annotations

import json
import hashlib
import tempfile
import threading
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from scripts import production_release_guard as guard


class _Headers(dict):
    def get(self, key, default=None):
        return super().get(key, default)


class _Response:
    def __init__(self, payload):
        self._payload = payload
        self.headers = _Headers({"Content-Type": "application/json"})

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return json.dumps(self._payload).encode("utf-8")


class ProductionReleaseGuardTests(unittest.TestCase):
    @staticmethod
    def _frontend_build():
        return {
            "schema_version": 1,
            "git_sha": "a" * 40,
            "source": {
                "package.json": "1" * 64,
                "yarn.lock": "2" * 64,
            },
            "toolchain": {
                "node": "22.19.0",
                "yarn": "1.22.22",
                "vite": "8.2.1",
            },
            "index": {
                "path": "index.html",
                "bytes": 5,
                "sha256": __import__("hashlib").sha256(b"index").hexdigest(),
            },
            "entrypoints": [],
            "assets": [{
                "path": "assets/app.js",
                "bytes": 3,
                "sha256": __import__("hashlib").sha256(b"app").hexdigest(),
            }],
            "artifact_tree_sha256": "3" * 64,
            "build_meta_sha256": __import__("hashlib").sha256(b"meta").hexdigest(),
        }

    @staticmethod
    def _lease_payload():
        return {
            "release_id": "9d7a9a23-1f46-44a8-a0d0-851a71e15af6",
            "git_sha": "a" * 40,
            "branch": guard.PRODUCTION_BRANCH,
            "actor": "test",
            "prepared_at": "2026-08-13T10:00:00+00:00",
            "protocol_version": guard.PROTOCOL_VERSION,
            "critical_file_hashes": {
                "server.py": "b" * 64,
                "integrations/qoyod_manual/routes.py": "c" * 64,
                "integrations/qoyod_manual/send.py": "d" * 64,
            },
            "frontend_build": ProductionReleaseGuardTests._frontend_build(),
        }

    @staticmethod
    def _health_payload_for(lease, boot_started_at, **release_updates):
        release = {
            "verified_identity_available": True,
            "release_id": lease["release_id"],
            "git_sha": lease["git_sha"],
            "branch": lease["branch"],
            "prepared_at": lease["prepared_at"],
            "protocol_version": lease["protocol_version"],
            "critical_file_hashes_match": True,
            "critical_file_hashes": lease["critical_file_hashes"],
            "frontend_build_verified": True,
            "frontend_build": lease["frontend_build"],
            "boot_started_at": boot_started_at,
        }
        release.update(release_updates)
        return {"ok": True, "release": release}

    def _verify_with(self, lease, health_payloads):
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        lease_dir = Path(temp_dir.name) / "release.lock"
        lease_dir.mkdir()
        lease_path = lease_dir / "lease.json"
        lease_path.write_text(json.dumps(lease), encoding="utf-8")
        patches = (
            patch.object(guard, "LEASE_DIR", lease_dir),
            patch.object(guard, "LEASE_PATH", lease_path),
            patch.object(guard, "_health_payload", side_effect=health_payloads),
            patch.object(guard.time, "sleep"),
            patch.object(
                guard,
                "_verify_public_frontend",
                return_value={
                    "artifact_tree_sha256": lease["frontend_build"][
                        "artifact_tree_sha256"
                    ],
                    "checked_paths": [
                        "/build-meta.json",
                        "/index.html",
                        "/assets/app.js",
                    ],
                },
            ),
            patch.object(
                guard,
                "_utc_datetime",
                return_value=datetime(2026, 8, 13, 10, 5, tzinfo=timezone.utc),
            ),
        )
        for active_patch in patches:
            active_patch.start()
            self.addCleanup(active_patch.stop)
        return lease_dir, guard.verify("https://mezansalla.com/")

    def test_health_payload_uses_public_api_route_and_cloudflare_safe_user_agent(self):
        response = _Response({"ok": True, "release": {"git_sha": "a" * 40}})
        with patch.object(
            guard.urllib.request,
            "urlopen",
            return_value=response,
        ) as opened:
            self.assertTrue(
                guard._health_payload("https://mezansalla.com/")["ok"]
            )

        request = opened.call_args.args[0]
        self.assertTrue(
            request.full_url.startswith("https://mezansalla.com/api/health?")
        )
        self.assertTrue(
            request.get_header("User-agent", "").startswith("Mozilla/5.0")
        )
        self.assertEqual(request.get_header("Accept"), "application/json")

    def test_release_protocol_is_v3(self):
        self.assertEqual(guard.PROTOCOL_VERSION, 3)

    def test_operation_mutex_blocks_concurrent_prepare(self):
        with tempfile.TemporaryDirectory() as tmp:
            git_dir = Path(tmp) / ".git"
            git_dir.mkdir()
            lease_dir = git_dir / "release.lock"
            holder_entered = threading.Event()
            release_holder = threading.Event()
            prepare_started = threading.Event()
            prepare_finished = threading.Event()
            errors = []

            def hold_operation_lock():
                try:
                    with guard._release_operation_lock():
                        holder_entered.set()
                        release_holder.wait(timeout=2)
                except BaseException as exc:  # pragma: no cover - assertion below
                    errors.append(exc)

            def run_prepare():
                prepare_started.set()
                try:
                    guard.prepare("second-owner")
                except BaseException as exc:  # pragma: no cover - assertion below
                    errors.append(exc)
                finally:
                    prepare_finished.set()

            with patch.object(guard, "GIT_DIR", git_dir), patch.object(
                guard, "LEASE_DIR", lease_dir
            ), patch.object(
                guard,
                "_prepare_locked",
                return_value={"prepared": True},
            ) as prepare_locked:
                holder = threading.Thread(target=hold_operation_lock)
                contender = threading.Thread(target=run_prepare)
                holder.start()
                self.assertTrue(holder_entered.wait(timeout=1))
                contender.start()
                self.assertTrue(prepare_started.wait(timeout=1))
                self.assertFalse(prepare_finished.wait(timeout=0.05))
                release_holder.set()
                holder.join(timeout=1)
                contender.join(timeout=1)

            self.assertEqual(errors, [])
            self.assertTrue(prepare_finished.is_set())
            prepare_locked.assert_called_once_with("second-owner")

    def test_verify_accepts_multiple_boots_for_same_prepared_identity(self):
        lease = self._lease_payload()
        boot_times = [
            "2026-08-13T10:01:00+00:00",
            "2026-08-13T10:01:03+00:00",
            "2026-08-13T10:01:00+00:00",
        ]
        lease_dir, result = self._verify_with(
            lease,
            [self._health_payload_for(lease, boot) for boot in boot_times],
        )

        self.assertTrue(result["verified"])
        self.assertEqual(result["git_sha"], lease["git_sha"])
        self.assertEqual(result["checks"], 3)
        self.assertEqual(result["boot_started_at"], boot_times[0])
        self.assertEqual(
            result["boot_started_at_observations"],
            boot_times[:2],
        )
        self.assertFalse(lease_dir.exists())

    def test_verify_rejects_identity_drift_even_when_sha_and_hash_check_match(self):
        lease = self._lease_payload()
        mismatches = {
            "release_id": "b6ab746b-8998-46dd-9685-2a7deecfbc8a",
            "branch": "main",
            "prepared_at": "2026-08-13T09:00:00+00:00",
            "protocol_version": guard.PROTOCOL_VERSION + 1,
        }
        for field, value in mismatches.items():
            with self.subTest(field=field), self.assertRaisesRegex(
                guard.ReleaseGuardError,
                f"release identity does not match prepared lease: {field}",
            ):
                self._verify_with(
                    lease,
                    [self._health_payload_for(
                        lease,
                        "2026-08-13T10:01:00+00:00",
                        **{field: value},
                    )],
                )

    def test_verify_wrong_release_id_same_sha_preserves_active_lease(self):
        lease = self._lease_payload()
        health = self._health_payload_for(
            lease,
            "2026-08-13T10:01:00+00:00",
            release_id="b6ab746b-8998-46dd-9685-2a7deecfbc8a",
        )
        with tempfile.TemporaryDirectory() as tmp:
            lease_dir = Path(tmp) / "release.lock"
            lease_dir.mkdir()
            lease_path = lease_dir / "lease.json"
            lease_path.write_text(json.dumps(lease), encoding="utf-8")
            with patch.object(guard, "LEASE_DIR", lease_dir), patch.object(
                guard, "LEASE_PATH", lease_path
            ), patch.object(
                guard, "_health_payload", return_value=health
            ), self.assertRaisesRegex(
                guard.ReleaseGuardError,
                "release identity does not match prepared lease: release_id",
            ):
                guard.verify("https://mezansalla.com")

            self.assertTrue(lease_dir.exists())

    def test_verify_does_not_delete_replacement_lease_created_during_checks(self):
        lease = self._lease_payload()
        replacement = {
            **lease,
            "release_id": "b6ab746b-8998-46dd-9685-2a7deecfbc8a",
            "actor": "replacement-owner",
        }
        health = self._health_payload_for(
            lease, "2026-08-13T10:01:00+00:00"
        )
        with tempfile.TemporaryDirectory() as tmp:
            lease_dir = Path(tmp) / "release.lock"
            lease_dir.mkdir()
            lease_path = lease_dir / "lease.json"
            lease_path.write_text(json.dumps(lease), encoding="utf-8")
            calls = 0

            def replace_on_last_health(_base_url):
                nonlocal calls
                calls += 1
                if calls == 3:
                    lease_path.write_text(
                        json.dumps(replacement), encoding="utf-8"
                    )
                return health

            with patch.object(guard, "LEASE_DIR", lease_dir), patch.object(
                guard, "LEASE_PATH", lease_path
            ), patch.object(
                guard, "_health_payload", side_effect=replace_on_last_health
            ), patch.object(
                guard,
                "_verify_public_frontend",
                return_value={
                    "artifact_tree_sha256": lease["frontend_build"][
                        "artifact_tree_sha256"
                    ],
                    "checked_paths": ["/index.html", "/assets/app.js"],
                },
            ), patch.object(guard.time, "sleep"), patch.object(
                guard,
                "_utc_datetime",
                return_value=datetime(
                    2026, 8, 13, 10, 5, tzinfo=timezone.utc
                ),
            ), self.assertRaisesRegex(
                guard.ReleaseGuardError,
                "active production release lease changed during operation",
            ):
                guard.verify("https://mezansalla.com")

            self.assertTrue(lease_dir.exists())
            self.assertEqual(
                json.loads(lease_path.read_text(encoding="utf-8")),
                replacement,
            )

    def test_verify_rejects_hash_drift_from_prepared_lease(self):
        lease = self._lease_payload()
        health = self._health_payload_for(
            lease,
            "2026-08-13T10:01:00+00:00",
            critical_file_hashes={
                **lease["critical_file_hashes"],
                "server.py": "e" * 64,
            },
        )

        with self.assertRaisesRegex(
            guard.ReleaseGuardError,
            "release identity does not match prepared lease: critical_file_hashes",
        ):
            self._verify_with(lease, [health])

    def test_verify_rejects_backend_frontend_proof_failure(self):
        lease = self._lease_payload()
        health = self._health_payload_for(
            lease,
            "2026-08-13T10:01:00+00:00",
            frontend_build_verified=False,
        )

        with self.assertRaisesRegex(
            guard.ReleaseGuardError,
            "production frontend build proof does not match",
        ):
            self._verify_with(lease, [health])

    def test_public_frontend_verifies_index_and_every_asset(self):
        expected = self._frontend_build()
        expected["assets"].append({
            "path": "assets/lazy.js",
            "bytes": 4,
            "sha256": hashlib.sha256(b"lazy").hexdigest(),
        })
        payloads = {
            "/build-meta.json": b"meta",
            "/index.html": b"index",
            "/assets/app.js": b"app",
            "/assets/lazy.js": b"lazy",
        }
        with patch.object(
            guard,
            "_public_frontend_bytes",
            side_effect=lambda _base, relative: payloads[relative],
        ):
            result = guard._verify_public_frontend(
                "https://mezansalla.com", expected
            )

        self.assertEqual(
            result["checked_paths"],
            list(payloads),
        )

    def test_public_frontend_rejects_unsafe_asset_path(self):
        expected = self._frontend_build()
        expected["assets"] = [{
            "path": "assets/../secret",
            "bytes": 1,
            "sha256": "a" * 64,
        }]
        with self.assertRaisesRegex(
            guard.ReleaseGuardError,
            "asset path is invalid",
        ):
            guard._verify_public_frontend(
                "https://mezansalla.com", expected
            )

    def test_public_frontend_mismatch_preserves_active_lease(self):
        lease = self._lease_payload()
        health = self._health_payload_for(
            lease, "2026-08-13T10:01:00+00:00"
        )
        with tempfile.TemporaryDirectory() as tmp:
            lease_dir = Path(tmp) / "release.lock"
            lease_dir.mkdir()
            lease_path = lease_dir / "lease.json"
            lease_path.write_text(json.dumps(lease), encoding="utf-8")
            with patch.object(guard, "LEASE_DIR", lease_dir), patch.object(
                guard, "LEASE_PATH", lease_path
            ), patch.object(
                guard, "_health_payload", return_value=health
            ), patch.object(
                guard,
                "_verify_public_frontend",
                side_effect=guard.ReleaseGuardError(
                    "deployed frontend SHA mismatch"
                ),
            ), patch.object(guard.time, "sleep"), patch.object(
                guard,
                "_utc_datetime",
                return_value=datetime(
                    2026, 8, 13, 10, 5, tzinfo=timezone.utc
                ),
            ), self.assertRaisesRegex(
                guard.ReleaseGuardError,
                "deployed frontend SHA mismatch",
            ):
                guard.verify("https://mezansalla.com")

            self.assertTrue(lease_dir.exists())

    def test_release_lease_without_frontend_proof_is_rejected(self):
        lease = {**self._lease_payload(), "frontend_build": None}
        with self.assertRaisesRegex(
            guard.ReleaseGuardError,
            "prepared frontend build proof is missing",
        ):
            guard._validated_release_lease(lease)

    def test_verify_rejects_missing_boot_identity(self):
        lease = self._lease_payload()
        health = self._health_payload_for(lease, None)

        with self.assertRaisesRegex(
            guard.ReleaseGuardError,
            "boot identity is missing",
        ):
            self._verify_with(lease, [health])

    def test_verify_rejects_boot_identity_without_timezone_or_before_prepare(self):
        lease = self._lease_payload()
        invalid_boots = {
            "not-a-timestamp": "is invalid",
            "2026-08-13T10:01:00": "must include a timezone",
            "2026-08-13T09:54:59+00:00": "predates prepared release",
        }
        for boot_started_at, error in invalid_boots.items():
            with self.subTest(boot_started_at=boot_started_at), self.assertRaisesRegex(
                guard.ReleaseGuardError,
                error,
            ):
                self._verify_with(
                    lease,
                    [self._health_payload_for(lease, boot_started_at)],
                )

    def test_verify_rejects_prepared_timestamp_without_timezone(self):
        lease = {
            **self._lease_payload(),
            "prepared_at": "2026-08-13T10:00:00",
        }

        with self.assertRaisesRegex(
            guard.ReleaseGuardError,
            "prepared release timestamp must include a timezone",
        ):
            self._verify_with(
                lease,
                [self._health_payload_for(
                    lease,
                    "2026-08-13T10:01:00+00:00",
                )],
            )

    def test_verify_rejects_missing_prepared_release_id(self):
        lease = {**self._lease_payload(), "release_id": None}

        with self.assertRaisesRegex(
            guard.ReleaseGuardError,
            "prepared release id is invalid",
        ):
            self._verify_with(
                lease,
                [self._health_payload_for(
                    lease,
                    "2026-08-13T10:01:00+00:00",
                )],
            )

    def test_verify_rejects_v1_lease_and_requires_reprepare(self):
        lease = {**self._lease_payload(), "protocol_version": 1}

        with self.assertRaisesRegex(
            guard.ReleaseGuardError,
            "abort the existing lease with its original guard.*prepare again",
        ):
            self._verify_with(lease, [])

    def test_prepublish_rejects_v1_lease_before_any_git_action(self):
        lease = {**self._lease_payload(), "protocol_version": 1}
        with tempfile.TemporaryDirectory() as tmp:
            lease_dir = Path(tmp) / "release.lock"
            lease_dir.mkdir()
            lease_path = Path(tmp) / "lease.json"
            lease_path.write_text(json.dumps(lease), encoding="utf-8")
            with patch.object(guard, "LEASE_DIR", lease_dir), patch.object(
                guard, "LEASE_PATH", lease_path
            ), patch.object(guard, "_run_git") as run_git, self.assertRaisesRegex(
                guard.ReleaseGuardError,
                "abort the existing lease with its original guard",
            ):
                guard.prepublish()

            run_git.assert_not_called()

    def test_prepublish_revalidates_lease_after_fetch_race(self):
        lease = self._lease_payload()
        replacement = {
            **lease,
            "release_id": "b6ab746b-8998-46dd-9685-2a7deecfbc8a",
            "actor": "replacement-owner",
        }
        with tempfile.TemporaryDirectory() as tmp:
            lease_dir = Path(tmp) / "release.lock"
            lease_dir.mkdir()
            lease_path = lease_dir / "lease.json"
            identity_path = Path(tmp) / "release_identity.json"
            lease_path.write_text(json.dumps(lease), encoding="utf-8")
            identity_path.write_text(json.dumps(lease), encoding="utf-8")

            def git_result(*args):
                if args == ("branch", "--show-current"):
                    return guard.PRODUCTION_BRANCH
                if args == ("status", "--porcelain", "--untracked-files=no"):
                    return ""
                if args == ("fetch", "origin", guard.PRODUCTION_BRANCH):
                    lease_path.write_text(
                        json.dumps(replacement), encoding="utf-8"
                    )
                    return ""
                if args[0] == "rev-parse":
                    return lease["git_sha"]
                raise AssertionError(f"unexpected git call: {args!r}")

            with patch.object(guard, "LEASE_DIR", lease_dir), patch.object(
                guard, "LEASE_PATH", lease_path
            ), patch.object(
                guard, "IDENTITY_PATH", identity_path
            ), patch.object(
                guard, "_run_git", side_effect=git_result
            ), patch.object(
                guard,
                "_critical_hashes",
                return_value=lease["critical_file_hashes"],
            ), patch.object(
                guard,
                "_frontend_build_identity",
                return_value=lease["frontend_build"],
            ), self.assertRaisesRegex(
                guard.ReleaseGuardError,
                "active production release lease changed during operation",
            ):
                guard.prepublish()

            self.assertTrue(lease_dir.exists())
            self.assertEqual(
                json.loads(lease_path.read_text(encoding="utf-8")),
                replacement,
            )

    def test_prepublish_rejects_frontend_drift_and_preserves_lease(self):
        lease = self._lease_payload()
        drifted_frontend = {
            **lease["frontend_build"],
            "artifact_tree_sha256": "9" * 64,
        }
        with tempfile.TemporaryDirectory() as tmp:
            lease_dir = Path(tmp) / "release.lock"
            lease_dir.mkdir()
            lease_path = lease_dir / "lease.json"
            identity_path = Path(tmp) / "release_identity.json"
            lease_path.write_text(json.dumps(lease), encoding="utf-8")
            identity_path.write_text(json.dumps(lease), encoding="utf-8")

            def git_result(*args):
                if args == ("branch", "--show-current"):
                    return guard.PRODUCTION_BRANCH
                if args == ("status", "--porcelain", "--untracked-files=no"):
                    return ""
                if args == ("fetch", "origin", guard.PRODUCTION_BRANCH):
                    return ""
                if args[0] == "rev-parse":
                    return lease["git_sha"]
                raise AssertionError(f"unexpected git call: {args!r}")

            with patch.object(guard, "LEASE_DIR", lease_dir), patch.object(
                guard, "LEASE_PATH", lease_path
            ), patch.object(
                guard, "IDENTITY_PATH", identity_path
            ), patch.object(
                guard, "_run_git", side_effect=git_result
            ), patch.object(
                guard,
                "_critical_hashes",
                return_value=lease["critical_file_hashes"],
            ), patch.object(
                guard,
                "_frontend_build_identity",
                return_value=drifted_frontend,
            ), self.assertRaisesRegex(
                guard.ReleaseGuardError,
                "release identity, critical files, or frontend build changed",
            ):
                guard.prepublish()

            self.assertTrue(lease_dir.exists())

    def test_abort_requires_exact_sha_and_release_id(self):
        lease = self._lease_payload()
        other_release_id = "b6ab746b-8998-46dd-9685-2a7deecfbc8a"
        with tempfile.TemporaryDirectory() as tmp:
            lease_dir = Path(tmp) / "release.lock"
            lease_dir.mkdir()
            lease_path = lease_dir / "lease.json"
            lease_path.write_text(json.dumps(lease), encoding="utf-8")
            with patch.object(guard, "LEASE_DIR", lease_dir), patch.object(
                guard, "LEASE_PATH", lease_path
            ):
                with self.assertRaisesRegex(
                    guard.ReleaseGuardError, "lease SHA mismatch"
                ):
                    guard.abort("b" * 40, lease["release_id"])
                self.assertTrue(lease_dir.exists())

                with self.assertRaisesRegex(
                    guard.ReleaseGuardError, "lease release id mismatch"
                ):
                    guard.abort(lease["git_sha"], other_release_id)
                self.assertTrue(lease_dir.exists())

                with self.assertRaisesRegex(
                    guard.ReleaseGuardError, "expected release id is invalid"
                ):
                    guard.abort(lease["git_sha"], "not-a-release-uuid")
                self.assertTrue(lease_dir.exists())

                result = guard.abort(
                    lease["git_sha"], lease["release_id"]
                )

            self.assertEqual(
                result,
                {
                    "aborted": True,
                    "git_sha": lease["git_sha"],
                    "release_id": lease["release_id"],
                    "protocol_version": guard.PROTOCOL_VERSION,
                },
            )
            self.assertFalse(lease_dir.exists())

    def test_abort_refuses_legacy_v1_lease_without_removing_it(self):
        lease = {**self._lease_payload(), "protocol_version": 1}
        with tempfile.TemporaryDirectory() as tmp:
            lease_dir = Path(tmp) / "release.lock"
            lease_dir.mkdir()
            lease_path = lease_dir / "lease.json"
            lease_path.write_text(json.dumps(lease), encoding="utf-8")
            with patch.object(guard, "LEASE_DIR", lease_dir), patch.object(
                guard, "LEASE_PATH", lease_path
            ), self.assertRaisesRegex(
                guard.ReleaseGuardError,
                "abort the existing lease with its original guard",
            ):
                guard.abort(lease["git_sha"], lease["release_id"])

            self.assertTrue(lease_dir.exists())

    def test_abort_does_not_delete_replacement_lease_before_removal(self):
        lease = self._lease_payload()
        replacement = {
            **lease,
            "release_id": "b6ab746b-8998-46dd-9685-2a7deecfbc8a",
            "actor": "replacement-owner",
        }
        with tempfile.TemporaryDirectory() as tmp:
            lease_dir = Path(tmp) / "release.lock"
            lease_dir.mkdir()
            lease_path = lease_dir / "lease.json"
            lease_path.write_text(json.dumps(lease), encoding="utf-8")
            remove_active_lease = guard._remove_active_lease

            def replace_before_removal(expected_lease):
                lease_path.write_text(
                    json.dumps(replacement), encoding="utf-8"
                )
                return remove_active_lease(expected_lease)

            with patch.object(guard, "LEASE_DIR", lease_dir), patch.object(
                guard, "LEASE_PATH", lease_path
            ), patch.object(
                guard,
                "_remove_active_lease",
                side_effect=replace_before_removal,
            ), self.assertRaisesRegex(
                guard.ReleaseGuardError,
                "active production release lease changed during operation",
            ):
                guard.abort(lease["git_sha"], lease["release_id"])

            self.assertTrue(lease_dir.exists())
            self.assertEqual(
                json.loads(lease_path.read_text(encoding="utf-8")),
                replacement,
            )

    def test_verify_rejects_boot_identity_too_far_in_future(self):
        lease = self._lease_payload()
        health = self._health_payload_for(
            lease,
            "2026-08-13T10:10:01+00:00",
        )

        with self.assertRaisesRegex(
            guard.ReleaseGuardError,
            "boot identity is in the future",
        ):
            self._verify_with(lease, [health])


if __name__ == "__main__":
    unittest.main()
