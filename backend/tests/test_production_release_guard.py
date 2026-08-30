from __future__ import annotations

import json
import hashlib
import io
import tempfile
import threading
import unittest
import urllib.error
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from scripts import production_release_guard as guard


class _Headers(dict):
    def get(self, key, default=None):
        return super().get(key, default)


class _Response:
    def __init__(self, payload, *, url, headers=None, status=200):
        self._payload = payload
        self._url = url
        self._status = status
        self.headers = _Headers(headers or {"Content-Type": "application/json"})

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        if isinstance(self._payload, bytes):
            return self._payload
        return json.dumps(self._payload).encode("utf-8")

    def getcode(self):
        return self._status

    def geturl(self):
        return self._url


class ProductionReleaseGuardTests(unittest.TestCase):
    def setUp(self):
        runtime_validator = patch.object(
            guard,
            "validate_runtime_release_identity",
            side_effect=lambda payload, **_kwargs: payload,
        )
        runtime_validator.start()
        self.addCleanup(runtime_validator.stop)
        # Source/deployment ancestry has a dedicated contract test below.
        # Other guard tests isolate lease, drift, and HTTP behavior.
        if self._testMethodName != (
            "test_reviewed_source_relation_allows_only_release_intent_delta"
        ):
            relation = patch.object(
                guard,
                "_assert_reviewed_source_relation",
                return_value=None,
            )
            relation.start()
            self.addCleanup(relation.stop)
        reproducibility = patch.object(
            guard,
            "_frontend_reproducibility_proof",
            side_effect=self._frontend_reproducibility,
        )
        reproducibility.start()
        self.addCleanup(reproducibility.stop)
        runtime_identity = self._lease_payload()["runtime_identity"]
        runtime_reader = patch.object(
            guard,
            "_read_runtime_identity",
            return_value=runtime_identity,
        )
        intent_reader = patch.object(
            guard,
            "_read_reviewed_runtime_identity",
            return_value=runtime_identity,
        )
        runtime_reader.start()
        intent_reader.start()
        self.addCleanup(runtime_reader.stop)
        self.addCleanup(intent_reader.stop)

    @staticmethod
    def _build_meta_bytes():
        return b'{"schema_version":1}\n'

    @staticmethod
    def _frontend_build():
        index = {
            "path": "index.html",
            "bytes": 5,
            "sha256": hashlib.sha256(b"index").hexdigest(),
        }
        app = {
            "path": "assets/app.js",
            "bytes": 3,
            "sha256": hashlib.sha256(b"app").hexdigest(),
        }
        retirement_bytes = (
            guard.REPO_ROOT / "frontend" / "public" / "sw.js"
        ).read_bytes()
        retirement_workers = [
            {
                "path": path,
                "bytes": len(retirement_bytes),
                "sha256": hashlib.sha256(retirement_bytes).hexdigest(),
            }
            for path in ("service-worker.js", "sw.js")
        ]
        return {
            "schema_version": 1,
            "git_sha": "a" * 40,
            "source": {
                "scope": "git_head_frontend_tree_v1",
                "git_tree_oid": "1" * 40,
                "file_count": 671,
                "tree_sha256": "2" * 64,
            },
            "toolchain": {
                "node": "22.23.2",
                "yarn": "1.22.22",
                "vite": "8.2.1",
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
            "entrypoints": [],
            "assets": [app],
            "public_files": sorted(
                [app, index, *retirement_workers],
                key=lambda row: row["path"],
            ),
            "artifact_tree_sha256": "3" * 64,
            "build_meta": {
                "path": "build-meta.json",
                "bytes": len(ProductionReleaseGuardTests._build_meta_bytes()),
                "sha256": hashlib.sha256(
                    ProductionReleaseGuardTests._build_meta_bytes()
                ).hexdigest(),
            },
        }

    @staticmethod
    def _lease_payload():
        frontend_build = ProductionReleaseGuardTests._frontend_build()
        critical_file_hashes = {
            relative.removeprefix("backend/"): f"{index:x}"[-1] * 64
            for index, relative in enumerate(guard.CRITICAL_FILES, start=11)
        }
        frontend_reproducibility = (
            ProductionReleaseGuardTests._frontend_reproducibility(
                frontend_build
            )
        )
        runtime_identity = {
            "kind": "mezan_runtime_release_identity_v5",
            "schema_version": 1,
            "protocol_version": guard.PROTOCOL_VERSION,
            "release_id": "rg5-" + "9" * 64,
            "source_git_sha": "a" * 40,
            "branch": guard.PRODUCTION_BRANCH,
            "critical_file_hashes": critical_file_hashes,
            "frontend_build": frontend_build,
            "frontend_reproducibility": frontend_reproducibility,
        }
        return {
            "release_id": runtime_identity["release_id"],
            "source_git_sha": "a" * 40,
            "git_sha": "a" * 40,
            "deployment_git_sha": "b" * 40,
            "branch": guard.PRODUCTION_BRANCH,
            "actor": "test",
            "prepared_at": "2026-08-13T10:00:00+00:00",
            "protocol_version": guard.PROTOCOL_VERSION,
            "critical_file_hashes": critical_file_hashes,
            "frontend_build": frontend_build,
            "frontend_reproducibility": frontend_reproducibility,
            "runtime_identity": runtime_identity,
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
    def _lease_with_release_id(lease, release_id):
        return {
            **lease,
            "release_id": release_id,
            "runtime_identity": {
                **lease["runtime_identity"],
                "release_id": release_id,
            },
        }

    @staticmethod
    def _health_payload_for(lease, boot_started_at, **release_updates):
        runtime_identity = lease["runtime_identity"]
        release = {
            "verified_identity_available": True,
            "identity_kind": runtime_identity["kind"],
            "identity_schema_version": runtime_identity["schema_version"],
            **{
                field: runtime_identity[field]
                for field in (
                    "release_id",
                    "source_git_sha",
                    "branch",
                    "protocol_version",
                    "critical_file_hashes",
                    "frontend_build",
                    "frontend_reproducibility",
                )
            },
            "git_sha": lease["git_sha"],
            "critical_file_hashes_match": True,
            "frontend_build_verified": True,
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
                    "checked_requests": [
                        "/build-meta.json",
                        "/index.html",
                        "/assets/app.js",
                    ],
                    "shell_cache": [],
                    "service_workers": [],
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
        opened_requests = []

        def open_response(request, timeout):
            opened_requests.append((request, timeout))
            return _Response(
                {"ok": True, "release": {"git_sha": "a" * 40}},
                url=request.full_url,
            )

        with patch.object(
            guard,
            "_urlopen_no_redirect",
            side_effect=open_response,
        ):
            self.assertTrue(
                guard._health_payload(guard.PRODUCTION_ORIGIN)["ok"]
            )

        request = opened_requests[0][0]
        self.assertEqual(request.get_method(), "GET")
        self.assertIsNone(request.data)
        self.assertTrue(
            request.full_url.startswith("https://mezansalla.com/api/health?")
        )
        self.assertTrue(
            request.get_header("User-agent", "").startswith("Mozilla/5.0")
        )
        self.assertEqual(request.get_header("Accept"), "application/json")
        query = urllib.parse.urlsplit(request.full_url).query
        self.assertEqual(
            [name for name, _ in urllib.parse.parse_qsl(query)],
            ["release_check"],
        )

    def test_canonical_frontend_probe_has_no_query_or_cache_bypass_header(self):
        requests = []

        def open_response(request, timeout):
            requests.append((request, timeout))
            return _Response(
                b"index",
                url=request.full_url,
                headers={
                    "Content-Type": "text/html",
                    "Cache-Control": "no-cache, no-store, must-revalidate",
                },
            )

        with patch.object(
            guard, "_urlopen_no_redirect", side_effect=open_response
        ):
            response = guard._fetch_production(
                guard.PRODUCTION_ORIGIN,
                "/",
                cache_bust=False,
                accept="*/*",
                timeout=30,
            )

        request = requests[0][0]
        self.assertEqual(request.get_method(), "GET")
        self.assertIsNone(request.data)
        self.assertEqual(request.full_url, "https://mezansalla.com/")
        self.assertIsNone(request.get_header("Cache-control"))
        self.assertEqual(response["body"], b"index")

    def test_frontend_probe_allows_only_an_explicit_404_contract(self):
        url = "https://mezansalla.com/sw.js"

        def not_found():
            return urllib.error.HTTPError(
                url,
                404,
                "Not Found",
                {"Content-Type": "text/plain"},
                io.BytesIO(b"not found"),
            )

        with patch.object(
            guard, "_urlopen_no_redirect", side_effect=not_found()
        ):
            result = guard._fetch_production(
                guard.PRODUCTION_ORIGIN,
                "/sw.js",
                cache_bust=False,
                accept="*/*",
                timeout=30,
                allowed_statuses=frozenset({200, 404}),
            )
        self.assertEqual(result["status"], 404)
        self.assertEqual(result["body"], b"not found")

        with patch.object(
            guard, "_urlopen_no_redirect", side_effect=not_found()
        ), self.assertRaisesRegex(
            guard.ReleaseGuardError,
            "returned HTTP 404",
        ):
            guard._fetch_production(
                guard.PRODUCTION_ORIGIN,
                "/sw.js",
                cache_bust=False,
                accept="*/*",
                timeout=30,
            )

    def test_service_worker_probe_uses_browser_script_request_header(self):
        opened_requests = []

        def open_response(request, timeout):
            opened_requests.append((request, timeout))
            return _Response(
                b"self.skipWaiting();",
                url=request.full_url,
                headers={
                    "Content-Type": "application/javascript",
                    "Cache-Control": "no-cache",
                },
            )

        with patch.object(
            guard, "_urlopen_no_redirect", side_effect=open_response
        ):
            guard._fetch_production(
                guard.PRODUCTION_ORIGIN,
                "/sw.js",
                cache_bust=False,
                accept="*/*",
                timeout=30,
                service_worker_script=True,
            )

        self.assertEqual(
            opened_requests[0][0].get_header("Service-worker"),
            "script",
        )

    def test_release_protocol_is_v5(self):
        self.assertEqual(guard.PROTOCOL_VERSION, 5)

    def test_reviewed_client_origin_is_required_and_exact(self):
        approved = {
            "REACT_APP_BACKEND_URL": {
                "present": True,
                "value": "https://mezansalla.com",
            }
        }
        self.assertEqual(
            guard._validated_reviewed_client_environment(approved),
            approved,
        )
        for rejected in (
            {
                "REACT_APP_BACKEND_URL": {
                    "present": False,
                    "value": None,
                }
            },
            {
                "REACT_APP_BACKEND_URL": {
                    "present": True,
                    "value": "http://mezansalla.com",
                }
            },
            {
                "REACT_APP_BACKEND_URL": {
                    "present": True,
                    "value": "https://evil.example.test",
                }
            },
        ):
            with self.subTest(rejected=rejected), self.assertRaises(
                guard.ReleaseGuardError
            ):
                guard._validated_reviewed_client_environment(rejected)

    def test_local_release_checks_require_clean_git_source_proof(self):
        proof = self._frontend_build()
        with patch.object(
            guard,
            "read_frontend_build_identity",
            return_value=proof,
        ) as reader:
            self.assertEqual(guard._frontend_build_identity("a" * 40), proof)
        reader.assert_called_once_with(
            expected_git_sha="a" * 40,
            require_git_source=True,
        )

    def test_verify_origin_is_pinned_and_release_check_is_never_duplicated(self):
        for invalid in (
            "http://mezansalla.com",
            "https://evil.mezansalla.com",
            "https://user@mezansalla.com",
            "https://mezansalla.com:443",
            "https://mezansalla.com/path",
            "https://mezansalla.com?release_check=old",
        ):
            with self.subTest(invalid=invalid), self.assertRaisesRegex(
                guard.ReleaseGuardError, "must be exactly"
            ):
                guard._validated_production_origin(invalid)

        canonical = guard._release_check_url(
            guard.PRODUCTION_ORIGIN, "/index.html", cache_bust=False
        )
        busted = guard._release_check_url(
            guard.PRODUCTION_ORIGIN, "/index.html", cache_bust=True
        )
        self.assertEqual(canonical, "https://mezansalla.com/index.html")
        self.assertEqual(busted.count("release_check="), 1)
        with self.assertRaisesRegex(
            guard.ReleaseGuardError, "path is invalid"
        ):
            guard._release_check_url(
                guard.PRODUCTION_ORIGIN,
                "/index.html?release_check=old",
                cache_bust=True,
            )

    def test_wrong_origin_preserves_active_lease_without_network(self):
        lease = self._lease_payload()
        with tempfile.TemporaryDirectory() as tmp:
            lease_dir = Path(tmp) / "release.lock"
            lease_dir.mkdir()
            lease_path = lease_dir / "lease.json"
            lease_path.write_text(json.dumps(lease), encoding="utf-8")
            with patch.object(guard, "LEASE_DIR", lease_dir), patch.object(
                guard, "LEASE_PATH", lease_path
            ), patch.object(
                guard, "_health_payload"
            ) as health, self.assertRaisesRegex(
                guard.ReleaseGuardError, "must be exactly"
            ):
                guard.verify("https://evil.mezansalla.com")
            health.assert_not_called()
            self.assertTrue(lease_dir.exists())

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

    def test_incomplete_lease_directory_is_reported_and_safely_recovered(self):
        with tempfile.TemporaryDirectory() as tmp:
            git_dir = Path(tmp) / ".git"
            git_dir.mkdir()
            lease_dir = git_dir / "release.lock"
            lease_dir.mkdir()
            lease_path = lease_dir / "lease.json"
            (lease_dir / "lease.json.tmp").write_text(
                "partial\n", encoding="utf-8"
            )
            with (
                patch.object(guard, "LEASE_DIR", lease_dir),
                patch.object(guard, "LEASE_PATH", lease_path),
            ):
                self.assertEqual(
                    guard.status(),
                    {
                        "active": False,
                        "incomplete_lease_directory": True,
                    },
                )
                guard._create_or_recover_lease_dir()

            self.assertTrue(lease_dir.is_dir())
            self.assertEqual(list(lease_dir.iterdir()), [])

    def test_incomplete_lease_recovery_rejects_symlink_or_unknown_entry(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for kind in ("symlink", "unknown"):
                with self.subTest(kind=kind):
                    lease_dir = root / f"{kind}.lock"
                    lease_path = lease_dir / "lease.json"
                    if kind == "symlink":
                        outside = root / "outside"
                        outside.mkdir(exist_ok=True)
                        lease_dir.symlink_to(outside, target_is_directory=True)
                    else:
                        lease_dir.mkdir()
                        (lease_dir / "unexpected").write_text(
                            "keep\n", encoding="utf-8"
                        )
                    with (
                        patch.object(guard, "LEASE_DIR", lease_dir),
                        patch.object(guard, "LEASE_PATH", lease_path),
                        self.assertRaises(guard.ReleaseGuardError),
                    ):
                        guard._create_or_recover_lease_dir()
                    if kind == "symlink":
                        self.assertTrue(lease_dir.is_symlink())
                    else:
                        self.assertTrue((lease_dir / "unexpected").exists())

    def test_prepare_reads_deterministic_identity_and_never_writes_it(self):
        lease = self._lease_payload()
        runtime_identity = lease["runtime_identity"]
        identity_bytes = b'{"sentinel":"adapter-owned"}\n'
        with tempfile.TemporaryDirectory() as tmp:
            git_dir = Path(tmp) / ".git"
            git_dir.mkdir()
            lease_dir = git_dir / "release.lock"
            lease_path = lease_dir / "lease.json"
            identity_path = Path(tmp) / "release_identity.json"
            identity_path.write_bytes(identity_bytes)

            def git_result(*args):
                if args == ("branch", "--show-current"):
                    return guard.PRODUCTION_BRANCH
                if args == ("status", "--porcelain", "--untracked-files=no"):
                    return ""
                if args == ("fetch", "origin", guard.PRODUCTION_BRANCH):
                    return ""
                if args[0] == "rev-parse":
                    return lease["deployment_git_sha"]
                raise AssertionError(f"unexpected git call: {args!r}")

            with patch.object(guard, "LEASE_DIR", lease_dir), patch.object(
                guard, "LEASE_PATH", lease_path
            ), patch.object(
                guard, "IDENTITY_PATH", identity_path
            ), patch.object(
                guard, "_run_git", side_effect=git_result
            ), patch.object(
                guard, "_read_runtime_identity", return_value=runtime_identity
            ), patch.object(
                guard,
                "_read_reviewed_runtime_identity",
                return_value=runtime_identity,
            ), patch.object(
                guard, "_frontend_build_identity", return_value=lease["frontend_build"]
            ), patch.object(
                guard, "_critical_hashes", return_value=lease["critical_file_hashes"]
            ):
                prepared = guard._prepare_locked("actor-one")

            self.assertEqual(identity_path.read_bytes(), identity_bytes)
            self.assertEqual(prepared["release_id"], runtime_identity["release_id"])
            self.assertRegex(prepared["release_id"], r"^rg5-[0-9a-f]{64}$")
            self.assertNotIn("actor", prepared["runtime_identity"])
            self.assertNotIn("prepared_at", prepared["runtime_identity"])
            self.assertEqual(
                json.loads(lease_path.read_text(encoding="utf-8"))["release_id"],
                runtime_identity["release_id"],
            )

    def test_lease_mirror_rejects_boolean_integer_type_coercion(self):
        lease = self._lease_payload()
        lease["frontend_build"] = json.loads(json.dumps(lease["frontend_build"]))
        lease["frontend_build"]["environment"]["effective"][
            "VITE_USER_NODE_ENV_present"
        ] = 0
        with self.assertRaisesRegex(
            guard.ReleaseGuardError,
            "prepared lease does not match runtime release identity",
        ):
            guard._runtime_identity_from_lease(lease)

    def test_local_identity_binding_rejects_integer_float_type_coercion(self):
        lease = self._lease_payload()
        identity = json.loads(json.dumps(lease["runtime_identity"]))
        identity["frontend_build"]["source"]["file_count"] = 671.0
        with (
            patch.object(
                guard,
                "_frontend_build_identity",
                return_value=lease["frontend_build"],
            ),
            patch.object(
                guard,
                "_critical_hashes",
                return_value=lease["critical_file_hashes"],
            ),
            self.assertRaisesRegex(
                guard.ReleaseGuardError,
                "does not match the local governed package",
            ),
        ):
            guard._assert_local_identity_binding(
                identity,
                expected_git_sha=lease["source_git_sha"],
            )

    def test_prepare_rejects_identity_that_differs_from_reviewed_intent(self):
        lease = self._lease_payload()
        runtime_identity = lease["runtime_identity"]
        reviewed_identity = {
            **runtime_identity,
            "release_id": "rg5-" + "b" * 64,
        }
        with tempfile.TemporaryDirectory() as tmp:
            lease_dir = Path(tmp) / "release.lock"
            lease_path = lease_dir / "lease.json"

            def git_result(*args):
                if args == ("branch", "--show-current"):
                    return guard.PRODUCTION_BRANCH
                if args == ("status", "--porcelain", "--untracked-files=no"):
                    return ""
                if args == ("fetch", "origin", guard.PRODUCTION_BRANCH):
                    return ""
                if args[0] == "rev-parse":
                    return lease["deployment_git_sha"]
                raise AssertionError(f"unexpected git call: {args!r}")

            with patch.object(guard, "LEASE_DIR", lease_dir), patch.object(
                guard, "LEASE_PATH", lease_path
            ), patch.object(
                guard, "_run_git", side_effect=git_result
            ), patch.object(
                guard, "_read_runtime_identity", return_value=runtime_identity
            ), patch.object(
                guard,
                "_read_reviewed_runtime_identity",
                return_value=reviewed_identity,
            ), self.assertRaisesRegex(
                guard.ReleaseGuardError,
                "does not match reviewed release intent",
            ):
                guard._prepare_locked("actor-one")

            self.assertFalse(lease_dir.exists())
            self.assertFalse(lease_path.exists())

    def test_reviewed_source_relation_allows_only_release_intent_delta(self):
        source = "a" * 40
        deployment = "b" * 40
        intent_blob = "c" * 40

        def allowed(*args):
            if args[:2] == ("merge-base", "--is-ancestor"):
                return ""
            if args[:2] == ("diff", "--name-only"):
                return guard.RELEASE_INTENT_RELATIVE_PATH
            if args[:2] == ("ls-files", "--error-unmatch"):
                return guard.RELEASE_INTENT_RELATIVE_PATH
            if args == (
                "rev-parse",
                f"{deployment}:{guard.RELEASE_INTENT_RELATIVE_PATH}",
            ):
                return intent_blob
            if args == ("hash-object", guard.RELEASE_INTENT_RELATIVE_PATH):
                return intent_blob
            raise AssertionError(args)

        with patch.object(guard, "_run_git", side_effect=allowed):
            guard._assert_reviewed_source_relation(
                source_git_sha=source,
                deployment_git_sha=deployment,
            )

        def forbidden(*args):
            if args[:2] == ("merge-base", "--is-ancestor"):
                return ""
            if args[:2] == ("diff", "--name-only"):
                return guard.RELEASE_INTENT_RELATIVE_PATH + "\nbackend/server.py"
            raise AssertionError(args)

        with patch.object(
            guard, "_run_git", side_effect=forbidden
        ), self.assertRaisesRegex(
            guard.ReleaseGuardError, "reviewed release intent only"
        ):
            guard._assert_reviewed_source_relation(
                source_git_sha=source,
                deployment_git_sha=deployment,
            )

        for changed, message in (
            ("", "no tracked change"),
            ("backend/server.py", "reviewed release intent only"),
        ):
            def incomplete(*args):
                if args[:2] == ("merge-base", "--is-ancestor"):
                    return ""
                if args[:2] == ("diff", "--name-only"):
                    return changed
                raise AssertionError(args)

            with self.subTest(changed=changed), patch.object(
                guard, "_run_git", side_effect=incomplete
            ), self.assertRaisesRegex(guard.ReleaseGuardError, message):
                guard._assert_reviewed_source_relation(
                    source_git_sha=source,
                    deployment_git_sha=deployment,
                )

        with self.assertRaisesRegex(
            guard.ReleaseGuardError,
            "separate tracked intent-only deployment commit",
        ):
            guard._assert_reviewed_source_relation(
                source_git_sha=source,
                deployment_git_sha=source,
            )

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
            "release_id": "rg5-" + "b" * 64,
            "branch": "main",
            "identity_kind": "unexpected_runtime_identity_kind",
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

    def test_verify_rejects_health_boolean_integer_type_coercion(self):
        lease = self._lease_payload()
        frontend_build = json.loads(json.dumps(lease["frontend_build"]))
        frontend_build["environment"]["effective"][
            "VITE_USER_NODE_ENV_present"
        ] = 0
        health = self._health_payload_for(
            lease,
            "2026-08-13T10:01:00+00:00",
            frontend_build=frontend_build,
        )
        with self.assertRaisesRegex(
            guard.ReleaseGuardError,
            "release identity does not match prepared lease: frontend_build",
        ):
            self._verify_with(lease, [health])

        integer_identity = self._health_payload_for(
            lease,
            "2026-08-13T10:01:00+00:00",
            verified_identity_available=1,
        )
        with self.assertRaisesRegex(
            guard.ReleaseGuardError,
            "no verified release identity",
        ):
            self._verify_with(lease, [integer_identity])

    def test_verify_wrong_release_id_same_sha_preserves_active_lease(self):
        lease = self._lease_payload()
        health = self._health_payload_for(
            lease,
            "2026-08-13T10:01:00+00:00",
            release_id="rg5-" + "b" * 64,
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
            **self._lease_with_release_id(lease, "rg5-" + "b" * 64),
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
                    "checked_requests": ["/index.html", "/assets/app.js"],
                    "shell_cache": [],
                    "service_workers": [],
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

    def test_public_frontend_verifies_canonical_busted_and_every_public_file(self):
        expected = self._frontend_build()
        lazy = {
            "path": "assets/lazy.js",
            "bytes": 4,
            "sha256": hashlib.sha256(b"lazy").hexdigest(),
        }
        expected["assets"].append(lazy)
        expected["public_files"] = sorted(
            [*expected["public_files"], lazy],
            key=lambda row: row["path"],
        )
        retirement = (
            guard.REPO_ROOT / "frontend" / "public" / "sw.js"
        ).read_bytes()
        payloads = {
            "/build-meta.json": self._build_meta_bytes(),
            "/index.html": b"index",
            "/assets/app.js": b"app",
            "/assets/lazy.js": b"lazy",
            "/sw.js": retirement,
            "/service-worker.js": retirement,
            "/": b"index",
            guard.SPA_SHELL_PATH: b"index",
        }
        calls = []

        def fetch(
            _origin,
            relative,
            *,
            cache_bust,
            accept,
            timeout,
            service_worker_script=False,
        ):
            calls.append((relative, cache_bust, accept, timeout))
            self.assertEqual(
                service_worker_script,
                relative in guard.STANDARD_SERVICE_WORKER_PATHS,
            )
            if relative == "/build-meta.json":
                content_type = "application/json; charset=utf-8"
            elif service_worker_script:
                content_type = "application/javascript"
            else:
                content_type = "text/html; charset=utf-8"
            return {
                "status": 200,
                "body": payloads[relative],
                "headers": {
                    "content-type": content_type,
                    "cache-control": (
                        "no-cache, no-store, must-revalidate, max-age=0"
                    ),
                    "etag": '"fixture"',
                    "age": "0",
                    "cf-cache-status": "DYNAMIC",
                    "x-content-type-options": "nosniff",
                },
            }

        with patch.object(
            guard,
            "_fetch_production",
            side_effect=fetch,
        ):
            result = guard._verify_public_frontend(
                "https://mezansalla.com", expected
            )

        self.assertEqual(
            len(result["checked_requests"]),
            len(payloads) * 2 + 2,
        )
        self.assertEqual(len(calls), len(payloads) * 2 + 2)
        for accept in ("application/json", "*/*"):
            self.assertIn(("/build-meta.json", False, accept, 30), calls)
            self.assertIn(("/build-meta.json", True, accept, 30), calls)
        self.assertIn(("/sw.js", False, "*/*", 30), calls)
        self.assertIn(("/sw.js", True, "*/*", 30), calls)
        self.assertIn(("/service-worker.js", False, "*/*", 30), calls)
        self.assertIn(("/service-worker.js", True, "*/*", 30), calls)
        self.assertEqual(
            [row["path"] for row in result["shell_cache"]],
            ["/index.html", "/", guard.SPA_SHELL_PATH],
        )
        self.assertEqual(
            [(row["path"], row["state"]) for row in result["service_workers"]],
            [
                ("/service-worker.js", "retirement_payload_present"),
                ("/sw.js", "retirement_payload_present"),
            ],
        )

    def test_build_meta_requires_json_mime_and_json_object(self):
        valid = {
            "status": 200,
            "body": self._build_meta_bytes(),
            "headers": {"content-type": "application/json; charset=utf-8"},
        }
        guard._assert_build_meta_response(valid, label="/build-meta.json")

        invalid_responses = (
            (
                {
                    **valid,
                    "headers": {"content-type": "text/html; charset=utf-8"},
                    "body": b"<html>SPA shell</html>",
                },
                "MIME is invalid",
            ),
            (
                {**valid, "body": b"<html>SPA shell</html>"},
                "invalid JSON",
            ),
            (
                {**valid, "body": b"[]"},
                "not a JSON object",
            ),
            (
                {**valid, "status": 404},
                "non-200",
            ),
            (
                {**valid, "headers": {"content-type": "application/jsonp"}},
                "MIME is invalid",
            ),
            (
                {
                    **valid,
                    "headers": {
                        "content-type": "application/json, text/html"
                    },
                },
                "MIME is invalid",
            ),
        )
        for response, error in invalid_responses:
            with self.subTest(error=error), self.assertRaisesRegex(
                guard.ReleaseGuardError, error
            ):
                guard._assert_build_meta_response(
                    response,
                    label="/build-meta.json",
                )

    def test_build_meta_spa_fallback_preserves_active_lease(self):
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
                    "production frontend build metadata MIME is invalid: "
                    "/build-meta.json=text/html"
                ),
            ), patch.object(guard.time, "sleep"), patch.object(
                guard,
                "_utc_datetime",
                return_value=datetime(
                    2026, 8, 13, 10, 5, tzinfo=timezone.utc
                ),
            ), self.assertRaisesRegex(
                guard.ReleaseGuardError,
                "build metadata MIME is invalid",
            ):
                guard.verify(guard.PRODUCTION_ORIGIN)

            self.assertTrue(lease_dir.exists())
            self.assertEqual(
                json.loads(lease_path.read_text(encoding="utf-8")), lease
            )

    def test_missing_or_modified_retirement_worker_fails_closed_before_fetch(self):
        expected = self._frontend_build()
        expected["public_files"] = [
            row for row in expected["public_files"] if row["path"] != "sw.js"
        ]
        with patch.object(guard, "_fetch_production") as fetch, self.assertRaisesRegex(
            guard.ReleaseGuardError,
            "retirement service worker is missing or invalid",
        ):
            guard._verify_public_frontend(guard.PRODUCTION_ORIGIN, expected)
        fetch.assert_not_called()

        expected = self._frontend_build()
        worker = next(
            row for row in expected["public_files"] if row["path"] == "sw.js"
        )
        worker["sha256"] = "0" * 64
        with patch.object(guard, "_fetch_production") as fetch, self.assertRaisesRegex(
            guard.ReleaseGuardError,
            "retirement service worker is missing or invalid",
        ):
            guard._verify_public_frontend(guard.PRODUCTION_ORIGIN, expected)
        fetch.assert_not_called()

    def test_retirement_worker_mime_and_cache_are_fail_closed(self):
        safe = {
            "headers": {
                "content-type": "application/javascript; charset=utf-8",
                "cache-control": "no-cache, no-store, must-revalidate, max-age=0",
                "x-content-type-options": "nosniff",
                "age": "0",
                "cf-cache-status": "DYNAMIC",
            }
        }
        result = guard._assert_service_worker_headers(safe, "/sw.js")
        self.assertEqual(result["state"], "retirement_payload_present")

        unsafe_headers = (
            {**safe["headers"], "content-type": "text/html"},
            {**safe["headers"], "x-content-type-options": ""},
            {**safe["headers"], "cache-control": "no-cache, max-age=0"},
            {**safe["headers"], "age": "10"},
            {**safe["headers"], "cf-cache-status": "HIT"},
        )
        for headers in unsafe_headers:
            with self.subTest(headers=headers), self.assertRaises(
                guard.ReleaseGuardError
            ):
                guard._assert_service_worker_headers(
                    {"headers": headers}, "/sw.js"
                )

    def test_public_frontend_rejects_unsafe_or_duplicate_public_path(self):
        expected = self._frontend_build()
        expected["public_files"] = [{
            "path": "assets/../secret",
            "bytes": 1,
            "sha256": "a" * 64,
        }]
        with self.assertRaisesRegex(
            guard.ReleaseGuardError,
            "public file path is invalid",
        ):
            guard._verify_public_frontend(
                "https://mezansalla.com", expected
            )

        expected = self._frontend_build()
        expected["public_files"].append(expected["public_files"][0])
        with self.assertRaisesRegex(
            guard.ReleaseGuardError,
            "sorted and unique",
        ):
            guard._verify_public_frontend(guard.PRODUCTION_ORIGIN, expected)

    def test_queryless_stale_shell_or_unsafe_cache_policy_fails_closed(self):
        expected = self._frontend_build()

        def stale_shell(_origin, relative, *, cache_bust, **_kwargs):
            body = b"stale" if relative == "/index.html" and not cache_bust else {
                "/": b"index",
                "/index.html": b"index",
                guard.SPA_SHELL_PATH: b"index",
                "/assets/app.js": b"app",
                "/build-meta.json": self._build_meta_bytes(),
            }[relative]
            return {
                "status": 200,
                "body": body,
                "headers": {
                    "content-type": (
                        "application/json"
                        if relative == "/build-meta.json"
                        else "text/html"
                    ),
                    "cache-control": "no-cache, no-store, must-revalidate",
                },
            }

        with patch.object(
            guard, "_fetch_production", side_effect=stale_shell
        ), self.assertRaisesRegex(
            guard.ReleaseGuardError, "deployed frontend SHA mismatch"
        ):
            guard._verify_public_frontend(guard.PRODUCTION_ORIGIN, expected)

        def cached_shell(_origin, relative, *, cache_bust, **_kwargs):
            body = {
                "/": b"index",
                "/index.html": b"index",
                guard.SPA_SHELL_PATH: b"index",
                "/assets/app.js": b"app",
                "/build-meta.json": self._build_meta_bytes(),
            }[relative]
            return {
                "status": 200,
                "body": body,
                "headers": {
                    "content-type": (
                        "application/json"
                        if relative == "/build-meta.json"
                        else "text/html"
                    ),
                    "cache-control": "public, max-age=3600, immutable",
                    "cf-cache-status": "HIT",
                },
            }

        with patch.object(
            guard, "_fetch_production", side_effect=cached_shell
        ), self.assertRaisesRegex(
            guard.ReleaseGuardError, "cache policy|immutable|cache age"
        ):
            guard._verify_public_frontend(guard.PRODUCTION_ORIGIN, expected)

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

    def test_release_lease_without_reproducibility_proof_is_rejected(self):
        lease = {**self._lease_payload(), "frontend_reproducibility": None}
        with self.assertRaisesRegex(
            guard.ReleaseGuardError,
            "prepared frontend reproducibility proof is missing",
        ):
            guard._validated_release_lease(lease)

    def test_release_lease_with_mismatched_reproducibility_proof_is_rejected(self):
        lease = self._lease_payload()
        lease["frontend_reproducibility"]["retained_pass"] = 1
        with self.assertRaisesRegex(
            guard.ReleaseGuardError,
            "prepared frontend reproducibility proof is invalid",
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

    def test_verify_rejects_v4_lease_and_requires_reprepare(self):
        lease = {**self._lease_payload(), "protocol_version": 4}

        with self.assertRaisesRegex(
            guard.ReleaseGuardError,
            "abort the existing lease with its original guard.*prepare again",
        ):
            self._verify_with(lease, [])

    def test_lease_rejects_protocol_and_sha_type_coercion(self):
        base = self._lease_payload()
        for invalid in (True, 5.0, "5"):
            with self.subTest(protocol=invalid), self.assertRaisesRegex(
                guard.ReleaseGuardError,
                "prepared release protocol is invalid",
            ):
                guard._validated_release_lease({
                    **base,
                    "protocol_version": invalid,
                })
        with self.assertRaisesRegex(
            guard.ReleaseGuardError,
            "prepared release SHA is invalid",
        ):
            guard._validated_release_lease({
                **base,
                "source_git_sha": int("1" * 40),
            })
        with self.assertRaisesRegex(
            guard.ReleaseGuardError,
            "prepared deployment SHA is invalid",
        ):
            guard._validated_release_lease({
                **base,
                "deployment_git_sha": int("1" * 40),
            })

    def test_prepare_frontend_double_read_drift_removes_lease_without_identity(self):
        first = self._frontend_build()
        second = {**first, "artifact_tree_sha256": "9" * 64}
        with tempfile.TemporaryDirectory() as tmp:
            lease_dir = Path(tmp) / "release.lock"
            lease_path = lease_dir / "lease.json"
            identity_path = Path(tmp) / "release_identity.json"

            def git_result(*args):
                if args == ("branch", "--show-current"):
                    return guard.PRODUCTION_BRANCH
                if args == ("status", "--porcelain", "--untracked-files=no"):
                    return ""
                if args == ("fetch", "origin", guard.PRODUCTION_BRANCH):
                    return ""
                if args[0] == "rev-parse":
                    return first["git_sha"]
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
                return_value=self._lease_payload()["critical_file_hashes"],
            ), patch.object(
                guard,
                "_frontend_build_identity",
                side_effect=[first, second],
            ), self.assertRaisesRegex(
                guard.ReleaseGuardError,
                "frontend source, critical files, or build changed while "
                "preparing release",
            ):
                guard._prepare_locked("test")

            self.assertFalse(lease_dir.exists())
            self.assertFalse(identity_path.exists())

    def test_prepublish_frontend_double_read_drift_preserves_lease(self):
        lease = self._lease_payload()
        drifted = {
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
                    return lease["deployment_git_sha"]
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
                side_effect=[lease["frontend_build"], drifted],
            ), self.assertRaisesRegex(
                guard.ReleaseGuardError,
                "frontend source, critical files, or build changed during "
                "prepublish verification",
            ):
                guard.prepublish()

            self.assertTrue(lease_dir.exists())
            self.assertEqual(
                json.loads(lease_path.read_text(encoding="utf-8")),
                lease,
            )

    def test_prepare_reproducibility_double_read_drift_removes_lease(self):
        frontend = self._frontend_build()
        first = self._frontend_reproducibility(frontend)
        second = {
            **first,
            "proof_file": {**first["proof_file"], "sha256": "9" * 64},
        }
        with tempfile.TemporaryDirectory() as tmp:
            lease_dir = Path(tmp) / "release.lock"
            lease_path = lease_dir / "lease.json"
            identity_path = Path(tmp) / "release_identity.json"

            def git_result(*args):
                if args == ("branch", "--show-current"):
                    return guard.PRODUCTION_BRANCH
                if args == ("status", "--porcelain", "--untracked-files=no"):
                    return ""
                if args == ("fetch", "origin", guard.PRODUCTION_BRANCH):
                    return ""
                if args[0] == "rev-parse":
                    return frontend["git_sha"]
                raise AssertionError(f"unexpected git call: {args!r}")

            with patch.object(guard, "LEASE_DIR", lease_dir), patch.object(
                guard, "LEASE_PATH", lease_path
            ), patch.object(
                guard, "IDENTITY_PATH", identity_path
            ), patch.object(
                guard, "_run_git", side_effect=git_result
            ), patch.object(
                guard, "_critical_hashes",
                return_value=self._lease_payload()["critical_file_hashes"],
            ), patch.object(
                guard, "_frontend_build_identity", return_value=frontend
            ), patch.object(
                guard,
                "_frontend_reproducibility_proof",
                side_effect=[first, second],
            ), self.assertRaisesRegex(
                guard.ReleaseGuardError,
                "frontend source, critical files, or build changed while preparing",
            ):
                guard._prepare_locked("test")

            self.assertFalse(lease_dir.exists())
            self.assertFalse(identity_path.exists())

    def test_prepublish_reproducibility_double_read_drift_preserves_lease(self):
        lease = self._lease_payload()
        first = lease["frontend_reproducibility"]
        second = {
            **first,
            "proof_file": {**first["proof_file"], "sha256": "9" * 64},
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
                    return lease["deployment_git_sha"]
                raise AssertionError(f"unexpected git call: {args!r}")

            with patch.object(guard, "LEASE_DIR", lease_dir), patch.object(
                guard, "LEASE_PATH", lease_path
            ), patch.object(
                guard, "IDENTITY_PATH", identity_path
            ), patch.object(
                guard, "_run_git", side_effect=git_result
            ), patch.object(
                guard, "_critical_hashes",
                return_value=lease["critical_file_hashes"],
            ), patch.object(
                guard,
                "_frontend_build_identity",
                return_value=lease["frontend_build"],
            ), patch.object(
                guard,
                "_frontend_reproducibility_proof",
                side_effect=[first, second],
            ), self.assertRaisesRegex(
                guard.ReleaseGuardError,
                "frontend source, critical files, or build changed during prepublish",
            ):
                guard.prepublish()

            self.assertTrue(lease_dir.exists())
            self.assertEqual(
                json.loads(lease_path.read_text(encoding="utf-8")), lease
            )

    def test_prepare_critical_drift_removes_lease_without_identity(self):
        frontend = self._frontend_build()
        first_hashes = self._lease_payload()["critical_file_hashes"]
        drifted_hashes = {**first_hashes, "server.py": "9" * 64}
        with tempfile.TemporaryDirectory() as tmp:
            lease_dir = Path(tmp) / "release.lock"
            lease_path = lease_dir / "lease.json"
            identity_path = Path(tmp) / "release_identity.json"

            def git_result(*args):
                if args == ("branch", "--show-current"):
                    return guard.PRODUCTION_BRANCH
                if args == ("status", "--porcelain", "--untracked-files=no"):
                    return ""
                if args == ("fetch", "origin", guard.PRODUCTION_BRANCH):
                    return ""
                if args[0] == "rev-parse":
                    return frontend["git_sha"]
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
                side_effect=[first_hashes, drifted_hashes],
            ), patch.object(
                guard,
                "_frontend_build_identity",
                return_value=frontend,
            ), self.assertRaisesRegex(
                guard.ReleaseGuardError,
                "frontend source, critical files, or build changed while "
                "preparing release",
            ):
                guard._prepare_locked("test")

            self.assertFalse(lease_dir.exists())
            self.assertFalse(identity_path.exists())

    def test_prepublish_critical_file_double_read_drift_preserves_lease(self):
        lease = self._lease_payload()
        drifted_hashes = {
            **lease["critical_file_hashes"],
            "server.py": "9" * 64,
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
                    return lease["deployment_git_sha"]
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
                side_effect=[lease["critical_file_hashes"], drifted_hashes],
            ), patch.object(
                guard,
                "_frontend_build_identity",
                return_value=lease["frontend_build"],
            ), self.assertRaisesRegex(
                guard.ReleaseGuardError,
                "frontend source, critical files, or build changed during "
                "prepublish verification",
            ):
                guard.prepublish()

            self.assertTrue(lease_dir.exists())
            self.assertEqual(
                json.loads(lease_path.read_text(encoding="utf-8")),
                lease,
            )

    def test_prepublish_rejects_v4_lease_before_any_git_action(self):
        lease = {**self._lease_payload(), "protocol_version": 4}
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
            **self._lease_with_release_id(lease, "rg5-" + "b" * 64),
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
                    return lease["deployment_git_sha"]
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
                    return lease["deployment_git_sha"]
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
                "runtime release identity, critical files, or frontend build",
            ):
                guard.prepublish()

            self.assertTrue(lease_dir.exists())

    def test_abort_requires_exact_sha_and_release_id(self):
        lease = self._lease_payload()
        other_release_id = "rg5-" + "b" * 64
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
                    guard.abort("c" * 40, lease["release_id"])
                self.assertTrue(lease_dir.exists())

                with self.assertRaisesRegex(
                    guard.ReleaseGuardError, "lease release id mismatch"
                ):
                    guard.abort(lease["deployment_git_sha"], other_release_id)
                self.assertTrue(lease_dir.exists())

                with self.assertRaisesRegex(
                    guard.ReleaseGuardError, "expected release id is invalid"
                ):
                    guard.abort(lease["deployment_git_sha"], "not-a-release-id")
                self.assertTrue(lease_dir.exists())

                result = guard.abort(
                    lease["deployment_git_sha"], lease["release_id"]
                )

            self.assertEqual(
                result,
                {
                    "aborted": True,
                    "source_git_sha": lease["source_git_sha"],
                    "git_sha": lease["git_sha"],
                    "deployment_git_sha": lease["deployment_git_sha"],
                    "release_id": lease["release_id"],
                    "protocol_version": guard.PROTOCOL_VERSION,
                },
            )
            self.assertFalse(lease_dir.exists())

    def test_abort_refuses_v4_lease_and_requires_its_original_guard(self):
        lease = {**self._lease_payload(), "protocol_version": 4}
        with tempfile.TemporaryDirectory() as tmp:
            lease_dir = Path(tmp) / "release.lock"
            lease_dir.mkdir()
            lease_path = lease_dir / "lease.json"
            lease_path.write_text(json.dumps(lease), encoding="utf-8")
            with patch.object(guard, "LEASE_DIR", lease_dir), patch.object(
                guard, "LEASE_PATH", lease_path
            ), self.assertRaisesRegex(
                guard.ReleaseGuardError,
                "abort the existing lease with its original guard.*protocol v5",
            ):
                guard.abort(lease["deployment_git_sha"], lease["release_id"])

            self.assertTrue(lease_dir.exists())

    def test_abort_does_not_delete_replacement_lease_before_removal(self):
        lease = self._lease_payload()
        replacement = {
            **self._lease_with_release_id(lease, "rg5-" + "b" * 64),
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
                guard.abort(lease["deployment_git_sha"], lease["release_id"])

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
