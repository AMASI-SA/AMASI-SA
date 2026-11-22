"""Exercise the same workspace routes used by the balances app."""
from time import perf_counter

from backend.tests.test_operational_balance_integration import seed_ad_account
from backend.tests.test_operational_balance_workspace import BASE, entry, run
from operational_balance_store import STATES, initial


def claims(count):
    return {f"unrelated-{i}": {"id": f"unrelated-{i}", "party_type": "supplier",
            "party_id": "supplier", "kind": "supplier", "direction": "payable", "currency": "SAR",
            "confirmed": "2.00", "expected": "0.00", "business_date": "2026-11-10"}
            for i in range(count)}


def test_hospitality_expense_skips_unrelated_settlements_and_replays_once(monkeypatch):
    import operational_balance_service as service

    lookups = []
    original = service.outstanding

    def observed(state, identity, **kwargs):
        lookups.append(identity)
        return original(state, identity, **kwargs)

    monkeypatch.setattr(service, "outstanding", observed)

    async def scenario(db, client):
        await db[STATES].update_one({"owner_id": "owner"}, {"$set": {"engine.obligations": claims(850)}})
        body = entry(party_type="operating_expense", party_id="hospitality_food",
                     reason="payment", amount="80.00")
        start = perf_counter()
        first = await client.post(BASE + "/entries", json=body)
        elapsed = perf_counter() - start
        assert first.status_code == 200, first.text
        async def unexpected_write(*args, **kwargs):
            raise AssertionError("a committed receipt does not require a second mutation")

        monkeypatch.setattr(service, "mutate", unexpected_write)
        start = perf_counter()
        retry = await client.post(BASE + "/entries", json=body)
        print(f"Expense POST {elapsed:.3f}s, same request {perf_counter()-start:.3f}s, {len(lookups)} lookups")
        assert retry.json() == first.json()
        state = await db[STATES].find_one({"owner_id": "owner"})
        assert len(state["movements"]) == 1 and state["movements"][0]["amount"] == "80.00"
        parties = {(r["party_type"], r["party_id"]): r for r in service.report(state)["parties"]}
        assert parties[("bank", "bank")]["actual"] == "920.00"
        assert parties[("operating_expense", "hospitality_food")]["expense_paid"] == "80.00"
        assert not lookups, "an expense with no payable balance has no obligations to allocate"
    run(scenario)


def test_ad_account_read_matches_directory_without_projecting_other_parties(monkeypatch):
    import operational_balance_workspace as workspace

    sizes = []
    original = workspace.report

    def observed(state, **options):
        sizes.append(len(state["engine"]["obligations"]))
        return original(state, **options)
    monkeypatch.setattr(workspace, "report", observed)

    async def scenario(db, client):
        await seed_ad_account(db)
        obligations = claims(850)
        obligations["ad-spend"] = {"id": "ad-spend", "party_type": "ad_account", "party_id": "ad",
            "kind": "advertising", "direction": "payable", "currency": "SAR", "confirmed": "2475.51",
            "expected": "0", "business_date": "2026-11-10"}
        await db[STATES].update_one({"owner_id": "owner"}, {"$set": {"engine.obligations": obligations}})
        catalog = await client.get(BASE + "/context")
        assert catalog.status_code == 200, catalog.text
        account = next(r for r in catalog.json()["resources"] if r["kind"] == "ad_account")
        assert account["balance"]["side"] == "for_party" and account["balance"]["amount"] == "2475.51"
        start = perf_counter()
        result = await client.get(BASE + "/ledger/ad_account/ad")
        print(f"Ad ledger {perf_counter()-start:.3f}s, projection sizes {sizes}")
        assert result.status_code == 200, result.text
        assert result.json()["resource"] == account
        assert [r["id"] for r in result.json()["items"]] == ["ad-spend"]
        assert sizes == [851, 1]
    run(scenario)


