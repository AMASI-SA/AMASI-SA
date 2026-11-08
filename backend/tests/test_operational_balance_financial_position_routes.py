"""Authenticated, owner-scoped financial report; reads cannot post money."""
import asyncio
from copy import deepcopy
import sys
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI, HTTPException

from operational_balance_routes import make_operational_balance_router
from operational_balance_store import STATES
from test_operational_balance_supplier_workspace import ReadOnlyDB


PATH = "/api/operational-balances/financial-position"


def test_indexed_coverage_is_lossless_and_does_not_change_balances_or_permissions(monkeypatch):
    import json
    monkeypatch.setitem(sys.modules, "operational_open_cost_exposure", None)
    db = ReadOnlyDB()
    db.data[STATES][0]['engine']['issues'] = [
        {'code': 'unresolved_source_contract', 'source_id': str(i), 'message': 'Source needs review'}
        for i in range(300)]
    before = deepcopy(db.data)
    expanded = asyncio.run(get(db)).json()
    response = asyncio.run(get(db, suffix='?coverage_format=indexed'))
    assert response.status_code == 200
    indexed = response.json()
    assert indexed['coverage_format'] == 'indexed'
    assert len(json.dumps(indexed)) < len(json.dumps(expanded)) * .4
    restored = deepcopy(indexed)
    catalog = restored.pop('coverage_issue_catalog')
    restored.pop('coverage_format')
    for currency in restored['currencies'].values():
        for table in currency['tables']:
            coverage = table.get('coverage')
            if coverage is not None:
                coverage['issues'] = [catalog[i] for i in coverage.pop('issue_indexes')]
    restored['generated_at'] = expanded['generated_at']
    assert restored == expanded
    assert db.data == before
    assert asyncio.run(get(db, principal={}, suffix='?coverage_format=indexed')).status_code == 401
    assert asyncio.run(get(db, suffix='?coverage_format=omit')).status_code == 422


async def get(db, principal=None, suffix=""):
    app = FastAPI()

    async def current_user():
        if principal == {}:
            raise HTTPException(401)
        return principal or {"id": "owner"}

    app.include_router(make_operational_balance_router(db, current_user), prefix="/api")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://isolated") as client:
        return await client.get(PATH + suffix)


def install_cost_reader(monkeypatch, calls):
    async def project(db, owner, state, *, as_of=None):
        calls.append((owner, state["owner_id"], as_of))
        return {"status": "preview", "as_of": as_of, "engine_as_of": as_of,
                "started_at": state["started_at"], "currencies": {},
                "groups": [], "issues": [], "complete": True}

    monkeypatch.setitem(sys.modules, "operational_open_cost_exposure", SimpleNamespace(project=project))


def test_read_is_owner_scoped_and_uses_same_snapshot_cutoff(monkeypatch):
    calls = []
    install_cost_reader(monkeypatch, calls)
    db = ReadOnlyDB()
    before = deepcopy(db.data)
    response = asyncio.run(get(db, suffix="?owner_id=other"))
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["authority"] == "operational_balance_report"
    assert body["permissions"] == {"reports": True}
    assert body["currencies"]["SAR"]["metrics"]["debts_payable"] == "9135.00"
    assert calls == [("owner", "owner", db.data[STATES][0]["engine"]["as_of"])]
    assert db.data == before
    foreign = asyncio.run(get(db, {"id": "other"}))
    assert foreign.status_code == 200 and foreign.json()["currencies"] == {}


def test_missing_cost_dependency_does_not_fabricate_forecast(monkeypatch):
    monkeypatch.setitem(sys.modules, "operational_open_cost_exposure", None)
    response = asyncio.run(get(ReadOnlyDB()))
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["currencies"]["SAR"]["metrics"]["forecast_monetary_position"] is None
    assert not body["currencies"]["SAR"]["completeness"]["forecast"]
    assert body["dependencies"]


def test_order_labels_use_authenticated_owner_after_financial_projection(monkeypatch):
    import operational_financial_order_labels
    monkeypatch.setitem(sys.modules, "operational_open_cost_exposure", None)
    calls = []

    async def attach_labels(db, owner, result):
        calls.append((owner, result["authority"], result["currencies"]["SAR"]["metrics"]["debts_payable"]))
        return {**result, "label_enrichment_checked": True}

    monkeypatch.setattr(operational_financial_order_labels, "attach_order_numbers", attach_labels)
    response = asyncio.run(get(ReadOnlyDB(), suffix="?owner_id=other"))
    assert response.status_code == 200, response.text
    assert calls == [("owner", "operational_balance_report", "9135.00")]
    assert response.json()["label_enrichment_checked"] is True


