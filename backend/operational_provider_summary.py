"""One provider balance view, with per-order fees and unchanged allocation identities."""
from collections import defaultdict
from decimal import Decimal

from operational_configured_provider_fees import cash, configured_terms, estimate_fee, number
from operational_fee_methods import fee_rail
from operational_payment_evidence import DIAGNOSTICS
from operational_provider_balance_sar import sar_amount
from operational_provider_report_coverage import PLATFORMS

ZERO = Decimal(0)
PENDING_SAMPLE_LIMIT = 20
PENDING_MESSAGES = {
    "provider_fee_estimate_unavailable": "عمولة هذا الطلب غير مكتملة؛ أصل المدفوع محفوظ وصافيه يحتاج استكمال الرسوم.",
    "provider_balance_recognition_pending": "المدفوع مثبت ويحتاج مطابقة الذمة أو الافتتاحي أو التسويات قبل إضافته إلى الصافي.",
    "provider_historical_capture_review_required": "إثبات طلب سابق يحتاج مطابقة الرصيد الافتتاحي والحوالات المسجلة.",
    "provider_settled_capture_review_required": "إثبات المدفوع يحتاج مطابقة توزيع التسويات المسجلة على الطلب.",
    "provider_balance_sar_unavailable": "المبلغ مثبت بعملته الأصلية؛ يلزم سعر تحويل موثق لهذا الطلب لعرضه بالريال.",
}


def _money(value):
    return None if value is None else format(cash(value), ".2f")


def _settlements(state, valuations):
    allocated = defaultdict(lambda: {"gross": ZERO, "fees": ZERO})
    scope_fees = defaultdict(Decimal)
    scope_cash = defaultdict(Decimal)
    native = defaultdict(lambda: {"gross": ZERO, "cash": ZERO, "returned": ZERO})
    for movement in state.get("movements", []):
        if movement.get("party_type") != "provider" or movement.get("kind") == "correction":
            continue
        incoming = movement.get("direction") == "incoming"
        received = Decimal(movement["amount"])
        total = received + Decimal(movement.get("actual_fee_amount", "0"))
        if total <= 0:
            continue
        cumulative, previous = ZERO, ZERO
        assigned_fees = ZERO
        for line in movement.get("allocations", []):
            identity = line["obligation_id"]
            gross = Decimal(line["amount"])
            cumulative += gross
            boundary = cash(received * cumulative / total)
            net = boundary - previous
            fee = gross - net
            previous = boundary
            assigned_fees += fee
            row = native[identity, movement["currency"], movement["party_id"]]
            if incoming:
                row["gross"] += gross
                row["cash"] += net
            else:
                row["returned"] += net
        remainder = Decimal(movement.get("actual_fee_amount", "0")) - assigned_fees
        scope = movement["currency"], movement["party_id"]
        if incoming:
            scope_fees[scope] += remainder
        scope_cash[scope] += (received - previous) * (1 if incoming else -1)
    for (identity, currency, provider), values in native.items():
        basis = valuations.get(identity.removeprefix("credit:"))
        if basis:
            values = {key: sar_amount(value, basis) for key, value in values.items()}
            currency = "SAR"
        fee = values["gross"] - values["cash"]
        allocated[identity].update(gross=values["gross"], fees=fee)
        scope_fees[currency, provider] += fee
        scope_cash[currency, provider] += values["cash"] - values["returned"]
    return allocated, scope_fees, scope_cash


