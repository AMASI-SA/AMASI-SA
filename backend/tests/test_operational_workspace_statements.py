"""Scoped statements and automatic histories over an isolated canonical ledger."""
from copy import deepcopy

import pytest

from operational_balance_access import normalized_policy
from operational_balance_store import STATES, digest, initial
from operational_test_delivery import native_delivery
from tests.test_operational_balance_workspace import BASE, entry, grant, resource, run


PERIOD = {"date_from": "2026-11-01", "date_to": "2026-11-30"}
STAFF = {"x-test-user": "staff"}


def report_grant(kind="employee", identity="employee", **changes):
    return {**resource(kind, identity, use=False, movements="all", balance=True),
            "report": True, "print": False, **changes}


def test_old_grants_keep_access_without_acquiring_report_or_print():
    old = {"enabled": True, "owner_id": "owner", "version": 1, "operations": [],
           "resources": [resource("employee", "employee", movements="all", balance=True)]}
    row = normalized_policy(old, owner="owner")["resources"][0]
    assert row["movements"] == "all" and row["balance"] is True
    assert row["report"] is False and row["print"] is False


def test_employee_period_statement_includes_prior_balance_and_all_period_rows():
    async def scenario(db, client):
        for day in range(2, 9):
            response = await client.post(BASE + "/entries", json=entry(
                party_type="employee", party_id="employee", side="for_us", reason="payment",
                amount="10.00", business_date=f"2026-11-{day:02d}"))
            assert response.status_code == 200, response.text
        response = await client.get(BASE + "/statements/employee/employee", params={
            "date_from": "2026-11-03", "date_to": "2026-11-08"})
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["opening_balance"] == "190.00"
        assert body["closing_balance"] == "130.00"
        assert body["totals"] == {"for_party": "0.00", "for_us": "60.00"}
        assert len(body["items"]) == 6
        assert [r["balance_after"] for r in body["items"]] == [
            "180.00", "170.00", "160.00", "150.00", "140.00", "130.00"]
        assert body["scope"] == "all" and body["can_print"] is True
        assert body["prepared_by"]["id"] == "owner"
    run(scenario)


@pytest.mark.parametrize("report_enabled,print_enabled,movements,purpose,status", [
    (False, False, "all", "view", 403), (False, True, "all", "print", 403),
    (True, False, "all", "view", 200), (True, False, "all", "print", 403),
    (True, True, "all", "print", 200), (True, True, "none", "print", 404),
])
def test_report_and_print_require_explicit_grant_and_movement_scope(report_enabled, print_enabled, movements, purpose, status):
    async def scenario(db, client):
        await grant(db, operations=[], resources=[report_grant(
            report=report_enabled, print=print_enabled, movements=movements)])
        response = await client.get(BASE + "/statements/employee/employee", headers=STAFF,
                                    params={**PERIOD, "purpose": purpose})
        assert response.status_code == status, response.text
        if status == 200:
            assert response.json()["can_print"] is print_enabled
        foreign = await client.get(BASE + "/statements/bank/foreign", headers=STAFF, params=PERIOD)
        assert foreign.status_code == 404
    run(scenario)


def test_own_statement_never_exposes_opening_actual_balance_or_hidden_counterpart():
    async def scenario(db, client):
        from operational_balance_service import create_movement
        await db[STATES].update_one({"_id": "owner"}, {"$set": {"openings.employee:employee.actor_id": "staff"}})
        for actor, value in [("owner", "30.00"), ("staff", "10.00")]:
            await create_movement(db, "owner", actor, {
                "request_id": "own-statement-" + actor, "party_type": "employee", "party_id": "employee",
                "bank_id": "bank", "source_account_type": "bank", "amount": value, "currency": "SAR",
                "direction": "outgoing", "kind": "payment", "note": "CONFIDENTIAL BANK DETAIL",
                "reference": "HIDDEN-REFERENCE-" + actor, "receipt_id": None, "allocations": [],
                "business_date": "2026-11-04"}, clock="2026-11-04T10:00:00+00:00")
        await grant(db, operations=[], resources=[report_grant(movements="own", print=True)])
        response = await client.get(BASE + "/statements/employee/employee", headers=STAFF,
                                    params={**PERIOD, "purpose": "print"})
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["scope"] == "own" and len(body["items"]) == 1
        assert body["items"][0]["amount"] == "10.00" and body["items"][0]["balance_after"] is None
        assert body["resource"]["balance"] is body["opening_balance"] is body["closing_balance"] is None
        assert "CONFIDENTIAL" not in response.text and "HIDDEN-REFERENCE" not in response.text
        ledger = (await client.get(BASE + "/ledger/employee/employee", headers=STAFF)).json()
        assert ledger["total"] == 1
    run(scenario)


