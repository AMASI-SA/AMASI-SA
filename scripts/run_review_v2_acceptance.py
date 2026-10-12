"""Bounded Review V2 CI: synthetic source tests and mandatory evidence readback.

No build, deployment, provider credential, migration or production connection.
The socket audit covers Python test execution, not an OS-wide security sandbox.
"""
import argparse
import contextlib
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import re
import socket
import subprocess
import sys
import threading
import traceback
import xml.etree.ElementTree as ET
import zipfile

ROOT = Path(__file__).resolve().parents[1]
TESTS = (
    "test_review_readback_v2.py", "test_review_session_fence.py",
    "test_review_catalog_first_use.py", "test_review_displayed_approval.py",
    "test_review_source_event_race.py", "test_review_local_completion.py",
    "test_review_acceptance_config_guard.py", "test_g47_component_lifecycle_integration.py",
    "test_review_local_assembly.py", "test_auth_session_revocation_v1.py",
    "test_mobile_session_security.py", "test_mfa_security_v1.py",
    "test_email_otp_security_v1.py", "test_passkey_security_v1.py",
    "test_auth_security_replica_startup.py",
)
MINIMUM_CASES = dict(zip(TESTS, (37, 28, 66, 11, 6, 16, 14, 19, 26, 9, 11, 11, 13, 9, 14)))
# Combined Build44 candidate: existing supplier, shipping and stock contracts.
# Minimum counts come from isolated collection; FAIL/ERROR/SKIP remains fatal.
BUILD44_CASES = {
    'test_build44_pdf_worker_allowlist.py': 1,
    'test_supplier_scan_lock.py': 16,
    'test_supplier_scan_recovery.py': 15,
    'test_supplier_scan_approval.py': 1,
    'test_supplier_scan_retention.py': 1,
    'test_supplier_native_invoice_v2.py': 26,
    'test_supplier_invoice_financial_integrity.py': 28,
    'test_supplier_invoice_private_history.py': 13,
    'test_supplier_invoice_display_readonly.py': 13,
    'test_supplier_invoice_display_projection.py': 18,
    'test_supplier_display_financial_boundary.py': 1,
    'test_supplier_refresh_safety.py': 11,
    'test_supplier_refresh_wrapper.py': 3,
    'test_supplier_refresh_real_mongo.py': 6,
    'test_supplier_receiving.py': 44,
    'test_supplier_receiving_read_scope.py': 3,
    'test_shipping_print_legacy_path.py': 236,
    'test_shipping_label_current_guard.py': 29,
    'test_fulfillment_carrier_label.py': 19,
    'test_preparation_piece_operations.py': 49,
    'test_build44_combined_overlap.py': 1,
}
TESTS += tuple(BUILD44_CASES)
MINIMUM_CASES.update(BUILD44_CASES)

FOCUSED_PACKAGES = {
    "fastapi", "pydantic", "pymongo", "motor", "pytest", "httpx", "openpyxl",
    "python-dotenv", "cryptography", "bcrypt", "pyjwt", "python-multipart",
    "reportlab", "pillow", "qrcode", "arabic-reshaper", "python-bidi", "pymupdf",
    "tzdata", "webauthn", "email-validator", "requests", "python-dateutil",
}
WEB_TESTS = ("src/services/orderReviewEngine.completion.test.js", "src/reviewConfirmation.test.js")
WEB_FILES = ("frontend/src/pages/OrderReview.jsx", "frontend/src/reviewConfirmation.test.js",
             "frontend/src/reviewCustomerWaiting.js", "frontend/src/services/orderReviewEngine.completion.test.js",
             "frontend/src/services/orderReviewEngine.js", "frontend/package.json", "frontend/yarn.lock")
PAYLOAD_FILES = {"source.json", "environment.json", "results.json", "pytest.xml", "pytest.log", "network.json",
                 "web-results.json", "web.log"}
WORKFLOW = ".github/workflows/review-readback-v2-candidate.yml"
COMMENT_ENV_FILE = "frontend/.env"
COMMENT_ENV_SHA256 = "d4bf7fd6787bdff6430b1d17290dc7bbe64789718912aae9f137d267ac166b4d"


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def allowed_pdf_worker(event, values):
    """Only the bounded parser, still inside the fixture's network-none namespace."""
    if event != "subprocess.Popen" or len(values) != 4:
        return False
    executable, argv, cwd, environment = values
    worker = ROOT / "backend" / "shipping_pdf_worker.py"
    return (executable == sys.executable
            and argv == [sys.executable, "-I", str(worker)]
            and cwd == str(worker.parent)
            and environment == {"PATH": os.defpath, "LANG": "C.UTF-8"})


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def git(*arguments):
    return subprocess.check_output(["git", "-C", str(ROOT), *arguments], text=True).strip()


