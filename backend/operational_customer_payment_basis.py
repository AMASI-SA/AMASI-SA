"""Keep proved paid principal separate from later commercial order changes.

This is a pure evidence reader. Refund hints, return status and current order
totals do not execute a refund, create a collection, or establish a fee credit.
The strict general payment normalizer remains the authority for references,
transaction binding and capture dates.
"""
from copy import deepcopy
from decimal import Decimal
from hashlib import sha256
import json

from operational_payment_evidence import (
    _amount, _identity, _instant, _method_rows, _object, _single, resolve_provider,
)


ADJUSTED_PAYMENT_SOURCE = "canonical_salla_adjusted_payment"
ZERO = Decimal(0)
RETAINED_ADJUSTMENT_ISSUES = frozenset({
    "provider_refund_execution_evidence_required", "provider_cancellation_execution_evidence_required",
    "provider_capture_status_conflict", "provider_payment_amount_conflict",
})


def _cash(value):
    return format(value, ".2f")


def is_adjusted_payment_balance(page, currency):
    """A cumulative paid proof with a separately disclosed commercial total."""
    if (not isinstance(page, dict) or page.get("source") != ADJUSTED_PAYMENT_SOURCE
            or page.get("currency") != currency or page.get("remaining") is not None):
        return False
    try:
        _identity(page.get("transaction_reference"), "provider_payment_reference_missing")
        return _amount(page["paid"], currency) > ZERO and _amount(page["gross"], currency) >= ZERO
    except (ValueError, KeyError, TypeError):
        return False


def _refund_observation(raw, payment, refund, currency):
    hints = [_amount(value, currency) for value in (
        raw.get("refunded_amount"), payment.get("refunded_amount"),
        refund.get("refund_amount"), refund.get("pending_refund_amount"),
    ) if value is not None]
    transactions = refund.get("refunded_transactions")
    if transactions is not None:
        if not isinstance(transactions, list) or len(transactions) > 100:
            raise ValueError("provider_payment_shape_invalid")
        amounts = []
        for row in transactions:
            if not isinstance(row, dict):
                raise ValueError("provider_payment_shape_invalid")
            amounts.append(_amount(row.get("amount"), currency))
        hints.append(sum(amounts, ZERO))
    # These fields can describe the same refund. Never add overlapping totals.
    return max(hints, default=ZERO)