def test_print_revocation_while_preparing_cannot_return_prepared_data(monkeypatch):
    async def scenario(db, client):
        import operational_balance_workspace as workspace
        await grant(db, operations=[], resources=[report_grant(print=True)])
        original = workspace.prepare_in_thread
        async def revoke_after_projection(function, *args, **kwargs):
            result = await original(function, *args, **kwargs)
            if "statement.<locals>.project" in function.__qualname__:
                await grant(db, operations=[], resources=[report_grant(print=False)], version=2)
            return result
        monkeypatch.setattr(workspace, "prepare_in_thread", revoke_after_projection)
        response = await client.get(BASE + "/statements/employee/employee", headers=STAFF,
                                    params={**PERIOD, "purpose": "print"})
        assert response.status_code == 409, response.text
        assert response.json()["detail"]["code"] == "operational_access_changed"
        assert "opening_balance" not in response.text
    run(scenario)


def test_running_balance_is_calculated_before_paging_and_account_filtering():
    async def scenario(db, client):
        for day in range(2, 9):
            response = await client.post(BASE + "/entries", json=entry(
                party_type="employee", party_id="employee", side="for_us", reason="payment",
                amount="10.00", business_date=f"2026-11-{day:02d}"))
            assert response.status_code == 200, response.text
        page = (await client.get(BASE + "/ledger/employee/employee", params={"page": 2})).json()
        assert [row["balance_after"] for row in page["items"]] == ["180.00", "190.00", "200.00"]
        bank = (await client.get(BASE + "/movements", params={"kind": "bank", "id": "bank"})).json()
        assert bank["items"][0]["balance_after"] == "930.00"
        assert all(row["party_type"] == "bank" and row["party_id"] == "bank" for row in bank["items"])
        cash = (await client.get(BASE + "/movements", params={"kind": "cash", "id": "cash"})).json()
        assert cash["items"][0]["balance_after"] == "500.00"
        assert (await client.get(BASE + "/movements", params={"kind": "bank"})).status_code == 422
        await grant(db, operations=[], resources=[report_grant(balance=False)])
        hidden = (await client.get(BASE + "/ledger/employee/employee", headers=STAFF)).json()
        assert all(row["balance_after"] is None for row in hidden["items"])
    run(scenario)


def test_daily_salary_compaction_preserves_manual_movements_and_complete_statement(monkeypatch):
    async def scenario(db, client):
        import operational_balance_workspace as workspace
        monkeypatch.setattr(workspace, "now", lambda: "2026-11-18T08:30:00+00:00")
        state = await db[STATES].find_one({"_id": "owner"})
        state["started_at"] = "2026-10-01T00:00:00+00:00"
        for day in ["2026-10-31", *[f"2026-11-{i:02d}" for i in range(1, 7)]]:
            key = "salary:employee:" + day
            state["engine"].setdefault("salary_days", {})[key] = {"amount": "100.00", "currency": "SAR"}
            state["engine"].setdefault("obligations", {})[key] = {
                "id": key, "kind": "salary", "party_type": "employee", "party_id": "employee",
                "currency": "SAR", "expected": "0.00", "confirmed": "100.00", "direction": "payable", "business_date": day}
        await db[STATES].replace_one({"_id": "owner"}, state)
        paid = await client.post(BASE + "/entries", json=entry(party_type="employee", party_id="employee",
            side="for_us", reason="payment", amount="30.00", business_date="2026-11-03"))
        assert paid.status_code == 200, paid.text
        manual = await client.post(BASE + "/entries", json=entry(party_type="employee", party_id="employee",
            side="for_party", reason="accrual", amount="50.00", business_date="2026-11-04", source_type=None, source_id=None))
        assert manual.status_code == 200, manual.text
        ledger = (await client.get(BASE + "/ledger/employee/employee")).json()
        assert ledger["total"] == 4  # latest salary, cash payment, manual accrual, opening
        auto = next(row for row in ledger["items"] if row.get("automatic_history"))
        assert auto["business_date"] == "2026-11-06" and auto["history_count"] == 7
        assert auto["balance_after"] == "920.00"
        history = (await client.get(BASE + "/history/employee/employee")).json()
        assert history["total"] == 6 and history["history_count"] == 7 and history["total_pages"] == 2
        assert history["current_month_total"] == "600.00"
        assert history["previous_months"] == [{"month": "2026-10", "count": 1, "amount": "100.00",
                                               "currency": "SAR", "balance_after": None}]
        statement = (await client.get(BASE + "/statements/employee/employee", params=PERIOD)).json()
        assert len(statement["items"]) == 8 and statement["closing_balance"] == "920.00"
        assert statement["opening_balance"] == "300.00"
        assert statement["totals"] == {"for_party": "650.00", "for_us": "30.00"}
    run(scenario)


