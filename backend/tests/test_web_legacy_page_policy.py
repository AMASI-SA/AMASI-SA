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
        assert (await save(client, {"legacy.team": "write"}, 1)).status_code == 422
        assert (await save(client, {"legacy.team": "read"}, 1)).status_code == 200
        assert (await read(client, "legacy.team"))["columns"]


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
        assert not p.get("owner_only")
        assert p.get("review_component") == "legacy_read" or p["to"] == "/profile"
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


@pytest.mark.asyncio
async def test_form_history_and_audits_restrict_participants_and_writes(store):
    paths = ["legacy.employee_corrections", "legacy.salary_reversals", "legacy.expense_reversals",
             "legacy.audit.employee_orphans", "legacy.audit.ad_account_forensic", "legacy.audit.post_migration",
             "legacy.audit.ad_debt", "legacy.audit.ledger_health", "legacy.new_transaction",
             "legacy.bnpl_settlements.register", "legacy.settlement_engine", "legacy.audit.balance_drift"]
    async with await build_client(store) as client:
        await prepare(client, store, paths, [access("employee", "worker", scope="own"), access("bank", "account:b1"),
                                            access("ad_account", "ad"), access("provider", "tabby")])
        await store.users.update_one({"id": "employee"}, {"$set": {"operational_balance_access.operations": ["supplier.payment"]}})
        base = {"user_id": "owner", "currency": "SAR", "amount": 42, "status": "posted", "side": "credit",
                "posted_at": "2026-11-18", "posted_by": "employee", "notes": "secret"}
        def leg(no, kind, eid, op, sub, **values):
            return {**base, "entry_no": no, "entry_type": op, "entity_type": kind, "entity_id": eid, "sub_account": sub, **values}
        await store.general_ledger.insert_many([
            leg(1, "employee", "worker", "correction", "salary"),
            leg(2, "employee", "worker", "correction", "salary", posted_by="other"),
            leg(3, "employee", "worker", "correction", "custody"),
            leg(4, "bank", "b1", "reversal", "main", reversal_of_txn_group_id="g", metadata={"original_operation": "salary_payment"}),
            leg(5, "bank", "b1", "reversal", "main", reversal_of_txn_group_id="g2", metadata={"original_operation": "expense_record"}),
            leg(6, "employee", "worker", "opening_balance", "salary", metadata={"legacy_orphan": True}),
            leg(7, "employee", "worker", "opening_balance", "salary"),
            leg(8, "ad_account", "ad", "spend", "debt", metadata={"source": "ad_account_cron"}),
            leg(9, "ad_account", "other", "spend", "debt", metadata={"source": "ad_account_cron"}),
            leg(10, "bank", "b1", "payment", "main", metadata={"source": "account_transaction_double_write",
                                                               "account_transaction_id": "t1"}),
            leg(11, "payment_gateway", "tabby", "bnpl_settlement", "receivable"),
            leg(12, "payment_gateway", "tamara", "bnpl_settlement", "receivable"),
            leg(13, "employee", "worker", "correction", "salary", user_id="other-owner"),
        ])
        await store.account_transactions.insert_many([{"user_id": owner, "account_id": bank, "id": tid, "transaction_type": tid}
            for owner, bank, tid in [("owner", "b1", "t1"), ("owner", "x", "secret"), ("other", "b1", "private")]])
        await store.counterparties.insert_one({"user_id": "owner", "id": "ad", "kind": "ad_account", "name": "إعلان"})
        await store.ad_account_ledger.insert_one({"user_id": "owner", "counterparty_id": "ad", "debt_after": 12})
        await store.settlement_invoices.insert_many([{"user_id": owner, "provider_name": p, "invoice_no": n, "rules_snapshot": "secret"}
            for owner, p, n in [("owner", "tabby", 101), ("owner", "tamara", 102), ("other", "tabby", 103)]])
        for name, expected in zip(paths[:5], [1, 4, 5, 6, 8]):
            result = await read(client, name)
            assert [r["entry_no"] for r in result["rows"]] == [expected]
            assert "secret" not in str(result)
        row, = (await read(client, paths[5]))["rows"]
        assert row["count"] == 2 and row["entity_name"] == "worker"
        row, = (await read(client, paths[6]))["rows"]
        assert (row["old"], row["live"], row["difference"]) == (12, 42, 30)
        row, = (await read(client, paths[7]))["rows"]
        assert row["transaction_type"] == "t1" and row["status"] == "موجود في الدفتر"
        assert (await read(client, paths[8]))["rows"] == [{"operation": "دفع مبلغ لمورد"}]
        assert [r["entry_no"] for r in (await read(client, paths[9]))["rows"]] == [11]
        assert [r["invoice_no"] for r in (await read(client, paths[10]))["rows"]] == [101]
        assert (await read(client, paths[11]))["columns"]
        assert await store.general_ledger.count_documents({}) == 13
        for name in paths:
            assert (await client.post(f"{GRANTS}/legacy-read/{name}", headers=actor("employee"))).status_code in {403, 405}


