"""Shipping payables keep their contract currency, independently of the sale."""
from copy import deepcopy
from decimal import Decimal

import pytest

from operational_balance_engine import reconcile
from operational_balance_sources import collect_sources
from operational_courier_tariffs import terms
from test_operational_balance_source_fixes import rich_rate, shipping_data
from test_operational_balance_sources import HardBoundaryDB, START, NOW, run


def initial():
    return {"owner_id": "owner", "status": "active", "started_at": START,
            "movements": [], "openings": {}}


def foreign_order_sources(*, cod=False, rate_kind="simple", fixed="0", percent="1", bounded=False,
                          delivered=False, operational_state=None, historical_fee_order=None):
    data = shipping_data(rich_rate() if rate_kind == "rich" else None,
                         currency="KWD", total={"amount": "100", "currency": "KWD"},
                         payment={"method": "cod" if cod else "mada"}, paid_amount="0",
                         delivered_shipment=delivered, status="delivered" if delivered else "in_progress")
    rate = data["mz2_shipping_setup_v2"][0]["contracts"][0]
    rate.update(delivery_fee="15", vat_included=False, vat_percent="15", cod_fixed_fee=fixed, cod_percent=percent)
    tier = {"min_amount": "0", "max_amount": "50" if bounded else None,
            "min_inclusive": True, "max_inclusive": False,
            "commission_percent": str(Decimal(percent) / 100), "fixed_fee": fixed,
            "vat_percent": "15", "vat_included": False}
    if rate_kind == "operational_courier_tariff":
        rate.update(kind=rate_kind, terms=terms({"cost_per_order": 15, "vat_percent": 15,
            "cod_fee_percent": str(Decimal(percent) / 100), "cod_fee_fixed_per_order": fixed,
            "cod_fee_vat_percent": 15, **({"cod_fee_tiers": [tier]} if bounded else {})}))
    elif rate_kind == "rich":
        rate["contract_version"].update(shipping_cost="15", shipping_cost_vat_inclusive=False,
            commission_vat_inclusive=False, cod_fee_tiers=[tier])
    return run(collect_sources(HardBoundaryDB(data), "owner", START, NOW,
                               operational_state=operational_state, historical_fee_order=historical_fee_order))


def test_foreign_sale_uses_sar_shipping_contract_currency_and_keeps_sale_currency():
    sources = foreign_order_sources()
    result = reconcile(initial(), sources, NOW)
    shipping = result["engine"]["obligations"]["shipping:100:base_shipping"]
    assert (shipping["expected"], shipping["currency"]) == ("17.25", "SAR")
    assert sources["orders"][0]["currency"] == "KWD"
    assert sources["orders"][0]["carrier"]["currency"] == "SAR"
    assert result["movements"] == []
    assert reconcile(result, sources, NOW) == result


@pytest.mark.parametrize("cod", [False, True])
def test_actual_courier_delivery_adapter_keeps_customer_currency_and_quotes_sar_fee(cod):
    sources = foreign_order_sources(cod=cod, delivered=True)
    source_order = sources["orders"][0]
    assert source_order["delivery_event"]["currency"] == "KWD"
    assert source_order["carrier"]["currency"] == "SAR"
    result = reconcile(initial(), sources, NOW)
    base = result["engine"]["obligations"]["shipping:100:base_shipping"]
    assert (base["confirmed"], base["currency"]) == ("17.25", "SAR")
    if cod:
        collection = result["engine"]["obligations"]["cod:100"]
        assert (collection["confirmed"], collection["currency"]) == ("100.00", "KWD")
        assert "shipping:100:cod_commission" not in result["engine"]["obligations"]
    reread = foreign_order_sources(cod=cod, delivered=True, operational_state=result)
    assert reconcile(result, reread, NOW) == result


def test_delivery_event_currency_must_match_the_order_currency():
    sources = foreign_order_sources(cod=True, delivered=True)
    sources["orders"][0]["delivery_event"]["currency"] = "SAR"
    result = reconcile(initial(), sources, NOW)
    assert result["engine"]["obligations"] == {}
    assert result["engine"]["facts"] == {}
    assert any(row["code"] == "operational_delivery_event_contract_invalid" for row in result["engine"]["issues"])


