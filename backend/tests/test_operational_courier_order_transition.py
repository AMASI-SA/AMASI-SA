"""Order-native delivery evidence must belong to exactly one current waybill."""
import pytest

from order_activity_v1 import normalize_history_row
from test_operational_balance_verified_shipping_fees import calculated
from test_operational_balance_delivery_cutover import active, courier_data, fee_total, DELIVERED, START, NOW

COLLECTION = "order_activity_events_v2"


def fixture(old=True):
    data = courier_data()
    source = data["unified_orders"][0]
    raw = source["raw_by_source"]["salla_direct"]
    current = source["salla_shipping_current"]
    current["source_kind"] = "order"
    for row in (raw["shipping"], raw["shipments"][0]):
        row.pop("delivered_at")
    if not old:
        raw["created_at"] = "2026-10-05T01:00:00+00:00"
    return data, source, raw, current


def history(at=DELIVERED, **changes):
    row = {"id": "transition-1", "description": "تم تغيير الحالة إلى تم التوصيل", "created_at": at, **changes}
    event = normalize_history_row(row, order_number="10", provider_order_id="100")
    proof = event.pop("native_status_transition", None)
    return {**event, "user_id": "owner", **({"native_status_transition": [proof]} if proof else {})}


@pytest.mark.parametrize("old", [True, False])
@pytest.mark.parametrize("proof", ["canonical", "history"])
def test_dated_order_transition_for_one_waybill_confirmed_once_without_cod(old, proof):
    data, source, raw, current = fixture(old)
    if proof == "history":
        data[COLLECTION] = [history({"date": "2026-10-06 15:00:00", "timezone": "Asia/Riyadh"})]
    else:
        raw["status"] = {"slug": "delivered", "changed_at": DELIVERED}
    first = calculated(data)
    assert fee_total(first) == 10
    assert first["engine"]["facts"]["delivery_event:100"]["delivered_at"] == DELIVERED
    assert first["engine"]["facts"]["delivery_event:100"]["timestamp_source"] == "order_status_transition"
    assert "cod:100" not in first["engine"]["obligations"]
    again = calculated(data, first)
    assert first["engine"]["obligations"] == again["engine"]["obligations"]
    assert first["openings"] == again["openings"] and first["movements"] == again["movements"] == []


def test_prior_period_uses_review_not_automatic_debt():
    data, source, raw, _ = fixture()
    raw["status_changed_at"] = DELIVERED
    prior = active()
    prior["engine"] = {"as_of": NOW}
    state = calculated(data, prior)
    assert fee_total(state) == 0
    held = next(i for i in state["engine"]["issues"] if i["code"] == "courier_fee_historical_review_required")
    assert held["courier_fee_review"]["candidate_amount"] == "10.00"


@pytest.mark.parametrize("case", [
    "split", "superseded", "no_waybill", "cancelled", "pending_component", "stale_version",
    "wrong_status", "conflicting_order_status", "wrong_owner", "wrong_order", "duplicate_delivery",
    "later_return", "history_updated_only", "ambiguous_timezone", "start", "shipment_after_delivery",
    "direct_disagrees", "malformed_shipment",
])
def test_conflicts_and_boundary_never_confirm_shipping(case):
    data, source, raw, current = fixture()
    record = history()
    data[COLLECTION] = [record]
    if case == "split":
        raw["shipments"].append({**raw["shipments"][0], "id": "second"})
    elif case == "superseded":
        current["superseded_shipment_ids"] = ["old"]
    elif case == "no_waybill":
        raw["shipments"][0].pop("tracking_number")
        raw["shipping"].pop("tracking_number")
    elif case in {"cancelled", "pending_component"}:
        source["g47_salla_snapshot"] = {"cancelled" if case == "cancelled" else "component_pending": True}
    elif case == "stale_version":
        source["g47_salla_snapshot"] = {"source_updated_at": START}
    elif case == "wrong_status":
        raw["status"] = "shipped"
    elif case == "conflicting_order_status":
        source["order_status_slug"] = "shipped"
    elif case == "wrong_owner":
        record["user_id"] = "elsewhere"
    elif case == "wrong_order":
        record["provider_order_id"] = "other"
    elif case == "duplicate_delivery":
        data[COLLECTION].append(history(NOW, id="transition-2"))
    elif case == "later_return":
        data[COLLECTION].append(history(NOW, id="transition-2", description="تم تغيير الحالة إلى مسترجع"))
    elif case == "history_updated_only":
        data[COLLECTION] = [history(None, updated_at=DELIVERED)]
    elif case == "ambiguous_timezone":
        data[COLLECTION] = [history("2026-10-06 15:00:00")]
    elif case == "start":
        data[COLLECTION] = [history(START)]
    elif case == "shipment_after_delivery":
        raw["shipments"][0]["created_at"] = NOW
    elif case == "direct_disagrees":
        raw["status_changed_at"] = NOW
    elif case == "malformed_shipment":
        raw["shipments"].append(None)
    state = calculated(data)
    assert fee_total(state) == 0 and "cod:100" not in state["engine"]["obligations"]


def test_authenticated_history_refresh_preserves_native_time_once_on_legacy_row(monkeypatch):
    import order_activity_v1 as activity
    from operational_balance_engine import reconcile
    from operational_balance_sources import collect_sources
    from test_operational_balance_integration import run

    async def scenario(db):
        data, source, raw, _ = fixture()
        source.update(order_number="10", salla_order_id="100")
        for collection, docs in data.items():
            if docs:
                await db[collection].insert_many(docs)
        native = {"id": "delivered-evt", "description": "تم تغيير الحالة إلى تم التوصيل",
                  "created_at": {"date": "2026-10-06 15:00:00", "timezone": "Asia/Riyadh"}}
        previous = normalize_history_row(native, order_number="10", provider_order_id="100")
        previous.pop("native_status_transition")
        await db[COLLECTION].insert_one({**previous, "user_id": "owner"})

        async def observed(_db, owner, path, **kwargs):
            assert owner == "owner"
            if path == "/transactions":
                assert kwargs["base_params"] == {"order_id": "100"}
                return []
            assert path == "/orders/100/histories"
            return [native]

        monkeypatch.setattr(activity, "_fetch_salla_pages", observed)
        async def snapshot():
            return await collect_sources(db, "owner", START, NOW, operational_state=active())
        assert fee_total(reconcile(active(), await snapshot(), NOW)) == 0
        for _ in range(2):
            await activity.refresh_order_activity_from_salla(db, user_id="owner", order_number="10")
        events = await db[COLLECTION].find({"user_id": "owner"}).to_list(5)
        assert len(events) == 1 and events[0]["native_status_transition"] == [{"delivered": True, "at": DELIVERED}]
        calculated = reconcile(active(), await snapshot(), NOW)
        assert fee_total(calculated) == 10
        assert "cod:100" not in calculated["engine"]["obligations"]
    run(scenario)