@pytest.mark.parametrize("dates", [
    {"date_from": "2026-11-31", "date_to": "2026-12-01"},
    {"date_from": "2026-11-05", "date_to": "2026-11-01"},
    {"date_from": "2026-10-01", "date_to": "2026-11-01"},
])
def test_invalid_or_prestart_period_is_rejected(dates):
    async def scenario(db, client):
        response = await client.get(BASE + "/statements/employee/employee", params=dates)
        assert response.status_code == 422, response.text
    run(scenario)


def test_opening_uses_cutover_day_even_if_its_audit_record_was_created_later():
    async def scenario(db, client):
        await db[STATES].update_one({"_id": "owner"}, {"$set": {
            "openings.employee:employee.created_at": "2026-11-15T13:00:00+00:00"}})
        saved = await client.post(BASE + "/entries", json=entry(party_type="employee", party_id="employee",
            side="for_us", reason="payment", amount="30.00", business_date="2026-11-02"))
        assert saved.status_code == 200, saved.text
        body = (await client.get(BASE + "/statements/employee/employee", params={
            "date_from": "2026-11-01", "date_to": "2026-11-02"})).json()
        assert body["opening_balance"] == "200.00" and body["closing_balance"] == "170.00"
        assert body["items"][0]["balance_after"] == "170.00"
        ledger = (await client.get(BASE + "/ledger/employee/employee")).json()
        opening = next(row for row in ledger["items"] if row["reason"] == "opening")
        assert opening["business_date"] == "2026-11-01"
        assert opening["recorded_at"] == "2026-11-15T13:00:00+00:00"
    run(scenario)


def test_hidden_balance_also_hides_opening_even_with_all_movements():
    async def scenario(db, client):
        await grant(db, operations=[], resources=[report_grant(balance=False)])
        body = (await client.get(BASE + "/ledger/employee/employee", headers=STAFF)).json()
        assert body["items"] == [] and body["resource"]["balance"] is None
    run(scenario)


def test_chronology_normalizes_timestamp_offsets_in_the_same_business_day():
    from operational_workspace_projection import with_running_balances
    account = {"kind": "employee", "access": {"movements": "all", "balance": True},
               "balance": {"amount": "30.00", "side": "for_party"}}
    rows = [{"id": "first", "business_date": "2026-11-02", "recorded_at": "2026-11-02T10:00:00+03:00",
             "side": "for_party", "amount": "10.00"},
            {"id": "second", "business_date": "2026-11-02", "recorded_at": "2026-11-02T08:00:00Z",
             "side": "for_party", "amount": "20.00"}]
    result, status = with_running_balances(account, rows)
    assert status == "available"
    assert [(row["id"], row["balance_after"]) for row in result] == [("second", "30.00"), ("first", "10.00")]


