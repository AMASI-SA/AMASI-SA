"""Approved screen HTTP contracts. No external network or dedicated tenant data."""
from tests.test_operational_balance_workspace import BASE, entry, grant, resource, run


def test_eight_rows_and_arabic_cross_page_search_preserve_period_balance_and_legacy_default():
    async def scenario(db, client):
        for day in range(2, 13):
            response = await client.post(BASE + "/entries", json=entry(
                amount="10", business_date=f"2026-11-{day:02}", reference=f"MV-{day:02}"))
            assert response.status_code == 200, response.text
        period = {"date_from": "2026-11-03", "date_to": "2026-11-12", "page_size": 8}
        path = BASE + "/ledger/bank/bank"
        first = (await client.get(path, params=period)).json()
        assert len(first["items"]) == 8
        assert first["items"][0]["reference"] == "MV-12"
        assert first["items"][0]["from_name"] == "الإنماء"
        assert first["items"][0]["to_name"] == "شركة آفاق"
        assert first["period"]["opening_balance"] == "990.00"
        assert first["period"]["closing_balance"] == "890.00"
        assert first["period"]["totals"] == {"for_us": "0.00", "for_party": "100.00"}
        assert first["total"] == 10 and first["total_pages"] == 2
        second = (await client.get(path, params={**period, "page": 2})).json()
        assert [row["reference"] for row in second["items"]] == ["MV-04", "MV-03"]
        found = (await client.get(path, params={**period, "q": "mv-٠٣"})).json()
        assert found["total"] == 1 and found["items"][0]["balance_after"] == "980.00"
        assert found["period"] == first["period"]
        report = (await client.get(BASE + "/statements/bank/bank", params={
            "date_from": period["date_from"], "date_to": period["date_to"], "purpose": "print"})).json()
        assert len(report["items"]) == 10 and report["closing_balance"] == "890.00"
        global_search = (await client.get(BASE + "/movements", params={**period, "q": "انماء", "direction":"out"})).json()
        assert global_search["total"] == 10 and len(global_search["items"]) == 8
        by_ref = (await client.get(BASE + "/movements", params={**period, "q":"mv-۰۳"})).json()
        assert by_ref["total"] == 1 and by_ref["items"][0]["reference"] == "MV-03"
        assert (await client.get(path)).json()["page_size"] == 5
        assert (await client.get(path, params={"page_size":99})).status_code == 422
    run(scenario)


def test_search_has_no_hidden_counterpart_and_does_not_elevate_report_permissions():
    async def scenario(db, client):
        await client.post(BASE + "/entries", json=entry(reference="sensitive-8201"))
        await grant(db, operations=[], resources=[resource("bank", "bank", use=False, movements="all", balance=True)])
        path = BASE + "/ledger/bank/bank"
        permitted = {"date_from": "2026-11-01", "date_to": "2026-11-19", "page_size":8}
        response = await client.get(path, params=permitted, headers={"x-test-user":"staff"})
        assert response.status_code == 200
        result = response.json()
        assert "period" not in result
        assert all("to_name" not in row and "reference" not in row for row in result["items"])
        for q in ("آفاق", "sensitive"):
            assert (await client.get(BASE + "/movements", headers={"x-test-user":"staff"}, params={**permitted,"q":q})).json()["total"] == 0
        assert (await client.get(path, params={"date_from":"2026-11-12"})).status_code == 422
    run(scenario)


def test_driver_evidence_is_verified_once_for_the_multi_account_log(monkeypatch):
    import operational_driver_review_report as reviews
    from operational_balance_workspace import _index_ledger_sources, ledger_items

    calls = []
    monkeypatch.setattr(reviews, "validated_review_facts", lambda state: (calls.append(1) or [], []))
    drivers = [{"id": f"driver-{n}", "kind":"store_driver", "name":f"موصل {n}", "currency":"SAR",
        "access":{"movements":"all", "balance":True}, "balance":{"amount":"0","side":"balanced"}} for n in range(15)]
    state = {"movements":[], "openings":{}}
    computed = {"obligations":[]}
    sources = _index_ledger_sources(state, computed, drivers)
    for driver in drivers:
        assert ledger_items(state, computed, driver, None, sources=sources[(driver["kind"],driver["id"],"SAR")]) == []
    assert calls == [1]


def test_resource_reads_resolve_only_selected_catalog_without_altering_snapshot_or_reference_rights(monkeypatch):
    import operational_balance_sources as sources
    from operational_balance_workspace import RESOURCE_KINDS

    calls = []
    original = sources.entities
    async def observed(db, owner, kind, **kwargs):
        calls.append(kind)
        return await original(db, owner, kind, **kwargs)
    monkeypatch.setattr(sources, "entities", observed)
    async def scenario(db, client):
        assert (await client.post(BASE + "/entries",json=entry(amount="37", reference="real-ref"))).status_code == 200
        calls.clear()
        catalog = (await client.get(BASE + "/context")).json()["resources"]
        assert calls == list(RESOURCE_KINDS)
        for path, expected, query in [
            ("ledger/bank/bank","bank",{}),
            ("ledger/external_person/external","external_person",{}),
            ("statements/bank/bank","bank",{"date_from":"2026-11-03","date_to":"2026-11-19"}),
            ("history/employee/employee","employee",{}),
            ("movements","bank",{"kind":"bank","id":"bank"})]:
            calls.clear()
            result = await client.get(BASE + "/" + path,params=query)
            assert result.status_code == 200, result.text
            assert calls == [expected]
            if path.startswith("ledger/"):
                item = next(r for r in catalog if r["kind"] == expected)
                assert result.json()["resource"] == item
                assert result.json()["items"][0]["reference"] == "real-ref"
                assert result.json()["items"][0]["from_name"] == "الإنماء"
                assert result.json()["items"][0]["to_name"] == "شركة آفاق"
        await grant(db,operations=[],resources=[resource("bank","bank",use=False,movements="all",balance=True)])
        calls.clear()
        denied = await client.get(BASE + "/ledger/external_person/external",headers={"x-test-user":"staff"})
        assert denied.status_code == 404 and not calls
        permitted = await client.get(BASE + "/ledger/bank/bank",headers={"x-test-user":"staff"})
        assert calls == ["bank"] and permitted.status_code == 200
        assert permitted.json()["resource"]["balance"]["amount"] == "963.00"
        assert all("reference" not in row and "to_name" not in row for row in permitted.json()["items"])
    run(scenario)


def test_flow_filters_follow_the_actual_fee_or_refund_effect():
    from operational_workspace_ledger_filters import movement_type
    assert movement_type({"kind":"settlement","direction":"incoming"}) == "in"
    assert movement_type({"kind":"settlement_fee","direction":"incoming"}) == "out"
    assert movement_type({"kind":"refund","status":"refunded"}) == "out"
    assert movement_type({"kind":"refund","status":"provider_matched"}) is None
    assert movement_type({"kind":"correction","direction":"outgoing"}) is None
