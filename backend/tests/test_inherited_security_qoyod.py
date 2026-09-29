"""A8 route boundary controls. Fake storage/ASGI only; no server or provider."""
from copy import deepcopy
import importlib

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from integrations.qoyod import routes


CANARY = "PRIVATE-CANARY mongodb://secret@host raw-body customer-phone traceback"
OWNER = {"id": "owner-a", "role": "owner"}
BASE = "/api/integrations/qoyod"
CASES = [
    ("GET", "/admin/diagnostics/build", None),
    ("POST", "/admin/preview-reprocess", {"order_number": "123"}),
    ("GET", "/admin/normalize-row-self-test?trace_id=trace-a", None),
    ("POST", "/admin/adopt-existing-payment", {
        "salla_order_number": "123", "qoyod_invoice_payment_id": "p1",
        "confirm_token": "ADOPT-PAYMENT-123"}),
    ("POST", "/admin/force-reprocess-dry", {
        "salla_order_number": "123", "confirm_token": "FORCE-REPROCESS-DRY-123"}),
    ("POST", "/admin/approve-locked-payment", {
        "lock_attempt_id": "a1", "confirm_token": "APPROVE-PAYMENT-123"}),
    ("GET", "/reconciliation-report?sync_first=true", None),
]


class Collection:
    def __init__(self, rows, reads):
        self.rows, self.reads = rows, reads

    async def find_one(self, query, projection=None):
        self.reads.append(query)
        for row in self.rows:
            if all(row.get(key) == value for key, value in query.items()):
                return deepcopy(row)
        return None


class DB:
    def __init__(self, actor=None, binding="owner-a", row=None):
        self.auth_reads, self.settings_reads, self.inbox_reads = [], [], []
        self.users = Collection([actor or OWNER], self.auth_reads)
        self.qoyod_settings = Collection(
            [{"user_id": "main", "security_owner_id": binding}], self.settings_reads)
        self.integration_inbox = Collection([row] if row else [], self.inbox_reads)

    def __getattr__(self, name):
        raise AssertionError(f"Unexpected data/provider dependency: {name}")


async def request(db, user, method, path, body=None):
    async def current_user():
        return user
    app = FastAPI()
    app.include_router(routes.make_qoyod_router(db, current_user), prefix="/api")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        return await client.request(method, BASE + path, json=body)


@pytest.mark.asyncio
@pytest.mark.parametrize("method,path,body", CASES)
@pytest.mark.parametrize("actor,binding,context", [
    (OWNER, None, OWNER),
    (OWNER, "other-owner", OWNER),
    (OWNER, ["owner-a"], OWNER),
    ({"id": "other-owner", "role": "owner"}, "owner-a", {"id": "other-owner"}),
    ({"id": "employee-a", "role": "operations", "created_by": "owner-a"},
     "owner-a", {"id": "employee-a", "role": "owner", "is_owner": True}),
    ({"id": "admin-a", "role": "admin"}, "owner-a", {"id": "admin-a"}),
    ({"id": "employee-a", "role": "operations", "created_by": "owner-a"},
     "owner-a", {"id": "owner-a", "role": "owner", "_mobile_actor_id": "employee-a"}),
])
async def test_sensitive_route_denies_before_work(monkeypatch, method, path, body,
                                                  actor, binding, context):
    db = DB(actor=actor, binding=binding)

    def unexpected(*args, **kwargs):
        raise AssertionError("Selected helper reached before authorization")

    for module_name, function in [
        ("sas_build_diagnostics", "build_diagnostics_report"),
        ("adopt_existing_payment", "adopt_existing_payment"),
        ("force_reprocess_dry", "force_reprocess_dry_row"),
        ("approve_locked_payment", "approve_locked_payment"),
        ("reconciliation_v2", "run_reconciliation_v2"),
        ("qoyod_invoices_sync", "sync_qoyod_invoices"),
    ]:
        module = importlib.import_module("integrations.qoyod." + module_name)
        monkeypatch.setattr(module, function, unexpected)
    monkeypatch.setattr(routes, "preview_reprocess_one_order", unexpected)
    monkeypatch.setattr(routes, "get_api_key", unexpected)
    response = await request(db, context, method, path, body)
    assert response.status_code == 403
    assert db.inbox_reads == []
    assert db.auth_reads == [{"id": actor["id"]}]


