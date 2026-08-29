from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path

import frontend_build_identity as identity


class FrontendBuildIdentityTests(unittest.TestCase):
    @staticmethod
    def _source_proof(frontend_root: Path) -> dict:
        records = []
        for path in sorted(
            (candidate for candidate in frontend_root.iterdir() if candidate.is_file()),
            key=lambda candidate: candidate.name,
        ):
            content = path.read_bytes()
            records.append({
                "path": path.name,
                "mode": "100644",
                "git_blob": identity._git_blob_oid(content),
                "bytes": len(content),
                "sha256": identity._sha256_bytes(content),
            })
        return {
            "scope": "git_head_frontend_tree_v1",
            "git_tree_oid": "f" * 40,
            "file_count": len(records),
            "files": records,
            "tree_sha256": identity._canonical_source_tree_sha256(records),
        }

    @staticmethod
    def _fixture(root: Path, git_sha: str = "a" * 40) -> tuple[Path, Path]:
        frontend_root = root / "frontend"
        build_root = frontend_root / "build"
        assets = build_root / "assets"
        well_known = build_root / ".well-known"
        assets.mkdir(parents=True)
        well_known.mkdir()
        (frontend_root / ".nvmrc").write_text("22.23.2\n", encoding="utf-8")
        (frontend_root / "package.json").write_text(
            json.dumps({"dependencies": {"vite": "8.2.1"}}) + "\n",
            encoding="utf-8",
        )
        (frontend_root / "vite.config.js").write_text(
            "export default {};\n", encoding="utf-8"
        )
        (frontend_root / "yarn.lock").write_text(
            "# yarn lockfile v1\n", encoding="utf-8"
        )
        (assets / "app.js").write_text("console.log('app');\n", encoding="utf-8")
        (assets / "lazy.js").write_text("console.log('lazy');\n", encoding="utf-8")
        (assets / "style.css").write_text("body{}\n", encoding="utf-8")
        (build_root / "sw.js").write_text("self.skipWaiting();\n", encoding="utf-8")
        (build_root / "favicon.svg").write_text("<svg/>\n", encoding="utf-8")
        (build_root / "manifest.webmanifest").write_text("{}\n", encoding="utf-8")
        (well_known / "security.txt").write_text("Contact: /\n", encoding="utf-8")
        (build_root / "_headers").write_text("/*\n", encoding="utf-8")
        (build_root / "_headers.json").write_text("{}\n", encoding="utf-8")
        (build_root / "index.html").write_text(
            '<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Cairo">'
            '<script type="module" src="/assets/app.js"></script>'
            '<link rel="stylesheet" href="/assets/style.css">\n',
            encoding="utf-8",
        )

        files = identity._build_records(build_root)
        records_by_path = {row["path"]: row for row in files}
        entrypoints = identity._entrypoints(
            build_root / "index.html", records_by_path
        )
        assets_proof = [
            row for row in files if row["path"].startswith("assets/")
        ]
        public_files = [
            row
            for row in files
            if row["path"] not in identity.NON_PUBLIC_BUILD_FILES
        ]
        metadata = {
            "schema_version": identity.SCHEMA_VERSION,
            "git_sha": git_sha,
            "source": FrontendBuildIdentityTests._source_proof(frontend_root),
            "toolchain": {
                "node": identity.EXPECTED_NODE_VERSION,
                "yarn": identity.EXPECTED_YARN_VERSION,
                "vite": "8.2.1",
            },
            "environment": {
                "mode": "production",
                "allowed_client_keys": list(identity.CLIENT_ENV_ALLOWLIST),
                "values": {
                    "REACT_APP_BACKEND_URL": {
                        "present": False,
                        "sha256": None,
                    }
                },
            },
            "build": {
                "mode": "production",
                "output_dir": "frontend/build",
            },
            "index": records_by_path["index.html"],
            "entrypoints": entrypoints,
            "assets": assets_proof,
            "public_files": public_files,
            "files": files,
            "artifact_tree_sha256": identity._canonical_build_tree_sha256(files),
        }
        (build_root / identity.META_NAME).write_text(
            json.dumps(metadata, indent=2) + "\n", encoding="utf-8"
        )
        return frontend_root, build_root

    def test_valid_identity_is_compact_and_covers_every_public_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            frontend_root, build_root = self._fixture(Path(tmp))
            result = identity.read_frontend_build_identity(
                frontend_root=frontend_root,
                build_root=build_root,
                expected_git_sha="a" * 40,
            )

        self.assertEqual(result["toolchain"]["node"], "22.23.2")
        self.assertNotIn("files", result["source"])
        self.assertEqual(
            [row["path"] for row in result["entrypoints"]],
            ["assets/app.js", "assets/style.css"],
        )
        public_paths = [row["path"] for row in result["public_files"]]
        self.assertIn("assets/lazy.js", public_paths)
        self.assertIn("sw.js", public_paths)
        self.assertIn("favicon.svg", public_paths)
        self.assertIn("manifest.webmanifest", public_paths)
        self.assertIn(".well-known/security.txt", public_paths)
        self.assertNotIn("_headers", public_paths)
        self.assertNotIn("_headers.json", public_paths)
        self.assertEqual(result["build_meta"]["path"], "build-meta.json")

    def test_dynamic_asset_and_service_worker_drift_fail_closed(self):
        for relative in ("assets/lazy.js", "sw.js"):
            with self.subTest(relative=relative), tempfile.TemporaryDirectory() as tmp:
                frontend_root, build_root = self._fixture(Path(tmp))
                (build_root / relative).write_text("changed\n", encoding="utf-8")
                with self.assertRaisesRegex(
                    identity.FrontendBuildIdentityError,
                    "metadata does not match",
                ):
                    identity.read_frontend_build_identity(
                        frontend_root=frontend_root,
                        build_root=build_root,
                        expected_git_sha="a" * 40,
                    )

    def test_external_stylesheet_is_ignored_but_external_script_is_rejected(self):
        self.assertIsNone(
            identity._normalize_entrypoint(
                "https://fonts.googleapis.com/css2?family=Cairo",
                allow_external=True,
            )
        )
        with self.assertRaisesRegex(
            identity.FrontendBuildIdentityError,
            "script entrypoint must be same-origin",
        ):
            identity._normalize_entrypoint(
                "https://cdn.example.test/app.js",
                allow_external=False,
            )

    def test_stale_git_sha_and_invalid_source_or_environment_fail_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            frontend_root, build_root = self._fixture(Path(tmp))
            with self.assertRaisesRegex(
                identity.FrontendBuildIdentityError,
                "frontend build is stale",
            ):
                identity.read_frontend_build_identity(
                    frontend_root=frontend_root,
                    build_root=build_root,
                    expected_git_sha="b" * 40,
                )
            meta_path = build_root / identity.META_NAME
            metadata = json.loads(meta_path.read_text(encoding="utf-8"))
            metadata["source"]["files"].pop()
            meta_path.write_text(json.dumps(metadata), encoding="utf-8")
            with self.assertRaisesRegex(
                identity.FrontendBuildIdentityError,
                "tracked source proof is invalid",
            ):
                identity.read_frontend_build_identity(
                    frontend_root=frontend_root,
                    build_root=build_root,
                    expected_git_sha="a" * 40,
                )

        with tempfile.TemporaryDirectory() as tmp:
            frontend_root, build_root = self._fixture(Path(tmp))
            meta_path = build_root / identity.META_NAME
            metadata = json.loads(meta_path.read_text(encoding="utf-8"))
            metadata["environment"]["values"]["DB_PASSWORD"] = {
                "present": True,
                "sha256": "1" * 64,
            }
            meta_path.write_text(json.dumps(metadata), encoding="utf-8")
            with self.assertRaisesRegex(
                identity.FrontendBuildIdentityError,
                "governed environment proof is invalid",
            ):
                identity.read_frontend_build_identity(
                    frontend_root=frontend_root,
                    build_root=build_root,
                    expected_git_sha="a" * 40,
                )

    def test_head_tree_comparison_defeats_assume_unchanged_and_untracked(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo_root = Path(tmp) / "repo"
            frontend_root = repo_root / "frontend"
            frontend_root.mkdir(parents=True)
            source = frontend_root / "app.js"
            source.write_text("original\n", encoding="utf-8")
            subprocess.run(["git", "init", "-q", str(repo_root)], check=True)
            subprocess.run(
                ["git", "-C", str(repo_root), "config", "user.email", "ci@example.test"],
                check=True,
            )
            subprocess.run(
                ["git", "-C", str(repo_root), "config", "user.name", "CI"],
                check=True,
            )
            subprocess.run(
                ["git", "-C", str(repo_root), "add", "frontend/app.js"],
                check=True,
            )
            subprocess.run(
                ["git", "-C", str(repo_root), "commit", "-qm", "fixture"],
                check=True,
            )
            clean = identity._tracked_frontend_source(
                repo_root=repo_root, frontend_root=frontend_root
            )
            self.assertEqual(clean["file_count"], 1)

            subprocess.run(
                ["git", "-C", str(repo_root), "update-index", "--assume-unchanged", "frontend/app.js"],
                check=True,
            )
            source.write_text("drifted\n", encoding="utf-8")
            with self.assertRaisesRegex(
                identity.FrontendBuildIdentityError,
                "differs from Git HEAD",
            ):
                identity._tracked_frontend_source(
                    repo_root=repo_root, frontend_root=frontend_root
                )

            subprocess.run(
                ["git", "-C", str(repo_root), "update-index", "--no-assume-unchanged", "frontend/app.js"],
                check=True,
            )
            source.write_text("original\n", encoding="utf-8")
            (frontend_root / "new.js").write_text("untracked\n", encoding="utf-8")
            with self.assertRaisesRegex(
                identity.FrontendBuildIdentityError,
                "frontend source is dirty",
            ):
                identity._tracked_frontend_source(
                    repo_root=repo_root, frontend_root=frontend_root
                )

    def test_metadata_is_byte_deterministic_without_timestamp(self):
        with tempfile.TemporaryDirectory() as first, tempfile.TemporaryDirectory() as second:
            _, first_build = self._fixture(Path(first))
            _, second_build = self._fixture(Path(second))
            first_meta = (first_build / identity.META_NAME).read_bytes()
            second_meta = (second_build / identity.META_NAME).read_bytes()

        self.assertEqual(first_meta, second_meta)
        self.assertNotIn(b"timestamp", first_meta)


if __name__ == "__main__":
    unittest.main()
