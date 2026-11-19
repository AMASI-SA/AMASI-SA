import pytest
from fastapi import HTTPException

from test_web_page_access import GRANTS, actor, build_client, save, store
from web_legacy_page_policy import LEGACY_WEB_PAGES, enforce_legacy_web_pages


def access(kind, identity, *, scope="all", balance=True, report=True):
    return {"kind": kind, "id": identity, "use": False, "movements": scope, "balance": balance, "report": report, "print": False}


async def prepare(client, db, pages, resources):
    result = await save(client, dict.fromkeys(pages, "read"))
    assert result.status_code == 200, result.text
    await db.users.update_one({"id": "employee"}, {"$set": {"operational_balance_access": {
        "owner_id": "owner", "version": 1, "enabled": True, "resources": resources, "operations": [],
    }}})


async def read(client, name, page=1):
    response = await client.get(f"{GRANTS}/legacy-read/{name}", params={"page": page}, headers=actor("employee"))
    assert response.status_code == 200, response.text
    return response.json()


@pytest.mark.asyncio
async def test_legacy_list_paging_tenant_and_projection(store):
    async with await build_client(store) as client:
        await prepare(client, store, ["legacy.orders"], [])
        await store.unified_orders.insert_many([
            {"user_id": "owner", "order_number": str(i), "order_date": f"2026-11-{i:02d}",
             "raw_by_source": {"salla": {"customer": "private"}}} for i in range(1, 11)
        ] + [{"user_id": "other-owner", "order_number": "secret", "order_date": "2099-11-19"}])
        first = await read(client, "legacy.orders")
        second = await read(client, "legacy.orders", 2)
        assert first["has_more"] and not second["has_more"]
        assert [row["order_number"] for row in first["rows"]] == list(map(str, range(10, 2, -1)))
        assert len(second["rows"]) == 2
        assert "raw_by_source" not in str(first)
        assert (await client.get(f"{GRANTS}/legacy-read/legacy.accounts", headers=actor("employee"))).status_code == 403
        assert (await save(client, {"legacy.team": "read"}, 1)).status_code == 422


@pytest.mark.asyncio
async def test_legacy_ledger_enforces_bank_namespace_own_only_and_separate_custody(store):
    async with await build_client(store) as client:
        await prepare(client, store, ["legacy.transactions", "legacy.employees.custody_balances"],
                      [access("bank", "account:shared", scope="own"), access("bank", "native-not-historical"),
                       access("employee", "worker"), access("employee_custody", "worker", balance=False, scope="none")])
        def leg(n, kind, identity, actor="employee", sub=None, tenant="owner"):
            return {"user_id": tenant, "entity_type": kind, "entity_id": identity, "entry_no": n,
                    "posted_by": actor, "sub_account": sub, "side": "debit", "amount": 15,
                    "status": "posted", "currency": "SAR", "notes": "private other participant"}
        await store.general_ledger.insert_many([
            leg(1, "bank", "shared"), leg(2, "bank", "shared", actor="other"),
            leg(3, "bank", "native-not-historical"), leg(4, "employee", "worker", sub="salary"),
            leg(5, "employee", "worker", sub="custody"), leg(6, "bank", "shared", tenant="other-owner"),
        ])
        visible = await read(client, "legacy.transactions")
        assert [row["entry_no"] for row in visible["rows"]] == [4, 1]
        assert "private other participant" not in str(visible)
        assert (await read(client, "legacy.employees.custody_balances"))["rows"] == []


@pytest.mark.asyncio
async def test_legacy_reports_need_separate_financial_grants_and_keep_currency(store):
    async with await build_client(store) as client:
        await prepare(client, store, ["legacy.suppliers_ledger", "legacy.reports.suppliers", "legacy.payment_settlements"],
                      [access("supplier", "s1", report=False), access("provider", "tabby"), access("provider", "tamara", scope="own")])
        await store.general_ledger.insert_many([
            {"user_id": "owner", "entity_type": "supplier", "entity_id": "s1", "side": "credit", "amount": amount,
             "status": "posted", "currency": currency, "entry_type": kind}
            for amount, currency, kind in [(30, "SAR", "accrual"), (10, "USD", "accrual"), (2000, "SAR", "reversal")]
        ])
        assert sorted(row["net"] for row in (await read(client, "legacy.suppliers_ledger"))["rows"]) == [-30, -10]
        assert not (await read(client, "legacy.reports.suppliers"))["rows"]
        await store.settlement_files.insert_many([{"user_id": user, "provider": provider, "rows": n, "original": "sensitive"}
                                                  for user, provider, n in [("owner", "tabby", 4), ("owner", "tamara", 5),
                                                                            ("other-owner", "tabby", 6)]])
        rows = (await read(client, "legacy.payment_settlements"))["rows"]
        assert [r["rows"] for r in rows] == [4] and "original" not in rows[0]
        await store.users.update_one({"id": "employee"}, {"$set": {"operational_balance_access.enabled": False}})
        assert (await client.get(f"{GRANTS}/legacy-read/legacy.suppliers_ledger", headers=actor("employee"))).status_code == 403


