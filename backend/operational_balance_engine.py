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


from operational_provider_fees import provider_projection, independent_capture_without_fee_policy
from operational_provider_history import late_payment_blocks, proof_hash
from operational_split_payment import apply_split_payment, split_reference_owners
from operational_provider_transaction_evidence import VERSION as NATIVE_TRANSACTION_EVIDENCE_VERSION, CANONICAL_WITH_UNBOUND_ACTIVITY
from operational_product_cost_policy import unreceived_eligible
from operational_order_page_payment import corroborated_refund_basis, observed_balance


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
    if "customer_tender_coverage" in sources:
        engine["customer_tender_coverage"] = deepcopy(sources["customer_tender_coverage"])
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

    # Supplier invoice approval is independent from the customer's order
    # lifecycle. In particular, an old order (or unavailable current estimate)
    # must not suppress a newly issued, validated invoice.
    order_currencies = {str(order.get("id")): order.get("currency") for order in sources.get("orders", [])}
    receipt_metadata = {}
    for receipt in sources.get("supplier_receipts", []):
        rid = "receipt:" + str(receipt.get("id") or "")
        try:
            oid, iid = str(receipt.get("order_id") or ""), str(receipt.get("item_id") or "")
            if not receipt.get("id") or not oid or not iid or not receipt.get("supplier_id"):
                raise ValueError("supplier_receipt_identity_incomplete")
            accepted = instant(receipt["accepted_at"])
            if not start <= accepted <= now:
                continue
            key = "supplier:" + oid + ":" + iid + ":" + rid
            previous = obligations.get(key, {})
            # Older normalized callers supplied currency on the order. The
            # native adapter now always supplies the supplier invoice currency.
            currency = receipt.get("currency") or order_currencies.get(oid)
            if not currency:
                raise ValueError("supplier_invoice_currency_missing")
            if previous.get("currency") and previous["currency"] != currency:
                raise ValueError("supplier_invoice_currency_conflict")
            if (previous.get("source_invoice_id") and receipt.get("invoice_id")
                    and previous["source_invoice_id"] != receipt["invoice_id"]):
                raise ValueError("confirmed_evidence_changed")
            gross = money(receipt.get("gross_amount", receipt["amount"]))
            net = money(receipt.get("net_amount", receipt["amount"]))
            tax = money(receipt.get("tax_amount", "0"))
            if gross != net + tax or gross != money(receipt["amount"]):
                raise ValueError("supplier_invoice_gross_conflict")
            # Preserve the exact frozen payload used before this change. New
            # display provenance belongs on obligations, never on old facts.
            fact(rid, {"order_id": oid, "item_id": iid,
                 "supplier_id": receipt["supplier_id"], "amount": amount(gross),
                 "net_amount": amount(net), "tax_amount": amount(tax), "gross_amount": amount(gross),
                 "tax_evidence": deepcopy(receipt.get("tax_evidence")),
                 "accepted_at": accepted.isoformat()})
            receipt_metadata[rid] = {"currency": currency,
                "source_invoice_id": receipt.get("invoice_id"),
                "source_invoice_number": receipt.get("invoice_number"),
                "source_component_fully_invoiced": (receipt.get("component_fully_invoiced") is True
                                                    and bool(receipt.get("invoice_id")))}
        except (ValueError, KeyError, TypeError, InvalidOperation) as error:
            issue(str(error), rid)

    for rid, receipt in list(facts.items()):
        if not rid.startswith("receipt:"):
            continue
        key = "supplier:" + receipt["order_id"] + ":" + receipt["item_id"] + ":" + rid
        previous, metadata = obligations.get(key, {}), receipt_metadata.get(rid, {})
        currency = metadata.get("currency") or previous.get("currency") or order_currencies.get(receipt["order_id"])
        if not currency:
            issue("supplier_invoice_currency_missing", rid)
            continue
        new_returns = []
        try:
            for ret in sources.get("supplier_returns", []):
                if str(ret.get("receipt_id")) != rid.removeprefix("receipt:") or ret.get("accepted") is not True:
                    continue
                if instant(ret["accepted_at"]) > now:
                    continue
                return_key = "return:" + str(ret["id"])
                if return_key not in facts:
                    new_returns.append(return_key)
                fact(return_key, {"receipt_id": rid, "amount": amount(money(ret["amount"]))})
            returned = sum((money(v["amount"]) for k, v in facts.items()
                            if k.startswith("return:") and v["receipt_id"] == rid), ZERO)
            if returned > money(receipt["amount"]):
                raise ValueError("supplier_return_exceeds_receipt")
        except (ValueError, KeyError, TypeError, InvalidOperation) as error:
            for return_key in new_returns:
                facts.pop(return_key, None)
            issue(str(error), rid)
            returned = sum((money(v["amount"]) for k, v in facts.items()
                            if k.startswith("return:") and v["receipt_id"] == rid), ZERO)
        provenance = {name: metadata.get(name) or previous.get(name)
                      for name in ("source_invoice_id", "source_invoice_number", "source_component_fully_invoiced")
                      if metadata.get(name) or previous.get(name)}
        put(key, "supplier", receipt["supplier_id"], "supplier", currency,
            confirmed=money(receipt["amount"]) - returned,
            business_date=instant(receipt["accepted_at"]).astimezone(RIYADH).date(), evidence_ids=[rid],
            net_amount=receipt.get("net_amount", receipt["amount"]), tax_amount=receipt.get("tax_amount", "0.00"),
            gross_amount=receipt.get("gross_amount", receipt["amount"]), returned_amount=amount(returned),
            tax_evidence=deepcopy(receipt.get("tax_evidence")),
            **provenance, source_invoice_approved_at=receipt["accepted_at"])

    reference_owners = split_reference_owners(facts)
    order_inputs = [(order, False) for order in sources.get("orders", [])]
    order_inputs.extend((order, True) for order in sources.get("delivery_events", []))
    for order, delivery_only in order_inputs:
        oid = str(order.get("id", ""))
        # Order processing replaces touched entries. The four maps can therefore
        # roll back independently without copying every previously booked order.
        checkpoint = (obligations.copy(), facts.copy(), engine["provider_reports"].copy(), engine["cod_reports"].copy())
        try:
            created = instant(order["created_at"])
            if delivery_only or order.get("delivery_event"):
                event = order.get("delivery_event") or {}
                carrier = order.get("carrier") or {}
                delivered = instant(event.get("delivered_at"))
                if (not oid or event.get("schema") != "operational_delivery_event_v1"
                        or not event.get("id") or str(event.get("order_id")) != oid
                        or event.get("order_number") != order.get("order_number")
                        or instant(event.get("order_created_at")) != created
                        or not (start < delivered <= now and created <= delivered)
                        or (created > start if delivery_only else created < start)
                        or instant(order.get("delivered_at")) != delivered
                        or order.get("status") not in DELIVERED
                        or order.get("currency") != "SAR" or event.get("currency") != "SAR"
                        or event.get("party_type") not in {"courier", "store_driver"}
                        or not event.get("party_id") or event.get("party_id") != carrier.get("id")
                        or event.get("party_type") != carrier.get("party_type", carrier.get("kind"))):
                    raise ValueError("operational_delivery_event_contract_invalid")
                frozen_delivery = facts.get("shipping:" + oid)
                if frozen_delivery and (frozen_delivery.get("id") != event["party_id"]
                        or frozen_delivery.get("party_type") != event["party_type"]
                        or instant(frozen_delivery["delivered_at"]) != delivered):
                    raise ValueError("operational_delivery_confirmed_evidence_changed")
                # Separate provenance preserves the payload of existing shipping
                # facts and the obligation IDs used by recorded settlements.
                fact("delivery_event:" + oid, deepcopy(event))
            elif not start <= created <= now:
                continue
            updated = instant(order.get("updated_at", order["created_at"]))
            if oid in versions and updated < instant(versions[oid]):
                continue
            currency = order["currency"]
            cancelled = order.get("status") in CANCELLED
            if not delivery_only:
                current_item_keys = {"supplier:" + oid + ":" + str(item["id"]) for item in order.get("items", [])}
                for old_key, old_row in obligations.items():
                    if (old_key.startswith("supplier:" + oid + ":") and ":receipt:" not in old_key
                            and old_key not in current_item_keys and order.get("items_complete") is not False):
                        obligations[old_key] = {**old_row, "expected": "0.00"}
                for item in order.get("items", []):
                    iid = str(item["id"])
                    key = "supplier:" + oid + ":" + iid
                    total = money(item["cost"]) * money(item.get("quantity", 1))
                    relevant = {k: v for k, v in facts.items() if k.startswith("receipt:")
                                and v["order_id"] == oid and v["item_id"] == iid}
                    received = sum((money(v.get("net_amount", v["amount"])) for v in relevant.values()), ZERO)
                    # A validated native invoice covers the whole physical component.
                    # Its actual price replaces the old estimate, even if the price is
                    # lower. Partial legacy receipts retain the previous remainder rule.
                    fully_invoiced = any(obligations.get(key + ":" + rid, {}).get(
                        "source_component_fully_invoiced") is True for rid in relevant)
                    if received > total and not fully_invoiced:
                        issue("supplier_receipt_exceeds_estimate", key)
                    # Expected moves with the current assignment; confirmed is by actual receiver.
                    expected_party = item.get("supplier_id") if item.get("supplier_receipt_proven") is True else None
                    cost_currency = item.get("cost_currency") or currency
                    # Source currency belongs to this component, not the sale.
                    physically_received = item.get("supplier_receipt_proven") is True
                    eligible = physically_received or unreceived_eligible(order.get("status"))
                    put(key, "supplier", expected_party, "supplier", cost_currency,
                        expected=ZERO if not eligible or fully_invoiced else max(total - received, ZERO),
                        business_date=created.date(),
                        **({"source_cost_stage": item["source_cost_stage"]} if item.get("source_cost_stage") else {}))
            carrier = order.get("carrier")
            service_key = "shipping:" + oid
            frozen = facts.get(service_key)
            if frozen is None and carrier and order.get("status") in DELIVERED:
                frozen = fact(service_key, {"id": carrier["id"], "cost": amount(money(carrier["cost"])) if carrier.get("cost") is not None else None,
                                           "party_type": carrier.get("party_type", carrier.get("kind", "courier")),
                                           "delivered_at": order.get("delivered_at", updated.isoformat()),
                                           "cod_amount": amount(money(order.get("cod_amount", "0"))),
                                           "cod_evidence": deepcopy(order.get("cod") or ({"evidence_complete": False} if order.get("cod_required") else {})),
                                           "component_mode": bool(carrier.get("fee_components"))})
            cod_fact = facts.get("cod_collection:" + oid)
            cod_evidence = deepcopy(order.get("cod") or {})
            if frozen:
                original_evidence = frozen.get("cod_evidence", {})
                # Delivery freezes the executor, not an absent cash collection.
                candidate = original_evidence if original_evidence.get("evidence_complete", True) else cod_evidence
                candidate_amount = frozen["cod_amount"] if candidate is original_evidence else order.get("cod_amount", "0")
                executor_matches = carrier and carrier["id"] == frozen["id"]
                if cod_fact is None and (candidate or not order.get("cod_required")) and candidate.get("evidence_complete", True) and (candidate is original_evidence or executor_matches):
                    cod_fact = fact("cod_collection:" + oid, {"amount": amount(money(candidate_amount)),
                        "evidence": deepcopy(candidate), "party_id": frozen["id"]})
                if cod_fact:
                    original = cod_fact["evidence"]
                    proof_key = "cod_customer_confirmation:" + oid
                    proof = facts.get(proof_key)
                    # Customer payment proof can complete later while custody remains
                    # the same immutable zero for bank/card collections.
                    if (proof is None and executor_matches and cod_evidence.get("evidence_complete") is True
                            and cod_evidence.get("collection_confirmed") is True
                            and original.get("collection_confirmed") is False
                            and original.get("payment_method") in {"bank_transfer", "card_terminal"}
                            and cod_evidence.get("payment_method") == original.get("payment_method")
                            and cod_evidence.get("evidence_ids") == original.get("evidence_ids")
                            and money(cod_evidence.get("custody_amount", "0")) == money(cod_fact["amount"])):
                        proof = fact(proof_key, {"evidence": deepcopy(cod_evidence)})
                    cod_evidence = deepcopy(proof["evidence"] if proof else original)
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
                if order.get("native_driver_earning"):
                    earning = order["native_driver_earning"]
                    if (earning["party_id"] != frozen["id"] or frozen["party_type"] != "store_driver"
                            or instant(earning["delivered_at"]) != instant(frozen["delivered_at"])):
                        raise ValueError("driver_fee_saved_conflict")
                    fact("native_driver_earning:" + oid, {k: v for k, v in earning.items() if k != "party_name"})
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
                        obligations[service_key] = {**obligations[service_key], "expected": "0.00"}
                    for component in components:
                        component_key = service_key + ":" + component["id"]
                        component_fact = facts.get(component_key)
                        if component_fact is None and component.get("complete") and component.get("amount") is not None:
                            component_fact = fact(component_key, {"cost": amount(money(component["amount"]))})
                        if component_fact is not None:
                            put(component_key, "shipping", frozen["id"], frozen["party_type"], currency,
                                confirmed=money(component_fact["cost"]), business_date=day(frozen["delivered_at"]), fee_component=component["id"])
                        elif component_key in obligations:
                            obligations[component_key] = {**obligations[component_key], "expected": "0.00"}
                elif fee is not None:
                    put(service_key, "shipping", frozen["id"], frozen["party_type"], currency,
                        confirmed=money(fee["cost"]), business_date=day(frozen["delivered_at"]))
                elif service_key in obligations:
                    obligations[service_key] = {**obligations[service_key], "expected": "0.00"}
                if confirmed_cod or cod_evidence or "cod:" + oid in obligations:
                    put("cod:" + oid, "cod", frozen["id"], frozen["party_type"], currency,
                        confirmed=confirmed_cod, direction="receivable", business_date=day(frozen["delivered_at"]), **cod_metadata)
            elif carrier:
                if carrier.get("fee_components"):
                    if service_key in obligations:
                        obligations[service_key] = {**obligations[service_key], "expected": "0.00"}
                    for component in carrier["fee_components"]:
                        component_key = service_key + ":" + component["id"]
                        put(component_key, "shipping", carrier["id"], carrier.get("party_type", carrier.get("kind", "courier")), currency,
                            expected=ZERO if cancelled or not component.get("complete") else money(component["amount"]),
                            business_date=created.date(), fee_component=component["id"])
                elif carrier.get("cost") is not None:
                    put(service_key, "shipping", carrier["id"], carrier.get("party_type", carrier.get("kind", "courier")), currency,
                        expected=ZERO if cancelled else money(carrier["cost"]), business_date=created.date())
                elif service_key in obligations:
                    obligations[service_key] = {**obligations[service_key], "expected": "0.00"}
                put("cod:" + oid, "cod", carrier["id"], carrier.get("party_type", carrier.get("kind", "courier")), currency,
                    expected=ZERO if cancelled else money(order.get("cod_amount", "0")), direction="receivable", business_date=created.date(), **cod_metadata)
            else:
                for key, obligation in obligations.items():
                    if key == service_key or key.startswith(service_key + ":") or key == "cod:" + oid:
                        obligations[key] = {**obligation, "expected": "0.00"}
            if (not delivery_only and order.get("payment") and
                    apply_split_payment(result, order, now, put, issue, sources.get("fee_policies", []), reference_owners)):
                versions[oid] = updated.isoformat()
                continue
            if not delivery_only and order.get("payment"):
                payment = dict(order["payment"], currency=currency)
                provider_key = "provider:" + oid
                provider_facts = facts.copy()
                page = payment.get("order_page_balance")
                page_key = "provider_order_balance:" + oid
                page_mode = (page is not None and not payment.get("captures")) or page_key in facts
                try:
                    refs = {str(c["id"]) for c in payment.get("captures", [])}
                    if page and page.get("transaction_reference"):
                        refs.add(page["transaction_reference"])
                    if any(reference_owners.get(ref, set()) - {oid} for ref in refs):
                        raise ValueError("provider_capture_identity_conflict")
                    current_refunds = [r for r in payment.get("refunds", [])
                                       if r.get("status") in {"executed", "settled", "Refund Executed", "Settled"}]
                    for fk, fv in facts.items():
                        prefix = "refund:" + payment["provider"] + ":"
                        if fk.startswith(prefix) and fv["order_id"] == oid:
                            rid = fk[len(prefix):]
                            if not any(str(r["id"]) == rid for r in current_refunds):
                                current_refunds.append({"id": rid, "amount": fv["amount"], "status": "executed"})
                    payment["refunds"] = current_refunds
                    if page_mode and current_refunds and corroborated_refund_basis(facts.get(page_key), payment, facts, oid):
                        page_mode = False
                    page_fact = observed_balance(page, payment, order, result, now=now, start=start) if page_mode else None
                    recognition_day = instant(page_fact["observed_at"]).astimezone(RIYADH).date() if page_mode else created.date()
                    policy_day = created.astimezone(RIYADH).date()
                    try:
                        earliest_date = None
                        capture_days = [instant(c["captured_at"]).astimezone(RIYADH).date()
                                        for c in payment.get("captures", []) if created <= instant(c["captured_at"]) <= now]
                        if capture_days:
                            earliest_date, policy_day = min(capture_days), max(capture_days)
                        elif page_mode and money(page_fact["paid"]) > ZERO:
                            earliest_date = policy_day
                            policy_day = instant(page_fact["observed_at"]).astimezone(RIYADH).date()
                        projection = provider_projection(payment, sources.get("fee_policies", []), policy_day,
                                                         earliest_date=earliest_date)
                    except (KeyError, TypeError, ValueError, InvalidOperation, OverflowError) as exc:
                        if not page_mode and (not isinstance(exc, ValueError) or not independent_capture_without_fee_policy(
                                payment, sources.get("fee_policies", []), policy_day, exc, created=created, as_of=now)):
                            if not page or current_refunds:
                                raise
                            # A corroborating transaction cannot invalidate the independently
                            # proved page balance just because the fee schedule is damaged.
                            observed_balance(page, payment, order, result, now=now, start=start)
                        if not page_mode and any(a.get("obligation_id") in {provider_key, "credit:" + provider_key}
                               for m in result.get("movements", []) for a in m.get("allocations", [])):
                            raise ValueError("provider_settled_capture_review_required") from exc
                        projection = None
                    # A late capture may already be covered by an opening or payout.
                    # A previous successful refresh alone does not establish overlap.
                    recovered = payment.get("capture_source") in {
                        CANONICAL_WITH_UNBOUND_ACTIVITY, NATIVE_TRANSACTION_EVIDENCE_VERSION}
                    require_history_check = projection is None or recovered
                    captures = payment.get("captures", [])
                    paid_total = (payment.get("paid_observation") or {}).get("amount")
                    if not page_mode and (require_history_check or (captures and paid_total is not None)):
                        previous = obligations.get(provider_key, {})
                        if (previous.get("party_id", payment["provider"]) != payment["provider"]
                                or previous.get("currency", currency) != currency
                                or any(v.get("provider") != payment["provider"] for k, v in facts.items()
                                       if k.startswith("capture:") and v.get("order_id") == oid)):
                            raise ValueError("provider_capture_identity_conflict")
                        prior_as_of = (state.get("engine") or {}).get("as_of")
                        if any("capture:" + str(c["id"]) not in facts for c in captures):
                            if (money(previous.get("confirmed", "0")) > ZERO or
                                    any(k.startswith("capture:") and v.get("order_id") == oid for k, v in facts.items())):
                                raise ValueError("provider_capture_identity_conflict")
                            late = require_history_check and prior_as_of and any(
                                instant(c["captured_at"]) <= instant(prior_as_of) for c in captures)
                            if (late or result.get("movements")) and late_payment_blocks(result, order, payment, through=now):
                                raise ValueError("provider_historical_capture_review_required" if late else "provider_settled_capture_review_required")
                    # A paid gross obligation already includes settlement fee deductions.
                    # A component refund needs the statement's actual fee-credit allocation;
                    # do not flip that settled obligation or invent a second fee charge.
                    if projection is not None and projection.get("fee_model_version") == 2 and current_refunds and any(
                            a.get("obligation_id") == provider_key and money(a.get("amount", "0")) > ZERO
                            for movement in result.get("movements", [])
                            for a in movement.get("allocations", [])):
                        raise ValueError("provider_postsettlement_refund_review_required")
                    for capture in payment.get("captures", []):
                        if instant(capture["captured_at"]) > now:
                            continue
                        fact("capture:" + str(capture["id"]), {"order_id": oid, "provider": payment["provider"],
                             "amount": amount(money(capture["amount"]))})
                    captured = sum((money(v["amount"]) for k, v in facts.items()
                                    if k.startswith("capture:") and v["order_id"] == oid), ZERO)
                    if page_mode:
                        facts[page_key] = page_fact
                        captured = money(page_fact["paid"])
                    for refund in payment.get("refunds", []):
                        if refund.get("status") in {"executed", "settled", "Refund Executed", "Settled"}:
                            fact("refund:" + payment["provider"] + ":" + str(refund["id"]),
                                 {"order_id": oid, "amount": amount(money(refund["amount"]))})
                    refunded = sum((money(v["amount"]) for k, v in facts.items()
                                    if k.startswith("refund:") and v["order_id"] == oid), ZERO)
                    if (captured > money(payment["gross"]) or (captured and refunded > captured)
                            or (captures and paid_total is not None and captured != money(paid_total))):
                        raise ValueError("provider_capture_amount_conflict")
                    confirmed = max(captured - refunded, ZERO)
                    if projection is None:
                        # Store only the proved gross claim. The absence of a fee
                        # projection keeps fee/net forecasts explicitly unavailable.
                        engine["provider_reports"].pop(provider_key, None)
                        put(provider_key, "provider", payment["provider"], "provider", currency,
                            confirmed=confirmed, direction="receivable", business_date=recognition_day,
                            fee_estimate_complete=False)
                        issue("provider_fee_estimate_unavailable", provider_key)
                    else:
                        projected = Decimal(projection["expected_receivable"])
                        projection["confirmed_receivable"] = amount(confirmed)
                        engine["provider_reports"][provider_key] = projection
                        if not captured and not page_mode:
                            issue("provider_capture_evidence_missing", provider_key)
                        put(provider_key, "provider", payment["provider"], "provider", currency,
                            expected=max(projected - confirmed, ZERO) if projected >= 0 else abs(projected),
                            confirmed=confirmed, direction="receivable" if projected >= 0 or confirmed else "payable",
                            business_date=recognition_day, projection=projection)
                    for ref in refs:
                        if "capture:" + ref in facts or (page_mode and page_fact.get("transaction_reference") == ref):
                            reference_owners.setdefault(ref, set()).add(oid)
                except (ValueError, KeyError) as error:
                    facts.clear()
                    facts.update(provider_facts)
                    if provider_key in obligations:
                        obligations[provider_key] = {**obligations[provider_key], "expected": "0.00", "incomplete": True}
                    if page and str(error) in {"provider_historical_capture_review_required", "provider_settled_capture_review_required"}:
                        issues.append({"code": str(error), "source_id": provider_key, "provider": payment["provider"],
                                       "history_proof_hash": proof_hash(order, payment), "history_created_at": order["created_at"],
                                       "order_page_balance_review": {k: page[k] for k in
                                           ("order_id", "order_number", "provider", "currency", "gross", "paid", "remaining")}})
                    else:
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

    from operational_salary_projection import candidates as salary_candidates, legacy_salary_day_matches
    from operational_salary_start import select_first_day
    try:
        first_salary_day = select_first_day(state, now)
    except (ValueError, KeyError, TypeError):
        issue("operational_salary_start_policy_invalid", "salary_start")
        first_salary_day = None
    today = now.astimezone(RIYADH).date()
    # An invalid policy never rewrites recorded days or silently accrues using
    # another cutoff. Other operational sources can still reconcile.
    current_salary = {}
    for employee in (sources.get("employees", []) if first_salary_day is not None else []):
        eid = str(employee.get("id", ""))
        try:
            projected, missing = salary_candidates(employee, first_salary_day, today)
            current_salary.update(projected)
            for key in missing:
                issue("salary_contract_missing", key)
        except (ValueError, KeyError, TypeError, InvalidOperation) as error:
            issue(str(error), eid)
    conflicts = {} if first_salary_day is not None else deepcopy(engine.get("salary_conflicts", {}))
    for key, previous in (engine["salary_days"].items() if first_salary_day is not None else []):
        proposed = current_salary.get(key)
        previous_currency = previous.get("currency", obligations.get(key, {}).get("currency"))
        if (proposed is None or previous.get("amount") != proposed["amount"]
                or previous_currency != proposed["currency"]) and not legacy_salary_day_matches(
                    key, previous, proposed, previous_currency):
            conflicts[key] = {"recorded": deepcopy(previous), "proposed": deepcopy(proposed),
                              "action_required": "review_documented_salary_adjustment"}
            issue("salary_source_changed", key)
    previous_conflicts = engine.get("salary_conflicts", {})
    for key, conflict in conflicts.items():
        if previous_conflicts.get(key) != conflict:
            event = {"action": "salary_source_changed", "obligation_id": key,
                     "before": previous_conflicts.get(key), "after": deepcopy(conflict),
                     "actor_id": "operational_worker", "source": "mz2", "at": now.isoformat()}
            event["id"] = fingerprint({**event, "sequence": len(engine["audit"])})
            engine["audit"].append(event)
    if conflicts or "salary_conflicts" in engine:
        engine["salary_conflicts"] = conflicts
    for key, candidate in current_salary.items():
        if key not in engine["salary_days"]:
            engine["salary_days"][key] = candidate
            eid, business_day = key[len("salary:"):].rsplit(":", 1)
            put(key, "salary", eid, "employee", candidate["currency"],
                confirmed=Decimal(candidate["amount"]), business_date=business_day)

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
            wallet_first = snapshot.get("funding_policy") == "wallet_first"
            if wallet_first:
                policy = state.get("ad_funding", {}).get(snapshot["account_id"], {})
                if funding != "hybrid" or policy.get("mode") != "wallet_first" or policy.get("currency") != snapshot["currency"]:
                    raise ValueError("advertising_wallet_policy_incomplete")
            fraction = ZERO if wallet_first else (money(snapshot.get("wallet_fraction")) if funding == "hybrid" else (Decimal(1) if funding == "prepaid" else ZERO))
            if fraction > 1:
                raise ValueError("advertising_hybrid_policy_incomplete")
            if prior and prior["closed"] and (prior.get("funding_type", funding) != funding
                                             or Decimal(prior.get("wallet_fraction", str(fraction))) != fraction
                                             or prior.get("timezone", str(zone)) != str(zone)
                                             or prior.get("funding_policy") != snapshot.get("funding_policy")):
                raise ValueError("advertising_closed_funding_changed")
            engine["ad_days"][key] = {"amount": amount(value), "closed": closed, "observed_at": observed.isoformat(),
                                      "funding_type": funding, "wallet_fraction": str(fraction), "timezone": str(zone),
                                      **({"funding_policy": "wallet_first"} if wallet_first else {})}
            metadata = {field: snapshot.get(field) for field in ("fx_rate", "fx_source", "fx_at", "fx_date", "fx_snapshot_id", "fx_evidence", "sar_amount",
                        "source_cumulative_amount", "baseline_amount", "hybrid_policy_id", "hybrid_policy_version", "hybrid_policy_evidence",
                        "hybrid_confirmed_at", "hybrid_confirmed_by")}
            parts = [(key, value, "wallet_first" if wallet_first else funding)]
            if funding == "hybrid" and not wallet_first:
                wallet = Decimal(amount(value * fraction))
                parts = [(key + ":wallet", wallet, "prepaid"), (key + ":credit", value-wallet, "postpaid")]
            active_parts = {part_key for part_key, _, _ in parts}
            for old_key in (key, key + ":wallet", key + ":credit"):
                if old_key in obligations and old_key not in active_parts and money(obligations[old_key]["confirmed"]) == ZERO:
                    obligations[old_key].update(expected="0.00", superseded=True)
            for part_key, part_value, part_funding in parts:
                part_metadata = dict(metadata)
                if funding == "hybrid" and not wallet_first and metadata.get("sar_amount") is not None:
                    wallet_sar = Decimal(amount(money(metadata["sar_amount"]) * fraction))
                    part_metadata["sar_amount"] = amount(wallet_sar if part_funding == "prepaid" else money(metadata["sar_amount"])-wallet_sar)
                put(part_key, "advertising", snapshot["account_id"], "ad_account", snapshot["currency"],
                    expected=ZERO if closed else part_value, confirmed=part_value if closed else ZERO,
                    business_date=ad_date, funding_type=part_funding, funding_mode=funding,
                    wallet_fraction=str(fraction), ad_day_id=key, ad_total_amount=amount(value),
                    original_currency=snapshot["currency"], timezone=str(zone),
                    **({"funding_policy": "wallet_first"} if wallet_first else {}), **part_metadata)
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

    from operational_historical_shipping_fee import apply_reviewed_fees
    resolved_fees = apply_reviewed_fees(result, sources, put, fact, issue)
    if resolved_fees:
        issues = [i for i in issues if (i.get("code"), i.get("source_id")) not in resolved_fees]
    from operational_customer_returns import shipping_obligations
    shipping_obligations(result)
    from operational_balance_exchanges import project_exchanges
    project_exchanges(result)
    from operational_balance_inventory import project_inventory
    project_inventory(result)
    coverage = sources.get("delivery_event_coverage") or {}
    if (coverage.get("version") == 1 and instant(coverage.get("started_at")) == start
            and instant(coverage.get("as_of")) == now):
        engine["delivery_event_coverage"] = deepcopy(coverage)
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
