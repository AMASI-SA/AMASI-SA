"""Supplier account projection and authenticated read-only boundary; no database writes."""
import asyncio
from copy import deepcopy

import httpx
import pytest
from fastapi import FastAPI, HTTPException

from mobile_app_permissions import OPERATIONAL_APP_READ, OPERATIONAL_APP_WRITE
from operational_balance_routes import make_operational_balance_router
from operational_balance_store import STATES, initial
from operational_supplier_workspace import supplier_workspace


START = "2026-11-01T00:00:00+03:00"
APPROVED = "2026-11-01T00:15:00+03:00"
AS_OF = "2026-11-01T01:00:00+03:00"
PATH = "/api/operational-balances/suppliers"


def state_for(owner="owner"):
    state = initial(owner)
    state.update(status="active", started_at=START)
    state["openings"] = {"opening-supplier": {
        "id": "opening-supplier", "party_type": "supplier", "party_id": "supplier-one",
        "name": "Test supplier", "currency": "SAR", "direction": "for_party", "amount": "100.00",
        "approved_at": START,
    }}
    common = {
        "kind": "supplier", "party_type": "supplier", "party_id": "supplier-one", "name": "Test supplier",
        "currency": "SAR", "direction": "payable", "expected": "0.00", "business_date": "2026-11-01",
        "source_invoice_id": "invoice-new", "source_invoice_number": "TEST-SI-001",
        "source_invoice_approved_at": APPROVED,
    }
    state["engine"] = {"as_of": AS_OF, "obligations": {
        "product": {**common, "id": "product", "gross_amount": "50.00", "returned_amount": "5.00",
                    "confirmed": "45.00", "evidence_ids": ["receipt:product"]},
        "service": {**common, "id": "service", "gross_amount": "10.00", "returned_amount": "0.00",
                    "confirmed": "10.00", "evidence_ids": ["receipt:service"]},
        "staff": {"id": "staff", "kind": "salary", "party_type": "employee", "party_id": "staff",
                  "currency": "SAR", "direction": "payable", "expected": "0.00", "confirmed": "9000.00"},
    }}
    state["movements"] = [{
        "id": "payment", "party_type": "supplier", "party_id": "supplier-one", "currency": "SAR",
        "direction": "outgoing", "kind": "payment", "amount": "20.00", "occurred_at": AS_OF,
        "allocations": [{"obligation_id": "product", "amount": "20.00"}],
    }]
    return state


def test_invoice_and_opening_remain_distinct_and_balances_match_report():
    state = state_for()
    before = deepcopy(state)
    result = supplier_workspace(state)
    assert result["authority"] == "operational_balance_report"
    assert result["accounts"] == [{
        "supplier_id": "supplier-one", "name": "Test supplier", "currency": "SAR", "opening": "-100.00",
        "expected_payable": "0.00", "confirmed_payable": "155.00", "settled": "20.00",
        "outstanding_payable": "135.00", "outstanding_receivable": "0.00",
    }]
    assert result["invoices"] == [{
        "id": "invoice-new", "supplier_id": "supplier-one", "currency": "SAR",
        "invoice_number": "TEST-SI-001", "approved_at": APPROVED,
        "gross_amount": "60.00", "returned_amount": "5.00", "confirmed": "55.00",
        "settled": "20.00", "outstanding": "35.00",
    }]
    assert result["openings"][0]["amount"] == "100.00"
    assert result["openings"][0]["outstanding"] == "100.00"
    assert result["summaries"] == {"SAR": {"payable": "135.00", "receivable": "0.00",
                                          "settled": "20.00", "invoice_count": 1}}
    assert state == before
    assert "9000.00" not in str(result)


@pytest.mark.parametrize("status", ["draft", "frozen", "accounting_active"])
def test_inactive_system_does_not_present_operational_balances_as_current(status):
    state = state_for()
    state["status"] = status
    result = supplier_workspace(state)
    assert result["active"] is False
    assert result["accounts"] == result["invoices"] == result["openings"] == []
    assert result["summaries"] == {}


def test_missing_invoice_provenance_never_erases_balance_or_invents_an_invoice():
    state = state_for()
    del state["engine"]["obligations"]["product"]["source_invoice_id"]
    del state["engine"]["obligations"]["service"]["source_invoice_id"]
    result = supplier_workspace(state)
    assert result["accounts"][0]["outstanding_payable"] == "135.00"
    assert result["invoices"] == []
    assert result["invoice_provenance_incomplete"] is True


def test_missing_invoice_metadata_amount_is_unavailable_never_an_invented_zero():
    state = state_for()
    del state["engine"]["obligations"]["product"]["gross_amount"]
    result = supplier_workspace(state)
    assert result["invoices"][0]["gross_amount"] is None
    assert result["invoices"][0]["confirmed"] == "55.00"
    assert result["accounts"][0]["outstanding_payable"] == "135.00"


def test_generic_evidence_conflicts_on_supplier_receipts_remain_visible():
    state = state_for()
    state["engine"]["issues"] = [
        {"code": "confirmed_evidence_changed", "source_id": "receipt:invoice-new:piece-one:product"},
        {"code": "confirmed_evidence_changed", "source_id": "supplier:order-old:piece-two"},
        {"code": "confirmed_evidence_changed", "source_id": "shipping:order-other"},
    ]
    result = supplier_workspace(state)
    assert result["source_issues_count"] == 2
    assert result["accounts"][0]["outstanding_payable"] == "135.00"


