from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import frontend_build_identity as identity


class FrontendBuildIdentityTests(unittest.TestCase):
    @staticmethod
    def _fixture(root: Path, git_sha: str = "a" * 40) -> tuple[Path, Path]:
        frontend_root = root / "frontend"
        build_root = frontend_root / "build"
        assets = build_root / "assets"
        assets.mkdir(parents=True)
        (frontend_root / ".nvmrc").write_text("22\n", encoding="utf-8")
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
        (build_root / "_headers").write_text("/*\n", encoding="utf-8")
        (build_root / "index.html").write_text(
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
        metadata = {
            "schema_version": identity.SCHEMA_VERSION,
            "git_sha": git_sha,
            "source": {
                relative: identity._sha256(frontend_root / relative)
                for relative in identity.SOURCE_FILES
            },
            "toolchain": {
                "node": "22.19.0",
                "yarn": identity.EXPECTED_YARN_VERSION,
                "vite": "8.2.1",
            },
            "build": {
                "mode": "production",
                "output_dir": "frontend/build",
            },
            "index": records_by_path["index.html"],
            "entrypoints": entrypoints,
            "assets": assets_proof,
            "files": files,
            "artifact_tree_sha256": identity._tree_sha256(files),
        }
        (build_root / identity.META_NAME).write_text(
            json.dumps(metadata, indent=2) + "\n", encoding="utf-8"
        )
        return frontend_root, build_root

    def test_valid_identity_includes_index_entrypoints_and_dynamic_assets(self):
        with tempfile.TemporaryDirectory() as tmp:
            frontend_root, build_root = self._fixture(Path(tmp))
            result = identity.read_frontend_build_identity(
                frontend_root=frontend_root,
                build_root=build_root,
                expected_git_sha="a" * 40,
            )

        self.assertEqual(result["toolchain"]["node"], "22.19.0")
        self.assertEqual(result["index"]["path"], "index.html")
        self.assertEqual(
            [row["path"] for row in result["entrypoints"]],
            ["assets/app.js", "assets/style.css"],
        )
        self.assertEqual(
            [row["path"] for row in result["assets"]],
            ["assets/app.js", "assets/lazy.js", "assets/style.css"],
        )

    def test_dynamic_asset_byte_drift_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            frontend_root, build_root = self._fixture(Path(tmp))
            (build_root / "assets" / "lazy.js").write_text(
                "changed\n", encoding="utf-8"
            )
            with self.assertRaisesRegex(
                identity.FrontendBuildIdentityError,
                "metadata does not match",
            ):
                identity.read_frontend_build_identity(
                    frontend_root=frontend_root,
                    build_root=build_root,
                    expected_git_sha="a" * 40,
                )

    def test_stale_git_sha_and_missing_lock_fail_closed(self):
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
            (frontend_root / "yarn.lock").unlink()
            with self.assertRaisesRegex(
                identity.FrontendBuildIdentityError,
                "source proof is missing",
            ):
                identity.read_frontend_build_identity(
                    frontend_root=frontend_root,
                    build_root=build_root,
                    expected_git_sha="a" * 40,
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
