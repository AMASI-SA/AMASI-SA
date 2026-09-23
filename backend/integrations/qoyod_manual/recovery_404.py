"""Closed-cohort recovery state machine, explicitly activated by the operator.

Importing
this module cannot release quarantine or enable sending. Ports must provide
fresh, complete provider evidence and a durable atomic claim before writes.
Unknown outcomes are never automatically retried, including after restart.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
import hashlib
import json
import re
from typing import Protocol


class EvidenceError(ValueError):
    pass


def safe_reason(exc):
    if isinstance(exc, EvidenceError):
        return str(exc)
    code = getattr(exc, "code", None)
    return code if isinstance(code, str) and re.fullmatch(r"[a-z][a-z0-9_]{0,80}", code) else "outcome_unknown"


def money(value: str) -> Decimal:
    try:
        result = Decimal(str(value))
    except InvalidOperation as exc:
        raise EvidenceError("invalid_money") from exc
    if not result.is_finite() or result < 0:
        raise EvidenceError("invalid_money")
    return result


@dataclass(frozen=True)
class Scope:
    references: tuple[str, ...]
    excluded: tuple[str, ...]
    from_date: date
    to_date: date
    release_identity: str

    def __post_init__(self):
        if (not self.references or len(set(self.references)) != len(self.references)
                or any(not ref.isdigit() for ref in self.references)
                or self.from_date > self.to_date or not self.release_identity
                or not set(self.excluded).issubset(self.references)):
            raise ValueError("invalid_closed_scope")

    @property
    def fingerprint(self) -> str:
        payload = [sorted(self.references), sorted(self.excluded),
                   self.from_date.isoformat(), self.to_date.isoformat(),
                   self.release_identity]
        return hashlib.sha256(json.dumps(payload).encode()).hexdigest()


@dataclass(frozen=True)
class Facts:
    reference: str
    order_date: date
    status: str
    payment_eligible: bool
    is_cod: bool
    skus_complete: bool
    total: str
    expected_invoice_total: str
    currency: str
    quarantine_open: bool
    failure_status: int
    failure_method: str
    failure_endpoint: str
    failure_precedes_release: bool
    fresh_salla: bool


@dataclass(frozen=True)
class Invoice:
    reference: str
    invoice_id: str
    total: str
    paid: str
    remaining: str
    currency: str


@dataclass(frozen=True)
class Observation:
    invoices: tuple[Invoice, ...]
    complete_reference_lookup: bool
    fresh_provider_read: bool
    mezan_invoice_id: str | None = None
    mezan_sent_reconciled: bool = False


@dataclass(frozen=True)
class Outcome:
    reference: str
    state: str
    reason: str
    invoice_id: str | None = None
    invoice_total: str | None = None
    paid_amount: str | None = None
    remaining: str | None = None
    salla_total: str | None = None
    read_diagnostic: dict | None = None


class Ports(Protocol):
    """Production adapter obligations; no default or live implementation.

    claim_once MUST atomically persist an attempt keyed by tenant/reference
    (not just campaign), surviving process restart. Never expire/reclaim it
    automatically. finish must persist per-order results; pause must durably
    disarm this campaign. Observation must bypass a pre-send snapshot cache.
    send_guarded must reuse the existing per-order lock/reference/amount and
    payment guards. reconcile_marker may write local markers only.
    """
    async def authorized(self, fingerprint: str) -> bool: ...
    async def facts(self, reference: str) -> Facts: ...
    async def observe(self, reference: str) -> Observation: ...
    async def claim_once(self, reference: str, fingerprint: str) -> bool: ...
    async def has_claim(self, reference: str) -> bool: ...
    async def send_guarded(self, reference: str) -> None: ...
    async def reconcile_marker(self, reference: str, invoice_id: str) -> None: ...
    async def finish(self, outcome: Outcome) -> None: ...
    async def pause(self, reason: str) -> None: ...


def refusal(scope: Scope, reference: str, facts: Facts) -> str | None:
    if reference not in scope.references or reference in scope.excluded:
        return "outside_recovery_scope"
    if facts.reference != reference or not facts.fresh_salla:
        return "salla_evidence_unverified"
    if not scope.from_date <= facts.order_date <= scope.to_date:
        return "outside_date_scope"
    if facts.status not in {"completed", "in_delivery", "delivered"}:
        return "ineligible_status"
    if facts.is_cod:
        return "cod_deferred"
    if not facts.skus_complete:
        return "missing_sku_deferred"
    if not facts.payment_eligible:
        return "payment_ineligible"
    if (not facts.quarantine_open or facts.failure_status != 404
            or facts.failure_method != "GET"
            or facts.failure_endpoint != "/products"
            or not facts.failure_precedes_release):
        return "not_proven_old_product_404"
    if facts.currency != "SAR":
        return "unsupported_currency"
    if money(facts.total) <= 0:
        return "nonpositive_total"
    if abs(money(facts.total) - money(facts.expected_invoice_total)) > Decimal("0.01"):
        return "preflight_amount_mismatch"
    return None


def verified_invoice(reference: str, facts: Facts, evidence: Observation) -> Invoice | None:
    if not evidence.complete_reference_lookup or not evidence.fresh_provider_read:
        raise EvidenceError("provider_reference_unknown")
    if any(i.reference != reference for i in evidence.invoices):
        raise EvidenceError("provider_reference_mismatch")
    if len(evidence.invoices) > 1:
        raise EvidenceError("duplicate_provider_invoices")
    if not evidence.invoices:
        return None
    invoice = evidence.invoices[0]
    if not invoice.invoice_id or invoice.currency != "SAR":
        raise EvidenceError("invoice_identity_or_currency_unverified")
    if abs(money(invoice.total) - money(facts.total)) > Decimal("0.01"):
        raise EvidenceError("provider_amount_mismatch")
    if money(invoice.remaining) != 0 or money(invoice.paid) != money(invoice.total):
        raise EvidenceError("provider_settlement_incomplete")
    return invoice


async def recover_one(scope: Scope, reference: str, ports: Ports) -> Outcome:
    """At most one financial attempt, followed by independent verification."""
    sent = False
    try:
        if reference not in scope.references or reference in scope.excluded:
            return Outcome(reference, "excluded", "outside_recovery_scope")
        if not await ports.authorized(scope.fingerprint):
            return Outcome(reference, "disabled", "activation_required")
        facts = await ports.facts(reference)
        reason = refusal(scope, reference, facts)
        if reason:
            result = Outcome(reference, "blocked", reason)
            await ports.finish(result)
            return result
        invoice = verified_invoice(reference, facts, await ports.observe(reference))
        if not await ports.authorized(scope.fingerprint):
            return Outcome(reference, "disabled", "activation_revoked")
        if not await ports.claim_once(reference, scope.fingerprint):
            return Outcome(reference, "unknown", "prior_attempt_requires_reconciliation")
        # Fresh checks AFTER claim close the queue-wait eligibility window.
        facts = await ports.facts(reference)
        reason = refusal(scope, reference, facts)
        if reason:
            raise EvidenceError(reason)
        invoice = verified_invoice(reference, facts, await ports.observe(reference))
        if not await ports.authorized(scope.fingerprint):
            raise EvidenceError("activation_revoked")
        if invoice is None:
            sent = True  # Set before awaiting: timeout may follow a provider commit.
            await ports.send_guarded(reference)
        # Never regard a sender return or an already-sent exception as proof.
        evidence = await ports.observe(reference)
        invoice = verified_invoice(reference, facts, evidence)
        if invoice is None:
            raise EvidenceError("post_send_invoice_not_verified")
        if not evidence.mezan_sent_reconciled or evidence.mezan_invoice_id != invoice.invoice_id:
            await ports.reconcile_marker(reference, invoice.invoice_id)
            evidence = await ports.observe(reference)
            verified = verified_invoice(reference, facts, evidence)
            if (verified is None or verified.invoice_id != invoice.invoice_id
                    or not evidence.mezan_sent_reconciled
                    or evidence.mezan_invoice_id != invoice.invoice_id):
                raise EvidenceError("mezan_marker_unverified")
        result = Outcome(reference, "verified_sent" if sent else "verified_existing",
                         "invoice_amount_settlement_and_marker_verified", invoice.invoice_id)
        await ports.finish(result)
        return result
    except Exception as exc:
        # Do not persist provider response bodies or exception strings (PII).
        reason = safe_reason(exc)
        if isinstance(exc, EvidenceError) and reason == "provider_settlement_incomplete":
            # Only this known settlement error may be isolated. Independently
            # refresh both sources; timeouts and uncertain writes never reach
            # this exception path and must still pause the whole campaign.
            try:
                audited = await audit_one(scope, reference, ports)
                if audited.state == "rounding_review":
                    return audited
            except Exception:
                # Even local audit persistence failure is not permission to
                # continue. Keep the original conservative pause behavior.
                pass
        result = Outcome(reference, "unknown" if sent else "blocked", reason,
                         read_diagnostic=getattr(exc, "_recovery_read_diagnostic", None))
        # Pause before finish: a local persistence failure must not permit more sends.
        await ports.pause(reason)
        await ports.finish(result)
        return result


async def audit_one(
    scope: Scope,
    reference: str,
    ports: Ports,
    *,
    allow_preclaim_requeue: bool = False,
) -> Outcome:
    """Read-only reconciliation of a submitted or pre-claim read failure.

    Never calls claim/send/marker repair and never treats absence as retry
    permission unless the caller proves the persisted failure is the narrow
    pre-claim provider-page 404 case and no durable claim exists. This is the
    path for a timeout, restart, or inconsistent UI.
    """
    if reference not in scope.references:
        return Outcome(reference, "excluded", "outside_recovery_scope")
    try:
        facts = await ports.facts(reference)
        if facts.reference != reference or not facts.fresh_salla:
            raise EvidenceError("salla_evidence_unverified")
        evidence = await ports.observe(reference)
        try:
            invoice = verified_invoice(reference, facts, evidence)
        except EvidenceError as exc:
            # Isolation requires a fresh, unique, known invoice; an unknown
            # result is never permission to continue the campaign. This path
            # changes local audit evidence only, never receipts or claims.
            if str(exc) != "provider_settlement_incomplete":
                raise
            invoice = evidence.invoices[0]
            if (reference in scope.excluded
                    or not scope.from_date <= facts.order_date <= scope.to_date
                    or facts.status not in {"completed", "in_delivery", "delivered"}
                    or not facts.payment_eligible or facts.is_cod
                    or not facts.skus_complete or facts.currency != "SAR"
                    or money(facts.total) <= 0
                    or (bool(evidence.mezan_invoice_id)
                        and evidence.mezan_invoice_id != invoice.invoice_id)
                    or money(invoice.total) != money(facts.total) + Decimal("0.01")
                    or money(invoice.paid) != money(facts.total)
                    or money(invoice.remaining) != Decimal("0.01")
                    or money(invoice.total) != money(invoice.paid) + money(invoice.remaining)):
                raise
            result = Outcome(
                reference, "rounding_review", "existing_invoice_rounding_requires_settlement",
                invoice.invoice_id, invoice.total, invoice.paid,
                invoice.remaining, facts.total)
            await ports.finish(result)
            return result
        if invoice is None:
            # A failed provider read before claim_once cannot have sent an
            # invoice.  Once a fresh, complete provider scan proves absence,
            # return that row to the closed campaign's pending queue.  A
            # durable claim means a write may have been attempted, so absence
            # remains non-retryable exactly as before.
            if not allow_preclaim_requeue or await ports.has_claim(reference):
                raise EvidenceError("submitted_invoice_not_found_do_not_retry")
            result = Outcome(reference, "pending", "pre_send_read_recovered")
            await ports.finish(result)
            return result
        if not evidence.mezan_sent_reconciled or evidence.mezan_invoice_id != invoice.invoice_id:
            raise EvidenceError("mezan_marker_unverified")
        result = Outcome(reference, "verified_audit",
                         "invoice_amount_settlement_and_marker_verified", invoice.invoice_id)
    except Exception as exc:
        result = Outcome(reference, "review",
                         safe_reason(exc), read_diagnostic=getattr(exc, "_recovery_read_diagnostic", None))
    await ports.finish(result)
    return result


async def recover_batch(scope: Scope, references: tuple[str, ...], ports: Ports,
                        limit: int = 1) -> list[Outcome]:
    """Bounded sequential slice; unknown or disabled outcomes stop the slice."""
    if not 1 <= limit <= 5:
        raise ValueError("batch_limit_must_be_1_to_5")
    outcomes = []
    for reference in dict.fromkeys(references):
        if len(outcomes) >= limit:
            break
        outcome = await recover_one(scope, reference, ports)
        outcomes.append(outcome)
        if outcome.state in {"unknown", "disabled"}:
            break
    return outcomes
