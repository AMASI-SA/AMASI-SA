from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from release_identity import (
    CRITICAL_FILES,
    RELEASE_PROTOCOL_VERSION,
    read_release_identity,
)


class ReleaseIdentityTests(unittest.TestCase):
    @staticmethod
    def _frontend_build():
        return {
            "schema_version": 1,
            "git_sha": "a" * 40,
            "artifact_tree_sha256": "f" * 64,
        }

    def test_valid_identity_is_public_and_exact(self):
        frontend_build = self._frontend_build()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "release_identity.json"
            path.write_text(json.dumps({
                "release_id": "9d7a9a23-1f46-44a8-a0d0-851a71e15af6",
                "git_sha": "a" * 40,
                "branch": "hotfix/prod-snap-meta-final",
                "prepared_at": "2026-08-12T00:00:00+00:00",
                "protocol_version": RELEASE_PROTOCOL_VERSION,
                "actor": "must-not-be-exposed",
                "critical_file_hashes": {
                    relative: __import__("hashlib").sha256(
                        (Path(__file__).resolve().parents[1] / relative).read_bytes()
                    ).hexdigest()
                    for relative in CRITICAL_FILES
                },
                "frontend_build": frontend_build,
            }), encoding="utf-8")

            with patch(
                "release_identity.read_frontend_build_identity",
                return_value=frontend_build,
            ):
                result = read_release_identity(path)

        self.assertTrue(result["verified_identity_available"])
        self.assertEqual(
            result["release_id"],
            "9d7a9a23-1f46-44a8-a0d0-851a71e15af6",
        )
        self.assertEqual(result["git_sha"], "a" * 40)
        self.assertEqual(result["protocol_version"], RELEASE_PROTOCOL_VERSION)
        self.assertNotIn("actor", result)
        self.assertTrue(result["critical_file_hashes_match"])
        self.assertTrue(result["frontend_build_verified"])
        self.assertEqual(result["frontend_build"], frontend_build)

    def test_frontend_identity_mismatch_is_exposed_fail_closed(self):
        frontend_build = self._frontend_build()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "release_identity.json"
            path.write_text(json.dumps({
                "release_id": "9d7a9a23-1f46-44a8-a0d0-851a71e15af6",
                "git_sha": "a" * 40,
                "branch": "hotfix/prod-snap-meta-final",
                "prepared_at": "2026-08-12T00:00:00+00:00",
                "protocol_version": RELEASE_PROTOCOL_VERSION,
                "critical_file_hashes": {
                    relative: __import__("hashlib").sha256(
                        (Path(__file__).resolve().parents[1] / relative).read_bytes()
                    ).hexdigest()
                    for relative in CRITICAL_FILES
                },
                "frontend_build": {**frontend_build, "git_sha": "b" * 40},
            }), encoding="utf-8")

            with patch(
                "release_identity.read_frontend_build_identity",
                return_value=frontend_build,
            ):
                result = read_release_identity(path)

        self.assertTrue(result["verified_identity_available"])
        self.assertFalse(result["frontend_build_verified"])
        self.assertEqual(result["frontend_build"], frontend_build)

    def test_missing_or_invalid_identity_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "missing.json"
            missing = read_release_identity(path)
            path.write_text('{"git_sha":"short"}', encoding="utf-8")
            invalid = read_release_identity(path)
            path.write_text(json.dumps({
                "release_id": "not-a-uuid",
                "git_sha": "a" * 40,
                "protocol_version": RELEASE_PROTOCOL_VERSION,
            }), encoding="utf-8")
            invalid_release_id = read_release_identity(path)

        self.assertFalse(missing["verified_identity_available"])
        self.assertIsNone(missing["git_sha"])
        self.assertFalse(invalid["verified_identity_available"])
        self.assertFalse(invalid_release_id["verified_identity_available"])
        self.assertIsNone(invalid_release_id["release_id"])

    def test_legacy_identity_is_rejected_after_v3_protocol_upgrade(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "legacy-v1-release-identity.json"
            path.write_text(
                json.dumps(
                    {
                        "release_id": "9d7a9a23-1f46-44a8-a0d0-851a71e15af6",
                        "git_sha": "a" * 40,
                        "branch": "hotfix/prod-snap-meta-final",
                        "prepared_at": "2026-08-12T00:00:00+00:00",
                        "protocol_version": 1,
                        "critical_file_hashes": {},
                    }
                ),
                encoding="utf-8",
            )

            result = read_release_identity(path)

        self.assertEqual(RELEASE_PROTOCOL_VERSION, 3)
        self.assertFalse(result["verified_identity_available"])
        self.assertIsNone(result["release_id"])
        self.assertEqual(result["protocol_version"], RELEASE_PROTOCOL_VERSION)


if __name__ == "__main__":
    unittest.main()