def test_old_menu_pages_are_unique_and_reads_have_no_write_surface():
    assert len({p["to"] for p in LEGACY_WEB_PAGES}) == len(LEGACY_WEB_PAGES)
    for p in LEGACY_WEB_PAGES:
        url = f"/api/web-page-access-v1/legacy-read/{p['id']}"
        grants = {p["id"]: "read"}
        with pytest.raises(HTTPException):
            enforce_legacy_web_pages(grants, url, "POST")
        if p.get("owner_only") or p["to"] in {"/integrations/salla/orders", "/integrations/salla/events"}:
            with pytest.raises(HTTPException):
                enforce_legacy_web_pages(grants, url, "GET")
        else:
            assert p.get("review_component") == "legacy_read" or p["to"] in {"/profile", "/integrations/salla/orders", "/integrations/salla/events"}
            enforce_legacy_web_pages(grants, url, "GET")

@pytest.mark.asyncio
async def test_report_scope_and_reconciliation_use_legacy_bank_namespace_and_main_account(store):
    async with await build_client(store) as client:
        await prepare(client, store, ["legacy.accounting.reconciliation", "legacy.alerts", "legacy.operations_dashboard"],
                      [access("bank", "account:b1"), access("bank", "native-other"), access("supplier", "s1")])
        await store.accounts.insert_many([{"id": "b1", "name": "Bank 1", "user_id": "owner", "account_type": "bank",
                                           "current_balance": 75, "currency": "SAR"},
                                          {"id": "other", "user_id": "owner", "account_type": "bank", "current_balance": 123}])
        await store.general_ledger.insert_many([{"entity_type": "bank", "entity_id": "b1", "user_id": "owner", "side": "debit",
                                                 "sub_account": sub, "status": "posted", "entry_type": "payment", "amount": val}
                                                for sub, val in [("main", 50), ("other", 250)]])
        row, = (await read(client, "legacy.accounting.reconciliation"))["rows"]
        assert (row["name"], row["balance"], row["ledger"], row["difference"]) == ("Bank 1", 75, 50, 25)
        await store.settlement_alerts.insert_many([{"user_id": "owner", "related_entity_type": "bank",
                                                    "related_entity_id": v, "title": v, "metadata": "secret"} for v in ("b1", "other")])
        alert, = (await read(client, "legacy.alerts"))["rows"]
        assert alert["title"] == "b1" and "metadata" not in alert
        await store.liabilities.insert_many([{"user_id": "owner", "kind": "supplier", "supplier_entity_id": s, "status": "partial",
                                              "expected_amount": val, "paid_amount": 2} for s, val in [("s1", 25), ("s2", 100)]])
        summary, = (await read(client, "legacy.operations_dashboard"))["rows"]
        assert (summary["expected"], summary["paid"]) == (25, 2)


@pytest.mark.asyncio
async def test_legacy_store_summary_and_daily_costs_are_read_only_and_tenant_bound(store):
    from auth import ensure_user_settings
    async with await build_client(store) as client:
        await prepare(client, store, ["legacy.reports", "legacy.daily_costs", "legacy.shipping.settings"], [])
        recorded = []
        async def dashboard(db, owner, **params):
            recorded.append((owner, params))
            return {"totals": {"total_orders": 9, "total_sales": 350, "net_profit": 80}, "bank_details": "hidden"}
        client._transport.app.state.legacy_dashboard_reader = dashboard
        response = await client.get(f"{GRANTS}/legacy-read/legacy.reports", params={"day": "2026-11-12"},
                                    headers=actor("employee"))
        assert response.status_code == 200, response.text
        row, = response.json()["rows"]
        assert (row["date"], row["net_profit"]) == ("2026-11-12", 80)
        assert "bank_details" not in row and recorded == [("owner", {"from_date": "2026-11-12", "to_date": "2026-11-12",
            "include_legacy_analyses": False, "allow_self_heal": False, "persist_settings": False})]
        await ensure_user_settings(store, "uninitialized", persist_defaults=False)
        assert await store.settings.count_documents({"user_id": "uninitialized"}) == 0
        await store.settings.insert_one({"user_id": "owner", "shipping_companies": [{"name": "Carrier", "secret_key": "secret",
                                                                                    "cost_per_order": 25}]})
        row, = (await read(client, "legacy.shipping.settings"))["rows"]
        assert row["name"] == "Carrier" and "secret" not in str(row)
        await store.daily_costs.insert_many([{"user_id": uid, "product_costs": cost, "date": "2026-11-12", "notes": "private"}
                                             for uid, cost in [("owner", 20), ("other-owner", 900)]])
        row, = (await read(client, "legacy.daily_costs"))["rows"]
        assert row["product_costs"] == 20 and "notes" not in row


def test_legacy_menu_inventory_covers_actual_sidebar_destinations():
    from pathlib import Path
    import re
    from web_page_access import PAGES
    sidebar = Path(__file__).resolve().parents[2].joinpath("frontend/src/components/Sidebar.jsx").read_text()
    urls = set(re.findall(r'to:\s*"([^"]+)",\s*label:', sidebar))
    covered = {url for p in PAGES for url in [p["to"], *p.get("aliases", [])] if isinstance(url, str)}
    assert urls - covered == set()
