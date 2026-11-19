"""One provider balance view, with per-order fees and unchanged allocation identities."""
from collections import defaultdict
from decimal import Decimal

from operational_configured_provider_fees import cash, configured_terms, estimate_fee_details, number
from operational_fee_methods import fee_rail
from operational_payment_evidence import DIAGNOSTICS
from operational_provider_balance_sar import sar_amount
from operational_provider_report_coverage import PLATFORMS
from operational_provider_statement_fees import (prepare_statement_fee_context, resolve_statement_fees,
    select_statement_average_profile, statement_average_summary, statement_average_source_summary)

ZERO = Decimal(0)
PENDING_SAMPLE_LIMIT = 20
PENDING_MESSAGES = {
    "provider_fee_estimate_unavailable": "عمولة هذا الطلب غير مكتملة؛ أصل المدفوع محفوظ وصافيه يحتاج استكمال الرسوم.",
    "provider_balance_recognition_pending": "المدفوع مثبت ويحتاج مطابقة الذمة أو الافتتاحي أو التسويات قبل إضافته إلى الصافي.",
    "provider_historical_capture_review_required": "إثبات طلب سابق يحتاج مطابقة الرصيد الافتتاحي والحوالات المسجلة.",
    "provider_settled_capture_review_required": "إثبات المدفوع يحتاج مطابقة توزيع التسويات المسجلة على الطلب.",
    "provider_balance_sar_unavailable": "المبلغ مثبت بعملته الأصلية؛ يلزم سعر تحويل موثق لهذا الطلب لعرضه بالريال.",
    "provider_fee_average_estimate": "العمولة تقديرية من كشف المنصة؛ نسبة العمولة المتغيرة قبل الضريبة، والرسم الثابت والضريبة منفصلان. يستبدلها دليل مطابق.",
    "provider_statement_fee_review_required": "رسوم الكشف أو توزيعها على الدفع والاسترجاع تحتاج مراجعة؛ لم تُضف رسوم غير مؤكدة.",
    "provider_statement_fee_currency_conflict": "عملة رسوم الكشف لا تطابق أصل العملية المثبت.",
    "provider_statement_fee_evidence_changed": "تغير دليل العملية منذ قبول الكشف؛ أعد مراجعة المطابقة.",
    "provider_statement_fee_principal_unconfirmed": "أصل الدفع أو الاسترجاع المرتبط برسوم الكشف غير مكتمل في اللقطة الحالية.",
    "provider_statement_fee_settlement_conflict": "رسوم الكشف تتداخل مع تسوية مسجلة أو تختلف عنها؛ يلزم تحديد الجزء المغطى لمنع التكرار.",
    "provider_statement_refund_alias_review": "ظهر مرجع استرجاع من المصدر يحتاج ربطًا بالدليل المعتمد؛ حُفظ الأصل وأُوقف صافي هذا الطلب حتى المطابقة.",
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
                                 "split_payment": bool(split),
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
        row["native_currency"] = original_currency
        row["native_refunded"] = row["refunded"]
        basis = valuations.get(identity)
        fx = row["fx"]
        if not basis and identity not in claims and fx.get("currency") == original_currency:
            paid = number(fx.get("native", {}).get(row["provider"]))
            sar = number(fx.get("shares", {}).get(row["provider"]))
            if paid == native_paid and paid and sar and fx.get("basis") in {"salla_order_fx", "salla_list_paid"}:
                basis = {"native_paid": str(paid), "sar_paid": str(sar)}
        if original_currency != "SAR" and basis:
            row["fee_fx_basis"] = basis
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


def _unclaimed_same_provider_aliases(state, records, claims):
    """Find cumulative SAR observations that overlap a retained unsplit proof.

    This is a report-only alias, never a claim migration. Allocation identities,
    fee authority and the original records must remain intact. Foreign quotes,
    wallet shares and cross-provider reallocations require their own evidence.
    """
    engine = state.get("engine", {})
    inputs = engine.get("provider_fee_inputs", {}).get("orders", {})
    facts = engine.get("facts", {})
    aliases = {}
    for identity, row in records.items():
        oid, provider = row["order_id"], row["provider"]
        parent = "provider:" + oid
        prior = records.get(parent)
        if (identity != parent + ":" + provider or identity in claims
                or not row.get("split_payment") or not prior or not prior.get("paid_proven")
                or prior["provider"] != provider or "provider_split:" + oid in facts):
            continue
        if any(record["native_currency"] != "SAR" or record["currency"] != "SAR"
               for record in (prior, row)):
            continue
        split = (inputs.get(oid, {}).get("payment") or {}).get("split") or {}
        tenders = split.get("tenders", [])
        if not tenders or any(leg.get("provider") != provider for leg in tenders):
            continue
        aliases[identity] = parent
    return aliases


def provider_balances(state, details, obligations, parties, valuations, *, sar_view=False, paypal_sar=False):
    """Project fees once on the unpaid gross claim. Never change saved financial state."""
    records, claims = _records(state, obligations, valuations)
    aliases = _unclaimed_same_provider_aliases(state, records, claims)
    displayed = {}
    for identity, parent in aliases.items():
        prior, current = records[parent], records[identity]
        # Both describe cumulative principal, not disjoint receipts. Keep the
        # strongest proved amount, without changing the claim used for net fees.
        paid = max(prior["gross"], current["gross"])
        displayed[parent] = (paid, max(prior["refunded"], current["refunded"]))
        displayed[identity] = (ZERO, ZERO)
    allocated, actual_fees, settled_cash = _settlements(state, valuations)
    settings = state.get("engine", {}).get("provider_fee_inputs", {}).get("settings", {})
    policies = state.get("engine", {}).get("provider_fee_inputs", {}).get("policies", [])
    source_mode = state.get('engine', {}).get('provider_fee_inputs', {}).get('source_mode', 'legacy_settings')
    statement_context = prepare_statement_fee_context(state)
    average_blocked = set()
    alias_held = set()
    for issue in state.get("engine", {}).get("issues", []):
        if issue.get("code") == "provider_statement_refund_alias_review":
            oid = str(issue.get("order_id") or str(issue.get("source_id") or "").removeprefix("provider:"))
            alias_held.add((oid, issue.get("provider")))
        if issue.get("code") in {"provider_fee_estimate_unavailable", "provider_payment_plan_required",
                                "provider_payment_method_required_for_fee", "provider_fee_policy_incomplete_or_ambiguous",
                                "provider_net_balance_incomplete"}:
            continue
        source_id = str(issue.get("source_id") or "")
        if issue.get("scope") == "provider_payment" or source_id.startswith("provider:"):
            average_blocked.add(source_id.removeprefix("provider:"))
    groups = {}
    resolved = set()

    def group(currency, provider):
        return groups.setdefault((currency, provider), {"provider": provider, "name": PLATFORMS.get(provider, provider),
            "orders": set(), "gross": ZERO, "fees": ZERO, "refunded": ZERO, "remaining_fees": ZERO,
            "complete": True, "fees_complete": True, "held_orders": 0, "opening": ZERO, "settled": ZERO,
            "known_gross": ZERO, "known_fees": ZERO, "known_net": ZERO,
            "actual_statement_fees": ZERO, "actual_settlement_fees": ZERO,
            "estimated_fees": ZERO, "average_estimated_fees": ZERO,
            "_estimated_calculated_orders": set(), "_estimated_incomplete": defaultdict(set),
            "_estimated_scope_incomplete": False,
            "average_variable_fees": ZERO, "average_fixed_fees": ZERO, "average_vat_fees": ZERO,
            "_average_fixed_complete": True, "_average_vat_complete": True, "_average_complete": True,
            "_average_profiles": {}, "_average_fixed_bases": set(), "_average_operations_bases": set(),
            "_source_average_scopes": {},
            "average_estimated_orders": set(), "plan_estimated_orders": set(),
            "statement_evidence_as_of": None, "_statement_evidence_stamp": None, "_fee_bases": set(),
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

    def incomplete_estimate(total, oid, code):
        # Coverage metadata only: never change money, recognition or review IDs.
        if code == "provider_fee_estimate_unavailable" and any(
                oid in ids for ids in total["_estimated_incomplete"].values()):
            return
        total["_estimated_incomplete"][code].add(oid)

    def average_components(total, row, profile, components):
        if profile is None or components is None:
            return
        total["_fee_bases"].add("average_estimate")
        total["average_estimated_orders"].add(row["order_id"])
        total["_average_profiles"][profile["profile_id"]] = statement_average_summary(profile)
        total["_average_fixed_bases"].add(components["fixed_basis"])
        total["_average_operations_bases"].add(components["operations_basis"])
        total["average_variable_fees"] += components["variable"]
        for field, component, complete in (("average_fixed_fees", "fixed", "_average_fixed_complete"),
                                            ("average_vat_fees", "vat", "_average_vat_complete")):
            if components[component] is None:
                total[complete] = False
            else:
                total[field] += components[component]
        total["_average_complete"] = total["_average_complete"] and components["fee"] is not None
        if components["fee"] is None:
            if components["fixed"] is None:
                incomplete_estimate(total, row["order_id"], "provider_fee_operation_count_missing")
            if components["vat_basis"] == "conflicting_policy":
                incomplete_estimate(total, row["order_id"], "provider_fee_vat_rate_conflict")
            elif components["vat_rate"] is None:
                incomplete_estimate(total, row["order_id"], "provider_fee_vat_rate_missing")
        pending(total, row["order_id"], "provider_fee_average_estimate", row.get("order_number"))

    for identity, row in records.items():
        currency, provider = row["currency"], row["provider"]
        total = group(currency, provider)
        account = (row.get("payment") or {}).get("merchant_account")
        source_scope = row["native_currency"], type(account).__name__, str(account)
        if source_scope not in total["_source_average_scopes"]:
            total["_source_average_scopes"][source_scope] = statement_average_source_summary(
                state, provider, row["native_currency"], payment=row.get("payment"))
        if row["gross"] > 0:
            total["orders"].add(row["order_id"])
            if row["paid_proven"]:
                # Customer paid proof is distinct from accepting the claim
                # against opening/settlement history or proving its net fees.
                total["paid_orders"].add(row["order_id"])
        displayed_gross, displayed_refund = displayed.get(identity, (row["gross"], row["refunded"]))
        total["gross"] += displayed_gross
        total["known_gross"] += displayed_gross
        total["_gross_available"] = total["_gross_available"] or row["paid_proven"]
        if row.get("order_number"):
            total["_order_numbers"][row["order_id"]] = str(row["order_number"])
        total["refunded"] += displayed_refund
        claim = claims.get(identity)
        original = (claim.get("native_balance") or claim) if claim else {}
        held = (not claim or claim["currency"] != currency or claim.get("incomplete")
                or row["native_gross"] > Decimal(original.get("confirmed", "0")) + row["native_refunded"]
                or (row["order_id"], provider) in alias_held)
        if held and row["gross"]:
            total["complete"] = False
            total["_held"].add(row["order_id"])
            incomplete_estimate(total, row["order_id"], "provider_balance_recognition_pending")
        elif row["gross"] > 0:
            total["confirmed_orders"].add(row["order_id"])
        if identity in aliases:
            # The old claim still owns its proved fees and settlements. A full
            # estimate on this held observation would price that portion again;
            # the unaccepted additional amount keeps its fee scope incomplete.
            total["complete"] = total["fees_complete"] = False
            incomplete_estimate(total, row["order_id"], "provider_balance_recognition_pending")
            continue
        projection = details.get("provider_reports", {}).get(identity)
        fee = None
        fee_basis = "unavailable"
        estimate = {}
        profile = select_statement_average_profile(state, provider, row["native_currency"], payment=row.get("payment"))
        applied = allocated[identity]["gross"]
        operations, operations_basis = None, "unavailable"
        captures = statement_context["captures"][row["order_id"], provider]
        if captures and sum(captures.values(), ZERO) == row["native_gross"]:
            operations, operations_basis = len(captures), "capture_facts"
        elif (not captures and not row.get("split_payment") and not applied
                and not statement_context["events"].get((row["order_id"], provider))
                and (row.get("payment") or {}).get("provider") == provider
                and statement_context["pages"].get((row["order_id"], provider)) == row["native_gross"] > ZERO):
            # Cost-only assumption for a proved single-provider paid order.
            # It never asserts an actual capture count or confirms net money.
            operations, operations_basis = 1, "one_provider_order_estimate"
        if projection and not held:
            fee = number(projection.get("estimated_fees_original", projection.get("estimated_fees")))
            # Only paid captures belong in the current balance. Future fees stay
            # inside the future net collection, rather than being charged here.
            projected_gross = number(projection.get("gross"))
            if projected_gross and row["gross"] < projected_gross:
                fee = None
            if fee is not None:
                fee_basis = "plan_estimate"
        if fee is None and not row.get("unresolved_split_fees"):
            estimate = estimate_fee_details(settings, provider, row["gross"], method=row["method"], refunded=row["refunded"],
                policies=policies, payment=row.get("payment"), created_at=row.get("created_at"),
                observed_at=row.get("observed_at"), refunds=row.get('refund_events'), source_mode=source_mode,
                as_of=state.get('engine', {}).get('as_of'), currency=currency,
                native_currency=row["native_currency"], native_gross=row["native_gross"],
                fee_fx_basis=row.get("fee_fx_basis"), started_at=state.get("started_at"),
                average_profile=profile, operations=operations, operations_basis=operations_basis,
                paid_proven=bool(not held and row["paid_proven"] and row["order_id"] not in average_blocked
                                 and identity.removeprefix("provider:") not in average_blocked))
            fee, fee_basis = estimate["fee"], estimate["basis"]
        retained_uncaptured = (claim and claim.get("direction") == "payable"
                               and Decimal(claim.get("expected", "0")) > 0 and row["gross"] == 0)
        if row["gross"] == 0 and not retained_uncaptured:
            fee = ZERO
        if retained_uncaptured:
            fee = None
        if (row["order_id"], provider) in alias_held:
            fee = None
        fully_settled = not held and row["gross"] > 0 and applied >= row["gross"]
        if row["refunded"] and applied > 0 and not fully_settled:
            # A refund overlapping a partial payout needs an actual fee-credit
            # allocation. The reduced principal cannot establish that split.
            fee = None
        if fee_basis == "average_estimate" and applied > 0 and not fully_settled:
            # An allocation has no capture IDs. Neither its fee nor fixed
            # component can safely be apportioned over assumed operations.
            fee, fee_basis, estimate = None, "unavailable", {}
            pending(total, row["order_id"], "provider_statement_fee_settlement_conflict", row.get("order_number"))
            incomplete_estimate(total, row["order_id"], "provider_statement_fee_settlement_conflict")
        if fee is None and fully_settled:
            # The allocation proves its full cash/fee split. A missing estimate
            # cannot turn an entirely settled actual fee back into uncertainty.
            fee = ZERO
            fee_basis = "actual_settlement"
        statement = None if held else resolve_statement_fees(state, identity, row, context=statement_context,
            applied=applied, allocated_fee=allocated[identity]["fees"], estimate=fee, estimate_basis=fee_basis,
            average_profile=profile, average_components=estimate.get("components"))
        if statement is not None:
            accepted = statement_context["latest"].get((row["order_id"], provider))
            if accepted and (total["_statement_evidence_stamp"] is None or accepted[0] > total["_statement_evidence_stamp"]):
                total["_statement_evidence_stamp"], total["statement_evidence_as_of"] = accepted
            if not statement["complete"]:
                if statement["average_used"]:
                    average_components(total, row, profile, statement["components"])
                total["complete"] = total["fees_complete"] = False
                pending(total, row["order_id"], statement["issue"], row.get("order_number"))
                incomplete_estimate(total, row["order_id"], statement["issue"])
                if statement["evidenced"]:
                    total["known_fees"] += statement["actual"]
                    total["actual_statement_fees"] += statement["actual"]
                    total["_fees_available"] = True
                    total["_fee_bases"].add("actual_statement")
                continue
            remaining = statement["actual"] + statement["estimated"]
            average = statement["average"]
            average_used = statement["average_used"]
            estimated = statement["estimated"]
            if statement["evidenced"] and not applied:
                total["_fee_bases"].add("actual_statement")
                total["actual_statement_fees"] += statement["actual"]
            if estimated or average_used:
                total["_fee_bases"].add("average_estimate" if average_used else "plan_estimate")
            if average_used:
                average_components(total, row, profile, statement["components"])
            fee = remaining
        else:
            covered = ZERO
            if fee is not None and not held and row["gross"] > 0 and applied > 0:
                covered = cash(fee * min(applied, row["gross"]) / row["gross"])
            remaining = max(fee - covered, ZERO) if fee is not None else None
            average = remaining or ZERO if fee_basis == "average_estimate" else ZERO
            average_used = fee_basis == "average_estimate" and not fully_settled
            estimated = remaining or ZERO if fee_basis in {"average_estimate", "plan_estimate"} else ZERO
            if average_used and not held:
                average_components(total, row, profile, estimate.get("components"))
            if not held and fee is not None and row["gross"] > 0 and (remaining or not fully_settled):
                total["_fee_bases"].add(fee_basis)
        if fee is None:
            total["complete"] = total["fees_complete"] = False
            pending(total, row["order_id"], "provider_fee_estimate_unavailable", row.get("order_number"))
            incomplete_estimate(total, row["order_id"], "provider_fee_estimate_unavailable")
            continue
        if not held and row["paid_proven"]:
            resolved.add(identity)
            total["_net_available"] = total["_fees_available"] = True
            if row["gross"] > 0 and not average_used:
                total["net_confirmed_orders"].add(row["order_id"])
        total["fees"] += remaining
        if not held:
            total["remaining_fees"] += remaining
            total["known_fees"] += remaining
            total["estimated_fees"] += estimated
            total["average_estimated_fees"] += average
            if (row["gross"] > ZERO and row["paid_proven"] and (average_used or estimated
                    or (statement is None and fee_basis == "plan_estimate" and not fully_settled))):
                total["_estimated_calculated_orders"].add(row["order_id"])
            if average_used:
                total["average_estimated_orders"].add(row["order_id"])
                pending(total, row["order_id"], "provider_fee_average_estimate", row.get("order_number"))
            elif estimated:
                total["plan_estimated_orders"].add(row["order_id"])

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
                incomplete_estimate(total, oid, code)
    for total in groups.values():
        explained = set().union(*(ids for code, ids in total["_pending"].items()
                                  if code != "provider_fee_estimate_unavailable"))
        for oid in total["_held"] - explained:
            pending(total, oid, "provider_balance_recognition_pending")
    if sar_view or paypal_sar:
        coverage = details.get("customer_tender_coverage", {}).get("sar", {})
        for source in coverage.get("by_method", []):
            provider = source.get("method")
            if provider not in PLATFORMS or (not sar_view and provider != "paypal"):
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
                    total["_estimated_scope_incomplete"] = True
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
                incomplete_estimate(total, oid, "provider_balance_recognition_pending")
            total["paid_orders"].update(source_ids)
            total["orders"].update(source_ids)
            if known is not None and known > total["gross"]:
                total["gross"] = known
                total["known_gross"] = known
                total["_gross_available"] = True
                total["complete"] = total["fees_complete"] = False
                total["_estimated_scope_incomplete"] = True
            if paid is None:
                total["_gross_complete"] = False
                total["complete"] = total["fees_complete"] = False
                total["_estimated_scope_incomplete"] = True
                for row in records.values():
                    if row["provider"] == provider and row["currency"] != "SAR":
                        pending(total, row["order_id"], "provider_balance_sar_unavailable", row.get("order_number"))
                        incomplete_estimate(total, row["order_id"], "provider_balance_sar_unavailable")

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
        if party["party_id"] == "paypal":
            offset = min(party["outstanding_receivable"], party["outstanding_payable"])
            party["outstanding_receivable"] -= offset
            party["outstanding_payable"] -= offset
        party["provider_fees_remaining"] = _money(deduction)
        party["provider_net_complete"] = total["complete"]
        total["known_net"] += party["outstanding_receivable"] - party["outstanding_payable"]
    result = defaultdict(list)
    for (currency, provider), total in sorted(groups.items()):
        total["fees"] += actual_fees[currency, provider]
        total["known_fees"] += actual_fees[currency, provider]
        total["actual_settlement_fees"] = actual_fees[currency, provider]
        if actual_fees[currency, provider] or settled_cash[currency, provider]:
            total["_fee_bases"].add("actual_settlement")
        bases = total["_fee_bases"] - {"unavailable"}
        total["fee_basis_sources"] = sorted(bases)
        total["fee_basis"] = next(iter(bases)) if len(bases) == 1 else "mixed" if bases else "unavailable"
        has_estimate = bool(bases & {"plan_estimate", "average_estimate"})
        has_actual = bool(bases & {"actual_statement", "actual_settlement"})
        total["fee_status"] = ("review" if not total["fees_complete"] else
                               "mixed" if has_estimate and has_actual else
                               "estimated" if has_estimate else "confirmed" if has_actual else "unavailable")
        total["average_estimated_orders"] = len(total["average_estimated_orders"])
        total["plan_estimated_orders"] = len(total["plan_estimated_orders"])
        total["average_fee_profiles"] = [total["_average_profiles"][key] for key in sorted(total["_average_profiles"])]
        source_profiles, source_issues = {}, defaultdict(int)
        for source in total["_source_average_scopes"].values():
            if source["available"]:
                source_profiles[source["profile_id"]] = source
            else:
                source_issues[source["native_currency"], source["reason_code"]] += 1
        total["source_average_profiles"] = [source_profiles[key] for key in sorted(source_profiles)]
        total["source_average_issues"] = [{"native_currency": native, "reason_code": reason, "scopes": count}
            for (native, reason), count in sorted(source_issues.items())]
        if not total["_source_average_scopes"]:
            total["source_average_issues"] = [{"native_currency": None, "reason_code": "source_scope_unproven", "scopes": 0}]
        total["source_average_status"] = ("available" if source_profiles and not source_issues else
                                          "partial" if source_profiles else "unavailable")
        total["average_fixed_bases"] = sorted(total["_average_fixed_bases"])
        total["average_operations_bases"] = sorted(total["_average_operations_bases"])
        # Preserve exactly the complete estimates already included in known_fees.
        # Partial variable-only components never enter this subtotal or net money.
        calculated_estimates = total["estimated_fees"]
        if not total["_average_fixed_complete"]:
            total["average_fixed_fees"] = None
        if not total["_average_vat_complete"]:
            total["average_vat_fees"] = None
        if not total["_average_complete"]:
            total["estimated_fees"] = total["average_estimated_fees"] = None
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
        incomplete_ids = set().union(*total["_estimated_incomplete"].values())
        total["estimated_fee_incomplete_orders"] = len(incomplete_ids)
        total["estimated_fee_incomplete_reasons"] = [
            {"code": code, "orders": len(ids)}
            for code, ids in sorted(total["_estimated_incomplete"].items())]
        total["estimated_fee_unidentified_orders"] = total["unidentified_pending_orders"]
        total["estimated_fee_scope_incomplete"] = bool(total["_estimated_scope_incomplete"]
            or total["estimated_fee_unidentified_orders"])
        total["estimated_fees_complete"] = bool(total["fees_complete"] and total["_average_complete"]
            and not incomplete_ids and not total["estimated_fee_scope_incomplete"])
        total["estimated_fee_calculated_orders"] = len(total["_estimated_calculated_orders"])
        total["known_estimated_fees_available"] = bool(total["estimated_fee_calculated_orders"]
            or total["estimated_fees_complete"])
        total["known_estimated_fees"] = (calculated_estimates
            if total["known_estimated_fees_available"] else None)
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
