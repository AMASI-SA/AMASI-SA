import pytest

import mezan_attribution_profit_bridge as bridge
from mezan_attribution_profit_bridge import aggregate_attribution_ledger


def _row(order_key, campaign_id=None, *, safe=False, profit=None, lines=None):
    return {
        "order_key": order_key,
        "attribution": {
            "quality": "confirmed" if safe else "inferred" if campaign_id else "unattributed",
            "decision_safe": safe,
            "provider": "snapchat" if campaign_id else None,
            "account_id": "a1" if campaign_id else None,
            "campaign_id": campaign_id,
            "campaign_name": "Winner" if campaign_id else None,
        },
        "profit": profit or {"known": False, "net_profit_sar": None},
        "line_items": lines or [],
    }


def test_unknown_profit_is_excluded_not_zeroed():
    result = aggregate_attribution_ledger([
        _row("1", "c1", safe=True, profit={"known": True, "net_profit_sar": 25}),
        _row("2", "c1", safe=True, profit={"known": False, "net_profit_sar": None}),
    ])
    assert result["coverage"]["profit_known_orders"] == 1
    assert result["coverage"]["profit_unknown_orders"] == 1
    assert result["known_order_profit"]["net_profit_sar"] == 25
    assert result["known_order_profit"]["partial"] is True
    assert result["known_order_profit"]["unknown_is_zero"] is False
    assert result["campaigns"][0]["known_net_profit_sar"] == 25
    assert result["campaigns"][0]["known_net_profit_is_partial"] is True


def test_only_confirmed_rows_are_decision_safe():
    result = aggregate_attribution_ledger([
        _row("1", "c1", safe=True),
        _row("2", "c1", safe=False),
        _row("3"),
    ])
    assert result["coverage"]["ledger_orders"] == 3
    assert result["coverage"]["confirmed_orders"] == 1
    assert result["coverage"]["decision_safe_orders"] == 1
    assert result["campaigns"][0]["orders"] == 2
    assert result["campaigns"][0]["decision_safe_orders"] == 1


def test_product_summary_never_invents_net_profit_allocation():
    result = aggregate_attribution_ledger([
        _row(
            "1",
            "c1",
            safe=True,
            profit={"known": True, "net_profit_sar": 80},
            lines=[
                {"product_id": "p1", "product_variant_id": "v1", "sku": "S1", "product_name": "One", "quantity": 2, "line_total_sar": 100},
                {"product_id": "p2", "product_variant_id": "v2", "sku": "S2", "product_name": "Two", "quantity": 1, "line_total_sar": 50},
            ],
        )
    ])
    assert len(result["products"]) == 2
    assert all(item["net_profit_sar"] is None for item in result["products"])
    assert all("not_allocated" in item["profit_allocation"] for item in result["products"])
    assert result["guardrails"]["order_profit_allocated_to_products"] is False


def test_product_line_sales_becomes_unknown_if_any_line_total_missing():
    result = aggregate_attribution_ledger([
        _row("1", lines=[{"product_id": "p1", "quantity": 1, "line_total_sar": 10}]),
        _row("2", lines=[{"product_id": "p1", "quantity": 2, "line_total_sar": None}]),
    ])
    product = result["products"][0]
    assert product["orders"] == 2
    assert product["units"] == 3
    assert product["line_sales_known"] is False
    assert product["line_sales_sar"] is None


class _Cursor:
    def __init__(self, rows):
        self.rows = rows

    def sort(self, *_args, **_kwargs):
        return self

    def limit(self, n):
        self.rows = self.rows[:n]
        return self

    async def to_list(self, length):
        return self.rows[:length]


class _Collection:
    def __init__(self, rows):
        self.rows = rows

    def find(self, *_args, **_kwargs):
        return _Cursor(list(self.rows))


class _DB:
    def __init__(self, orders):
        self.unified_orders = _Collection(orders)


@pytest.mark.asyncio
async def test_backfill_continues_after_individual_failure(monkeypatch):
    calls = []

    async def fake_sync(_db, *, user_id, order):
        calls.append(order["order_number"])
        if order["order_number"] == "2":
            return {"synced": False, "reason": "ledger_sync_failed", "error_type": "RuntimeError"}
        return {
            "synced": True,
            "decision_safe": order["order_number"] == "1",
            "profit_known": order["order_number"] == "3",
        }

    monkeypatch.setattr(bridge, "safe_sync_order_to_attribution_ledger", fake_sync)
    result = await bridge.refresh_existing_orders_to_ledger(
        _DB([
            {"user_id": "u1", "order_number": "1"},
            {"user_id": "u1", "order_number": "2"},
            {"user_id": "u1", "order_number": "3"},
        ]),
        "u1",
    )
    assert calls == ["1", "2", "3"]
    assert result["scanned"] == 3
    assert result["synced"] == 2
    assert result["failed"] == 1
    assert result["decision_safe"] == 1
    assert result["profit_known"] == 1
    assert result["external_writes"] is False


@pytest.mark.asyncio
async def test_bridge_never_compares_partial_known_profit_as_full_total(monkeypatch):
    async def fake_envelope(*_args, **_kwargs):
        return {"totals": {"net_profit": 1000}, "quality": {"complete": True}}

    async def fake_rows(*_args, **_kwargs):
        return [
            _row("1", "c1", safe=True, profit={"known": True, "net_profit_sar": 100}),
            _row("2", "c1", safe=True, profit={"known": False, "net_profit_sar": None}),
        ]

    import mezan_profit_engine

    monkeypatch.setattr(mezan_profit_engine, "build_mezan_profit_envelope", fake_envelope)
    monkeypatch.setattr(bridge, "load_ledger_rows_for_period", fake_rows)
    result = await bridge.build_attribution_profit_bridge(
        object(), "u1", from_date="2026-08-01", to_date="2026-08-22"
    )
    assert result["reconciliation"]["store_net_profit_sar"] == 1000
    assert result["reconciliation"]["known_order_net_profit_sar"] == 100
    assert result["reconciliation"]["comparable_as_full_total"] is False
    assert result["reconciliation"]["difference_sar"] is None