@pytest.mark.parametrize("kind,side,direction,expected", [
    ("employee", "for_party", "outgoing", "70.00"),
    ("supplier", "for_party", "outgoing", "70.00"),
    ("courier", "for_party", "outgoing", "70.00"),
    ("store_driver", "for_party", "outgoing", "70.00"),
    ("provider", "for_us", "incoming", "-70.00"),
    ("pos_receivable", "for_us", "incoming", "-70.00"),
    ("bank", "for_us", "incoming", "170.00"),
    ("cash", "for_us", "incoming", "170.00"),
])
def test_statement_signs_reconcile_with_canonical_accounts(kind, side, direction, expected):
    from operational_balance_access import AccessPolicy
    from operational_balance_service import report
    from operational_balance_workspace import _balance, ledger_items
    from operational_workspace_projection import statement
    state = initial("owner")
    state.update(status="active", started_at="2026-11-01T00:00:00+00:00")
    state["openings"] = {"opening": {"id": "opening", "party_type": kind, "party_id": "target", "name": "Target",
        "currency": "SAR", "amount": "120.00", "direction": side, "created_at": state["started_at"]}}
    cash = kind in {"bank", "cash"}
    state["movements"] = [{"id": "move", "party_type": "external_person" if cash else kind,
        "party_id": "outside" if cash else "target", "name": "Source", "currency": "SAR", "amount": "50.00",
        "direction": direction, "kind": "collection" if direction == "incoming" else "payment",
        "bank_kind": kind if cash else "bank", "bank_id": "target" if cash else "receiving-bank",
        "actual_fee_amount": "0.00", "allocations": [] if cash else [{"obligation_id": "opening", "amount": "50.00"}],
        "occurred_at": "2026-11-02T10:00:00+00:00", "actor_id": "owner"}]
    before = deepcopy(state)
    computed = report(state)
    current = next(row for row in computed["parties"] if (row["party_type"], row["party_id"]) == (kind, "target"))
    target = {"kind": kind, "id": "target", "name": "Target", "currency": "SAR",
              "access": {"movements": "all", "balance": True, "report": True, "print": True},
              "balance": _balance(current, kind)}
    rows = ledger_items(state, computed, target, AccessPolicy({"id": "owner"}, "owner", True, True, 1, (), []))
    body = statement(target, rows, **PERIOD, actor={"id": "owner"}, generated_at="2026-11-18T12:00:00+00:00",
                     purpose="print", started_at=state["started_at"])
    assert body["closing_balance"] == body["items"][-1]["balance_after"] == expected
    assert body["balance_status"] == "available"
    assert state == before


def test_real_engine_delivery_history_and_direct_bank_evidence_do_not_double_driver_balance(monkeypatch):
    async def scenario(db, client):
        from operational_balance_engine import reconcile
        from tests.test_operational_balance_driver_review_report import recorded
        import operational_balance_workspace as workspace
        monkeypatch.setattr(workspace, "now", lambda: "2026-11-18T14:00:00+00:00")
        await db.store_drivers.insert_one({"id": "driver", "user_id": "owner", "name": "Driver", "status": "active"})
        state = await db[STATES].find_one({"_id": "owner"})
        state["started_at"] = "2026-10-01T00:00:00+00:00"
        days = ["2026-10-20", "2026-10-21", *[f"2026-11-{i:02d}" for i in range(1, 10)], "2026-11-18"]
        orders = [{"id": "order-" + str(i), "created_at": day + "T06:00:00+00:00",
            "updated_at": day + "T10:00:00+00:00", "delivered_at": day + "T10:00:00+00:00",
            "currency": "SAR", "status": "delivered", "items": [],
            "carrier": {"id": "driver", "party_type": "store_driver", "cost": "20.00", "currency": "SAR"},
            "cod_amount": "100.00" if day == "2026-11-09" else "0.00"} for i, day in enumerate(days)]
        orders += [{**orders[-1], "id": "pending-" + str(i), "status": "in_progress", "cod_amount": value}
                   for i, value in enumerate(["200.00", "300.00", "400.00"])]
        for order in orders:
            if order["status"] == "delivered":
                native_delivery(order, order["delivered_at"])
        state = reconcile(state, {"orders": orders}, "2026-11-18T12:00:00+00:00")
        evidence, _ = recorded("bank_transfer")
        fact = next(iter(evidence["driver_payment_review_facts"].values()))
        fact.update(destination_id="bank", amount="300.00", order_id="order-11", order_number="D-110",
                    approved_by="owner", approved_at="2026-11-18T13:00:00+00:00")
        fact["seal"] = digest({key: value for key, value in fact.items() if key != "seal"})
        movement = evidence["movements"][0]
        movement.update(bank_id="bank", party_id="bank", name="Bank", bank_name="Bank", amount="300.00",
                        order_id="order-11", kind="collection", actual_fee_amount="0.00", actor_id="owner",
                        occurred_at=fact["approved_at"], business_date="2026-11-18")
        state["movements"].append(movement)
        state["driver_payment_review_facts"] = {fact["id"]: fact}
        await db[STATES].replace_one({"_id": "owner"}, state)
        response = await client.get(BASE + "/ledger/store_driver/driver")
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["resource"]["balance"]["amount"] == "140.00"
        assert body["total"] == 3  # latest fee, actual cash custody, bank proof
        auto = next(row for row in body["items"] if row.get("automatic_history"))
        info = next(row for row in body["items"] if row.get("informational"))
        assert auto["history_count"] == 12 and auto["amount"] == "20.00"
        assert auto["balance_after"] == info["balance_after"] == "140.00"
        assert info["side"] == "informational" and info["amount"] == "300.00"
        assert body["expected_collections"]["total"] == 3 and body["expected_collections"]["amount"] == "900.00"
        history = (await client.get(BASE + "/history/store_driver/driver")).json()
        assert history["total"] == 10 and history["current_month_total"] == "200.00"
        assert history["previous_months"][0]["count"] == 2 and history["previous_months"][0]["amount"] == "40.00"
        report = (await client.get(BASE + "/statements/store_driver/driver", params={
            "date_from": "2026-10-01", "date_to": "2026-11-18"})).json()
        assert report["totals"] == {"for_party": "240.00", "for_us": "100.00"}
        assert report["closing_balance"] == "140.00"
    run(scenario)