def test_ad_read_keeps_both_currencies_and_falls_back_for_cross_account_allocations():
    from operational_party_projection_state import party_projection_state

    state = initial("owner")
    state["openings"]["usd"] = {"party_type": "ad_account", "party_id": "ad", "currency": "USD"}
    state["engine"]["obligations"] = {
        "sar": {"party_type": "ad_account", "party_id": "ad", "currency": "SAR"},
        "other": {"party_type": "ad_account", "party_id": "another", "currency": "SAR"},
    }
    scoped = party_projection_state(state, "ad_account", "ad")
    assert set(scoped["openings"]) == {"usd"} and set(scoped["engine"]["obligations"]) == {"sar"}
    state["movements"].append({"party_type": "external_person", "party_id": "other",
                               "allocations": [{"obligation_id": "sar"}]})
    assert party_projection_state(state, "ad_account", "ad") is state

def test_manual_payments_collections_and_transfers_post_to_accounts_without_invoice_lookup(monkeypatch):
    import operational_balance_service as service
    from operational_balance_store import read

    def no_lookup(*args, **kwargs):
        raise AssertionError("a manual account entry cannot search for a matching obligation")

    monkeypatch.setattr(service, "outstanding", no_lookup)

    async def scenario(db, client):
        for kind, identity, reason, side, amount in (
                ("supplier", "supplier", "payment", "for_us", "80"),
                ("employee", "employee", "payment", "for_us", "30"),
                ("external_person", "external", "receipt", "for_party", "20"),
                ("cash", "cash", "transfer", "for_us", "40")):
            body = entry(party_type=kind, party_id=identity, reason=reason, side=side, amount=amount)
            first = await client.post(BASE + "/entries", json=body)
            assert first.status_code == 200, first.text
            assert (await client.post(BASE + "/entries", json=body)).json() == first.json()
        saved = await read(db, "owner")
        assert len(saved["movements"]) == 4 and all(not m["allocations"] for m in saved["movements"])
        parties = {(p["party_type"], p["party_id"]): p for p in service.report(saved)["parties"]}
        assert parties["supplier", "supplier"]["outstanding_payable"] == "120.00"
        assert parties["employee", "employee"]["outstanding_payable"] == "170.00"
        assert parties["external_person", "external"]["outstanding_payable"] == "420.00"
        assert parties["bank", "bank"]["actual"] == "870.00"
        assert parties["cash", "cash"]["actual"] == "540.00"
    run(scenario)

def test_advanced_manual_payment_uses_the_same_account_balance_without_auto_allocation(monkeypatch):
    import operational_balance_service as service
    from operational_balance_store import digest, read

    def no_lookup(*args, **kwargs):
        raise AssertionError("a payment on account must not allocate invoices")

    monkeypatch.setattr(service, "outstanding", no_lookup)

    async def scenario(db, client):
        body = {"request_id": "advanced-on-account", "expected_session_scope": digest(["owner", "owner"]),
                "party_type": "supplier", "party_id": "supplier", "bank_id": "bank", "currency": "SAR",
                "direction": "outgoing", "kind": "payment", "amount": "80", "allocations": []}
        result = await client.post("/api/operational-balances/movements", json=body)
        assert result.status_code == 200, result.text
        state = await read(db, "owner")
        assert state["movements"][0]["settlement_scope"] == "account"
        supplier = next(p for p in service.report(state)["parties"] if p["party_type"] == "supplier")
        assert supplier["outstanding_payable"] == "120.00"
    run(scenario)


