"""Pure operational calculations. No database, posting, or integration side effects.

The source adapter supplies owner-scoped MZ2 evidence. Persist the returned state
atomically with movements; never derive actual bank money from these estimates.
All amounts are decimal strings in the source currency. Confirmed evidence is
immutable and corrections require a new, explicitly approved evidence identity.
"""
from calendar import monthrange
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from hashlib import sha256
import json
from zoneinfo import ZoneInfo


RIYADH = timezone(timedelta(hours=3))
CENT = Decimal("0.01")
ZERO = Decimal("0")
DELIVERED = {"delivered", "تم التوصيل"}
CANCELLED = {"cancelled", "canceled", "ملغي", "ملغى"}


def money(value):
    if value is None or isinstance(value, bool):
        raise ValueError("amount_missing")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError):
        raise ValueError("amount_invalid") from None
    if not result.is_finite() or result < 0:
        raise ValueError("amount_invalid")
    return result


def amount(value):
    return format(value.quantize(CENT, rounding=ROUND_HALF_UP), ".2f")


def instant(value):
    result = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise ValueError("timezone_required")
    return result


def day(value):
    return date.fromisoformat(str(value)[:10])


def fingerprint(value):
    return sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()


def reconcile_credits(state):
    """Derive return/refund credits from paid excess; no second cash movement.

    A credit uses its own allocation identity, retaining the original invoice or
    capture and its payments. Re-reading source evidence cannot recreate debt.
    """
    obligations = state.setdefault("engine", {}).setdefault("obligations", {})
    used = {}
    for movement in state.get("movements", []):
        for allocation in movement.get("allocations", []):
            key = allocation["obligation_id"]
            used[key] = used.get(key, ZERO) + money(allocation["amount"])
    for key, obligation in list(obligations.items()):
        if obligation.get("derived_credit") or obligation.get("kind") not in {"supplier", "provider"}:
            continue
        credit_id = "credit:" + key
        credit = max(used.get(key, ZERO) - money(obligation.get("confirmed", "0")), ZERO)
        if credit or credit_id in obligations:
            obligations[credit_id] = {
                "id": credit_id, "kind": obligation["kind"], "party_id": obligation["party_id"],
                "party_type": obligation["party_type"], "currency": obligation["currency"],
                "expected": "0.00", "confirmed": amount(credit),
                "direction": "receivable" if obligation["direction"] == "payable" else "payable",
                "business_date": obligation["business_date"], "derived_credit": True,
                "original_obligation_id": key, "evidence_ids": obligation.get("evidence_ids", []),
                **({"name": obligation["name"]} if obligation.get("name") else {}),
            }
    return state


def provider_projection(payment, policies, transaction_date):
    """Expected receivable only. Actual settlement fees belong to the movement.

    Explicit return-fee terms are required whenever cancellation/refund occurs;
    historical contracts lacking these fields remain visibly incomplete.
    """
    gross, cancelled = money(payment["gross"]), money(payment.get("cancelled", "0"))
    refunds, seen = ZERO, {}
    for refund in payment.get("refunds", []):
        if refund.get("status") not in {"executed", "settled", "Refund Executed", "Settled"}:
            continue
        key = str(refund["id"])
        value = money(refund["amount"])
        if key in seen and seen[key] != value:
            raise ValueError("refund_identity_conflict")
        seen[key] = value
    refunds = sum(seen.values(), ZERO)
    if cancelled + refunds > gross:
        raise ValueError("cancellation_refund_overlap")
    matches = [p for p in policies if p.get("provider") == payment["provider"]
               and p.get("currency") == payment["currency"] and p.get("status") == "active"
               and day(p["effective_from"]) <= transaction_date
               and (not p.get("effective_to") or transaction_date <= day(p["effective_to"]))]
    if len(matches) != 1:
        raise ValueError("provider_fee_policy_incomplete_or_ambiguous")
    policy = matches[0]
    base = gross
    for value, field in ((cancelled, "cancellation_fee_treatment"), (refunds, "refund_fee_treatment")):
        if value:
            treatment = policy.get(field)
            if treatment not in {"retain", "recalculate"}:
                raise ValueError(field + "_incomplete")
            if treatment == "recalculate":
                base -= value
    fees = ZERO if base == 0 else base * money(policy["percentage"]) / 100 + money(policy["fixed_amount"])
    if base and policy.get("minimum") is not None:
        fees = max(fees, money(policy["minimum"]))
    if base and policy.get("maximum") is not None:
        fees = min(fees, money(policy["maximum"]))
    vat = policy.get("vat_treatment")
    if vat == "exclusive":
        fees *= 1 + money(policy.get("vat_rate")) / 100
    elif vat not in {"inclusive", "exempt", "not_applicable"}:
        raise ValueError("fee_tax_policy_incomplete")
    net = gross - cancelled - refunds
    # Fees exceeding the receivable are a payable; do not silently clamp debt.
    expected = net - Decimal(amount(fees))
    return {"gross": amount(gross), "cancelled": amount(cancelled), "refunded": amount(refunds),
            "net": amount(net), "estimated_fees": amount(fees), "expected_receivable": amount(expected),
            "policy_id": policy["id"]}