def source_identity(args):
    require(re.fullmatch(r"[0-9a-f]{40}", args.sha), "exact source SHA required")
    require(str(args.run_id).isdigit() and str(args.run_attempt).isdigit(), "run identity required")
    require(args.repository == "AMASI-SA/AMASI-SA", "unexpected repository identity")
    require(git("rev-parse", "HEAD") == args.sha, "checkout does not match approved event HEAD")
    require(not git("status", "--porcelain", "--untracked-files=no"), "tracked checkout is dirty")
    return {"head": args.sha, "tree": git("rev-parse", "HEAD^{tree}"),
            "run_id": str(args.run_id), "run_attempt": str(args.run_attempt),
            "repository": args.repository, "workflow": WORKFLOW,
            "workflow_sha": args.sha}


def source_hashes():
    names = git("ls-files", "backend", "scripts/run_review_v2_acceptance.py", WORKFLOW, COMMENT_ENV_FILE, *WEB_FILES).splitlines()
    selected = [name for name in names if name.endswith((".py", ".yml")) or name in ("backend/requirements.txt", COMMENT_ENV_FILE) or name in WEB_FILES]
    require(all("backend/tests/" + name in selected for name in TESTS), "required test source missing")
    require(all(name in selected for name in WEB_FILES), "required Web contract source missing")
    require(COMMENT_ENV_FILE in selected, "approved comment-only environment source missing")
    for name in selected:
        require((ROOT/name).is_file() and not (ROOT/name).is_symlink(), "unexpected source type: " + name)
    return {name: sha256((ROOT/name).read_bytes()) for name in sorted(selected)}


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def environment_preflight():
    tracked = set(git("ls-files", "--", ".env*", "backend/.env*", "frontend/.env*").splitlines())
    # These two existing templates are not loaded by dotenv or CRA. No wildcard
    # exemption: every other dotenv path except the pinned comments is denied.
    templates = {".env.salla-amasi-test.example", ".env.salla-sandbox.example"}
    for folder in (ROOT, ROOT/"backend", ROOT/"frontend"):
        for path in folder.iterdir():
            if path.name != ".env" and not path.name.startswith(".env."):
                continue
            name = path.relative_to(ROOT).as_posix()
            require(path.is_file() and not path.is_symlink(), "unexpected dotenv file type: " + name)
            if name in templates:
                require(name in tracked, "untracked environment template: " + name)
                continue
            require(name == COMMENT_ENV_FILE, "active dotenv file forbidden: " + name)
    path = ROOT/COMMENT_ENV_FILE
    require(COMMENT_ENV_FILE in tracked and path.is_file() and not path.is_symlink(),
            "tracked comment-only frontend environment file required")
    content = path.read_bytes(); normalized = content.replace(b"\r\n", b"\n")
    require(len(normalized.splitlines()) == 2 and all(line.startswith(b"#") for line in normalized.splitlines())
            and sha256(normalized) == COMMENT_ENV_SHA256, "frontend environment is not the pinned two-comment file")
    blob = subprocess.check_output(["git", "-C", str(ROOT), "show", "HEAD:" + COMMENT_ENV_FILE])
    require(sha256(blob.replace(b"\r\n", b"\n")) == COMMENT_ENV_SHA256,
            "committed frontend environment differs from approved comments")
    return {"file": COMMENT_ENV_FILE, "normalized_sha256": COMMENT_ENV_SHA256,
            "raw_sha256": sha256(content), "tracked_blob_sha256": sha256(blob),
            "normalization": "CRLF-to-LF only", "assignments": 0, "active_dotenv_files": [],
            "dotenv_values_loaded": False}


