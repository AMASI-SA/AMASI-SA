"""Explicit report presentation scopes; no partial accounting calculation."""
from typing import Literal

from operational_financial_activity_runtime import preparation_checkpoint


FinancialPositionView = Literal[
    "all", "summary", "providers", "couriers", "drivers", "employees", "accounts",
    "suppliers", "advertising", "other", "expected", "forecast",
]
FORECAST_VIEWS = frozenset({"all", "expected", "forecast"})
SUMMARY_VIEWS = frozenset({"summary", "forecast"})
TABLES = {
    "summary": frozenset(), "forecast": frozenset(),
    "providers": frozenset({"providers", "balance_origin"}),
    "couriers": frozenset({"couriers", "balance_origin", "courier_fee_review"}),
    "drivers": frozenset({"drivers", "balance_origin", "driver_fee_review"}),
    "employees": frozenset({"employees", "employee_custody"}),
    "accounts": frozenset({"banks_network", "cash"}),
    "suppliers": frozenset({"suppliers"}),
    "advertising": frozenset({"ads_currency_conversion", "ads"}),
    "other": frozenset({"customer_wallets", "customer_other_tenders", "external", "other_balances"}),
    "expected": frozenset({"products_unreceived", "products_received_uninvoiced", "shipping_expected",
                           "cod_expected", "provider_collections_expected"}),
}
PARTY_VIEWS = {"providers": "provider", "couriers": "courier", "drivers": "store_driver"}
DETAIL_VIEWS = frozenset({"all", "couriers", "drivers", "expected"})
LABEL_VIEWS = frozenset({"all", "providers", "couriers", "drivers", "expected"})


def selected_table(view, identity):
    return view == "all" or identity in TABLES[view]


def scope_financial_position(result, state, view):
    """Scope a completed integrity-checked projection, never its source ledger."""
    if view != "all" and view not in TABLES:
        raise ValueError("operational_financial_view_invalid")
    result.update(view=view, revision=state.get("revision", 0), forecast_pending=view == "summary")
    if view == "all":
        return result
    for data in result["currencies"].values():
        preparation_checkpoint()
        tables = []
        pending = data.pop("_unassigned_order_details", {})
        for table in data["tables"]:
            preparation_checkpoint()
            if not selected_table(view, table["id"]):
                continue
            if table["id"] == "balance_origin":
                kind = PARTY_VIEWS[view]
                table["rows"] = [row for row in table["rows"] if row["id"].startswith(kind + ":")]
            tables.append(table)
        if view == "expected":
            for kind, name in (("courier", "شركات الشحن"), ("store_driver", "مناديب المتجر")):
                preparation_checkpoint()
                entries = pending.get(kind, [])
                if not entries:
                    continue
                # Unknown identities stay reachable without assigning money to
                # a named counterparty or manufacturing a zero balance.
                tables.append({"id": "unassigned_" + kind,
                    "title": "طلبات غير منسوبة لجهة: " + name,
                    "section": "fulfillment", "kind": "review", "complete": False,
                    "columns": [{"key": "name", "label": "نوع الجهة", "type": "text"},
                                {"key": "orders", "label": "الطلبات", "type": "count"}],
                    "rows": [{"id": kind, "name": name, "orders": len(entries)}],
                    "totals": {}, "show_totals": False,
                    "unassigned_order_details": entries,
                    "notes": ["جهة التوصيل غير مثبتة؛ لا تنسب هذه الحالات إلى جهة بالاسم ولا تنشئ مبالغ مالية."]})
        data["tables"] = tables
        if view in {"couriers", "drivers", "expected"}:
            delivery = data.get("deliveries")
            if delivery is not None:
                if view == "expected":
                    delivery["rows"] = [row for row in delivery["rows"]
                        if row.get("party_type") not in {"courier", "store_driver"} or not row.get("party_id")]
                else:
                    kind = PARTY_VIEWS[view]
                    delivery["rows"] = [row for row in delivery["rows"] if row.get("party_type") == kind]
        else:
            data.pop("deliveries", None)
        if view != "drivers":
            data.pop("driver_fee_review", None)
        if view not in {"couriers", "drivers"}:
            data.pop("shipping_fee_review_orders", None)
    return result
