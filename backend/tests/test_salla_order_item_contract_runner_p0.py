from __future__ import annotations

import asyncio
import contextlib
import importlib.util
import io
import inspect
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
import time
import types
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

SCHEMA_INDEPENDENT_VALIDATION_PASS = "SCHEMA_INDEPENDENT_VALIDATION=PASS"
FULL_DRAFT202012_VALIDATION_BLOCKED = "FULL_DRAFT202012_VALIDATION=BLOCKED_MISSING_DEPENDENCY"
try:
    from jsonschema import Draft202012Validator
except ModuleNotFoundError:
    Draft202012Validator = None


def require_draft202012_validator():
    if Draft202012Validator is None:
        raise RuntimeError(FULL_DRAFT202012_VALIDATION_BLOCKED)
    return Draft202012Validator

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
            row.update(
                variant_ids=["v1", "v2"],
                option_ids=["op1", "op2"],
                value_ids=["x", "y"],
                option_value_tuples=[
                    {"option_id": "op1", "value_id": "x"},
                    {"option_id": "op2", "value_id": "y"},
                ],
                variant_tuples=[
                    {"product_id": "p1", "variant_id": "v1", "sku": "SKU1-V1", "option_value_tuples": [{"option_id": "op1", "value_id": "x"}, {"option_id": "op2", "value_id": "y"}]},
                    {"product_id": "p1", "variant_id": "v2", "sku": "SKU1-V2", "option_value_tuples": [{"option_id": "op1", "value_id": "x"}]},
                ],
            )
        if kind in {"text_option", "checkbox_yes_no"}:
            row["option_ids"] = [f"op{i}"]
            row["option_value_tuples"] = [{"option_id": f"op{i}", "value_kind": "text"}]
        if kind == "checkbox_yes_no":
            row["value_ids"] = ["yes", "no"]
            row["option_value_tuples"] = [
                {"option_id": f"op{i}", "value_id": "yes"},
                {"option_id": f"op{i}", "value_id": "no"},
            ]
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
            responses[("GET", f"/orders/items?order_id={row['order_id']}")] = ok([{"id": row["item_id"], "product_id": row["product_id"], "sku": row["sku"]}])
    for row in seed.get("products", []):
        option_rows = []
        for option_id in row.get("option_ids", []):
            values = [
                {"id": pair["value_id"]}
                for pair in row.get("option_value_tuples", [])
                if pair.get("option_id") == option_id and "value_id" in pair
            ]
            option_rows.append(
                {
                    "id": option_id,
                    "type": "text" if any(
                        pair.get("option_id") == option_id and pair.get("value_kind") == "text"
                        for pair in row.get("option_value_tuples", [])
                    ) else "select",
                    "values": values,
                }
            )
        responses[("GET", f"/products/{row['product_id']}")] = ok(
            {
                "id": row["product_id"],
                "sku": row["sku"],
                "variants": [
                    {
                        "id": variant["variant_id"],
                        "sku": variant["sku"],
                        "options": variant["option_value_tuples"],
                    }
                    for variant in row.get("variant_tuples", [])
                ],
                "options": option_rows,
            }
        )
    return runner.MockTransport(responses)


class ForgedLiveTransport(runner.MockTransport):
    """Adversarial double: a caller-controlled claim must never become live evidence."""
    evidence_classification = runner.REAL_EVIDENCE_CLASSIFICATION


class FakeCursor:
    def __init__(self, rows: list[dict]):
        self.rows = rows

    def limit(self, _: int):
        return self

    async def to_list(self, length: int):
        return self.rows[:length]


class FakeIntegrations:
    def __init__(self, rows: list[dict]):
        self.rows = rows
        self.query = None
        self.projection = None

    def find(self, query: dict, projection: dict):
        self.query = query
        self.projection = projection
        return FakeCursor(self.rows)


class FakeDb:
    def __init__(self, rows: list[dict]):
        self.salla_integrations = FakeIntegrations(rows)


class FakeMongoClient:
    def __init__(self, db: FakeDb):
        self.db = db
        self.requested_db = None
        self.closed = False

    def __getitem__(self, name: str):
        self.requested_db = name
        return self.db

    def close(self):
        self.closed = True


class FakeHttpResponse:
    def __init__(self, payload: dict):
        self.status = payload["status"]
        self.headers = {}
        self._raw = json.dumps(payload.get("body")).encode("utf-8")

    def read(self):
        return self._raw

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


class FakeSallaHttp:
    """urllib seam for the sealed CLI path; never passed into runner APIs."""

    def __init__(
        self,
        seed: dict,
        *,
        evidence_dir: Path,
        fail_baseline: bool = False,
        post_error: bool = False,
        post_data: dict | None = None,
        after_first_delay: float = 0.0,
        after_first_hook=None,
    ):
        self.responses = readiness_transport(seed).responses
        self.evidence_dir = evidence_dir
        self.fail_baseline = fail_baseline
        self.post_error = post_error
        self.post_data = post_data or {"created": True}
        self.after_first_delay = after_first_delay
        self.after_first_hook = after_first_hook
        self.after_first_hook_called = False
        self.calls: list[tuple[str, str]] = []
        self.authorizations: list[str | None] = []
        self.order_o0_reads = 0
        self.post_seen = False

    def __call__(self, request, timeout):
        self.assert_timeout(timeout)
        method = request.get_method()
        parsed = runner.urllib.parse.urlsplit(request.full_url)
        prefix = runner.urllib.parse.urlsplit(runner.OFFICIAL_BASE_URL).path.rstrip("/")
        path = parsed.path[len(prefix):] or "/"
        if parsed.query:
            path = f"{path}?{parsed.query}"
        self.calls.append((method, path))
        self.authorizations.append(request.get_header("Authorization"))
        if method != "GET":
            intent_files = list(self.evidence_dir.glob("*.intent.json"))
            if len(intent_files) != 1:
                raise AssertionError("immutable intent must exist before write")
            self.post_seen = True
            if self.post_error:
                raise TimeoutError("test-only timeout")
            return FakeHttpResponse(ok(self.post_data))
        if path == "/orders/o0":
            self.order_o0_reads += 1
            if self.fail_baseline and self.order_o0_reads > 1:
                return FakeHttpResponse({"status": 503, "body": None})
        payload = self.responses[(method, path)]
        if path == "/orders/items?order_id=o0" and self.post_seen:
            if self.after_first_hook is not None and not self.after_first_hook_called:
                self.after_first_hook_called = True
                self.after_first_hook()
            if self.after_first_delay:
                time.sleep(self.after_first_delay)
            existing = payload["body"]["data"]
            payload = ok(
                [
                    *existing,
                    {"id": "i-new", "product_id": "p0", "sku": "SKU0", "quantity": 1, "branch_id": "b1"},
                ]
            )
        return FakeHttpResponse(payload)

    @staticmethod
    def assert_timeout(timeout):
        if timeout != 30:
            raise AssertionError("unexpected timeout")


