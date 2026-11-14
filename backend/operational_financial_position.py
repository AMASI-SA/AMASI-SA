"""Compose the unchanged financial projection with evidenced noncash linkage."""
from operational_financial_activity_runtime import preparation_checkpoint
from operational_financial_position_core import build_financial_position as project_balances
from operational_driver_review_report import link_review_destinations
from operational_balance_completeness import reason_for
from operational_financial_position_views import DETAIL_VIEWS, FORECAST_VIEWS, TABLES, scope_financial_position


def build_financial_position(state, *, cost_exposure=None, generated_at=None, view="all"):
    preparation_checkpoint()
    result = project_balances(state, cost_exposure=cost_exposure, generated_at=generated_at,
        include_forecast=view in FORECAST_VIEWS,
        include_delivery_details=view in DETAIL_VIEWS or bool(state.get("driver_payment_review_facts")),
        detail_tables=None if view == "all" else TABLES[view])
    for currency, data in result["currencies"].items():
        preparation_checkpoint()
        if currency != "SAR":
            continue
        # Source candidates have their own nonposting table, never part of debt, cash or COD.
        data["driver_fee_review"] = [{**row["driver_fee_review"], "reason": row["code"],
                                      "required_action": row["required_action"]}
            for row in state.get("engine", {}).get("issues", []) if row.get("driver_fee_review")
            and view in {"all", "drivers"}]
        # An undated shipment may precede startup; never assign an amount/date.
        # Group these potentially historical warnings to avoid giant order tables.
        groups = {}
        pending_by_group = {}
        candidates = {}
        for issue in state.get("engine", {}).get("issues", []):
            preparation_checkpoint()
            cand = issue.get("courier_fee_review") or {}
            if view not in {"all", "couriers"} or not cand or cand.get("currency") not in (None, "SAR"):
                continue
            n = str(cand["order_number"])
            candidates.setdefault(n, {"order_number": n, "name": cand["party_name"]})
            k = (cand["party_name"], issue["code"])
            group = groups.setdefault(k, {"name": cand["party_name"], "orders": 0, "examples": [],
                "required_action": reason_for(issue)["message"], "pending_order_details": []})
            pending_by_number = pending_by_group.setdefault(k, {})
            existing = pending_by_number.get(n)
            reason = reason_for(issue)
            if existing is None:
                existing = {"order_id": str(cand.get("order_id") or issue.get("source_id") or n),
                    "order_number": n, "reasons": [reason]}
                pending_by_number[n] = existing
                group["pending_order_details"].append(existing)
                group["orders"] += 1
            elif reason not in existing["reasons"]:
                existing["reasons"].append(reason)
            if len(group["examples"]) < 3 and cand["order_number"] not in group["examples"]:
                group["examples"].append(cand["order_number"])
        for row in data["driver_fee_review"]:
            preparation_checkpoint()
            n = str(row["order_number"])
            candidates.setdefault(n, {"order_number": n, "name": row.get("driver_id") or "مندوب المتجر"})
        data["shipping_fee_review_orders"] = sorted(candidates.values(), key=lambda r: r["order_number"])
        if groups:
            data["tables"].append({"id": "courier_fee_review", "title": "شحنات شركات التوصيل قيد المطابقة",
                "section": "fulfillment", "kind": "review", "complete": False, "totals": {},
                "columns": [{"key":k,"label":v,"type":t} for k,v,t in [
                    ("name","شركة الشحن","text"), ("orders","عدد الطلبات","exceptions"), ("samples","عينة طلبات","text"),
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
            data["completeness"]["actual"] = False
            data["completeness"]["forecast"] = False
            reason = "غير متاح: اعتماد التحصيل لا يطابق الأثر المسجل؛ يلزم مراجعة الدليل."
            for key in ("monetary_position", "displayed_position"):
                preparation_checkpoint()
                data["metrics"][key] = None
                for row in data["current_rows"]:
                    preparation_checkpoint()
                    if row["id"] == key:
                        row.update(amount=None, reason=reason)
            # The future components stay independently useful. Their sum with
            # an unverified current monetary position cannot be certified.
            data["metrics"]["forecast_monetary_position"] = None
            for row in data["forecast_rows"]:
                preparation_checkpoint()
                if row["id"] == "forecast_monetary_position":
                    row.update(amount=None, complete=False, reason=reason)
    preparation_checkpoint()
    return scope_financial_position(result, state, view)