def _records(state, obligations, valuations):
    engine = state.get("engine", {})
    inputs = engine.get("provider_fee_inputs", {}).get("orders", {})
    settings = engine.get("provider_fee_inputs", {}).get("settings", {})
    records = {}
    refunds = defaultdict(Decimal)
    refund_events = defaultdict(list)
    captured = defaultdict(Decimal)
    pages = {}
    page_rows = {}
    for key, fact in engine.get("facts", {}).items():
        # Facts also retain payroll, inventory and shipping evidence. Their
        # payloads are not provider schemas and may legitimately be text.
        if not key.startswith(("refund:", "capture:", "provider_order_balance:", "provider_split:")) or not isinstance(fact, dict):
            continue
        evidence = fact.get("evidence")
        evidence = evidence if isinstance(evidence, dict) else {}
        oid = str(fact.get("order_id") or evidence.get("order_id") or "")
        if key.startswith("refund:") and oid:
            refunds[oid, key.split(":", 2)[1]] += Decimal(fact["amount"])
            refund_events[oid, key.split(':', 2)[1]].append({
                'id': key.split(':', 2)[2], 'amount': fact['amount'], 'status': 'executed'})
        elif key.startswith("capture:") and oid and fact.get("provider"):
            captured[oid, fact["provider"]] += Decimal(fact["amount"])
        elif key == "provider_order_balance:" + oid:
            pages[oid, fact["provider"]] = Decimal(fact["paid"])
            page_rows[oid, fact["provider"]] = fact
        elif key == "provider_split:" + oid:
            split = evidence
            for leg in split.get("tenders", []):
                if leg.get("provider") in fact.get("booked_providers", []):
                    captured[oid, leg["provider"]] += Decimal(leg["amount"])
    for pair, paid in pages.items():
        captured[pair] = max(captured[pair], paid)
    claims = {row["id"]: row for row in obligations
              if row.get("kind") == "provider" and not row.get("derived_credit")}
    for oid, source in inputs.items():
        payment = source.get("payment") or {}
        fx = source.get("customer_payment_sar") or {}
        native = {key: Decimal(value) for key, value in fx.get("native", {}).items() if key in PLATFORMS}
        split = payment.get("split")
        methods = {}
        pricing = {}
        if split:
            legs = defaultdict(list)
            for leg in split.get("tenders", []):
                if leg.get("provider") in PLATFORMS:
                    legs[leg["provider"]].append(leg)
            for provider, values in legs.items():
                native.setdefault(provider, sum((Decimal(v["amount"]) for v in values), ZERO))
                methods[provider] = values[0].get("method") if len(values) == 1 else None
                pricing[provider] = values[0] if len(values) == 1 else {}
                if provider == "salla" and len(values) > 1:
                    rails = {fee_rail(value.get("method")) for value in values}
                    terms = {configured_terms(settings, provider, value.get("method")) for value in values}
                    if len(rails) == 1 and None not in rails and len(terms) == 1 and None not in terms:
                        methods[provider] = values[0].get("method")
        elif payment.get("provider") in PLATFORMS:
            provider = payment["provider"]
            page = payment.get("order_page_balance") or {}
            paid = number(page.get("paid"))
            if paid is None and payment.get("captures"):
                paid = sum((Decimal(c["amount"]) for c in payment["captures"]), ZERO)
            if paid is not None:
                native.setdefault(provider, paid)
            methods[provider] = payment.get("payment_method")
            pricing[provider] = payment
        for provider, paid in native.items():
            identity = "provider:" + oid + (":" + provider if split else "")
            records[identity] = {"order_id": oid, "provider": provider, "currency": source["currency"],
                                 "order_number": source.get("order_number") or (payment.get("order_page_balance") or {}).get("order_number"),
                                 "gross": max(paid, captured[oid, provider]), "method": methods.get(provider),
                                 "paid_proven": True,
                                 "payment": pricing.get(provider, {}), "created_at": source.get("created_at"),
                                 "observed_at": source.get("updated_at"),
                                 "fx": fx, "unresolved_split_fees": bool(
                                     split and len(legs[provider]) != 1 and methods.get(provider) is None)}
    for identity, claim in claims.items():
        provider = claim["party_id"]
        oid = identity.removeprefix("provider:").removesuffix(":" + provider)
        original = claim.get("native_balance") or claim
        gross = max(captured[oid, provider], Decimal(original["confirmed"]) + refunds[oid, provider])
        if identity not in records:
            records[identity] = {"order_id": oid, "provider": provider, "currency": original["currency"],
                                 "gross": gross, "method": None, "fx": {}, "order_number": claim.get("order_number")}
        else:
            records[identity]["gross"] = max(records[identity]["gross"], gross)
    for (oid, provider), page in page_rows.items():
        records.setdefault("provider:" + oid, {"order_id": oid, "provider": provider,
            "currency": page["currency"], "gross": captured[oid, provider], "method": None, "fx": {}})
        if not records["provider:" + oid].get("order_number"):
            records["provider:" + oid]["order_number"] = page.get("order_number")
    for issue in engine.get("issues", []):
        page = issue.get("order_page_balance_review")
        if not isinstance(page, dict) or page.get("provider") not in PLATFORMS:
            continue
        identity = "provider:" + str(page["order_id"])
        record = records.setdefault(identity, {"order_id": str(page["order_id"]), "provider": page["provider"],
            "currency": page["currency"], "gross": Decimal(page["paid"]), "method": None, "fx": {}})
        if (record["provider"], record["currency"]) == (page["provider"], page["currency"]):
            record["gross"] = max(record["gross"], Decimal(page["paid"]))
            if not record.get("order_number"):
                record["order_number"] = page.get("order_number")
    for identity, row in records.items():
        original_currency, native_paid = row["currency"], row["gross"]
        row.setdefault("paid_proven", native_paid > 0 or (row["order_id"], row["provider"]) in page_rows)
        row["refunded"] = refunds[row["order_id"], row["provider"]]
        row['refund_events'] = sorted(refund_events[row['order_id'], row['provider']], key=lambda event: event['id'])
        source_refunds = {str(event.get('id')): event for event in (row.get('payment') or {}).get('refunds', [])}
        row['refund_events'] = [{**event, **{key: source_refunds.get(event['id'], {}).get(key)
            for key in ('refunded_at', 'executed_at') if source_refunds.get(event['id'], {}).get(key)}}
            for event in row['refund_events']]
        row["native_gross"] = native_paid
        row["native_refunded"] = row["refunded"]
        basis = valuations.get(identity)
        fx = row["fx"]
        if not basis and identity not in claims and fx.get("currency") == original_currency:
            paid = number(fx.get("native", {}).get(row["provider"]))
            sar = number(fx.get("shares", {}).get(row["provider"]))
            if paid == native_paid and paid and sar and fx.get("basis") in {"salla_order_fx", "salla_list_paid"}:
                basis = {"native_paid": str(paid), "sar_paid": str(sar)}
        if original_currency != "SAR" and basis:
            row.update(currency="SAR", gross=sar_amount(native_paid, basis), refunded=sar_amount(row["refunded"], basis))
            cumulative = previous = ZERO
            converted = []
            for event in row['refund_events']:
                cumulative += Decimal(event['amount'])
                boundary = sar_amount(cumulative, basis)
                converted.append({**event, 'amount': str(boundary - previous)})
                previous = boundary
            row['refund_events'] = converted
    return records, claims