def confirmed_paid_basis(raw, currency, total, created_at, as_of, *, owner,
                         order_number, provider_order_id, source, rows=()):
    """Return independent paid evidence, or None if its scope is not proved.

    Amounts are strings in the original currency. Suggestions are observations
    before local confirmed refunds/collections are applied by the caller. This
    function performs no reads or writes and never mutates its arguments.
    """
    from operational_provider_transaction_evidence import normalize_transaction_payment
    from salla_payment_collection import provider_order_page_current

    try:
        # Customer adjustment confirmations currently operate in SAR. Foreign
        # tenders retain their existing FX/closed-tender evidence rules; this
        # workflow must not convert an ambiguous refund into new native money.
        if currency != "SAR":
            return None
        raw, source = _object(raw), _object(source)
        provider = resolve_provider(raw, require_known=True)
        if not provider or not provider_order_page_current(source, raw, owner=owner,
                order_number=order_number, provider_order_id=provider_order_id):
            return None
        commercial = _amount(total, currency)
        amounts = _object(raw.get("amounts"))
        payment = _object(raw.get("payment"))
        actions = _object(raw.get("payment_actions"))
        refund = _object(actions.get("refund_action"))
        remaining = _object(actions.get("remaining_action"))
        totals = [value for value in (raw.get("total"), raw.get("total_amount"), amounts.get("total"))
                  if value is not None]
        if (not totals or any(_amount(value, currency) != commercial for value in totals)
                or any(container.get("currency") not in (None, currency)
                       for container in (raw, amounts, payment, refund, remaining))):
            return None
        paid_values = [_amount(container["paid_amount"], currency)
                       for container in (raw, payment, refund, remaining)
                       if container.get("paid_amount") is not None]
        paid = _single(paid_values, "provider_payment_amount_conflict")
        if paid is None or paid <= ZERO:
            return None
        native = _method_rows(raw, provider, currency, page_balance=False)
        if native and native[0][1] != paid:
            return None
        refs = [_identity(payment[key], "provider_payment_reference_missing")
                for key in ("reference", "transaction_reference") if payment.get(key) is not None]
        refs += [native[0][0]] if native else []
        reference = _single(refs, "provider_payment_reference_conflict")
        if not reference:
            return None
        states = [value for value in (payment.get("status"), payment.get("collection_status"),
                  raw.get("payment_status"), raw.get("payment_collection_status")) if value is not None]
        if any(not isinstance(value, str) for value in states):
            return None
        states = {value.strip().lower() for value in states}
        if states & {"unpaid", "authorized", "authorised", "failed", "declined", "voided",
                     "reversed", "refunded", "cancelled", "canceled", "غير مدفوع"}:
            return None
        complete_capture = (payment.get("status") in {"paid", "captured"}
                            and payment.get("paid_amount") is not None and payment.get("paid_at") is not None)
        # A native paid observation must be tied to its original transaction.
        # A method label or order completion by itself proves no customer cash.
        if not complete_capture and not (native and any(
                container.get("paid_amount") is not None for container in (refund, remaining))):
            return None
        if raw.get("is_pending_payment") is True and commercial <= paid:
            return None
        created, observed, now = (_instant(created_at),
            _instant(raw.get("updated_at") or raw.get("date_updated")), _instant(as_of))
        if not created <= observed <= now:
            return None
        hint = _refund_observation(raw, payment, refund, currency)
        if hint > paid:
            return None
        for container in (raw, payment, remaining):
            if container.get("remaining_amount") is not None:
                _amount(container["remaining_amount"], currency)
            if container.get("has_remaining_amount") is not None and type(container["has_remaining_amount"]) is not bool:
                return None
        adjustment = {
            "order_total": _cash(commercial), "paid_amount": _cash(paid),
            "refund_hint_amount": _cash(hint),
            "suggested_refund_amount": _cash(max(paid - commercial, hint, ZERO)),
            "suggested_collection_amount": _cash(max(commercial - paid, ZERO)),
            "evidence_source": ADJUSTED_PAYMENT_SOURCE,
        }
        proof = {"owner": owner, "order_id": provider_order_id, "order_number": order_number,
                 "provider": provider, "currency": currency, "reference": reference,
                 "source_updated_at": observed.isoformat(), **adjustment}
        proof_hash = sha256(json.dumps(proof, ensure_ascii=False, sort_keys=True,
                                      separators=(",", ":")).encode()).hexdigest()
        # Project only the independent tender into the strict capture reader.
        # The actual order/refund payload is preserved unchanged above and the
        # commercial differences remain explicit in customer_adjustment.
        canonical = deepcopy(raw)
        canonical.pop("status", None)
        canonical.pop("refunded_amount", None)
        canonical.pop("is_pending_payment", None)
        canonical["payment"] = deepcopy(payment)
        canonical["payment"].pop("refunded_amount", None)
        canonical["payment"]["paid_amount"] = _cash(paid)
        canonical["payment_actions"] = {
            "refund_action": {"paid_amount": _cash(paid)},
            "remaining_action": {"paid_amount": _cash(paid), "remaining_amount": "0.00"},
        }
        normalized, issues = normalize_transaction_payment(canonical, currency, _cash(paid), created_at, as_of,
            owner=owner, order_number=order_number, provider_order_id=provider_order_id, rows=rows,
            page_balance={"paid": _cash(paid), "remaining": "0.00"})
        if normalized is None or any(issue.get("code") != "provider_capture_evidence_incomplete" for issue in issues):
            return None
        normalized.update(gross=_cash(paid), customer_adjustment={**adjustment, "proof_hash": proof_hash})
        normalized["order_page_balance"] = {
            "source": ADJUSTED_PAYMENT_SOURCE, "owner_id": owner, "order_id": provider_order_id,
            "order_number": order_number, "provider": provider, "currency": currency,
            "gross": _cash(commercial), "paid": _cash(paid), "remaining": None,
            "transaction_reference": reference, "source_updated_at": observed.isoformat(),
        }
        normalized.pop("order_page_reference", None)
        return {"provider": provider, "currency": currency, **adjustment,
                "payment": normalized, "proof_hash": proof_hash}
    except (ValueError, KeyError, TypeError):
        return None