def test_order_bearing_route_uses_real_canonical_label_reader_without_changing_money(monkeypatch):
    from operational_balance_engine import reconcile
    from operational_financial_position import build_financial_position
    from test_operational_balance_financial_order_labels import ReadOnlyDB as LabelDB, source

    monkeypatch.setitem(sys.modules, "operational_open_cost_exposure", None)

    class DBWithOrderIdentities(ReadOnlyDB):
        def __init__(self):
            super().__init__()
            self.order_identities = LabelDB([
                source(100, "TEST-REFERENCE-100"),
                source(100, "FOREIGN-REFERENCE", owner="other"),
                source(200, "100"),  # A matching public number is not the canonical ID.
            ])

        def __getitem__(self, name):
            if name == "unified_orders":
                return self.order_identities[name]
            return super().__getitem__(name)

    db = DBWithOrderIdentities()
    original = db.data[STATES][0]
    order = {"id": "100", "created_at": "2026-11-01T00:10:00+03:00",
             "updated_at": "2026-11-01T00:30:00+03:00", "currency": "SAR", "status": "delivered",
             "items": [], "carrier": {"id": "test-carrier", "party_type": "courier", "cost": "12.00"},
             "cod_required": True, "cod_amount": "200.00", "cod": {
                 "gross": "300.00", "collected": "100.00", "outstanding": "200.00",
                 "custody_amount": "200.00", "payment_method": "cod", "evidence_ids": [],
                 "evidence_complete": True}}
    db.data[STATES][0] = reconcile(original, {"orders": [order]}, original["engine"]["as_of"])
    before = deepcopy(db.data)
    identities_before = deepcopy(db.order_identities.records)

    response = asyncio.run(get(db, suffix="?owner_id=other&order_id=200"))
    assert response.status_code == 200, response.text
    body = response.json()
    deliveries = body["currencies"]["SAR"]["deliveries"]["rows"]
    assert len(deliveries) == 1
    assert deliveries[0]["order_id"] == "100" and deliveries[0]["order_number"] == "TEST-REFERENCE-100"
    assert deliveries[0]["shipping_confirmed"] == "12.00"
    assert deliveries[0]["collection_outstanding"] == "200.00"
    assert db.order_identities.calls == [{
        "query": {"user_id": "owner", "raw_by_source.salla_direct.id": {"$in": ["100", 100]}},
        "projection": {"_id": 0, "user_id": 1, "raw_by_source.salla_direct.id": 1,
                       "raw_by_source.salla_direct.reference_id": 1,
                       "raw_by_source.salla_direct.order_number": 1,
                       "g47_salla_snapshot.requires_authoritative_refresh": 1},
        "limit": 20_001, "length": 20_001,
    }]
    # Compare the complete public projection, not merely one balance, so label
    # enrichment cannot modify a fee, debt, cash amount, currency, or forecast.
    expected = build_financial_position(before[STATES][0], generated_at=body["generated_at"])
    expected["currencies"]["SAR"]["deliveries"]["rows"][0]["order_number"] = "TEST-REFERENCE-100"
    actual = {key: value for key, value in body.items() if key not in {"permissions", "session_scope"}}
    assert actual == expected
    assert "PRIVATE" not in str(body) and "FOREIGN-REFERENCE" not in str(body)
    assert db.data == before and db.order_identities.records == identities_before


@pytest.mark.parametrize("code", ["operational_source_rejected", "operational_source_scope_too_large",
                                  "operational_source_owner_mismatch"])
def test_known_cost_scope_failure_preserves_actual_snapshot_and_withholds_forecast(monkeypatch, code):
    async def rejected(*args, **kwargs):
        raise ValueError(code)

    monkeypatch.setitem(sys.modules, "operational_open_cost_exposure", SimpleNamespace(project=rejected))
    response = asyncio.run(get(ReadOnlyDB()))
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["currencies"]["SAR"]["metrics"]["debts_payable"] == "9135.00"
    assert body["currencies"]["SAR"]["metrics"]["forecast_monetary_position"] is None
    assert any(issue["code"] == code for issue in body["issues"])


def test_unexpected_cost_reader_failure_is_not_hidden_as_missing_data(monkeypatch):
    async def broken(*args, **kwargs):
        raise ValueError("unexpected_calculation_failure")

    monkeypatch.setitem(sys.modules, "operational_open_cost_exposure", SimpleNamespace(project=broken))
    with pytest.raises(ValueError, match="unexpected_calculation_failure"):
        asyncio.run(get(ReadOnlyDB()))


def test_missing_engine_timestamp_never_queries_fresh_costs_as_if_historical(monkeypatch):
    calls = []
    install_cost_reader(monkeypatch, calls)
    db = ReadOnlyDB()
    db.data[STATES][0]["engine"].pop("as_of")
    response = asyncio.run(get(db))
    assert response.status_code == 200, response.text
    assert calls == []
    assert response.json()["as_of"] is None


def test_read_requires_authentication_and_active_membership(monkeypatch):
    monkeypatch.setitem(sys.modules, "operational_open_cost_exposure", None)
    assert asyncio.run(get(ReadOnlyDB(), {})).status_code == 401
    db = ReadOnlyDB()
    db.data["users"][0]["disabled"] = True
    assert asyncio.run(get(db)).status_code == 403


def test_mobile_read_grant_does_not_promote_real_actor_to_consolidated_owner(monkeypatch):
    monkeypatch.setitem(sys.modules, "operational_open_cost_exposure", None)
    db = ReadOnlyDB()
    actor = {"id": "owner", "role": "owner", "_session_client": "amasi_mobile", "_mobile_actor_id": "staff"}
    response = asyncio.run(get(db, actor))
    assert response.status_code == 403, response.text
    assert response.json()["detail"]["code"] == "operational_consolidated_report_owner_required"
    db.data["mezan_mobile_app_access_v1"][0]["permissions"] = []
    assert asyncio.run(get(db, actor)).status_code == 403


def test_native_accounting_activation_prevents_competing_current_balances(monkeypatch):
    import operational_balance_service
    calls = []
    install_cost_reader(monkeypatch, calls)

    async def accounting_active(db, owner):
        raise HTTPException(409, detail={"code": "operational_accounting_active"})

    monkeypatch.setattr(operational_balance_service, "accounting_inactive", accounting_active)
    response = asyncio.run(get(ReadOnlyDB()))
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "accounting_active"
    assert response.json()["currencies"] == {}
    assert calls == []
