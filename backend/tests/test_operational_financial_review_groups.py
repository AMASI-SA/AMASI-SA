"""Review grouping keeps each order and each distinct reason without changing money."""
from copy import deepcopy

from operational_financial_position import build_financial_position
from test_operational_balance_financial_position import NOW, opening, state_for, table


def test_courier_review_groups_preserve_distinct_orders_reasons_and_first_evidence():
    state = state_for()
    opening(state, "bank", "bank", "100.00")

    def issue(number, reason, *, name="شركة الشحن", identity=None):
        return {"code": "courier_fee_review_required", "source_id": identity or f"order-{number}",
                "required_action": reason, "courier_fee_review": {
                    "order_number": number, "party_name": name, "currency": "SAR"}}

    state["engine"]["issues"] = [
        issue("7", "راجع تاريخ التنفيذ"), issue("7", "راجع تاريخ التنفيذ"),
        issue("8", "راجع التعرفة"), issue("7", "راجع التعرفة", identity="later-evidence"),
        issue("9", "راجع التعرفة"), issue("7", "راجع التعرفة"),
        issue("7", "راجع تاريخ التنفيذ", name="شركة أخرى"),
    ]
    before = deepcopy(state)
    result = build_financial_position(state, generated_at=NOW)
    rows = table(result, "courier_fee_review")["rows"]
    assert [(row["name"], row["orders"]) for row in rows] == [("شركة الشحن", 3), ("شركة أخرى", 1)]
    assert rows[0]["samples"] == "7، 8، 9"
    assert rows[0]["pending_order_details"] == [
        {"order_id": "order-7", "order_number": "7", "reasons": [
            {"code": "courier_fee_review_required", "message": "راجع تاريخ التنفيذ"},
            {"code": "courier_fee_review_required", "message": "راجع التعرفة"}]},
        {"order_id": "order-8", "order_number": "8", "reasons": [
            {"code": "courier_fee_review_required", "message": "راجع التعرفة"}]},
        {"order_id": "order-9", "order_number": "9", "reasons": [
            {"code": "courier_fee_review_required", "message": "راجع التعرفة"}]},
    ]
    assert [row["order_number"] for row in result["currencies"]["SAR"]["shipping_fee_review_orders"]] == ["7", "8", "9"]
    assert all(set(row) == {"name", "orders", "examples", "required_action", "pending_order_details", "samples"} for row in rows)
    assert state == before
    assert result == build_financial_position(state, generated_at=NOW)
