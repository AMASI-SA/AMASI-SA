#!/usr/bin/env python3
"""Isolated Linux rehearsal of the proposed access log format; no production IO.

Requires an installed nginx binary. All HTTP traffic is loopback and nginx uses
an ephemeral prefix/config/PID. Only sanitized access rows survive the run.
"""
from __future__ import annotations

import argparse
import hashlib
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import math
import os
from pathlib import Path
import platform
import shutil
import socket
import subprocess
import tempfile
import threading
import time


CANARIES = ("TOKEN_REHEARSAL_PRIVATE", "CUSTOMER_REHEARSAL_PRIVATE", "INVOICE_REHEARSAL_PRIVATE")


class Upstream(BaseHTTPRequestHandler):
    def do_GET(self):
        time.sleep(0.4 if self.headers.get("X-Rehearsal-Delay") == "slow" else 0.05)
        try:
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"ok")
        except (BrokenPipeError, ConnectionResetError):
            pass  # Expected when nginx times out the deliberately slow request.

    def log_message(self, *_args):
        pass


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def run():
    if platform.system() != "Linux":
        return {"status": "SKIP", "reason": "Linux nginx rehearsal required", "platform": platform.system()}
    nginx = shutil.which("nginx")
    if not nginx:
        raise RuntimeError("nginx is required on the Linux test runner")
    source = Path(__file__).resolve().parents[1] / "ops/observability/nginx-timing.example.conf"
    source_bytes = source.read_bytes()
    version = subprocess.run([nginx, "-V"], capture_output=True, text=True, check=True)
    upstream = ThreadingHTTPServer(("127.0.0.1", 0), Upstream)
    thread = threading.Thread(target=upstream.serve_forever, daemon=True)
    thread.start()
    nginx_process = None
    try:
        with tempfile.TemporaryDirectory(prefix="phase1-nginx-") as tmp:
            root = Path(tmp)
            port = free_port()
            config = root / "nginx.conf"
            # Copy the exact reviewed map/log_format, including review comments.
            (root / "timing.conf").write_bytes(source_bytes)
            config.write_text(
                "worker_processes 1;\npid nginx.pid;\nerror_log /dev/null;\n"
                "events { worker_connections 32; }\nhttp {\n"
                f'include "{root / "timing.conf"}";\n'
                f'access_log "{root / "access.jsonl"}" phase1;\n'
                "server {\n"
                f"listen 127.0.0.1:{port};\n"
                "location / {\nproxy_read_timeout 150ms;\nproxy_connect_timeout 1s;\n"
                "proxy_next_upstream off;\n"
                f"proxy_pass http://127.0.0.1:{upstream.server_port};\n"
                "}\n}\n}\n", encoding="utf-8")
            argv = [nginx, "-p", str(root) + os.sep, "-c", str(config)]
            tested = subprocess.run(argv + ["-t"], capture_output=True, text=True, check=True)
            nginx_process = subprocess.Popen(argv + ["-g", "daemon off;"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            deadline = time.monotonic() + 5
            while True:
                if nginx_process.poll() is not None:
                    raise RuntimeError("isolated nginx exited during startup")
                try:
                    with socket.create_connection(("127.0.0.1", port), timeout=0.1):
                        break
                except OSError:
                    if time.monotonic() >= deadline:
                        raise RuntimeError("isolated nginx startup timed out")
                    time.sleep(0.02)
            statuses = []
            for delay in ("normal", "slow"):
                conn = http.client.HTTPConnection("127.0.0.1", port, timeout=3)
                try:
                    conn.request("GET", f"/api/live?token={CANARIES[0]}&customer={CANARIES[1]}", headers={
                        "Authorization": "Bearer " + CANARIES[0],
                        "X-Invoice": CANARIES[2], "X-Rehearsal-Delay": delay,
                    })
                    response = conn.getresponse()
                    statuses.append(response.status)
                    response.read()
                finally:
                    conn.close()
            if statuses != [200, 504]:
                raise AssertionError(f"unexpected statuses: {statuses}")
            log_path = root / "access.jsonl"
            log_deadline = time.monotonic() + 2
            while not log_path.exists() or len(log_path.read_text(encoding="utf-8").splitlines()) < 2:
                if time.monotonic() >= log_deadline:
                    raise AssertionError("nginx did not flush both unbuffered access records")
                time.sleep(0.02)
            nginx_process.terminate()
            nginx_process.wait(timeout=5)
            nginx_process = None
            raw = log_path.read_text(encoding="utf-8")
            if any(canary in raw for canary in CANARIES):
                raise AssertionError("sensitive canary leaked into timing log")
            rows = [json.loads(line) for line in raw.splitlines()]
            allowed = {"at", "route", "status", "request_seconds", "upstream_seconds", "upstream_connect_seconds", "upstream_header_seconds"}
            if len(rows) != 2 or [row["status"] for row in rows] != [200, 504]:
                raise AssertionError("expected exactly the success and timeout access rows")
            for row in rows:
                if set(row) != allowed or row["route"] != "health":
                    raise AssertionError("timing log schema or constant route changed")
                for key in ("request_seconds", "upstream_seconds", "upstream_connect_seconds"):
                    number = float(row[key])
                    if not math.isfinite(number) or number < 0:
                        raise AssertionError(f"invalid {key}")
            if float(rows[0]["upstream_header_seconds"]) < 0.04:
                raise AssertionError("successful request did not record injected delay")
            if rows[1]["upstream_header_seconds"] != "-" or float(rows[1]["request_seconds"]) < 0.12:
                raise AssertionError("timeout timing did not capture missing upstream header")
            return {"status": "PASS", "platform": platform.platform(), "nginx_build": (version.stdout + version.stderr).strip(),
                    "config_test": "PASS" if tested.returncode == 0 else "FAIL",
                    "source_sha256": hashlib.sha256(source_bytes).hexdigest(),
                    "scope": "isolated loopback only; no production configuration changed",
                    "injected_success_delay_ms": 50, "proxy_read_timeout_ms": 150,
                    "injected_timeout_delay_ms": 400, "requests": 2,
                    "privacy_canaries_absent": True, "access_rows": rows}
    finally:
        if nginx_process is not None:
            nginx_process.terminate()
            try:
                nginx_process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                nginx_process.kill()
                nginx_process.wait(timeout=5)
        upstream.shutdown()
        upstream.server_close()
        thread.join(timeout=2)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        evidence = run()
    except Exception as exc:
        evidence = {"status": "FAIL", "reason": type(exc).__name__ + ": " + str(exc)}
    rendered = json.dumps(evidence, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 1 if evidence["status"] == "FAIL" else 0


if __name__ == "__main__":
    raise SystemExit(main())
