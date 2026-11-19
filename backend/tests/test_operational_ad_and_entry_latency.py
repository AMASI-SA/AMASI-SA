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