def test_native_driver_foreign_currency_delivery_remains_unsupported():
    from test_operational_balance_delivery_evidence import evidence, resolve
    values = evidence(driver=True)
    values[0]["currency"] = values[0]["amounts"]["total"]["currency"] = "KWD"
    values[2][0]["currency"] = "KWD"
    with pytest.raises(ValueError, match="^operational_delivery_currency_unsupported$"):
        resolve(values)


def test_prestart_foreign_delivery_remains_outside_the_sar_historical_fee_review_contract():
    from test_operational_balance_delivery_cutover import courier_data
    data = courier_data()
    raw = data["unified_orders"][0]["raw_by_source"]["salla_direct"]
    raw["currency"] = raw["total"]["currency"] = "KWD"
    sources = run(collect_sources(HardBoundaryDB(data), "owner", START, NOW))
    assert not sources["orders"] and not sources["delivery_events"]
    assert any(row["code"] == "operational_delivery_currency_unsupported" for row in sources["issues"])


@pytest.mark.parametrize("rate_kind", ["simple", "operational_courier_tariff", "rich"])
def test_cross_currency_cod_never_prices_a_percentage_without_conversion_evidence(rate_kind):
    sources = foreign_order_sources(cod=True, rate_kind=rate_kind)
    carrier = sources["orders"][0]["carrier"]
    assert carrier["fee_components"][1] == {
        "id": "cod_commission", "amount": None, "complete": False}
    assert carrier["cost"] == "17.25" and not carrier["fee_complete"]
    assert any(row["code"] == "shipping_cod_currency_requires_review" for row in sources["issues"])
    result = reconcile(initial(), sources, NOW)
    obligations = result["engine"]["obligations"]
    assert (obligations["shipping:100:base_shipping"]["expected"],
            obligations["shipping:100:base_shipping"]["currency"]) == ("17.25", "SAR")
    assert (obligations["cod:100"]["expected"], obligations["cod:100"]["currency"]) == ("100.00", "KWD")
    assert reconcile(result, sources, NOW) == result


@pytest.mark.parametrize("rate_kind", ["simple", "operational_courier_tariff", "rich"])
def test_currency_independent_fixed_cod_fee_is_kept_in_contract_currency(rate_kind):
    sources = foreign_order_sources(cod=True, rate_kind=rate_kind, fixed="2", percent="0")
    carrier = sources["orders"][0]["carrier"]
    assert carrier["fee_complete"] and carrier["cost"] == "19.55"
    assert carrier["fee_components"][1] == {"id": "cod_commission", "amount": "2.30", "complete": True}
    result = reconcile(initial(), sources, NOW)
    row = result["engine"]["obligations"]["shipping:100:cod_commission"]
    assert (row["expected"], row["currency"]) == ("2.30", "SAR")
    assert result["engine"]["obligations"]["cod:100"]["currency"] == "KWD"


@pytest.mark.parametrize("rate_kind", ["simple", "operational_courier_tariff", "rich"])
def test_mixed_cod_preserves_known_fixed_part_without_claiming_percentage_is_complete(rate_kind):
    sources = foreign_order_sources(cod=True, rate_kind=rate_kind, fixed="2")
    carrier = sources["orders"][0]["carrier"]
    assert carrier["cost"] == "19.55" and not carrier["fee_complete"]
    parts = {row["id"]: row for row in carrier["fee_components"]}
    assert parts["cod_fixed_commission"] == {"id": "cod_fixed_commission", "amount": "2.30", "complete": True}
    assert parts["cod_commission"]["amount"] is None and not parts["cod_commission"]["complete"]
    result = reconcile(initial(), sources, NOW)
    row = result["engine"]["obligations"]["shipping:100:cod_fixed_commission"]
    assert (row["expected"], row["currency"]) == ("2.30", "SAR")
    assert any(row["code"] == "shipping_cod_currency_requires_review" for row in result["engine"]["issues"])
    assert reconcile(result, sources, NOW) == result


@pytest.mark.parametrize("rate_kind", ["operational_courier_tariff", "rich"])
def test_amount_bounded_fixed_fee_is_unavailable_without_a_basis_for_the_contract_band(rate_kind):
    sources = foreign_order_sources(cod=True, rate_kind=rate_kind, fixed="2", percent="0", bounded=True)
    carrier = sources["orders"][0]["carrier"]
    assert carrier["cost"] == "17.25" and not carrier["fee_complete"]
    assert not any(row["id"] == "cod_fixed_commission" for row in carrier["fee_components"])


