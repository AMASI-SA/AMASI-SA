"""Pure fee-only projection of accepted statement events over a saved snapshot.

Raw imports are never a fee source here. Accepted evidence cannot create a
capture, a refund, a settlement or bank cash, and is rechecked on every read.
"""
from collections import defaultdict
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from hashlib import sha256
import json
import re

CENT = Decimal(".01")
ZERO = Decimal(0)
LIMIT = Decimal("999999999999.99")
TABBY_AVERAGE_INCLUSIVE_RATE = Decimal(".091")
PROVIDERS = frozenset(("tabby", "tamara", "salla"))
REVIEW = "provider_statement_fee_review_required"
RIYADH = timezone(timedelta(hours=3))


def statement_evidence_fingerprint(value):
    return sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                             separators=(",", ":"), default=str).encode()).hexdigest()


def _cash(value):
    if value is None or isinstance(value, bool):
        raise ValueError(REVIEW)
    try:
        amount = Decimal(str(value))
        if not amount.is_finite() or abs(amount) > LIMIT or amount != amount.quantize(CENT):
            raise ValueError(REVIEW)
        return amount
    except (InvalidOperation, TypeError, ValueError):
        raise ValueError(REVIEW) from None


def _positive(value):
    amount = _cash(value)
    if amount <= ZERO:
        raise ValueError(REVIEW)
    return amount


def _stamp(value):
    stamp = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
    if stamp.tzinfo is None:
        raise ValueError(REVIEW)
    return stamp


def freeze_statement_fee_events(state, *, accepted_at=None):
    """Freeze accepted evidence in the existing fee inputs in the caller's CAS.

    The source snapshot's as_of, principal, movements and refund state are not
    changed. The review service remains the sole writer of accepted events.
    """
    canonical = state.get("provider_statement_reconciliation", {}).get("events", {})
    if not isinstance(canonical, dict):
        raise ValueError(REVIEW)
    events = [deepcopy(canonical[key]) for key in sorted(canonical)]
    inputs = state.setdefault("engine", {}).setdefault("provider_fee_inputs", {})
    inputs["statement_events"] = events
    stamps = [event.get("accepted_at") for event in events if isinstance(event, dict)]
    if accepted_at is not None:
        stamps.append(accepted_at)
    if stamps:
        inputs["statement_evidence_as_of"] = max(stamps, key=_stamp)
    else:
        inputs.pop("statement_evidence_as_of", None)


def prepare_statement_fee_context(state):
    """Index once for a whole upload/report, not once per statement row."""
    engine = state.get("engine", {})
    inputs = engine.get("provider_fee_inputs", {})
    context = {"orders": inputs.get("orders", {}), "facts": engine.get("facts", {}),
               "movements": {str(row.get("id")): row for row in state.get("movements", [])},
               "returns": {str(row.get("id")): row for row in state.get("customer_returns", [])},
               "captures": defaultdict(dict), "refunds": defaultdict(dict), "pages": {},
               "events": defaultdict(list), "latest": {},
               "allocations": defaultdict(lambda: {"gross": ZERO, "fees": ZERO}),
               "unallocated_fee_scopes": set()}
    for key, fact in context["facts"].items():
        if not isinstance(fact, dict):
            continue
        oid = str(fact.get("order_id") or "")
        try:
            if key.startswith("capture:") and oid and fact.get("provider") in PROVIDERS:
                context["captures"][oid, fact["provider"]][key] = _positive(fact.get("amount"))
            elif key.startswith("refund:") and oid and key.split(":", 2)[1] in PROVIDERS:
                context["refunds"][oid, key.split(":", 2)[1]][key] = _positive(fact.get("amount"))
            elif key == "provider_order_balance:" + oid and fact.get("provider") in PROVIDERS:
                context["pages"][oid, fact["provider"]] = _positive(fact.get("paid"))
        except ValueError:
            continue
    for event in inputs.get("statement_events", []):
        if isinstance(event, dict):
            pair = str(event.get("order_id") or ""), event.get("provider")
            context["events"][pair].append(event)
            try:
                stamp = _stamp(event.get("accepted_at"))
                if pair not in context["latest"] or stamp > context["latest"][pair][0]:
                    context["latest"][pair] = stamp, event["accepted_at"]
            except ValueError:
                pass
    for movement in context["movements"].values():
        if (movement.get("party_type") != "provider" or movement.get("party_id") not in PROVIDERS
                or movement.get("kind") == "correction" or movement.get("direction") != "incoming"):
            continue
        scope = movement.get("currency"), movement["party_id"]
        try:
            received, actual = _cash(movement.get("amount")), _cash(movement.get("actual_fee_amount", "0"))
            total = received + actual
            if received < ZERO or actual < ZERO or total <= ZERO:
                raise ValueError(REVIEW)
            cumulative = previous = assigned = ZERO
            for line in movement.get("allocations", []):
                value = _positive(line.get("amount"))
                cumulative += value
                boundary = (received * cumulative / total).quantize(CENT, rounding=ROUND_HALF_UP)
                fee = value - (boundary - previous)
                previous = boundary
                assigned += fee
                allocation = context["allocations"][line["obligation_id"], *scope]
                allocation["gross"] += value
                allocation["fees"] += fee
            if cumulative > total or actual != assigned:
                context["unallocated_fee_scopes"].add(scope)
        except (ValueError, TypeError, KeyError, InvalidOperation):
            context["unallocated_fee_scopes"].add(scope)
    return context


