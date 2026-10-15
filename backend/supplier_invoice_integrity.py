"""Fail-closed supplier-invoice document/ledger contract. No writes or repairs.

Call inside the existing close transaction *after* all writes and before commit.
Read APIs reuse the same verifier for new-contract documents; historical rows are
never backfilled. Mezan 2 must consume these source IDs, not post a second group.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from fastapi import HTTPException

CONTRACT = "supplier_receiving_financial_integrity_v1"
INVOICES = "mezan_supplier_invoices_v2"
SESSIONS = "mezan_supplier_receiving_sessions_v1"


def require(condition: bool, reason: str) -> None:
    if not condition:
        raise HTTPException(status_code=409, detail={
            "code": "supplier_invoice_financial_integrity_failed",
            "reason": reason,
            "message": "تعذّر التحقق من تطابق الفاتورة والقيد والجلسة؛ لا تعِد الإرسال قبل التحقق من السجل.",
        })


def identifier(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip()) and value == value.strip()


def halalas(value: Any, *, positive: bool = True) -> int:
    require(isinstance(value, int) and not isinstance(value, bool), "invalid_halalas")
    require(value > 0 if positive else value >= 0, "nonpositive_amount")
    return value


def ledger_halalas(value: Any) -> int:
    require(not isinstance(value, bool) and value is not None, "invalid_ledger_amount")
    try:
        amount = value.to_decimal() if hasattr(value, "to_decimal") else Decimal(str(value))
        require(amount.is_finite(), "invalid_ledger_amount")
        minor = amount * 100
        require(minor > 0 and minor == minor.to_integral_value(), "inexact_ledger_amount")
        return int(minor)
    except (InvalidOperation, ValueError, TypeError, OverflowError) as exc:
        raise HTTPException(status_code=409, detail={
            "code": "supplier_invoice_financial_integrity_failed",
            "reason": "invalid_ledger_amount",
        }) from exc


def verify_invoice_totals(invoice: dict[str, Any]) -> int:
    total = halalas(invoice.get("total_halalas"))
    require(invoice.get("currency") == "SAR", "currency_mismatch")
    rows = invoice.get("lines")
    require(isinstance(rows, list) and bool(rows), "missing_lines")
    seen: set[str] = set()
    actual = 0
    for row in rows:
        ids = row.get("piece_ids")
        require(isinstance(ids, list) and bool(ids), "missing_pieces")
        require(all(identifier(p) for p in ids) and len(set(ids)) == len(ids), "invalid_piece_ids")
        require(not seen.intersection(ids), "duplicate_piece_charge")
        seen.update(ids)
        require(row.get("quantity") == len(ids), "quantity_mismatch")
        product = halalas(row.get("product_total_halalas"), positive=False)
        services = halalas(row.get("services_total_halalas"), positive=False)
        require(product + services == halalas(row.get("total_halalas"), positive=False), "line_sum_mismatch")
        require(sum(halalas(s.get("total_halalas"), positive=False) for s in row.get("services", [])) == services, "service_sum_mismatch")
        actual += product + services
    require(actual == total and invoice.get("subtotal_halalas") == total, "invoice_sum_mismatch")
    require(invoice.get("piece_count") == len(seen) and invoice.get("line_count") == len(rows), "invoice_count_mismatch")
    return total


async def verify_persisted_supplier_invoice(
    db: Any, *, user_id: str, invoice_id: str, session_id: str,
    supplier_id: str, expected_total: int | None = None,
    actor_id: str | None = None, mongo_session: Any = None,
) -> dict[str, Any]:
    """Validate actual persisted rows, using the caller's transaction snapshot."""
    kw = {"session": mongo_session} if mongo_session is not None else {}
    require(all(identifier(x) for x in (user_id, invoice_id, session_id, supplier_id)), "missing_identity")
    invoice = await db[INVOICES].find_one({"user_id": user_id, "id": invoice_id}, {"_id": 0}, **kw)
    session = await db[SESSIONS].find_one({"user_id": user_id, "id": session_id}, {"_id": 0}, **kw)
    require(isinstance(invoice, dict) and isinstance(session, dict), "missing_document")
    if invoice.get("mz2_financial_contract") == "mz2_supplier_invoice_v1":
        from supplier_native_invoice_v2 import verify_native_invoice
        require(invoice.get("supplier_id") == supplier_id and invoice.get("session_id") == session_id, "native_source_mismatch")
        return await verify_native_invoice(db, invoice=invoice, session=session, mongo_session=mongo_session,
            expected_total=expected_total, actor_id=actor_id)
    total = verify_invoice_totals(invoice)
    if expected_total is not None:
        require(total == halalas(expected_total), "confirmed_amount_mismatch")
    require(invoice.get("session_id") == session_id, "session_mismatch")
    require(invoice.get("supplier_id") == supplier_id == session.get("supplier_id"), "supplier_mismatch")
    require((invoice.get("supplier_snapshot") or {}).get("id") == supplier_id, "invoice_supplier_snapshot_mismatch")
    require((session.get("supplier_snapshot") or {}).get("id") == supplier_id, "session_supplier_snapshot_mismatch")
    require(identifier(invoice.get("invoice_number")), "missing_invoice_number")
    require(invoice.get("status") == "payable_posted" and invoice.get("experiment_mode") is False, "not_real_invoice")
    require(isinstance(invoice.get("approved_at"), datetime), "missing_approval_time")
    approver = invoice.get("approved_by")
    require(identifier(approver) and approver == invoice.get("supplier_approved_by") == session.get("closed_by"), "actor_mismatch")
    if actor_id is not None:
        require(approver == actor_id, "unexpected_actor")
    for document in (invoice, session):
        require(document.get("financial_invoice_created") is True, "missing_financial_flag")
        require(document.get("liability_created") is True, "missing_liability_flag")
        require(document.get("financial_integrity_verified") is True, "missing_integrity_flag")
        require(document.get("financial_integrity_contract") == CONTRACT, "missing_contract")
    require(session.get("status") == "closed" and session.get("supplier_invoice_id") == invoice_id, "session_not_finalized")
    gid = invoice.get("ledger_txn_group_id")
    require(identifier(gid) and gid == str(uuid.uuid5(uuid.NAMESPACE_URL, f"{invoice_id}:ledger")), "invalid_group_identity")
    entry_ids = invoice.get("ledger_entry_ids")
    require(isinstance(entry_ids, list) and len(entry_ids) == 2 and len(set(entry_ids)) == 2
            and all(identifier(e) for e in entry_ids), "invalid_entry_ids")
    summary = session.get("supplier_invoice")
    require(isinstance(summary, dict), "missing_session_summary")
    for key in ("id", "invoice_number", "session_id", "supplier_id", "total_halalas", "currency",
                "approved_at", "ledger_txn_group_id", "ledger_entry_ids", "status",
                "financial_invoice_created", "liability_created", "financial_integrity_verified"):
        require(key in summary and summary[key] == invoice[key], "session_summary_" + key)
    # Also find any second group linked to this source invoice; it must not exist.
    entries = await db["general_ledger"].find({"user_id": user_id, "$or": [
        {"txn_group_id": gid}, {"metadata.supplier_invoice_v2_id": invoice_id},
    ]}, {"_id": 0}, **kw).to_list(length=3)
    require(len(entries) == 2 and {e.get("id") for e in entries} == set(entry_ids), "ledger_group_cardinality")
    debit = credit = 0
    for entry in entries:
        require(entry.get("txn_group_id") == gid and entry.get("entry_type") == "supplier_invoice", "ledger_source_mismatch")
        require(entry.get("status") == "posted" and entry.get("currency") == "SAR", "ledger_state_mismatch")
        require(entry.get("posted_by") == approver, "ledger_actor_mismatch")
        meta = entry.get("metadata") or {}
        require(meta.get("supplier_invoice_v2_id") == invoice_id
                and meta.get("supplier_receiving_session_id") == session_id
                and meta.get("supplier_id") == supplier_id, "ledger_metadata_mismatch")
        amount = ledger_halalas(entry.get("amount"))
        if entry.get("side") == "credit":
            require(entry.get("entity_type") == "supplier" and entry.get("sub_account") == "payable"
                    and entry.get("entity_id") == supplier_id, "payable_supplier_mismatch")
            credit += amount
        elif entry.get("side") == "debit":
            require(entry.get("entity_type") == "expense" and entry.get("entity_id") == "inventory", "debit_account_mismatch")
            debit += amount
        else:
            require(False, "invalid_ledger_side")
    require(debit == credit == total, "unbalanced_or_mismatched_amount")
    return invoice
