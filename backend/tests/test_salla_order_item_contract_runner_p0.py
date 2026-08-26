from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

ROOT = Path(__file__).resolve().parents[2]
RUNNER_PATH = ROOT / "scripts" / "research" / "salla_order_item_contract_runner.py"
SPEC = importlib.util.spec_from_file_location("salla_p0_runner", RUNNER_PATH)
runner = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
sys.modules[SPEC.name] = runner
SPEC.loader.exec_module(runner)

STATES = ("pending", "under_review", "in_progress", "paid", "partially_paid", "completed", "cancelled")
KINDS = ("simple", "size_color_variant", "text_option", "checkbox_yes_no", "multi_quantity", "replacement")


def seed_data(complete: bool = True) -> dict:
    orders = [{"state": state, "order_id": f"o{i}", "order_number": f"N{i}", "item_id": f"i{i}", "product_id": "p0", "sku": "SKU0", "payment_method": "demo", "branch_id": "b1"} for i, state in enumerate(STATES)]
    products = []
    for i, kind in enumerate(KINDS):
        row = {"kind": kind, "product_id": f"p{i}", "sku": f"SKU{i}", "branch_id": "b1"}
        if kind == "size_color_variant":
            row.update(variant_ids=["v1"], option_ids=["op1", "op2"], value_ids=["x", "y"])
        if kind in {"text_option", "checkbox_yes_no"}:
            row["option_ids"] = [f"op{i}"]
        if kind == "checkbox_yes_no":
            row["value_ids"] = ["yes", "no"]
        products.append(row)
    if not complete:
        orders = [{"state": state} for state in STATES]
    return {"classification": "SANDBOX_SEED_MANIFEST", "store_id": "demo-1", "orders": orders, "products": products}


def configured(tmp: Path, *, complete: bool = True, **overrides: str) -> tuple[runner.SandboxConfig, dict]:
    seed = seed_data(complete)
    manifest = tmp / "seed.json"
    manifest.write_text(json.dumps(seed), encoding="utf-8")
    env = {
        "SALLA_SANDBOX_BASE_URL": runner.OFFICIAL_BASE_URL,
        "SALLA_SANDBOX_ACCESS_TOKEN": "demo-token-secret",
        "SALLA_DEMO_STORE_ID": "demo-1",
        "SALLA_DEMO_TOKEN_SCOPES": "orders.read_write,products.read_write",
        "SALLA_SANDBOX_SEED_MANIFEST": str(manifest),
        "SALLA_SANDBOX_EVIDENCE_DIR": str(tmp / "evidence"),
        "SALLA_DEMO_STORE_CONFIRMED": "true",
        "SALLA_SANDBOX_RUN_WRITES": "false",
        "SALLA_ACCESS_TOKEN": "production-must-not-be-read",
        "SALLA_API_BASE_URL": "https://production.invalid",
    }
    env.update(overrides)
    return runner.SandboxConfig.from_env(env), seed


def ok(data):
    return {"status": 200, "body": {"success": True, "data": data}}


def readiness_transport(seed: dict, store_id: str = "demo-1") -> runner.MockTransport:
    responses = {("GET", "/store/info"): ok({"id": store_id, "type": "demo"})}
    for row in seed.get("orders", []):
        if row.get("order_id"):
            responses[("GET", f"/orders/{row['order_id']}")] = ok({"id": row["order_id"], "status": {"slug": row["state"]}})
            responses[("GET", f"/orders/items?order_id={row['order_id']}")] = ok([{"id": row["item_id"], "sku": row["sku"]}])
    for row in seed.get("products", []):
        responses[("GET", f"/products/{row['product_id']}")] = ok({"id": row["product_id"], "sku": row["sku"]})
    return runner.MockTransport(responses)


class RealLikeTransport(runner.MockTransport):
    """Local-only test double used to exercise real-evidence verdict logic."""
    evidence_classification = runner.REAL_EVIDENCE_CLASSIFICATION


