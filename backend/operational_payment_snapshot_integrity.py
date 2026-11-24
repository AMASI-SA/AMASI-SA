"""Read-only integrity review of the *existing* operational balance snapshot.

This is NOT a fresh Salla/BNPL statement reconciliation, an automatic journal
writer, or a claim that an unobserved payment is unpaid. It reports confirmed
customer collection cases and canonical capture facts already in the owner's
operational aggregate. Never mutates the supplied snapshot.
"""
from __future__ import annotations

from collections import Counter
from decimal import Decimal, InvalidOperation

MAX_CASES = 20000
MAX_ISSUES = 100


def _money(value):
    try:
        result = Decimal(str(value))
        if not result.is_finite() or result <= 0 or result != result.quantize(Decimal("0.01")):
            return None
        return format(result, ".2f")
    except (ValueError, InvalidOperation, TypeError):
        return None


def audit_payment_snapshot(state: dict, *, max_issues: int = MAX_ISSUES) -> dict:
    """Summarize reference collisions, unlinked extra payments and capture conflicts.

    All evidence is from the passed immutable snapshot. Missing external
    payment events are unknown, not counted as unpaid. No bank balances,
    account balances, opening values or patient/customer data are included.
    """
    if not isinstance(state, dict) or not isinstance(state.get("engine", {}), dict):
        raise ValueError("operational_snapshot_invalid")
    if type(max_issues) is not int or not 0 <= max_issues <= MAX_ISSUES:
        raise ValueError("operational_issue_limit_invalid")
    cases = state.get("customer_collections", [])
    if not isinstance(cases, list) or len(cases) > MAX_CASES:
        raise ValueError("operational_collection_scope_invalid")
    facts = state.get("engine", {}).get("facts", {})
    if not isinstance(facts, dict) or len(facts) > MAX_CASES:
        raise ValueError("operational_fact_scope_invalid")

    from operational_customer_collection_effects import collection_reference_match
    issues = []
    counts = Counter()
    keys = {}
    seen_ids = set()

    def record(code, case=None):
        counts[code] += 1
        if len(issues) < max_issues:
            entry = {"code": code}
            if isinstance(case, dict):
                # Financially scoped identifiers only, no notes/customer PII.
                entry.update({key: case[key] for key in ("id", "order_number")
                              if isinstance(case.get(key), str) and len(case[key]) <= 160})
            issues.append(entry)

    for case in cases:
        if not isinstance(case, dict):
            record("collection_case_invalid")
            continue
        if case.get("status") != "confirmed":
            counts["collection_not_confirmed"] += 1
            continue
        counts["collections_confirmed"] += 1
        identifier = case.get("id")
        if not isinstance(identifier, str) or not identifier or identifier in seen_ids:
            record("collection_identity_invalid", case)
            continue
        seen_ids.add(identifier)
        kind, provider, ref = (case.get("destination_type"), case.get("destination_id"), case.get("reference"))
        order, amount, currency = (case.get("order_id"), _money(case.get("amount")), case.get("currency"))
        if kind not in {"provider", "bank"} or not isinstance(provider, str) or not provider or not isinstance(ref, str) or not ref or amount is None or not order or currency != "SAR":
            record("collection_financial_fields_invalid", case)
            continue
        key = (kind, provider, ref)
        signature = (str(order), amount, currency)
        if key in keys:
            record("collection_reference_duplicate" if keys[key] == signature
                   else "collection_reference_conflict", case)
            continue
        keys[key] = signature
        try:
            link = collection_reference_match(state, case)
        except (ValueError, KeyError, TypeError):
            record("collection_source_conflict", case)
            continue
        if link:
            counts["collections_source_matched"] += 1
        else:
            record("collection_source_unmatched", case)

    inbox = state.get("payment_event_evidence_inbox", {})
    if not isinstance(inbox, dict) or len(inbox) > MAX_CASES:
        raise ValueError("operational_payment_inbox_scope_invalid")
    for evidence in inbox.values():
        if not isinstance(evidence, dict):
            record("payment_evidence_record_invalid")
        elif evidence.get("status") == "already_recognized":
            counts["payment_evidence_already_recognized"] += 1
        elif evidence.get("status") == "evidence_received":
            counts["payment_evidence_pending_projection"] += 1
        else:
            record("payment_evidence_status_invalid")

    # Capture IDs are authoritative only within the existing projection.
    # Never invent a transaction from order status or a statement row alone.
    for key, fact in facts.items():
        if not isinstance(key, str) or not key.startswith("capture:"):
            continue
        counts["canonical_capture_facts"] += 1
        if not isinstance(fact, dict) or not fact.get("order_id") or not fact.get("provider") or _money(fact.get("amount")) is None:
            record("capture_fact_invalid")
    return {
        "scope": "saved_operational_snapshot_only",
        "snapshot_as_of": state.get("engine", {}).get("as_of"),
        "revision": state.get("revision"),
        "counts": dict(sorted(counts.items())),
        "issues": issues,
        "issues_truncated": sum(n for code, n in counts.items() if code not in
                                 {"collection_not_confirmed", "collections_confirmed",
                                  "collections_source_matched", "canonical_capture_facts",
                                  "payment_evidence_already_recognized", "payment_evidence_pending_projection"}) > len(issues),
        "safe_for_automatic_posting": False,
    }
