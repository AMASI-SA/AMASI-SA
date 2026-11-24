"""Correlate existing CAS-staged payment evidence with the *canonical* projection.

Never creates an obligation, capture, refund, bank movement, or posting. It only
marks an already-calculated effect as recognized when its immutable reference,
provider, order and amount match. Call within the operational refresh CAS
candidate after canonical reconcile, not in a web request or via a raw webhook.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation

INBOX = "payment_event_evidence_inbox"
MAX_ITEMS = 10000


def _money(value):
    try:
        result = Decimal(str(value))
        if not result.is_finite() or result <= 0 or result != result.quantize(Decimal("0.01")):
            return None
        return format(result, ".2f")
    except (ValueError, InvalidOperation, TypeError):
        return None


def reconcile_staged_payment_evidence(state: dict) -> dict:
    """Mark recognized or conflicted staged evidence inside ONE candidate state.

    This is an observation of what the current balance engine has *already*
    recognized. It cannot be used as authorization to post missing amounts.
    Pending evidence remains pending and must not enter any balance totals.
    """
    inbox = state.get(INBOX, {})
    engine = state.get("engine", {})
    if not isinstance(inbox, dict) or len(inbox) > MAX_ITEMS or not isinstance(engine, dict):
        raise ValueError("operational_payment_evidence_scope_invalid")
    facts = engine.get("facts", {})
    if not isinstance(facts, dict):
        raise ValueError("operational_payment_canonical_facts_invalid")
    counts = {"recognized": 0, "pending": 0, "conflict": 0}
    for evidence in inbox.values():
        if not isinstance(evidence, dict):
            raise ValueError("operational_payment_evidence_record_invalid")
        status = evidence.get("status")
        if status not in {"evidence_received", "already_recognized", "conflict"}:
            raise ValueError("operational_payment_evidence_status_invalid")
        # Conflicted evidence requires explicit reviewed correction; a later
        # source update must never silently clear a recorded identity conflict.
        if status == "conflict":
            counts["conflict"] += 1
            continue
        kind, ref = evidence.get("kind"), evidence.get("native_ref")
        if kind == "payment":
            key = "capture:" + ref if isinstance(ref, str) and ref else None
        elif kind == "refund":
            provider = evidence.get("provider")
            key = ("refund:" + provider + ":" + ref if isinstance(provider, str)
                   and isinstance(ref, str) and ref else None)
        else:
            # COD bank/POS/cash is handled by its separate verified collection
            # workflow. Do not infer a bank receipt from an order delivery.
            counts["pending"] += 1
            continue
        if not key:
            evidence["status"] = "conflict"
            counts["conflict"] += 1
            continue
        fact = facts.get(key)
        if fact is None:
            if status == "already_recognized":
                # A formerly recognized financial effect disappeared: never
                # silently downgrade to pending or credit it again.
                evidence["status"] = "conflict"
                counts["conflict"] += 1
            elif status == "conflict":
                counts["conflict"] += 1
            else:
                counts["pending"] += 1
            continue
        expected = (str(evidence.get("order_id")), evidence.get("provider"),
                    _money(evidence.get("amount")))
        observed = (str(fact.get("order_id")) if isinstance(fact, dict) else None,
                    (fact.get("provider") if kind == "payment" else evidence.get("provider"))
                        if isinstance(fact, dict) else None,
                    _money(fact.get("amount")) if isinstance(fact, dict) else None)
        if expected[2] is None or observed != expected:
            evidence["status"] = "conflict"
            counts["conflict"] += 1
        else:
            evidence["status"] = "already_recognized"
            counts["recognized"] += 1
    engine["payment_event_evidence_coverage"] = counts
    return counts
