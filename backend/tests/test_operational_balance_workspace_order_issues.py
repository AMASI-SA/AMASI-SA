import asyncio

from tests.test_operational_balance_workspace import BASE, STATES, grant, resource, run
from operational_workspace_order_issues import indexed, navigation_counts, issue_badge, page_of


def test_saved_counters_do_not_scan_order_lists_and_unknown_stays_unknown():
    class NeverRead:
        def __iter__(self):
            raise AssertionError("directory traversed all order evidence")
    result = {"details": {"provider_balances": {"SAR": [{"provider": "tabby", "pending_orders": 17,
                "pending_order_details": NeverRead()}]}}}
    counts = navigation_counts(result)
    assert issue_badge("provider", "tabby", {"movements": "all"}, True, True, counts, {}) == {"order_issues_count": 17}
    assert issue_badge("courier", "aramex", {"movements": "all"}, True, True, counts, {}) == {"order_issues_count": None}
    assert issue_badge("provider", "tabby", {"movements": "own"}, True, True, counts, {}) == {}


def test_distinct_orders_across_resources_retain_reasons_and_missing_evidence():
    def packet(kind, name, order, missing=0):
        return {"kind": kind, "id": name, "name": name, "unidentified": missing, "pending_order_details": [
            {"order_id": "1", "order_number": "9012", "known": None,
             "reasons": [{"code": order, "message": order}]}]}
    result, counts = indexed({"parties": [packet("provider", "تابي", "عمولة", 2), packet("courier", "أرامكس", "بوليصة")],
                               "started_at": None, "as_of": None})
    assert counts == {("provider", "تابي"): 3, ("courier", "أرامكس"): 1}
    assert page_of(result, "٩٠١٢", 1)["total"] == 1
    assert page_of(result, "عمولة", 1)["items"][0]["groups"][1]["name"] == "أرامكس"
    assert result["unidentified"] == [{"name": "تابي", "count": 2}]


def test_scoped_order_pages_canonical_references_no_per_row_reads_and_revoked_permission(monkeypatch):
    import operational_workspace_order_issues as subject
    reads, labels = [], []
    original, original_labels = subject.review_groups, subject.attach_order_numbers
    def count_projection(*args):
        reads.append(True)
        return original(*args)
    async def count_labels(*args):
        labels.append(True)
        return await original_labels(*args)
    monkeypatch.setattr(subject, "review_groups", count_projection)
    monkeypatch.setattr(subject, "attach_order_numbers", count_labels)

    async def scenario(db, client):
        for identity in ("driver-1", "driver-2"):
            await db.store_drivers.insert_one({"user_id": "owner", "id": identity, "name": identity, "status": "active"})
        issues = [{"code": "driver_fee_evidence_conflict", "order_id": str(i), "currency": "SAR",
                   "party_id": "driver-1", "party_type": "store_driver", "order_number": "untrusted"} for i in range(101, 111)]
        issues += [dict(issues[0], code="operational_delivery_timestamp_missing"),
                   dict(issues[0], order_id="8800", party_id="driver-2"),
                   {"code": "operational_delivery_courier_identity_ambiguous", "order_id": "9999", "currency": "SAR"}]
        await db[STATES].update_one({"_id":"owner"}, {"$set": {"engine.issues":issues,"engine.as_of":"2026-11-19T10:00:00Z"}})
        for i in range(101,111):
            await db.unified_orders.insert_one({"user_id":"owner","raw_by_source":{"salla_direct":{"id":str(i),"reference_id":str(8000+i)}}})
        await grant(db, operations=[], resources=[resource("store_driver","driver-1",movements="all"),
                                                 resource("store_driver","driver-2",movements="own")])
        headers={"X-Test-User":"staff"}
        ctx=(await client.get(BASE+"/context",headers=headers)).json()
        assert reads == labels == []
        one=next(r for r in ctx["resources"] if r["id"]=="driver-1")
        two=next(r for r in ctx["resources"] if r["id"]=="driver-2")
        assert one["order_issues_count"] is None and "order_issues_count" not in two
        response, parallel = await asyncio.gather(client.get(BASE+"/order-issues",headers=headers),
                                                  client.get(BASE+"/order-issues",headers=headers))
        assert response.json() == parallel.json() and len(reads) == 1
        assert response.status_code == 200,response.text
        first=response.json()
        assert (first["total"], len(first["items"]), first["total_pages"]) == (10,8,2)
        assert "8800" not in response.text and "9999" not in response.text and "untrusted" not in response.text
        second=(await client.get(BASE+"/order-issues?page=2",headers=headers)).json()
        assert len(second["items"])==2 and not {r["order_id"] for r in first["items"]} & {r["order_id"] for r in second["items"]}
        found=(await client.get(BASE+"/order-issues",params={"q":"٨١٠١"},headers=headers)).json()
        assert found["total"]==1 and len(found["items"][0]["groups"][0]["reasons"])==2
        assert len(reads)==len(labels)==1
        scoped=await client.get(BASE+"/order-issues",params={"kind":"store_driver","id":"driver-2"},headers=headers)
        assert scoped.status_code==404 and len(reads)==1
        ctx=(await client.get(BASE+"/context",headers=headers)).json()
        assert next(r for r in ctx["resources"] if r["id"]=="driver-1")["order_issues_count"]==10
        await grant(db,operations=[],resources=[resource("store_driver","driver-1",movements="own")],version=2)
        assert (await client.get(BASE+"/order-issues",headers=headers)).json()["total"]==0
        assert (await client.get(BASE+"/order-issues",params={"kind":"store_driver","id":"driver-1"},headers=headers)).status_code==404
    run(scenario)