def test_names_directory_and_selected_shipping_balance_do_not_build_other_accounts(monkeypatch):
    import operational_balance_workspace as workspace
    import operational_financial_position_state as positions
    from backend.tests.test_operational_balance_workspace import grant, resource

    reads, projected = [], []
    original_read, original_report = positions.read_financial_position_state, workspace.report

    async def counted_read(db, owner):
        reads.append(owner)
        return await original_read(db, owner)

    def counted_report(state, **options):
        projected.append({r["party_id"] for r in state["engine"]["obligations"].values()})
        return original_report(state, **options)

    monkeypatch.setattr(positions, "read_financial_position_state", counted_read)
    monkeypatch.setattr(workspace, "report", counted_report)

    async def scenario(db, client):
        await db.mz2_shipping_setup_v2.insert_one({"user_id": "owner", "couriers": [
            {"courier_key": key, "name": name, "confirmed_by": "owner", "confirmed_at": "2026-11-01"}
            for key, name in (("imile", "iMile"), ("aramex", "أرامكس"), ("smsa", "سمسا"))]})
        obligations = claims(90)
        for key, amount in (("imile", "26220.25"), ("smsa", "6335.75")):
            obligations[key] = {**obligations["unrelated-0"], "party_type": "courier", "party_id": key,
                "kind": "shipping", "id": key, "confirmed": amount}
        await db[STATES].update_one({"owner_id": "owner"}, {"$set": {"engine.obligations": obligations}})
        directory = (await client.get(BASE + "/context?directory_only=true&home_preview=true")).json()
        assert directory["directory_only"] and "recent_movements" not in directory
        assert all(r["balance"] is None for r in directory["resources"])
        assert {r["id"] for r in directory["resources"] if r["kind"] == "courier"} == {"smsa", "aramex", "imile"}
        assert not reads and not projected
        detail = await client.get(BASE + "/ledger/courier/imile")
        assert detail.status_code == 200, detail.text
        assert detail.json()["resource"]["balance"]["amount"] == "26220.25"
        assert reads == ["owner"] and projected == [{"imile"}]
        await grant(db, operations=[], resources=[resource("courier", "smsa", use=False, balance=True, movements="none")])
        headers = {"x-test-user": "staff"}
        selected = await client.get(BASE + "/resource/courier/smsa", headers=headers)
        assert selected.json()["balance"]["amount"] == "6335.75" and "items" not in selected.json()
        assert (await client.get(BASE + "/ledger/courier/smsa", headers=headers)).status_code == 404
        assert (await client.get(BASE + "/resource/courier/imile", headers=headers)).status_code == 404
    run(scenario)


def test_register_only_projects_requested_rows_and_invalidates_on_change(monkeypatch):
    import operational_balance_workspace as ws
    from backend.tests.test_operational_balance_workspace import grant, resource

    async def scenario(db, client):
        for i in range(9):
            body = entry(party_type="supplier", party_id="supplier", reason="payment", amount=str(i + 1))
            assert (await client.post(BASE + "/entries", json=body)).status_code == 200
        legacy = [(await client.get(BASE + f"/movements?page={n}")).json() for n in (1, 2)]
        builds, rendered = [], []
        original_index, original_row = ws._register_index, ws._row

        def indexed(*args, **kwargs):
            builds.append(True)
            return original_index(*args, **kwargs)

        def render(*args):
            rendered.append(True)
            return original_row(*args)

        def unexpected(*args, **kwargs):
            raise AssertionError("the register must not calculate account balances")

        monkeypatch.setattr(ws, "_register_index", indexed)
        monkeypatch.setattr(ws, "_row", render)
        monkeypatch.setattr(ws, "report", unexpected)
        monkeypatch.setattr(ws, "with_running_balances", unexpected)
        for n in (1, 2, 1):
            current = (await client.get(BASE + f"/movements?page={n}&page_only=true")).json()
            old = legacy[n - 1]
            assert current["total"] == old["total"] and current["page_only"]
            assert current["items"] == [{**r, "balance_after": None} for r in old["items"]]
            assert len(builds) == 1 and len(current["items"]) == 5
        assert len(rendered) == 15
        await db[STATES].update_one({"owner_id": "owner"}, {"$inc": {"revision": 1}})
        await client.get(BASE + "/movements?page=2&page_only=true")
        assert len(builds) == 2
        await grant(db, operations=[], resources=[resource("supplier", "supplier", movements="own")])
        staff = (await client.get(BASE + "/movements?page_only=true", headers={"x-test-user": "staff"})).json()
        assert staff["items"] == [] and len(builds) == 3
        await grant(db, operations=[], resources=[resource("supplier", "supplier", movements="all")], version=2)
        staff = (await client.get(BASE + "/movements?page_only=true", headers={"x-test-user": "staff"})).json()
        assert staff["total"] == 9 and len(builds) == 4
    run(scenario)


