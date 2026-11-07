"""Check offline safety and discovery boundaries without application imports."""
import json
from pathlib import Path
import tempfile
import unittest

from inventory import hook_key, scan, validate_plan


class InventoryTests(unittest.TestCase):
    def scan_source(self, source):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "example.py"
            path.write_text(source, encoding="utf-8")
            return scan(directory)

    def test_discovery_never_executes_module_or_emits_literals(self):
        result = self.scan_source('raise RuntimeError("must never execute")\n'
            'SECRET = "fixture-value-never-in-output"\n'
            'def attach(router):\n'
            '    @router.on_event("startup")\n'
            '    async def start():\n'
            '        asyncio.create_task(run(), name=SECRET)\n'
            '    router.on_shutdown.append(stop)\n')
        self.assertEqual([(r["event"], r["callback"]) for r in result["hooks"]],
            [("startup", "attach.start"), ("shutdown", "attach.stop")])
        self.assertEqual(result["launch_candidates"][0]["target"], "run")
        self.assertNotIn("fixture-value-never-in-output", json.dumps(result))
        self.assertFalse(result["runtime_verified"])

    def test_dynamic_append_is_explicitly_unresolved(self):
        result = self.scan_source('router.on_startup.append(make_callback())\n')
        self.assertIsNone(result["hooks"][0]["callback"])
        self.assertTrue(result["hooks"][0]["unresolved"])

    def test_syntax_error_is_recorded_without_source(self):
        result = self.scan_source('broken "sensitive-fixture" syntax\n')
        self.assertEqual(result["parse_errors"],
            [{"path": "example.py", "error": "SyntaxError"}])
        self.assertNotIn("sensitive-fixture", json.dumps(result))

    def test_unrelated_decorators_and_nested_scopes(self):
        result = self.scan_source('class Container:\n'
            '    def attach(self):\n'
            '        @router.on_event("shutdown")\n'
            '        async def stop(): pass\n'
            '        @router.get("/startup")\n'
            '        async def endpoint(): pass\n')
        self.assertEqual(len(result["hooks"]), 1)
        self.assertEqual(result["hooks"][0]["callback"], "Container.attach.stop")

    def test_excludes_tests_and_reports_source_drift(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "tests").mkdir()
            (root / "tests/bad.py").write_text("invalid syntax!", encoding="utf-8")
            module = root / "module.py"
            module.write_text("start_worker(db)\n", encoding="utf-8")
            before = scan(root)
            module.write_text("start_worker(other_db)\n", encoding="utf-8")
            after = scan(root)
            self.assertEqual(before["parse_errors"], [])
            self.assertNotEqual(before["source_sha256"], after["source_sha256"])

    def test_new_hook_cannot_inherit_acceptance_from_old_plan(self):
        old = self.scan_source('router.on_startup.append(start)\n')
        plan = {"assignments": [{"key": hook_key(old["hooks"][0]),
            "owner": "worker_sync"}], "source_sha256": old["source_sha256"]}
        self.assertTrue(validate_plan(old, plan)["inventory_coverage_passed"])
        new = self.scan_source('router.on_startup.append(start)\n'
            'router.on_shutdown.append(stop)\n')
        checked = validate_plan(new, plan)
        self.assertFalse(checked["inventory_coverage_passed"])
        self.assertEqual({e["code"] for e in checked["errors"]},
            {"unclassified_entry", "source_drift"})
        self.assertFalse(checked["deployment_authorized"])

    def test_multiple_or_invalid_owners_rejected(self):
        result = self.scan_source('router.on_startup.append(start)\n')
        key = hook_key(result["hooks"][0])
        plan = {"source_sha256": result["source_sha256"], "assignments": [
            {"key": key, "owner": "worker_sync"},
            {"key": key, "owner": "arbitrary_role"}]}
        checked = validate_plan(result, plan)
        self.assertEqual({e["code"] for e in checked["errors"]},
            {"duplicate_assignment", "invalid_owner"})

    def test_dynamic_hook_never_passes_even_if_manually_classified(self):
        result = self.scan_source('router.on_startup.append(factory())\n')
        plan = {"source_sha256": result["source_sha256"], "assignments": [
            {"key": hook_key(result["hooks"][0]), "owner": "worker_sync"}]}
        self.assertIn({"code": "dynamic_hook_unresolved"},
            validate_plan(result, plan)["errors"])


if __name__ == "__main__":
    unittest.main()
