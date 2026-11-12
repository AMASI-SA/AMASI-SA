"""Project only the existing, nonoverlapping future-work tables; never post money."""
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

COMPONENTS = {
    "cod_expected": 1,
    "provider_collections_expected": 1,
    "products_unreceived": -1,
    "products_received_uninvoiced": -1,
    "shipping_expected": -1,
}
SCOPE = "البنود المتوقعة المعروضة فقط؛ المصروفات والرواتب والإعلانات والضرائب المستقبلية خارج هذه المجموعة."


def number(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        value = Decimal(str(value))
        return value if value.is_finite() else None
    except (ValueError, InvalidOperation, TypeError):
        return None


def money(value):
    return None if value is None else format(value.quantize(Decimal(".01"), rounding=ROUND_HALF_UP), ".2f")


def cod_forecast_coverage(exposure, started_at, as_of, issues=()):
    """Only explicit coverage of both COD source cohorts can establish a full total."""
    from operational_payment_evidence import _instant

    reasons, withheld = [], 0
    if not isinstance(exposure, dict) or exposure.get("status") != "preview":
        return {"complete": False, "withheld_candidates": None, "reason_codes": ["cod_forecast_source_unavailable"]}
    try:
        if (_instant(exposure["started_at"]) != _instant(started_at)
                or _instant(exposure["as_of"]) != _instant(as_of)
                or _instant(exposure["engine_as_of"]) != _instant(as_of)):
            reasons.append("cod_forecast_period_mismatch")
    except (ValueError, TypeError, KeyError):
        reasons.append("cod_forecast_period_mismatch")
    for name in ("prestart_open_cod", "open_customer_cod"):
        source = exposure.get(name)
        count = source.get("withheld_candidates") if isinstance(source, dict) else None
        if (not isinstance(source, dict) or not isinstance(source.get("by_currency"), dict)
                or type(count) is not int or count < 0):
            reasons.append(name + "_coverage_missing")
        else:
            withheld += count
            if count:
                reasons.append(name + "_candidates_withheld")
    for issue in [*issues, *(exposure.get("issues") or [])]:
        code = str(issue.get("code") or "")
        if (str(issue.get("source_id") or "").startswith("cod:")
                or issue.get("scope") in {"cod", "customer_collection", "driver_custody"}
                or code.startswith(("cod_", "operational_cod_", "open_customer_cod_"))
                or code in {"operational_source_scope_too_large", "operational_source_read_timeout",
                            "current_cost_snapshot_time_incomplete", "canonical_salla_source_missing_for_open_cost_preview"}):
            reasons.append(code or "cod_forecast_source_incomplete")
    return {"complete": not reasons, "withheld_candidates": withheld if not any(code.endswith("coverage_missing") for code in reasons) else None,
            "reason_codes": sorted(set(reasons))}


def project_forecast(tables, *, current_position, actual_complete):
    """A complete component can be zero; absent/partial sources never default to zero."""
    grouped = {key: [] for key in COMPONENTS}
    for table in tables:
        if table.get("id") in grouped:
            grouped[table["id"]].append(table)
    change = known = Decimal(0)
    any_known, complete, pending = False, True, []
    rows = []
    for identity, direction in COMPONENTS.items():
        candidates = grouped[identity]
        table = candidates[0] if len(candidates) == 1 else {}
        amount = number((table.get("totals") or {}).get("amount"))
        covered = len(candidates) == 1 and table.get("complete") is True and amount is not None
        values = [number(row.get("amount")) for row in table.get("rows", [])]
        subtotal = number((table.get("known_totals") or {}).get("amount"))
        if subtotal is None:
            subtotal = sum((value for value in values if value is not None), Decimal(0)) if any(value is not None for value in values) else None
        if covered:
            subtotal = amount
            change += direction * amount
        else:
            complete = False
            pending.append(identity)
        if subtotal is not None:
            known += direction * subtotal
            any_known = True
        rows.append({"id": identity, "label": table.get("title") or identity,
            "amount": money(amount) if covered else None, "known_subtotal": money(subtotal),
            "complete": covered, "emphasis": "positive" if direction > 0 else "negative",
            **({"reason": "تغطية هذا البند غير مكتملة؛ المجموع المعلوم يخص الصفوف المثبتة فقط."} if not covered else {})})
    current = number(current_position)
    projected = current + change if complete and actual_complete and current is not None else None
    rows.extend([
        {"id": "forecast_change", "label": "صافي تغير البنود المتوقعة المعروضة", "amount": money(change) if complete else None,
         "known_subtotal": money(known) if any_known else None, "complete": complete, "emphasis": "total", "reason": SCOPE},
        {"id": "forecast_monetary_position", "label": "المركز بعد البنود المتوقعة المعروضة", "amount": money(projected),
         "complete": projected is not None, "emphasis": "total", "reason": SCOPE},
    ])
    return {"rows": rows, "forecast_change": money(change) if complete else None,
        "known_forecast_change": money(known) if any_known else None,
        "forecast_monetary_position": money(projected), "complete": complete and projected is not None,
        "components_complete": complete, "pending_components": pending, "scope": "listed_order_work_only"}
