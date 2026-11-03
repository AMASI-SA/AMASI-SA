"""Pure bridge for owner-scoped observations from Salla's authenticated /transactions.

Legacy display rows flattened timestamps/statuses and defaulted missing currency to
SAR. They are not financial evidence. No network call or data mutation here.
"""
from collections import defaultdict

from operational_payment_evidence import (
    PaymentEvidenceError, _amount, _identity, _instant, _method_rows, _single,
    normalize_payment, provider_for,
)

COLLECTION = "order_payment_transactions_v2"
VERSION = "salla_transaction_evidence_v1"


def native_transaction_evidence(row, provider_order_id):
    """Preserve only financial source attributes, never card or customer fields."""
    try:
        refs = row.get("references")
        if not isinstance(refs, dict) or _identity(refs.get("order_id"), "order") != provider_order_id:
            return None
        method, status, total = row.get("payment_method"), row.get("status"), row.get("total")
        provider = provider_for(method.get("slug")) if isinstance(method, dict) else None
        slug = status.get("slug") if isinstance(status, dict) else None
        if not provider or not isinstance(slug, str) or not slug or not isinstance(total, dict):
            return None
        ccy = total.get("currency")
        if not isinstance(ccy, str) or len(ccy) != 3 or not ccy.isascii() or not ccy.isalpha() or ccy.upper() != ccy:
            return None
        if any(row.get(f) not in (None, "", "capture", "sale", "payment", "purchase") for f in ("type", "operation", "transaction_type")):
            slug = "unverified_transaction_operation"
        stamps = [_instant(row[k]).isoformat() for k in ("paid_at", "captured_at") if row.get(k) is not None]
        stamp = _single(stamps, "provider_capture_timestamp_conflict")
        amount = _amount(total, ccy)
        if row.get("currency") not in (None, ccy) or (row.get("amount") is not None and _amount(row["amount"], ccy) != amount):
            return None
        return {"schema": VERSION, "provider_order_id": provider_order_id, "provider": provider,
                "transaction_id": _identity(row.get("id"), "transaction_id"),
                "reference": _identity(refs.get("transaction"), "transaction_reference"),
                "status": slug, "paid_at": stamp, "currency": ccy, "amount": format(amount, ".2f")}
    except (ValueError, TypeError):
        return None


def normalize_transaction_payment(raw, currency, total, created_at, as_of, *, owner,
                                  order_number, provider_order_id, rows):
    payment, issues = normalize_payment(raw, currency, total, created_at, as_of)
    if not rows or payment is None:
        return payment, issues
    if not owner or str(raw.get("id", "")) != provider_order_id or str(raw.get("reference_id") or raw.get("order_number") or "") != order_number:
        return None, [{"code": "provider_transaction_order_conflict"}]
    provider = payment["provider"]
    scope = [r for r in rows if r.get("user_id") == owner and r.get("source") == "salla_transactions"
             and r.get("order_number") == order_number and r.get("provider_order_id") == provider_order_id]
    if not scope:
        return payment, issues

    def withheld(code, **details):
        return None, [{"code": code, "provider": provider, **details}]

    proofs, legacy = [], []
    for row in scope:
        group = row.get("native_transaction_evidence")
        if group is None:
            legacy.append(row)
            continue
        if not isinstance(group, list) or not group or len(group) > 16:
            return withheld("provider_transaction_history_conflict")
        for ev in group:
            if (not isinstance(ev, dict) or ev.get("schema") != VERSION
                    or ev.get("provider_order_id") != provider_order_id
                    or ev.get("transaction_id") != row.get("transaction_id")
                    or ev.get("provider") != provider or ev.get("currency") != currency):
                return withheld("provider_transaction_binding_conflict")
            if ev not in proofs:
                proofs.append(ev)
    if not proofs:
        return payment, issues  # display-only legacy records have no native timestamp provenance
    if legacy:
        return withheld("provider_transaction_history_conflict")
    # A later observation can supply the missing effective time for the exact
    # same paid transaction. Disagreeing dates, identity, status or amounts are
    # competing evidence, not two payments.
    ev = proofs[0]
    for other in proofs[1:]:
        if dict(ev, paid_at=None) != dict(other, paid_at=None) or (
                ev.get("paid_at") and other.get("paid_at") and ev["paid_at"] != other["paid_at"]):
            return withheld("provider_transaction_history_conflict")
        if other.get("paid_at"):
            ev = other
    if ev.get("status") not in {"paid", "captured"}:
        return withheld("provider_transaction_not_captured")
    if not ev.get("paid_at"):
        # The endpoint's created_at dates the transaction, not its capture. A
        # separately complete canonical capture is sufficient if identities agree.
        no_paid_at = True
    else:
        no_paid_at = False
    try:
        ref = _identity(ev.get("reference"), "provider_payment_reference_missing")
        paid = _amount(ev.get("amount"), currency)
        if paid <= 0 or paid > _amount(total, currency):
            return withheld("provider_transaction_amount_conflict")
        observed = payment.get("paid_observation")
        native = _method_rows(raw, provider, currency)
        canonical = raw.get("payment") or {}
        if canonical.get("status") not in (None, "", "paid", "captured") or raw.get("is_pending_payment") is True:
            return withheld("provider_capture_status_conflict")
        refs = [_identity(canonical[k], "provider_payment_reference_missing")
                for k in ("reference", "transaction_reference") if canonical.get(k) is not None]
        refs += [n[0] for n in native]
        if any(r != ref for r in refs):
            return withheld("provider_payment_reference_conflict")
        if (native and native[0][1] != paid) or (observed and _amount(observed["amount"], currency) != paid):
            return withheld("provider_transaction_amount_conflict")
        if no_paid_at:
            if payment.get("captures"):
                return payment, issues
            return payment, [{"code":"provider_capture_evidence_incomplete", "provider":provider,
                              "missing_fields":["capture_timestamp"], "paid_transaction_observed":True}]
        at = _instant(ev["paid_at"])
        if not _instant(created_at) <= at <= _instant(as_of):
            return withheld("provider_capture_timestamp_conflict")
        capture = {"id":ref, "amount":format(paid, ".2f"), "captured_at":at.isoformat()}
        if payment.get("captures"):
            if payment["captures"] != [capture]:
                return withheld("provider_payment_reference_conflict")
            return payment, issues
        if any(i.get("code") != "provider_capture_evidence_incomplete" for i in issues):
            return payment, issues
        return {**payment, "captures":[capture], "capture_source":VERSION}, []
    except (PaymentEvidenceError, KeyError):
        return withheld("provider_transaction_binding_conflict")


