from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from release_identity import CRITICAL_FILES, read_release_identity


class ReleaseIdentityTests(unittest.TestCase):
    def test_valid_identity_is_public_and_exact(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "release_identity.json"
            path.write_text(json.dumps({
                "git_sha": "a" * 40,
                "branch": "hotfix/prod-snap-meta-final",
                "prepared_at": "2026-08-12T00:00:00+00:00",
                "protocol_version": 1,
                "actor": "must-not-be-exposed",
                "critical_file_hashes": {
                    relative: __import__("hashlib").sha256(
                        (Path(__file__).resolve().parents[1] / relative).read_bytes()
                    ).hexdigest()
                    for relative in CRITICAL_FILES
                },
            }), encoding="utf-8")

            result = read_release_identity(path)

        self.assertTrue(result["verified_identity_available"])
        self.assertEqual(result["git_sha"], "a" * 40)
        self.assertNotIn("actor", result)
        self.assertTrue(result["critical_file_hashes_match"])

    def test_missing_or_invalid_identity_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "missing.json"
            missing = read_release_identity(path)
            path.write_text('{"git_sha":"short"}', encoding="utf-8")
            invalid = read_release_identity(path)

        self.assertFalse(missing["verified_identity_available"])
        self.assertIsNone(missing["git_sha"])
        self.assertFalse(invalid["verified_identity_available"])


if __name__ == "__main__":
    unittest.main()
