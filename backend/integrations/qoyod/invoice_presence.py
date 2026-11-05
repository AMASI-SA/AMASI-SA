"""Read-only, time-bounded proof of invoice absence for the owner banner.

Queue eligibility is not evidence that a provider invoice does not exist.
Only a complete provider refresh can establish absence as of that refresh.
This module never changes sender selection or payment/retry safeguards.
"""
from datetime import datetime, timedelta, timezone

MAX_EVIDENCE_AGE = timedelta(minutes=15)


def verified_unsent_summary(proofs, snapshot, *, now=None, scan_truncated=False):
    now = now or datetime.now(timezone.utc)
    snapshot = snapshot or {}
    try:
        started = datetime.fromisoformat(snapshot["started_at"])
        if started.tzinfo is None:
            raise ValueError("timezone required")
        floor = datetime.fromisoformat(snapshot["sync_start"]).date()
        complete = (snapshot.get("complete") is True and not scan_truncated
                    and timedelta(0) <= now - started <= MAX_EVIDENCE_AGE)
    except (KeyError, TypeError, ValueError):
        started, floor, complete = None, None, False
    absent, unknown, seen = [], 0, set()
    for proof in proofs:
        reference = str(proof.get("order_number") or "")
        if not reference or reference in seen:
            continue
        seen.add(reference)
        if proof.get("qoyod_invoice") is not None:
            continue  # Existence is independent of payment and local failures.
        marker = proof.get("manual_qoyod_invoice_id") or proof.get("local_qoyod_invoice_id")
        try:
            order_day = datetime.fromisoformat(str(proof.get("order_date"))).date()
            covered = complete and floor <= order_day < started.date()
        except (TypeError, ValueError):
            covered = False
        if not covered or marker:
            unknown += 1
        else:
            absent.append(reference)
    return {"count": len(absent) if complete else None,
            "order_numbers": sorted(absent), "verification_required": unknown,
            "verified_at": started.isoformat() if complete else None,
            "expires_at": (started + MAX_EVIDENCE_AGE).isoformat() if complete else None,
            "complete": bool(complete)}
