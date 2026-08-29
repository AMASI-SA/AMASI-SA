from __future__ import annotations

import dataclasses
import hashlib
import json
import os
import shlex
import shutil
import subprocess
import tarfile
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from scripts import frontend_release_toolchain as toolchain


class _FixtureRunner:
    """Model the pinned Node/Corepack/Yarn commands without network access."""

    def __init__(
        self,
        *,
        node_version: str = toolchain.NODE_VERSION,
        yarn_version: str = toolchain.YARN_VERSION,
    ) -> None:
        self.node_version = node_version
        self.yarn_version = yarn_version
        self.calls: list[tuple[list[str], dict]] = []

    def __call__(self, argv, **kwargs):
        command = [os.fspath(part) for part in argv]
        self.calls.append((command, dict(kwargs)))
        executable = Path(command[0]).name

        if len(command) == 2 and command[-1] == "--version" and executable == "node":
            return self._completed(command, self.node_version)
        if executable == "node" and len(command) >= 3:
            if command[-1] == "--version":
                return self._completed(command, "0.34.6")
            if "enable" in command:
                install_dir = Path(command[command.index("--install-directory") + 1])
                yarn = install_dir / "yarn"
                if yarn.exists() or yarn.is_symlink():
                    yarn.unlink()
                os.symlink(
                    "../lib/node_modules/corepack/dist/yarn.js",
                    yarn,
                )
                return self._completed(command)
            if "prepare" in command:
                corepack_home = Path(kwargs["env"]["COREPACK_HOME"])
                package_root = corepack_home / "v1" / "yarn" / "1.22.22"
                (package_root / "bin").mkdir(parents=True, exist_ok=True)
                (package_root / "bin" / "yarn.js").write_text(
                    "// pinned yarn payload\n", encoding="utf-8"
                )
                (package_root / ".corepack").write_text(
                    json.dumps(
                        {
                            "locator": {
                                "name": "yarn",
                                "reference": (
                                    f"{toolchain.YARN_VERSION}+sha512."
                                    f"{toolchain.YARN_SHA512}"
                                ),
                            },
                            "bin": {"yarn": "./bin/yarn.js"},
                            "hash": f"sha512.{toolchain.YARN_SHA512}",
                        },
                        separators=(",", ":"),
                    ),
                    encoding="utf-8",
                )
                return self._completed(command)
        if command[-1:] == ["--version"] and executable == "yarn":
            return self._completed(command, self.yarn_version)
        return self._completed(command)

    @staticmethod
    def _completed(command: list[str], stdout: str = ""):
        return SimpleNamespace(
            args=command,
            returncode=0,
            stdout=f"{stdout}\n" if stdout else "",
            stderr="",
        )