def test_smsa_opens_final_balance_and_one_page_then_reuses_balance_until_revision_changes(monkeypatch):
    import operational_balance_workspace as ws
    import operational_financial_position_state as positions

    async def scenario(db, client):
        await db.mz2_shipping_setup_v2.insert_one({"user_id": "owner", "couriers": [
            {"name": "سمسا", "courier_key": "smsa", "confirmed_by": "owner", "confirmed_at": "2026-11-01"}]})
        source = claims(60)
        for i in range(12):
            k = f"smsa-{i:02}"
            source[k] = {**source["unrelated-0"], "id": k, "party_type": "courier", "party_id": "smsa",
                         "kind": "shipping", "confirmed": "5.50", "business_date": f"2026-11-{i + 1:02}"}
        source["forecast"] = {**source["smsa-00"], "id": "forecast", "kind": "cod", "direction": "receivable",
                              "confirmed": "0", "expected": "230", "business_date": "2026-11-14"}
        await db[STATES].update_one({"owner_id": "owner"}, {"$set": {"engine.obligations": source}})
        url = BASE + "/ledger/courier/smsa?page_only=true"
        calls = {"read": 0, "balance": 0, "detail": 0}
        read_snapshot, report, render = positions.read_financial_position_state, ws.report, ws._row
        async def observed_read(*args):
            calls["read"] += 1
            return await read_snapshot(*args)
        def observed_report(state, **kwargs):
            assert all(r["party_id"] == "smsa" for r in state["engine"]["obligations"].values())
            calls["balance"] += 1
            return report(state, **kwargs)
        def observed_row(*args):
            calls["detail"] += 1
            return render(*args)
        monkeypatch.setattr(positions, "read_financial_position_state", observed_read)
        monkeypatch.setattr(ws, "report", observed_report)
        monkeypatch.setattr(ws, "_row", observed_row)
        for page, ids in ((1, range(11, 6, -1)), (2, range(6, 1, -1)), (3, range(1, -1, -1))):
            response = await client.get(url + f"&page={page}")
            assert response.status_code == 200, response.text
            data = response.json()
            assert data["page_only"] and data["resource"]["balance"]["amount"] == "66.00"
            assert "expected_collections" not in data
            assert data["total"] == 12 and data["total_pages"] == 3
            assert [r["id"] for r in data["items"]] == [f"smsa-{i:02}" for i in ids]
            assert calls == {"read": 1, "balance": 1, "detail": min(page * 5, 12)}
        focused = (await client.get(url + "&focus_id=smsa-01")).json()
        assert focused["page"] == 3 and calls["balance"] == 1
        forecast = (await client.get(BASE + "/forecast/courier/smsa")).json()
        assert forecast["amount"] == "230.00" and forecast["total"] == 1 and calls["balance"] == 1
        await db[STATES].update_one({"owner_id": "owner"},
            {"$inc": {"revision": 1}, "$set": {"engine.obligations.smsa-00.confirmed": "15.50"}})
        updated = (await client.get(url + "&page=2")).json()
        assert updated["resource"]["balance"]["amount"] == "76.00" and calls["balance"] == 2
    run(scenario)


