import pytest

from test_order_engine_routes import make_raw
from test_web_legacy_page_policy import access, prepare
from test_web_page_access import GRANTS, actor, build_client, save, store
from web_detail_pages import DETAIL_WEB_PAGES


async def fetch(client, key, identity=None, **params):
    return await client.get(f"{GRANTS}/details/detail.{key}", headers=actor("employee"),
                            params={**({"identity": identity} if identity is not None else {}), **params})


@pytest.mark.asyncio
async def test_order_and_analysis_grants_are_independent_and_tenant_scoped(store):
    async with await build_client(store) as client:
        await prepare(client, store, ["orders.list", "legacy.history"], [])
        raw = make_raw("1001", "2026-11-16T12:00:00+00:00")
        await store.unified_orders.insert_many([
            {"user_id": "owner", "order_number": "1001", "order_date": "2026-11-16", "raw_by_source": {"salla_direct": raw}},
            {"user_id": "other-owner", "order_number": "1002", "order_date": "2026-11-16", "raw_by_source": {"salla_direct": raw}},
        ])
        await store.analyses.insert_many([
            {"user_id": who, "id": i, "name": i, "report": {"summary": {"total_sales": 175, "private": "SECRET"},
              "orders_sample": [{"phone": "SECRET"}]}} for who, i in [("owner", "analysis-a"), ("other-owner", "analysis-b")]])
        assert (await fetch(client, "order", "1001")).status_code == 403
        assert (await fetch(client, "analysis", "analysis-a")).status_code == 403
        assert (await save(client, {"detail.order": "read", "detail.analysis": "read"}, 1)).status_code == 200
        result = await fetch(client, "order", "1001")
        assert result.status_code == 200, result.text
        assert "منتج" in result.text and "عميل اختبار" not in result.text and "raw_by_source" not in result.text
        assert (await fetch(client, "order", "1002")).status_code == 404
        assert (await client.get("/api/orders-v2/1001", headers=actor("employee"))).status_code == 403
        assert (await client.get("/api/orders-v2/1001/profitability", headers=actor("employee"))).status_code == 403
        assert (await fetch(client, "analysis")).json()["choices"] == [{"id": "analysis-a", "label": "analysis-a"}]
        read = await fetch(client, "analysis", "analysis-a")
        assert read.status_code == 200 and any(f["value"] == 175 for f in read.json()["facts"])
        assert "SECRET" not in read.text and read.headers["cache-control"] == "no-store, private"
        assert (await fetch(client, "analysis", "analysis-b")).status_code == 404
        assert (await save(client, {}, 2)).status_code == 200
        assert (await fetch(client, "order", "1001")).status_code == 403


@pytest.mark.asyncio
async def test_account_and_reconciliation_honor_resource_bindings_and_tenant(store):
    async with await build_client(store) as client:
        await prepare(client, store, ["detail.account", "detail.reconciliation"],
                      [access("bank", "account:good", scope="own"), access("bank", "collision"),
                       access("provider", "tabby"), access("provider", "tamara", report=False)])
        await store.accounts.insert_many([
            {"user_id": "owner", "id": "good", "name": "Granted bank", "account_type": "bank", "currency": "SAR", "current_balance": 120},
            {"user_id": "owner", "id": "collision", "account_type": "bank", "name": "Forbidden"},
            {"user_id": "owner", "id": "prov", "name": "Tabby", "account_type": "payment_platform",
             "normalized_payment_method": "tabby", "expected_orders_balance": 128, "currency": "SAR"},
            {"user_id": "owner", "id": "spoof", "name": "tabby", "account_type": "payment_platform", "normalized_payment_method": "tamara"},
            {"user_id": "other-owner", "id": "other", "name": "Foreign", "account_type": "payment_platform", "normalized_payment_method": "tabby"},
        ])
        await store.general_ledger.insert_many([{"user_id": who, "entity_type": "bank", "entity_id": "good",
             "posted_by": by, "entry_no": i, "status": "draft", "side": "debit", "amount": i}
             for who, by, i in [("owner", "employee", 1), ("owner", "peer", 2), ("other-owner", "employee", 3)]])
        await store.account_transactions.insert_one({"user_id": "owner", "account_id": "good", "amount": 123456})
        await store.transfers.insert_many([{"user_id": who, "from_account_id": "prov", "transfer_date": "2026-11-17",
             "amount": amount, "notes": "PRIVATE"} for who, amount in [("owner", 18), ("other-owner", 777)]])
        found = await fetch(client, "account")
        assert {c["id"] for c in found.json()["choices"]} == {"good", "prov"}
        assert (await fetch(client, "account", "collision")).status_code == 404
        assert (await fetch(client, "reconciliation", "good")).status_code == 404
        bank = await fetch(client, "account", "good")
        assert bank.status_code == 200 and bank.json()["facts"][-1]["value"] == 120
        assert [r["entry_no"] for r in bank.json()["tables"][0]["rows"]] == [1] and "123456" not in bank.text
        platform = await fetch(client, "reconciliation", "prov")
        assert [x["amount"] for x in platform.json()["tables"][0]["rows"]] == [18]
        assert "PRIVATE" not in platform.text
        for key in ["spoof", "other"]:
            assert (await fetch(client, "reconciliation", key)).status_code == 404