def complete_commission(sources):
    complete = deepcopy(sources)
    carrier = complete["orders"][0]["carrier"]
    carrier.update(cost="20.70", fee_complete=True, fee_components=[
        {"id": "base_shipping", "amount": "17.25", "complete": True},
        {"id": "cod_commission", "amount": "3.45", "complete": True}])
    complete["issues"] = []
    return complete


@pytest.mark.parametrize("delivered", [False, True])
def test_later_complete_commission_includes_the_fixed_part_once_and_keeps_native_rounding(delivered):
    sources = foreign_order_sources(cod=True, fixed="2")
    if delivered:
        sources["orders"][0].update(status="delivered", delivered_at="2026-10-06T12:00:00+00:00")
    partial = reconcile(initial(), sources, NOW)
    complete = complete_commission(sources)
    result = reconcile(partial, complete, NOW)
    rows = [r for r in result["engine"]["obligations"].values() if r["kind"] == "shipping"]
    field = "confirmed" if delivered else "expected"
    assert sum(Decimal(r[field]) for r in rows) == Decimal("20.70")
    fixed_key = "shipping:100:cod_fixed_commission"
    if delivered:
        assert result["engine"]["facts"][fixed_key] == partial["engine"]["facts"][fixed_key]
        assert result["engine"]["obligations"]["shipping:100:cod_commission"]["confirmed"] == "1.15"
    else:
        assert result["engine"]["obligations"][fixed_key]["expected"] == "0.00"
    assert reconcile(result, complete, NOW) == result


def test_saved_whole_commission_does_not_gain_a_second_fixed_fee_from_partial_source():
    partial = foreign_order_sources(cod=True, fixed="2")
    partial["orders"][0].update(status="delivered", delivered_at="2026-10-06T12:00:00+00:00")
    prior = reconcile(initial(), complete_commission(partial), NOW)
    result = reconcile(prior, partial, NOW)
    assert result["engine"]["facts"] == prior["engine"]["facts"]
    assert result["engine"]["obligations"] == prior["engine"]["obligations"]
    assert "shipping:100:cod_fixed_commission" not in result["engine"]["obligations"]


@pytest.mark.parametrize("allocated", [False, True])
def test_open_fixed_estimate_is_not_duplicated_when_delivery_and_complete_fee_arrive_together(allocated):
    partial = foreign_order_sources(cod=True, fixed="2")
    prior = reconcile(initial(), partial, NOW)
    if allocated:
        prior["movements"] = [payment("shipping:100:cod_fixed_commission", currency="SAR")]
    complete = complete_commission(partial)
    complete["orders"][0].update(status="delivered", delivered_at="2026-10-06T12:00:00+00:00")
    result = reconcile(prior, complete, NOW)
    rows = {k: r for k, r in result["engine"]["obligations"].items() if r["kind"] == "shipping"}
    if allocated:
        assert rows == {k: r for k, r in prior["engine"]["obligations"].items() if r["kind"] == "shipping"}
        assert any(row["code"] == "shipping_settled_component_review_required" for row in result["engine"]["issues"])
        assert not any(k.startswith("shipping:100:") for k in result["engine"]["facts"])
    else:
        assert sum(Decimal(r["expected"]) for r in rows.values()) == 0
        assert sum(Decimal(r["confirmed"]) for r in rows.values()) == Decimal("20.70")
    assert result["movements"] == prior["movements"]
    assert reconcile(result, complete, NOW) == result


def old_estimate():
    state = initial()
    key = "shipping:100:base_shipping"
    state["engine"] = {"facts": {}, "obligations": {key: {
        "id": key, "kind": "shipping", "party_id": "carrier", "party_type": "courier",
        "currency": "KWD", "expected": "17.25", "confirmed": "0.00", "direction": "payable",
        "business_date": "2026-10-06", "fee_component": "base_shipping"}}}
    return state


def test_old_unconfirmed_estimate_changes_currency_in_place_and_replays_without_duplicate():
    prior = old_estimate()
    before = deepcopy(prior)
    sources = foreign_order_sources()
    result = reconcile(prior, sources, NOW)
    shipping = [r for r in result["engine"]["obligations"].values() if r["kind"] == "shipping"]
    assert {row["currency"] for row in shipping} == {"SAR"}
    assert sum(Decimal(row["expected"]) for row in shipping) == Decimal("17.25")
    assert result["engine"]["obligations"]["shipping:100:base_shipping"]["expected"] == "17.25"
    assert result["engine"]["facts"] == before["engine"]["facts"]
    assert result["movements"] == before["movements"] and prior == before
    assert reconcile(result, sources, NOW) == result