def test_multi_component_delivery_counts_once_without_fabricated_summary_balance():
    from operational_balance_access import AccessPolicy
    from operational_balance_engine import reconcile
    from operational_balance_service import report
    from operational_balance_workspace import _balance, ledger_items
    from operational_workspace_projection import compact_rows, history
    state = initial("owner")
    state.update(status="active", started_at="2026-11-01T00:00:00+00:00")
    order = {"id": "order", "created_at": "2026-11-02T06:00:00+00:00", "updated_at": "2026-11-02T10:00:00+00:00",
             "delivered_at": "2026-11-02T10:00:00+00:00", "currency": "SAR", "status": "delivered", "items": [],
             "cod_amount": "0.00", "carrier": {"id": "courier", "party_type": "courier", "currency": "SAR",
             "cost": "25.00", "fee_components": [{"id": "base_shipping", "amount": "20.00", "complete": True},
                 {"id": "cod_commission", "amount": "5.00", "complete": True}]}}
    native_delivery(order, order["delivered_at"])
    state = reconcile(state, {"orders": [order]}, "2026-11-18T12:00:00+00:00")
    computed = report(state)
    current = next(row for row in computed["parties"] if row["party_type"] == "courier")
    target = {"kind": "courier", "id": "courier", "name": "Courier", "currency": "SAR",
              "access": {"movements": "all", "balance": True}, "balance": _balance(current, "courier")}
    rows = ledger_items(state, computed, target, AccessPolicy({"id": "owner"}, "owner", True, True, 1, (), []))
    assert len(rows) == 2
    compact = compact_rows(rows)
    assert len(compact) == 1 and compact[0]["amount"] == "25.00" and compact[0]["history_count"] == 1
    assert compact[0]["summary"] is True and compact[0]["balance_after"] is None
    details = history(target, rows, page=1, page_size=5, generated_at="2026-11-18T12:00:00+00:00")
    assert details["total"] == 1 and details["current_month_total"] == "25.00"


def test_unreconciled_projection_withholds_historical_balances_with_explanation():
    from operational_workspace_projection import statement
    target = {"kind": "employee", "currency": "SAR", "access": {
        "movements": "all", "balance": True, "report": True, "print": True},
        "balance": {"amount": "200.00", "side": "for_party"}}
    body = statement(target, [{"id": "incomplete", "business_date": "2026-11-02", "amount": "100.00",
        "side": "for_party"}], **PERIOD, actor={"id": "owner"}, generated_at="2026-11-18T12:00:00+00:00",
        purpose="view", started_at="2026-11-01T00:00:00+00:00")
    assert body["balance_status"] == "unreconciled" and body["balance_note"]
    assert body["opening_balance"] is body["closing_balance"] is body["items"][0]["balance_after"] is None