def reconcile(state, sources, as_of):
    """Return updated independent state; deterministic replay never duplicates money.

    See source adapter for normalized shapes. Missing evidence becomes an issue,
    never a zero. Previously confirmed facts survive cancellation and stale input.
    """
    result = deepcopy(state)
    if state.get("status") not in {"active", "ACTIVE"} or not state.get("started_at"):
        return result
    now, start = instant(as_of), instant(state["started_at"])
    if now < start:
        raise ValueError("as_of_before_start")
    engine = result.setdefault("engine", {})
    obligations = engine.setdefault("obligations", {})
    facts = engine.setdefault("facts", {})
    versions = engine.setdefault("order_versions", {})
    engine.setdefault("salary_days", {})
    engine.setdefault("ad_days", {})
    engine.setdefault("provider_reports", {})
    engine.setdefault("cod_reports", {})
    engine.setdefault("audit", [])
    issues = deepcopy(sources.get("issues", []))

    def issue(code, identity):
        entry = {"code": str(code), "source_id": str(identity)}
        if entry not in issues:
            issues.append(entry)

    def fact(key, payload):
        previous = facts.get(key)
        if previous is not None and previous != payload:
            raise ValueError("confirmed_evidence_changed")
        facts[key] = payload
        return payload

    def put(key, kind, party, party_type, currency, expected=ZERO, confirmed=ZERO,
            direction="payable", business_date=None, **metadata):
        existing_name = obligations.get(key, {}).get("name")
        obligations[key] = {"id": key, "kind": kind, "party_id": party, "party_type": party_type,
                            "currency": currency, "expected": amount(expected), "confirmed": amount(confirmed),
                            "direction": direction, "business_date": str(business_date or now.astimezone(RIYADH).date()),
                            **metadata}
        if existing_name:
            obligations[key]["name"] = existing_name

    for order in sources.get("orders", []):
        oid = str(order.get("id", ""))
        checkpoint = (deepcopy(obligations), deepcopy(facts), deepcopy(engine["provider_reports"]), deepcopy(engine["cod_reports"]))
        try:
            created = instant(order["created_at"])
            if not start <= created <= now:
                continue
            updated = instant(order.get("updated_at", order["created_at"]))
            if oid in versions and updated < instant(versions[oid]):
                continue
            currency = order["currency"]
            cancelled = order.get("status") in CANCELLED
            current_item_keys = {"supplier:" + oid + ":" + str(item["id"]) for item in order.get("items", [])}
            for old_key, old_row in obligations.items():
                if (old_key.startswith("supplier:" + oid + ":") and ":receipt:" not in old_key
                        and old_key not in current_item_keys):
                    old_row["expected"] = "0.00"
            for item in order.get("items", []):
                iid = str(item["id"])
                key = "supplier:" + oid + ":" + iid
                total = money(item["cost"]) * money(item.get("quantity", 1))
                receipts = [r for r in sources.get("supplier_receipts", [])
                            if str(r.get("order_id")) == oid and str(r.get("item_id")) == iid]
                for receipt in receipts:
                    accepted = instant(receipt["accepted_at"])
                    if accepted > now:
                        continue
                    gross = money(receipt.get("gross_amount", receipt["amount"]))
                    net = money(receipt.get("net_amount", receipt["amount"]))
                    tax = money(receipt.get("tax_amount", "0"))
                    if gross != net + tax or gross != money(receipt["amount"]):
                        raise ValueError("supplier_invoice_gross_conflict")
                    fact("receipt:" + str(receipt["id"]), {"order_id": oid, "item_id": iid,
                         "supplier_id": receipt["supplier_id"], "amount": amount(gross),
                         "net_amount": amount(net), "tax_amount": amount(tax), "gross_amount": amount(gross),
                         "tax_evidence": deepcopy(receipt.get("tax_evidence")),
                         "accepted_at": accepted.isoformat()})
                relevant = {k: v for k, v in facts.items() if k.startswith("receipt:")
                            and v["order_id"] == oid and v["item_id"] == iid}
                received = sum((money(v.get("net_amount", v["amount"])) for v in relevant.values()), ZERO)
                if received > total:
                    issue("supplier_receipt_exceeds_estimate", key)
                # Expected moves with the current assignment; confirmed is by actual receiver.
                put(key, "supplier", item.get("supplier_id"), "supplier", currency,
                    expected=ZERO if cancelled else max(total - received, ZERO), business_date=created.date())
                for rid, receipt in relevant.items():
                    returned = ZERO
                    for ret in sources.get("supplier_returns", []):
                        if str(ret.get("receipt_id")) != rid.removeprefix("receipt:") or ret.get("accepted") is not True:
                            continue
                        if instant(ret["accepted_at"]) > now:
                            continue
                        fact("return:" + str(ret["id"]), {"receipt_id": rid, "amount": amount(money(ret["amount"]))})
                    returned = sum((money(v["amount"]) for k, v in facts.items()
                                    if k.startswith("return:") and v["receipt_id"] == rid), ZERO)
                    if returned > money(receipt["amount"]):
                        raise ValueError("supplier_return_exceeds_receipt")
                    put(key + ":" + rid, "supplier", receipt["supplier_id"], "supplier", currency,
                        confirmed=money(receipt["amount"]) - returned, business_date=day(receipt["accepted_at"]), evidence_ids=[rid],
                        net_amount=receipt.get("net_amount", receipt["amount"]), tax_amount=receipt.get("tax_amount", "0.00"),
                        gross_amount=receipt.get("gross_amount", receipt["amount"]), returned_amount=amount(returned),
                        tax_evidence=deepcopy(receipt.get("tax_evidence")))
            carrier = order.get("carrier")
            service_key = "shipping:" + oid
            frozen = facts.get(service_key)
            if frozen is None and carrier and order.get("status") in DELIVERED:
                frozen = fact(service_key, {"id": carrier["id"], "cost": amount(money(carrier["cost"])) if carrier.get("cost") is not None else None,
                                           "party_type": carrier.get("party_type", carrier.get("kind", "courier")),
                                           "delivered_at": order.get("delivered_at", updated.isoformat()),
                                           "cod_amount": amount(money(order.get("cod_amount", "0"))),
                                           "cod_evidence": deepcopy(order.get("cod") or {}),
                                           "component_mode": bool(carrier.get("fee_components"))})
            cod_fact = facts.get("cod_collection:" + oid)
            cod_evidence = deepcopy(order.get("cod") or {})
            if frozen:
                original_evidence = frozen.get("cod_evidence", {})
                # Delivery freezes the executor, not an absent cash collection.
                candidate = original_evidence if original_evidence.get("evidence_complete", True) else cod_evidence
                candidate_amount = frozen["cod_amount"] if candidate is original_evidence else order.get("cod_amount", "0")
                executor_matches = carrier and carrier["id"] == frozen["id"]
                if cod_fact is None and candidate.get("evidence_complete", True) and (candidate is original_evidence or executor_matches):
                    cod_fact = fact("cod_collection:" + oid, {"amount": amount(money(candidate_amount)),
                        "evidence": deepcopy(candidate), "party_id": frozen["id"]})
                if cod_fact:
                    cod_evidence = deepcopy(cod_fact["evidence"])
            confirmed_cod = money(cod_fact["amount"]) if cod_fact else ZERO
            cod_metadata = {"gross": cod_evidence.get("gross"), "collected": cod_evidence.get("collected"),
                            "customer_outstanding": cod_evidence.get("outstanding"),
                            "custody_amount": cod_evidence.get("custody_amount"),
                            "collection_amount": cod_evidence.get("collection_amount"),
                            "payment_method": cod_evidence.get("payment_method"), "evidence_ids": cod_evidence.get("evidence_ids", [])}
            if cod_evidence:
                engine["cod_reports"]["cod:" + oid] = {**cod_metadata, "order_id": oid, "currency": currency,
                    "party_id": frozen["id"] if frozen else (carrier or {}).get("id"),
                    "confirmed_custody": amount(confirmed_cod)}
            if frozen:
                # Identity/COD can be proven while the fee contract is incomplete.
                # A later verified fee attaches to the already frozen actual executor.
                fee = facts.get("shipping_fee:" + oid)
                if fee is None and frozen.get("cost") is not None:
                    fee = {"cost": frozen["cost"]}
                if fee is None and carrier and carrier["id"] == frozen["id"] and carrier.get("cost") is not None:
                    fee = fact("shipping_fee:" + oid, {"cost": amount(money(carrier["cost"]))})
                components = carrier.get("fee_components", []) if carrier and carrier["id"] == frozen["id"] else []
                # Preserve an already confirmed aggregate from older snapshots;
                # introducing component rows must not duplicate that liability.
                if not frozen.get("component_mode") and money(obligations.get(service_key, {}).get("confirmed", "0")):
                    components = []
                if components and not facts.get("shipping_components:" + oid):
                    fact("shipping_components:" + oid, {"enabled": True})
                component_mode = frozen.get("component_mode") or bool(facts.get("shipping_components:" + oid))
                if component_mode:
                    if service_key in obligations:
                        obligations[service_key]["expected"] = "0.00"
                    for component in components:
                        component_key = service_key + ":" + component["id"]
                        component_fact = facts.get(component_key)
                        if component_fact is None and component.get("complete") and component.get("amount") is not None:
                            component_fact = fact(component_key, {"cost": amount(money(component["amount"]))})
                        if component_fact is not None:
                            put(component_key, "shipping", frozen["id"], frozen["party_type"], currency,
                                confirmed=money(component_fact["cost"]), business_date=day(frozen["delivered_at"]), fee_component=component["id"])
                        elif component_key in obligations:
                            obligations[component_key]["expected"] = "0.00"
                elif fee is not None:
                    put(service_key, "shipping", frozen["id"], frozen["party_type"], currency,
                        confirmed=money(fee["cost"]), business_date=day(frozen["delivered_at"]))
                elif service_key in obligations:
                    obligations[service_key]["expected"] = "0.00"
                if confirmed_cod or cod_evidence or "cod:" + oid in obligations:
                    put("cod:" + oid, "cod", frozen["id"], frozen["party_type"], currency,
                        confirmed=confirmed_cod, direction="receivable", business_date=day(frozen["delivered_at"]), **cod_metadata)
            elif carrier:
                if carrier.get("fee_components"):
                    if service_key in obligations:
                        obligations[service_key]["expected"] = "0.00"
                    for component in carrier["fee_components"]:
                        component_key = service_key + ":" + component["id"]
                        put(component_key, "shipping", carrier["id"], carrier.get("party_type", carrier.get("kind", "courier")), currency,
                            expected=ZERO if cancelled or not component.get("complete") else money(component["amount"]),
                            business_date=created.date(), fee_component=component["id"])
                elif carrier.get("cost") is not None:
                    put(service_key, "shipping", carrier["id"], carrier.get("party_type", carrier.get("kind", "courier")), currency,
                        expected=ZERO if cancelled else money(carrier["cost"]), business_date=created.date())
                elif service_key in obligations:
                    obligations[service_key]["expected"] = "0.00"
                put("cod:" + oid, "cod", carrier["id"], carrier.get("party_type", carrier.get("kind", "courier")), currency,
                    expected=ZERO if cancelled else money(order.get("cod_amount", "0")), direction="receivable", business_date=created.date(), **cod_metadata)
            else:
                for key, obligation in obligations.items():
                    if key == service_key or key.startswith(service_key + ":") or key == "cod:" + oid:
                        obligation["expected"] = "0.00"
            if order.get("payment"):
                payment = dict(order["payment"], currency=currency)
                provider_key = "provider:" + oid
                provider_facts = deepcopy(facts)
                try:
                    current_refunds = [r for r in payment.get("refunds", [])
                                       if r.get("status") in {"executed", "settled", "Refund Executed", "Settled"}]
                    for fk, fv in facts.items():
                        prefix = "refund:" + payment["provider"] + ":"
                        if fk.startswith(prefix) and fv["order_id"] == oid:
                            rid = fk[len(prefix):]
                            if not any(str(r["id"]) == rid for r in current_refunds):
                                current_refunds.append({"id": rid, "amount": fv["amount"], "status": "executed"})
                    payment["refunds"] = current_refunds
                    projection = provider_projection(payment, sources.get("fee_policies", []), created.astimezone(RIYADH).date())
                    projected = Decimal(projection["expected_receivable"])
                    for capture in payment.get("captures", []):
                        if instant(capture["captured_at"]) > now:
                            continue
                        fact("capture:" + str(capture["id"]), {"order_id": oid, "provider": payment["provider"],
                             "amount": amount(money(capture["amount"]))})
                    captured = sum((money(v["amount"]) for k, v in facts.items()
                                    if k.startswith("capture:") and v["order_id"] == oid), ZERO)
                    for refund in payment.get("refunds", []):
                        if refund.get("status") in {"executed", "settled", "Refund Executed", "Settled"}:
                            fact("refund:" + payment["provider"] + ":" + str(refund["id"]),
                                 {"order_id": oid, "amount": amount(money(refund["amount"]))})
                    refunded = sum((money(v["amount"]) for k, v in facts.items()
                                    if k.startswith("refund:") and v["order_id"] == oid), ZERO)
                    if captured > money(payment["gross"]) or (captured and refunded > captured):
                        raise ValueError("provider_capture_amount_conflict")
                    confirmed = max(captured - refunded, ZERO)
                    projection["confirmed_receivable"] = amount(confirmed)
                    engine["provider_reports"][provider_key] = projection
                    if not captured:
                        issue("provider_capture_evidence_missing", provider_key)
                    put(provider_key, "provider", payment["provider"], "provider", currency,
                        expected=max(projected - confirmed, ZERO) if projected >= 0 else abs(projected),
                        confirmed=confirmed, direction="receivable" if projected >= 0 or confirmed else "payable",
                        business_date=created.date(), projection=projection)
                except (ValueError, KeyError) as error:
                    facts.clear()
                    facts.update(provider_facts)
                    if provider_key in obligations:
                        obligations[provider_key]["expected"] = "0.00"
                        obligations[provider_key]["incomplete"] = True
                    issue(str(error), provider_key)
            versions[oid] = updated.isoformat()
        except (ValueError, KeyError, TypeError, InvalidOperation) as error:
            obligations.clear()
            obligations.update(checkpoint[0])
            facts.clear()
            facts.update(checkpoint[1])
            engine["provider_reports"] = checkpoint[2]
            engine["cod_reports"] = checkpoint[3]
            issue(str(error), oid)

    first_salary_day = start.astimezone(RIYADH).date() + timedelta(days=1)
    today = now.astimezone(RIYADH).date()
    for employee in sources.get("employees", []):
        eid = str(employee.get("id", ""))
        try:
            revisions = sorted(employee["salary_revisions"], key=lambda r: day(r["effective_from"]))
            if len({day(r["effective_from"]) for r in revisions}) != len(revisions):
                raise ValueError("salary_revision_ambiguous")
            current = first_salary_day
            while current <= today:
                key = "salary:" + eid + ":" + str(current)
                eligibility_start = max([first_salary_day] + [day(employee[f]) for f in ("hire_date", "accrual_start_date") if employee.get(f)])
                if current < eligibility_start:
                    current += timedelta(days=1)
                    continue
                if employee.get("effective_to") and current > day(employee["effective_to"]):
                    break
                if key not in engine["salary_days"]:
                    active = [r for r in revisions if day(r["effective_from"]) <= current
                              and (not r.get("effective_to") or current <= day(r["effective_to"]))]
                    if not active:
                        issue("salary_contract_missing", key)
                    else:
                        revision = active[-1]
                        status = revision.get("status")
                        if status not in {"active", "unpaid_leave", "inactive"}:
                            raise ValueError("salary_status_missing")
                        salary = money(revision["salary"]) if status == "active" else ZERO
                        for period in employee.get("payroll_suspension_periods", []):
                            period_start = period.get("started_on", period.get("start_date"))
                            period_end = period.get("returned_on", period.get("end_date"))
                            ends_after = not period_end or (current < day(period_end) if "returned_on" in period else current <= day(period_end))
                            if day(period_start) <= current and ends_after:
                                salary = ZERO
                        days = Decimal(monthrange(current.year, current.month)[1])
                        # Difference of rounded calendar cumulative entitlement conserves a full month's salary.
                        earned = Decimal(amount(salary * current.day / days)) - Decimal(amount(salary * (current.day - 1) / days))
                        engine["salary_days"][key] = {"amount": amount(earned), "revision": deepcopy(revision)}
                        put(key, "salary", eid, "employee", employee["currency"], confirmed=earned, business_date=current)
                current += timedelta(days=1)
        except (ValueError, KeyError, TypeError, InvalidOperation) as error:
            issue(str(error), eid)

    for snapshot in sorted(sources.get("ad_snapshots", sources.get("advertising", [])), key=lambda r: str(r.get("observed_at", ""))):
        key = "advertising:" + str(snapshot.get("account_id")) + ":" + str(snapshot.get("date"))
        try:
            observed = instant(snapshot["observed_at"])
            ad_date = day(snapshot["date"])
            zone = ZoneInfo(snapshot.get("timezone") or state.get("timezone") or "Asia/Riyadh")
            day_start = datetime.combine(ad_date, datetime.min.time(), zone)
            day_end = datetime.combine(ad_date + timedelta(days=1), datetime.min.time(), zone)
            if observed > now or day_end <= start:
                continue
            # The partial first day cannot be separated from the baseline without explicit coverage evidence.
            if day_start < start < day_end and not snapshot.get("covers_since_start"):
                issue("advertising_start_day_coverage_required", key)
                continue
            value = money(snapshot["amount"])
            prior = engine["ad_days"].get(key)
            if prior and observed < instant(prior["observed_at"]):
                continue
            closed = snapshot.get("closed") is True and snapshot.get("complete") is True
            if closed and (not snapshot.get("day_ended") or observed < day_end or now < day_end + timedelta(hours=2)):
                issue("advertising_close_not_due_or_evidence_incomplete", key)
                closed = False
            if prior and prior["closed"] and value != money(prior["amount"]):
                if not snapshot.get("correction_approved") or not snapshot.get("correction_id"):
                    raise ValueError("advertising_closed_day_correction_required")
                fact("ad_correction:" + str(snapshot["correction_id"]), {"day": key, "amount": amount(value)})
                closed = True
            elif prior and prior["closed"]:
                closed = True
            funding = snapshot["funding_type"]
            if funding not in {"prepaid", "postpaid", "hybrid"}:
                raise ValueError("advertising_funding_incomplete")
            fraction = money(snapshot.get("wallet_fraction")) if funding == "hybrid" else (Decimal(1) if funding == "prepaid" else ZERO)
            if fraction > 1:
                raise ValueError("advertising_hybrid_policy_incomplete")
            if prior and prior["closed"] and (prior.get("funding_type", funding) != funding
                                             or Decimal(prior.get("wallet_fraction", str(fraction))) != fraction
                                             or prior.get("timezone", str(zone)) != str(zone)):
                raise ValueError("advertising_closed_funding_changed")
            engine["ad_days"][key] = {"amount": amount(value), "closed": closed, "observed_at": observed.isoformat(),
                                      "funding_type": funding, "wallet_fraction": str(fraction), "timezone": str(zone)}
            metadata = {field: snapshot.get(field) for field in ("fx_rate", "fx_source", "fx_at", "fx_date", "fx_snapshot_id", "fx_evidence", "sar_amount",
                        "source_cumulative_amount", "baseline_amount", "hybrid_policy_id", "hybrid_policy_version", "hybrid_policy_evidence",
                        "hybrid_confirmed_at", "hybrid_confirmed_by")}
            parts = [(key, value, funding)]
            if funding == "hybrid":
                wallet = Decimal(amount(value * fraction))
                parts = [(key + ":wallet", wallet, "prepaid"), (key + ":credit", value-wallet, "postpaid")]
            active_parts = {part_key for part_key, _, _ in parts}
            for old_key in (key, key + ":wallet", key + ":credit"):
                if old_key in obligations and old_key not in active_parts and money(obligations[old_key]["confirmed"]) == ZERO:
                    obligations[old_key].update(expected="0.00", superseded=True)
            for part_key, part_value, part_funding in parts:
                part_metadata = dict(metadata)
                if funding == "hybrid" and metadata.get("sar_amount") is not None:
                    wallet_sar = Decimal(amount(money(metadata["sar_amount"]) * fraction))
                    part_metadata["sar_amount"] = amount(wallet_sar if part_funding == "prepaid" else money(metadata["sar_amount"])-wallet_sar)
                put(part_key, "advertising", snapshot["account_id"], "ad_account", snapshot["currency"],
                    expected=ZERO if closed else part_value, confirmed=part_value if closed else ZERO,
                    business_date=ad_date, funding_type=part_funding, funding_mode=funding,
                    wallet_fraction=str(fraction), ad_day_id=key, ad_total_amount=amount(value),
                    original_currency=snapshot["currency"], timezone=str(zone), **part_metadata)
        except (ValueError, KeyError, TypeError, InvalidOperation) as error:
            issue(str(error), key)

    for recurring in sources.get("recurring", sources.get("recurring_obligations", [])):
        key = "recurring:" + str(recurring.get("id"))
        try:
            raw_due = recurring["due_at"]
            due = datetime.combine(day(raw_due), datetime.min.time(), RIYADH) if len(str(raw_due)) == 10 else instant(raw_due)
            if due > now or due.astimezone(RIYADH).date() < start.astimezone(RIYADH).date():
                continue
            value = money(recurring["amount"])
            paid_confirmed = sum((money(fact["amount"]) for fact_key, fact in facts.items()
                                  if fact_key.startswith("recurring_payment:") and fact["obligation_id"] == key), ZERO)
            put(key, "recurring", recurring["party_id"], recurring.get("party_type", "external_person"), recurring["currency"],
                expected=max(value-paid_confirmed, ZERO), confirmed=paid_confirmed,
                business_date=due.date(), source_context=deepcopy(recurring.get("source_context", {})),
                source_invoice_id=recurring.get("source_invoice_id", recurring.get("invoice_id")), period_start=recurring.get("period_start"), period_end=recurring.get("period_end"))
        except (ValueError, KeyError, TypeError, InvalidOperation) as error:
            issue(str(error), key)

    engine["issues"] = issues
    reconcile_credits(result)
    before = state.get("engine", {}).get("obligations", {})
    for key, row in obligations.items():
        if before.get(key) != row:
            event = {"action": "operational_reconciled", "obligation_id": key,
                     "actor_id": "operational_worker", "source": "mz2",
                     "reason": "مطابقة الدليل التشغيلي المعتمد",
                     "before": before.get(key), "after": deepcopy(row), "at": now.isoformat()}
            event["id"] = fingerprint({**event, "sequence": len(engine["audit"])})
            engine["audit"].append(event)
    return result