def retained_confirmed_paid_basis(raw, currency, total, created_at, as_of, *, owner,
                                  order_number, provider_order_id, source, state, rows=()):
    """Read already recognized original money after the source reports a net.

    This fallback is for order lookup/review only. It neither normalizes a new
    source payment nor creates facts. A return status or zero-paid field cannot
    prove the original receipt: its scoped frozen capture/page and provider
    claim must both already exist.
    """
    from salla_payment_collection import provider_order_page_current

    try:
        if currency != "SAR" or state.get("owner_id") != owner:
            return None
        raw, source = _object(raw), _object(source)
        provider = resolve_provider(raw, require_known=True)
        if not provider or not provider_order_page_current(source, raw, owner=owner,
                order_number=order_number, provider_order_id=provider_order_id):
            return None
        created, stamp, now = _instant(created_at), _instant(raw.get("updated_at") or raw.get("date_updated")), _instant(as_of)
        if not created <= stamp <= now:
            return None
        commercial = _amount(total, currency)
        amounts = _object(raw.get("amounts"))
        payment = _object(raw.get("payment"))
        actions = _object(raw.get("payment_actions"))
        refund = _object(actions.get("refund_action"))
        if (any(container.get("currency") not in (None, currency) for container in (raw, amounts, payment, refund))
                or any(_amount(value, currency) != commercial for value in (
                    raw.get("total"), raw.get("total_amount"), amounts.get("total")) if value is not None)):
            return None
        engine = state.get("engine") or {}
        facts = engine.get("facts") or {}
        if "provider_split:" + provider_order_id in facts:
            return None
        claim = (engine.get("obligations") or {}).get("provider:" + provider_order_id)
        if (not isinstance(claim, dict) or (claim.get("party_type"), claim.get("party_id"), claim.get("currency"))
                != ("provider", provider, currency)):
            return None
        frozen = {}
        for key, fact in facts.items():
            if not key.startswith("capture:") or not isinstance(fact, dict) or str(fact.get("order_id")) != provider_order_id:
                continue
            if fact.get("provider") != provider or fact.get("currency", currency) != currency:
                return None
            reference = _identity(key.removeprefix("capture:"), "provider_payment_reference_missing")
            frozen[reference] = _amount(fact["amount"], currency)
        page = facts.get("provider_order_balance:" + provider_order_id)
        page_paid = ZERO
        if page is not None:
            if (not isinstance(page, dict) or (page.get("owner_id"), str(page.get("order_id")),
                    str(page.get("order_number")), page.get("provider"), page.get("currency")) !=
                    (owner, provider_order_id, order_number, provider, currency)):
                return None
            page_paid = _amount(page["paid"], currency)
        paid = max(sum(frozen.values(), ZERO), page_paid)
        if paid <= ZERO:
            return None
        refunded = sum((_amount(fact["amount"], currency) for key, fact in facts.items()
            if key.startswith("refund:" + provider + ":") and isinstance(fact, dict)
            and str(fact.get("order_id")) == provider_order_id), ZERO)
        confirmed = _amount(claim.get("confirmed"), currency)
        if refunded > paid or confirmed not in {paid, paid - refunded}:
            return None
        if claim.get("direction") != "receivable" and not (confirmed == 0 and refunded == paid):
            return None
        native = _method_rows(raw, provider, currency, page_balance=True)
        current_refs = [reference for reference, _, _ in native if reference]
        current_refs += [_identity(payment[key], "provider_payment_reference_missing")
                         for key in ("reference", "transaction_reference") if payment.get(key) is not None]
        references = set(frozen)
        if page and page.get("transaction_reference"):
            references.add(_identity(page["transaction_reference"], "provider_payment_reference_missing"))
        if any(reference not in references for reference in current_refs):
            return None
        hint = _refund_observation(raw, payment, refund, currency)
        if hint > paid:
            return None
        adjustment = {"order_total": _cash(commercial), "paid_amount": _cash(paid),
            "refund_hint_amount": _cash(hint), "suggested_refund_amount": _cash(max(paid - commercial, hint, ZERO)),
            "suggested_collection_amount": _cash(max(commercial - paid, ZERO)),
            "evidence_source": "retained_operational_payment"}
        proof = {"owner": owner, "order_id": provider_order_id, "order_number": order_number,
                 "provider": provider, "currency": currency, "source_updated_at": stamp.isoformat(),
                 "captures": sorted((key, _cash(value)) for key, value in frozen.items()),
                 "page": page, "claim": {key: claim.get(key) for key in
                     ("party_type", "party_id", "currency", "confirmed", "direction")}, **adjustment}
        proof_hash = sha256(json.dumps(proof, ensure_ascii=False, sort_keys=True,
                                      separators=(",", ":")).encode()).hexdigest()
        retained = {"provider": provider, "currency": currency, "gross": _cash(paid), "cancelled": "0",
                    "refunds": [], "retained_confirmed_payment": True,
                    "customer_adjustment": {**adjustment, "proof_hash": proof_hash}}
        previous = ((engine.get("provider_fee_inputs") or {}).get("orders", {}).get(provider_order_id) or {}).get("payment") or {}
        if previous.get("provider") == provider and previous.get("currency", currency) == currency:
            for key in ("payment_method", "plan_id", "merchant_account"):
                if key in previous:
                    retained[key] = deepcopy(previous[key])
            captures = previous.get("captures") or []
            if captures and all(str(capture.get("id")) in frozen
                    and _amount(capture["amount"], currency) == frozen[str(capture["id"])]
                    and created <= _instant(capture["captured_at"]) <= now for capture in captures):
                retained["captures"] = deepcopy(captures)
        if page:
            retained["order_page_balance"] = deepcopy(page)
        return {"provider": provider, "currency": currency, **adjustment,
                "payment": retained, "proof_hash": proof_hash}
    except (ValueError, KeyError, TypeError):
        return None


