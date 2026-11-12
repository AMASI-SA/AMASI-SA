"""Evidence coverage per table and counterparty; never changes ledger amounts.

An exception belongs to its evidenced counterparty. Unassigned exceptions stay
visible at table level; a name alone never assigns a financial identity. Counts
are distinct order IDs, not issue rows or fee components.
"""
from collections import defaultdict
from decimal import Decimal, InvalidOperation


TABLE_KINDS = {
    "banks_network": {"bank", "pos_receivable"}, "cash": {"cash"},
    "suppliers": {"supplier"}, "employees": {"employee"},
    "employee_custody": {"employee_custody"}, "external": {"external_person"},
    "couriers": {"courier"}, "drivers": {"store_driver"},
    "providers": {"provider"},
    "ads": {"ad_account"}, "ads_currency_conversion": {"ad_account"},
    "other_balances": {"operating_expense", "owner_withdrawal"},
    "balance_origin": {"provider", "courier", "store_driver"},
}
ORDER_PREFIXES = ("shipping-prepaid:", "shipping-completed:", "shipping:",
                  "delivery_event:", "completed_shipping:", "cod_collection:", "cod:", "provider:")
REASONS = {
    "operational_delivery_timestamp_missing": "تاريخ التسليم المؤكد غير متوفر.",
    "operational_delivery_order_waybill_unproven": "البوليصة الحالية لا تطابق دليل التسليم.",
    "operational_delivery_courier_identity_ambiguous": "هوية شركة الشحن غير محسومة.",
    "shipping_identity_or_rate_incomplete": "هوية جهة التوصيل أو تعرفة الأجرة غير مكتملة.",
    "operational_shipping_completion_history_review_required": "أجرة تنفيذ سابقة تحتاج مطابقة الرصيد الافتتاحي والسداد قبل اعتمادها.",
    "courier_cod_collection_proof_required": "تحصيل شركة الشحن من العميل يحتاج مطابقة كشف الشركة.",
    "driver_fee_evidence_conflict": "بيانات إسناد التوصيل أو الأجرة متعارضة.",
    "canonical_salla_source_missing": "بيانات طلب سلة الأساسية غير متوفرة.",
    "canonical_salla_refresh_required": "بيانات الطلب تحتاج تحديثًا موثقًا من سلة.",
}
GLOBAL_FAILURES = {"operational_refresh_failed", "operational_source_setup_incomplete", "operational_source_timeout",
    "operational_source_read_timeout", "operational_source_read_invalid", "operational_capacity_requires_archive",
    "operational_audit_archive_invalid"}


def order_identity(row):
    if row.get("order_id"):
        return str(row["order_id"])
    value = str(row.get("source_id") or row.get("id") or "")
    prefix = next((prefix for prefix in ORDER_PREFIXES if value.startswith(prefix)), None)
    if prefix:
        value = value[len(prefix):]
        for component in ("base_shipping", "cod_commission", "cod_fixed_commission", "cod_percentage_commission", "shipping_fee"):
            value = value.removesuffix(":" + component)
        return value or None
    return None


def reason_for(issue):
    code = str(issue.get("code") or "source_incomplete")
    message = issue.get("required_action") or issue.get("message") or REASONS.get(code)
    if not message:
        if "timestamp" in code or "date" in code:
            message = "تاريخ الحدث غير مثبت أو متعارض؛ يلزم مراجعة المصدر."
        elif "fee" in code or "tariff" in code:
            message = "أجرة الطلب أو تعرفة احتسابها تحتاج مطابقة."
        elif "identity" in code or "owner" in code:
            message = "هوية الطلب أو الجهة لا تطابق المصدر المعتمد."
        else:
            message = "بيانات الطلب غير مكتملة؛ يلزم مراجعة الدليل المشار إليه."
    return {"code": code, "message": str(message)}