def _native_totals(context, oid, provider):
    captured = sum(context["captures"][oid, provider].values(), ZERO)
    return (max(captured, context["pages"].get((oid, provider), ZERO)),
            sum(context["refunds"][oid, provider].values(), ZERO))


def _movement_fee(movement, identity, principal):
    """Use the same cumulative-cent allocation as the existing settlement."""
    received = _cash(movement.get("amount"))
    actual = _cash(movement.get("actual_fee_amount", "0"))
    gross = received + actual
    if received < ZERO or actual < ZERO or gross <= ZERO:
        raise ValueError(REVIEW)
    cumulative = previous = assigned = fee = ZERO
    for line in movement.get("allocations", []):
        value = _positive(line.get("amount"))
        cumulative += value
        boundary = (received * cumulative / gross).quantize(CENT, rounding=ROUND_HALF_UP)
        if line.get("obligation_id") == identity:
            assigned += value
            fee += value - (boundary - previous)
        previous = boundary
    if cumulative > gross or assigned != principal:
        raise ValueError(REVIEW)
    return fee


def _check_event(state, event, context):
    if not isinstance(event, dict):
        raise ValueError(REVIEW)
    provider, oid = event.get("provider"), str(event.get("order_id") or "")
    kind, currency = event.get("kind"), event.get("native_currency")
    if (provider not in PROVIDERS or not oid or kind not in {"capture", "refund"}
            or not isinstance(event.get("event_key"), str) or not event["event_key"].strip()
            or event.get("confirmation") not in {"exact_match", "owner_approved"}
            or not isinstance(currency, str) or not re.fullmatch(r"[A-Z]{3}", currency)):
        raise ValueError(REVIEW)
    principal = _positive(event.get("principal_amount"))
    net, vat, fee = (_cash(event.get(key)) for key in ("fee_net", "fee_vat", "fee_total"))
    if net + vat != fee or (kind == "capture" and min(net, vat, fee) < ZERO) or (kind == "refund" and max(net, vat, fee) > ZERO):
        raise ValueError(REVIEW)
    source = event.get("source") or {}
    if (not isinstance(source, dict) or not source.get("statement_ref")
            or any(not isinstance(source.get(key), str) or not re.fullmatch(r"[a-f0-9]{64}", source[key])
                   for key in ("file_hash", "row_fingerprint"))):
        raise ValueError(REVIEW)
    accepted_day = _stamp(event.get("accepted_at")).astimezone(RIYADH).date()
    as_of_day = _stamp(state.get("engine", {}).get("as_of")).astimezone(RIYADH).date()
    start_day = _stamp(state.get("started_at")).astimezone(RIYADH).date()
    interval = event.get("date_precision") == "snapshot_interval" and event.get("event_date") is None
    event_day = None if interval else date.fromisoformat(str(event.get("event_date") or "")[:10])
    if not interval and (event.get("date_precision") not in {"day", "instant"}
                         or not start_day <= event_day <= min(accepted_day, as_of_day)):
        raise ValueError(REVIEW)
    order = context["orders"].get(oid)
    if not isinstance(order, dict) or order.get("currency") != currency:
        raise ValueError("provider_statement_fee_currency_conflict")
    paid, refunded = _native_totals(context, oid, provider)
    payment = order.get("payment") or {}
    suffix = ":" + provider if payment.get("split") else ""
    claim = state.get("engine", {}).get("obligations", {}).get("provider:" + oid + suffix, {})
    native_claim = claim.get("native_balance") or claim
    if (not claim or claim.get("incomplete") or claim.get("party_id") != provider
            or native_claim.get("currency") != currency
            or paid > _cash(native_claim.get("confirmed")) + refunded):
        raise ValueError("provider_statement_fee_principal_unconfirmed")
    if principal > (paid if kind == "capture" else refunded):
        raise ValueError("provider_statement_fee_principal_unconfirmed")
    allocation = context["allocations"]["provider:" + oid + suffix, currency, provider]
    if ((currency, provider) in context["unallocated_fee_scopes"] or allocation["gross"] and (
            kind != "capture" or refunded or principal != paid or allocation["gross"] != paid
            or allocation["fees"] != fee)):
        raise ValueError("provider_statement_fee_settlement_conflict")
    cover = event.get("covered_event") or {}
    if (not isinstance(cover, dict) or cover.get("financial_effect") not in {"fee_only", "operational_fee"}
            or _positive(cover.get("amount")) != principal):
        raise ValueError(REVIEW)
    cover_kind, identity = cover.get("kind"), str(cover.get("id") or "")
    if interval and (kind != "capture" or cover_kind != "fact"
                     or identity != "provider_order_balance:" + oid
                     or context["captures"][oid, provider] or principal != paid):
        raise ValueError(REVIEW)
    canonical = cover_kind, identity
    if cover_kind == "fact":
        saved = context["facts"].get(identity)
        if not isinstance(saved, dict) or str(saved.get("order_id") or "") != oid:
            raise ValueError(REVIEW)
        if kind == "capture":
            if saved.get("provider") != provider:
                raise ValueError(REVIEW)
            if identity.startswith("capture:"):
                value = _positive(saved.get("amount"))
            elif identity == "provider_order_balance:" + oid and saved.get("currency") == currency:
                value = _positive(saved.get("paid"))
            else:
                raise ValueError(REVIEW)
        elif identity.startswith("refund:" + provider + ":"):
            value = _positive(saved.get("amount"))
        else:
            raise ValueError(REVIEW)
        if value != principal or saved.get("currency", currency) != currency or cover["financial_effect"] != "fee_only":
            raise ValueError(REVIEW)
    elif cover_kind == "customer_return":
        saved = context["returns"].get(identity)
        if (kind != "refund" or not saved or saved.get("status") not in {"refunded", "salla_refund_confirmed"}
                or str(saved.get("order_id")) != oid or saved.get("currency") != currency
                or saved.get("refund_source_type") != "provider" or saved.get("refund_source_id") != provider
                or _positive(saved.get("amount")) != principal or cover["financial_effect"] != "fee_only"):
            raise ValueError(REVIEW)
        refund_key = "refund:" + provider + ":" + str(saved.get("refund_reference") or "")
        if context["refunds"][oid, provider].get(refund_key) != principal:
            raise ValueError("provider_statement_fee_principal_unconfirmed")
        canonical = "fact", refund_key
    elif cover_kind == "movement":
        saved = context["movements"].get(identity)
        if (not saved or kind != "capture" or saved.get("kind") == "correction"
                or saved.get("party_type") != "provider" or saved.get("party_id") != provider
                or saved.get("currency") != currency or saved.get("direction") != "incoming"
                or cover["financial_effect"] != "operational_fee"):
            raise ValueError(REVIEW)
        if _movement_fee(saved, "provider:" + oid + suffix, principal) != fee:
            raise ValueError("provider_statement_fee_settlement_conflict")
    else:
        raise ValueError(REVIEW)
    if statement_evidence_fingerprint(saved) != cover.get("fingerprint"):
        raise ValueError("provider_statement_fee_evidence_changed")
    return {"event": event, "principal": principal, "fee": fee, "cover": canonical,
            "operational": cover["financial_effect"] == "operational_fee"}


