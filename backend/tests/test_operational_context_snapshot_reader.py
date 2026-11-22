"""Legacy context keeps its public contract without reading historical audit pages."""
import asyncio
from copy import deepcopy

import httpx
import pytest
from fastapi import FastAPI, HTTPException

import operational_balance_archive as archive
import operational_financial_position_state as financial_state
from operational_balance_routes import make_operational_balance_router
from operational_balance_store import STATES, digest, read
from test_operational_balance_supplier_workspace import ReadOnlyCollection, ReadOnlyDB


PATH = "/api/operational-balances/context"
ISSUES = [{"code": "synthetic_current_issue", "source_id": "saved-proof", "amount": "11.00"}]


class ContextCollection(ReadOnlyCollection):
    async def find_one(self, query, projection=None):
        self.db.queries.append((self.name, deepcopy(query), deepcopy(projection)))
        row = await super().find_one(query, projection)
        if self.name == "users":
            self.db.actor_reads += 1
            if row and self.db.revoke_after_gate and self.db.actor_reads > 1:
                row["disabled"] = True
        # Honor the actual top-level exclusion projection, unlike the broad
        # fixture's default collection. Every other DB method still raises.
        if row and projection and all(value == 0 for value in projection.values()):
            row = {key: value for key, value in row.items() if key not in projection}
        return row


class ContextDB(ReadOnlyDB):
    def __init__(self):
        super().__init__()
        self.queries = []
        self.actor_reads = 0
        self.revoke_after_gate = False

    def __getitem__(self, name):
        if name != archive.AUDIT_PAGES:
            super().__getitem__(name)  # Keep the existing read-only allowlist.
        return ContextCollection(self, name)


def archived_context(*, packed=False, status="active"):
    db = ContextDB()
    state = db.data[STATES][0]
    state["status"] = status
    if status == "draft":
        state["started_at"] = None
    state["engine"]["issues"] = deepcopy(ISSUES)
    state["requests"] = {"synthetic-private-receipt": {"result": "private"}}
    state["audit"] = [{"reason": "synthetic-private-action"}]
    pages, references = [], []
    for index in range(3):
        events = [{"id": f"synthetic-{index}-{offset}", "amount": "1.00"} for offset in range(2)]
        key = archive.content_key("owner", index * 2, events)
        pages.append({"_id": key, "owner_id": "owner", "start": index * 2, "events": events})
        references.append({"id": key, "count": len(events)})
    state[archive.MARKER] = archive.manifest(6, references)
    state["engine"]["audit"] = {"tail": [{"id": "synthetic-current-tail"}]}
    if packed:
        state = archive.compact_issues(archive.compact_financial_snapshot(state, 1_000_000), 1_000_000)
        db.data[STATES][0] = state
    db.data[archive.AUDIT_PAGES] = pages
    return db


async def get_context(db, principal=None):
    app = FastAPI()

    async def current_user():
        if principal == {}:
            raise HTTPException(401)
        return principal or {"id": "owner"}

    app.include_router(make_operational_balance_router(db, current_user), prefix="/api")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://isolated") as client:
        # A query argument cannot substitute a different merchant.
        return await client.get(PATH, params={"owner_id": "other"})


def archive_reads(db):
    return [query for name, query in db.reads if name == archive.AUDIT_PAGES]


@pytest.mark.parametrize("packed", [False, True], ids=["inline", "packed"])
@pytest.mark.parametrize("status", ["draft", "active", "frozen"])
def test_context_preserves_complete_response_without_materializing_audit(monkeypatch, packed, status):
    db = archived_context(packed=packed, status=status)
    before = deepcopy(db.data)
    # Independent reference: the unchanged complete store reader, through the
    # same actual HTTP response/auth path. No response fields are synthesized.
    with monkeypatch.context() as baseline:
        baseline.setattr(financial_state, "read_financial_position_state", read)
        expected = asyncio.run(get_context(db))
    assert expected.status_code == 200, expected.text
    assert len(archive_reads(db)) == 3
    db.reads.clear()
    db.queries.clear()

    response = asyncio.run(get_context(db))

    assert response.status_code == 200, response.text
    assert response.content == expected.content
    assert response.json() == {
        "status": status, "started_at": before[STATES][0]["started_at"],
        "operational_banks": None, "can_create_cash": True,
        "can_create_external_person": True, "can_select_ad_accounts": True,
        "can_configure_ad_funding": True, "can_manage_inventory_opening": True,
        "can_manage_salary_start": status == "active", "can_create_owner_withdrawal": True,
        "session_scope": digest(["owner", "owner"]), "opening_count": 1,
        "permissions": {"view": True, "move": True, "manage": True, "reports": True},
        "issues": ISSUES,
    }
    assert archive_reads(db) == []
    assert [item for item in db.queries if item[0] == STATES] == [
        (STATES, {"_id": "owner", "owner_id": "owner"}, {"requests": 0, "audit": 0})
    ]
    assert db.data == before


def test_absent_state_keeps_the_original_draft_context(monkeypatch):
    db = ContextDB()
    db.data[STATES] = []
    before = deepcopy(db.data)
    with monkeypatch.context() as baseline:
        baseline.setattr(financial_state, "read_financial_position_state", read)
        expected = asyncio.run(get_context(db))
    response = asyncio.run(get_context(db))
    assert response.status_code == expected.status_code == 200
    assert response.content == expected.content
    assert response.json()["status"] == "draft"
    assert response.json()["started_at"] is None
    assert response.json()["opening_count"] == 0
    assert response.json()["issues"] == []
    assert db.data == before and archive_reads(db) == []