def _families(issue, obligation=None):
    if obligation:
        return {obligation.get("party_type")}
    kind = issue.get("party_type")
    if kind:
        return {kind}
    code, source = str(issue.get("code") or ""), str(issue.get("source_id") or "")
    if issue.get("courier_fee_review") or issue.get("completed_shipping_review"):
        return {"courier"}
    if issue.get("driver_fee_review"):
        return {"store_driver"}
    if issue.get("scope") == "provider_payment" or issue.get("provider") or source.startswith("provider:") or "provider_" in code:
        return {"provider"}
    if "salary" in code or "employee_" in code or source.startswith("salary:"):
        return {"employee"}
    if "supplier" in code or "invoice" in code or issue.get("scope") == "received_products":
        return {"supplier"}
    if "advertising" in code or "ad_" in code or source.startswith("ad:"):
        return {"ad_account"}
    if "driver_" in code:
        return {"store_driver"}
    if "courier_" in code or "prepaid_shipping" in code or "completed_shipping" in code:
        return {"courier"}
    if "shipping" in code or "delivery_" in code or issue.get("component") in {"shipping_fee", "base_shipping", "cod_commission"}:
        return {"courier", "store_driver"}
    if "bank_" in code or "noncash" in code:
        return {"bank", "pos_receivable"}
    if code in GLOBAL_FAILURES:
        return set().union(*TABLE_KINDS.values())
    return set()


def _positive(value):
    try:
        return Decimal(str(value)) > 0
    except (TypeError, ValueError, InvalidOperation):
        return False


def _add_order(target, identity, number, reason):
    if not identity:
        return
    item = target.setdefault(str(identity), {"order_id": str(identity), "order_number": number, "reasons": []})
    if number and not item["order_number"]:
        item["order_number"] = str(number)
    if reason not in item["reasons"]:
        item["reasons"].append(reason)