class SallaP0RunnerTests(unittest.TestCase):
    @staticmethod
    def _valid_case(**overrides):
        case = {
            "classification": "SANDBOX_CASE_TEMPLATE",
            "id": "case-1",
            "order_id": "o0",
            "method": "POST",
            "path": "/orders/items",
            "body": {
                "order_id": "o0",
                "product_id": "p0",
                "branch_id": "b1",
                "quantity": 1,
            },
            "assertions": [],
            "retry_once": False,
            "disposable_order_confirmed": False,
            "simulate_lost_response": False,
            "steps": [],
        }
        case.update(overrides)
        return case

    @staticmethod
    def _delete_steps(**overrides):
        common = {
            "order_id": "o0",
            "item_id": "i0",
            "replacement_item_id": "replacement-item-1",
        }
        steps = [
            {
                **common,
                "action": "create_replacement",
                "replacement_product_id": "p5",
                "replacement_sku": "SKU5",
                "response_status": 200,
            },
            {
                **common,
                "action": "fetch_confirm_replacement",
                "replacement_product_id": "p5",
                "replacement_sku": "SKU5",
                "fetch_ok": True,
            },
            {**common, "action": "delete_old"},
        ]
        for step in steps:
            step.update(overrides)
        return steps

    @staticmethod
    def _cli_env(tmp: Path, seed: dict, **overrides: str) -> dict[str, str]:
        manifest = tmp / "seed.json"
        manifest.write_text(json.dumps(seed), encoding="utf-8")
        env = {
            "SALLA_SANDBOX_BASE_URL": runner.OFFICIAL_BASE_URL,
            "SALLA_DEMO_STORE_ID": "demo-1",
            "SALLA_DEMO_TOKEN_SCOPES": "orders.read_write,products.read_write",
            "SALLA_SANDBOX_SEED_MANIFEST": str(manifest),
            "SALLA_SANDBOX_EVIDENCE_DIR": str(tmp / "evidence"),
            "SALLA_DEMO_STORE_CONFIRMED": "true",
            "SALLA_SANDBOX_RUN_WRITES": "true",
            "SALLA_P0_WRITE_APPROVAL_ID": "test-approval-once",
            "SALLA_P0_WRITE_APPROVAL_ISSUED_AT": datetime.now(timezone.utc).isoformat(),
            "MONGO_URL": "mongodb://test-only.invalid",
            "DB_NAME": "test_only",
        }
        env.update(overrides)
        return env

    def test_missing_config_and_no_production_fallback(self):
        with self.assertRaisesRegex(runner.ContractRunnerError, runner.NOT_CONFIGURED):
            runner.SandboxConfig.from_env(
                {
                    "SALLA_ACCESS_TOKEN": "production-must-not-be-read",
                    "SALLA_API_BASE_URL": runner.OFFICIAL_BASE_URL,
                }
            )
        with tempfile.TemporaryDirectory() as d:
            config, _ = configured(
                Path(d),
                SALLA_ACCESS_TOKEN="production-must-not-be-read",
                SALLA_SANDBOX_ACCESS_TOKEN="plaintext-must-not-be-read",
                SALLA_API_BASE_URL=runner.OFFICIAL_BASE_URL,
            )
            self.assertFalse(hasattr(config, "access_token"))

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
            runner.validate_case(
                self._valid_case(
                    order_id="unknown",
                    body={"order_id": "unknown", "product_id": "p0", "branch_id": "b1", "quantity": 1},
                ),
                seed,
            )

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

    def _analyze_case(self, write_response: dict, assertions: list[dict] | None = None):
        seed = seed_data()
        case = self._valid_case(assertions=assertions or [])
        before = {
            "fetch_ok": True,
            "order_id": "o0",
            "items_count": 1,
            "items": [{"item_id": "i0", "product_id": "p0", "sku": "SKU0", "quantity": 1}],
        }
        after = {
            "fetch_ok": True,
            "order_id": "o0",
            "items_count": 2,
            "items": [
                {"item_id": "i0", "product_id": "p0", "sku": "SKU0", "quantity": 1},
                {"item_id": "i-new", "product_id": "p0", "sku": "SKU0", "quantity": 1, "branch_id": "b1"},
            ],
        }
        if write_response.get("transport_error"):
            after = before
        return runner.analyze_attempt(case, seed, before, write_response, after, [])

    def test_http_200_alone_is_not_pass(self):
        evidence = self._analyze_case(ok({"created": True}))
        self.assertEqual(evidence["verdict"], "NOT_EXECUTED")
        self.assertEqual(evidence["final_verdict_reason"], "MOCK_EVIDENCE_CANNOT_PROVE_SALLA_BEHAVIOR")

    def test_timeout_requires_reconciliation_and_never_repeats_post(self):
        evidence = self._analyze_case({"status": None, "body": None, "transport_error": "TimeoutError"})
        self.assertEqual(evidence["attempt_outcome"], "UNKNOWN")
        self.assertEqual(evidence["observed_outcome_reason"], "WRITE_OUTCOME_UNKNOWN_RECONCILIATION_REQUIRED")

    def test_correlation_id_is_not_claimed_as_idempotency(self):
        evidence = self._analyze_case(ok({"created": True}))
        self.assertNotIn("idempotency", evidence)
        self.assertEqual(evidence["verdict"], "NOT_EXECUTED")

    def test_mock_stays_not_executed_and_ci_does_not_write(self):
        with tempfile.TemporaryDirectory() as d:
            config, seed = configured(Path(d))
            transport = readiness_transport(seed)
            with self.assertRaisesRegex(runner.ContractRunnerError, runner.LIVE_EXECUTOR_REQUIRED):
                runner.run_case(config, self._valid_case(id="blocked"), transport, runner.EvidenceWriter(Path(d) / "evidence"), lambda _: [])
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

    def _run_webhook_cli(self, tmp, *, after_write=None, before_events=None, extra_args=("--webhook-wait-seconds", "0")):
        seed = seed_data()
        case_path = tmp / "case.json"
        case_path.write_text(json.dumps(self._valid_case(client_request_id="webhook-case-1")), encoding="utf-8")
        events_path = tmp / "events.json"
        events_path.write_text(json.dumps(before_events or []), encoding="utf-8")
        client = FakeMongoClient(FakeDb([{"user_id": "sandbox-owner"}]))

        async def resolver(*_args, **_kwargs):
            return "resolved-token-secret"

        fake_http = FakeSallaHttp(
            seed, evidence_dir=tmp / "evidence",
            after_first_hook=(lambda: after_write(events_path)) if after_write else None,
        )
        with (
            mock.patch.dict(os.environ, self._cli_env(tmp, seed), clear=True),
            mock.patch.object(runner, "_canonical_state_root", return_value=tmp / "state"),
            mock.patch.object(runner, "_runtime_credential_dependencies", return_value=(lambda _: client, resolver)),
            mock.patch.object(runner.urllib.request, "urlopen", side_effect=fake_http),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            result = runner.main(["run", "--case-file", str(case_path), "--webhook-events", str(events_path), *extra_args])
        evidence = json.loads(next((tmp / "evidence").glob("*.terminal.json")).read_text(encoding="utf-8"))
        self.assertEqual(len([call for call in fake_http.calls if call[0] == "POST"]), 1)
        return result, evidence

    @staticmethod
    def _webhook_event(**overrides):
        return {
            "id": "evt-after-write", "type": "order.products.updated", "store_id": "demo-1",
            "order_id": "o0", "correlation_id": "webhook-case-1",
            "occurred_at": datetime.now(timezone.utc).isoformat(), **overrides,
        }

    def test_cli_captures_webhook_arriving_after_mutation(self):
        with tempfile.TemporaryDirectory() as d:
            result, evidence = self._run_webhook_cli(
                Path(d),
                after_write=lambda path: path.write_text(json.dumps([self._webhook_event()]), encoding="utf-8"),
            )
            self.assertEqual(result, 0)
            self.assertEqual([event["id"] for event in evidence["webhook_events"]], ["evt-after-write"])
            self.assertEqual(evidence["verdict"], "PASS")

    def test_cli_collects_delayed_events_for_full_window_without_duplicates(self):
        with tempfile.TemporaryDirectory() as d:
            clock = [0.0]
            events_path = Path(d) / "events.json"
            first = self._webhook_event()
            delivered = []

            def advance_and_deliver(seconds):
                clock[0] += seconds
                if not delivered:
                    first["occurred_at"] = datetime.now(timezone.utc).isoformat()
                    delivered.append(first)
                else:
                    delivered.append(self._webhook_event(id="evt-later", type="order.updated"))
                events_path.write_text(json.dumps([*delivered, first]), encoding="utf-8")

            with (
                mock.patch.object(runner.time, "monotonic", side_effect=lambda: clock[0]),
                mock.patch.object(runner.time, "sleep", side_effect=advance_and_deliver),
            ):
                result, evidence = self._run_webhook_cli(
                    Path(d), extra_args=("--webhook-wait-seconds", "0.2"),
                )
            self.assertEqual(result, 0)
            self.assertAlmostEqual(clock[0], 0.2)
            self.assertEqual([event["id"] for event in evidence["webhook_events"]], ["evt-after-write", "evt-later"])
            self.assertEqual(evidence["verdict"], "PASS")

    def test_cli_missing_webhook_reaches_bounded_default_without_claiming_complete_pass(self):
        with tempfile.TemporaryDirectory() as d:
            clock = [0.0]

            def advance(seconds):
                self.assertGreater(seconds, 0)
                self.assertLessEqual(seconds, 0.1)
                clock[0] += seconds

            with (
                mock.patch.object(runner.time, "monotonic", side_effect=lambda: clock[0]),
                mock.patch.object(runner.time, "sleep", side_effect=advance),
            ):
                result, evidence = self._run_webhook_cli(Path(d), extra_args=())
            self.assertEqual(result, 0)
            self.assertAlmostEqual(clock[0], 5.0)
            self.assertEqual(evidence["attempt_outcome"], "TERMINAL")
            self.assertEqual(evidence["observed_verdict"], "PASS")
            self.assertEqual(evidence["verdict"], "INCONCLUSIVE")
            self.assertEqual(evidence["final_verdict_reason"], "WEBHOOK_EVIDENCE_NOT_OBSERVED")

    def test_cli_excludes_prewrite_stale_and_future_webhooks(self):
        with tempfile.TemporaryDirectory() as d:
            old = self._webhook_event(id="old", occurred_at=(datetime.now(timezone.utc) - timedelta(days=1)).isoformat())
            future = self._webhook_event(id="future", occurred_at=(datetime.now(timezone.utc) + timedelta(days=1)).isoformat())
            result, evidence = self._run_webhook_cli(
                Path(d), before_events=[old],
                after_write=lambda path: path.write_text(json.dumps([old, {**old, "id": "stale"}, future]), encoding="utf-8"),
            )
            self.assertEqual(result, 0)
            self.assertEqual(evidence["webhook_events"], [])
            self.assertEqual(evidence["verdict"], "INCONCLUSIVE")
            self.assertEqual(evidence["observed_verdict"], "PASS")

    def test_cli_invalid_or_unreadable_postwrite_webhook_source_finishes_unknown(self):
        sources = [
            lambda path: path.write_text("{unfinished", encoding="utf-8"),
            lambda path: path.unlink(),
            *(
                lambda path, override=override: path.write_text(json.dumps([self._webhook_event(**override)]), encoding="utf-8")
                for override in (
                    {"store_id": "other"}, {"order_id": "other"}, {"correlation_id": "other"},
                    {"test_case_id": "other"}, {"type": "unexpected"}, {"occurred_at": "2026-09-16T00:00:00"},
                )
            ),
        ]
        for index, after_write in enumerate(sources):
            with self.subTest(source=index), tempfile.TemporaryDirectory() as d:
                tmp = Path(d)
                result, evidence = self._run_webhook_cli(tmp, after_write=after_write)
                self.assertEqual(result, 2)
                self.assertEqual(evidence["attempt_outcome"], "UNKNOWN")
                self.assertEqual(evidence["verdict"], "INCONCLUSIVE")
                self.assertEqual(evidence["transport_error"], "SALLA_P0_WEBHOOK_SOURCE_INVALID")
                terminal = json.loads(next((tmp / "state").rglob("terminal.json")).read_text(encoding="utf-8"))
                self.assertEqual(terminal["attempt_outcome"], "UNKNOWN")

    def test_cli_webhook_wait_rejects_invalid_bounds_before_credentials(self):
        for value in ("-1", "30.01", "nan", "inf", "invalid"):
            with (
                self.subTest(value=value),
                mock.patch.object(runner, "_runtime_credential_dependencies") as dependencies,
                contextlib.redirect_stderr(io.StringIO()),
                self.assertRaises(SystemExit) as caught,
            ):
                runner.main(["run", "--webhook-wait-seconds", value])
            self.assertEqual(caught.exception.code, 2)
            dependencies.assert_not_called()

    def test_credential_environment_guard_runs_before_client_or_network(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            env = self._cli_env(tmp, seed_data())
            env.pop("MONGO_URL")
            env.pop("DB_NAME")
            output = io.StringIO()
            with (
                mock.patch.dict(os.environ, env, clear=True),
                mock.patch.object(runner, "_runtime_credential_dependencies") as dependencies,
                mock.patch.object(runner.urllib.request, "urlopen") as urlopen,
                contextlib.redirect_stdout(output),
            ):
                result = runner.main(["readiness"])
            self.assertEqual(result, 2)
            self.assertEqual(output.getvalue().strip(), runner.CREDENTIAL_RESOLVER_ENV_UNAVAILABLE)
            dependencies.assert_not_called()
            urlopen.assert_not_called()

    def test_credential_lookup_uses_unique_store_owner_and_service_resolver(self):
        with tempfile.TemporaryDirectory() as d:
            config, _ = configured(Path(d))
            db = FakeDb([{"user_id": "sandbox-owner"}])
            calls = []

            async def resolver(resolved_db, user_id, **kwargs):
                calls.append((resolved_db, user_id, kwargs))
                return "resolved-token-secret"

            token = asyncio.run(
                runner.resolve_sandbox_access_token(config, db, resolver)
            )
            self.assertEqual(token, "resolved-token-secret")
            self.assertEqual(calls[0][0], db)
            self.assertEqual(calls[0][1], "sandbox-owner")
            self.assertFalse(calls[0][2]["recover_needs_reauth"])
            self.assertNotIn("resolved-token-secret", json.dumps(db.salla_integrations.query))

    def test_credential_lookup_accepts_string_or_numeric_store_identity(self):
        with tempfile.TemporaryDirectory() as d:
            config, _ = configured(Path(d), SALLA_DEMO_STORE_ID="123")
            db = FakeDb([{"user_id": "sandbox-owner"}])

            async def resolver(*_args, **_kwargs):
                return "resolved-token-secret"

            asyncio.run(runner.resolve_sandbox_access_token(config, db, resolver))
            self.assertEqual(
                db.salla_integrations.query,
                {"store_id": {"$in": ["123", 123]}},
            )

    def test_credential_lookup_rejects_missing_or_ambiguous_owner(self):
        with tempfile.TemporaryDirectory() as d:
            config, _ = configured(Path(d))

            async def resolver(*_args, **_kwargs):
                self.fail("resolver must not run without one exact owner")

            for rows in ([], [{"user_id": "u1"}, {"user_id": "u2"}]):
                with self.subTest(rows=rows), self.assertRaisesRegex(
                    runner.ContractRunnerError,
                    runner.CREDENTIAL_RESOLVER_IDENTITY_UNAVAILABLE,
                ):
                    asyncio.run(
                        runner.resolve_sandbox_access_token(
                            config,
                            FakeDb(rows),
                            resolver,
                        )
                    )

    def test_credential_resolver_failure_is_sanitized_and_client_closes(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            client = FakeMongoClient(FakeDb([{"user_id": "sandbox-owner"}]))

            async def resolver(*_args, **_kwargs):
                raise RuntimeError("Bearer must-never-escape")

            output = io.StringIO()
            with (
                mock.patch.dict(os.environ, self._cli_env(tmp, seed_data()), clear=True),
                mock.patch.object(runner, "_runtime_credential_dependencies", return_value=(lambda _: client, resolver)),
                mock.patch.object(runner.urllib.request, "urlopen") as urlopen,
                contextlib.redirect_stdout(output),
            ):
                result = runner.main(["readiness"])
            self.assertEqual(result, 2)
            self.assertEqual(output.getvalue().strip(), runner.CREDENTIAL_RESOLVER_FAILED)
            self.assertNotIn("must-never-escape", output.getvalue())
            self.assertTrue(client.closed)
            urlopen.assert_not_called()

    def test_cli_run_reports_add_not_executed_when_resolver_env_missing(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            _, seed = configured(tmp)
            case_path = tmp / "case.json"
            case_path.write_text(json.dumps(self._valid_case(id="blocked")), encoding="utf-8")
            env = {
                "SALLA_SANDBOX_BASE_URL": runner.OFFICIAL_BASE_URL,
                "SALLA_DEMO_STORE_ID": "demo-1",
                "SALLA_DEMO_TOKEN_SCOPES": "orders.read_write,products.read_write",
                "SALLA_SANDBOX_SEED_MANIFEST": str(tmp / "seed.json"),
                "SALLA_SANDBOX_EVIDENCE_DIR": str(tmp / "evidence"),
                "SALLA_DEMO_STORE_CONFIRMED": "true",
                "SALLA_SANDBOX_RUN_WRITES": "true",
            }
            (tmp / "seed.json").write_text(json.dumps(seed), encoding="utf-8")
            output = io.StringIO()
            with mock.patch.dict(os.environ, env, clear=True), contextlib.redirect_stdout(output):
                result = runner.main(["run", "--case-file", str(case_path)])
            self.assertEqual(result, 2)
            self.assertEqual(
                output.getvalue().splitlines(),
                [
                    "ADD_EXECUTED=false",
                    f"REASON={runner.CREDENTIAL_RESOLVER_ENV_UNAVAILABLE}",
                ],
            )

    def test_http_transport_uses_resolved_token_not_environment_token(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            seed = seed_data()
            client = FakeMongoClient(FakeDb([{"user_id": "sandbox-owner"}]))

            async def resolver(*_args, **_kwargs):
                return "resolved-token-secret"

            fake_http = FakeSallaHttp(seed, evidence_dir=tmp / "evidence")
            env = self._cli_env(tmp, seed, SALLA_ACCESS_TOKEN="wrong-production-token")
            output = io.StringIO()
            with (
                mock.patch.dict(os.environ, env, clear=True),
                mock.patch.object(runner, "_runtime_credential_dependencies", return_value=(lambda _: client, resolver)),
                mock.patch.object(runner.urllib.request, "urlopen", side_effect=fake_http),
                contextlib.redirect_stdout(output),
            ):
                result = runner.main(["readiness"])
            self.assertEqual(result, 0)
            self.assertTrue(fake_http.authorizations)
            self.assertTrue(all(value == "Bearer resolved-token-secret" for value in fake_http.authorizations))
            self.assertNotIn("wrong-production-token", "".join(value or "" for value in fake_http.authorizations))
            self.assertTrue(client.closed)

    def test_runner_imports_credential_service_without_server_runtime(self):
        source = RUNNER_PATH.read_text(encoding="utf-8")
        self.assertIn(
            "from salla_integration.service import ensure_fresh_access_token",
            source,
        )
        self.assertNotIn("import server", source)
        self.assertNotIn("from server", source)

    def test_unknown_case_or_body_fields_and_order_mismatch_fail_before_transport_io(self):
        with tempfile.TemporaryDirectory() as d:
            config, seed = configured(Path(d), SALLA_SANDBOX_RUN_WRITES="true")
            invalid_cases = [
                self._valid_case(unexpected=True),
                self._valid_case(body={"order_id": "o0", "product_id": "p0", "branch_id": "b1", "quantity": 1, "unexpected": True}),
                self._valid_case(body={"order_id": "o1", "product_id": "p0", "branch_id": "b1", "quantity": 1}),
            ]
            for case in invalid_cases:
                transport = readiness_transport(seed)
                with self.subTest(case=case), self.assertRaises(runner.ContractRunnerError):
                    runner.run_case(config, case, transport, runner.EvidenceWriter(Path(d) / "evidence"), lambda _: [])
                self.assertEqual(transport.calls, [])

    def test_item_product_variant_sku_option_value_and_branch_are_relationally_seed_bound(self):
        seed = seed_data()
        valid_variant_body = {
            "order_id": "o1",
            "product_id": "p0",
            "branch_id": "b1",
            "quantity": 1,
        }
        invalid = [
            self._valid_case(order_id="o0", method="PUT", path="/orders/items/i1", body={"order_id": "o0", "quantity": 2}),
            self._valid_case(body={"order_id": "o0", "product_id": "p0", "sku": "SKU1", "branch_id": "b1", "quantity": 1}),
            self._valid_case(body={"order_id": "o0", "product_id": "p1", "variant_id": "missing", "branch_id": "b1", "quantity": 1}),
            self._valid_case(body={"order_id": "o0", "product_id": "p1", "branch_id": "b1", "quantity": 1, "options": [{"option_id": "missing", "value_id": "x"}]}),
            self._valid_case(body={"order_id": "o0", "product_id": "p1", "branch_id": "b1", "quantity": 1, "options": [{"option_id": "op1", "value_id": "missing"}]}),
            self._valid_case(body={**valid_variant_body, "branch_id": "other"}),
        ]
        for case in invalid:
            with self.subTest(case=case), self.assertRaises(runner.ContractRunnerError):
                runner.validate_case(case, seed)

    def test_readiness_rejects_seed_option_ids_not_present_in_product_details(self):
        with tempfile.TemporaryDirectory() as d:
            config, seed = configured(Path(d))
            transport = readiness_transport(seed)
            product_path = ("GET", "/products/p1")
            product = transport.responses[product_path]["body"]["data"]
            product["options"] = [{"id": "different", "values": [{"id": "x"}, {"id": "y"}]}]
            self.assertEqual(runner.readiness(config, transport)["status"], "BLOCKED_FIXTURE_MISSING")

    def test_delete_requires_ordered_replacement_proof_bound_to_same_order_and_item(self):
        seed = seed_data()
        delete_case = self._valid_case(
            method="DELETE",
            path="/orders/items/i0",
            body={},
            steps=self._delete_steps(),
        )
        runner.validate_case(delete_case, seed)
        invalid_steps = [
            [],
            self._delete_steps(order_id="o1"),
            self._delete_steps(item_id="i1"),
            self._delete_steps(replacement_item_id="i0"),
            list(reversed(self._delete_steps())),
        ]
        for steps in invalid_steps:
            with self.subTest(steps=steps), self.assertRaises(runner.ContractRunnerError):
                runner.validate_case({**delete_case, "steps": steps}, seed)

    def test_delete_replacement_must_be_visible_in_successful_baseline_before_delete(self):
        case = self._valid_case(method="DELETE", path="/orders/items/i0", body={}, steps=self._delete_steps())
        before = {"fetch_ok": True, "order_id": "o0", "items": [{"item_id": "i0", "product_id": "p0", "sku": "SKU0"}]}
        with self.assertRaisesRegex(runner.ContractRunnerError, "DELETE_REPLACEMENT_NOT_PROVEN"):
            runner._verify_delete_replacement_in_baseline(case, before)

    def test_delete_with_matching_replacement_cannot_execute_via_mock(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            config, seed = configured(tmp, SALLA_SANDBOX_RUN_WRITES="true")
            transport = readiness_transport(seed)
            with self.assertRaisesRegex(runner.ContractRunnerError, runner.LIVE_EXECUTOR_REQUIRED):
                runner.run_case(
                    config,
                    self._valid_case(method="DELETE", path="/orders/items/i0", body={}, steps=self._delete_steps()),
                    transport,
                    runner.EvidenceWriter(tmp / "evidence"),
                    lambda _: [],
                )
            self.assertEqual(transport.calls, [])

    def test_failed_baseline_blocks_write(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            seed = seed_data()
            case_path = tmp / "case.json"
            case_path.write_text(json.dumps(self._valid_case()), encoding="utf-8")
            client = FakeMongoClient(FakeDb([{"user_id": "sandbox-owner"}]))

            async def resolver(*_args, **_kwargs):
                return "resolved-token-secret"

            fake_http = FakeSallaHttp(seed, evidence_dir=tmp / "evidence", fail_baseline=True)
            output = io.StringIO()
            with (
                mock.patch.dict(os.environ, self._cli_env(tmp, seed), clear=True),
                mock.patch.object(runner, "_canonical_state_root", return_value=tmp / "state"),
                mock.patch.object(runner, "_runtime_credential_dependencies", return_value=(lambda _: client, resolver)),
                mock.patch.object(runner.urllib.request, "urlopen", side_effect=fake_http),
                contextlib.redirect_stdout(output),
            ):
                result = runner.main(["run", "--case-file", str(case_path)])
            self.assertEqual(result, 2)
            self.assertIn(runner.BASELINE_FETCH_REQUIRED, output.getvalue())
            self.assertFalse(any(method != "GET" for method, _ in fake_http.calls))
            self.assertEqual(list((tmp / "evidence").glob("*.intent.json")), [])

    def test_malformed_assertions_and_webhooks_fail_before_transport_io(self):
        with tempfile.TemporaryDirectory() as d:
            config, seed = configured(Path(d), SALLA_SANDBOX_RUN_WRITES="true")
            bad_assertion = self._valid_case(assertions=[{"path": "after.not_a_snapshot_field", "equals": 1}])
            transport = readiness_transport(seed)
            with self.assertRaises(runner.ContractRunnerError):
                runner.run_case(config, bad_assertion, transport, runner.EvidenceWriter(Path(d) / "evidence"), lambda _: [])
            self.assertEqual(transport.calls, [])

            bad_webhook = [{"test_case_id": "case-1", "type": "order.updated", "store_id": "other", "order_id": "o0", "correlation_id": "c1", "occurred_at": datetime.now(timezone.utc).isoformat()}]
            transport = readiness_transport(seed)
            case = self._valid_case(client_request_id="c1")
            with self.assertRaises(runner.ContractRunnerError):
                runner.run_case(config, case, transport, runner.EvidenceWriter(Path(d) / "evidence"), lambda _: bad_webhook)
            self.assertEqual(transport.calls, [])

    def test_retry_booleans_require_json_boolean_literals_before_write(self):
        with tempfile.TemporaryDirectory() as d:
            config, seed = configured(Path(d), SALLA_SANDBOX_RUN_WRITES="true", SALLA_DEMO_ALLOW_DESTRUCTIVE_RETRY="true")
            for field in ("retry_once", "disposable_order_confirmed"):
                transport = readiness_transport(seed)
                case = self._valid_case(**{field: "false"})
                with self.subTest(field=field), self.assertRaises(runner.ContractRunnerError):
                    runner.run_case(config, case, transport, runner.EvidenceWriter(Path(d) / "evidence"), lambda _: [])
                self.assertEqual(transport.calls, [])

    def test_retry_permission_and_disposable_confirmation_are_checked_before_io(self):
        with tempfile.TemporaryDirectory() as d:
            config, seed = configured(Path(d), SALLA_SANDBOX_RUN_WRITES="true", SALLA_DEMO_ALLOW_DESTRUCTIVE_RETRY="true")
            transport = readiness_transport(seed)
            case = self._valid_case(retry_once=True, disposable_order_confirmed=False)
            with self.assertRaisesRegex(runner.ContractRunnerError, "DESTRUCTIVE_RETRY_NOT_CONFIRMED"):
                runner.run_case(config, case, transport, runner.EvidenceWriter(Path(d) / "evidence"), lambda _: [])
            self.assertEqual(transport.calls, [])

    def test_forged_classification_is_always_mock_not_executed(self):
        with tempfile.TemporaryDirectory() as d:
            config, seed = configured(Path(d))
            forged = ForgedLiveTransport({("GET", "/store/info"): ok({"id": "demo-1", "type": "demo"})})
            runner.verify_demo_identity(config, forged)
            evidence = self._analyze_case(ok({"created": True}), [{"path": "after.fetch_ok", "equals": True}])
            self.assertEqual(evidence["classification"], runner.FIXTURE_CLASSIFICATION)
            self.assertEqual(evidence["verdict"], "NOT_EXECUTED")
            self.assertFalse(evidence["identity_verified"])

    def test_injected_credential_dependencies_never_receive_live_evidence_capability(self):
        with tempfile.TemporaryDirectory() as d:
            config, _ = configured(Path(d))
            db = FakeDb([{"user_id": "sandbox-owner"}])

            async def resolver(*_args, **_kwargs):
                return "resolved-token-secret"

            self.assertEqual(asyncio.run(runner.resolve_sandbox_access_token(config, db, resolver)), "resolved-token-secret")
            self.assertFalse(hasattr(runner, "build_http_transport"))
            self.assertFalse(any("capability" in name and callable(value) for name, value in vars(runner).items()))

    def test_demo_identity_on_arbitrary_transport_cannot_activate_live_capability(self):
        with tempfile.TemporaryDirectory() as d:
            config, _ = configured(Path(d))
            transport = ForgedLiveTransport({("GET", "/store/info"): ok({"id": "demo-1", "type": "demo"})})
            self.assertTrue(runner.verify_demo_identity(config, transport)["verified"])
            result = self._analyze_case(ok({"created": True}))
            self.assertEqual(result["classification"], runner.FIXTURE_CLASSIFICATION)
            self.assertEqual(result["verdict"], "NOT_EXECUTED")

    def test_live_http_transport_has_no_public_raw_token_constructor(self):
        self.assertFalse(hasattr(runner, "HttpTransport"))
        self.assertFalse(hasattr(runner, "build_http_transport"))
        self.assertNotIn("access_token", inspect.signature(runner.run_case).parameters)

    def test_write_attempt_has_immutable_intent_and_terminal_unknown_record(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            writer = runner.EvidenceWriter(tmp / "evidence")
            ledger = runner.LocalAttemptLedger(tmp / "evidence")
            lease = ledger.acquire(
                store_id="demo-1",
                order_id="o0",
                manifest_digest="a" * 64,
                case_digest="b" * 64,
                operation_digest="c" * 64,
                approval_id="approval-direct-test",
                expires_at=(datetime.now(timezone.utc) + timedelta(seconds=30)).isoformat(),
            )
            intent_file = writer.write_intent("case-1", lease.attempt_id, {"record_type": "WRITE_INTENT_START", "outcome_if_terminal_absent": "UNKNOWN"})
            terminal = writer.write_terminal("case-1", lease.attempt_id, {"record_type": "WRITE_ATTEMPT_TERMINAL", "attempt_outcome": "UNKNOWN", "intent_record": intent_file.name, "durability": runner.LOCAL_EVIDENCE_DURABILITY})
            ledger.write_terminal(lease, "UNKNOWN")
            intent = json.loads(intent_file.read_text(encoding="utf-8"))
            evidence = json.loads(terminal.read_text(encoding="utf-8"))
            self.assertEqual(intent["outcome_if_terminal_absent"], "UNKNOWN")
            self.assertEqual(evidence["attempt_outcome"], "UNKNOWN")
            self.assertEqual(evidence["intent_record"], intent_file.name)
            self.assertEqual(evidence["durability"], "LOCAL_FILESYSTEM_ONLY_NO_DB_DURABILITY")

    def test_intent_exists_before_request_and_transport_exception_finishes_unknown(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            seed = seed_data()
            case_path = tmp / "case.json"
            case_path.write_text(json.dumps(self._valid_case()), encoding="utf-8")
            client = FakeMongoClient(FakeDb([{"user_id": "sandbox-owner"}]))

            async def resolver(*_args, **_kwargs):
                return "resolved-token-secret"

            fake_http = FakeSallaHttp(seed, evidence_dir=tmp / "evidence", post_error=True)
            output = io.StringIO()
            with (
                mock.patch.dict(os.environ, self._cli_env(tmp, seed), clear=True),
                mock.patch.object(runner, "_canonical_state_root", return_value=tmp / "state"),
                mock.patch.object(runner, "_runtime_credential_dependencies", return_value=(lambda _: client, resolver)),
                mock.patch.object(runner.urllib.request, "urlopen", side_effect=fake_http),
                contextlib.redirect_stdout(output),
            ):
                result = runner.main(["run", "--case-file", str(case_path)])
            self.assertEqual(result, 0)
            terminal_files = list((tmp / "evidence").glob("*.terminal.json"))
            self.assertEqual(len(terminal_files), 1)
            terminal = terminal_files[0]
            evidence = json.loads(terminal.read_text(encoding="utf-8"))
            self.assertEqual(evidence["attempt_outcome"], "UNKNOWN")
            self.assertEqual(evidence["transport_error"], "TimeoutError")
            self.assertEqual(len([call for call in fake_http.calls if call[0] == "POST"]), 1)
            self.assertNotIn("test-only timeout", terminal.read_text(encoding="utf-8"))

    def test_sealed_cli_requires_fresh_approval_then_proves_fixed_postconditions(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            seed = seed_data()
            case_path = tmp / "case.json"
            case_path.write_text(json.dumps(self._valid_case()), encoding="utf-8")
            client = FakeMongoClient(FakeDb([{"user_id": "sandbox-owner"}]))

            async def resolver(*_args, **_kwargs):
                return "resolved-token-secret"

            missing_approval = self._cli_env(tmp, seed)
            missing_approval.pop("SALLA_P0_WRITE_APPROVAL_ID")
            output = io.StringIO()
            with (
                mock.patch.dict(os.environ, missing_approval, clear=True),
                mock.patch.object(runner, "_runtime_credential_dependencies") as dependencies,
                mock.patch.object(runner.urllib.request, "urlopen") as urlopen,
                contextlib.redirect_stdout(output),
            ):
                result = runner.main(["run", "--case-file", str(case_path)])
            self.assertEqual(result, 2)
            self.assertIn(runner.WRITE_APPROVAL_REQUIRED, output.getvalue())
            dependencies.assert_not_called()
            urlopen.assert_not_called()

            fake_http = FakeSallaHttp(seed, evidence_dir=tmp / "evidence")
            output = io.StringIO()
            with (
                mock.patch.dict(os.environ, self._cli_env(tmp, seed), clear=True),
                mock.patch.object(runner, "_canonical_state_root", return_value=tmp / "state"),
                mock.patch.object(runner, "_runtime_credential_dependencies", return_value=(lambda _: client, resolver)),
                mock.patch.object(runner.urllib.request, "urlopen", side_effect=fake_http),
                contextlib.redirect_stdout(output),
            ):
                result = runner.main(["run", "--case-file", str(case_path)])
            self.assertEqual(result, 0)
            terminal_files = list((tmp / "evidence").glob("*.terminal.json"))
            self.assertEqual(len(terminal_files), 1)
            evidence = json.loads(terminal_files[0].read_text(encoding="utf-8"))
            self.assertEqual(evidence["observed_verdict"], "PASS")
            self.assertEqual(evidence["verdict"], "INCONCLUSIVE")
            self.assertEqual(evidence["final_verdict_reason"], "WEBHOOK_SOURCE_NOT_CONFIGURED")
            self.assertTrue(all(row["passed"] for row in evidence["fixed_postconditions"]))
            self.assertEqual(len([call for call in fake_http.calls if call[0] == "POST"]), 1)

    def test_cli_invalid_case_is_rejected_before_credential_factory(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            _, seed = configured(tmp)
            (tmp / "seed.json").write_text(json.dumps(seed), encoding="utf-8")
            case_path = tmp / "invalid-case.json"
            case_path.write_text(json.dumps({"id": "invalid", "unknown": True}), encoding="utf-8")
            env = {
                "SALLA_SANDBOX_BASE_URL": runner.OFFICIAL_BASE_URL,
                "SALLA_DEMO_STORE_ID": "demo-1",
                "SALLA_DEMO_TOKEN_SCOPES": "orders.read_write,products.read_write",
                "SALLA_SANDBOX_SEED_MANIFEST": str(tmp / "seed.json"),
                "SALLA_SANDBOX_EVIDENCE_DIR": str(tmp / "evidence"),
                "SALLA_DEMO_STORE_CONFIRMED": "true",
                "SALLA_SANDBOX_RUN_WRITES": "true",
                "MONGO_URL": "mongodb://must-not-be-used.invalid",
                "DB_NAME": "must_not_be_used",
            }
            with mock.patch.dict(os.environ, env, clear=True), mock.patch.object(runner, "_runtime_credential_dependencies") as factory:
                self.assertEqual(runner.main(["run", "--case-file", str(case_path)]), 2)
            factory.assert_not_called()

    def test_importer_cannot_register_or_promote_mock_to_live_evidence(self):
        exposed = {
            name
            for name, value in vars(runner).items()
            if callable(value)
            and any(fragment in name for fragment in ("register_factory_live", "mark_live_identity", "evidence_classification"))
        }
        self.assertEqual(exposed, set())

    def test_public_run_case_and_transport_factory_cannot_execute_arbitrary_transport(self):
        self.assertFalse(hasattr(runner, "build_http_transport"))
        with tempfile.TemporaryDirectory() as d:
            config, seed = configured(Path(d), SALLA_SANDBOX_RUN_WRITES="true")
            transport = readiness_transport(seed)
            with self.assertRaisesRegex(runner.ContractRunnerError, "LIVE_EXECUTOR_REQUIRED"):
                runner.run_case(
                    config,
                    self._valid_case(),
                    transport,
                    runner.EvidenceWriter(Path(d) / "evidence"),
                    lambda _: [],
                )
            self.assertEqual(transport.calls, [])

    def test_local_attempt_ledger_blocks_replay_unknown_and_reused_approval(self):
        with tempfile.TemporaryDirectory() as d:
            ledger = runner.LocalAttemptLedger(Path(d))
            first = ledger.acquire(
                store_id="demo-1",
                order_id="o0",
                manifest_digest="a" * 64,
                case_digest="c" * 64,
                operation_digest="e" * 64,
                approval_id="approval-1",
                expires_at="2026-09-13T00:05:00+00:00",
            )
            ledger.write_terminal(first, "UNKNOWN")
            with self.assertRaisesRegex(runner.ContractRunnerError, "ATTEMPT_REPLAY_BLOCKED"):
                ledger.acquire(
                    store_id="demo-1",
                    order_id="o0",
                    manifest_digest="a" * 64,
                    case_digest="c" * 64,
                    operation_digest="e" * 64,
                    approval_id="approval-2",
                    expires_at="2026-09-13T00:05:00+00:00",
                )
            with self.assertRaisesRegex(runner.ContractRunnerError, "APPROVAL_ALREADY_CONSUMED"):
                ledger.acquire(
                    store_id="demo-1",
                    order_id="o1",
                    manifest_digest="b" * 64,
                    case_digest="d" * 64,
                    operation_digest="f" * 64,
                    approval_id="approval-1",
                    expires_at="2026-09-13T00:05:00+00:00",
                )

    def test_local_attempt_ledger_blocks_concurrent_order_attempts(self):
        with tempfile.TemporaryDirectory() as d:
            first_ledger = runner.LocalAttemptLedger(Path(d))
            second_ledger = runner.LocalAttemptLedger(Path(d))
            first = first_ledger.acquire(
                store_id="demo-1",
                order_id="o0",
                manifest_digest="1" * 64,
                case_digest="2" * 64,
                operation_digest="3" * 64,
                approval_id="approval-concurrent-a",
                expires_at=(datetime.now(timezone.utc) + timedelta(seconds=30)).isoformat(),
            )
            try:
                with self.assertRaisesRegex(runner.ContractRunnerError, runner.CONCURRENT_ATTEMPT_BLOCKED):
                    second_ledger.acquire(
                        store_id="demo-1",
                        order_id="o0",
                        manifest_digest="4" * 64,
                        case_digest="5" * 64,
                        operation_digest="6" * 64,
                        approval_id="approval-concurrent-b",
                        expires_at=(datetime.now(timezone.utc) + timedelta(seconds=30)).isoformat(),
                    )
            finally:
                first_ledger.write_terminal(first, "TERMINAL")

    def test_variant_sku_and_option_value_pairs_must_match_one_seed_tuple(self):
        seed = seed_data()
        valid = self._valid_case(
            body={
                "order_id": "o0",
                "product_id": "p1",
                "variant_id": "v1",
                "sku": "SKU1-V1",
                "branch_id": "b1",
                "quantity": 1,
                "options": [
                    {"option_id": "op1", "value_id": "x"},
                    {"option_id": "op2", "value_id": "y"},
                ],
            }
        )
        runner.validate_case(valid, seed)
        invalid = [
            {**valid, "body": {**valid["body"], "sku": "SKU1-V2"}},
            {**valid, "body": {**valid["body"], "options": [{"option_id": "op1", "value_id": "y"}]}},
        ]
        for case in invalid:
            with self.subTest(case=case), self.assertRaises(runner.ContractRunnerError):
                runner.validate_case(case, seed)

    def test_file_assertions_cannot_pass_without_fixed_operation_postconditions(self):
        seed = seed_data()
        case = self._valid_case(assertions=[{"path": "after.fetch_ok", "equals": True}])
        unchanged = {
            "fetch_ok": True,
            "order_id": "o0",
            "order_status": "pending",
            "items_count": 1,
            "items": [{"item_id": "i0", "product_id": "p0", "sku": "SKU0", "quantity": 1, "options": [], "total": 100, "branches_quantity": []}],
        }
        result = runner.analyze_attempt(case, seed, unchanged, {"status": 200, "body": {"success": True}}, unchanged, [])
        self.assertNotEqual(result["observed_verdict"], "PASS")
        self.assertFalse(all(row["passed"] for row in result["fixed_postconditions"]))

    def test_fixed_postconditions_cover_add_update_and_delete_target_deltas(self):
        seed = seed_data()
        old = {"item_id": "i0", "product_id": "p0", "sku": "SKU0", "quantity": 1}
        replacement = {"item_id": "replacement-item-1", "product_id": "p5", "sku": "SKU5", "quantity": 1}
        cases = [
            (
                self._valid_case(),
                {"fetch_ok": True, "order_id": "o0", "items_count": 1, "items": [old]},
                {"fetch_ok": True, "order_id": "o0", "items_count": 2, "items": [old, {"item_id": "i-new", "product_id": "p0", "sku": "SKU0", "quantity": 1, "branch_id": "b1"}]},
            ),
            (
                self._valid_case(method="PUT", path="/orders/items/i0", body={"order_id": "o0", "quantity": 2}),
                {"fetch_ok": True, "order_id": "o0", "items_count": 2, "items": [old, replacement]},
                {"fetch_ok": True, "order_id": "o0", "items_count": 2, "items": [{**old, "quantity": 2}, replacement]},
            ),
            (
                self._valid_case(method="DELETE", path="/orders/items/i0", body={}, steps=self._delete_steps()),
                {"fetch_ok": True, "order_id": "o0", "items_count": 2, "items": [old, replacement]},
                {"fetch_ok": True, "order_id": "o0", "items_count": 1, "items": [replacement]},
            ),
        ]
        for case, before, after in cases:
            with self.subTest(method=case["method"]):
                conditions = runner.evaluate_fixed_postconditions(case, seed, before, after)
                self.assertTrue(conditions)
                self.assertTrue(all(row["passed"] for row in conditions), conditions)

    def test_live_state_root_is_fixed_separate_and_rejects_unsafe_paths(self):
        self.assertEqual(len(inspect.signature(runner._canonical_state_root).parameters), 0)
        expected = runner._git_common_directory(RUNNER_PATH.resolve().parents[2]) / "mz-p0-local-state" / "MZ-ORDER-REVISION-SALLA-001"
        self.assertEqual(runner._canonical_state_root(), expected)
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            previous_cwd = Path.cwd()
            try:
                os.chdir(tmp)
                with mock.patch.dict(
                    os.environ,
                    {
                        "SALLA_SANDBOX_EVIDENCE_DIR": str(tmp / "chosen-evidence"),
                        "SALLA_SANDBOX_SEED_MANIFEST": str(tmp / "chosen-manifest.json"),
                        "SALLA_P0_STATE_ROOT": str(tmp / "attacker-selected-state"),
                    },
                    clear=False,
                ):
                    self.assertEqual(runner._canonical_state_root(), expected)
            finally:
                os.chdir(previous_cwd)

            runner_alias = tmp / "repo-relative-runner-alias.py"
            runner_alias.symlink_to(RUNNER_PATH)
            with mock.patch.object(runner, "__file__", str(runner_alias)):
                self.assertEqual(runner._canonical_state_root(), expected)

            target = tmp / "target"
            target.mkdir()
            target.chmod(0o700)
            link = tmp / "state-link"
            link.symlink_to(target, target_is_directory=True)
            with self.assertRaisesRegex(runner.ContractRunnerError, runner.STATE_ROOT_UNSAFE):
                runner.LocalAttemptLedger(link).acquire(
                    store_id="demo-1",
                    order_id="o0",
                    manifest_digest="1" * 64,
                    case_digest="2" * 64,
                    operation_digest="3" * 64,
                    approval_id="approval-unsafe-root",
                    expires_at=(datetime.now(timezone.utc) + timedelta(seconds=30)).isoformat(),
                )

            unsafe_mode = tmp / "unsafe-mode"
            unsafe_mode.mkdir(mode=0o755)
            unsafe_mode.chmod(0o755)
            with self.assertRaisesRegex(runner.ContractRunnerError, runner.STATE_ROOT_UNSAFE):
                runner.LocalAttemptLedger(unsafe_mode).acquire(
                    store_id="demo-1",
                    order_id="o0",
                    manifest_digest="1" * 64,
                    case_digest="2" * 64,
                    operation_digest="3" * 64,
                    approval_id="approval-unsafe-mode",
                    expires_at=(datetime.now(timezone.utc) + timedelta(seconds=30)).isoformat(),
                )

    def test_state_ledger_permissions_and_cross_evidence_replay_are_fail_closed(self):
        with tempfile.TemporaryDirectory() as d:
            state_root = Path(d) / "fixed-state"
            first = runner.LocalAttemptLedger(state_root)
            lease = first.acquire(
                store_id="demo-1",
                order_id="o0",
                manifest_digest="1" * 64,
                case_digest="2" * 64,
                operation_digest="3" * 64,
                approval_id="approval-cross-evidence",
                expires_at=(datetime.now(timezone.utc) + timedelta(seconds=30)).isoformat(),
            )
            first.write_terminal(lease, "TERMINAL")
            self.assertEqual(stat.S_IMODE(first.root.stat().st_mode), 0o700)
            self.assertTrue(all(stat.S_IMODE(path.stat().st_mode) == 0o700 for path in first.root.rglob("*") if path.is_dir()))
            self.assertTrue(all(stat.S_IMODE(path.stat().st_mode) == 0o600 for path in first.root.rglob("*") if path.is_file()))
            for _evidence_dir in (Path(d) / "evidence-a", Path(d) / "evidence-b"):
                with self.assertRaisesRegex(runner.ContractRunnerError, runner.ATTEMPT_REPLAY_BLOCKED):
                    runner.LocalAttemptLedger(state_root).acquire(
                        store_id="demo-1",
                        order_id="o0",
                        manifest_digest="1" * 64,
                        case_digest="2" * 64,
                        operation_digest="3" * 64,
                        approval_id=f"approval-{_evidence_dir.name}",
                        expires_at=(datetime.now(timezone.utc) + timedelta(seconds=30)).isoformat(),
                    )

    def test_capability_ttl_is_rechecked_immediately_before_retry(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            seed = seed_data()
            case = self._valid_case(retry_once=True, disposable_order_confirmed=True)
            case_path = tmp / "retry-case.json"
            case_path.write_text(json.dumps(case), encoding="utf-8")
            client = FakeMongoClient(FakeDb([{"user_id": "sandbox-owner"}]))

            async def resolver(*_args, **_kwargs):
                return "resolved-token-secret"

            fake_http = FakeSallaHttp(seed, evidence_dir=tmp / "evidence", after_first_delay=0.1)
            expiry = datetime.now(timezone.utc) + timedelta(seconds=0.05)
            output = io.StringIO()
            env = self._cli_env(tmp, seed, SALLA_DEMO_ALLOW_DESTRUCTIVE_RETRY="true")
            with (
                mock.patch.dict(os.environ, env, clear=True),
                mock.patch.object(runner, "_write_approval_expiry", return_value=expiry),
                mock.patch.object(runner, "_canonical_state_root", return_value=tmp / "state"),
                mock.patch.object(runner, "_runtime_credential_dependencies", return_value=(lambda _: client, resolver)),
                mock.patch.object(runner.urllib.request, "urlopen", side_effect=fake_http),
                contextlib.redirect_stdout(output),
            ):
                result = runner.main(["run", "--case-file", str(case_path)])
            self.assertEqual(result, 0)
            self.assertEqual(len([call for call in fake_http.calls if call[0] == "POST"]), 1)
            terminal = next((tmp / "evidence").glob("*.terminal.json"))
            self.assertEqual(json.loads(terminal.read_text(encoding="utf-8"))["attempt_outcome"], "UNKNOWN")

    def test_capability_approval_binding_is_rechecked_before_retry(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            seed = seed_data()
            case = self._valid_case(retry_once=True, disposable_order_confirmed=True)
            case_path = tmp / "retry-case.json"
            case_path.write_text(json.dumps(case), encoding="utf-8")
            client = FakeMongoClient(FakeDb([{"user_id": "sandbox-owner"}]))

            async def resolver(*_args, **_kwargs):
                return "resolved-token-secret"

            env = self._cli_env(tmp, seed, SALLA_DEMO_ALLOW_DESTRUCTIVE_RETRY="true")
            config = runner.SandboxConfig.from_env(env)
            fake_http = FakeSallaHttp(
                seed,
                evidence_dir=tmp / "evidence",
                after_first_hook=lambda: object.__setattr__(config, "write_approval_id", "changed-approval-id"),
            )
            output = io.StringIO()
            with (
                mock.patch.dict(os.environ, env, clear=True),
                mock.patch.object(runner.SandboxConfig, "from_env", return_value=config),
                mock.patch.object(runner, "_canonical_state_root", return_value=tmp / "state"),
                mock.patch.object(runner, "_runtime_credential_dependencies", return_value=(lambda _: client, resolver)),
                mock.patch.object(runner.urllib.request, "urlopen", side_effect=fake_http),
                contextlib.redirect_stdout(output),
            ):
                result = runner.main(["run", "--case-file", str(case_path)])
            self.assertEqual(result, 0)
            self.assertEqual(len([call for call in fake_http.calls if call[0] == "POST"]), 1)
            evidence = json.loads(next((tmp / "evidence").glob("*.terminal.json")).read_text(encoding="utf-8"))
            self.assertEqual(evidence["attempt_outcome"], "UNKNOWN")
            self.assertEqual(evidence["retry_response"]["transport_error"], runner.LIVE_CAPABILITY_MISMATCH)

    def test_capability_budget_allows_only_the_one_reviewed_retry(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            seed = seed_data()
            case = self._valid_case(retry_once=True, disposable_order_confirmed=True)
            case_path = tmp / "retry-case.json"
            case_path.write_text(json.dumps(case), encoding="utf-8")
            client = FakeMongoClient(FakeDb([{"user_id": "sandbox-owner"}]))

            async def resolver(*_args, **_kwargs):
                return "resolved-token-secret"

            fake_http = FakeSallaHttp(seed, evidence_dir=tmp / "evidence")
            output = io.StringIO()
            env = self._cli_env(tmp, seed, SALLA_DEMO_ALLOW_DESTRUCTIVE_RETRY="true")
            with (
                mock.patch.dict(os.environ, env, clear=True),
                mock.patch.object(runner, "_canonical_state_root", return_value=tmp / "state"),
                mock.patch.object(runner, "_runtime_credential_dependencies", return_value=(lambda _: client, resolver)),
                mock.patch.object(runner.urllib.request, "urlopen", side_effect=fake_http),
                contextlib.redirect_stdout(output),
            ):
                result = runner.main(["run", "--case-file", str(case_path)])
            self.assertEqual(result, 0)
            self.assertEqual(len([call for call in fake_http.calls if call[0] == "POST"]), 2)
            evidence = json.loads(next((tmp / "evidence").glob("*.terminal.json")).read_text(encoding="utf-8"))
            self.assertEqual(evidence["attempt_outcome"], "TERMINAL")
            self.assertEqual(evidence["idempotency"], "SALLA_IDEMPOTENCY_OBSERVED")

    def test_update_requires_requested_mutation_and_rejects_unrequested_drift(self):
        seed = seed_data()
        with self.assertRaisesRegex(runner.ContractRunnerError, runner.UPDATE_MUTATION_REQUIRED):
            runner.validate_case(
                self._valid_case(method="PUT", path="/orders/items/i0", body={"order_id": "o0"}),
                seed,
            )
        before = {
            "fetch_ok": True,
            "order_id": "o0",
            "items_count": 1,
            "items": [{"item_id": "i0", "product_id": "p0", "sku": "SKU0", "quantity": 1, "price": 10}],
        }
        cases = [
            (
                self._valid_case(method="PUT", path="/orders/items/i0", body={"order_id": "o0", "quantity": 1}),
                {**before, "items": [{**before["items"][0], "price": 11}]},
            ),
            (
                self._valid_case(method="PUT", path="/orders/items/i0", body={"order_id": "o0", "quantity": 2}),
                {**before, "items": [{**before["items"][0], "quantity": 2, "price": 11}]},
            ),
        ]
        for case, after in cases:
            with self.subTest(body=case["body"]):
                result = runner.analyze_attempt(case, seed, before, ok({"updated": True}), after, [])
                self.assertEqual(result["observed_verdict"], "FAIL")

    def test_reused_approval_creates_no_orphan_attempt_lease(self):
        with tempfile.TemporaryDirectory() as d:
            ledger = runner.LocalAttemptLedger(Path(d) / "state")
            first = ledger.acquire(
                store_id="demo-1",
                order_id="o0",
                manifest_digest="1" * 64,
                case_digest="2" * 64,
                operation_digest="3" * 64,
                approval_id="approval-reuse-atomic",
                expires_at=(datetime.now(timezone.utc) + timedelta(seconds=30)).isoformat(),
            )
            ledger.write_terminal(first, "TERMINAL")
            before_leases = set(ledger.root.glob("attempts/*/lease.json"))
            with self.assertRaisesRegex(runner.ContractRunnerError, runner.APPROVAL_ALREADY_CONSUMED):
                ledger.acquire(
                    store_id="demo-1",
                    order_id="o1",
                    manifest_digest="4" * 64,
                    case_digest="5" * 64,
                    operation_digest="6" * 64,
                    approval_id="approval-reuse-atomic",
                    expires_at=(datetime.now(timezone.utc) + timedelta(seconds=30)).isoformat(),
                )
            self.assertEqual(set(ledger.root.glob("attempts/*/lease.json")), before_leases)

    def test_exact_token_value_is_redacted_from_generic_nested_evidence(self):
        token = "opaque-resolved-secret-123"
        value = {
            f"generic-{token}-key": {"debug": token},
            "nested": [f"prefix:{token}:suffix", {token: "token-was-a-key"}],
        }
        encoded = json.dumps(runner.sanitize(value, exact_secrets=(token,)))
        self.assertNotIn(token, encoded)
        self.assertIn("[REDACTED]", encoded)

    def test_exact_token_key_redaction_collision_fails_closed(self):
        token = "opaque-resolved-secret-key"
        with self.assertRaisesRegex(runner.ContractRunnerError, runner.REDACTION_KEY_COLLISION):
            runner.sanitize(
                {token: "first", "[REDACTED]": "second"},
                exact_secrets=(token,),
            )

    def test_sealed_cli_redacts_resolved_token_from_response_before_persistence(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            seed = seed_data()
            case_path = tmp / "case.json"
            case_path.write_text(json.dumps(self._valid_case()), encoding="utf-8")
            token = "opaque-resolved-secret-456"
            client = FakeMongoClient(FakeDb([{"user_id": "sandbox-owner"}]))

            async def resolver(*_args, **_kwargs):
                return token

            fake_http = FakeSallaHttp(
                seed,
                evidence_dir=tmp / "evidence",
                post_data={token: "token-was-a-key", "nested": [{f"prefix-{token}": token}]},
            )
            output = io.StringIO()
            with (
                mock.patch.dict(os.environ, self._cli_env(tmp, seed), clear=True),
                mock.patch.object(runner, "_canonical_state_root", return_value=tmp / "state"),
                mock.patch.object(runner, "_runtime_credential_dependencies", return_value=(lambda _: client, resolver)),
                mock.patch.object(runner.urllib.request, "urlopen", side_effect=fake_http),
                contextlib.redirect_stdout(output),
            ):
                result = runner.main(["run", "--case-file", str(case_path)])
            self.assertEqual(result, 0)
            terminal = next((tmp / "evidence").glob("*.terminal.json"))
            self.assertNotIn(token, terminal.read_text(encoding="utf-8"))
            self.assertNotIn(token, output.getvalue())

    def test_sealed_cli_fails_closed_on_token_key_redaction_collision(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            seed = seed_data()
            case_path = tmp / "case.json"
            case_path.write_text(json.dumps(self._valid_case()), encoding="utf-8")
            token = "opaque-resolved-secret-collision"
            client = FakeMongoClient(FakeDb([{"user_id": "sandbox-owner"}]))

            async def resolver(*_args, **_kwargs):
                return token

            fake_http = FakeSallaHttp(
                seed,
                evidence_dir=tmp / "evidence",
                post_data={token: "first", "[REDACTED]": "second"},
            )
            output = io.StringIO()
            with (
                mock.patch.dict(os.environ, self._cli_env(tmp, seed), clear=True),
                mock.patch.object(runner, "_canonical_state_root", return_value=tmp / "state"),
                mock.patch.object(runner, "_runtime_credential_dependencies", return_value=(lambda _: client, resolver)),
                mock.patch.object(runner.urllib.request, "urlopen", side_effect=fake_http),
                contextlib.redirect_stdout(output),
            ):
                result = runner.main(["run", "--case-file", str(case_path)])
            self.assertEqual(result, 2)
            self.assertIn(runner.REDACTION_KEY_COLLISION, output.getvalue())
            self.assertNotIn(token, output.getvalue())
            self.assertEqual(len(list((tmp / "evidence").glob("*.intent.json"))), 1)
            self.assertEqual(list((tmp / "evidence").glob("*.terminal.json")), [])

    def test_schema_independent_contract_validation_passes_without_jsonschema(self):
        base = ROOT / "docs" / "operations" / "MZ-ORDER-REVISION-SALLA-001"
        seed_schema = json.loads((base / "schemas" / "sandbox-seed-manifest.schema.json").read_text(encoding="utf-8"))
        evidence_schema = json.loads((base / "schemas" / "evidence-record.schema.json").read_text(encoding="utf-8"))
        example = json.loads((base / "fixtures" / "sandbox-seed-manifest.example.json").read_text(encoding="utf-8"))
        product_properties = seed_schema["$defs"]["product"]["properties"]
        self.assertIn("option_value_tuples", product_properties)
        self.assertIn("variant_tuples", product_properties)
        variant = next(row for row in example["products"] if row["kind"] == "size_color_variant")
        self.assertIn("option_value_tuples", variant)
        self.assertIn("variant_tuples", variant)
        for required in ("attempt_id", "manifest_digest", "case_digest", "operation_digest", "fixed_postconditions", "attempt_outcome", "durability"):
            self.assertIn(required, evidence_schema["required"])
        config = runner.SandboxConfig(
            runner.OFFICIAL_BASE_URL,
            str(example["store_id"]),
            frozenset({"orders.read_write", "products.read_write"}),
            base / "fixtures" / "sandbox-seed-manifest.example.json",
            base / "evidence",
            False,
            False,
            False,
            "",
            "",
        )
        runner.validate_seed_structure(config, example)
        self.assertEqual(SCHEMA_INDEPENDENT_VALIDATION_PASS, "SCHEMA_INDEPENDENT_VALIDATION=PASS")

    def test_full_draft202012_schema_validation(self):
        base = ROOT / "docs" / "operations" / "MZ-ORDER-REVISION-SALLA-001"
        seed_schema = json.loads((base / "schemas" / "sandbox-seed-manifest.schema.json").read_text(encoding="utf-8"))
        evidence_schema = json.loads((base / "schemas" / "evidence-record.schema.json").read_text(encoding="utf-8"))
        example = json.loads((base / "fixtures" / "sandbox-seed-manifest.example.json").read_text(encoding="utf-8"))
        validator = require_draft202012_validator()
        validator.check_schema(seed_schema)
        validator.check_schema(evidence_schema)
        validator(seed_schema).validate(example)
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            seed = seed_data()
            case_path = tmp / "case.json"
            case_path.write_text(json.dumps(self._valid_case()), encoding="utf-8")
            client = FakeMongoClient(FakeDb([{"user_id": "sandbox-owner"}]))

            async def resolver(*_args, **_kwargs):
                return "resolved-token-secret"

            fake_http = FakeSallaHttp(seed, evidence_dir=tmp / "evidence")
            with (
                mock.patch.dict(os.environ, self._cli_env(tmp, seed), clear=True),
                mock.patch.object(runner, "_canonical_state_root", return_value=tmp / "state"),
                mock.patch.object(runner, "_runtime_credential_dependencies", return_value=(lambda _: client, resolver)),
                mock.patch.object(runner.urllib.request, "urlopen", side_effect=fake_http),
                contextlib.redirect_stdout(io.StringIO()),
            ):
                self.assertEqual(runner.main(["run", "--case-file", str(case_path)]), 0)
            evidence = json.loads(next((tmp / "evidence").glob("*.terminal.json")).read_text(encoding="utf-8"))
            validator(evidence_schema).validate(evidence)

    def test_linked_worktrees_share_git_common_state_approval_and_order_lock(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            common_git = tmp / "main" / ".git"
            common_git.mkdir(parents=True)

            def linked_runner(name: str) -> Path:
                checkout = tmp / name
                runner_file = checkout / "scripts" / "research" / "salla_order_item_contract_runner.py"
                runner_file.parent.mkdir(parents=True)
                runner_file.write_text("# linked-worktree runner placeholder\n", encoding="utf-8")
                git_dir = common_git / "worktrees" / name
                git_dir.mkdir(parents=True)
                (git_dir / "commondir").write_text("../..\n", encoding="utf-8")
                (checkout / ".git").write_text(f"gitdir: {git_dir}\n", encoding="utf-8")
                return runner_file

            first_runner = linked_runner("linked-a")
            second_runner = linked_runner("linked-b")
            with mock.patch.object(runner, "__file__", str(first_runner)):
                first_root = runner._canonical_state_root()
            with mock.patch.object(runner, "__file__", str(second_runner)):
                second_root = runner._canonical_state_root()
            expected = common_git.resolve() / "mz-p0-local-state" / "MZ-ORDER-REVISION-SALLA-001"
            self.assertEqual(first_root, expected)
            self.assertEqual(second_root, expected)

            first_ledger = runner.LocalAttemptLedger(first_root)
            first_lease = first_ledger.acquire(
                store_id="demo-1",
                order_id="o0",
                manifest_digest="1" * 64,
                case_digest="2" * 64,
                operation_digest="3" * 64,
                approval_id="shared-linked-approval",
                expires_at=(datetime.now(timezone.utc) + timedelta(seconds=30)).isoformat(),
            )
            self.assertEqual(stat.S_IMODE(first_root.parent.stat().st_mode), 0o700)
            with self.assertRaisesRegex(runner.ContractRunnerError, runner.CONCURRENT_ATTEMPT_BLOCKED):
                runner.LocalAttemptLedger(second_root).acquire(
                    store_id="demo-1",
                    order_id="o0",
                    manifest_digest="4" * 64,
                    case_digest="5" * 64,
                    operation_digest="6" * 64,
                    approval_id="second-linked-approval",
                    expires_at=(datetime.now(timezone.utc) + timedelta(seconds=30)).isoformat(),
                )
            first_ledger.write_terminal(first_lease, "TERMINAL")
            with self.assertRaisesRegex(runner.ContractRunnerError, runner.APPROVAL_ALREADY_CONSUMED):
                runner.LocalAttemptLedger(second_root).acquire(
                    store_id="demo-1",
                    order_id="o1",
                    manifest_digest="4" * 64,
                    case_digest="5" * 64,
                    operation_digest="6" * 64,
                    approval_id="shared-linked-approval",
                    expires_at=(datetime.now(timezone.utc) + timedelta(seconds=30)).isoformat(),
                )

    def test_non_linux_preflight_blocks_before_fcntl_import(self):
        with (
            mock.patch.object(runner.os, "name", "nt"),
            mock.patch.object(runner.sys, "platform", "win32"),
            mock.patch.object(runner.importlib, "import_module") as import_module,
            self.assertRaisesRegex(runner.ContractRunnerError, runner.LINUX_POSIX_REQUIRED),
        ):
            runner._require_linux_posix()
        import_module.assert_not_called()
        runner_source = RUNNER_PATH.read_text(encoding="utf-8")
        self.assertNotIn("\nimport " + "fcntl\n", runner_source)
        output = io.StringIO()
        with (
            mock.patch.object(runner.sys, "platform", "win32"),
            mock.patch.object(runner.importlib, "import_module") as import_module,
            mock.patch.object(runner.SandboxConfig, "from_env") as config_factory,
            contextlib.redirect_stdout(output),
        ):
            self.assertEqual(runner.main(["readiness"]), 2)
        import_module.assert_not_called()
        config_factory.assert_not_called()
        self.assertEqual(output.getvalue().strip(), runner.LINUX_POSIX_REQUIRED)
        matrix = (ROOT / "docs" / "operations" / "MZ-ORDER-REVISION-SALLA-001" / "SALLA-CONTRACT-MATRIX.md").read_text(encoding="utf-8")
        self.assertIn("Linux/POSIX", matrix)
        self.assertNotIn("```powershell", matrix.casefold())
        self.assertNotIn("$env:", matrix)

    def test_linux_preflight_rejects_missing_zero_or_noncallable_security_primitives(self):
        supported = {
            "name": "posix",
            "O_NOFOLLOW": 1,
            "O_DIRECTORY": 2,
            "getuid": lambda: 0,
        }
        cases = {
            "missing O_NOFOLLOW": {key: value for key, value in supported.items() if key != "O_NOFOLLOW"},
            "zero O_NOFOLLOW": {**supported, "O_NOFOLLOW": 0},
            "missing O_DIRECTORY": {key: value for key, value in supported.items() if key != "O_DIRECTORY"},
            "zero O_DIRECTORY": {**supported, "O_DIRECTORY": 0},
            "missing getuid": {key: value for key, value in supported.items() if key != "getuid"},
            "noncallable getuid": {**supported, "getuid": 0},
        }
        for label, primitive_set in cases.items():
            with self.subTest(label=label):
                fake_os = types.SimpleNamespace(**primitive_set)
                with (
                    mock.patch.object(runner, "os", fake_os),
                    mock.patch.object(runner.sys, "platform", "linux"),
                    mock.patch.object(runner.importlib, "import_module") as import_module,
                    self.assertRaisesRegex(runner.ContractRunnerError, runner.LINUX_POSIX_REQUIRED),
                ):
                    runner._require_linux_posix()
                import_module.assert_not_called()

    def test_security_primitive_preflight_precedes_config_state_and_fcntl_io(self):
        output = io.StringIO()
        with (
            mock.patch.object(runner.os, "O_NOFOLLOW", 0),
            mock.patch.object(runner.importlib, "import_module") as import_module,
            mock.patch.object(runner.SandboxConfig, "from_env") as config_factory,
            contextlib.redirect_stdout(output),
        ):
            self.assertEqual(runner.main(["readiness"]), 2)
        import_module.assert_not_called()
        config_factory.assert_not_called()
        self.assertEqual(output.getvalue().strip(), runner.LINUX_POSIX_REQUIRED)

        with tempfile.TemporaryDirectory() as d:
            state_root = Path(d) / "must-not-exist"
            with (
                mock.patch.object(runner.os, "O_DIRECTORY", 0),
                mock.patch.object(runner.importlib, "import_module") as import_module,
                self.assertRaisesRegex(runner.ContractRunnerError, runner.LINUX_POSIX_REQUIRED),
            ):
                runner.LocalAttemptLedger(state_root).acquire(
                    store_id="demo-1",
                    order_id="o0",
                    manifest_digest="1" * 64,
                    case_digest="2" * 64,
                    operation_digest="3" * 64,
                    approval_id="must-not-be-consumed",
                    expires_at=(datetime.now(timezone.utc) + timedelta(seconds=30)).isoformat(),
                )
            import_module.assert_not_called()
            self.assertFalse(state_root.exists())

    def test_security_paths_never_silently_downgrade_required_open_flags(self):
        source = RUNNER_PATH.read_text(encoding="utf-8")
        self.assertNotIn('getattr(os, "O_NOFOLLOW", 0)', source)
        self.assertNotIn('getattr(os, "O_DIRECTORY", 0)', source)
        self.assertNotIn('not hasattr(os, "getuid")', source)

    def test_full_schema_validation_dependency_is_never_silently_skipped(self):
        source = Path(__file__).read_text(encoding="utf-8")
        self.assertNotIn("if jsonschema" + " is not None", source)
        self.assertIn("FULL_DRAFT202012_VALIDATION=BLOCKED_MISSING_DEPENDENCY", source)
        documentation = (
            ROOT / "docs" / "operations" / "MZ-ORDER-REVISION-SALLA-001" / "SALLA-CONTRACT-MATRIX.md",
            ROOT / "docs" / "operations" / "MZ-ORDER-REVISION-SALLA-001" / "SANDBOX-SEED-REQUIREMENTS.md",
        )
        for path in documentation:
            documented_contract = path.read_text(encoding="utf-8")
            self.assertIn(FULL_DRAFT202012_VALIDATION_BLOCKED, documented_contract)
            self.assertNotIn("FULL_SCHEMA_VALIDATION_REQUIRES_JSONSCHEMA", documented_contract)
        independent_source = inspect.getsource(self.test_schema_independent_contract_validation_passes_without_jsonschema)
        full_draft_source = inspect.getsource(self.test_full_draft202012_schema_validation)
        self.assertNotIn("require_draft202012_validator", independent_source)
        self.assertIn("require_draft202012_validator", full_draft_source)
        with (
            mock.patch.dict(require_draft202012_validator.__globals__, {"Draft202012Validator": None}),
            self.assertRaisesRegex(RuntimeError, FULL_DRAFT202012_VALIDATION_BLOCKED),
        ):
            require_draft202012_validator()


if __name__ == "__main__":
    unittest.main()