def retained_adjustment_observation(raw, currency, total, created_at, as_of, *, owner,
                                    order_number, provider_order_id, source, state, issues, rows=()):
    """Refresh terminal zero-paid review metadata; never return a payment candidate."""
    try:
        if not issues or any(issue.get("code") not in RETAINED_ADJUSTMENT_ISSUES for issue in issues):
            return None
        payment = _object(raw.get("payment"))
        actions = _object(raw.get("payment_actions"))
        containers = (raw, payment, _object(actions.get("refund_action")), _object(actions.get("remaining_action")))
        values = [_amount(row["paid_amount"], currency) for row in containers if row.get("paid_amount") is not None]
        status = raw.get("status")
        status = status.get("slug") or status.get("name") if isinstance(status, dict) else status
        states = {str(value).strip().lower() for value in (status, payment.get("status"), raw.get("payment_status"))}
        terminal = {"refunded", "restored", "مسترجع", "cancelled", "canceled", "ملغي", "ملغى", "reversed", "voided"}
        if (not values or any(value != ZERO for value in values) or not states.intersection(terminal)
                or any(row.get("currency") not in (None, currency) for row in containers)):
            return None
        basis = retained_confirmed_paid_basis(raw, currency, total, created_at, as_of,
            owner=owner, order_number=order_number, provider_order_id=provider_order_id,
            source=source, state=state, rows=rows)
        if basis is None:
            return None
        engine = state.get("engine") or {}
        previous = (engine.get("provider_fee_inputs") or {}).get("orders", {}).get(provider_order_id) or {}
        saved = previous.get("payment") or {}
        if (previous.get("currency") != currency or saved.get("provider") != basis["provider"]
                or saved.get("split") or previous.get("order_number") != order_number):
            return None
        stamp = _instant(raw.get("updated_at") or raw.get("date_updated"))
        if stamp <= _instant(previous.get("updated_at")):
            return None
        version = (engine.get("order_versions") or {}).get(provider_order_id)
        if version and stamp < _instant(version):
            return None
        prior = previous.get("retained_customer_adjustment") or {}
        if prior:
            prior_stamp = _instant(prior.get("source_updated_at"))
            if stamp < prior_stamp or (stamp == prior_stamp and any(
                    prior.get("customer_adjustment", {}).get(key) != basis[key]
                    for key in ("order_total", "refund_hint_amount"))):
                return None
        return {"order_id": provider_order_id, "order_number": order_number, "provider": basis["provider"],
            "currency": currency, "source_updated_at": stamp.isoformat(), "proof_hash": basis["proof_hash"],
            "issue_codes": sorted({issue["code"] for issue in issues}),
            "customer_adjustment": deepcopy(basis["payment"]["customer_adjustment"])}
    except (ValueError, KeyError, TypeError):
        return None


def is_retained_adjustment_issue(state, issue):
    """Only the exact retained report observation is informational on the card."""
    proof = issue.get("customer_adjustment_proof")
    if not proof or issue.get("code") not in RETAINED_ADJUSTMENT_ISSUES:
        return False
    oid = str(issue.get("source_id") or "").removeprefix("provider:")
    saved = state.get("engine", {}).get("provider_fee_inputs", {}).get("orders", {}).get(oid) or {}
    observed = saved.get("retained_customer_adjustment") or {}
    payment = saved.get("payment") or {}
    return bool(observed.get("proof_hash") == proof
        and observed.get("order_id") == oid and observed.get("currency") == issue.get("currency") == saved.get("currency")
        and observed.get("provider") == issue.get("provider") == payment.get("provider")
        and issue.get("code") in observed.get("issue_codes", ())
        and payment.get("customer_adjustment") == observed.get("customer_adjustment"))