def apply_table_coverage(tables, parties, obligations, issues, facts, *, currency, as_of):
    """Annotate a report already filtered by owner and currency, in linear passes."""
    by_id = {str(row["id"]): row for row in obligations}
    contexts, counted = defaultdict(set), defaultdict(set)
    names = {(row["party_type"], str(row["party_id"])): row.get("name") for row in parties}
    for row in obligations:
        identity = order_identity(row)
        if identity and row.get("party_type") in {"courier", "store_driver", "provider"}:
            context = (row["party_type"], str(row["party_id"]))
            contexts[identity].add(context)
            fee_proof = facts.get("shipping_fee:" + identity) or facts.get("shipping:" + identity) or {}
            if row.get("fee_component"):
                fee_proof = facts.get("shipping_fee:" + identity + ":" + str(row["fee_component"])) or {}
            proved_zero = (row.get("confirmed") in {"0", "0.00"}
                           and fee_proof.get("cost") in {"0", "0.00"}
                           and (fee_proof.get("currency") or fee_proof.get("fee_currency")) == currency)
            if row.get("kind") == "shipping" and (_positive(row.get("confirmed")) or proved_zero):
                counted[context].add(identity)
    for key, fact in facts.items():
        if not isinstance(fact, dict):
            continue
        identity = str(fact.get("order_id") or key.split(":", 1)[-1])
        kind = fact.get("party_type")
        party = fact.get("party_id") or (fact.get("id") if key.startswith("shipping:") else None)
        if kind in {"courier", "store_driver"} and party:
            contexts[identity].add((kind, str(party)))

    pending, unassigned, relevant = defaultdict(dict), defaultdict(dict), defaultdict(list)
    party_warnings = set()
    failed_snapshot = False
    for issue in issues:
        if issue.get("currency") not in (None, currency) or issue.get("severity") in {"info", "informational"}:
            continue
        failed_snapshot = failed_snapshot or issue.get("code") in GLOBAL_FAILURES
        obligation = by_id.get(str(issue.get("source_id") or ""))
        candidate = next((issue[key] for key in ("courier_fee_review", "driver_fee_review", "completed_shipping_review")
                          if isinstance(issue.get(key), dict)), {})
        if candidate.get("currency") not in (None, currency):
            continue
        identity = str(candidate.get("order_id") or issue.get("order_id") or "") or order_identity(obligation or issue)
        if not identity and (candidate or issue.get("component") in {"shipping_fee", "prepaid_shipping", "customer_collection"}
                or str(issue.get("code") or "").startswith(("operational_delivery_", "shipping_", "driver_fee_", "courier_fee_"))):
            identity = str(issue.get("source_id") or "") or None
        families = _families(issue, obligation)
        kind = candidate.get("party_type") or ("store_driver" if candidate.get("driver_id") else None)
        party = candidate.get("party_id") or candidate.get("driver_id") or issue.get("party_id")
        if party and not kind and len(families) == 1:
            kind = next(iter(families))
        target = (kind, str(party)) if kind and party else None
        if not target and obligation:
            target = (obligation["party_type"], str(obligation["party_id"]))
        if not target and identity and len(contexts[identity]) == 1:
            target = next(iter(contexts[identity]))
        if target:
            families = {target[0]}
            party_warnings.add(target)
        for family in families:
            relevant[family].append(issue)
        number = candidate.get("order_number") or issue.get("order_number")
        if target and identity:
            _add_order(pending[target], identity, number, reason_for(issue))
        elif identity:
            for family in families:
                _add_order(unassigned[family], identity, number, reason_for(issue))

    for table in tables:
        kinds = TABLE_KINDS.get(table["id"])
        if not kinds:
            continue
        scoped = [issue for kind in kinds for issue in relevant[kind]]
        table["complete"] = bool(as_of) and not failed_snapshot and not scoped
        table["coverage_basis"] = "saved_operational_snapshot"
        if table["id"] == "providers":
            for row in table["rows"]:
                row["complete"] = bool(as_of) and not failed_snapshot and row.get("complete") is True
            table["complete"] = table["complete"] and all(row["complete"] for row in table["rows"])
        if table["id"] not in {"couriers", "drivers"}:
            continue
        kind = next(iter(kinds))
        rows = table["rows"]
        by_party = {str(row["id"]).removeprefix(kind + ":").removesuffix(":" + currency): row for row in rows}
        for (party_kind, party), orders in pending.items():
            if party_kind != kind or party in by_party:
                continue
            # An exception with no saved balance is unknown, never a zero debt.
            row = {"id": f"{kind}:{party}:{currency}", "name": names.get((kind, party)) or party,
                   "receivable": None, "payable": None}
            rows.append(row)
            by_party[party] = row
        all_pending = set()
        for party, row in by_party.items():
            context = (kind, party)
            details = sorted(pending[context].values(), key=lambda value: (str(value.get("order_number") or ""), value["order_id"]))
            row.update(confirmed_orders=len(counted[context]), pending_orders=len(details), pending_order_details=details,
                       complete=bool(as_of) and not failed_snapshot and context not in party_warnings and not details and not unassigned[kind], balance_basis="recorded")
            all_pending.update(item["order_id"] for item in details)
        table["unassigned_order_details"] = sorted(unassigned[kind].values(), key=lambda value: value["order_id"])
        table["columns"] += [{"key": "confirmed_orders", "label": "تواصيل محتسبة", "type": "count"},
                              {"key": "pending_orders", "label": "طلبات غير مؤكدة", "type": "exceptions"}]
        table["totals"].update(confirmed_orders=len(set().union(*(counted[(kind, party)] for party in by_party))) if by_party else 0,
                               pending_orders=len(all_pending))
        table["notes"].append("الأرصدة الظاهرة هي المبالغ المحتسبة. الطلبات غير المؤكدة لا تلغي الأرصدة المثبتة؛ اضغط عددها لعرض رقم كل طلب وسبب المراجعة.")
        if unassigned[kind]:
            table["notes"].append("توجد طلبات لم تثبت جهة توصيلها؛ تظهر منفصلة للمراجعة ولا تنسب إلى شركة أو مندوب بالاسم فقط.")
    return tables