def payment(key, currency="KWD"):
    return {"id": "synthetic-payment", "currency": currency, "amount": "1.00",
            "party_type": "courier", "party_id": "carrier", "kind": "payment",
            "direction": "outgoing", "bank_kind": "bank", "bank_id": "synthetic-bank",
            "allocations": [{"obligation_id": key, "amount": "1.00"}]}


def test_allocated_old_estimate_is_held_for_review_without_relabelling_payment_or_adding_fee():
    prior = old_estimate()
    key = "shipping:100:base_shipping"
    prior["movements"] = [payment(key)]
    before = deepcopy(prior)
    sources = foreign_order_sources(cod=True)
    result = reconcile(prior, sources, NOW)
    assert {k: r for k, r in result["engine"]["obligations"].items() if r["kind"] == "shipping"} == before["engine"]["obligations"]
    assert result["engine"]["facts"] == before["engine"]["facts"]
    assert result["movements"] == before["movements"] and prior == before
    assert any(row["code"] == "shipping_confirmed_currency_conflict" for row in result["engine"]["issues"])
    assert result["engine"]["obligations"]["cod:100"]["currency"] == "KWD"
    assert reconcile(result, sources, NOW) == result


@pytest.mark.parametrize("settled", [False, True])
def test_legacy_confirmed_fee_keeps_its_currency_amount_and_facts_until_explicit_review(settled):
    sources = foreign_order_sources()
    source_order = sources["orders"][0]
    source_order.update(status="delivered", delivered_at="2026-10-06T12:00:00+00:00")
    prior = reconcile(initial(), sources, NOW)
    # Reproduce the persisted pre-fix shape: facts had no fee currency and the
    # payable inherited the customer's currency.
    for row in prior["engine"]["obligations"].values():
        if row["kind"] == "shipping":
            row["currency"] = "KWD"
    for key, saved in prior["engine"]["facts"].items():
        if key.startswith(("shipping:", "shipping_fee:")):
            saved.pop("currency", None)
            saved.pop("fee_currency", None)
    if settled:
        prior["movements"] = [payment("shipping:100:base_shipping")]
    before = deepcopy(prior)
    result = reconcile(prior, sources, NOW)
    assert result["engine"]["obligations"] == before["engine"]["obligations"]
    assert result["engine"]["facts"] == before["engine"]["facts"]
    assert result["movements"] == before["movements"] and prior == before
    assert any(row["code"] == "shipping_confirmed_currency_conflict" for row in result["engine"]["issues"])
    assert reconcile(result, sources, NOW) == result


def test_new_delivery_freezes_contract_currency_and_keeps_foreign_cod_separate():
    sources = foreign_order_sources(cod=True)
    sources["orders"][0].update(status="delivered", delivered_at="2026-10-06T12:00:00+00:00")
    result = reconcile(initial(), sources, NOW)
    base = result["engine"]["obligations"]["shipping:100:base_shipping"]
    cod = result["engine"]["obligations"]["cod:100"]
    assert (base["confirmed"], base["currency"]) == ("17.25", "SAR")
    assert (cod["confirmed"], cod["currency"]) == ("100.00", "KWD")
    assert "shipping:100:cod_commission" not in result["engine"]["obligations"]
    assert result["engine"]["facts"]["shipping:100:base_shipping"]["currency"] == "SAR"
    assert reconcile(result, sources, NOW) == result

    changed = deepcopy(sources)
    changed["orders"][0]["carrier"].update(currency="EUR", cost="99")
    changed["orders"][0]["carrier"]["fee_components"][0]["amount"] = "99"
    rechecked = reconcile(result, changed, NOW)
    assert rechecked["engine"]["facts"] == result["engine"]["facts"]
    assert rechecked["engine"]["obligations"] == result["engine"]["obligations"]
    assert any(row["code"] == "shipping_confirmed_currency_conflict" for row in rechecked["engine"]["issues"])


@pytest.mark.parametrize("paid_currency,opening_currency,held", [
    ("SAR", None, True), ("SAR", "KWD", True), ("KWD", None, False), ("SAR", "SAR", False)])
