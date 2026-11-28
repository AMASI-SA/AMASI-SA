"""Operator-only V5 handoff safety checks. All filesystem tests use temp roots."""
from __future__ import annotations
import contextlib
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ops import mezan_whatsapp_oneway_v5_promote_verified_artifacts as h


class HandoffContracts(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.app = self.root / "app"
        self.clone = self.root / "clone"
        self.backups = self.root / "backups"
        (self.app / ".git").mkdir(parents=True)
        for root, text in ((self.app, "old"), (self.clone, "new")):
            (root / "frontend" / "build").mkdir(parents=True)
            (root / "frontend" / ".release").mkdir(parents=True)
            (root / "backend").mkdir(parents=True)
            (root / "frontend" / "build" / "index.html").write_text(text)
            (root / "frontend" / ".release" / "reproducible-build.json").write_text(text)
            (root / "backend" / "release_identity.json").write_text(text)

    def custom_environment(self, mode):
        return contextlib.ExitStack()

    def run_mode(self, mode, *, proof_failure=False):
        fake_untracked = b"f\0" * 433
        def fake_git(*args, **kwargs):
            if args == ("rev-parse", "HEAD"):
                return h.DEPLOY_SHA
            if args == ("status", "--porcelain", "--untracked-files=no"):
                return ""
            self.fail("unexpected git invocation: " + repr(args))

        log = io.StringIO()
        with patch.object(h, "APP", self.app), \
             patch.object(h, "REHEARSAL", self.clone), \
             patch.object(h, "RECOVERY_ROOT", self.backups), \
             patch.object(h, "ensure_app_and_clone"), \
             patch.object(h, "verify_isolated"), \
             patch.object(h, "git", side_effect=fake_git), \
             patch.object(h, "git_raw", return_value=fake_untracked), \
             patch.object(h, "verify_local_proof",
                          side_effect=RuntimeError("injected_proof_failure")
                          if proof_failure else None), \
             patch.object(h.sys, "argv", ["handoff.py", mode]), \
             contextlib.redirect_stdout(log):
            if proof_failure:
                with self.assertRaises(SystemExit) as e:
                    h.main()
                self.assertEqual(e.exception.code, 2)
            else:
                h.main()
        return log.getvalue()

    def test_fixed_source_identity_and_unmodified_main_flags(self):
        self.assertEqual(h.DEPLOY_SHA, "b020f3e918915a5873366ddcaab1db0244ef8e26")
        self.assertEqual(h.INTENT_SHA, "ac5b459ced939e499b963f5296457de168019323")
        self.assertEqual(h.SOURCE_SHA, "55995c02c2bdcf4ec63fe37ea3c14983244952c5")
        self.assertEqual(h.BASE_SHA, "53e52767a1bf2b4efd4ec796e8c59de9425c8839")
        self.assertEqual(h.RECORDS, (
            ("frontend/build", "directory"),
            ("frontend/.release", "directory"),
            ("backend/release_identity.json", "file"),
        ))

    def test_single_file_hash_independent_of_temporary_name(self):
        a = self.root / "alpha"
        b = self.root / "different-name"
        a.write_bytes(b"same identity")
        b.write_bytes(b"same identity")
        self.assertEqual(h.tree_hash(a), h.tree_hash(b))
        b.write_bytes(b"changed")
        self.assertNotEqual(h.tree_hash(a), h.tree_hash(b))

    def test_tree_hash_refuses_symlinks(self):
        (self.app / "frontend" / "build" / "shortcut").symlink_to("index.html")
        with self.assertRaisesRegex(RuntimeError, "symlink"):
            h.tree_hash(self.app / "frontend" / "build")

    def test_precheck_does_not_replace_any_artifact(self):
        text = self.run_mode("check")
        self.assertIn("V5_ONEWAY_HANDOFF_PRECHECK_PASS", text)
        self.assertEqual((self.app / "frontend" / "build" / "index.html").read_text(), "old")
        self.assertEqual((self.app / "backend" / "release_identity.json").read_text(), "old")
        self.assertFalse(self.backups.exists())

    def test_prior_orphan_not_automatically_deleted(self):
        orphan = self.app / "backend" / ".release_identity.json.v5-new-92c617c56b20"
        orphan.write_text("stale data; do not delete")
        with patch.object(h, "APP", self.app), \
             patch.object(h, "REHEARSAL", self.clone), \
             patch.object(h, "ensure_app_and_clone"), \
             patch.object(h, "verify_isolated"), \
             patch.object(h.sys, "argv", ["handoff.py", "check"]):
            with self.assertRaisesRegex(RuntimeError, "legacy staging orphan"):
                h.main()
        self.assertEqual(orphan.read_text(), "stale data; do not delete")

    def test_promotion_retains_originals_in_verified_backup(self):
        output = self.run_mode("promote")
        self.assertIn("V5_ARTIFACT_HANDOFF_PASS", output)
        self.assertIn("NO_REPUBLISH_NO_RUNTIME_RESTART", output)
        self.assertEqual((self.app / "frontend/build/index.html").read_text(), "new")
        self.assertEqual((self.app / "frontend/.release/reproducible-build.json").read_text(), "new")
        self.assertEqual((self.app / "backend/release_identity.json").read_text(), "new")
        backup = next(self.backups.glob("before-*"))
        self.assertEqual((backup / "frontend/build/index.html").read_text(), "old")
        self.assertEqual((backup / "backend/release_identity.json").read_text(), "old")
        self.assertTrue((backup / "handoff.json").is_file())

    def test_failed_post_swap_validation_restores_previous_artifacts(self):
        self.run_mode("promote", proof_failure=True)
        for rel,kind in h.RECORDS:
            member = self.app / rel
            value = (member / "index.html") if rel == "frontend/build" else (
                member / "reproducible-build.json" if rel == "frontend/.release" else member
            )
            self.assertEqual(value.read_text(), "old")
        backup = next(self.backups.glob("before-*"))
        self.assertEqual((backup / "backend/release_identity.json").read_text(), "old")


if __name__ == "__main__":
    unittest.main()