def provider_balances(state, details, obligations, parties, valuations, *, sar_view=False):
    """Project fees once on the unpaid gross claim. Never change saved financial state."""
    records, claims = _records(state, obligations, valuations)
    allocated, actual_fees, settled_cash = _settlements(state, valuations)
    settings = state.get("engine", {}).get("provider_fee_inputs", {}).get("settings", {})
    policies = state.get("engine", {}).get("provider_fee_inputs", {}).get("policies", [])
    source_mode = state.get('engine', {}).get('provider_fee_inputs', {}).get('source_mode', 'legacy_settings')
    groups = {}
    resolved = set()

    def group(currency, provider):
        return groups.setdefault((currency, provider), {"provider": provider, "name": PLATFORMS.get(provider, provider),
            "orders": set(), "gross": ZERO, "fees": ZERO, "refunded": ZERO, "remaining_fees": ZERO,
            "complete": True, "fees_complete": True, "held_orders": 0, "opening": ZERO, "settled": ZERO,
            "known_gross": ZERO, "known_fees": ZERO, "known_net": ZERO,
            "paid_orders": set(), "paid_orders_complete": True,
            "confirmed_orders": set(), "net_confirmed_orders": set(),
            "_pending": defaultdict(set), "_order_numbers": {}, "_held": set(),
            "_gross_complete": True, "_gross_available": False, "_net_available": False,
            "_fees_available": False, "_excluded_net": ZERO})

    def pending(total, oid, code, order_number=None):
        total["_pending"][code].add(oid)
        total["complete"] = False
        if order_number:
            total["_order_numbers"][oid] = str(order_number)

    for identity, row in records.items():
        currency, provider = row["currency"], row["provider"]
        total = group(currency, provider)
        if row["gross"] > 0:
            total["orders"].add(row["order_id"])
            if row["paid_proven"]:
                # Customer paid proof is distinct from accepting the claim
                # against opening/settlement history or proving its net fees.
                total["paid_orders"].add(row["order_id"])
        total["gross"] += row["gross"]
        total["known_gross"] += row["gross"]
        total["_gross_available"] = total["_gross_available"] or row["paid_proven"]
        if row.get("order_number"):
            total["_order_numbers"][row["order_id"]] = str(row["order_number"])
        total["refunded"] += row["refunded"]
        claim = claims.get(identity)
        original = (claim.get("native_balance") or claim) if claim else {}
        held = (not claim or claim["currency"] != currency or claim.get("incomplete")
                or row["native_gross"] > Decimal(original.get("confirmed", "0")) + row["native_refunded"])
        if held and row["gross"]:
            total["complete"] = False
            total["_held"].add(row["order_id"])
        elif row["gross"] > 0:
            total["confirmed_orders"].add(row["order_id"])
        projection = details.get("provider_reports", {}).get(identity)
        fee = None
        if projection and not held:
            fee = number(projection.get("estimated_fees_original", projection.get("estimated_fees")))
            # Only paid captures belong in the current balance. Future fees stay
            # inside the future net collection, rather than being charged here.
            projected_gross = number(projection.get("gross"))
            if projected_gross and row["gross"] < projected_gross:
                fee = None
        if fee is None and not row.get("unresolved_split_fees"):
            fee = estimate_fee(settings, provider, row["gross"], method=row["method"], refunded=row["refunded"],
                policies=policies, payment=row.get("payment"), created_at=row.get("created_at"),
                observed_at=row.get("observed_at"), refunds=row.get('refund_events'), source_mode=source_mode,
                as_of=state.get('engine', {}).get('as_of'), currency=currency)
        retained_uncaptured = (claim and claim.get("direction") == "payable"
                               and Decimal(claim.get("expected", "0")) > 0 and row["gross"] == 0)
        if row["gross"] == 0 and not retained_uncaptured:
            fee = ZERO
        if retained_uncaptured:
            fee = None
        applied = allocated[identity]["gross"]
        fully_settled = not held and row["gross"] > 0 and applied >= row["gross"]
        if row["refunded"] and applied > 0 and not fully_settled:
            # A refund overlapping a partial payout needs an actual fee-credit
            # allocation. The reduced principal cannot establish that split.
            fee = None
        if fee is None and fully_settled:
            # The allocation proves its full cash/fee split. A missing estimate
            # cannot turn an entirely settled actual fee back into uncertainty.
            fee = ZERO
        if fee is None:
            total["complete"] = total["fees_complete"] = False
            pending(total, row["order_id"], "provider_fee_estimate_unavailable", row.get("order_number"))
            continue
        if not held and row["paid_proven"]:
            resolved.add(identity)
            total["_net_available"] = total["_fees_available"] = True
            if row["gross"] > 0:
                total["net_confirmed_orders"].add(row["order_id"])
        covered = ZERO
        if not held and row["gross"] > 0 and applied > 0:
            covered = cash(fee * min(applied, row["gross"]) / row["gross"])
        remaining = max(fee - covered, ZERO)
        total["fees"] += remaining
        if not held:
            total["remaining_fees"] += remaining
            total["known_fees"] += remaining

    # Preserve reasons and unique order identities, including orders whose
    # native payment failed normalization before a claim could be created.
    by_order = defaultdict(list)
    for identity, row in records.items():
        by_order[row["order_id"]].append((identity, row))
    for issue in state.get("engine", {}).get("issues", []):
        code = str(issue.get("code") or "")
        source_id = str(issue.get("source_id") or "")
        if not source_id or code == "provider_net_balance_incomplete":
            continue
        if issue.get("scope") != "provider_payment" and not source_id.startswith("provider:"):
            continue
        oid = source_id.removeprefix("provider:")
        provider = issue.get("provider")
        exact = records.get(source_id)
        if exact:
            oid, provider = exact["order_id"], exact["provider"]
        candidates = [(identity, row) for identity, row in by_order.get(oid, [])
                      if provider is None or row["provider"] == provider]
        if not candidates and provider in PLATFORMS:
            # These identifiers describe account-level diagnostics, not orders.
            if oid == provider:
                continue
            currency = issue.get("currency")
            if currency is None:
                currencies = {ccy for ccy, party in groups if party == provider}
                currency = next(iter(currencies)) if len(currencies) == 1 else None
            if currency:
                candidates = [(None, {"order_id": oid, "provider": provider, "currency": currency})]
        for identity, row in candidates:
            if code == "provider_fee_estimate_unavailable" and identity in resolved:
                continue
            total = group(row["currency"], row["provider"])
            pending(total, oid, code, issue.get("order_number") or row.get("order_number"))
            if identity is None or not row.get("paid_proven"):
                total["_gross_complete"] = total["fees_complete"] = False
    for total in groups.values():
        explained = set().union(*(ids for code, ids in total["_pending"].items()
                                  if code != "provider_fee_estimate_unavailable"))
        for oid in total["_held"] - explained:
            pending(total, oid, "provider_balance_recognition_pending")
    if sar_view:
        coverage = details.get("customer_tender_coverage", {}).get("sar", {})
        for source in coverage.get("by_method", []):
            provider = source.get("method")
            if provider not in PLATFORMS:
                continue
            total = group("SAR", provider)
            total["source_order_count"] = source.get("orders", 0)
            paid = number(source.get("paid"))
            known = number(source.get("known_paid", source.get("paid")))
            source_ids = source.get("paid_order_ids")
            indexed = (isinstance(source_ids, list)
                       and all(isinstance(oid, (str, int)) and not isinstance(oid, bool) and str(oid) for oid in source_ids)
                       and (bool(source_ids) or not known))
            individual = {row["order_id"]: row for row in records.values()
                          if row["provider"] == provider and row["paid_proven"] and row["gross"] > 0}
            if indexed:
                source_ids = {str(oid) for oid in source_ids}
            elif (paid is not None and paid > 0 and type(source.get("orders")) is int
                    and source["orders"] == len(individual)):
                # Legacy snapshots can still supply a complete individual
                # proof index. A bare aggregate amount never supplies a count.
                source_ids, indexed = set(individual), True
            else:
                source_ids = set()
                if known and (not total["paid_orders"] or source.get("orders", 0) > len(total["paid_orders"])):
                    total["paid_orders_complete"] = False
            # Source orders include verified zero-paid orders. They are not
            # missing paid identities and must not become review exceptions.
            # The current source index contains every positive valued share;
            # its verified count therefore establishes the zero-paid count
            # without inventing identities or changing any source amount.
            source_count = source.get("orders", 0)
            incomplete_count = source.get("incomplete_orders")
            zero_orders = source_count if paid == ZERO else 0
            if (indexed and type(source_count) is int and type(incomplete_count) is int
                    and 0 <= incomplete_count <= source_count):
                zero_orders = max(source_count - incomplete_count - len(source_ids), 0)
            total["_source_zero_paid_orders"] = zero_orders
            for oid in source_ids - total["paid_orders"]:
                source_order = individual.get(oid, {})
                pending(total, oid, "provider_balance_recognition_pending", source_order.get("order_number"))
            total["paid_orders"].update(source_ids)
            total["orders"].update(source_ids)
            if known is not None and known > total["gross"]:
                total["gross"] = known
                total["known_gross"] = known
                total["_gross_available"] = True
                total["complete"] = total["fees_complete"] = False
            if paid is None:
                total["_gross_complete"] = False
                total["complete"] = total["fees_complete"] = False
                for row in records.values():
                    if row["provider"] == provider and row["currency"] != "SAR":
                        pending(total, row["order_id"], "provider_balance_sar_unavailable", row.get("order_number"))

    # Unknown fees or conflicting recognition cannot turn gross principal into
    # known net. Remove only those claims (and their derived credits), retaining
    # independent opening balances and other proved orders in the same account.
    for obligation in obligations:
        if obligation.get("party_type") != "provider":
            continue
        total = group(obligation["currency"], obligation["party_id"])
        original = obligation.get("original_obligation_id", obligation["id"])
        if original in claims and original not in resolved:
            sign = 1 if obligation["direction"] == "receivable" else -1
            total["_excluded_net"] += Decimal(obligation["outstanding"]) * sign
        elif obligation.get("kind") == "baseline":
            total["_net_available"] = True
    for movement in state.get("movements", []):
        if movement.get("party_type") != "provider":
            continue
        unallocated = Decimal(movement["amount"]) - sum(
            (Decimal(a["amount"]) for a in movement.get("allocations", [])), ZERO)
        if movement.get("kind") == "correction" or unallocated > 0:
            group(movement["currency"], movement["party_id"])["_net_available"] = True
    for party in parties.values():
        if party["party_type"] != "provider":
            continue
        key = party["currency"], party["party_id"]
        total = group(*key)
        total["opening"] += party["opening"]
        total["settled"] = settled_cash[key]
        # Allocated settlement fees already reduced the gross debt. Only the
        # estimated fee on its remaining share is still owed to the platform.
        deduction = total["remaining_fees"]
        taken = min(party["outstanding_receivable"], deduction)
        party["outstanding_receivable"] -= taken
        party["outstanding_payable"] += deduction - taken
        party["provider_fees_remaining"] = _money(deduction)
        party["provider_net_complete"] = total["complete"]
        total["known_net"] += party["outstanding_receivable"] - party["outstanding_payable"]
    result = defaultdict(list)
    for (currency, provider), total in sorted(groups.items()):
        total["fees"] += actual_fees[currency, provider]
        total["known_fees"] += actual_fees[currency, provider]
        total["known_net"] -= total["_excluded_net"]
        pending_ids = set().union(*total["_pending"].values())
        samples = sorted(pending_ids)
        total["pending_orders"] = len(pending_ids)
        total["pending_order_ids"] = samples
        total["pending_order_numbers"] = sorted({total["_order_numbers"][oid] for oid in samples
                                                  if oid in total["_order_numbers"]})
        total["pending_details_truncated"] = False
        total["pending_reasons"] = [{"code": code, "message": PENDING_MESSAGES.get(code, DIAGNOSTICS.get(code, "إثبات هذا الطلب يحتاج مطابقة.")),
            "orders": len(ids), "order_ids": sorted(ids)[:PENDING_SAMPLE_LIMIT]}
            for code, ids in sorted(total["_pending"].items())]
        total["pending_order_details"] = [{"order_id": oid,
            **({"order_number": total["_order_numbers"][oid]} if oid in total["_order_numbers"] else {}),
            "reasons": [{"code": reason["code"], "message": reason["message"]}
                        for reason in total["pending_reasons"] if oid in total["_pending"][reason["code"]]]}
            for oid in samples]
        known_orders = len(total["orders"] | pending_ids)
        total["orders"] = max(known_orders, total.pop("source_order_count", 0))
        total["unidentified_pending_orders"] = max(
            total["orders"] - known_orders - total.get("_source_zero_paid_orders", 0), 0)
        total["pending_orders"] += total["unidentified_pending_orders"]
        total["pending_details_complete"] = total["unidentified_pending_orders"] == 0
        total["held_orders"] = len(total["_held"])
        total["paid_order_ids"] = sorted(total["paid_orders"])
        total["paid_orders"] = (len(total["paid_orders"]) if total["paid_orders"] or total["paid_orders_complete"] else None)
        total["confirmed_orders"] = len(total["confirmed_orders"])
        total["net_confirmed_orders"] = len(total["net_confirmed_orders"])
        total["net"] = total["known_net"] if total["complete"] else None
        if not total["_net_available"] and not total["complete"]:
            total["known_net"] = None
        if not total["_fees_available"] and not actual_fees[currency, provider] and not total["fees_complete"]:
            total["known_fees"] = None
        if not total["_gross_complete"]:
            total["gross"] = None
            if not total["_gross_available"]:
                total["known_gross"] = None
        if not total["fees_complete"]:
            total["fees"] = None
        result[currency].append({k: _money(v) if isinstance(v, Decimal) else v for k, v in total.items()
                                 if k != "remaining_fees" and not k.startswith("_")})
    return dict(result), resolved
