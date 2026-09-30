"""Final integration seam: verified bank arrival OR successful POS receivable.

No Legacy resolver, inferred bank arrival, or configurable bypass. Integration
must resolve bank identity through Track A, and POS receivable through an
approved MZ2 identity contract. The returned proof must match the reviewed
receipt, owner, amount, reference and method in the same Mongo transaction.
POS is never represented by a bank tuple at driver-review acceptance.
"""
from typing import Literal, TypedDict
from fastapi import HTTPException


class DriverPaymentDestination(TypedDict):
    user_id: str
    financial_account_id: str
    destination_kind: Literal["bank", "pos_receivable"]
    entity_type: str
    entity_id: str
    sub_account: str
    currency: str
    amount: str
    status: Literal["verified"]
    evidence_reference: str
    source_revision: str
    receipt_hash: str
    source_namespace: str
    source_record_id: str
    bank_movement_id: str | None
    verification: Literal["bank_arrival_confirmed", "pos_transaction_successful"]


async def require_driver_payment_destination(db, owner, *, payment_method,
        financial_account_id, evidence_reference, amount, receipt_hash) -> DriverPaymentDestination:
    """Bank transfer requires arrived funds; card requires successful POS proof.

    Receipt upload alone is not proof of success/arrival. A production adapter
    must verify evidence and resolve the exact canonical ledger destination.
    Bank arrival must identify its native unclassified daily movement, which
    the consumer atomically consumes. POS success must identify a canonical
    processor transaction (not an upload token or arbitrary caller reference);
    never merely echo caller-supplied identity, amount or reference.
    """
    raise HTTPException(503, detail={"code": "mz2_driver_payment_destination_not_integrated"})
