"""Isolated accounting/operational composition; never merges or deploys.

Checkouts are pinned in CI. Test DB URIs must be disposable loopback Mongo.
The unchanged baseline must reproduce the fee bug as an assertion failure.
"""
import argparse
import ast
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET

BASE = "ba743862f4ac0e6b428bf251402bd7fc04862002"
BASE_TREE = "d9c97d996087686b6e393af211b499a71a51fe0c"
OPERATIONAL = "6507c813a73ea71a5eab2a8d39c5bb92a4075c70"
OPERATIONAL_TREE = "cb9507cbf79ce401a3381c3311355c0cd3effe05"
OWNED = {
    ".github/workflows/mz2-current-carrier-fee.yml",
    "scripts/verify_current_carrier_fee.py",
    "backend/accounting_public_errors.py",
    "backend/accounting_shipping_current_guard.py",
    "backend/accounting_shipping_native.py",
    "backend/accounting_shipping_native_evidence.py",
    "backend/accounting_shipping_p02.py",
    "backend/tests/test_mz2_shipping_current_carrier.py",
    "backend/tests/test_mz2_shipping_current_carrier_native.py",
    "backend/tests/test_mz2_shipping_native.py",
    "docs/operations/MZ2-SALLA-CURRENT-CARRIER-ACCOUNTING-20261001/STATUS.md",
}
OPS_FILES = (
    "backend/salla_shipping.py", "backend/orders_db.py",
    "backend/salla_integration/sync.py", "backend/salla_integration/webhook_order_sync.py",
    "backend/order_engine/mapper.py", "backend/order_engine/models.py", "backend/order_engine/repository.py",
    "backend/order_engine/salla_refresh.py", "backend/order_engine/shipping_label_service.py",
    "backend/tests/test_g47_current_shipping.py", "backend/tests/test_salla_current_shipping.py",
    "backend/tests/test_shipping_label_current_guard.py", "backend/tests/test_order_engine_salla_refresh.py",
)
SUITES = (
    "test_mz2_shipping_current_carrier", "test_mz2_shipping_current_carrier_native",
    "test_mz2_shipping_native", "test_mz2_shipping_p02", "test_mz2_driver_payment_review",
    "test_mz2_driver_pos_manual_review", "test_mz2_driver_bank_evidence", "test_mz2_shipping_bank_port",
    "test_accounting_ledger_v2", "test_accounting_onboarding", "test_accounting_onboarding_domains",
    "test_financial_provider_apps_v2", "test_fulfillment_v2_contract", "test_order_engine_repository",
    "test_order_engine_mapper", "test_mz2_shipping_contracts", "test_mz2_shipping_contract_isolation",
    "test_mz2_shipping_payment_evidence", "test_mz2_legacy_shipping_gate",
    "test_store_delivery_operational_v2_contract", "test_store_delivery_driver_app_v2_contract",
    "test_store_delivery_payment_evidence", "test_g47_current_shipping", "test_salla_current_shipping",
    "test_shipping_label_current_guard", "test_order_engine_salla_refresh",
)


def git(root, *args):
    return subprocess.check_output(["git", "-C", str(root), *args], text=True).strip()


def entries(root):
    return {row.split("\t", 1)[1]: row.split("\t", 1)[0] for row in git(root, "ls-tree", "-r", "HEAD").splitlines()}


def functions(root, path):
    return {n.name: ast.dump(n, include_attributes=False) for n in ast.parse((root / path).read_text()).body
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}