@pytest.mark.asyncio
async def test_build_diagnostic_keeps_identity_without_host_internals(monkeypatch):
    from integrations.qoyod import sas_build_diagnostics as build
    monkeypatch.setattr(build, "build_diagnostics_report", lambda: {
        "git_sha": "a" * 40, "process_cwd": CANARY, "env_flags": {"secret": CANARY},
        "pipeline_module": {"loaded": True, "sha256_first16": "a" * 16,
                            "module_path": CANARY, "read_error": CANARY},
        "worker_task": {"loaded": True, "worker_task_present": True,
                        "worker_task_done": False, "import_error": CANARY},
        "acceptance": {"code_matches_expected": True},
    })
    response = await request(DB(), OWNER, *CASES[0])
    assert response.status_code == 200
    assert response.json()["git_sha"] == "a" * 40
    assert response.json()["worker_task"]["worker_task_present"] is True
    assert CANARY not in response.text


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [False, True, "throw"])
async def test_preview_projection_covers_success_nested_failure_and_exception(monkeypatch, failure):
    async def preview(db, **kwargs):
        assert kwargs["user_id"] == "main"
        if failure == "throw":
            raise RuntimeError(CANARY)
        return {
            "ok": not failure, "mode": "preview", "qoyod_request_sent": False,
            "message": CANARY, "error_code": CANARY, "extra": {"raw": CANARY},
            "errors": [{"message": CANARY}],
            "stages": {
                "customer_preview": {"ok": True, "request_body": {"contact": CANARY}},
                "normalize": {"ok": True, "canonical_preview": {
                    "order_number": "123", "total_amount": 115, "customer": CANARY},
                    "items": [{"sku": "sku-1", "quantity": 1, "raw": CANARY}]},
                "preflight": {"ok": False, "exception": CANARY,
                              "failures": [{"raw_body": CANARY}]},
            },
            "safety_summary": {"salla_total": 115, "expected_qoyod_total": 115,
                               "approval_required_to_send": True, "extra_charges": {"raw": CANARY}},
        }
    monkeypatch.setattr(routes, "preview_reprocess_one_order", preview)
    response = await request(DB(), OWNER, *CASES[1])
    assert response.status_code == 200
    assert CANARY not in response.text
    assert response.json()["qoyod_request_sent"] is False
    if failure != "throw":
        assert response.json()["safety_summary"]["salla_total"] == 115
        assert response.json()["stages"]["normalize"]["items"][0]["sku"] == "sku-1"


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [None, "adapter", "normalizer"])
async def test_normalizer_projects_canonical_and_both_error_paths(monkeypatch, failure):
    from integrations.qoyod import legacy_adapter, normalizer
    canonical = {"order_number": "123", "customer": {"mobile": CANARY},
                 "items": [{"sku": "sku-1", "quantity": 1, "unit_price": 10,
                            "private": CANARY}]}
    row = {"user_id": "main", "trace_id": "trace-a", "raw_payload": {"private": CANARY},
           "canonical_payload": canonical, "salla_order_number": "123"}
    def adapt(raw):
        if failure == "adapter":
            raise RuntimeError(CANARY)
        return {"data": {"items": canonical["items"]}}, {"adapter_applied": True, "raw": CANARY}
    class DTO:
        def model_dump(self, **kwargs):
            return canonical
    def normalize(*args, **kwargs):
        if failure == "normalizer":
            raise RuntimeError(CANARY)
        return DTO()
    monkeypatch.setattr(legacy_adapter, "adapt", adapt)
    monkeypatch.setattr(normalizer, "normalize", normalize)
    db = DB(row=row)
    response = await request(db, OWNER, *CASES[2])
    assert response.status_code == 200
    assert CANARY not in response.text
    assert response.json()["stored_canonical"]["order_number"] == "123"
    assert db.inbox_reads == [{"user_id": "main", "trace_id": "trace-a"}]