def test_account_directory_batches_only_opened_bank_cash_or_employee_custody(monkeypatch):
    import operational_balance_workspace as ws
    from backend.tests.test_operational_balance_workspace import grant, resource
    import operational_financial_position_state as positions

    async def scenario(db, client):
        assert (await client.post(BASE + "/entries", json=entry(party_type="cash", party_id="cash",
                reason="transfer", amount="35"))).status_code == 200
        def no_report(*args, **kwargs):
            raise AssertionError("the category must not build a snapshot report or calculate salaries")
        monkeypatch.setattr(ws, "report", no_report)
        monkeypatch.setattr(positions, "read_financial_position_state", no_report)
        for kind, amount in (("bank", "965.00"), ("cash", "535.00"), ("employee_custody", "150.00")):
            response = await client.get(BASE + "/accounts/" + kind)
            assert response.status_code == 200, response.text
            rows = response.json()["items"]
            assert len(rows) == 1 and rows[0]["kind"] == kind and rows[0]["balance"]["amount"] == amount
            assert "movements" not in rows[0] and "items" not in rows[0]
        await grant(db, operations=[], resources=[resource("bank", "bank", balance=False)])
        headers = {"x-test-user": "staff"}
        hidden = (await client.get(BASE + "/accounts/bank", headers=headers)).json()["items"]
        assert len(hidden) == 1 and hidden[0]["balance"] is None
        assert (await client.get(BASE + "/accounts/employee_custody", headers=headers)).json()["items"] == []
    run(scenario)


def test_ad_and_bank_groups_match_web_cards_and_individual_resources():
    from operational_ad_balance_sar import COLLECTION
    from backend.tests.test_operational_balance_workspace import grant, resource
    async def scenario(db, client):
        await seed_ad_account(db)
        await db.mezan_integration_accounts_v2.update_one({"mezan_integration_account_id": "ia"}, {"$set": {"currency": "USD"}})
        opening = {"id": "ad-opening", "name": "Ad", "party_type": "ad_account", "party_id": "ad",
                   "amount": "100.00", "currency": "USD", "direction": "for_party",
                   "created_at": "2026-11-01T00:00:00+00:00", "actor_id": "owner"}
        await db[STATES].update_one({"owner_id": "owner"}, {"$set": {"openings.ad": opening,
            "engine.ad_balance_sar_basis.ad": {"source": COLLECTION, "currency": "USD", "exchange_rate_to_sar": "3.75",
                "bank_commission_pct": "2", "apply_bank_commission": True, "bank_commission_vat_pct": "15"},
            "openings.bank:bank.direction": "for_party"}})
        web = (await client.get(BASE + "/context?ad_bank_fx=true&provider_positions=true")).json()["resources"]
        for kind, identity, group, field in (("ad_account", "ad", "/parties?kind=ad_account&ad_bank_fx=true", "ad_amounts"),
                                             ("bank", "bank", "/accounts/bank", "balance")):
            expected = next(r for r in web if (r["kind"], r["id"]) == (kind, identity))[field]
            if kind == "ad_account":
                position = await client.get("/api/operational-balances/financial-position?view=advertising")
                assert position.status_code == 200
                grids = {t["id"]: t["rows"] for t in position.json()["currencies"]["SAR"]["tables"]}
                assert grids["ads"][0]["payable"] == expected["payable"] == "383.63"
                assert grids["ads_currency_conversion"][0]["amount_usd"] == expected["native"]["payable"] == "100.00"
                assert expected["currency"] == "SAR"
            else:
                assert (expected["side"], expected["amount"]) == ("for_party", "1000.00")
            headers = {"x-test-mobile": "yes"}
            rows = (await client.get(BASE + group, headers=headers)).json()["items"]
            item = next(r for r in rows if r["id"] == identity)
            assert item[field] == expected
            if kind == "ad_account":
                assert "ads.fund" in item["operations"]
            for route in ("resource", "ledger"):
                data = (await client.get(f"{BASE}/{route}/{kind}/{identity}?page_only=true&ad_bank_fx=true", headers=headers)).json()
                assert data.get("resource", data)[field] == expected
        await grant(db, operations=["ads.fund"], resources=[resource("ad_account","ad",balance=False)])
        limited = (await client.get(BASE + "/parties?kind=ad_account&ad_bank_fx=true", headers={"x-test-user":"staff"})).json()["items"]
        assert len(limited) == 1 and "ad_amounts" not in limited[0] and "ad_wallet_balance" not in limited[0]
    run(scenario)