def test_mixed_currency_payout_guard_checks_the_fee_and_its_payables(paid_currency, opening_currency, held):
    prior = initial()
    allocations = []
    if opening_currency:
        prior["openings"]["old"] = dict(id="old", party_type="courier", party_id="carrier",
            currency=opening_currency, direction="for_party", name="Carrier", amount="10.00")
        allocations = [{"obligation_id": "old", "amount": "10.00"}]
    prior["movements"] = [dict(id="paid", party_type="courier", party_id="carrier", kind="payment",
        currency=paid_currency, direction="outgoing", amount="10.00", allocations=allocations)]
    sources = foreign_order_sources(cod=True, percent="0", delivered=True, operational_state=prior)
    result = reconcile(prior, sources, NOW)
    obs = result["engine"]["obligations"]
    if held:
        assert "shipping:100:base_shipping" not in obs
        candidate = next(i["courier_fee_review"] for i in sources["issues"]
                         if i["code"] == "courier_fee_historical_review_required")
        assert (candidate["candidate_amount"], candidate["currency"]) == ("17.25", "SAR")
    else:
        assert (obs["shipping:100:base_shipping"]["confirmed"], obs["shipping:100:base_shipping"]["currency"]) == ("17.25", "SAR")
    assert (obs["cod:100"]["confirmed"], obs["cod:100"]["currency"]) == ("100.00", "KWD")
    assert result["engine"]["facts"]["delivery_event:100"]["currency"] == "KWD"
    assert result["movements"] == prior["movements"] and result["openings"] == prior["openings"]


def test_native_courier_overlap_shows_sar_fee_for_foreign_customer_and_preserves_cod(monkeypatch):
    from operational_financial_position import build_financial_position
    async def native_postings(db, owner, candidates, *, max_rows):
        assert owner == "owner" and [p["order_number"] for p in candidates] == ["10"]
        return {"10": "Existing native courier fee"}
    monkeypatch.setattr("operational_historical_shipping_fee.native_conflicts", native_postings)
    sources = foreign_order_sources(cod=True, percent="0", delivered=True)
    result = reconcile(initial(), sources, NOW)
    info = next(i["courier_fee_review"] for i in sources["issues"] if i["code"] == "courier_fee_native_overlap")
    assert (info["candidate_amount"], info["currency"]) == ("17.25", "SAR")
    table = next(t for t in build_financial_position(result)["currencies"]["SAR"]["tables"] if t["id"] == "courier_fee_review")
    assert table["rows"][0]["samples"] == "10"
    obs = result["engine"]["obligations"]
    assert "shipping:100:base_shipping" not in obs and obs["cod:100"]["currency"] == "KWD"


def test_poststart_historical_fee_preview_and_approved_replay_keep_contract_currency():
    from operational_balance_store import digest
    from operational_historical_shipping_fee import APPROVALS, review
    prior = initial()
    sources = foreign_order_sources(delivered=True, historical_fee_order="10")
    candidate, = sources["historical_fee_candidates"]
    assert (candidate["amount"], candidate["currency"]) == ("17.25", "SAR")
    assert sources["orders"][0]["delivery_event"]["currency"] == "KWD"
    openings, payouts, issues = review(prior, candidate)
    assert not issues
    prior[APPROVALS] = {digest("10"): {"candidate": candidate, "approved_by": "owner",
        "opening_payout_hash": digest([openings, payouts])}}
    reread = foreign_order_sources(delivered=True, operational_state=prior)
    result = reconcile(prior, reread, NOW)
    row = result["engine"]["obligations"][candidate["obligation_id"]]
    assert (row["confirmed"], row["currency"]) == ("17.25", "SAR")
    assert "historical_base_fee:100" in result["engine"]["facts"]
    assert result["movements"] == [] and reconcile(result, reread, NOW) == result


@pytest.mark.parametrize("rate_currency,fact_currency,expected", [
    ("legacy", None, "28.75"), ("KWD", None, None),
    ("legacy", "legacy", "23.00"), ("legacy", "KWD", None)])