def requirements(args):
    pins = {}
    for line in (ROOT/"backend/requirements.txt").read_text(encoding="utf-8").splitlines():
        if "==" in line:
            name = line.split("==", 1)[0].strip().lower().replace("_", "-")
            if name in FOCUSED_PACKAGES:
                pins[name] = line.strip()
    require(set(pins) == FOCUSED_PACKAGES, "focused dependency has no exact source pin")
    # Test-only packages use the repository's existing Review CI versions.
    pins.update({"pytest-asyncio": "pytest-asyncio==1.3.0", "mongomock-motor": "mongomock-motor==0.0.36"})
    args.output.write_text("\n".join(pins[key] for key in sorted(pins)) + "\n", encoding="utf-8")


def outcomes(xml_bytes):
    cases = list(ET.fromstring(xml_bytes).iter("testcase"))
    counts = {"PASS": 0, "FAIL": 0, "ERROR": 0, "SKIP": 0}
    rows = []
    for case in cases:
        state = next((status for tag, status in (("failure", "FAIL"), ("error", "ERROR"), ("skipped", "SKIP"))
                      if case.find(tag) is not None), "PASS")
        counts[state] += 1
        rows.append({"class": case.get("classname", ""), "name": case.get("name", ""), "status": state})
    return counts, rows


def require_pass(counts, rows):
    require(counts["PASS"] > 0 and not any(counts[key] for key in ("FAIL", "ERROR", "SKIP")),
            "acceptance contains FAIL/ERROR/SKIP or no executed tests")
    for filename in TESTS:
        module = filename.removesuffix(".py")
        executed = sum(module in row["class"].split(".") for row in rows)
        require(executed >= MINIMUM_CASES[filename], "missing accepted cases: " + filename + " = " + str(executed))


def require_web_pass(result, identity):
    require(result.get("acceptance_identity") == identity and result.get("acceptance_exit_code") == 0
            and result.get("acceptance_source_unchanged") is True, "Web execution or source identity failed")
    require(result.get("success") is True and result.get("wasInterrupted") is False,
            "Web execution unsuccessful or interrupted")
    require(all(result.get(key) == 0 for key in ("numFailedTests", "numPendingTests", "numTodoTests",
            "numFailedTestSuites", "numPendingTestSuites", "numRuntimeErrorTestSuites")), "Web FAIL/SKIP/TODO/ERROR")
    require(result.get("numTotalTests") == result.get("numPassedTests") == 21
            and result.get("numTotalTestSuites") == result.get("numPassedTestSuites") == 2,
            "required 21 Web assertions were not all executed")
    suites = result.get("testResults", [])
    require(len(suites) == 2 and {Path(suite["name"]).as_posix().split("/frontend/")[-1] for suite in suites}
            == set(WEB_TESTS), "Web suite identity differs")
    assertions = [assertion for suite in suites for assertion in suite.get("assertionResults", [])]
    require(len(assertions) == 21 and all(assertion.get("status") == "passed" for assertion in assertions)
            and all(suite.get("status") == "passed" for suite in suites), "Web assertion results differ")