def test_supplier_currencies_are_never_added_together():
    state = state_for()
    state["engine"]["obligations"]["foreign-currency"] = {
        **state["engine"]["obligations"]["service"], "id": "foreign-currency", "currency": "USD",
        "source_invoice_id": "invoice-usd", "gross_amount": "12.00", "confirmed": "12.00",
    }
    result = supplier_workspace(state)
    assert result["summaries"]["SAR"]["payable"] == "135.00"
    assert result["summaries"]["USD"]["payable"] == "12.00"


class ReadOnlyCollection:
    def __init__(self, db, name):
        self.db, self.name = db, name

    async def find_one(self, query, projection=None):
        self.db.reads.append((self.name, deepcopy(query)))
        return next((deepcopy(row) for row in self.db.data.get(self.name, [])
                     if all(row.get(key) == value for key, value in query.items())), None)

    def __getattr__(self, name):
        raise AssertionError(f"Read endpoint attempted {self.name}.{name}")


class ReadOnlyDB:
    def __init__(self):
        self.reads = []
        self.data = {
            "users": [
                {"id": "owner", "role": "owner", "is_active": True},
                {"id": "other", "role": "owner", "is_active": True},
                {"id": "staff", "role": "employee", "created_by": "owner", "is_active": True},
            ],
            STATES: [state_for()],
            "mezan_role_assignments_v2": [{"owner_user_id": "owner", "user_id": "staff",
                                           "extra_permissions": ["suppliers.read"]}],
            "mezan_mobile_app_access_v1": [{"owner_user_id": "owner", "user_id": "staff",
                                           "enabled": True, "permissions": [OPERATIONAL_APP_READ]}],
        }

    def __getitem__(self, name):
        assert name in {"users", STATES, "mezan_role_assignments_v2", "mezan_mobile_app_access_v1",
                        "mezan_employees_v2", "mz2_atomic_owners", "settings"}, name
        return ReadOnlyCollection(self, name)


async def request(db, principal=None, suffix=""):
    app = FastAPI()
    async def current_user():
        if principal == {}:
            raise HTTPException(401)
        return principal or {"id": "owner"}
    app.include_router(make_operational_balance_router(db, current_user), prefix="/api")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://isolated") as client:
        return await client.get(PATH + suffix)


def test_http_is_owner_scoped_read_only_and_cannot_query_another_owner():
    db = ReadOnlyDB()
    before = deepcopy(db.data)
    response = asyncio.run(request(db, suffix="?owner_id=other&supplier_id=foreign"))
    assert response.status_code == 200, response.text
    assert response.json()["accounts"][0]["outstanding_payable"] == "135.00"
    assert response.json()["permissions"] == {"record_movements": True}
    foreign = asyncio.run(request(db, {"id": "other"}))
    assert foreign.status_code == 200 and foreign.json()["accounts"] == []
    assert db.data == before
    assert (STATES, {"_id": "owner", "owner_id": "owner"}) in db.reads
    assert (STATES, {"_id": "other", "owner_id": "other"}) in db.reads


def test_http_requires_active_membership_and_supplier_read_permission():
    db = ReadOnlyDB()
    db.data["mezan_role_assignments_v2"][0]["extra_permissions"] = []
    assert asyncio.run(request(db, {"id": "staff"})).status_code == 403
    assert asyncio.run(request(db, {})).status_code == 401
    db.data["users"][0]["disabled"] = True
    assert asyncio.run(request(db)).status_code == 403


def test_mobile_read_and_write_grants_stay_independent_without_owner_impersonation():
    db = ReadOnlyDB()
    principal = {"id": "owner", "role": "owner", "_session_client": "amasi_mobile", "_mobile_actor_id": "staff"}
    read = asyncio.run(request(db, principal))
    assert read.status_code == 200, read.text
    assert read.json()["permissions"] == {"record_movements": False}
    db.data["mezan_mobile_app_access_v1"][0]["permissions"] = [OPERATIONAL_APP_WRITE]
    assert asyncio.run(request(db, principal)).status_code == 403
    db.data["mezan_mobile_app_access_v1"][0]["permissions"] = [OPERATIONAL_APP_READ, OPERATIONAL_APP_WRITE]
    both = asyncio.run(request(db, principal))
    assert both.status_code == 200 and both.json()["permissions"]["record_movements"] is True
    db.data["mezan_role_assignments_v2"][0]["extra_permissions"] = []
    assert asyncio.run(request(db, principal)).status_code == 403


def test_native_accounting_activation_cannot_leave_two_current_authorities():
    db = ReadOnlyDB()
    db.data["mz2_atomic_owners"] = [{"_id": "owner", "ledger_backend_state": "v2_active"}]
    before = deepcopy(db.data)
    result = asyncio.run(request(db)).json()
    assert result["status"] == "accounting_active" and not result["active"]
    assert result["accounts"] == result["invoices"] == []
    assert result["permissions"]["record_movements"] is False
    assert db.data == before