def test_customer_and_counterparty_titles_require_the_saved_counterpart_scope():
    from decimal import Decimal
    from operational_balance_access import AccessPolicy
    from operational_balance_workspace import _row
    target = {"kind": "bank", "id": "bank", "name": "الإنماء", "currency": "SAR"}
    item = {"id": "settlement", "party_type": "provider", "party_id": "salla", "name": "سلة",
            "bank_kind": "bank", "bank_id": "bank", "bank_name": "الإنماء",
            "customer_name_snapshot": "عميل محفوظ", "customer_name": "اسم لاحق",
            "order_number": "S-100", "note": "وصف خاص بسلة", "reference": "S-TRANSFER",
            "business_date": "2026-11-02", "kind": "settlement"}
    bank_only = [resource("bank", "bank", movements="all")]
    restricted = AccessPolicy({"id": "staff"}, "owner", False, True, 1, (), bank_only)
    hidden = _row(restricted, target, item, Decimal("40"), "for_us", "تسوية")
    assert hidden["description"] == "تسوية" and hidden["order_number"] is None
    assert not {"customer_name", "counterparty_name", "source_label", "reference"}.intersection(hidden)
    permitted = AccessPolicy({"id": "staff"}, "owner", False, True, 1, (), [
        *bank_only, resource("provider", "salla", movements="all")])
    visible = _row(permitted, target, item, Decimal("40"), "for_us", "تسوية")
    assert visible["customer_name"] == "عميل محفوظ"
    assert visible["counterparty_name"] == "سلة" and visible["source_label"] == "الإنماء"
    assert visible["order_number"] == "S-100" and visible["reference"] == "S-TRANSFER"
    assert visible["note"] == item["note"] and visible["description"] == "تسوية"


def test_similar_salary_or_shipping_identifiers_without_matching_recorded_provenance_are_not_compacted():
    from operational_workspace_projection import automatic_source
    salary = {"id": "salary:employee:2026-11-02", "kind": "salary", "party_type": "employee",
              "party_id": "employee", "business_date": "2026-11-02", "direction": "payable",
              "confirmed": "100.00", "currency": "SAR"}
    shipping = {"id": "shipping:order:manual", "kind": "shipping", "party_type": "store_driver",
                "party_id": "driver", "direction": "payable", "confirmed": "25.00", "currency": "SAR"}
    state = {"engine": {"salary_days": {salary["id"]: {"amount": "90.00", "currency": "SAR"}},
        "facts": {"shipping:order": {"id": "driver", "party_type": "store_driver", "cost": "20.00",
            "fee_currency": "SAR", "delivered_at": "2026-11-02T10:00:00+00:00"}}}}
    assert automatic_source({}, salary) == (None, None)
    assert automatic_source(state, salary) == (None, None)
    assert automatic_source(state, shipping) == (None, None)
    assert automatic_source(state, {**shipping, "id": "shipping:order"}) == (None, None)
    assert automatic_source(state, {**shipping, "id": "shipping:order", "confirmed": "20.00"}) == (
        "delivery", "shipping:order")


def provider_fee_state():
    state = initial("owner")
    state.update(status="active", started_at="2026-11-01T00:00:00+00:00")
    state["openings"] = {"provider": {"id": "provider", "party_type": "provider", "party_id": "provider",
        "name": "Provider", "currency": "SAR", "amount": "120.00", "direction": "for_us",
        "created_at": state["started_at"]}}
    state["movements"] = [{"id": "settlement", "party_type": "provider", "party_id": "provider",
        "name": "Provider", "currency": "SAR", "amount": "50.00", "actual_fee_amount": "10.00",
        "direction": "incoming", "kind": "settlement", "bank_kind": "bank", "bank_id": "bank",
        "allocations": [{"obligation_id": "provider", "amount": "60.00"}],
        "occurred_at": "2026-11-02T10:00:00+00:00", "actor_id": "owner"}]
    return state


def test_provider_settlement_fee_is_a_recorded_separate_leg_without_an_extra_bank_debit():
    from operational_balance_access import AccessPolicy
    from operational_balance_service import report
    from operational_balance_workspace import _balance, ledger_items
    from operational_workspace_projection import statement
    state = provider_fee_state()
    computed = report(state)
    provider = next(row for row in computed["parties"] if row["party_type"] == "provider")
    assert provider["outstanding_receivable"] == "60.00"
    target = {"kind": "provider", "id": "provider", "name": "Provider", "currency": "SAR",
              "access": {"movements": "all", "balance": True, "report": True, "print": True},
              "balance": _balance(provider, "provider")}
    rows = ledger_items(state, computed, target, AccessPolicy({"id": "owner"}, "owner", True, True, 1, (), []))
    body = statement(target, rows, **PERIOD, actor={"id": "owner"}, generated_at="2026-11-18T12:00:00+00:00",
                     purpose="view", started_at=state["started_at"])
    assert body["balance_status"] == "available"
    assert body["opening_balance"] == "-120.00" and body["closing_balance"] == "-60.00"
    assert body["totals"] == {"for_party": "60.00", "for_us": "0.00"}
    assert [(row["amount"], row["balance_after"]) for row in body["items"]] == [("50.00", "-70.00"), ("10.00", "-60.00")]
    assert body["items"][1]["reason"] == "settlement_fee"
    bank = next(row for row in computed["parties"] if row["party_type"] == "bank")
    target.update(kind="bank", id="bank", name="Bank", balance=_balance(bank, "bank"))
    bank_rows = ledger_items(state, computed, target, AccessPolicy({"id": "owner"}, "owner", True, True, 1, (), []))
    bank_body = statement(target, bank_rows, **PERIOD, actor={"id": "owner"}, generated_at="2026-11-18T12:00:00+00:00",
                          purpose="view", started_at=state["started_at"])
    assert bank_body["closing_balance"] == "50.00" and len(bank_body["items"]) == 1


