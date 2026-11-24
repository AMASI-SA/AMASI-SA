"""Internal CAS-backed evidence inbox for the operational-balance workspace.

No API endpoint and no automatic money posting are exposed here. The caller
MUST already have validated a canonical payment capture using an independent
trusted financial source, then invoke this service under an authenticated,
tenant-bound source worker. This writes only an auditable source *observation*
into the same operational aggregate. It never changes engine claims or balances.
"""
from __future__ import annotations

from decimal import Decimal
from hashlib import sha256
import json


def digest(value):
    # Identical deterministic hash contract to operational_balance_store.digest,
    # but without importing the web/database runtime for pure policy tests.
    return sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                             separators=(",", ":"), default=str).encode()).hexdigest()
from operational_payment_event_policy import Decision, Kind, PaymentEvent, decide

MAX_INBOX = 10000
INBOX_KEY = "payment_event_evidence_inbox"


def event_record(event: PaymentEvent, *, owner: str, source: str, source_ref: str) -> dict:
    if not isinstance(owner, str) or not owner or source not in {
        "salla_native_transactions", "provider_statement_independent_match",
        "approved_operational_accountant",
    } or not isinstance(source_ref, str) or not source_ref.strip() or len(source_ref) > 256:
        raise ValueError("untrusted_payment_event_source")
    decision = decide(event)
    if decision.action is not Decision.POST or not decision.idempotency_key:
        raise ValueError("payment_event_not_confirmed")
    if event.kind not in {Kind.PAYMENT, Kind.REFUND, Kind.COD}:
        raise ValueError("unknown_payment_event_kind")
    amount = format(Decimal(event.amount), ".2f")
    key = digest([owner, decision.idempotency_key])
    return {
        "key": key, "source": source, "source_ref": source_ref,
        "order_id": event.order_id, "provider": event.provider.strip().lower(),
        "kind": event.kind.value, "currency": event.currency, "amount": amount,
        "transaction_key": decision.idempotency_key,
        "native_ref": (event.refund_id if event.kind == Kind.REFUND else (event.transaction_id or event.installment_id)),
        "captured_at": event.captured_at,
        "target": decision.balance_bucket, "status": "evidence_received",
    }


def _financial_identity(record):
    return tuple(record.get(key) for key in (
        "order_id", "provider", "kind", "currency", "amount", "transaction_key", "captured_at", "target",
    ))


def queue_evidence_in_state(state: dict, record: dict, stamp: str) -> dict:
    """Stage one verified observation. Must execute inside operational CAS.

    Reject any changed financial identity on an existing key. Provenance from
    a second trusted source can be attached only without changing the amount.
    Never alter a previously posted movement, obligation or canonical fact.
    """
    if state.get("status") != "active":
        raise ValueError("operational_workspace_not_active")
    inbox = state.setdefault(INBOX_KEY, {})
    if not isinstance(inbox, dict) or len(inbox) > MAX_INBOX:
        raise ValueError("payment_event_inbox_capacity")
    existing = inbox.get(record["key"])
    if existing is None and len(inbox) >= MAX_INBOX:
        raise ValueError("payment_event_inbox_capacity")
    if existing is not None:
        if _financial_identity(existing) != _financial_identity(record):
            raise ValueError("payment_event_identity_conflict")
        sources = existing.setdefault("sources", [])
        proof = {"source": record["source"], "ref": record["source_ref"]}
        if proof not in sources:
            sources.append(proof)
        return {"status": "already_received", "key": record["key"]}
    # Existing canonical capture is already included in the derived provider
    # balance. Mark it as recognized, not as a new receipt candidate.
    recognized = False
    if record["kind"] == "payment":
        facts = state.get("engine", {}).get("facts", {})
        if not isinstance(facts, dict):
            raise ValueError("payment_event_canonical_facts_invalid")
        canonical = facts.get("capture:" + record["native_ref"])
        if canonical is not None:
            if not isinstance(canonical, dict) or (
                str(canonical.get("order_id")) != record["order_id"]
                or canonical.get("provider") != record["provider"]
                or canonical.get("amount") != record["amount"]
            ):
                raise ValueError("payment_event_canonical_conflict")
            recognized = True
    saved = {**record, "created_at": stamp,
             "status": "already_recognized" if recognized else "evidence_received",
             "sources": [{"source": record["source"], "ref": record["source_ref"]}]}
    saved.pop("source", None)
    saved.pop("source_ref", None)
    inbox[record["key"]] = saved
    return {"status": saved["status"], "key": record["key"]}


async def stage_verified_evidence(db, owner, event: PaymentEvent, *,
                                  source: str, source_ref: str, stamp=None) -> dict:
    """Internal source-worker primitive; caller must enforce source auth.

    Avoid calling from public/browser routes. The operational aggregate CAS
    serializes concurrent same-owner requests. This is *not* money posting.
    """
    from operational_balance_store import fail, mutate, now
    record = event_record(event, owner=owner, source=source, source_ref=source_ref)
    stamp = stamp or now()

    async def apply(state):
        try:
            result = queue_evidence_in_state(state, record, stamp)
        except ValueError as exc:
            fail(str(exc), "تعذر حفظ إثبات الدفع بأمان", 409)
        return result

    return await mutate(db, owner, apply)

def queue_verified_batch_in_state(state: dict, records: list[dict], stamp: str) -> list[dict]:
    """Apply an all-or-nothing batch inside a single CAS candidate snapshot.

    The surrounding mutate() discards the entire copied state if any record
    conflicts. This helper must not be called on the real stored state outside
    that copy. No financial movement or provider obligation is created.
    """
    if not isinstance(records, list) or not 1 <= len(records) <= 16:
        raise ValueError("payment_event_batch_size_invalid")
    return [queue_evidence_in_state(state, record, stamp) for record in records]


async def stage_normalized_captures(db, owner, order_id, normalized, diagnostics,
                                    *, source_ref: str, stamp=None) -> list[dict]:
    """Trusted-source worker adapter; not mounted on any public route.

    The caller must guarantee ownership and authenticity of normalized source
    data. This entrypoint adds only source observations; it never posts money.
    """
    from operational_balance_store import fail, mutate, now
    from operational_payment_capture_adapter import payment_events_from_normalized
    events = payment_events_from_normalized(order_id, normalized, diagnostics)
    if not events:
        return []
    records = [event_record(event, owner=owner, source="salla_native_transactions",
                            source_ref=source_ref) for event in events]
    stamp = stamp or now()

    async def apply(state):
        try:
            return queue_verified_batch_in_state(state, records, stamp)
        except ValueError as exc:
            fail(str(exc), "تعذر حفظ إثباتات الدفع بأمان", 409)

    return await mutate(db, owner, apply)