async def load_transaction_rows(db, owner, orders, start, now, *, to_instant, max_rows):
    """One bounded order-index query per batch. No foreign, uncapped, or per-order read."""
    if not isinstance(owner, str) or not owner.strip() or owner != owner.strip():
        raise ValueError("operational_source_owner_mismatch")
    keys = set()
    for raw in orders:
        try:
            if not start < to_instant(raw.get("date") or raw.get("created_at") or raw.get("order_date")) <= now:
                continue
            method = raw.get("payment") or {}
            if not isinstance(method, dict) or not provider_for(method.get("method") or raw.get("payment_method")):
                continue
            number = raw.get("reference_id") or raw.get("order_number")
            if number and raw.get("id"):
                keys.add(str(number))
        except (ValueError, TypeError, KeyError):
            continue  # main reader reports malformed canonical orders
    numbers, groups, count = sorted(keys), defaultdict(list), 0
    projection = {"_id":0, **{k:1 for k in ("user_id","source","order_number","provider_order_id",
                                               "transaction_id","native_transaction_evidence")}}
    for offset in range(0, len(numbers), 250):
        batch = numbers[offset:offset+250]
        query = {"user_id":owner, "order_number":{"$in":batch}, "source":"salla_transactions"}
        matches = await db[COLLECTION].find(query, projection).to_list(max_rows-count+1)
        count += len(matches)
        if count > max_rows:
            raise ValueError("operational_source_scope_too_large")
        for r in matches:
            if r.get("user_id") != owner or r.get("order_number") not in batch or r.get("source") != "salla_transactions":
                raise ValueError("operational_source_owner_mismatch")
            groups[r["order_number"]].append(r)
    # Before reducing any orders, quarantine all colliding source references.
    # Otherwise the first order in an unsorted DB cursor could claim the money.
    by_ref = defaultdict(set)
    for number, records in groups.items():
        for record in records:
            observations = record.get("native_transaction_evidence")
            for ev in observations if isinstance(observations, list) and len(observations) <= 16 else ():
                if isinstance(ev, dict) and isinstance(ev.get("reference"), str):
                    by_ref[ev["reference"]].add((number, record.get("provider_order_id")))
    for identities in by_ref.values():
        if len(identities) > 1:
            for number, order_id in identities:
                groups[number].append({"user_id":owner, "source":"salla_transactions",
                    "order_number":number, "provider_order_id":order_id, "native_transaction_evidence":[]})
    return groups
