"""Compose the unchanged financial projection with evidenced noncash linkage."""
from operational_financial_position_core import build_financial_position as project_balances, refresh_computed_dependencies
from operational_driver_review_report import link_review_destinations
from operational_financial_table_coverage import apply_table_coverage


def build_financial_position(state, *, cost_exposure=None, generated_at=None):
    result = project_balances(state, cost_exposure=cost_exposure, generated_at=generated_at)
    for currency, data in result["currencies"].items():
        if currency != "SAR":
            continue
        # Source candidates have their own nonposting table, never part of debt, cash or COD.
        data["driver_fee_review"] = [{**row["driver_fee_review"], "reason": row["code"],
                                      "required_action": row["required_action"]}
            for row in state.get("engine", {}).get("issues", []) if row.get("driver_fee_review")]
        # An undated shipment may precede startup; never assign an amount/date.
        # Group these potentially historical warnings to avoid giant order tables.
        groups = {}
        candidates = {}
        for issue in state.get("engine", {}).get("issues", []):
            cand = issue.get("courier_fee_review") or {}
            if not cand or cand.get("currency") not in (None, "SAR"):
                continue
            n = str(cand["order_number"])
            candidates.setdefault(n, {"order_number": n, "name": cand["party_name"]})
            k = (cand["party_name"], issue["code"])
            group = groups.setdefault(k, {"name": cand["party_name"], "orders": 0, "examples": [],
                "required_action": issue["required_action"]})
            group["orders"] += 1
            if len(group["examples"]) < 3:
                group["examples"].append(cand["order_number"])
        for row in data["driver_fee_review"]:
            n = str(row["order_number"])
            candidates.setdefault(n, {"order_number": n, "name": row.get("driver_id") or "مندوب المتجر"})
        data["shipping_fee_review_orders"] = sorted(candidates.values(), key=lambda r: r["order_number"])
        if groups:
            data["tables"].append({"id": "courier_fee_review", "title": "شحنات شركات التوصيل قيد المطابقة",
                "section": "fulfillment", "kind": "review", "complete": False, "totals": {},
                "columns": [{"key":k,"label":v,"type":t} for k,v,t in [
                    ("name","شركة الشحن","text"), ("orders","عدد الحالات","count"), ("samples","عينة طلبات","text"),
                    ("required_action","ما يحتاج إلى مطابقة","text")]],
                "rows": [{**g, "samples": "، ".join(g["examples"])} for g in groups.values()],
                "notes": ["قد تكون بعض الحالات سابقة للبداية عند غياب تاريخ تسليم موثوق. لا تُحتسب أجرة أو ذمة من عدد هذه الحالات."]})
        if data["driver_fee_review"]:
            data["tables"].append({
                "id": "driver_fee_review", "title": "أجور توصيل تحتاج مطابقة", "section": "fulfillment",
                "kind": "review", "complete": False, "totals": {},
                "columns": [{"key":k,"label":label,"type":kind} for k,label,kind in [
                    ("order_number","الطلب","text"), ("party_name","المندوب","text"),
                    ("delivered_at","تاريخ التسليم (UTC)","text"), ("candidate_amount","الأجرة في المصدر (ر.س.)","money"),
                    ("required_action","سبب المراجعة","text")]],
                "rows": data["driver_fee_review"],
                "notes": ["مبالغ للمطابقة فقط؛ لا تضاف من هذا الجدول إلى الأجرة المؤكدة أو عهدة التحصيل أو إجمالي الديون."],
            })
        data["deliveries"], issues = link_review_destinations(state, data["deliveries"])
        result["issues"].extend(issues)
        # Keep recorded rows visible, but a contradictory approval/effect cannot
        # be presented as a complete, trustworthy net monetary position.
        if any(issue["code"] != "operational_driver_review_awaiting_delivery_snapshot" for issue in issues):
            apply_table_coverage(data["tables"], [issue for issue in result["issues"]
                if issue["code"] != "operational_driver_review_awaiting_delivery_snapshot"],
                as_of=result["as_of"], started_at=result["started_at"], currency=currency)
            data["completeness"]["actual"] = False
            data["completeness"]["forecast"] = False
            data["metrics"]["forecast_monetary_position"] = None
            for row in data["forecast_rows"]:
                if row["id"] == "forecast_monetary_position":
                    row.update(amount=None, reason="غير متاح: المركز الحالي متعارض مع دليل اعتماد التحصيل؛ لا يمكن إضافته إلى التغير المتوقع.")
            for key in ("monetary_position", "displayed_position"):
                data["metrics"][key] = None
                for row in data["current_rows"]:
                    if row["id"] == key:
                        row.update(amount=None, reason="غير متاح: اعتماد التحصيل لا يطابق الأثر المسجل؛ يلزم مراجعة الدليل.")
    refresh_computed_dependencies(result)
    return result