def statement_event_issue(state, event, *, context=None):
    try:
        _check_event(state, event, context if context is not None else prepare_statement_fee_context(state))
    except (ValueError, TypeError, KeyError, InvalidOperation, OverflowError) as exc:
        code = str(exc)
        return code if code.startswith("provider_statement_fee_") else REVIEW
    return None


def statement_event_issues(state, events):
    context = prepare_statement_fee_context(state)
    return [statement_event_issue(state, event, context=context) for event in events]


def resolve_statement_fees(state, identity, record, *, context, applied, allocated_fee,
                           estimate, estimate_basis):
    """Return remaining fee parts, or a review result for unsafe overlap.

    A partial known-plan estimate has per-order fixed-fee ambiguity. Only the
    pure percentage average can price disjoint, individually proved captures.
    """
    events = context["events"].get((record["order_id"], record["provider"]), [])
    if not events:
        return None
    result = {"actual": ZERO, "estimated": ZERO, "average": ZERO, "complete": False,
              "issue": REVIEW, "evidenced": False, "average_used": False}
    checked, by_key, by_cover = [], {}, {}
    try:
        for event in events:
            item = _check_event(state, event, context)
            if event["native_currency"] != record["native_currency"]:
                raise ValueError("provider_statement_fee_currency_conflict")
            # A re-export with the same economic event never contributes twice.
            economic = (event["kind"], item["principal"], _cash(event["fee_net"]), _cash(event["fee_vat"]),
                        item["fee"], item["cover"], event.get("event_date"), event.get("date_precision"))
            if event["event_key"] in by_key and by_key[event["event_key"]] != economic:
                raise ValueError(REVIEW)
            if item["cover"] in by_cover:
                if by_cover[item["cover"]] != economic:
                    raise ValueError(REVIEW)
                continue
            by_key[event["event_key"]] = by_cover[item["cover"]] = economic
            checked.append(item)
        captures = [item for item in checked if item["event"]["kind"] == "capture"]
        refunds = [item for item in checked if item["event"]["kind"] == "refund"]
        capture_amount = sum((item["principal"] for item in captures), ZERO)
        refund_amount = sum((item["principal"] for item in refunds), ZERO)
        if capture_amount > record["native_gross"] or refund_amount > record["native_refunded"]:
            raise ValueError(REVIEW)
        if any(item["cover"][1].startswith("provider_order_balance:") for item in captures) and len(captures) != 1:
            raise ValueError(REVIEW)
        actual = sum((item["fee"] for item in checked), ZERO)
        # Convert only after native-currency identity and native coverage pass.
        def display(value):
            if record["currency"] == record["native_currency"]:
                return value
            if record.get("fee_fx_basis") is None:
                raise ValueError("provider_statement_fee_currency_conflict")
            from operational_provider_balance_sar import sar_amount
            return sar_amount(value, record["fee_fx_basis"])
        full = capture_amount == record["native_gross"] and refund_amount == record["native_refunded"]
        if applied:
            # A payout allocation has no capture IDs. Do not infer which
            # partial capture it covered, or add its fee a second time.
            if not full or applied < record["gross"] or display(actual) != allocated_fee:
                raise ValueError("provider_statement_fee_settlement_conflict")
            return {**result, "complete": True, "issue": None, "evidenced": True}
        if any(item["operational"] for item in checked):
            raise ValueError("provider_statement_fee_settlement_conflict")
        result.update(actual=display(actual), evidenced=True)
        if full:
            return {**result, "complete": True, "issue": None}
        all_captures = context["captures"][record["order_id"], record["provider"]]
        disjoint = (sum(all_captures.values(), ZERO) == record["native_gross"]
                    and all(item["cover"][0] == "fact" and item["cover"][1] in all_captures for item in captures))
        if (not record["native_refunded"] and not refunds and disjoint and capture_amount > ZERO
                and estimate is not None and estimate_basis == "average_estimate"
                and record["native_currency"] == "SAR"):
            remaining = ((record["native_gross"] - capture_amount) * TABBY_AVERAGE_INCLUSIVE_RATE).quantize(CENT, rounding=ROUND_HALF_UP)
            return {**result, "estimated": remaining, "average": remaining,
                    "average_used": True, "complete": True, "issue": None}
        return result
    except (ValueError, TypeError, KeyError, InvalidOperation, OverflowError) as exc:
        code = str(exc)
        return {**result, "actual": ZERO, "evidenced": False,
                "issue": code if code.startswith("provider_statement_fee_") else REVIEW}
