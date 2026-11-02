"""Source-scope regressions; synthetic files only, no runtime imports or writes."""
from __future__ import annotations

import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from scripts import run_security_contracts as scope


class SecuritySourceScopeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        for path in scope.CORE_TESTS:
            self.write(path, "# synthetic security test\n")
        self.binding('"app.page.my_products"')

    def write(self, path, text):
        target = self.root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")

    def binding(self, args):
        self.write(scope.MOBILE_SOURCE, f'''# Synthetic AST only: never imported.
raise AssertionError("runtime must not be imported")
MOBILE_ROUTE_PERMISSIONS: tuple = (
    ("/unrelated", _permissions("app.page.reviewed_preparation")),
    ("/api/preparation-file-safety-v1", _permissions({args})),
)
''')

    def test_mz2_ignores_only_foreign_build37_request_with_proven_old_contract(self):
        without_extra, _ = scope.select_tests(self.root, [])
        with_extra, reason = scope.select_tests(self.root, [scope.BUILD37_TEST])
        self.assertEqual(with_extra, without_extra)
        self.assertEqual(with_extra, list(scope.CORE_TESTS))
        self.assertIn("NOT_APPLICABLE", reason)

    def test_new_contract_requires_test_even_when_workflow_did_not_request_it(self):
        self.binding('"app.page.reviewed_preparation", "app.page.my_products"')
        with self.assertRaisesRegex(ValueError, "Required Security Gate test is missing.*build37"):
            scope.select_tests(self.root, [])
        self.write(scope.BUILD37_TEST, "# synthetic fixture; not the BUILD37 test\n")
        paths, reason = scope.select_tests(self.root, [])
        self.assertEqual(paths, [*scope.CORE_TESTS, scope.BUILD37_TEST])
        self.assertIn("REQUIRED", reason)

    def test_existing_build37_test_is_never_filtered_even_with_old_contract(self):
        self.write(scope.BUILD37_TEST, "# synthetic fixture\n")
        paths, _ = scope.select_tests(self.root, [scope.BUILD37_TEST, scope.BUILD37_TEST])
        self.assertEqual(paths.count(scope.BUILD37_TEST), 1)

    def test_missing_any_core_test_fails_instead_of_filtering(self):
        for path in scope.CORE_TESTS:
            with self.subTest(path=path):
                (self.root / path).unlink()
                with self.assertRaisesRegex(ValueError, "Required Security Gate test is missing"):
                    scope.select_tests(self.root, [])
                self.write(path, "# synthetic security test\n")

    def test_additional_requested_test_is_mandatory(self):
        extra = "backend/tests/test_extra_security_boundary.py"
        with self.assertRaisesRegex(ValueError, "Required Security Gate test is missing"):
            scope.select_tests(self.root, [extra])
        self.write(extra, "# synthetic security test\n")
        paths, _ = scope.select_tests(self.root, [extra])
        self.assertIn(extra, paths)
        self.assertTrue(set(scope.CORE_TESTS).issubset(paths))

    def test_unknown_or_incomplete_contract_cannot_claim_not_applicable(self):
        cases = [
            "MOBILE_ROUTE_PERMISSIONS = load_permissions()",
            "MOBILE_ROUTE_PERMISSIONS = ()",
            "MOBILE_ROUTE_PERMISSIONS = ((ROUTE, _permissions('app.page.my_products')),)",
            "MOBILE_ROUTE_PERMISSIONS = (('/api/preparation-file-safety-v1', OTHER),)",
            "MOBILE_ROUTE_PERMISSIONS = (('/api/preparation-file-safety-v1', _permissions(*OTHER)),)",
            "MOBILE_ROUTE_PERMISSIONS = (( '/api/preparation-file-safety-v1', _permissions('app.page.my_products')),)*2",
        ]
        for source in cases:
            with self.subTest(source=source):
                self.write(scope.MOBILE_SOURCE, source)
                with self.assertRaises(ValueError):
                    scope.select_tests(self.root, [scope.BUILD37_TEST])

    def test_unknown_changed_literal_contract_requires_coverage(self):
        self.binding('"app.page.orders"')
        with self.assertRaisesRegex(ValueError, "Required Security Gate test is missing.*build37"):
            scope.select_tests(self.root, [])

    def test_missing_or_invalid_source_fails_closed(self):
        (self.root / scope.MOBILE_SOURCE).unlink()
        with self.assertRaises(FileNotFoundError):
            scope.select_tests(self.root, [])
        self.write(scope.MOBILE_SOURCE, "not valid python!")
        with self.assertRaises(SyntaxError):
            scope.select_tests(self.root, [])

    def test_run_preserves_pytest_failures_and_executes_all_selected_tests(self):
        self.write(scope.BUILD37_TEST, "# synthetic failing fixture\n")
        for exit_code in (0, 1, 2, 4, 5):
            with self.subTest(exit_code=exit_code), \
                 patch.dict(scope.os.environ, {"SECURITY_SOURCE_SHA": "source-head"}), \
                 patch.object(scope.subprocess, "check_output", return_value="source-head\n"), \
                 patch.object(scope.subprocess, "run", return_value=Mock(returncode=exit_code)) as execute, \
                 contextlib.redirect_stdout(io.StringIO()) as log:
                self.assertEqual(scope.run(self.root, []), exit_code)
                execute.assert_called_once()
                argv = execute.call_args.args[0]
                self.assertEqual(argv[1:4], ["-m", "pytest", "-q"])
                self.assertEqual(argv[4:], [*scope.CORE_TESTS, scope.BUILD37_TEST])
                self.assertEqual(json.loads(log.getvalue())["source_git_sha"], "source-head")

    def test_wrong_checked_out_sha_cannot_run_pytest(self):
        with patch.dict(scope.os.environ, {"SECURITY_SOURCE_SHA": "expected"}), \
             patch.object(scope.subprocess, "check_output", return_value="other\n"), \
             patch.object(scope.subprocess, "run") as execute:
            with self.assertRaisesRegex(ValueError, "expected source SHA"):
                scope.run(self.root, [])
            execute.assert_not_called()

    def test_workflow_keeps_core_and_inherited_security_checks(self):
        root = Path(__file__).resolve().parents[2]
        workflow = (root / ".github/workflows/security-gate.yml").read_text(encoding="utf-8")
        self.assertIn("python scripts/run_security_contracts.py", workflow)
        self.assertIn("backend.tests.test_security_gate_source_scope", workflow)
        self.assertIn("backend/tests/test_inherited_security_*.py", workflow)
        for path in scope.CORE_TESTS:
            self.assertIn(path, workflow)
        self.assertIn("ref: ${{ github.event.pull_request.head.sha || github.sha }}", workflow)
        self.assertNotIn("continue-on-error:", workflow)


if __name__ == "__main__":
    unittest.main()
