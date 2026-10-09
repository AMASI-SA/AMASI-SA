"""Finite isolated validation/benchmark runner; never starts the application."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import xml.etree.ElementTree as ET

p = argparse.ArgumentParser()
p.add_argument("--repo", required=True)
p.add_argument("--baseline", required=True)
p.add_argument("--output", required=True)
p.add_argument("--node", required=True)
p.add_argument("--yarn", required=True)
a = p.parse_args()
root, baseline, output = (Path(x).resolve() for x in (a.repo, a.baseline, a.output))
assert (root / "backend/order_review_completion.py").is_file()
assert (baseline / "backend/order_review_completion.py").is_file()
assert os.environ["MZ2_TEST_MONGO_URI"].startswith("mongodb://127.0.0.1:")
output.mkdir(parents=True, exist_ok=True)
env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1", "PYTHON_DOTENV_DISABLED": "1",
       "PATH": str(Path(a.node).resolve().parent) + os.pathsep + os.environ.get("PATH", ""),
       "PYTHONPATH": os.pathsep.join((str(root / "backend"), str(root / "backend/tests"))),
       "PYTHONIOENCODING": "utf-8", "CI": "true", "BROWSER": "none"}
results = {}
head = subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD"], text=True).strip()


def step(name, argv, cwd, timeout=1800):
    print("START " + name, flush=True)
    start = time.monotonic()
    with (output / (name + ".log")).open("w", encoding="utf-8") as log:
        try:
            run = subprocess.run([str(x) for x in argv], cwd=cwd, env=env, stdout=log,
                                 stderr=subprocess.STDOUT, timeout=timeout)
            code = run.returncode
        except subprocess.TimeoutExpired:
            code = 124
            log.write("\nVALIDATION TIMEOUT\n")
    results[name] = {"exit_code": code, "seconds": round(time.monotonic() - start, 3),
                     "status": "PASS" if code == 0 else "FAIL"}
    print(name + " " + results[name]["status"], flush=True)
    return code == 0


def suite(name, files):
    xml = output / (name + ".xml")
    ok = step(name, [sys.executable, "-B", "-m", "pytest", "--noconftest", "-p", "no:cacheprovider",
                    "-q", "--tb=short", *files, "--junitxml=" + str(xml)], root / "backend")
    if xml.exists():
        cases = list(ET.parse(xml).iter("testcase"))
        counts = {tag: sum(c.find(tag) is not None for c in cases) for tag in ("failure", "error", "skipped")}
        results[name].update(tests=len(cases), **counts)
        if not cases or any(counts.values()):
            results[name]["status"] = "FAIL"
            ok = False
    else:
        results[name]["status"] = "FAIL"
        ok = False
    return ok


step("pip-check", [sys.executable, "-B", "-m", "pip", "check"], root)
step("mongo-evidence", [sys.executable, "-B", "-c",
    "import os,json;from pymongo import MongoClient;c=MongoClient(os.environ['MZ2_TEST_MONGO_URI']);h=c.admin.command('hello');v=c.admin.command('buildInfo')['version'];assert h.get('setName') and v=='8.0.12';print(json.dumps({'version':v,'replica_set':h['setName'],'primary':h.get('isWritablePrimary'),'loopback':True}));c.close()"], root)
local_ok = suite("local-contracts", ["tests/test_review_local_completion.py", "tests/test_review_local_downstream.py",
                                      "tests/test_review_local_assembly.py", "tests/test_review_local_queues.py"])
review_files = [
    "test_review_completion_provider.py", "test_review_completion_confirmation.py", "test_review_completion_recovery.py",
    "test_review_completion_preparation.py", "test_review_completion_extra.py", "test_review_completion_acceptance.py",
    "test_review_completion_auto_resume.py", "test_review_completion_business_snapshot.py",
    "test_review_completion_representation_integration.py", "test_review_approval_scope.py",
    "test_review_acceptance_config_guard.py", "test_order_review_stage_one.py",
    "test_order_review_export_controls.py", "test_order_review_mobile_app_permissions.py", "test_reviewed_products_catalog.py",
]
suite("review-regressions", ["tests/" + f for f in review_files])
g47 = sorted(str(f.relative_to(root / "backend")) for f in (root / "backend/tests").glob("test_g47*.py"))
suite("g47", g47 + ["tests/test_stock_component_consumption.py"])
suite("preparation-regressions", ["tests/" + f for f in (
    "test_preparation_piece_operations.py", "test_preparation_piece_stage_semantics.py",
    "test_preparation_piece_execution_guard.py", "test_preparation_route_history.py",
    "test_supplier_dispatch_waiting_current_status.py", "test_fulfillment_v2_contract.py",
    "test_shipping_label_current_guard.py", "test_fulfillment_carrier_label.py",
)])
# Existing shipping/provider/financial code stays byte-identical to the base.
forbidden = ["backend/order_engine/shipping_label_service.py", "backend/salla_integration",
             "backend/integrations/qoyod_manual", "backend/accounting_atomic.py", "release/release-intent-v5.json",
             "scripts/production_release_guard.py", ".github/workflows/security-gate.yml", "frontend/yarn.lock", "backend/requirements.txt"]
step("scope-boundaries", ["git", "diff", "--exit-code", "128bfc1c2b4670d853a8bce33f15a667d2fdb120", "HEAD", "--", *forbidden], root)
installed = step("frontend-install", [a.node, a.yarn, "install", "--frozen-lockfile", "--non-interactive"], root / "frontend")
if installed:
    tests = ["reviewConfirmation", "orderReviewEngine.completion", "OrderReview", "FulfillmentMobileOverview",
             "reviewCustomerWaiting", "reviewUnitSplitGuard", "reviewAutoAdvance", "reviewExportControlsEnhancer",
             "reviewSpecReplacementEnhancer", "reviewMezanImageEnhancer", "reviewCustomerHistory", "reviewInternalPreparation"]
    step("frontend-tests", [a.node, root / "frontend/node_modules/react-scripts/bin/react-scripts.js", "test", "--watchAll=false",
          "--runInBand", "--testPathPattern=" + "|".join(tests), "--json", "--outputFile=" + str(output / "frontend-tests.json")], root / "frontend")
    step("frontend-build", [a.node, root / "frontend/node_modules/vite/bin/vite.js", "build",
                            "--outDir", output / "frontend-build"], root / "frontend")
if local_ok:
    bench = root / "backend/tests/benchmark_review_local.py"
    for label, checkout in (("baseline-performance", baseline), ("local-performance", root)):
        step(label, [sys.executable, "-B", bench, "--checkout", checkout, "--output", output / (label + ".json"),
                     "--samples", "80", "--provider-latency-ms", "50"], checkout / "backend", timeout=1200)
else:
    results["performance"] = {"status": "BLOCKED", "reason": "Local contracts must pass before performance comparison"}
step("source-unchanged-during-validation", ["git", "diff", "--exit-code", "HEAD"], root)
assert subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD"], text=True).strip() == head
report = {"head": head, "tree": subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD^{tree}"], text=True).strip(),
          "production_writes": 0, "results": results}
(output / "validation-summary.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
print(json.dumps(report, indent=2), flush=True)
sys.exit(0 if all(row["status"] == "PASS" for row in results.values()) else 1)