def run_web(args):
    identity = source_identity(args); hashes = source_hashes()
    environment_proof = environment_preflight()
    require(not args.directory.exists(), "Web evidence output must be fresh")
    args.directory.mkdir(parents=True)
    isolated_home = args.directory/"home"; isolated_home.mkdir()
    environment = {key: value for key, value in os.environ.items() if key.upper() in {
        "PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "TMPDIR", "COMSPEC", "LANG", "LC_ALL"}}
    environment.update({"CI": "true", "HOME": str(isolated_home), "PYTHON_DOTENV_DISABLED": "1"})
    report = args.directory/"web-results.json"
    command = ["node", "node_modules/react-scripts/bin/react-scripts.js", "test", "--watchAll=false",
               "--runInBand", "--runTestsByPath", *WEB_TESTS, "--json", "--outputFile=" + str(report)]
    with (args.directory/"web.log").open("w", encoding="utf-8") as log:
        try:
            process = subprocess.run(command, cwd=ROOT/"frontend", env=environment,
                                     stdout=log, stderr=subprocess.STDOUT, timeout=180)
            code = process.returncode
        except (OSError, subprocess.TimeoutExpired):
            traceback.print_exc(file=log); code = 3
    result = json.loads(report.read_text(encoding="utf-8")) if report.exists() else {
        "success": False, "launcher_error": "Jest did not produce its JSON report"}
    result.update({"acceptance_identity": identity, "acceptance_exit_code": code,
                   "acceptance_environment": environment_proof,
                   "acceptance_source_unchanged": source_identity(args) == identity and source_hashes() == hashes})
    write_json(report, result)
    require_web_pass(result, identity)
    print("Web: 21 PASS / 0 FAIL / 0 SKIP")


def require_network_proof(proof):
    require(all(re.fullmatch(r"net:\[[0-9]+\]", proof.get(key, "")) for key in
                ("namespace", "expected_namespace", "host_namespace")), "invalid network namespace identity")
    require(proof["namespace"] == proof["expected_namespace"] != proof["host_namespace"],
            "test process must share only the fixture network namespace")
    require(proof["interfaces"] == ["lo"] and proof["ipv4_routes"] == [],
            "non-loopback interface or route present; no external/metadata connectivity permitted")
    require(type(proof["uid"]) is int and proof["uid"] > 0 and proof["uid"] == proof["expected_uid"]
            and proof["gid"] == proof["expected_gid"] and proof["groups"] == []
            and proof["effective_capabilities"] == 0, "test process privileges were not dropped")


def network_preflight(args):
    require(platform.system() == "Linux", "network namespace acceptance requires Linux")
    status = dict(line.split(":", 1) for line in Path("/proc/self/status").read_text().splitlines() if ":" in line)
    proof = {"namespace": os.readlink("/proc/self/ns/net"), "expected_namespace": args.network_namespace,
        "host_namespace": args.host_network_namespace, "interfaces": sorted(name for _, name in socket.if_nameindex()),
        "ipv4_routes": Path("/proc/net/route").read_text().splitlines()[1:],
        "uid": os.geteuid(), "gid": os.getegid(), "groups": os.getgroups(),
        "expected_uid": args.runner_uid, "expected_gid": args.runner_gid,
        "effective_capabilities": int(status["CapEff"].strip(), 16)}
    require_network_proof(proof)
    return proof


def run_tests(args):
    identity = source_identity(args)
    hashes = source_hashes()
    environment_proof = environment_preflight()
    namespace_proof = network_preflight(args)
    require(not args.directory.exists(), "evidence output must be fresh for this run attempt")
    args.directory.mkdir(parents=True)
    write_json(args.directory/"source.json", {"identity": identity, "files": hashes})
    for name in ("web-results.json", "web.log"):
        require(args.web_directory and (args.web_directory/name).is_file(), "missing Web evidence: " + name)
        (args.directory/name).write_bytes((args.web_directory/name).read_bytes())
    # No inherited token, cookie, database URI or provider configuration reaches
    # import/test code. GitHub artifact transport runs in separate official steps.
    keep = {key: value for key, value in os.environ.items() if key.upper() in {
        "PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "TMPDIR", "COMSPEC", "LANG", "LC_ALL"}}
    os.environ.clear(); os.environ.update(keep)
    os.environ.update({"PYTHON_DOTENV_DISABLED": "1", "PYTHONDONTWRITEBYTECODE": "1",
        "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1", "CI": "true",
        "JWT_SECRET": "public-synthetic-review-v2-ci-signing-key",
        "MZ2_TEST_MONGO_URI": "mongodb://127.0.0.1:27589/?replicaSet=readbackV2Candidate"})
    # Every supplier fixture shares the same disposable, network-none replica set.
    for name in ("BUILD20_SCAN_TEST_MONGO_URL", "BUILD20_INVOICE_TEST_MONGO_URL",
                 "BUILD37_TEST_MONGO_URI"):
        os.environ[name] = os.environ["MZ2_TEST_MONGO_URI"]
    sys.dont_write_bytecode = True
    sys.path[:0] = [str(ROOT/"backend"), str(ROOT/"backend/tests")]
    local = threading.local(); old_pair = socket.socketpair; blocked = []; executing_tests = False
    worker = ROOT / "backend" / "shipping_pdf_worker.py"
    worker_digest = sha256(worker.read_bytes())

    def pair(*a, **kw):
        local.pair = True
        try:
            return old_pair(*a, **kw)
        finally:
            local.pair = False

    def audit(event, values):
        if event == "socket.connect":
            address = values[1]
            if not (isinstance(address, tuple) and address[0] in ("127.0.0.1", "::1")
                    and (address[1] == 27589 or getattr(local, "pair", False))):
                blocked.append({"event": event, "destination": str(address)})
                raise PermissionError("review fixture: external connection forbidden")
        elif event == "socket.getaddrinfo" and values[0] not in ("127.0.0.1", "::1", "localhost"):
            blocked.append({"event": event, "destination": str(values[0])})
            raise PermissionError("review fixture: external DNS forbidden")
        elif executing_tests and event in ("subprocess.Popen", "os.system", "os.posix_spawn"):
            if allowed_pdf_worker(event, values) and sha256(worker.read_bytes()) == worker_digest:
                return
            blocked.append({"event": event})
            raise PermissionError("review fixture: child process forbidden during tests")

    socket.socketpair = pair
    sys.addaudithook(audit)
    # Load real libraries before a legacy test can install a fake jwt globally.
    import jwt, bcrypt, pytest, pymongo, motor  # noqa: F401
    client = pymongo.MongoClient(os.environ["MZ2_TEST_MONGO_URI"], serverSelectionTimeoutMS=5000)
    hello = client.admin.command("hello")
    version = client.admin.command("buildInfo")["version"]
    require(version == "8.0.12" and hello.get("setName") == "readbackV2Candidate"
            and hello.get("isWritablePrimary") is True, "real Mongo 8.0.12 PRIMARY required")
    client.close()
    write_json(args.directory/"environment.json", {"python": sys.version, "os": platform.platform(),
        "mongo": version, "replica_set": hello["setName"], "PRIMARY": True,
        "packages": {dist.metadata["Name"]: dist.version for dist in importlib.metadata.distributions()},
        "mongo_network": "Docker network none; host Python shares fixture netns; no published ports",
        "network_namespace": namespace_proof,
        "python_network": "loopback27589 and stdlib socketpair only; only exact isolated PDF worker with cleared environment may spawn inside network-none namespace",
        "production_credentials_copied": False, "dotenv_disabled": True,
        "dotenv_preflight": environment_proof,
        "web_tests": list(WEB_TESTS), "web_isolation": "mocked API contracts; stripped environment; no provider credentials"})
    exit_code = 3
    extra_errors = []
    class Results:
        def pytest_runtest_logreport(self, report):
            if getattr(report, "wasxfail", None):
                extra_errors.append({"nodeid": report.nodeid, "reason": "XFAIL/XPASS is not accepted"})
    with (args.directory/"pytest.log").open("w", encoding="utf-8") as log:
        try:
            executing_tests = True
            with contextlib.redirect_stdout(log), contextlib.redirect_stderr(log):
                exit_code = int(pytest.main(["--noconftest", "-p", "no:cacheprovider", "-p", "pytest_asyncio.plugin",
                    "-q", "--tb=short", "--asyncio-mode=auto", *["backend/tests/" + name for name in TESTS],
                    "--junitxml=" + str(args.directory/"pytest.xml")], plugins=[Results()]))
        except BaseException:
            traceback.print_exc(file=log)
        finally:
            executing_tests = False
    write_json(args.directory/"network.json", {"blocked_attempts": blocked})
    if not (args.directory/"pytest.xml").exists():
        (args.directory/"pytest.xml").write_text(
            '<testsuites><testsuite><testcase name="test_launcher"><error message="no pytest result"/></testcase></testsuite></testsuites>',
            encoding="utf-8")
    counts, rows = outcomes((args.directory/"pytest.xml").read_bytes())
    source_unchanged = source_identity(args) == identity and source_hashes() == hashes
    write_json(args.directory/"results.json", {"identity": identity, "counts": counts, "tests": rows,
        "pytest_exit_code": exit_code, "additional_errors": extra_errors, "source_unchanged": source_unchanged})
    print(json.dumps(counts, sort_keys=True))
    require(exit_code == 0 and not extra_errors and source_unchanged, "test run or source verification failed")
    require_pass(counts, rows)


def seal(args):
    identity = source_identity(args)
    require({p.name for p in args.directory.iterdir() if p.is_file()} == PAYLOAD_FILES, "incomplete/unexpected evidence files")
    source = json.loads((args.directory/"source.json").read_text(encoding="utf-8"))
    results = json.loads((args.directory/"results.json").read_text(encoding="utf-8"))
    require(source["identity"] == results["identity"] == identity, "evidence source/run mismatch")
    require(source["files"] == source_hashes(), "evidence source bytes changed")
    manifest = {"schema": "amasi.review-v2.evidence.v1", "identity": identity,
                "files": {name: sha256((args.directory/name).read_bytes()) for name in sorted(PAYLOAD_FILES)}}
    args.payload.parent.mkdir(parents=True, exist_ok=True)
    require(not args.payload.exists(), "evidence package already exists")
    with zipfile.ZipFile(args.payload, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name in sorted(PAYLOAD_FILES):
            archive.write(args.directory/name, name)
        archive.writestr("manifest.json", json.dumps(manifest, sort_keys=True, indent=2))
    digest = sha256(args.payload.read_bytes())
    print("evidence.zip SHA256 " + digest)
    if args.output:
        with args.output.open("a", encoding="utf-8") as handle:
            handle.write("sha256=" + digest + "\n")


def verify(args):
    identity = source_identity(args)
    environment_proof = environment_preflight()
    require(args.artifact_id.isdigit(), "upload must return a concrete Artifact ID")
    require(re.fullmatch(r"[a-f0-9]{64}", args.sha256 or ""), "original archive SHA256 required")
    require(args.payload.is_file() and args.payload.stat().st_size <= 128*1024*1024, "missing/oversized readback")
    require(sha256(args.payload.read_bytes()) == args.sha256, "downloaded archive SHA256 mismatch")
    with zipfile.ZipFile(args.payload) as archive:
        names = archive.namelist()
        require(len(names) == len(set(names)) and set(names) == PAYLOAD_FILES | {"manifest.json"}, "archive members differ")
        require(sum(info.file_size for info in archive.infolist()) <= 128*1024*1024, "unpacked evidence too large")
        manifest = json.loads(archive.read("manifest.json"))
        require(manifest.get("schema") == "amasi.review-v2.evidence.v1" and manifest["identity"] == identity,
                "manifest HEAD/TREE/run/attempt mismatch")
        require(set(manifest["files"]) == PAYLOAD_FILES, "manifest file list differs")
        for name, digest in manifest["files"].items():
            require(sha256(archive.read(name)) == digest, "readback file digest mismatch: " + name)
        source = json.loads(archive.read("source.json")); results = json.loads(archive.read("results.json"))
        require(source["identity"] == results["identity"] == identity, "embedded source/run identity differs")
        require(source["files"] == source_hashes(), "readback source fingerprints differ")
        counts, rows = outcomes(archive.read("pytest.xml"))
        require(counts == results["counts"] and rows == results["tests"], "XML and stored results differ")
        require(results["pytest_exit_code"] == 0 and not results["additional_errors"] and results["source_unchanged"],
                "readback records failed execution")
        require_pass(counts, rows)
        web_results = json.loads(archive.read("web-results.json"))
        require_web_pass(web_results, identity)
        environment = json.loads(archive.read("environment.json"))
        require_network_proof(environment["network_namespace"])
        require(environment["mongo"] == "8.0.12" and environment["PRIMARY"] is True
                and environment["replica_set"] == "readbackV2Candidate" and environment["dotenv_disabled"] is True
                and environment["production_credentials_copied"] is False, "isolated environment evidence differs")
        require(environment.get("dotenv_preflight") == web_results.get("acceptance_environment") == environment_proof,
                "dotenv source preflight/readback differs")
    print(json.dumps({"status": "PASS", "artifact_id": args.artifact_id, "archive_sha256": args.sha256,
                      "identity": identity, "counts": counts}, sort_keys=True))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("environment", "requirements", "web", "test", "seal", "verify"))
    parser.add_argument("--directory", type=Path)
    parser.add_argument("--web-directory", type=Path)
    parser.add_argument("--payload", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--sha", default=os.environ.get("GITHUB_SHA", ""))
    parser.add_argument("--repository", default=os.environ.get("GITHUB_REPOSITORY", ""))
    parser.add_argument("--run-id", default=os.environ.get("GITHUB_RUN_ID", ""))
    parser.add_argument("--run-attempt", default=os.environ.get("GITHUB_RUN_ATTEMPT", ""))
    parser.add_argument("--sha256")
    parser.add_argument("--artifact-id", default="")
    parser.add_argument("--network-namespace", default="")
    parser.add_argument("--host-network-namespace", default="")
    parser.add_argument("--runner-uid", type=int)
    parser.add_argument("--runner-gid", type=int)
    args = parser.parse_args()
    os.chdir(ROOT)
    return {"environment": lambda _: print(json.dumps(environment_preflight(), sort_keys=True)),
            "requirements": requirements, "web": run_web, "test": run_tests, "seal": seal, "verify": verify}[args.mode](args)


if __name__ == "__main__":
    main()