def test_sar_return_quote_accepts_legacy_domestic_evidence_and_rejects_explicit_foreign_amount(
        monkeypatch, rate_currency, fact_currency, expected):
    import operational_customer_returns as returns
    from fastapi import HTTPException
    setup = shipping_data()["mz2_shipping_setup_v2"][0]
    rate = setup["contracts"][0]
    rate.update(delivery_fee="25", vat_included=False)
    if rate_currency == "legacy":
        rate.pop("currency")
    else:
        rate["currency"] = rate_currency
    facts = {}
    if fact_currency:
        facts = {"shipping:100": {"id": "carrier", "party_type": "courier"},
                 "shipping:100:base_shipping": {"cost": "23.00"}}
        if fact_currency != "legacy":
            facts["shipping:100:base_shipping"]["currency"] = fact_currency

    def reply(value):
        async def read_only(*args):
            return value
        return read_only
    monkeypatch.setattr(returns, "entity", reply({"name": "Carrier"}))
    monkeypatch.setattr(returns, "order_view", reply({"id": "100", "currency": "SAR", "shipping_at": NOW}))
    monkeypatch.setattr(returns, "read", reply({"engine": {"facts": facts}}))
    monkeypatch.setattr(returns, "rows", reply([setup]))
    quote = returns.shipping_quote(None, "owner", "courier", "carrier", NOW, "10")
    if expected is None:
        with pytest.raises(HTTPException) as err:
            run(quote)
        assert err.value.detail["code"] == "customer_return_shipping_incomplete"
    else:
        assert run(quote)["amount"] == expected


@pytest.mark.parametrize("old_currency", ["KWD", "SAR"])
@pytest.mark.parametrize("hold", ["pooled", "native"])
def test_delivery_hold_removes_only_open_shipping_forecast_and_replays(monkeypatch, old_currency, hold):
    prior = old_estimate() if old_currency == "KWD" else reconcile(
        initial(), foreign_order_sources(cod=True, percent="0"), NOW)
    if hold == "pooled":
        prior["movements"] = [{**payment("unused", "SAR"), "allocations": []}]
    else:
        async def conflict(*args, **kwargs):
            return {"10": "Native courier fee"}
        monkeypatch.setattr("operational_historical_shipping_fee.native_conflicts", conflict)
    key = "shipping:100:base_shipping"
    assert prior["engine"]["obligations"][key]["expected"] == "17.25"
    def read(state):
        return foreign_order_sources(cod=True, percent="0", delivered=True, operational_state=state)
    result = reconcile(prior, read(prior), NOW)
    obs = result["engine"]["obligations"]
    assert key not in obs
    if hold == "native":
        # An empty old KWD payable would itself reject a later SAR review,
        # independently of the separately reported native conflict.
        from operational_historical_shipping_fee import review
        preview = foreign_order_sources(delivered=True, operational_state=result, historical_fee_order="10")
        candidate, = preview["historical_fee_candidates"]
        assert not review(result, candidate)[2]
    assert key not in result["engine"]["facts"] and result["engine"]["facts"]["delivery_event:100"]["currency"] == "KWD"
    assert (obs["cod:100"]["confirmed"], obs["cod:100"]["currency"]) == ("100.00", "KWD")
    assert result["movements"] == prior["movements"] and result["openings"] == prior["openings"]
    assert reconcile(result, read(result), NOW) == result


@pytest.mark.parametrize("protected", ["allocated", "frozen", "confirmed"])
def test_delivery_hold_preserves_each_protected_fee_row(protected):
    prior = old_estimate()
    key = "shipping:100:base_shipping"
    if protected == "confirmed":
        # Other components can already be confirmed while the base is still open.
        key = "shipping:100:cod_fixed_commission"
        prior["engine"]["obligations"][key] = {**prior["engine"]["obligations"]["shipping:100:base_shipping"],
            "id": key, "currency": "SAR", "confirmed": "2.30", "expected": "0.00", "fee_component": "cod_fixed_commission"}
    if protected == "frozen":
        prior["engine"]["facts"][key] = {"cost": "17.25", "currency": "KWD"}
    prior["movements"] = [payment(key)] if protected == "allocated" else [{**payment("unused", "SAR"), "allocations": []}]
    previous = deepcopy(prior)
    def read(state):
        return foreign_order_sources(cod=True, percent="0", delivered=True, operational_state=state)
    result = reconcile(prior, read(prior), NOW)
    assert result["engine"]["obligations"][key] == previous["engine"]["obligations"][key]
    if key in previous["engine"]["facts"]:
        assert result["engine"]["facts"][key] == previous["engine"]["facts"][key]
    assert result["movements"] == previous["movements"] and result["openings"] == previous["openings"]
    assert result["engine"]["obligations"]["cod:100"]["currency"] == "KWD"
    assert reconcile(result, read(result), NOW) == result
