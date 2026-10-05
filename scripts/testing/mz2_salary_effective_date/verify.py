"""Repeatable local-only salary verification; emits inspectable evidence."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import xml.etree.ElementTree as ET

sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parents[3]
parser = argparse.ArgumentParser()
parser.add_argument("--python", required=True)
parser.add_argument("--node-modules", required=True)
parser.add_argument("--mongo-uri", required=True)
parser.add_argument("--output", required=True)
args = parser.parse_args()
if not args.mongo_uri.startswith("mongodb://127.0.0.1:"):
    raise SystemExit("Only a disposable loopback replica set is accepted")
out = Path(args.output).resolve()
out.mkdir(parents=True, exist_ok=True)
env = {k: v for k, v in os.environ.items() if k.upper() in {
    "SYSTEMROOT", "WINDIR", "PATH", "TEMP", "TMP", "COMSPEC", "PATHEXT", "USERPROFILE",
}}
env.update(PYTHONPATH=str(ROOT / "backend"), PYTHON_DOTENV_DISABLED="1",
           JWT_SECRET="synthetic-salary-effective-test-only", MZ2_TEST_MONGO_URI=args.mongo_uri,
           NODE_PATH=str(Path(args.node_modules).resolve()), CI="true", NODE_ENV="test")
backend = [args.python, "-m", "pytest", "--noconftest", "-q", "-o", "asyncio_mode=auto",
           "--tb=short", "--disable-warnings", f"--junitxml={out / 'backend.xml'}", *[
               f"backend/tests/{name}.py" for name in (
                   "test_employee_salary_effective_date", "test_employee_salary_contract_management",
                   "test_employee_payroll_v2_cutover", "test_employees_v2_management",
                   "test_employee_account_access_revocation", "test_employee_password_policy")]]
jest = """
const base=process.env.NODE_PATH;
const real=require('fs').realpathSync(base+'/react-scripts');
const req=require('module').createRequire(real+'/package.json');
const config=req('./scripts/utils/createJestConfig')(p=>real+'/'+p,process.cwd(),false);
config.rootDir=process.cwd();
config.testMatch=['**/EmployeesV2Management.test.jsx','**/employeesV2.test.js'];
config.moduleDirectories=['node_modules',base];
config.testEnvironment=req.resolve('jest-environment-jsdom');
req('jest').run(['--config',JSON.stringify(config),'--watch=false','--runInBand','--json','--outputFile',process.argv[1]]);
"""
frontend = ["node", "-e", jest, str(out / "frontend.json")]
results = []
for name, argv, cwd in [("backend", backend, ROOT), ("frontend", frontend, ROOT / "frontend")]:
    with (out / f"{name}.log").open("w", encoding="utf-8") as log:
        result = subprocess.run(argv, cwd=cwd, env=env, stdout=log, stderr=subprocess.STDOUT)
    results.append({"name": name, "argv": argv, "cwd": str(cwd), "exit_code": result.returncode})
    print(name, "exit", result.returncode, flush=True)
    if result.returncode:
        print((out / f"{name}.log").read_text(encoding="utf-8"), flush=True)
        break
source_paths = [
    "backend/server.py", "backend/employee_password_policy.py", "backend/tests/test_employee_password_policy.py",
    "frontend/src/services/employeesV2.js", "frontend/src/services/employeesV2.test.js",
    "backend/employee_payroll_status.py", "backend/employees_v2_routes.py",
    "backend/tests/test_employee_salary_effective_date.py", "backend/tests/test_employee_salary_contract_management.py",
    "backend/tests/test_employee_payroll_v2_cutover.py", "frontend/src/pages/EmployeesV2Management.jsx",
    "frontend/src/pages/EmployeesV2Management.test.jsx",
]
summary = {"head": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
           "production_writes": 0, "accounting_routes_installed_in_ui_fixture": False,
           "environment_policy": "OS allowlist; dotenv disabled; synthetic JWT; disposable loopback Mongo",
           "commands": results,
           "sha256": {p: hashlib.sha256((ROOT / p).read_bytes()).hexdigest() for p in source_paths}}
if (out / "backend.xml").exists():
    suites = ET.parse(out / "backend.xml").getroot().findall("testsuite")
    summary["backend"] = {key: sum(int(s.get(key, 0)) for s in suites) for key in ("tests", "failures", "errors", "skipped")}
if (out / "frontend.json").exists():
    data = json.loads((out / "frontend.json").read_text(encoding="utf-8"))
    summary["frontend"] = {key: data[key] for key in ("numPassedTests", "numFailedTests", "numPendingTests", "success")}
(out / "verification.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
raise SystemExit(0 if len(results) == 2 and all(r["exit_code"] == 0 for r in results) else 1)