class SallaP0RunnerTests(unittest.TestCase):
    def test_missing_config_and_no_production_fallback(self):
        with self.assertRaisesRegex(runner.ContractRunnerError, runner.NOT_CONFIGURED):
            runner.SandboxConfig.from_env({"SALLA_ACCESS_TOKEN": "prod", "SALLA_API_BASE_URL": runner.OFFICIAL_BASE_URL})

    def test_official_host_is_accepted_but_identity_is_required(self):
        with tempfile.TemporaryDirectory() as d:
            config, seed = configured(Path(d))
            self.assertEqual(config.base_url, runner.OFFICIAL_BASE_URL)
            result = runner.readiness(config, readiness_transport(seed, "wrong-store"))
            self.assertEqual(result["status"], "BLOCKED_IDENTITY_MISMATCH")

    def test_store_id_mismatch_with_token_is_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            config, _ = configured(Path(d))
            with self.assertRaisesRegex(runner.ContractRunnerError, runner.IDENTITY_MISMATCH):
                runner.verify_demo_identity(config, runner.MockTransport({("GET", "/store/info"): ok({"id": "other", "type": "demo"})}))

    def test_transactions_and_branches_scopes_are_not_required(self):
        with tempfile.TemporaryDirectory() as d:
            config, seed = configured(Path(d), SALLA_DEMO_TOKEN_SCOPES="orders.read_write,products.read_write")
            result = runner.readiness(config, readiness_transport(seed))
            self.assertEqual(result["status"], "READY_FOR_READ_ONLY")
            self.assertFalse(result["transactions_scope_required"])
            self.assertFalse(result["branches_scope_required"])

    def test_scope_missing_blocks_readiness(self):
        with tempfile.TemporaryDirectory() as d:
            config, seed = configured(Path(d), SALLA_DEMO_TOKEN_SCOPES="orders.read_write")
            self.assertEqual(runner.readiness(config, readiness_transport(seed))["status"], "BLOCKED_SCOPE_MISSING")

    def test_names_only_seed_is_not_ready(self):
        with tempfile.TemporaryDirectory() as d:
            config, seed = configured(Path(d), complete=False)
            self.assertEqual(runner.readiness(config, readiness_transport(seed))["status"], "BLOCKED_FIXTURE_MISSING")

    def test_endpoint_and_unknown_ids_are_rejected(self):
        with self.assertRaisesRegex(runner.ContractRunnerError, runner.ENDPOINT_NOT_ALLOWED):
            runner.validate_endpoint("GET", "/transactions")
        seed = seed_data()
        with self.assertRaisesRegex(runner.ContractRunnerError, "ORDER_ID_NOT_AUTHORIZED"):
            runner.validate_case({"method": "POST", "path": "/orders/items", "order_id": "unknown", "body": {}}, seed)

    def test_actual_snapshot_is_extracted_from_responses(self):
        snapshot = runner.extract_snapshot(
            ok({"id": "o1", "status": {"slug": "paid"}, "amounts": {"total": {"amount": 150}, "paid": {"amount": 100}, "remaining": {"amount": 50}}, "payment": {"status": "partially_paid", "urls": {"checkout": "https://pay.invalid"}}}),
            ok([{"id": "i1", "product_id": "p1", "sku": "SKU1", "quantity": 2, "options": [{"id": "op1"}]}]),
        )
        self.assertEqual(snapshot["order_total"], 150)
        self.assertEqual(snapshot["paid_amount"], 100)
        self.assertEqual(snapshot["outstanding_amount"], 50)
        self.assertEqual(snapshot["items"][0]["quantity"], 2)
        self.assertEqual(snapshot["transactions"], "UNAVAILABLE_SCOPE_NOT_GRANTED")

    def _run_case(self, tmp: Path, write_response: dict, assertions: list[dict] | None = None):
        config, seed = configured(Path(tmp), SALLA_SANDBOX_RUN_WRITES="true")
        base = readiness_transport(seed).responses
        base[("POST", "/orders/items")] = write_response
        transport = RealLikeTransport(base)
        case = {"id": "case-1", "order_id": "o0", "method": "POST", "path": "/orders/items", "body": {"order_id": "o0", "product_id": "p0", "branch_id": "b1", "quantity": 1}, "assertions": assertions or []}
        path = runner.run_case(config, case, transport, runner.EvidenceWriter(Path(tmp) / "evidence"), lambda _: [])
        return json.loads(path.read_text(encoding="utf-8")), transport

    def test_http_200_alone_is_not_pass(self):
        with tempfile.TemporaryDirectory() as d:
            evidence, _ = self._run_case(Path(d), ok({"created": True}))
            self.assertEqual(evidence["verdict"], "INCONCLUSIVE")
            self.assertEqual(evidence["final_verdict_reason"], "HTTP_SUCCESS_WITHOUT_BUSINESS_ASSERTIONS")

    def test_timeout_requires_reconciliation_and_never_repeats_post(self):
        with tempfile.TemporaryDirectory() as d:
            evidence, transport = self._run_case(Path(d), {"status": None, "body": None, "transport_error": "TimeoutError"})
            self.assertEqual(evidence["final_verdict_reason"], "WRITE_OUTCOME_UNKNOWN_RECONCILIATION_REQUIRED")
            self.assertEqual(len([x for x in transport.calls if x[0] == "POST"]), 1)

    def test_correlation_id_is_not_claimed_as_idempotency(self):
        with tempfile.TemporaryDirectory() as d:
            evidence, _ = self._run_case(Path(d), ok({"created": True}))
            self.assertEqual(evidence["idempotency"], "SALLA_IDEMPOTENCY_INCONCLUSIVE")

    def test_mock_stays_not_executed_and_ci_does_not_write(self):
        with tempfile.TemporaryDirectory() as d:
            config, seed = configured(Path(d))
            transport = readiness_transport(seed)
            with self.assertRaisesRegex(runner.ContractRunnerError, "READY_FOR_READ_ONLY"):
                runner.run_case(config, {"id": "blocked"}, transport, runner.EvidenceWriter(Path(d) / "evidence"), lambda _: [])
            self.assertEqual([x for x in transport.calls if x[0] in {"POST", "PUT", "DELETE"}], [])

    def test_sensitive_values_are_redacted(self):
        encoded = json.dumps(runner.sanitize({"Authorization": "Bearer secret-value", "customer": {"mobile": "0500000000"}}))
        self.assertNotIn("secret-value", encoded)
        self.assertNotIn("0500000000", encoded)

    def test_webhooks_require_identity_order_time_and_correlation(self):
        started = datetime.now(timezone.utc)
        base = {"type": "order.updated", "store_id": "demo-1", "order_id": "o1", "correlation_id": "c1", "occurred_at": (started + timedelta(seconds=1)).isoformat()}
        events = [base, {**base, "store_id": "other"}, {**base, "order_id": "other"}, {**base, "correlation_id": "other"}]
        accepted = runner.filter_webhooks(events, store_id="demo-1", order_id="o1", started_at=started, correlation_id="c1")
        self.assertEqual(len(accepted), 1)
        self.assertNotIn("store_id", accepted[0])
        self.assertIn("store_id_hash", accepted[0])


if __name__ == "__main__":
    unittest.main()