def test_bank_receipt_status_reviews_count_distinct_orders_without_credit_or_cross_bank_disclosure():
    async def scenario(db, client):
        await db.mz2_financial_accounts.insert_one({
            "id": "second-bank", "user_id": "owner", "name": "بنك آخر", "account_type": "bank",
            "currency": "SAR", "status": "active"})
        await grant(db, operations=[], resources=[
            resource("bank", "bank", balance=True, movements="all"),
            resource("bank", "second-bank", balance=True, movements="own")])
        headers = {"X-Test-User": "staff"}
        ledger_path = BASE + "/ledger/bank/bank"
        before = (await client.get(ledger_path, headers=headers)).json()
        blocked = {"code": "operational_order_bank_status_ineligible", "party_type": "bank", "party_id": "bank",
                   "source_id": "100", "order_status": "under_review", "order_number": "untrusted"}
        await db[STATES].update_one({"_id": "owner"}, {"$set": {
            "engine.as_of": "2026-11-21T10:00:00Z", "engine.issues": [
                blocked, blocked, dict(blocked, source_id="secret", party_id="second-bank"),
                dict(blocked, source_id="unmapped", party_id=None),
                dict(blocked, source_id="unrelated", code="other_issue")]}})
        await db.unified_orders.insert_one({"user_id": "owner", "raw_by_source": {
            "salla_direct": {"id": "100", "reference_id": "90100"}}})
        ledger = (await client.get(ledger_path, headers=headers)).json()
        assert ledger["resource"]["order_issues_count"] == 1
        assert ledger["items"] == before["items"] and ledger["resource"]["balance"] == before["resource"]["balance"]
        for params in ({}, {"kind": "bank", "id": "bank", "q": "بانتظار المراجعة"}):
            res = await client.get(BASE + "/order-issues", params=params, headers=headers)
            assert res.status_code == 200, res.text
            assert res.json()["total"] == 1
            row = res.json()["items"][0]
            assert (row["order_id"], row["order_number"]) == ("100", "90100")
            assert row["groups"][0]["reasons"] == [{
                "code": blocked["code"], "message": "الإيصال غير معتمد بسبب حالة الطلب · بانتظار المراجعة"}]
            assert all(word not in res.text for word in ("secret", "unmapped", "unrelated", "untrusted"))
        denied = await client.get(BASE + "/order-issues", headers=headers,
                                  params={"kind": "bank", "id": "second-bank"})
        assert denied.status_code == 404
        owner_page = (await client.get(BASE + "/order-issues", params={"q": "بنك التحويل غير محدد"})).json()
        assert owner_page["total"] == 1
        assert owner_page["items"][0]["order_id"] == "unmapped"
        assert owner_page["items"][0]["groups"][0]["kind"] == "unassigned_bank"
        owner_bank = (await client.get(BASE + "/order-issues", params={"kind": "bank", "id": "bank"})).json()
        assert [row["order_id"] for row in owner_bank["items"]] == ["100"]
    run(scenario)


def test_provider_only_pending_source_is_listed_with_its_actual_reason_and_owner_only_unassigned():
    async def scenario(db, client):
        issues = [{"scope": "provider_payment", "provider": "salla", "currency": "SAR",
                   "code": "provider_payment_amount_conflict", "source_id": "100", "order_number": "9001"},
                  {"code": "operational_delivery_order_waybill_unproven", "order_id": "888", "currency": "SAR"}]
        await db[STATES].update_one({"_id":"owner"},{"$set":{"engine.issues":issues,"engine.as_of":"2026-11-19T12:00:00Z"}})
        await db.unified_orders.insert_one({"user_id":"owner","raw_by_source":{"salla_direct":{"id":"100","reference_id":"98001"}}})
        await grant(db,operations=[],resources=[resource("provider","salla",movements="all")])
        h={"X-Test-User":"staff"}
        ctx=(await client.get(BASE+"/context",headers=h)).json()
        assert next(r for r in ctx["resources"] if r["id"]=="salla")["order_issues_count"]==1
        found=(await client.get(BASE+"/order-issues",headers=h)).json()
        assert found["total"]==1
        only=found["items"][0]
        assert only["order_number"]=="98001" and only["groups"][0]["reasons"][0]["code"]=="provider_payment_amount_conflict"
        owner=(await client.get(BASE+"/order-issues")).json()
        assert next(row for row in owner["items"] if row["order_id"]=="888")["groups"][0]["kind"]=="unassigned"
    run(scenario)


def test_confirmed_provider_amount_and_driver_delivery_require_the_bound_resource():
    from operational_workspace_order_issues import known_evidence
    from operational_delivery_register import PREFIX
    state={"owner_id":"owner","engine":{"obligations":{"provider:100":{"party_id":"tabby","party_type":"provider",
        "direction":"receivable","confirmed":"320.00","currency":"SAR"}},"delivery_register":{PREFIX+"100":{
        "party_type":"store_driver","party_id":"driver-a","order_id":"100","current_status":"returned"}}}}
    assert "320.00 SAR" in known_evidence(state,"provider","tabby","100")
    assert known_evidence(state,"provider","tamara","100") is None
    assert "سبق أن ثبت" in known_evidence(state,"store_driver","driver-a","100")
    assert known_evidence(state,"courier","driver-a","100") is None