@pytest.mark.asyncio
async def test_store_diagnostics_date_and_read_only_action_context(store):
    paths = ["legacy.ai.control_center", "legacy.diagnostics.cod_source", "legacy.diagnostics",
             "legacy.accounting.migration", "legacy.upload"]
    async with await build_client(store) as client:
        await prepare(client, store, paths, [])
        await store.unified_orders.insert_many([{"user_id": owner, "order_date": day, "order_status": "جديد",
            "customer": "secret", "payment_method": "cash_on_delivery"} for owner, day in [
                ("owner", "2026-11-18"), ("owner", "2026-11-18"), ("owner", "2026-11-17"), ("other", "2026-11-18")]])
        await store.analyses.insert_one({"user_id": "owner", "date": "2026-11-18", "orders_imported": 0,
                                        "report": {"summary": {"total_orders": 3}}})
        await store.import_jobs.insert_many([{"user_id": owner, "filename": name, "file_contents": "secret"}
                                            for owner, name in [("owner", "orders.xlsx"), ("other", "secret")]])
        async def day_view(name):
            res = await client.get(f"{GRANTS}/legacy-read/{name}", params={"day": "2026-11-18"}, headers=actor("employee"))
            assert res.status_code == 200, res.text
            assert "secret" not in res.text
            return res.json()["rows"]
        assert [r["count"] for r in await day_view(paths[0])] == [2]
        assert [r["count"] for r in await day_view(paths[1])] == [2]
        assert [r["count"] for r in await day_view(paths[2])] == [2, 3]
        assert (await read(client, paths[3]))["rows"][0]["status"] == "لم يُنفّذ الترحيل"
        assert [r["filename"] for r in (await read(client, paths[4]))["rows"]] == ["orders.xlsx"]
        assert await store.migration_cutoffs.count_documents({}) == 0


@pytest.mark.asyncio
async def test_salla_source_monitor_and_delivery_log_match_active_merchant(store):
    pages = ["legacy.integrations.salla.orders", "legacy.integrations.salla.events"]
    async with await build_client(store) as client:
        await prepare(client, store, pages, [])
        await store.salla_integrations.insert_one({"user_id": "owner", "store_id": "A"})
        await store.salla_webhook_inbox.insert_many([
            {"user_id": owner, "merchant_id": merchant, "event_name": label, "payload_ciphertext": "private"}
            for owner, merchant, label in [("owner", "A", "order.updated"), ("owner", "B", "other"),
                                           ("other", "A", "private")]])
        await store.unified_orders.insert_many([
            {"user_id": owner, "order_number": num, "raw_by_source": {source: {"customer": "private"}}}
            for owner, num, source in [("owner", "s1", "salla_direct"), ("owner", "x1", "excel"),
                                       ("other", "s2", "salla_direct")]])
        assert [r["order_number"] for r in (await read(client, pages[0]))["rows"]] == ["s1"]
        events = await read(client, pages[1])
        assert [r["event_name"] for r in events["rows"]] == ["order.updated"] and "private" not in str(events)
