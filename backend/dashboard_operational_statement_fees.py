"""Read-only dashboard projection of already accepted operational fee evidence.

An immutable report state supplies the same order, claim and covered-event
checks used by operational balances. Raw statement rows never authorize an
order fee. Amounts remain in native currency; the dashboard owns conversion.
"""
from collections import defaultdict
from datetime import date
from decimal import Decimal, InvalidOperation

from operational_provider_statement_fees import (
    _check_event, prepare_statement_fee_context,
)
from provider_fee_weekly_average import RIYADH, _instant, _report_boundary

MAX_COHORT_ORDERS = 20000
MAX_ACCEPTED_EVENTS = 100000
ZERO = Decimal("0.00")
REVIEW = "provider_statement_fee_review_required"


def _unavailable(reason, **details):
    return {"available": False, "reason_code": reason, "plan_id": None, **details}


def _money(value):
    return format(value, ".2f")


def _native_reference(value):
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        return None
    text = str(value)
    if (not text or text != text.strip() or len(text) > 256
            or any(ord(char) < 32 or ord(char) == 127 for char in text)
            or text.startswith("statement:")):
        # Statement-only refund identities are generated bookkeeping keys,
        # not the provider's subsequently observed transaction reference.
        return None
    return text


def _snapshot_references(context, order_id, provider):
    """Return native references from the already validated principal basis.

    The engine writes capture:<normalized capture.id> and
    refund:<provider>:<normalized refund.id> without reinterpreting their IDs.
    A cumulative page fact has no capture ID; only its explicit reference may
    be used. Accepted statement event keys and order IDs are never aliases.
    """
    captures = context["captures"][order_id, provider]
    capture_refs = {_native_reference(key[len("capture:"):]) for key in captures}
    if not captures:
        page = context["facts"].get("provider_order_balance:" + order_id) or {}
        if page.get("provider") == provider and str(page.get("order_id")) == order_id:
            capture_refs.add(_native_reference(page.get("transaction_reference")))
    prefix = "refund:" + provider + ":"
    refund_refs = {_native_reference(key[len(prefix):])
                   for key in context["refunds"][order_id, provider]}
    # The caller compares complete known reference sets. A native subset plus
    # an unidentified statement-owned refund must not look like the full set.
    return (sorted(capture_refs) if None not in capture_refs else [],
            sorted(refund_refs) if None not in refund_refs else [])


def _plan(checked):
    values = [item["event"].get("source", {}).get("plan_id") for item in checked
              if item["event"]["kind"] == "capture"
              and item["event"].get("source", {}).get("plan_id") not in (None, "")]
    if any(not isinstance(value, str) or value != value.strip() for value in values):
        return None, "provider_statement_plan_invalid"
    values = set(values)
    if len(values) > 1:
        return None, "provider_statement_plan_ambiguous"
    return (next(iter(values)), None) if values else (None, None)


def _project_order(state, order_id, order, events, context):
    checked, validated, by_key, by_cover = [], [], {}, {}
    try:
        payment = order.get("payment") or {}
        if payment.get("split"):
            return _unavailable("provider_split_payment_review_required")
        providers = {event.get("provider") for event in events}
        if len(providers) != 1 or payment.get("provider") not in providers:
            return _unavailable("provider_statement_fee_provider_conflict")
        provider = next(iter(providers))
        for issue in state.get("engine", {}).get("issues", []):
            if (issue.get("code") == "provider_statement_refund_alias_review"
                    and issue.get("provider") == provider
                    and str(issue.get("order_id") or str(issue.get("source_id") or "").removeprefix("provider:")) == order_id):
                return _unavailable("provider_statement_refund_alias_review")
        currency = order.get("currency")
        for event in events:
            item = _check_event(state, event, context)
            validated.append(item)
            if event["native_currency"] != currency:
                return _unavailable("provider_statement_fee_currency_conflict")
            economic = (event["kind"], item["principal"], Decimal(event["fee_net"]),
                        Decimal(event["fee_vat"]), item["fee"], item["cover"],
                        event.get("event_date"), event.get("date_precision"))
            if event["event_key"] in by_key and by_key[event["event_key"]] != economic:
                return _unavailable(REVIEW)
            by_key[event["event_key"]] = economic
            if item["cover"] in by_cover:
                if by_cover[item["cover"]] != economic:
                    return _unavailable(REVIEW)
                continue
            by_cover[item["cover"]] = economic
            checked.append(item)
        plan_id, plan_issue = _plan(validated)
        captured = max(sum(context["captures"][order_id, provider].values(), ZERO),
                       context["pages"].get((order_id, provider), ZERO))
        refunded = sum(context["refunds"][order_id, provider].values(), ZERO)
        captures = [item for item in checked if item["event"]["kind"] == "capture"]
        refunds = [item for item in checked if item["event"]["kind"] == "refund"]
        capture_amount = sum((item["principal"] for item in captures), ZERO)
        refund_amount = sum((item["principal"] for item in refunds), ZERO)
        if (capture_amount > captured or refund_amount > refunded
                or any(item["cover"][1].startswith("provider_order_balance:") for item in captures)
                and len(captures) != 1):
            return _unavailable(REVIEW)
        capture_refs, refund_refs = _snapshot_references(context, order_id, provider)
        details = {"provider": provider, "native_currency": currency, "order_id": order_id,
            "merchant_account": payment.get("merchant_account"),
            "plan_id": plan_id, "plan_issue": plan_issue,
            "principal_amount": _money(captured), "refunded_amount": _money(refunded),
            "covered_principal_amount": _money(capture_amount),
            "covered_refunded_amount": _money(refund_amount),
            "capture_references": capture_refs, "refund_references": refund_refs,
            "source_event_keys": sorted(by_key), "source_event_count": len(checked),
            "evidence_as_of": max((item["event"]["accepted_at"] for item in checked), key=_instant),
            "snapshot_as_of": state["engine"]["as_of"], "started_at": state["started_at"]}
        if captured <= ZERO or capture_amount != captured or refund_amount != refunded:
            # A proved plan remains useful to the existing dated calculator;
            # a partial actual amount is never presented as the full order fee.
            return _unavailable("provider_statement_fee_coverage_incomplete", **details)
        net = sum((Decimal(item["event"]["fee_net"]) for item in checked), ZERO)
        vat = sum((Decimal(item["event"]["fee_vat"]) for item in checked), ZERO)
        return {"available": True, "reason_code": None, **details,
                "fee_net": _money(net), "fee_vat": _money(vat), "fee_total": _money(net + vat)}
    except (ValueError, TypeError, KeyError, InvalidOperation, OverflowError) as exc:
        code = str(exc)
        return _unavailable(code if code.startswith("provider_statement_fee_") else REVIEW)