def test_workspace_positions_match_operational_position_and_paginated_courier_history(monkeypatch):
    from decimal import Decimal
    import operational_balance_workspace as ws
    from backend.tests.test_operational_balance_workspace import grant, resource

    async def scenario(db, client):
        await db.mz2_shipping_setup_v2.insert_one({"user_id": "owner", "couriers": [
            {"name": "سمسا", "courier_key": "smsa", "confirmed_by": "owner", "confirmed_at": "2026-11-01"}]})
        def claim(identity, amount, direction, day):
            return {"id": identity, "party_type": "courier", "party_id": "smsa", "kind": "shipping" if direction == "payable" else "cod",
                    "direction": direction, "currency": "SAR", "confirmed": amount, "expected": "0.00", "business_date": day}
        rows = [claim("fee-old", "6525.50", "payable", "2026-11-19"),
                claim("fee-order", "17.25", "payable", "2026-11-20"),
                claim("confirmed-cod", "6642.75", "receivable", "2026-11-21")]
        await db[STATES].update_one({"owner_id": "owner"}, {"$set": {"engine.obligations": {r["id"]: r for r in rows},
                                    "engine.as_of": "2026-11-21T02:00:00+00:00"}})
        summary = await client.get("/api/operational-balances/reports?position_amounts=true")
        assert summary.status_code == 200, summary.text
        report_amounts = {(r["party_type"], r["party_id"]): r["position_balance"] for r in summary.json()["parties"]}
        for kind, identity, view, table, field, expected in [
            ("courier","smsa","couriers","couriers",None,"100.00"), ("supplier","supplier","suppliers","suppliers",None,"-200.00"),
            ("employee","employee","employees","employees",None,"-200.00"), ("external_person","external","other","external",None,"-400.00"),
            ("bank","bank","accounts","banks_network","available","1000.00"), ("cash","cash","accounts","cash","available","500.00"),
            ("employee_custody","employee","employees","employee_custody","available","150.00")]:
            web = await client.get(f"/api/operational-balances/financial-position?view={view}")
            assert web.status_code == 200, web.text
            grid = next(t for t in web.json()["currencies"]["SAR"]["tables"] if t["id"] == table)
            canonical = next(r for r in grid["rows"] if r["id"] == f"{kind}:{identity}:SAR")
            net = Decimal(canonical[field]) if field else Decimal(canonical["receivable"]) - Decimal(canonical["payable"])
            app = await client.get(f"{BASE}/resource/{kind}/{identity}")
            assert app.status_code == 200, app.text
            assert net == Decimal(expected)
            assert app.json()["position_balance"] == report_amounts[(kind, identity)] == expected
        history, calls = ws._courier_history, []
        def counted(state, company, policy):
            calls.append(company["id"])
            return history(state, company, policy)
        monkeypatch.setattr(ws, "_courier_history", counted)
        for route in ("movements", "ledger/courier/smsa"):
            url = f"{BASE}/{route}?page_only=true&courier_history=true&page_size=1"
            for page, expected_id, balance in [(1, "confirmed-cod", "-100.00"), (2, "fee-order", "6542.75"), (1, "confirmed-cod", "-100.00")]:
                result = (await client.get(url + f"&page={page}")).json()
                assert [(r["id"], r["balance_after"]) for r in result["items"]] == [(expected_id, balance)]
        assert calls == ["smsa"]
        await grant(db, operations=[], resources=[resource("courier", "smsa", movements="all", balance=False)])
        restricted = (await client.get(f"{BASE}/movements?page_only=true&courier_history=true",
                                      headers={"x-test-user": "staff"})).json()
        assert restricted["items"] and all(r["balance_after"] is None for r in restricted["items"])
        assert calls == ["smsa"]
    run(scenario)
