"""Card and order list must explain the same outstanding amount, without posting it twice."""
from copy import deepcopy

from operational_balance_service import report
from operational_balance_store import STATES
from operational_configured_provider_fees import refresh_provider_fee_inputs
from operational_provider_balance_sar import refresh_basis
from operational_workspace_provider_orders import provider_position
from test_operational_balance_configured_provider_fees import paid_state, settings
from test_operational_balance_workspace import BASE, grant, resource, run


def test_card_and_drilldown_reuse_net_after_paid_out_cash_and_fees():
    state, _ = paid_state("emkan", terms=settings("إمكان", rate="10", fixed="0", vat="0"))
    state["movements"].append({"id": "payout", "kind": "settlement", "party_type": "provider", "party_id": "emkan",
        "currency": "SAR", "amount": "47.00", "actual_fee_amount": "3.00", "direction": "incoming",
        "allocations": [{"obligation_id": "provider:one", "amount": "50.00"}]})
    before = deepcopy(state)
    data = report(state, provider_balances_sar=True, workspace_provider="emkan")
    assert provider_position(data, "emkan", show_orders=True)["net"] == "45.00"
    assert provider_position(data, "emkan", show_orders=True)["paid_orders"] == 1
    row, = data["details"]["workspace_provider_orders"]
    assert (row["paid"], row["settled"], row["remaining_fee"], row["net"]) == ("100.00", "50.00", "5.00", "45.00")
    assert data["details"]["provider_balances"]["SAR"][0]["settled"] == "47.00"
    assert "workspace_provider_orders" not in report(state)["details"]
    assert state == before


def test_foreign_payment_links_only_its_proven_sar_value_and_keeps_native_principal():
    terms = settings("PayPal", rate="4.9", fixed="1.13", vat="0")
    state, order = paid_state("paypal", gross="10.00", currency="KWD", terms=terms)
    data = report(state, provider_balances_sar=True, workspace_provider="paypal")
    row, = data["details"]["workspace_provider_orders"]
    assert (row["currency"], row["paid"], row["paid_sar"]) == ("KWD", "10.00", None)
    assert provider_position(data, "paypal", show_orders=True)["net"] is None
    assert "paid_orders" not in provider_position(data, "paypal", show_orders=False)
    order["customer_payment_sar"] = {"currency":"KWD", "native":{"paypal":"10.00"}, "shares":{"paypal":"100.00"},
                                      "paid":"100.00", "basis":"salla_list_paid"}
    refresh_basis(state, [order], state["engine"]["issues"])
    refresh_provider_fee_inputs(state, {"provider_fee_settings":terms, "orders":[order]})
    data = report(state, provider_balances_sar=True, workspace_provider="paypal")
    row, = data["details"]["workspace_provider_orders"]
    assert (row["paid"], row["paid_sar"], row["balance_currency"], row["net"]) == ("10.00","100.00","SAR","93.97")


def test_scoped_orders_by_day_reference_and_number_preserve_review_items_and_permissions():
    async def scenario(db, client):
        state, _ = paid_state("emkan", terms=settings("إمكان"))
        engine = state["engine"]
        saved = engine["provider_fee_inputs"]["orders"]["one"]
        saved["order_number"] = "123450"
        saved["payment"]["order_page_reference"] = "ref-70"
        for index in range(12):
            engine["issues"].append({"scope":"provider_payment", "code":"provider_capture_evidence_incomplete",
                "source_id":str(index), "order_number":"REF" + str(index), "provider":"emkan", "currency":"SAR"})
        await db[STATES].update_one({"owner_id":"owner"}, {"$set":{"engine":engine}})
        path = BASE + "/ledger/provider/emkan"
        params = {"provider_orders":"true", "page_size":8}
        answer = await client.get(path, params=params)
        assert answer.status_code == 200, answer.text
        all_rows = answer.json()
        assert (all_rows["total"], len(all_rows["items"]), all_rows["total_pages"]) == (13, 8, 2)
        second = (await client.get(path, params={**params, "page":2})).json()
        assert len(second["items"]) == 5
        assert not {r["id"] for r in all_rows["items"]}.intersection(r["id"] for r in second["items"])
        held = (await client.get(path, params={**params, "q":"ref11"})).json()["items"]
        assert len(held) == 1 and held[0]["paid"] is None and held[0]["net"] is None and held[0]["reasons"]
        for q in ("123450", "ref-70"):
            answer = (await client.get(path, params={**params,"q":q,"date_from":"2026-11-01","date_to":"2026-11-01"})).json()
            assert [r["order_number"] for r in answer["items"]] == ["123450"]
        other = await client.get(BASE + "/ledger/provider/paypal", params=params)
        assert other.status_code == 200 and other.json()["items"] == []
        for version, rules in enumerate(({"movements":"own","report":True,"balance":True},
                                        {"movements":"all","report":False,"balance":True},
                                        {"movements":"all","report":True,"balance":False}), 1):
            row = {**resource("provider","emkan"), **rules}
            await grant(db, operations=[], resources=[row], version=version)
            denied = await client.get(path, params=params, headers={"x-test-user":"staff"})
            assert denied.status_code in {403,404} and "123450" not in denied.text
            context = await client.get(BASE + "/context", params={"provider_positions":"true"}, headers={"x-test-user":"staff"})
            assert context.status_code == 200
            emkan = next(r for r in context.json()["resources"] if r["id"] == "emkan")
            assert "paid_orders" not in emkan.get("provider_position", {})
            assert "123450" not in context.text
    run(scenario)
