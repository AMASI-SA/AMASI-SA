"""Deterministic, read-only reconciliation plan for operational payment events.

Phases:
  1. New verified payments
  2. Added installments, refunds, bank/POS COD collection confirmation
  3. Historical evidence matching/backfill

The result is a *proposal*, not a financial posting or an approval. It neither
reads nor writes any database, and the caller MUST use the canonical
operational-balance CAS ledger with audited, tenant-scoped, atomic idempotency.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Iterable, Mapping

from operational_payment_event_policy import (
    Decision, Kind, PaymentEvent, PostingDecision, decide,
)


@dataclass(frozen=True)
class ReconciliationRow:
    event: PaymentEvent
    phase: int
    # Existing canonical effects must be keyed by exact provider transaction,
    # and include the original owner/order/amount/currency; amounts alone
    # are not a proof of equivalence.
    existing_effect: Mapping[str, str] | None = None
    # This field must be set from actual operational opening/baseline cohort
    # recognition, not from a guessed date or order status.
    included_in_opening: bool = False


@dataclass(frozen=True)
class ReconciliationAction:
    phase: int
    status: str
    reason: str
    idempotency_key: str | None
    order_id: str
    provider: str
    amount: str


def _normalized_amount(value: str) -> str:
    return format(Decimal(value), ".2f")


def _equivalent(event: PaymentEvent, effect: Mapping[str, str]) -> bool:
    try:
        return (
            str(effect["order_id"]) == event.order_id
            and str(effect["provider"]).strip().lower() == event.provider.strip().lower()
            and effect["currency"] == event.currency
            and _normalized_amount(str(effect["amount"])) == _normalized_amount(event.amount)
            and effect["kind"] == event.kind.value
        )
    except (KeyError, ValueError, ArithmeticError, TypeError):
        return False


def plan(rows: Iterable[ReconciliationRow]) -> list[ReconciliationAction]:
    """Fail closed for duplicate identities and historical/opening overlaps.

    The snapshot input must be complete for the tenant and period. The caller
    must re-read and revalidate source evidence at the protected CAS commit.
    """
    entries = list(rows)
    if any(type(row.phase) is not int or row.phase not in (1, 2, 3) for row in entries):
        raise ValueError("invalid_phase")

    results: list[ReconciliationAction] = []
    seen: dict[str, tuple[str, str, str, str, str]] = {}

    for row in entries:
        event = row.event
        decision: PostingDecision = decide(event)
        status, reason = decision.action.value, decision.reason
        key = decision.idempotency_key
        if decision.action is Decision.POST:
            assert key is not None
            signature = (event.order_id, event.provider.strip().lower(),
                         event.currency, _normalized_amount(event.amount), event.kind.value)
            prior = seen.get(key)
            if prior is not None and prior != signature:
                status, reason = "conflict", "transaction_identity_reused"
            elif prior is not None:
                status, reason = "already_seen", "same_event_replayed"
            elif row.existing_effect is not None:
                status, reason = (
                    ("already_posted", "canonical_effect_matches")
                    if _equivalent(event, row.existing_effect)
                    else ("conflict", "canonical_effect_disagrees")
                )
            elif row.included_in_opening:
                status, reason = "requires_reconciliation", "historical_opening_overlap"
            elif row.phase == 3:
                status, reason = "backfill_candidate", "dry_run_only"
            else:
                status, reason = "eligible_candidate", "verified_not_yet_posted"
            seen.setdefault(key, signature)
        results.append(ReconciliationAction(
            phase=row.phase, status=status, reason=reason,
            idempotency_key=key, order_id=event.order_id,
            provider=event.provider, amount=event.amount,
        ))
    return results


def exception_summary(actions: Iterable[ReconciliationAction]) -> dict:
    """A bounded summary suitable for a daily operational exception job.

    No private identifiers or amounts are emitted in the summary. The scheduler
    and notifications remain outside this pure module until tenant/role
    scoping and access-control tests exist.
    """
    statuses = {}
    phases = {1: {}, 2: {}, 3: {}}
    for action in actions:
        if action.phase not in phases:
            raise ValueError("invalid_phase")
        statuses[action.status] = statuses.get(action.status, 0) + 1
        group = phases[action.phase]
        group[action.status] = group.get(action.status, 0) + 1
    review = sum(statuses.get(key, 0) for key in (
        "pending_accountant", "pending_evidence", "conflict", "requires_reconciliation", "reject"
    ))
    return {"total": sum(statuses.values()), "requires_review": review,
            "by_status": dict(sorted(statuses.items())),
            "by_phase": {phase: dict(sorted(counts.items())) for phase, counts in phases.items()}}