@pytest.mark.asyncio
@pytest.mark.parametrize("index,module_name,function,exception", [
    (3, "adopt_existing_payment", "adopt_existing_payment", "AdoptPaymentRefused"),
    (4, "force_reprocess_dry", "force_reprocess_dry_row", "ForceReprocessRefused"),
    (5, "approve_locked_payment", "approve_locked_payment", "ApproveLockedPaymentRefused"),
])
@pytest.mark.parametrize("mode", ["success", "returned_failure", "refusal", "unhandled"])
async def test_recovery_only_returns_public_outcome(monkeypatch, index, module_name,
                                                  function, exception, mode):
    module = importlib.import_module("integrations.qoyod." + module_name)
    async def helper(db, **kwargs):
        assert kwargs["user_id"] == "main"
        assert kwargs["actor"] == "owner-a"
        if mode == "unhandled":
            raise RuntimeError(CANARY)
        if mode == "refusal":
            raise getattr(module, exception)("unsafe_code", CANARY, payload=CANARY)
        return {"ok": mode == "success", "outcome": "COMPLETED" if mode == "success" else "POST_FAILED",
                "qoyod_invoice_payment_id": "p1", "code": CANARY, "detail": CANARY,
                "payload_replayed": CANARY, "raw_response": {"raw": CANARY},
                "pipeline_result": {"error": CANARY}, "debug": {"raw": CANARY}}
    monkeypatch.setattr(module, function, helper)
    response = await request(DB(), OWNER, *CASES[index])
    assert response.status_code == 200
    assert CANARY not in response.text
    if mode == "success":
        assert response.json()["qoyod_invoice_payment_id"] == "p1"
        assert response.json()["outcome"] == "COMPLETED"
    else:
        assert response.json()["ok"] is False
        assert response.json()["code"] == "operation_failed"


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["success", "nested_failure", "sync_exception", "report_exception"])
async def test_reconciliation_guards_sync_and_projects_nested_errors(monkeypatch, mode):
    from integrations.qoyod import qoyod_invoices_sync, reconciliation_v2
    async def key(*args):
        return "fake-key"
    async def client(*args):
        return object()
    async def sync(db, **kwargs):
        if mode == "sync_exception":
            raise RuntimeError(CANARY)
        return {"ok": mode != "nested_failure", "fetched": 2,
                "error": CANARY, "raw_response": CANARY}
    async def report(db, **kwargs):
        assert kwargs["orders_user_id"] == "owner-a"
        assert kwargs["markers_user_id"] == "main"
        if mode == "report_exception":
            raise RuntimeError(CANARY)
        return {"ok": True, "counts": {"مطابق": 1}, "rows": [
            {"order_number": "123", "customer_name": "Authorized Customer", "salla_total": 115,
             "debug": {"match_source": "reference", "notes_snippet": CANARY}, "raw": CANARY}],
             "raw_response": CANARY}
    monkeypatch.setattr(routes, "get_api_key", key)
    monkeypatch.setattr(routes, "_build_qoyod_client_for", client)
    monkeypatch.setattr(qoyod_invoices_sync, "sync_qoyod_invoices", sync)
    monkeypatch.setattr(reconciliation_v2, "run_reconciliation_v2", report)
    response = await request(DB(), OWNER, *CASES[6])
    assert response.status_code == 200
    assert CANARY not in response.text
    body = response.json()
    assert body["sync_summary"]["ran"] is True
    assert body["ok"] is (mode == "success")
    if mode == "success":
        assert body["rows"][0]["customer_name"] == "Authorized Customer"
        assert body["counts"] == {"مطابق": 1}
    else:
        assert body["counts"] == {}
        assert body["rows"] == []


@pytest.mark.asyncio
async def test_settings_cannot_assign_security_owner_id():
    db = DB()
    response = await request(db, OWNER, "PUT", "/settings", {"security_owner_id": "owner-b"})
    assert response.status_code == 422
    assert db.auth_reads == db.settings_reads == []


@pytest.mark.asyncio
@pytest.mark.parametrize("foreign_attempt", [None, {"attempt_id": "a1", "user_id": "foreign"}])
async def test_locked_attempt_foreign_and_absent_have_same_public_refusal(foreign_attempt):
    db = DB()
    attempt_reads = []
    db.qoyod_write_lock_attempts = Collection([foreign_attempt] if foreign_attempt else [], attempt_reads)
    response = await request(db, OWNER, *CASES[5])
    assert response.status_code == 200
    assert response.json() == {"ok": False, "outcome": "REFUSED", "code": "row_not_found",
                               "detail": "operation_failed"}
    assert db.inbox_reads == []


@pytest.mark.asyncio
async def test_foreign_normalizer_row_is_not_visible():
    db = DB(row={"user_id": "foreign", "trace_id": "trace-a", "raw_payload": CANARY})
    response = await request(db, OWNER, *CASES[2])
    assert response.status_code == 404
    assert CANARY not in response.text


@pytest.mark.asyncio
async def test_operational_salla_statuses_keep_employee_access_and_safe_error(monkeypatch):
    from salla_integration.service import SallaError
    async def call(*args):
        assert args[1] == "owner-a"
        raise SallaError(CANARY)
    class EmptyCursor:
        def __aiter__(self):
            return self
        async def __anext__(self):
            raise StopAsyncIteration
    class Orders:
        def find(self, query, projection):
            assert query == {"user_id": "owner-a"}
            return EmptyCursor()
    db = DB()
    db.unified_orders = Orders()
    monkeypatch.setattr(routes, "call_salla", call)
    user = {"id": "employee-a", "role": "operations", "created_by": "owner-a"}
    response = await request(db, user, "GET", "/salla-order-statuses")
    assert response.status_code == 200
    assert response.json()["error"]["message"] == "provider_operation_failed"
    assert CANARY not in response.text
    assert db.auth_reads == db.settings_reads == []