@pytest.mark.asyncio
async def test_parties_filter_own_exact_kind_range_and_paginate(store):
    async with await build_client(store) as client:
        await prepare(client, store, ["detail.supplier", "detail.entity"],
                      [access("supplier", "shared", scope="own", balance=False), access("courier", "shared", balance=False)])
        await store.counterparties.insert_many([{"user_id": "owner", "id": "shared", "name": kind, "kind": kind}
                                                for kind in ["supplier", "courier"]])
        def entry(n, kind="supplier", who="employee", owner="owner", entity="shared"):
            return {"user_id": owner, "entity_type": kind, "entity_id": entity, "sub_account": "payable",
                    "posted_by": who, "entry_no": n, "created_at": f"2026-11-{n:02d}", "status": "posted",
                    "side": "debit", "amount": n, "currency": "SAR", "notes": "SECRET"}
        await store.general_ledger.insert_many([entry(i) for i in range(1, 12)] + [
            entry(12, who="other"), entry(13, owner="other-owner"), entry(14, "courier"), entry(15, entity="ungranted")])
        res = (await fetch(client, "supplier", "shared")).json()["tables"][0]
        assert len(res["rows"]) == 8 and res["has_more"]
        assert [r["amount"] for r in (await fetch(client, "supplier", "shared", page=2)).json()["tables"][0]["rows"]] == [3, 2, 1]
        filtered = await fetch(client, "supplier", "shared", start="2026-11-06", end="2026-11-07")
        assert [r["amount"] for r in filtered.json()["tables"][0]["rows"]] == [7, 6]
        assert "SECRET" not in filtered.text
        assert (await fetch(client, "entity", "shared", kind="external")).status_code == 404
        assert (await fetch(client, "supplier", "ungranted")).status_code == 404
        assert (await fetch(client, "entity", "shared", kind="supplier")).status_code == 422
        assert (await fetch(client, "entity", "shared", kind="courier")).json()["tables"][0]["rows"][0]["amount"] == 14
        assert (await fetch(client, "supplier", "shared", owner="other-owner")).status_code == 422
        assert (await fetch(client, "supplier", "a/b")).status_code == 422
        assert (await fetch(client, "supplier", "shared", start="2026-11-30", end="2026-11-01")).status_code == 422


@pytest.mark.asyncio
async def test_active_v2_supplier_uses_approved_source_only(store, monkeypatch):
    async with await build_client(store) as client:
        await prepare(client, store, ["detail.supplier"], [access("supplier", "s", scope="own", balance=False)])
        await store.mz2_atomic_owners.insert_one({"_id": "owner", "ledger_backend_state": "v2_active"})
        def line(kind, identity, actor_id, n):
            return {"entity_type": kind, "entity_id": identity, "posted_by": actor_id, "entry_no": n,
                    "side": "credit", "amount": n, "currency": "SAR", "sub_account": "payable", "effective_at": "2026-11-18T09:00:00Z"}
        async def active(db, *, owner, required_accounts):
            assert owner == "owner" and required_accounts == [("supplier", "s", "payable")]
            return {"status": "available", "ledger_backend": "v2", "items": [
                line("supplier", "s", "employee", 2), line("supplier", "s", "other", 3), line("supplier", "x", "employee", 7)
            ]}
        monkeypatch.setattr("accounting_mz2_reports.read_mz2_ledger", active)
        response = await fetch(client, "supplier", "s")
        assert response.status_code == 200 and response.json()["facts"] == []
        assert [r["amount"] for r in response.json()["tables"][0]["rows"]] == [2]
        async def unavailable(*args, **kwargs):
            return {"status": "needs_opening_balance"}
        monkeypatch.setattr("accounting_mz2_reports.read_mz2_ledger", unavailable)
        assert (await fetch(client, "supplier", "s")).status_code == 409


def test_detail_catalogue_has_real_landing_for_every_native_detail():
    assert len({p["detail_route"] for p in DETAIL_WEB_PAGES}) == len(DETAIL_WEB_PAGES) == 6
    assert all(p["to"] == "/employee-detail/" + p["id"] and not p.get("owner_only") for p in DETAIL_WEB_PAGES)

@pytest.mark.asyncio
async def test_integration_focus_resolves_canonical_provider_for_the_authorized_owner(store, monkeypatch):
    from integrations_control_center.service import IntegrationsControlCenterService
    calls = []
    async def overview(self, owner, *, provider_ids):
        calls.append((owner, provider_ids))
        return {"providers": [{"provider": ident, "name_ar": ident, "accounts": [
            {"display_name": ident + "-account", "connection_status": "connected"}]} for ident in provider_ids]}
    monkeypatch.setattr(IntegrationsControlCenterService, "overview", overview)
    async with await build_client(store) as client:
        await prepare(client, store, ["apps.integrations", "apps.advertising"], [])
        for provider in ["snapchat_ads", "tiktok_ads", "google_ads", "meta_ads"]:
            for page in ["apps.integrations", "apps.advertising"]:
                url = f"{GRANTS}/marketing-read/{page}"
                response = await client.get(url, params={"q": provider}, headers=actor("employee"))
                assert response.status_code == 200 and response.json()["rows"][0][0] == provider
                assert calls[-1] == ("owner", {provider})
        assert (await client.get(url, params={"q": "other"}, headers=actor("employee"))).status_code == 422