def test_mobile_owner_keeps_bank_assignments_flags_and_session(monkeypatch):
    import operational_app_banks

    async def bank_choices(db, owner, kind="bank"):
        assert owner == "owner"
        return [{"id": "synthetic-bank" if kind == "bank" else "synthetic-cash"}]

    # Isolate only the unrelated bank directory. assigned_banks, fresh actor
    # lookup, native permission policy, and the real route remain exercised.
    monkeypatch.setattr(operational_app_banks, "bank_choices", bank_choices)
    db = archived_context(packed=True)
    before = deepcopy(db.data)
    principal = {"id": "owner", "_session_client": "amasi_mobile"}
    with monkeypatch.context() as baseline:
        baseline.setattr(financial_state, "read_financial_position_state", read)
        expected = asyncio.run(get_context(db, principal))
    assert expected.status_code == 200, expected.text
    db.reads.clear()
    response = asyncio.run(get_context(db, principal))
    assert response.status_code == 200, response.text
    assert response.content == expected.content
    body = response.json()
    assert body["session_scope"] == digest(["owner", "owner"])
    assert body["operational_banks"] == {
        "bank_ids": ["synthetic-bank"], "cash_ids": ["synthetic-cash"], "default_bank_id": None,
    }
    assert body["permissions"] == {"view": True, "move": True, "manage": False, "reports": True}
    assert body["can_manage_inventory_opening"] is False
    assert body["can_manage_salary_start"] is False
    assert body["issues"] == ISSUES
    assert db.data == before and archive_reads(db) == []


@pytest.mark.parametrize("history_damage", ["missing", "changed"])
def test_current_context_does_not_claim_historical_page_validation(history_damage):
    db = archived_context(packed=True)
    if history_damage == "missing":
        db.data[archive.AUDIT_PAGES].pop(0)
    else:
        db.data[archive.AUDIT_PAGES][0]["events"][0]["amount"] = "999.00"
    before = deepcopy(db.data)
    # Audit and mutation readers still fail closed on incomplete history.
    with pytest.raises(HTTPException) as failure:
        asyncio.run(read(db, "owner"))
    assert failure.value.status_code == 503
    assert failure.value.detail["code"] == "operational_audit_archive_invalid"
    db.reads.clear()
    response = asyncio.run(get_context(db))
    assert response.status_code == 200, response.text
    assert response.json()["issues"] == ISSUES
    assert archive_reads(db) == [] and db.data == before


@pytest.mark.parametrize("damage", ["financial_packet", "issues_packet", "audit_manifest"])
def test_current_corruption_preserves_exact_original_503_response(monkeypatch, damage):
    db = archived_context(packed=True)
    key = {"financial_packet": archive.SNAPSHOT, "issues_packet": archive.ISSUES,
           "audit_manifest": archive.MARKER}[damage]
    db.data[STATES][0][key]["sha256"] = "0" * 64
    before = deepcopy(db.data)
    with monkeypatch.context() as baseline:
        baseline.setattr(financial_state, "read_financial_position_state", read)
        expected = asyncio.run(get_context(db))
    response = asyncio.run(get_context(db))
    assert response.status_code == expected.status_code == 503
    assert response.content == expected.content
    assert response.json() == {"detail": {
        "code": "operational_audit_archive_invalid",
        "message": "تعذر التحقق من اكتمال سجل التدقيق التشغيلي",
    }}
    assert archive_reads(db) == [] and db.data == before


@pytest.mark.parametrize("case", ["anonymous", "disabled", "staff", "mobile_staff", "revoked_page"])
def test_denied_scope_cannot_read_current_or_historical_state(case):
    db = archived_context(packed=True)
    principal = {"id": "owner", "role": "owner", "is_owner": True}
    if case == "anonymous":
        principal = {}
    elif case == "disabled":
        db.data["users"][0]["disabled"] = True
    elif case in {"staff", "revoked_page"}:
        principal["id"] = "staff"  # Stale/forged owner claims do not authorize staff.
        if case == "revoked_page":
            db.data["users"][2]["web_page_access"] = {"owner_id": "owner", "grants": [], "revision": 2}
    else:
        principal.update(_session_client="amasi_mobile", _mobile_actor_id="staff")
    before = deepcopy(db.data)
    response = asyncio.run(get_context(db, principal))
    assert response.status_code == (401 if case == "anonymous" else 403), response.text
    assert not any(name in {STATES, archive.AUDIT_PAGES} for name, _ in db.reads)
    assert db.data == before


def test_revocation_after_router_gate_is_rechecked_before_state_read():
    db = archived_context(packed=True)
    db.revoke_after_gate = True
    before = deepcopy(db.data)
    response = asyncio.run(get_context(db, {"id": "owner", "role": "owner"}))
    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "operational_actor_inactive"
    assert db.actor_reads >= 2
    assert not any(name in {STATES, archive.AUDIT_PAGES} for name, _ in db.reads)
    assert db.data == before


@pytest.mark.parametrize("status,detail", [
    (403, {"code": "synthetic_permission_rejected"}),
    (503, {"code": "synthetic_other_failure", "message": "Synthetic unavailable"}),
])
def test_reader_boundary_does_not_reclassify_unrelated_http_failures(monkeypatch, status, detail):
    async def rejected(*args):
        raise HTTPException(status, detail=detail)

    monkeypatch.setattr(financial_state, "read_financial_position_state", rejected)
    db = ContextDB()
    response = asyncio.run(get_context(db))
    assert response.status_code == status
    assert response.json() == {"detail": detail}
    assert not any(name in {STATES, archive.AUDIT_PAGES} for name, _ in db.reads)