def test_settlement_fee_inherits_own_scope_and_hidden_bank_redaction():
    from operational_balance_access import AccessPolicy
    from operational_balance_service import report
    from operational_balance_workspace import _balance, ledger_items
    from operational_workspace_projection import statement
    state = provider_fee_state()
    first = state["movements"][0]
    first.update(actor_id="staff", bank_name="Hidden bank", note="Hidden bank note", reference="Hidden bank reference")
    state["movements"].append({**first, "id": "other-actor-settlement", "actor_id": "owner",
        "amount": "5.00", "actual_fee_amount": "1.00", "allocations": [{"obligation_id": "provider", "amount": "6.00"}]})
    computed = report(state)
    current = next(row for row in computed["parties"] if row["party_type"] == "provider")
    grant = report_grant("provider", "provider", movements="own", print=True)
    target = {"kind": "provider", "id": "provider", "name": "Provider", "currency": "SAR",
              "access": grant, "balance": _balance(current, "provider")}
    policy = AccessPolicy({"id": "staff"}, "owner", False, True, 1, (), [grant])
    rows = ledger_items(state, computed, target, policy)
    body = statement(target, rows, **PERIOD, actor={"id": "staff"}, generated_at="2026-11-18T12:00:00+00:00",
                     purpose="print", started_at=state["started_at"])
    assert len(body["items"]) == 2 and body["totals"]["for_party"] == "60.00"
    assert all(row["balance_after"] is None for row in body["items"])
    assert "Hidden bank" not in str(body)
    assert not any("other-actor" in row["id"] for row in body["items"])


@pytest.mark.parametrize("kind,direction,payment_direction,expected", [
    ("supplier", "payable", "outgoing", "-10.00"),
    ("provider", "receivable", "incoming", "10.00"),
])
def test_derived_credit_allocation_does_not_duplicate_claim_or_cash(kind, direction, payment_direction, expected):
    from operational_balance_access import AccessPolicy
    from operational_balance_service import report
    from operational_balance_workspace import _balance, ledger_items
    from operational_workspace_projection import statement
    state = initial("owner")
    state.update(status="active", started_at="2026-11-01T00:00:00+00:00")
    claim = kind + ":invoice"
    state["engine"]["obligations"] = {claim: {"id": claim, "kind": kind, "party_type": kind,
        "party_id": "target", "currency": "SAR", "direction": direction, "confirmed": "80.00",
        "expected": "0.00", "business_date": "2026-11-02"}}
    paid = {"id": "payment", "party_type": kind, "party_id": "target", "name": "Target",
        "currency": "SAR", "direction": payment_direction, "kind": "payment", "amount": "100.00",
        "actual_fee_amount": "0.00", "bank_kind": "bank", "bank_id": "bank",
        "allocations": [{"obligation_id": claim, "amount": "100.00"}],
        "occurred_at": "2026-11-03T10:00:00+00:00", "actor_id": "owner"}
    state["movements"] = [paid, {**paid, "id": "refund", "kind": "refund", "amount": "10.00",
        "direction": "incoming" if payment_direction == "outgoing" else "outgoing",
        "allocations": [{"obligation_id": "credit:" + claim, "amount": "10.00"}],
        "occurred_at": "2026-11-04T10:00:00+00:00"}]
    before = deepcopy(state)
    computed = report(state)
    assert any(row.get("derived_credit") for row in computed["obligations"])
    current = next(row for row in computed["parties"] if row["party_type"] == kind)
    target = {"kind": kind, "id": "target", "name": "Target", "currency": "SAR",
              "access": {"movements": "all", "balance": True, "report": True, "print": True},
              "balance": _balance(current, kind)}
    rows = ledger_items(state, computed, target, AccessPolicy({"id": "owner"}, "owner", True, True, 1, (), []))
    body = statement(target, rows, **PERIOD, actor={"id": "owner"}, generated_at="2026-11-18T12:00:00+00:00",
                     purpose="view", started_at=state["started_at"])
    assert len(body["items"]) == 3 and body["closing_balance"] == expected
    assert body["balance_status"] == "available"
    assert not any(row["id"].startswith("credit:") for row in body["items"])
    assert state == before