def annotate_report_coverage(projection):
    """Add display coverage to a freshly projected report, leaving ledger fields intact."""
    parties = projection.get("parties", [])
    details = projection.get("details", {})
    currencies = {row["currency"] for row in parties}
    currencies.update(details.get("provider_balances", {}))
    currencies.update(issue["currency"] for issue in projection.get("issues", []) if issue.get("currency"))
    projection["unassigned_order_details"] = {}
    for currency in currencies:
        scoped = [row for row in parties if row["currency"] == currency]
        obligations = [row for row in projection.get("obligations", []) if row.get("currency") == currency]
        tables = [{"id": identity, "rows": [{"id": f"{kind}:{row['party_id']}:{currency}",
                    "name": row.get("name"), "receivable": row["outstanding_receivable"],
                    "payable": row["outstanding_payable"]} for row in scoped if row["party_type"] == kind],
                   "columns": [], "totals": {}, "notes": []}
                  for kind, identity in (("courier", "couriers"), ("store_driver", "drivers"))]
        apply_table_coverage(tables, scoped, obligations, projection.get("issues", []), details.get("facts", {}),
                             currency=currency, as_of=projection.get("as_of"))
        existing = {(row["party_type"], str(row["party_id"])): row for row in scoped}
        for kind, table in zip(("courier", "store_driver"), tables):
            for covered in table["rows"]:
                identity = covered["id"].removeprefix(kind + ":").removesuffix(":" + currency)
                row = existing.get((kind, identity))
                if row is None:
                    row = {"party_type": kind, "party_id": identity, "name": covered["name"], "currency": currency,
                           "outstanding_receivable": None, "outstanding_payable": None}
                    parties.append(row)
                for key in ("confirmed_orders", "pending_orders", "pending_order_details", "complete", "balance_basis"):
                    row[key] = covered[key]
            projection["unassigned_order_details"].setdefault(currency, {})[kind] = table["unassigned_order_details"]
        providers = {row["provider"]: row for row in details.get("provider_balances", {}).get(currency, [])}
        failed_snapshot = any(issue.get("code") in GLOBAL_FAILURES and issue.get("currency") in {None, currency}
                              for issue in projection.get("issues", []))
        for identity, provider in providers.items():
            row = existing.get(("provider", identity))
            if row is None:
                row = {"party_type": "provider", "party_id": identity, "name": provider.get("name") or identity,
                       "currency": currency, "outstanding_receivable": None, "outstanding_payable": None}
                parties.append(row)
            for key in ("confirmed_orders", "paid_orders", "paid_orders_complete", "pending_orders", "pending_order_details", "complete"):
                row[key] = provider.get(key)
            row["complete"] = bool(projection.get("as_of")) and not failed_snapshot and row.get("complete") is True
            known = provider.get("known_net", provider.get("net"))
            value = Decimal(known) if known is not None else None
            row["known_outstanding_receivable"] = format(max(value, Decimal(0)), ".2f") if value is not None else None
            row["known_outstanding_payable"] = format(max(-value, Decimal(0)), ".2f") if value is not None else None
    known_summaries = {currency: dict(summary) for currency, summary in projection.get("summaries", {}).items()}
    for currency in currencies:
        known_summaries.setdefault(currency, {})
    for currency, summary in known_summaries.items():
        rows = [row for row in parties if row["currency"] == currency]
        totals = {}
        for side in ("receivable", "payable"):
            values = [row.get("known_outstanding_" + side, row.get("outstanding_" + side)) for row in rows]
            known = [Decimal(value) for value in values if value is not None]
            totals[side] = sum(known, Decimal(0)) if known or not values else None
            summary[side] = format(totals[side], ".2f") if totals[side] is not None else None
        known = [value for value in totals.values() if value is not None]
        summary["outstanding"] = format(sum(known, Decimal(0)), ".2f") if known else None
    projection["known_summaries"] = known_summaries
    return projection