def inspect_scope(candidate, baseline, operational, results):
    assert git(baseline, "rev-parse", "HEAD") == BASE
    assert git(baseline, "rev-parse", "HEAD^{tree}") == BASE_TREE
    assert git(operational, "rev-parse", "HEAD") == OPERATIONAL
    assert git(operational, "rev-parse", "HEAD^{tree}") == OPERATIONAL_TREE
    before, after = entries(baseline), entries(candidate)
    changed = sorted(p for p in before.keys() | after.keys() if before.get(p) != after.get(p))
    assert set(changed) == OWNED, changed
    retained = {
        "backend/accounting_shipping_native.py": ("_post", "_economic", "recognition_legs", "_seal_delivery", "settle"),
        "backend/accounting_shipping_native_evidence.py": ("canonical_facts", "driver_facts"),
        "backend/accounting_shipping_p02.py": ("select_courier_rate", "post_courier_fee"),
    }
    for path, names in retained.items():
        original, proposed = functions(baseline, path), functions(candidate, path)
        for name in names:
            assert original[name] == proposed[name], (path, name)
    guard = ast.parse((candidate / "backend/accounting_shipping_current_guard.py").read_text())
    forbidden = {"insert_one", "insert_many", "update_one", "update_many", "replace_one", "delete_one",
                 "delete_many", "bulk_write", "post_txn_group", "post_journal_v2", "select_rate", "quote"}
    assert not [n.func.attr for n in ast.walk(guard) if isinstance(n, ast.Call) and
                isinstance(n.func, ast.Attribute) and n.func.attr in forbidden]
    result = {"candidate_head": git(candidate, "rev-parse", "HEAD"),
              "candidate_tree": git(candidate, "rev-parse", "HEAD^{tree}"),
              "baseline_head": BASE, "baseline_tree": BASE_TREE,
              "operational_head": OPERATIONAL, "operational_tree": OPERATIONAL_TREE,
              "changed_files": changed, "unchanged_functions": retained,
              "writer": "accounting_shipping_native.accrue_fee -> accounting_ledger_v2.post_journal_v2",
              "production_financial_writes": 0, "composition": "temporary test checkout only"}
    (results / "source-proof.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2), flush=True)


def pytest(root, cases, report):
    env = {**os.environ, "PYTHONPATH": str(root / "backend") + ":" + str(root / "backend/tests")}
    return subprocess.call([sys.executable, "-m", "pytest", "--noconftest", "-p", "no:cacheprovider",
                            "-q", "--tb=short", "--asyncio-mode=auto", "--junitxml=" + str(report), *cases],
                           cwd=root, env=env)


def main():
    parser = argparse.ArgumentParser()
    for name in ("candidate", "baseline", "operational", "work", "results"):
        parser.add_argument("--" + name, required=True, type=Path)
    args = parser.parse_args()
    for name in ("MZ2_TEST_MONGO_URI", "MZ2_TEST_STANDALONE_URI"):
        assert os.environ[name].startswith("mongodb://127.0.0.1:"), name
    args.results.mkdir(parents=True, exist_ok=True)
    inspect_scope(args.candidate, args.baseline, args.operational, args.results)
    regression = args.work / "baseline"
    shutil.copytree(args.baseline, regression, ignore=shutil.ignore_patterns(".git"))
    test = "backend/tests/test_mz2_shipping_current_carrier_native.py"
    shutil.copy2(args.candidate / test, regression / test)
    report = args.results / "baseline-regression.xml"
    code = pytest(regression, [test + "::test_native_old_imile_evidence_cannot_fee_store_driver"], report)
    cases = list(ET.parse(report).iter("testcase"))
    assert code == 1 and len(cases) == 1, "Baseline bug was not reproduced"
    failure = cases[0].find("failure")
    assert failure is not None and "DID NOT RAISE" in (failure.text or ""), "Baseline failed for an unrelated reason"
    assert cases[0].find("error") is None and cases[0].find("skipped") is None
    combined = args.work / "candidate-with-operational"
    shutil.copytree(args.candidate, combined, ignore=shutil.ignore_patterns(".git"))
    for path in OPS_FILES:
        shutil.copy2(args.operational / path, combined / path)
    report = args.results / "acceptance.xml"
    code = pytest(combined, ["backend/tests/" + name + ".py" for name in SUITES], report)
    cases = list(ET.parse(report).iter("testcase"))
    rejected = [row.attrib for row in cases if any(row.find(tag) is not None for tag in ("skipped", "failure", "error"))]
    observed = {part for row in cases for part in row.attrib.get("classname", "").split(".")}
    assert code == 0 and cases and not rejected, rejected
    assert set(SUITES) <= observed, "A required suite did not execute"
    print(f"Current carrier: {len(cases)} executed cases; zero skips/failures/errors", flush=True)


if __name__ == "__main__":
    main()
