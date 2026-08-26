"""Opt-in Salla Sandbox contract runner for MZ-ORDER-REVISION-SALLA-001 P0.

This is research tooling, not application code. It never reads production
credential variables and refuses all writes unless the explicit Sandbox write
gate is enabled. Evidence is sanitized before it is written to disk.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable


NOT_CONFIGURED = "SALLA_SANDBOX_NOT_CONFIGURED"
WRITES_DISABLED = "SALLA_SANDBOX_WRITES_DISABLED"
FIXTURE_CLASSIFICATION = "MOCK_CONTRACT_FIXTURE"
REAL_EVIDENCE_CLASSIFICATION = "SALLA_SANDBOX_EVIDENCE"
PRODUCTION_HOSTS = {"api.salla.dev", "salla.dev", "www.salla.dev"}
SENSITIVE_KEYS = {
    "access_token", "authorization", "bearer", "card", "customer", "email",
    "mobile", "name", "phone", "receiver", "refresh_token", "token",
}


class ContractRunnerError(RuntimeError):
    pass


@dataclass(frozen=True)
class SandboxConfig:
    base_url: str
    access_token: str
    store_id: str
    seed_manifest: Path
    evidence_dir: Path
    writes_enabled: bool

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> "SandboxConfig":
        source = env if env is not None else os.environ
        required = {
            "base_url": source.get("SALLA_SANDBOX_BASE_URL", "").strip(),
            "access_token": source.get("SALLA_SANDBOX_ACCESS_TOKEN", "").strip(),
            "store_id": source.get("SALLA_SANDBOX_STORE_ID", "").strip(),
            "seed_manifest": source.get("SALLA_SANDBOX_SEED_MANIFEST", "").strip(),
            "evidence_dir": source.get("SALLA_SANDBOX_EVIDENCE_DIR", "").strip(),
        }
        if any(not value for value in required.values()):
            raise ContractRunnerError(NOT_CONFIGURED)
        parsed = urllib.parse.urlparse(required["base_url"])
        if parsed.scheme != "https" or not parsed.hostname:
            raise ContractRunnerError("SALLA_SANDBOX_BASE_URL_INVALID")
        if parsed.hostname.casefold() in PRODUCTION_HOSTS:
            raise ContractRunnerError("SALLA_PRODUCTION_ENDPOINT_FORBIDDEN")
        return cls(
            base_url=required["base_url"].rstrip("/"),
            access_token=required["access_token"],
            store_id=required["store_id"],
            seed_manifest=Path(required["seed_manifest"]),
            evidence_dir=Path(required["evidence_dir"]),
            writes_enabled=source.get("SALLA_SANDBOX_RUN_WRITES", "").casefold() == "true",
        )


def _is_sensitive(key: str) -> bool:
    normalized = re.sub(r"[^a-z0-9]+", "_", key.casefold()).strip("_")
    return normalized in SENSITIVE_KEYS or any(
        token in normalized for token in ("secret", "password", "authorization", "token")
    )


def sanitize(value: Any, *, key: str = "") -> Any:
    if key and _is_sensitive(key):
        return "[REDACTED]"
    if isinstance(value, dict):
        return {str(k): sanitize(v, key=str(k)) for k, v in value.items()}
    if isinstance(value, list):
        return [sanitize(item) for item in value]
    if isinstance(value, str):
        value = re.sub(r"(?i)bearer\s+[a-z0-9._~+/=-]+", "Bearer [REDACTED]", value)
        value = re.sub(r"(?i)(access_token|refresh_token|token)=([^&\s]+)", r"\1=[REDACTED]", value)
    return value


def load_seed_manifest(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("classification") != "SANDBOX_SEED_MANIFEST":
        raise ContractRunnerError("SALLA_SANDBOX_SEED_MANIFEST_INVALID")
    return data


class HttpTransport:
    evidence_classification = REAL_EVIDENCE_CLASSIFICATION

    def __init__(self, config: SandboxConfig):
        self.config = config

    def request(self, method: str, path: str, body: dict[str, Any] | None, client_request_id: str) -> dict[str, Any]:
        url = f"{self.config.base_url}/{path.lstrip('/')}"
        payload = None if body is None else json.dumps(body).encode("utf-8")
        request = urllib.request.Request(
            url,
            data=payload,
            method=method,
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.config.access_token}",
                "X-Client-Request-Id": client_request_id,
                "User-Agent": "Mezan-Salla-P0-Contract-Runner/1",
            },
        )
        started = time.monotonic()
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                raw = response.read().decode("utf-8", errors="replace")
                parsed = json.loads(raw) if raw else None
                return {"status": response.status, "body": parsed, "elapsed_ms": round((time.monotonic() - started) * 1000)}
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8", errors="replace")
            try:
                parsed = json.loads(raw) if raw else None
            except json.JSONDecodeError:
                parsed = {"unparsed": raw[:2000]}
            return {"status": exc.code, "body": parsed, "elapsed_ms": round((time.monotonic() - started) * 1000)}


class MockTransport:
    """Deterministic local transport. Its output can never count as real evidence."""

    evidence_classification = FIXTURE_CLASSIFICATION

    def __init__(self):
        self.calls: list[tuple[str, str, str]] = []
        self.responses: dict[tuple[str, str], dict[str, Any]] = {}

    def request(self, method: str, path: str, body: dict[str, Any] | None, client_request_id: str) -> dict[str, Any]:
        self.calls.append((method, path, client_request_id))
        return self.responses.get((method, path), {"status": 200, "body": {"success": True, "data": []}, "elapsed_ms": 1})


def operation_fingerprint(case_id: str, method: str, path: str, body: dict[str, Any] | None) -> str:
    encoded = json.dumps([case_id, method, path, body], sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


class EvidenceWriter:
    def __init__(self, directory: Path):
        self.directory = directory

    def write(self, evidence: dict[str, Any]) -> Path:
        self.directory.mkdir(parents=True, exist_ok=True)
        case_id = re.sub(r"[^A-Za-z0-9_.-]", "_", str(evidence["test_case_id"]))
        path = self.directory / f"{case_id}-{uuid.uuid4().hex[:10]}.json"
        path.write_text(json.dumps(sanitize(evidence), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return path


def build_evidence(*, case: dict[str, Any], classification: str, before: dict[str, Any], request: dict[str, Any], response: dict[str, Any], after: dict[str, Any], webhooks: list[dict[str, Any]], retry: dict[str, Any] | None, verdict: str) -> dict[str, Any]:
    if classification != REAL_EVIDENCE_CLASSIFICATION and verdict == "PASS":
        verdict = "NOT_EXECUTED"
    return {
        "classification": classification,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "test_case_id": case["id"],
        "order_status_before": before.get("status"),
        "request": request,
        "response_status": response.get("status"),
        "response_body": response.get("body"),
        "source_item_id_before": before.get("source_item_id"),
        "source_item_id_after": after.get("source_item_id"),
        "order_total_before": before.get("order_total"),
        "order_total_after": after.get("order_total"),
        "payment_state_before": before.get("payment_state"),
        "payment_state_after": after.get("payment_state"),
        "webhook_events": webhooks,
        "retry_behavior": retry,
        "final_fetch_result": after,
        "verdict": verdict,
    }


def ensure_replacement_order(steps: list[dict[str, Any]]) -> None:
    actions = [str(step.get("action")) for step in steps]
    if "delete_old" in actions:
        delete_index = actions.index("delete_old")
        required = {"create_replacement", "fetch_confirm_replacement"}
        if not required.issubset(set(actions[:delete_index])):
            raise ContractRunnerError("DELETE_FIRST_FORBIDDEN")


def retry_once(transport: Any, method: str, path: str, body: dict[str, Any] | None, client_request_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
    first = transport.request(method, path, body, client_request_id)
    second = transport.request(method, path, body, client_request_id)
    return first, {"same_client_request_id": True, "second_response": second}


def readiness(config: SandboxConfig) -> dict[str, Any]:
    manifest = load_seed_manifest(config.seed_manifest)
    required_states = {"pending", "under_review", "in_progress", "paid", "partially_paid", "completed", "cancelled"}
    provided = {str(row.get("state")) for row in manifest.get("orders", [])}
    missing = sorted(required_states - provided)
    return {
        "status": "READY" if not missing else "BLOCKED",
        "writes_enabled": config.writes_enabled,
        "store_id_present": bool(config.store_id),
        "missing_order_states": missing,
        "product_fixture_count": len(manifest.get("products", [])),
    }


def run_case(config: SandboxConfig, case: dict[str, Any], transport: Any, writer: EvidenceWriter, webhook_loader: Callable[[str], list[dict[str, Any]]]) -> Path:
    if not config.writes_enabled:
        raise ContractRunnerError(WRITES_DISABLED)
    method, path = case["method"], case["path"]
    client_request_id = case.get("client_request_id") or f"p0-{case['id']}-{uuid.uuid4().hex}"
    before = transport.request("GET", case["before_fetch_path"], None, client_request_id)
    if case.get("simulate_lost_response"):
        first = transport.request(method, path, case.get("body"), client_request_id)
        retry = {
            "same_client_request_id": True,
            "simulated_timeout_after_write": True,
            "commercial_write_repeated_after_timeout": False,
            "required_recovery": "read_after_write_reconciliation",
        }
    elif case.get("retry_once"):
        first, retry = retry_once(transport, method, path, case.get("body"), client_request_id)
    else:
        first = transport.request(method, path, case.get("body"), client_request_id)
        retry = {"attempted": False, "reason": "case_did_not_opt_in"}
    after = transport.request("GET", case["after_fetch_path"], None, client_request_id)
    evidence = build_evidence(
        case=case,
        classification=getattr(transport, "evidence_classification", FIXTURE_CLASSIFICATION),
        before=case.get("before_summary", {}),
        request={"method": method, "path": path, "body": case.get("body"), "client_request_id": client_request_id},
        response=first,
        after={**case.get("after_summary", {}), "fetch": after},
        webhooks=webhook_loader(case["id"]),
        retry=retry,
        verdict="PASS" if first.get("status", 500) < 400 else "FAIL",
    )
    return writer.write(evidence)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("readiness", "run"))
    parser.add_argument("--case-file", type=Path)
    parser.add_argument("--webhook-events", type=Path)
    args = parser.parse_args(argv)
    try:
        config = SandboxConfig.from_env()
        if args.command == "readiness":
            print(json.dumps(readiness(config), ensure_ascii=False, indent=2))
            return 0
        if not args.case_file:
            raise ContractRunnerError("SALLA_SANDBOX_CASE_FILE_REQUIRED")
        case = json.loads(args.case_file.read_text(encoding="utf-8"))
        ensure_replacement_order(case.get("steps", []))
        events = []
        if args.webhook_events and args.webhook_events.exists():
            events = json.loads(args.webhook_events.read_text(encoding="utf-8"))
        path = run_case(config, case, HttpTransport(config), EvidenceWriter(config.evidence_dir), lambda case_id: [row for row in events if row.get("test_case_id") == case_id])
        print(json.dumps({"status": "EVIDENCE_WRITTEN", "path": str(path)}, ensure_ascii=False))
        return 0
    except ContractRunnerError as exc:
        print(str(exc))
        return 2


if __name__ == "__main__":
    sys.exit(main())