class FrontendReleaseToolchainTests(unittest.TestCase):
    @staticmethod
    def _archive_fixture(
        root: Path,
        *,
        include_node: bool = True,
        include_corepack: bool = True,
    ) -> tuple[toolchain.ArchiveSpec, bytes]:
        base = toolchain.select_archive("Linux", "x86_64")
        source = root / "source" / base.extracted_root
        (source / "bin").mkdir(parents=True)
        if include_node:
            node = source / "bin" / "node"
            node.write_text(
                "#!/bin/sh\nprintf '%s\\n' 'v22.23.2'\n",
                encoding="utf-8",
            )
            node.chmod(0o755)
        if include_corepack:
            corepack = (
                source
                / "lib"
                / "node_modules"
                / "corepack"
                / "dist"
                / "corepack.js"
            )
            corepack.parent.mkdir(parents=True)
            corepack.write_text(
                "#!/bin/sh\n"
                "case \"$(basename \"$0\")\" in\n"
                "  yarn) printf '%s\\n' '1.22.22' ;;\n"
                "  *) printf '%s\\n' '0.34.6' ;;\n"
                "esac\n",
                encoding="utf-8",
            )
            corepack.chmod(0o755)
            yarn_launcher = corepack.parent / "yarn.js"
            yarn_launcher.write_text(
                "#!/bin/sh\nprintf '%s\\n' '1.22.22'\n",
                encoding="utf-8",
            )
            yarn_launcher.chmod(0o755)
            corepack_implementation = corepack.parent / "lib" / "corepack.cjs"
            corepack_implementation.parent.mkdir(parents=True)
            corepack_implementation.write_text(
                "module.exports = { fixture: true };\n",
                encoding="utf-8",
            )
        archive = root / base.filename
        with tarfile.open(archive, "w:xz") as bundle:
            bundle.add(source, arcname=base.extracted_root)
        payload = archive.read_bytes()
        spec = dataclasses.replace(
            base,
            sha256=hashlib.sha256(payload).hexdigest(),
        )
        return spec, payload

    @staticmethod
    def _downloader(payload: bytes, calls: list[str] | None = None):
        def download(url: str, destination: Path) -> None:
            if calls is not None:
                calls.append(url)
            destination.write_bytes(payload)

        return download

    def test_select_archive_x86_64_uses_pinned_linux_x64(self):
        spec = toolchain.select_archive("Linux", "x86_64")

        self.assertEqual(spec.platform_tag, "linux-x64")
        self.assertEqual(spec.filename, "node-v22.23.2-linux-x64.tar.xz")
        self.assertEqual(
            spec.url,
            "https://nodejs.org/dist/v22.23.2/"
            "node-v22.23.2-linux-x64.tar.xz",
        )
        self.assertEqual(
            spec.sha256,
            "d60acfe00a2932254bb0ad20e01b0d74397a0875595de719654b214f4b03f307",
        )

    def test_select_archive_aarch64_uses_pinned_linux_arm64(self):
        spec = toolchain.select_archive("Linux", "aarch64")

        self.assertEqual(spec.platform_tag, "linux-arm64")
        self.assertEqual(spec.filename, "node-v22.23.2-linux-arm64.tar.xz")
        self.assertEqual(
            spec.url,
            "https://nodejs.org/dist/v22.23.2/"
            "node-v22.23.2-linux-arm64.tar.xz",
        )
        self.assertEqual(
            spec.sha256,
            "fff4078c5def658577f92c88db7db3bc0072924bfb93fe52c1e744a54e94abb8",
        )

    def test_select_archive_rejects_every_other_system_or_machine(self):
        for system_name, machine in (
            ("Darwin", "x86_64"),
            ("Windows", "AMD64"),
            ("Linux", "amd64"),
            ("Linux", "armv7l"),
            ("Linux", "riscv64"),
        ):
            with self.subTest(system=system_name, machine=machine):
                with self.assertRaises(toolchain.ToolchainError):
                    toolchain.select_archive(system_name, machine)

    def test_checksum_mismatch_prevents_extract_and_any_subprocess(self):
        spec = toolchain.select_archive("Linux", "x86_64")
        runner = Mock()

        with tempfile.TemporaryDirectory() as tmp:
            cache_root = Path(tmp) / "cache"
            cache_root.mkdir()
            final_dir = cache_root / "toolchain"
            with patch.object(toolchain, "_safe_extract") as extractor:
                with self.assertRaisesRegex(
                    toolchain.ToolchainError,
                    "checksum mismatch",
                ):
                    toolchain._install_toolchain(
                        spec,
                        cache_root,
                        final_dir,
                        self._downloader(b"not-the-pinned-archive"),
                        runner,
                    )

            extractor.assert_not_called()
            runner.assert_not_called()
            self.assertFalse(final_dir.exists())
            self.assertEqual(list(cache_root.iterdir()), [])

    def test_corrupt_or_incomplete_archive_is_never_published(self):
        base = toolchain.select_archive("Linux", "x86_64")
        payload = b"this is not an xz archive"
        spec = dataclasses.replace(
            base,
            sha256=hashlib.sha256(payload).hexdigest(),
        )

        with tempfile.TemporaryDirectory() as tmp:
            cache_root = Path(tmp) / "cache"
            cache_root.mkdir()
            final_dir = cache_root / "toolchain"
            runner = Mock()
            with self.assertRaisesRegex(
                toolchain.ToolchainError,
                "incomplete or invalid",
            ):
                toolchain._install_toolchain(
                    spec,
                    cache_root,
                    final_dir,
                    self._downloader(payload),
                    runner,
                )

            runner.assert_not_called()
            self.assertFalse(final_dir.exists())
            self.assertEqual(list(cache_root.iterdir()), [])

    def test_verified_archive_missing_node_is_rejected_before_execution(self):
        with tempfile.TemporaryDirectory() as tmp:
            fixture_root = Path(tmp)
            spec, payload = self._archive_fixture(
                fixture_root,
                include_node=False,
            )
            cache_root = fixture_root / "cache"
            cache_root.mkdir()
            final_dir = cache_root / "toolchain"
            runner = Mock()

            with self.assertRaisesRegex(
                toolchain.ToolchainError,
                r"missing .*bin/node",
            ):
                toolchain._install_toolchain(
                    spec,
                    cache_root,
                    final_dir,
                    self._downloader(payload),
                    runner,
                )

            runner.assert_not_called()
            self.assertFalse(final_dir.exists())

    def test_install_is_atomic_and_validation_failure_cleans_stage(self):
        with tempfile.TemporaryDirectory() as tmp:
            fixture_root = Path(tmp)
            spec, payload = self._archive_fixture(fixture_root)
            cache_root = fixture_root / "cache"
            cache_root.mkdir()
            final_dir = cache_root / "toolchain"
            runner = _FixtureRunner(node_version="v20.20.2")
            replace_targets: list[Path] = []
            real_replace = os.replace

            def replace_spy(source, destination):
                replace_targets.append(Path(destination))
                return real_replace(source, destination)

            with patch.object(
                toolchain.os,
                "replace",
                side_effect=replace_spy,
            ):
                with self.assertRaisesRegex(
                    toolchain.ToolchainError,
                    "installed Node version mismatch",
                ):
                    toolchain._install_toolchain(
                        spec,
                        cache_root,
                        final_dir,
                        self._downloader(payload),
                        runner,
                    )

            self.assertNotIn(final_dir, replace_targets)
            self.assertFalse(final_dir.exists())
            self.assertEqual(list(cache_root.iterdir()), [])

    def test_successful_install_publishes_only_after_exact_versions(self):
        with tempfile.TemporaryDirectory() as tmp:
            fixture_root = Path(tmp)
            spec, payload = self._archive_fixture(fixture_root)
            cache_root = fixture_root / "cache"
            cache_root.mkdir()
            final_dir = cache_root / "toolchain"
            runner = _FixtureRunner()
            events: list[str] = []
            real_replace = os.replace

            def runner_spy(argv, **kwargs):
                command = [os.fspath(part) for part in argv]
                if Path(command[0]).name == "yarn" and command[-1] == "--version":
                    events.append("yarn-version-verified")
                return runner(command, **kwargs)

            def replace_spy(source, destination):
                if Path(destination) == final_dir:
                    events.append("final-publish")
                return real_replace(source, destination)

            with patch.object(
                toolchain.os,
                "replace",
                side_effect=replace_spy,
            ):
                toolchain._install_toolchain(
                    spec,
                    cache_root,
                    final_dir,
                    self._downloader(payload),
                    runner_spy,
                )

            self.assertTrue(final_dir.is_dir())
            metadata = json.loads(
                (final_dir / "metadata.json").read_text(encoding="utf-8")
            )
            self.assertEqual(metadata["corepack_version"], "0.34.6")
            self.assertEqual(
                events,
                ["yarn-version-verified", "final-publish"],
            )
            corepack_calls = [
                command
                for command, _kwargs in runner.calls
                if len(command) > 1 and command[1].endswith("/corepack.js")
            ]
            self.assertEqual(len(corepack_calls), 3)
            self.assertTrue(
                all(
                    command[0].startswith(os.fspath(cache_root))
                    and command[1].startswith(os.fspath(cache_root))
                    for command in corepack_calls
                )
            )

    def test_cold_then_warm_cache_downloads_only_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            fixture_root = Path(tmp)
            spec, payload = self._archive_fixture(fixture_root)
            cache_root = fixture_root / "cache"
            downloads: list[str] = []
            runner = _FixtureRunner()

            with patch.object(toolchain, "select_archive", return_value=spec):
                first = toolchain.ensure_toolchain(
                    cache_root=cache_root,
                    downloader=self._downloader(payload, downloads),
                    runner=runner,
                )
                second = toolchain.ensure_toolchain(
                    cache_root=cache_root,
                    downloader=self._downloader(payload, downloads),
                    runner=runner,
                )

            self.assertEqual(first["cache_state"], "cold_installed")
            self.assertEqual(second["cache_state"], "warm_verified")
            self.assertEqual(downloads, [spec.url])
            self.assertEqual(first["archive_sha256"], spec.sha256)

    def test_corrupt_retained_archive_is_not_executed_and_is_reinstalled(self):
        with tempfile.TemporaryDirectory() as tmp:
            fixture_root = Path(tmp)
            spec, payload = self._archive_fixture(fixture_root)
            cache_root = fixture_root / "cache"
            downloads: list[str] = []
            runner = _FixtureRunner()

            with patch.object(toolchain, "select_archive", return_value=spec):
                first = toolchain.ensure_toolchain(
                    cache_root=cache_root,
                    downloader=self._downloader(payload, downloads),
                    runner=runner,
                )
                final_dir = Path(first["toolchain_dir"])
                archive = final_dir / "source" / spec.filename
                archive.write_bytes(b"corrupt retained archive")
                runner.calls.clear()
                repaired = toolchain.ensure_toolchain(
                    cache_root=cache_root,
                    downloader=self._downloader(payload, downloads),
                    runner=runner,
                )

            self.assertEqual(repaired["cache_state"], "corrupt_reinstalled")
            self.assertEqual(downloads, [spec.url, spec.url])
            first_command = runner.calls[0][0]
            self.assertIn(".frontend-toolchain-", first_command[0])
            self.assertNotEqual(first_command[0], os.fspath(final_dir / "node/bin/node"))

    def test_corrupt_metadata_is_not_used_and_forces_reinstall(self):
        with tempfile.TemporaryDirectory() as tmp:
            fixture_root = Path(tmp)
            spec, payload = self._archive_fixture(fixture_root)
            cache_root = fixture_root / "cache"
            downloads: list[str] = []
            runner = _FixtureRunner()

            with patch.object(toolchain, "select_archive", return_value=spec):
                first = toolchain.ensure_toolchain(
                    cache_root=cache_root,
                    downloader=self._downloader(payload, downloads),
                    runner=runner,
                )
                final_dir = Path(first["toolchain_dir"])
                metadata_path = final_dir / "metadata.json"
                metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
                metadata["node_version"] = "v20.20.2"
                metadata_path.write_text(
                    json.dumps(metadata), encoding="utf-8"
                )
                runner.calls.clear()
                repaired = toolchain.ensure_toolchain(
                    cache_root=cache_root,
                    downloader=self._downloader(payload, downloads),
                    runner=runner,
                )

            self.assertEqual(repaired["cache_state"], "corrupt_reinstalled")
            self.assertEqual(downloads, [spec.url, spec.url])
            first_command = runner.calls[0][0]
            self.assertIn(".frontend-toolchain-", first_command[0])

    def test_intermediate_symlink_escape_is_rejected_before_execution(self):
        with tempfile.TemporaryDirectory() as tmp:
            fixture_root = Path(tmp)
            spec, payload = self._archive_fixture(fixture_root)
            cache_root = fixture_root / "cache"
            with patch.object(toolchain, "select_archive", return_value=spec):
                installed = toolchain.ensure_toolchain(
                    cache_root=cache_root,
                    downloader=self._downloader(payload),
                    runner=_FixtureRunner(),
                )

            final_dir = Path(installed["toolchain_dir"])
            escaped_node = fixture_root / "outside-node"
            shutil.copytree(final_dir / "node", escaped_node, symlinks=True)
            shutil.rmtree(final_dir / "node")
            os.symlink(escaped_node, final_dir / "node", target_is_directory=True)
            runner = Mock()

            with self.assertRaisesRegex(
                toolchain.ToolchainError,
                "not a real directory|escapes cache",
            ):
                toolchain._validate_toolchain(final_dir, spec, runner)

            runner.assert_not_called()

    def test_non_launcher_corepack_tamper_is_rejected_before_execution(self):
        with tempfile.TemporaryDirectory() as tmp:
            fixture_root = Path(tmp)
            spec, payload = self._archive_fixture(fixture_root)
            cache_root = fixture_root / "cache"
            with patch.object(toolchain, "select_archive", return_value=spec):
                installed = toolchain.ensure_toolchain(
                    cache_root=cache_root,
                    downloader=self._downloader(payload),
                    runner=_FixtureRunner(),
                )

            final_dir = Path(installed["toolchain_dir"])
            implementation = (
                final_dir
                / "node/lib/node_modules/corepack/dist/lib/corepack.cjs"
            )
            implementation.write_text(
                "module.exports = { tampered: true };\n",
                encoding="utf-8",
            )
            runner = Mock()

            with self.assertRaisesRegex(
                toolchain.ToolchainError,
                "Corepack tree does not match verified archive",
            ):
                toolchain._validate_toolchain(final_dir, spec, runner)

            runner.assert_not_called()

    def test_group_writable_cached_directory_is_rejected_before_execution(self):
        with tempfile.TemporaryDirectory() as tmp:
            fixture_root = Path(tmp)
            spec, payload = self._archive_fixture(fixture_root)
            cache_root = fixture_root / "cache"
            with patch.object(toolchain, "select_archive", return_value=spec):
                installed = toolchain.ensure_toolchain(
                    cache_root=cache_root,
                    downloader=self._downloader(payload),
                    runner=_FixtureRunner(),
                )

            final_dir = Path(installed["toolchain_dir"])
            final_dir.chmod(0o770)
            runner = Mock()

            with self.assertRaisesRegex(
                toolchain.ToolchainError,
                "must not be group/world writable",
            ):
                toolchain._validate_toolchain(final_dir, spec, runner)

            runner.assert_not_called()

    def test_world_writable_cached_file_is_rejected_before_execution(self):
        with tempfile.TemporaryDirectory() as tmp:
            fixture_root = Path(tmp)
            spec, payload = self._archive_fixture(fixture_root)
            cache_root = fixture_root / "cache"
            with patch.object(toolchain, "select_archive", return_value=spec):
                installed = toolchain.ensure_toolchain(
                    cache_root=cache_root,
                    downloader=self._downloader(payload),
                    runner=_FixtureRunner(),
                )

            final_dir = Path(installed["toolchain_dir"])
            (final_dir / "metadata.json").chmod(0o606)
            runner = Mock()

            with self.assertRaisesRegex(
                toolchain.ToolchainError,
                "ownership or permissions are unsafe",
            ):
                toolchain._validate_toolchain(final_dir, spec, runner)

            runner.assert_not_called()

    def test_yarn_corepack_marker_must_match_pinned_sha512(self):
        with tempfile.TemporaryDirectory() as tmp:
            fixture_root = Path(tmp)
            spec, payload = self._archive_fixture(fixture_root)
            cache_root = fixture_root / "cache"
            with patch.object(toolchain, "select_archive", return_value=spec):
                installed = toolchain.ensure_toolchain(
                    cache_root=cache_root,
                    downloader=self._downloader(payload),
                    runner=_FixtureRunner(),
                )

            final_dir = Path(installed["toolchain_dir"])
            marker = next((final_dir / "corepack-home").rglob(".corepack"))
            value = json.loads(marker.read_text(encoding="utf-8"))
            value["hash"] = "sha512." + ("0" * 128)
            marker.write_text(json.dumps(value), encoding="utf-8")
            runner = Mock()

            with self.assertRaisesRegex(
                toolchain.ToolchainError,
                "does not have the pinned SHA512",
            ):
                toolchain._validate_toolchain(final_dir, spec, runner)

            runner.assert_not_called()

    def test_cached_node_or_yarn_version_mismatch_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            fixture_root = Path(tmp)
            spec, payload = self._archive_fixture(fixture_root)
            cache_root = fixture_root / "cache"
            correct = _FixtureRunner()
            with patch.object(toolchain, "select_archive", return_value=spec):
                installed = toolchain.ensure_toolchain(
                    cache_root=cache_root,
                    downloader=self._downloader(payload),
                    runner=correct,
                )

            final_dir = Path(installed["toolchain_dir"])
            for node_version, yarn_version in (
                ("v20.20.2", toolchain.YARN_VERSION),
                (toolchain.NODE_VERSION, "4.0.0"),
            ):
                with self.subTest(
                    node_version=node_version,
                    yarn_version=yarn_version,
                ):
                    with self.assertRaises(toolchain.ToolchainError):
                        toolchain._validate_toolchain(
                            final_dir,
                            spec,
                            _FixtureRunner(
                                node_version=node_version,
                                yarn_version=yarn_version,
                            ),
                        )

    def test_execute_uses_local_env_without_mutating_parent_process(self):
        with tempfile.TemporaryDirectory() as tmp:
            fixture_root = Path(tmp)
            spec, payload = self._archive_fixture(fixture_root)
            cache_root = fixture_root / "cache"
            runner = _FixtureRunner()
            original_path = os.environ.get("PATH")

            with (
                patch.object(toolchain, "select_archive", return_value=spec),
                patch.dict(
                    os.environ,
                    {
                        "COREPACK_HOME": "/must/not/be/inherited",
                        "BASH_ENV": "/must/not/be/inherited",
                    },
                    clear=False,
                ),
            ):
                status = toolchain.execute(
                    ["python", "-c", "print('fixture')"],
                    cache_root=cache_root,
                    downloader=self._downloader(payload),
                    runner=runner,
                )

            self.assertEqual(status, 0)
            self.assertEqual(os.environ.get("PATH"), original_path)
            user_command, user_kwargs = runner.calls[-1]
            self.assertEqual(user_command[:2], ["python", "-c"])
            child_env = user_kwargs["env"]
            bin_dir = Path(child_env["PATH"].split(os.pathsep)[0])
            final_dir = bin_dir.parents[1]
            self.assertEqual(bin_dir, final_dir / "node/bin")
            self.assertEqual(
                child_env["COREPACK_HOME"],
                os.fspath(final_dir / "corepack-home"),
            )
            self.assertEqual(
                child_env["BASH_ENV"],
                os.fspath(final_dir / "exec-env.sh"),
            )

    def test_execute_sanitizes_hostile_process_and_shell_environment(self):
        hostile_environment = {
            "NODE_OPTIONS": "--require=/tmp/untrusted-node-hook.js",
            "NODE_PATH": "/tmp/untrusted-node-modules",
            "LD_PRELOAD": "/tmp/untrusted-loader.so",
            "LD_LIBRARY_PATH": "/tmp/untrusted-libraries",
            "ENV": "/tmp/untrusted-sh-env",
            "BASH_ENV": "/tmp/untrusted-bash-env",
            "BASH_FUNC_untrusted%%": "() { printf compromised; }",
            "COREPACK_HOME": "/tmp/untrusted-corepack",
            "COREPACK_ENABLE_NETWORK": "1",
            "COREPACK_NPM_REGISTRY": "https://untrusted.invalid",
            "COREPACK_INTEGRITY_KEYS": "0",
            "YARN_PATH": "/tmp/untrusted-yarn.js",
        }
        with tempfile.TemporaryDirectory() as tmp:
            fixture_root = Path(tmp)
            spec, payload = self._archive_fixture(fixture_root)
            cache_root = fixture_root / "cache"
            runner = _FixtureRunner()

            with (
                patch.object(toolchain, "select_archive", return_value=spec),
                patch.dict(os.environ, hostile_environment, clear=False),
            ):
                status = toolchain.execute(
                    ["python", "-c", "print('environment is governed')"],
                    cache_root=cache_root,
                    downloader=self._downloader(payload),
                    runner=runner,
                )

            self.assertEqual(status, 0)
            _command, kwargs = runner.calls[-1]
            child_env = kwargs["env"]
            for key in (
                "NODE_OPTIONS",
                "NODE_PATH",
                "LD_PRELOAD",
                "LD_LIBRARY_PATH",
                "ENV",
                "COREPACK_NPM_REGISTRY",
                "COREPACK_INTEGRITY_KEYS",
                "YARN_PATH",
            ):
                self.assertNotIn(key, child_env)
            self.assertFalse(
                any(key.startswith("BASH_FUNC_") for key in child_env)
            )
            self.assertEqual(child_env["COREPACK_ENABLE_NETWORK"], "0")
            self.assertEqual(child_env["COREPACK_DEFAULT_TO_LATEST"], "0")
            self.assertEqual(child_env["YARN_IGNORE_PATH"], "1")
            self.assertTrue(child_env["BASH_ENV"].startswith(os.fspath(cache_root)))

    def test_download_rejects_non_allowlisted_url_without_opening_network(self):
        spec = toolchain.select_archive("Linux", "x86_64")
        with tempfile.TemporaryDirectory() as tmp:
            destination = Path(tmp) / spec.filename
            with patch.object(
                toolchain.urllib.request,
                "build_opener",
            ) as build_opener:
                with self.assertRaisesRegex(
                    toolchain.ToolchainError,
                    "not the pinned official release URL",
                ):
                    toolchain._download_archive(
                        f"{spec.url}?unreviewed=1",
                        destination,
                    )

            build_opener.assert_not_called()
            self.assertFalse(destination.exists())

    def test_download_failure_is_normalized_and_partial_file_removed(self):
        spec = toolchain.select_archive("Linux", "x86_64")
        opener = Mock()
        opener.open.side_effect = OSError("simulated transport failure")
        with tempfile.TemporaryDirectory() as tmp:
            destination = Path(tmp) / spec.filename
            with patch.object(
                toolchain.urllib.request,
                "build_opener",
                return_value=opener,
            ):
                with self.assertRaisesRegex(
                    toolchain.ToolchainError,
                    "cannot download pinned Node archive",
                ):
                    toolchain._download_archive(spec.url, destination)

            self.assertFalse(destination.exists())

    def test_exec_version_mismatch_never_runs_user_command(self):
        with tempfile.TemporaryDirectory() as tmp:
            fixture_root = Path(tmp)
            spec, payload = self._archive_fixture(fixture_root)
            cache_root = fixture_root / "cache"
            good = _FixtureRunner()
            with patch.object(toolchain, "select_archive", return_value=spec):
                toolchain.ensure_toolchain(
                    cache_root=cache_root,
                    downloader=self._downloader(payload),
                    runner=good,
                )
                wrong = _FixtureRunner(node_version="v20.20.2")
                with self.assertRaises(toolchain.ToolchainError):
                    toolchain.execute(
                        ["python", "-c", "raise SystemExit('must not run')"],
                        cache_root=cache_root,
                        downloader=self._downloader(payload),
                        runner=wrong,
                    )

            self.assertFalse(
                any(
                    command[:2] == ["python", "-c"]
                    for command, _kwargs in wrong.calls
                )
            )

    def test_exec_preserves_local_tools_inside_bash_login_shell(self):
        with tempfile.TemporaryDirectory() as tmp:
            fixture_root = Path(tmp)
            spec, payload = self._archive_fixture(fixture_root)
            cache_root = fixture_root / "cache"
            with patch.object(toolchain, "select_archive", return_value=spec):
                toolchain.ensure_toolchain(
                    cache_root=cache_root,
                    downloader=self._downloader(payload),
                    runner=_FixtureRunner(),
                )
                output = fixture_root / "login-shell.txt"
                probe = (
                    "{ command -v node; node --version; command -v yarn; "
                    f"yarn --version; }} > {shlex.quote(os.fspath(output))}"
                )
                status = toolchain.execute(
                    ["bash", "-lc", probe],
                    cache_root=cache_root,
                )

            lines = output.read_text(encoding="utf-8").splitlines()
            self.assertEqual(status, 0)
            self.assertTrue(lines[0].startswith(os.fspath(cache_root)))
            self.assertEqual(lines[1], toolchain.NODE_VERSION)
            self.assertTrue(lines[2].startswith(os.fspath(cache_root)))
            self.assertEqual(lines[3], toolchain.YARN_VERSION)

    def test_default_cache_honors_xdg_and_remains_outside_repo(self):
        with tempfile.TemporaryDirectory() as tmp:
            fixture_root = Path(tmp)
            spec, payload = self._archive_fixture(fixture_root)
            xdg = fixture_root / "xdg-cache"
            with (
                patch.object(toolchain, "select_archive", return_value=spec),
                patch.dict(
                    os.environ,
                    {
                        "XDG_CACHE_HOME": os.fspath(xdg),
                        "HOME": os.fspath(fixture_root / "home"),
                    },
                    clear=False,
                ),
            ):
                result = toolchain.ensure_toolchain(
                    downloader=self._downloader(payload),
                    runner=_FixtureRunner(),
                )

            toolchain_dir = Path(result["toolchain_dir"])
            self.assertTrue(
                toolchain_dir.is_relative_to(
                    xdg / toolchain.CACHE_NAMESPACE
                )
            )
            self.assertFalse(toolchain_dir.is_relative_to(toolchain.REPO_ROOT))

    def test_default_cache_uses_account_home_when_environment_home_is_unset(self):
        with tempfile.TemporaryDirectory() as tmp:
            account_home = Path(tmp) / "account-home"
            environment = dict(os.environ)
            environment.pop("HOME", None)
            environment.pop("XDG_CACHE_HOME", None)
            with (
                patch.dict(os.environ, environment, clear=True),
                patch.object(toolchain.Path, "home", return_value=account_home),
            ):
                cache_root = toolchain._default_cache_root()

            self.assertEqual(
                cache_root,
                account_home / ".cache" / toolchain.CACHE_NAMESPACE,
            )

    def test_bootstrap_does_not_change_system_node_path_or_git_status(self):
        before_path = os.environ.get("PATH")
        before_node_path = shutil.which("node")
        before_node_identity = None
        if before_node_path:
            before_node_identity = (
                os.stat(before_node_path),
                toolchain._sha256_file(Path(before_node_path)),
            )
        before_node = subprocess.run(
            ["node", "--version"],
            check=False,
            capture_output=True,
            text=True,
        )
        before_git = subprocess.run(
            ["git", "status", "--short"],
            cwd=toolchain.REPO_ROOT,
            check=True,
            capture_output=True,
            text=True,
        ).stdout

        with tempfile.TemporaryDirectory() as tmp:
            fixture_root = Path(tmp)
            spec, payload = self._archive_fixture(fixture_root)
            with patch.object(toolchain, "select_archive", return_value=spec):
                toolchain.ensure_toolchain(
                    cache_root=fixture_root / "cache",
                    downloader=self._downloader(payload),
                    runner=_FixtureRunner(),
                )

        after_node = subprocess.run(
            ["node", "--version"],
            check=False,
            capture_output=True,
            text=True,
        )
        after_git = subprocess.run(
            ["git", "status", "--short"],
            cwd=toolchain.REPO_ROOT,
            check=True,
            capture_output=True,
            text=True,
        ).stdout
        self.assertEqual(os.environ.get("PATH"), before_path)
        self.assertEqual(shutil.which("node"), before_node_path)
        if before_node_path and before_node_identity:
            after_identity = (
                os.stat(before_node_path),
                toolchain._sha256_file(Path(before_node_path)),
            )
            self.assertEqual(after_identity, before_node_identity)
        self.assertEqual(after_node.returncode, before_node.returncode)
        self.assertEqual(after_node.stdout, before_node.stdout)
        self.assertEqual(after_node.stderr, before_node.stderr)
        self.assertEqual(after_git, before_git)


if __name__ == "__main__":
    unittest.main()
