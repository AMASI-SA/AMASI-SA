"""Isolated real-collector transport/storage contracts; no performance verdict.

Only loopback synthetic HTTP is used. This surrogate tests transport contracts,
not runtime instrumentation. The acceptance harness separately covers runtime.
"""
from __future__ import annotations

import argparse
import contextlib
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib.util
import io
import json
import os
from pathlib import Path
import secrets
import socket
import stat
import subprocess
import sys
import tempfile
import threading
import time


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    source = Path(__file__).resolve().parents[1] / "observability_phase1_collector.py"
    spec = importlib.util.spec_from_file_location("real_collector_contract", source)
    collector = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(collector)
    source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    rows = []

    def run(name, fn):
        started = time.monotonic()
        try:
            details = fn() or {}
            rows.append({"name": name, "result": "PASS", "details": details,
                         "elapsed_seconds": time.monotonic() - started})
        except Exception as exc:
            # Never serialize an exception message which could contain payloads.
            rows.append({"name": name, "result": "FAIL", "error_type": type(exc).__name__,
                         "elapsed_seconds": time.monotonic() - started})

    with tempfile.TemporaryDirectory(prefix="obs1316-collector-contract-") as temporary:
        root = Path(temporary)
        token = secrets.token_hex(32)
        sentinel = "synthetic-private-" + secrets.token_hex(16)
        requests = []
        payload = {
            "token": token, "customer": sentinel, "order": sentinel, "invoice": sentinel,
            "memory": {"process_rss_bytes": 42, "raw": sentinel},
            "mongo": {"active_connections": 2, "recent_query_shapes": [sentinel]},
            "phase1": {"enabled": True, "worker": {"pid": 7, "started_at": 123,
                         "cpu_seconds": 0.25, "token": token},
                       "api": {"active": 1, "path": sentinel},
                       "counters": {"api.status.2xx": 9, sentinel: 10},
                       "histograms": {"api.duration.other": {"count": 9, "sum": 0.1,
                           "bounds": [0.1], "buckets": [9, 0], "customer": sentinel},
                           sentinel: {"count": 1}}},
        }

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def do_GET(self):
                correct = self.headers.get("X-Mezan-Diagnostics-Token") == token
                requests.append({"path": self.path, "auth_valid": correct})
                if self.path == "/redirect":
                    self.send_response(302)
                    self.send_header("Location", "/redirect-destination")
                    self.end_headers()
                    return
                if self.path == "/unavailable":
                    self.send_response(503)
                    self.end_headers()
                    return
                if not correct:
                    self.send_response(401)
                    self.end_headers()
                    return
                body = b"x" * (collector.MAX_PAYLOAD + 1) if self.path == "/oversized" else json.dumps(payload).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        origin = f"http://127.0.0.1:{server.server_port}"
        output_capture = io.StringIO()
        try:
            def transport_privacy():
                with contextlib.redirect_stdout(output_capture), contextlib.redirect_stderr(output_capture):
                    sample = collector.fetch(origin, "/api/health/diagnostics", token)
                assert sample["outcome"] == "ok" and sample["status"] == 200
                assert sample["metrics"]["counters"]["api.status.2xx"] == 9
                assert sample["metrics"]["histograms"]["api.duration.other"]["count"] == 9
                assert requests[-1]["auth_valid"]
                storage = root / "private-storage"
                collector.BoundedStore(storage).append(sample)
                saved = (storage / "phase1-0.jsonl").read_text()
                assert token not in saved and sentinel not in saved
                assert "recent_query_shapes" not in saved and "invoice" not in saved
                assert token not in output_capture.getvalue() and sentinel not in output_capture.getvalue()
                assert os.name == "posix", "POSIX permission evidence requires Linux"
                assert stat.S_IMODE(storage.stat().st_mode) == 0o700
                assert stat.S_IMODE((storage / "phase1-0.jsonl").stat().st_mode) == 0o600
                return {"auth_header_confirmed": True, "raw_payload_persisted": False,
                        "secret_logged": False, "directory_mode": "0700", "file_mode": "0600"}
            run("authenticated_transport_sanitize_storage_permissions", transport_privacy)

            def auth_failure():
                before = len(requests)
                sample = collector.fetch(origin, "/api/health/diagnostics", "synthetic-wrong")
                assert sample["outcome"] == "http_error" and sample["status"] == 401
                assert len(requests) - before == 1
                return {"requests": 1, "status": 401, "retries": 0}
            run("unauthorized_no_retry", auth_failure)

            def redirect():
                before = len(requests)
                sample = collector.fetch(origin, "/redirect", token)
                assert sample["outcome"] == "http_error" and sample["status"] == 302
                assert len(requests) - before == 1
                assert not any(r["path"] == "/redirect-destination" for r in requests)
                return {"requests": 1, "followed_redirects": 0}
            run("redirect_rejected", redirect)

            def oversized():
                sample = collector.fetch(origin, "/oversized", token)
                assert sample["outcome"] == "oversized" and "metrics" not in sample
                return {"maximum_response_bytes": collector.MAX_PAYLOAD}
            run("oversized_response_rejected", oversized)

            def unavailable():
                before = len(requests)
                sample = collector.fetch(origin, "/unavailable", token)
                assert sample["outcome"] == "http_error" and sample["status"] == 503
                assert len(requests) - before == 1
                # A bound, non-listening socket gives connection refusal locally.
                with socket.socket() as held:
                    held.bind(("127.0.0.1", 0))
                    missing = collector.fetch(f"http://127.0.0.1:{held.getsockname()[1]}", "/api/health/diagnostics", token)
                assert missing["outcome"] == "unavailable" and "metrics" not in missing
                assert token not in json.dumps(missing)
                return {"http_503_requests": 1, "http_retries": 0, "connection_refusal": "unavailable"}
            run("unavailable_target_bounded_failure", unavailable)

            def disabled():
                before = len(requests)
                inventory = root / "inventory.json"
                inventory.write_text(json.dumps({"workers": {"worker-1": origin}, "health_origin": "https://127.0.0.1"}))
                unused = root / "disabled-output"
                env = dict(os.environ, INTERNAL_DIAGNOSTICS_TOKEN=token, PYTHONDONTWRITEBYTECODE="1")
                completed = subprocess.run([sys.executable, "-B", str(source), "--once", "--inventory", str(inventory),
                                            "--output", str(unused)], capture_output=True, env=env, timeout=5)
                assert completed.returncode == 0 and not unused.exists()
                assert len(requests) == before
                assert not completed.stdout and not completed.stderr
                return {"exit_code": 0, "output_created": False, "requests": 0, "stdout_bytes": 0, "stderr_bytes": 0}
            run("disabled_real_cli_no_output_or_requests", disabled)
        finally:
            server.shutdown()
            server.server_close()
            worker.join(timeout=2)

        def storage_bounds():
            assert collector.FILE_BYTES == 4 * 1024 * 1024 and collector.MAX_FILES == 8
            assert collector.RETENTION_SECONDS == 7 * 86400
            directory = root / "rotation"
            store = collector.BoundedStore(directory)
            # Numeric synthetic fixtures, not raw customer payloads. Exceed 32MiB
            # with the production constants unchanged, then inspect all files.
            record = {"numeric_fixture": [123456789] * 6000}
            for n in range(620):
                store.append({**record, "sequence": n})
            files = sorted(directory.glob("phase1-*.jsonl"))
            assert len(files) == 8
            sizes = [p.stat().st_size for p in files]
            assert max(sizes) <= collector.FILE_BYTES
            assert sum(sizes) <= 32 * 1024 * 1024
            assert all(stat.S_IMODE(p.stat().st_mode) == 0o600 for p in files)
            return {"files": len(files), "file_sizes": sizes, "total_bytes": sum(sizes),
                    "configured_limit_bytes": 32 * 1024 * 1024, "constants_modified": False}
        run("real_32mib_eight_file_rotation", storage_bounds)

        def retention():
            directory = root / "expiry"
            store = collector.BoundedStore(directory)
            old = directory / "phase1-7.jsonl"
            old.write_text(json.dumps({"_stored_at": time.time() - 8 * 86400, "old_fixture": True}) + "\n")
            os.utime(old, None)
            store.append({"numeric_fixture": 1})
            assert not old.exists()
            active = directory / "phase1-0.jsonl"
            active.write_text(json.dumps({"_stored_at": time.time() - 2 * 86400, "previous_day": True}) + "\n")
            active.chmod(0o600)
            store.append({"current_day": True})
            assert (directory / "phase1-1.jsonl").exists()
            return {"expired_despite_recent_mtime": True, "daily_rotation": True,
                    "expiry_runs_on_append_only": True, "inactive_store_cleanup_provided": False}
        run("seven_day_expiry_and_daily_rotation", retention)

    unchanged = hashlib.sha256(source.read_bytes()).hexdigest() == source_hash
    rows.append({"name": "collector_source_unchanged", "result": "PASS" if unchanged else "FAIL"})
    report = {"scope": "synthetic_loopback_collector_contract_only", "platform": sys.platform,
              "collector_sha256": source_hash, "diagnostic_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
              "tests": rows, "pass": sum(r["result"] == "PASS" for r in rows),
              "fail": sum(r["result"] == "FAIL" for r in rows), "skip": 0,
              "limitations": ["Not runtime instrumentation or performance evidence.",
                              "Retention cleanup occurs on append; stopped collector needs external cleanup.",
                              "Permissions tested on newly created storage, not a preexisting unsafe directory."]}
    report["result"] = "FAIL" if report["fail"] else "PASS"
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: report[k] for k in ("result", "pass", "fail", "skip")}))
    return 1 if report["fail"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