def build_operational_statement_fee_index(owner, state, order_numbers, *, accepted_as_of):
    """Project the selected cohort from a validated, unpacked report state.

    Canonical accepted events must exactly match the report's frozen copies.
    Later acceptance may restate an older dashboard cohort; evidence beyond
    ``accepted_as_of`` never contributes. A missing key means no accepted fee
    proof; an unavailable key is a known validation or coverage issue.
    """
    numbers = {str(value) for value in order_numbers if value is not None}
    if not numbers:
        return {}
    def rejected(reason):
        return {number: _unavailable(reason) for number in sorted(numbers)}
    if len(numbers) > MAX_COHORT_ORDERS:
        return rejected("provider_statement_fee_scope_limit")
    try:
        _, acceptance_limit = _report_boundary(accepted_as_of)
        if (not owner or state.get("owner_id") != owner or state.get("_id") != owner
                or state.get("status") != "active"):
            return rejected("provider_statement_fee_source_invalid")
        engine = state.get("engine") or {}
        if _instant(engine.get("as_of")) > acceptance_limit:
            return rejected("provider_statement_fee_source_future")
        inputs = engine.get("provider_fee_inputs") or {}
        orders = inputs.get("orders") or {}
        canonical = (state.get("provider_statement_reconciliation") or {}).get("events") or {}
        frozen = inputs.get("statement_events") or []
        if (not isinstance(orders, dict) or not isinstance(canonical, dict)
                or not isinstance(frozen, list) or len(canonical) > MAX_ACCEPTED_EVENTS
                or len(frozen) > MAX_ACCEPTED_EVENTS):
            return rejected("provider_statement_fee_source_invalid")
        by_number = defaultdict(list)
        for identity, order in orders.items():
            if isinstance(order, dict) and str(order.get("order_number")) in numbers:
                by_number[str(order["order_number"])].append(str(identity))
        wanted_ids = {identity for ids in by_number.values() for identity in ids}
        frozen_by_key = defaultdict(list)
        for event in frozen:
            if isinstance(event, dict) and str(event.get("order_id")) in wanted_ids:
                frozen_by_key[event.get("event_key")].append(event)
        events, failures = defaultdict(list), {}
        for key, event in canonical.items():
            if not isinstance(event, dict) or str(event.get("order_id")) not in wanted_ids:
                continue
            oid = str(event["order_id"])
            if _instant(event.get("accepted_at")) > acceptance_limit:
                continue
            if key != event.get("event_key") or frozen_by_key.get(key) != [event]:
                failures[oid] = "provider_statement_fee_evidence_changed"
                continue
            event_date = event.get("event_date")
            if event_date:
                if len(str(event_date)) > 10:
                    future = _instant(event_date) > acceptance_limit
                else:
                    future = date.fromisoformat(event_date) > acceptance_limit.astimezone(RIYADH).date()
                if future:
                    failures[oid] = "provider_statement_fee_source_future"
                    continue
            events[oid].append(event)
        # A frozen event without its canonical acceptance cannot be trusted.
        for key, copies in frozen_by_key.items():
            for event in copies:
                if _instant(event.get("accepted_at")) <= acceptance_limit and canonical.get(key) != event:
                    failures[str(event["order_id"])] = "provider_statement_fee_evidence_changed"
        context = prepare_statement_fee_context(state)
        result = {}
        for number, identities in sorted(by_number.items()):
            if not any(identity in events or identity in failures for identity in identities):
                continue
            if len(identities) != 1:
                result[number] = _unavailable("provider_statement_fee_order_ambiguous")
                continue
            oid = identities[0]
            result[number] = (_unavailable(failures[oid]) if oid in failures else
                              _project_order(state, oid, orders[oid], events[oid], context))
        return result
    except (ValueError, TypeError, KeyError, AttributeError, OverflowError):
        return rejected("provider_statement_fee_source_invalid")
