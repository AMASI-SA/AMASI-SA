"""Final integration seam: bank arrival OR accountant-reviewed POS receipt.

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
    verification: Literal["bank_arrival_confirmed", "accountant_pos_review_approved"]


async def _manual_pos_destination(db, owner, *, financial_account_id, evidence_reference,
        amount, receipt_hash, review, receipt, actor_id, reviewed_amount):
    from pydantic import ValidationError
    from accounting_onboarding_ssot import FACTS, TypedFactCreate, _key
    from accounting_shipping_native_contract import digest, money, fail

    if not reviewed_amount:
        fail("driver_pos_reviewed_amount_required")
    if money(reviewed_amount, positive=True) != money(amount, positive=True):
        fail("driver_pos_reviewed_amount_mismatch")
    if (not actor_id or not review or not receipt
            or review.get("user_id") != owner or receipt.get("user_id") != owner
            or review.get("status") != "pending" or review.get("payment_method") != "card_terminal"
            or receipt.get("status") != "bound" or receipt.get("sha256") != receipt_hash
            or receipt.get("token") != review.get("receipt_reference")
            or receipt.get("assignment_id") != review.get("assignment_id")
            or receipt.get("driver_id") != review.get("driver_id")
            or money(review.get("amount")) != money(amount)):
        fail("driver_pos_review_evidence_required")
    rows = await db[FACTS].find({"user_id": owner, "id": financial_account_id,
        "status": "active", "category": "other_receivable"}).limit(2).to_list(2)
    if len(rows) != 1:
        fail("driver_pos_receivable_identity_required")
    fact = rows[0]
    try:
        contract = TypedFactCreate.model_validate({key: fact[key] for key in TypedFactCreate.model_fields if key in fact})
    except (ValidationError, ValueError):
        fail("driver_pos_receivable_identity_required")
    identity = _key(owner, "other_receivable", contract.reference, str(contract.cutover_date))
    if (fact.get("_id") != identity or fact.get("id") != identity or fact.get("entity_id") != identity
            or (fact.get("entity_type"), fact.get("sub_account"), fact.get("side")) != ("asset", "other_receivable", "debit")
            or contract.currency != "SAR" or fact.get("source") != "documented_opening_fact"
            or not fact.get("created_by") or not fact.get("created_at") or fact.get("version") != 1):
        fail("driver_pos_receivable_identity_required")
    # Selection is explicit for this review. A generic receivable's label never
    # makes it POS, and no account, opening fact or default binding is created.
    reference = str(evidence_reference or "").strip() or None
    return dict(user_id=owner, financial_account_id=identity, destination_kind="pos_receivable",
        entity_type="asset", entity_id=identity, sub_account="other_receivable", currency="SAR",
        amount=amount, status="verified", evidence_reference=evidence_reference,
        receipt_hash=receipt_hash, receipt_reference=receipt["token"],
        source_namespace="manual_pos_receipt",
        # A selected receivable is not a processor-reference namespace. Changing
        # the selection must not permit reuse of the same manual transaction.
        source_record_id=digest(["transaction_reference", reference]) if reference else receipt_hash,
        source_revision=digest([review["id"], review.get("revision", 1), receipt_hash, fact]),
        verification="accountant_pos_review_approved", bank_movement_id=None,
        display_name=contract.display_name, identity_source=FACTS,
        identity_reference=contract.reference, identity_evidence=contract.evidence,
        reviewed_by=actor_id, reviewed_amount=reviewed_amount, transaction_reference=reference)


async def require_driver_payment_destination(db, owner, *, payment_method,
        financial_account_id, evidence_reference, amount, receipt_hash,
        review=None, receipt=None, actor_id=None, reviewed_amount=None) -> DriverPaymentDestination:
    """Bank transfer requires arrived funds; POS requires accountant review.

    The caller authorizes the accountant and verifies bound receipt bytes in
    the same transaction. Manual POS approval selects an existing documented
    native receivable; it neither asserts processor verification nor bank
    arrival. The writer still requires opening coverage and consumes evidence
    atomically. No uploaded image or selected identity approves itself.
    """
    if payment_method == "card_terminal":
        return await _manual_pos_destination(db, owner, financial_account_id=financial_account_id,
            evidence_reference=evidence_reference, amount=amount, receipt_hash=receipt_hash,
            review=review, receipt=receipt, actor_id=actor_id, reviewed_amount=reviewed_amount)
    if payment_method != "bank_transfer":
        raise HTTPException(503, detail={"code": "mz2_driver_payment_destination_not_integrated"})
    from accounting_financial_identity import require_financial_ledger_identity
    from accounting_bank_statement_proof import verified_bank_movement
    identity = await require_financial_ledger_identity(db, owner=owner,
        financial_account_id=financial_account_id, account_types=("bank",), currency="SAR")
    movement, proof = await verified_bank_movement(db, owner, movement_id=evidence_reference,
        bank_id=financial_account_id, direction="in", amount=amount)
    # The review consumer verifies the bound receipt bytes before calling this
    # port and consumes this exact bank movement in the same transaction.
    return dict(user_id=owner, financial_account_id=financial_account_id,
        destination_kind="bank", entity_type=identity["entity_type"],
        entity_id=identity["entity_id"], sub_account=identity["sub_account"],
        currency="SAR", amount=movement["amount"], status="verified",
        evidence_reference=evidence_reference, receipt_hash=receipt_hash,
        bank_movement_id=movement["id"], verification="bank_arrival_confirmed",
        **{k: proof[k] for k in ("source_namespace", "source_record_id", "source_revision")})