def test_period_notes_preserve_user_text_without_system_descriptions_or_hidden_counterpart_notes():
    async def scenario(db, client):
        original = "  ملاحظة الموظف الأصلية\nمرجع المراجعة 42  "
        saved = await client.post(BASE + "/entries", json=entry(party_type="employee", party_id="employee",
            side="for_us", reason="payment", amount="10.00", business_date="2026-11-02", note=original))
        assert saved.status_code == 200, saved.text
        saved = await client.post(BASE + "/entries", json=entry(party_type="employee", party_id="employee",
            side="for_us", reason="payment", amount="10.00", business_date="2026-11-03"))
        assert saved.status_code == 200, saved.text
        response = await client.get(BASE + "/statements/employee/employee", params=PERIOD)
        body = response.json()
        assert body["items"][0]["note"] == original.strip()  # request validation trims only outer whitespace
        assert "ملاحظة الموظف" not in body["items"][0]["description"]
        assert "note" not in body["items"][1]
        await grant(db, operations=[], resources=[report_grant()])
        hidden = (await client.get(BASE + "/statements/employee/employee", params=PERIOD, headers=STAFF)).json()
        assert all("note" not in row for row in hidden["items"])
    run(scenario)


@pytest.mark.parametrize("refunded_at", ["2026-11-04T10:00:00+00:00", None])
def test_proven_provider_refund_match_does_not_post_its_existing_effect_again_or_invent_execution_date(refunded_at):
    from fastapi import HTTPException
    from operational_balance_access import AccessPolicy
    from operational_balance_service import report
    from operational_balance_workspace import _balance, ledger_items
    from operational_workspace_projection import statement
    state = initial("owner")
    state.update(status="active", started_at="2026-11-01T00:00:00+00:00")
    key = "provider:order"
    state["engine"]["obligations"] = {key: {"id": key, "kind": "provider", "party_type": "provider",
        "party_id": "provider", "currency": "SAR", "direction": "receivable", "confirmed": "200.00",
        "expected": "0.00", "business_date": "2026-11-02"}}
    evidence = {"order_id": "order", "amount": "100.00"}
    evidence_id = "refund:provider:reference"
    state["engine"]["facts"] = {evidence_id: evidence}
    state["customer_returns"] = [{"id": "refund-case", "status": "provider_matched", "method": "original",
        "refund_source_type": "provider", "refund_source_id": "provider", "order_id": "order",
        "amount": "100.00", "currency": "SAR", "provider_evidence_id": evidence_id,
        "provider_evidence_reference": "reference", "provider_evidence_hash": digest(evidence),
        "matched_by": "owner", "matched_at": "2026-11-05T10:00:00+00:00", "refunded_at": refunded_at}]
    computed = report(state)
    current = next(row for row in computed["parties"] if row["party_type"] == "provider")
    target = {"kind": "provider", "id": "provider", "name": "Provider", "currency": "SAR",
              "access": {"movements": "all", "balance": True, "report": True, "print": True},
              "balance": _balance(current, "provider")}
    rows = ledger_items(state, computed, target, AccessPolicy({"id": "owner"}, "owner", True, True, 1, (), []))
    info = next(row for row in rows if row.get("informational"))
    assert info["side"] == "informational"
    if refunded_at is None:
        assert info["business_date"] == ""
        with pytest.raises(HTTPException) as rejected:
            statement(target, rows, **PERIOD, actor={"id": "owner"}, generated_at="2026-11-18T12:00:00+00:00",
                      purpose="view", started_at=state["started_at"])
        assert rejected.value.status_code == 409
    else:
        body = statement(target, rows, **PERIOD, actor={"id": "owner"}, generated_at="2026-11-18T12:00:00+00:00",
                         purpose="view", started_at=state["started_at"])
        assert body["closing_balance"] == "-200.00" and body["items"][-1]["balance_after"] == "-200.00"
        assert body["totals"] == {"for_party": "0.00", "for_us": "200.00"}
