"""Offline checks of fail-closed boundaries; never a Mongo substitute."""
import ast
import unittest

import run


def minimal_container():
    return {"Id": "test", "Image": "sha256:test", "Mounts": [],
            "Config": {"User": "999:999", "Env": ["PATH=/usr/bin"]},
            "NetworkSettings": {"Networks": {"none": {}}},
            "HostConfig": {"NetworkMode": "none", "Privileged": False,
                           "ReadonlyRootfs": True, "CapDrop": ["ALL"], "CapAdd": None,
                           "SecurityOpt": ["no-new-privileges"], "PortBindings": {},
                           "PublishAllPorts": False, "PidMode": "", "IpcMode": "private",
                           "Binds": None, "Devices": [], "ExtraHosts": None,
                           "NanoCpus": 4_000_000_000, "Memory": 4 * 1024**3,
                           "Tmpfs": {"/data/db": "rw"}}}


class ContractTests(unittest.TestCase):
    def test_runtime_writer_schema_changes_are_rejected(self):
        for path in ["backend/operational_atomic.py", "backend/orders_db.py",
                     "backend/salla_integration/auto_sync.py", "backend/schema.py",
                     ".github/workflows/mezan-production-release.yml"]:
            with self.subTest(path=path), self.assertRaises(RuntimeError):
                run.verify_changed_files([run.DIRECTORY + "probe.py", path])

    def test_only_independent_harness_is_accepted(self):
        run.verify_changed_files([run.DIRECTORY + "probe.py", run.WORKFLOW])

    def test_unsafe_container_settings_fail_closed(self):
        for key, value in {"NetworkMode": "bridge", "Privileged": True,
                           "ReadonlyRootfs": False, "CapAdd": ["NET_ADMIN"],
                           "CapDrop": [], "SecurityOpt": [], "PortBindings": {"27018/tcp": [{}]},
                           "PublishAllPorts": True, "PidMode": "host", "IpcMode": "host",
                           "Binds": ["/var/run/docker.sock:/var/run/docker.sock"],
                           "Devices": [{"PathOnHost": "/dev/sda"}], "ExtraHosts": ["example:1.2.3.4"],
                           "NanoCpus": 2_000_000_000}.items():
            candidate = minimal_container()
            candidate["HostConfig"][key] = value
            with self.subTest(key=key), self.assertRaises(RuntimeError):
                run.validate_container(candidate, "none", ["PATH=/usr/bin"])

    def test_inherited_credentials_or_persistent_data_are_rejected(self):
        for mutation in [lambda c: c["Config"]["Env"].append("API_TOKEN=synthetic-for-test"),
                         lambda c: c["Mounts"].append({"Type": "bind"}),
                         lambda c: c["Mounts"].append({"Type": "volume"}),
                         lambda c: c["Config"].update(User="0"),
                         lambda c: c["NetworkSettings"]["Networks"].update(bridge={})]:
            candidate = minimal_container()
            mutation(candidate)
            with self.assertRaises(RuntimeError):
                run.validate_container(candidate, "none", ["PATH=/usr/bin"])

    def test_safe_synthetic_config_accepted_without_printing_env_values(self):
        result = run.validate_container(minimal_container(), "none", ["PATH=/usr/bin"])
        self.assertEqual(result["environment_keys"], ["PATH"])
        self.assertNotIn("/usr/bin", str(result))

    def test_probe_has_no_application_or_mock_imports(self):
        tree = ast.parse((run.HERE / "probe.py").read_text())
        imports = {node.module.split(".")[0] for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)}
        imports |= {alias.name.split(".")[0] for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names}
        self.assertEqual(imports - {"errno", "importlib", "json", "os", "platform", "socket", "sys", "time", "pathlib", "pymongo"}, set())

    def test_workflow_contains_no_benchmark_or_secret_step(self):
        workflow = (run.ROOT / run.WORKFLOW).read_text()
        self.assertIn("run.py --environment-only", workflow)
        for forbidden in ["secrets.", "workflow_dispatch:", "pull_request:", "performance.py", "safety.py",
                          "production_release_guard", "npm ", "yarn ", "pytest ", "self-hosted"]:
            self.assertNotIn(forbidden, workflow)

    def test_build_context_is_explicitly_limited(self):
        dockerfile = (run.HERE / "Dockerfile").read_text()
        copies = [line for line in dockerfile.splitlines() if line.startswith("COPY ")]
        self.assertEqual(copies, ["COPY requirements.txt /probe/requirements.txt", "COPY probe.py /probe/probe.py"])
        self.assertTrue((run.HERE / ".dockerignore").read_text().startswith("*\n"))


if __name__ == "__main__":
    unittest.main()
