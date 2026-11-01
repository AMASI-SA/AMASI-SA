"""Compose the unchanged financial projection with evidenced noncash linkage."""
from operational_financial_position_core import build_financial_position as project_balances
from operational_driver_review_report import link_review_destinations


def build_financial_position(state, *, cost_exposure=None, generated_at=None):
    result = project_balances(state, cost_exposure=cost_exposure, generated_at=generated_at)
    for currency, data in result["currencies"].items():
        if currency != "SAR":
            continue
        data["deliveries"], issues = link_review_destinations(state, data["deliveries"])
        result["issues"].extend(issues)
        # Keep recorded rows visible, but a contradictory approval/effect cannot
        # be presented as a complete, trustworthy net monetary position.
        if any(issue["code"] != "operational_driver_review_awaiting_delivery_snapshot" for issue in issues):
            data["completeness"]["actual"] = False
            for key in ("monetary_position", "displayed_position"):
                data["metrics"][key] = None
                for row in data["current_rows"]:
                    if row["id"] == key:
                        row.update(amount=None, reason="غير متاح: اعتماد التحصيل لا يطابق الأثر المسجل؛ يلزم مراجعة الدليل.")
    return result
