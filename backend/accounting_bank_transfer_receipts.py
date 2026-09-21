"""MZ2-native review of customer bank-transfer receipts.

The customer's selected bank is read from the Salla order itself. Reviewers
never choose a bank or retype an amount per order. They upload the receipt,
visually compare it with the actual incoming bank movement, select that exact
movement, then explicitly approve arrival.

Upload/review is non-financial. Approval is the accounting boundary:
- transfer before delivery -> DR bank / CR customer advance
- once delivered -> DR customer advance / CR revenue / CR sales VAT
Both groups are immutable/idempotent and use the manual MZ2 tax policy.
"""
from __future__ import annotations

from datetime import date, datetime, time, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import hashlib
import re
import uuid
from typing import Any, Literal
from zoneinfo import ZoneInfo

from fastapi import Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import Response
from urllib.parse import quote
from pydantic import BaseModel, ConfigDict, Field, field_validator

from accounting_atomic import atomic_owner
from accounting_mz2_balances import read_mz2_write_balances
from accounting_module_contract import (
    OPERATION_ID,
    accounting_owner_id,
    require_accounting_permission,
)
from accounting_module_status_routes import fresh_accounting_user
from accounting_sales_tax_service import read_policy, sale_snapshot
from ledger_core import post_txn_group


RIYADH = ZoneInfo("Asia/Riyadh")
SOURCE = "accounting_bank_transfer_receipt_p01"
MAX_RECEIPT_BYTES = 5 * 1024 * 1024
MONEY = Decimal("0.01")
ALLOWED_EXTENSIONS = {".pdf", ".jpg", ".jpeg", ".png", ".webp"}
ALLOWED_CONTENT_TYPES = {
    "application/pdf",
    "image/jpeg",
    "image/png",
    "image/webp",
    "application/octet-stream",
}
APPROVE_CONFIRMATION = "CONFIRM_BANK_TRANSFER_RECEIPT"


class BankTransferError(ValueError):
    pass


def _clean(value: Any) -> str:
    if value is None:
        return ""
    return " ".join(str(value).strip().strip("'").split())


def _norm(value: Any) -> str:
    text = _clean(value).lower()
    text = re.sub(r"[\u064B-\u0652]", "", text)
    text = (
        text.replace("أ", "ا").replace("إ", "ا").replace("آ", "ا")
        .replace("ى", "ي").replace("ة", "ه")
    )
    text = re.sub(r"[^\w\u0600-\u06FF]+", " ", text)
    return " ".join(text.split())


def _bank_key(value: Any) -> str:
    text = _norm(value)
    for token in ("مصرف", "بنك", "bank", "الحساب", "حساب"):
        text = re.sub(rf"(^|\s){re.escape(token)}(?=\s|$)", " ", text)
    return " ".join(text.split())


def bank_selected_from_order(payment_method_raw: Any) -> str:
    text = _clean(payment_method_raw)
    if not text:
        raise BankTransferError("bank_transfer_method_missing")
    normalized = _norm(text)
    prefixes = (
        "حواله بنكيه",
        "تحويل بنكي",
        "bank transfer",
        "banktransfer",
    )
    for prefix in prefixes:
        p = _norm(prefix)
        if normalized.startswith(p):
            # Work from the original text so the displayed bank name remains
            # human-readable. Handle Salla's common concatenated form:
            # "حوالة بنكيةمصرف الراجحي".
            raw_patterns = (
                r"^\s*حوال[هة]\s*بنكي[هة]\s*",
                r"^\s*تحويل\s*بنكي\s*",
                r"^\s*bank\s*transfer\s*",
            )
            remainder = text
            for pattern in raw_patterns:
                remainder = re.sub(pattern, "", remainder, count=1, flags=re.IGNORECASE)
            remainder = _clean(remainder)
            if remainder:
                return remainder
            break
    raise BankTransferError("bank_selected_in_order_required")


def _money(value: Any) -> Decimal:
    try:
        result = Decimal(str(value)).quantize(MONEY, rounding=ROUND_HALF_UP)
    except (InvalidOperation, ValueError, TypeError):
        raise BankTransferError("bank_transfer_amount_invalid") from None
    if not result.is_finite() or result <= 0:
        raise BankTransferError("bank_transfer_amount_invalid")
    return result


def _source_day(value: Any) -> str:
    text = _clean(value)
    if not text:
        raise BankTransferError("bank_transfer_date_required")
    try:
        return date.fromisoformat(text[:10]).isoformat()
    except ValueError:
        raise BankTransferError("bank_transfer_date_invalid") from None


def _day_instant(value: str) -> datetime:
    day = date.fromisoformat(value)
    return datetime.combine(day, time.min, RIYADH).astimezone(timezone.utc)


def _delivery_instant(value: Any) -> datetime | None:
    text = _clean(value)
    if not text:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=RIYADH).astimezone(timezone.utc)
        except ValueError:
            pass
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        raise BankTransferError("bank_transfer_delivery_date_invalid") from None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        parsed = parsed.replace(tzinfo=RIYADH)
    return parsed.astimezone(timezone.utc)


def _hash(*parts: Any) -> str:
    raw = "|".join(str(part) for part in parts)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _public(row: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in row.items() if key != "_id"}


async def _cutover(db, owner: str) -> datetime:
    row = await db.settings.find_one(
        {"user_id": owner},
        {"_id": 0, "mezan2_financial_cutover": 1},
    )
    state = (row or {}).get("mezan2_financial_cutover") or {}
    if (
        state.get("operation_id") != OPERATION_ID
        or state.get("status") != "active"
        or not state.get("cutover_at")
    ):
        raise BankTransferError("mz2_p01_not_active")
    try:
        instant = datetime.fromisoformat(str(state["cutover_at"]).replace("Z", "+00:00"))
    except ValueError:
        raise BankTransferError("mz2_cutover_invalid") from None
    if instant.tzinfo is None or instant.utcoffset() is None:
        raise BankTransferError("mz2_cutover_invalid")
    return instant.astimezone(timezone.utc)


async def _resolve_order_bank(db, owner: str, selected_bank: str) -> dict[str, Any]:
    selected_key = _bank_key(selected_bank)
    if not selected_key:
        raise BankTransferError("bank_selected_in_order_required")

    settings = await db.settings.find_one(
        {"user_id": owner},
        {"_id": 0, "mezan2_bank_transfer_bank_aliases": 1},
    ) or {}
    aliases = settings.get("mezan2_bank_transfer_bank_aliases") or {}
    explicit_id = str(aliases.get(selected_key) or "").strip()
    if explicit_id:
        bank = await db.accounts.find_one(
            {
                "user_id": owner,
                "id": explicit_id,
                "account_type": "bank",
                "status": {"$ne": "hidden"},
            },
            {"_id": 0, "id": 1, "name": 1, "account_type": 1},
        )
        if not bank:
            raise BankTransferError("configured_order_bank_missing")
        return {
            "state": "resolved",
            "selected_bank": selected_bank,
            "bank_account_id": bank["id"],
            "bank_account_name": bank.get("name") or selected_bank,
            "resolution": "owner_alias",
        }

    banks = await db.accounts.find(
        {
            "user_id": owner,
            "account_type": "bank",
            "status": {"$ne": "hidden"},
        },
        {"_id": 0, "id": 1, "name": 1, "account_type": 1},
    ).to_list(200)
    exact = [
        bank for bank in banks
        if _bank_key(bank.get("name")) == selected_key
    ]
    if len(exact) == 1:
        bank = exact[0]
        return {
            "state": "resolved",
            "selected_bank": selected_bank,
            "bank_account_id": bank["id"],
            "bank_account_name": bank.get("name") or selected_bank,
            "resolution": "exact_name",
        }
    if len(exact) > 1:
        raise BankTransferError("order_bank_mapping_ambiguous")

    fuzzy = []
    for bank in banks:
        key = _bank_key(bank.get("name"))
        if len(selected_key) >= 3 and key and (
            selected_key in key or key in selected_key
        ):
            fuzzy.append(bank)
    if len(fuzzy) == 1:
        bank = fuzzy[0]
        return {
            "state": "resolved",
            "selected_bank": selected_bank,
            "bank_account_id": bank["id"],
            "bank_account_name": bank.get("name") or selected_bank,
            "resolution": "unique_name_alias",
        }
    if len(fuzzy) > 1:
        raise BankTransferError("order_bank_mapping_ambiguous")
    return {
        "state": "unresolved",
        "selected_bank": selected_bank,
        "bank_account_id": None,
        "bank_account_name": None,
        "resolution": "not_configured",
    }


async def _current_evidence(db, owner: str, evidence_id: str) -> dict[str, Any]:
    evidence = await db.mz2_salla_order_evidence.find_one(
        {"user_id": owner, "id": evidence_id},
        {"_id": 0},
    )
    if not evidence:
        raise BankTransferError("order_evidence_missing")
    if evidence.get("conflict"):
        raise BankTransferError("order_evidence_conflict")
    if evidence.get("accounting_provider") != "bank_transfer":
        raise BankTransferError("order_is_not_bank_transfer")
    if Decimal(str(evidence.get("refunded_sar") or "0")) != 0:
        raise BankTransferError("bank_transfer_refund_requires_review")
    return evidence


def _expected_amount(evidence: dict[str, Any]) -> Decimal:
    try:
        amount = Decimal(str(evidence.get("current_net_sar") or "0")).quantize(MONEY)
    except InvalidOperation:
        raise BankTransferError("bank_transfer_order_amount_invalid") from None
    if amount <= 0:
        raise BankTransferError("bank_transfer_order_amount_invalid")
    return amount


async def _receipt_duplicate_guard(
    db,
    *,
    owner: str,
    review_id: str,
    file_sha256: str,
    bank_reference: str,
) -> None:
    duplicate_file = await db.mz2_bank_transfer_receipts.find_one(
        {
            "user_id": owner,
            "id": {"$ne": review_id},
            "receipt_file_sha256": file_sha256,
            "status": {"$in": [
                "pending_approval",
                "confirmed_waiting_delivery",
                "recognized",
            ]},
        },
        {"_id": 0, "id": 1, "order_number": 1},
    )
    if duplicate_file:
        raise BankTransferError("receipt_file_already_used_for_another_order")
    if bank_reference:
        duplicate_ref = await db.mz2_bank_transfer_receipts.find_one(
            {
                "user_id": owner,
                "id": {"$ne": review_id},
                "bank_reference_key": bank_reference.upper(),
                "status": {"$in": [
                    "pending_approval",
                    "confirmed_waiting_delivery",
                    "recognized",
                ]},
            },
            {"_id": 0, "id": 1, "order_number": 1},
        )
        if duplicate_ref:
            raise BankTransferError("bank_reference_already_used")


async def bank_transfer_queue(db, *, owner: str, limit: int = 300) -> dict[str, Any]:
    evidence_rows = await db.mz2_salla_order_evidence.find(
        {
            "user_id": owner,
            "accounting_provider": "bank_transfer",
        },
        {"_id": 0},
    ).sort([("updated_source_text", -1), ("order_number", -1)]).limit(
        min(max(limit, 1), 500)
    ).to_list(500)

    items = []
    for evidence in evidence_rows:
        try:
            selected_bank = (
                evidence.get("bank_selected_from_order")
                or bank_selected_from_order(evidence.get("payment_method_raw"))
            )
            bank = await _resolve_order_bank(db, owner, selected_bank)
            error = None
        except BankTransferError as exc:
            selected_bank = evidence.get("bank_selected_from_order") or ""
            bank = {
                "state": "unresolved",
                "selected_bank": selected_bank,
                "bank_account_id": None,
                "bank_account_name": None,
                "resolution": "error",
            }
            error = str(exc)
        review_id = str(evidence.get("bank_transfer_receipt_review_id") or "")
        review = None
        if review_id:
            review = await db.mz2_bank_transfer_receipts.find_one(
                {"user_id": owner, "id": review_id},
                {"_id": 0, "receipt_file_id": 0},
            )
        items.append({
            "evidence_id": evidence["id"],
            "order_number": evidence.get("order_number"),
            "order_status": evidence.get("order_status"),
            "expected_amount": format(_expected_amount(evidence), ".2f"),
            "delivery_source_text": evidence.get("delivery_source_text"),
            "selected_bank": bank.get("selected_bank") or selected_bank,
            "bank_resolution": bank,
            "state": (
                review.get("status") if review else
                "needs_bank_mapping" if bank.get("state") != "resolved" else
                "waiting_receipt"
            ),
            "review": review,
            "reason": error,
        })
    summary = {}
    for item in items:
        summary[item["state"]] = summary.get(item["state"], 0) + 1
    return {
        "items": items,
        "total": len(items),
        "by_state": summary,
    }


async def bank_transfer_candidates(
    db,
    *,
    owner: str,
    review_id: str,
    limit: int = 30,
) -> dict[str, Any]:
    review = await db.mz2_bank_transfer_receipts.find_one(
        {"_id": review_id, "user_id": owner},
        {"_id": 0},
    )
    if not review:
        raise BankTransferError("bank_transfer_review_missing")
    expected = Decimal(str(review["expected_amount"]))
    rows = await db.mz2_daily_movements.find(
        {
            "user_id": owner,
            "bank_account_id": review["bank_account_id"],
            "direction": "in",
            "status": "unclassified",
            "receipt_id": None,
            "accounting_event_id": {"$in": [None, ""]},
            "explicit_provider": None,
            "confirmed_provider": {"$in": [None, ""]},
            "suggested_provider": None,
        },
        {"_id": 0},
    ).sort([("movement_date", -1), ("created_at", -1)]).limit(
        min(max(limit, 1), 100)
    ).to_list(100)
    items = []
    for movement in rows:
        try:
            amount = Decimal(str(movement.get("amount") or "0")).quantize(MONEY)
        except InvalidOperation:
            continue
        items.append({
            "id": movement["id"],
            "movement_date": movement.get("movement_date"),
            "amount": format(amount, ".2f"),
            "amount_matches": amount == expected,
            "description": movement.get("description") or "",
            "reference": movement.get("reference"),
            "bank_account_id": movement.get("bank_account_id"),
            "bank_account_name": movement.get("bank_account_name"),
        })
    items.sort(key=lambda item: (not item["amount_matches"], str(item.get("movement_date") or "")), reverse=False)
    return {
        "review_id": review_id,
        "expected_amount": review["expected_amount"],
        "bank_account_id": review["bank_account_id"],
        "bank_account_name": review["bank_account_name"],
        "items": items,
        "exact_count": sum(1 for item in items if item["amount_matches"]),
    }


async def save_receipt_draft(
    db,
    *,
    owner: str,
    actor: dict[str, Any],
    evidence_id: str,
    filename: str,
    content_type: str,
    content: bytes,
    notes: str,
) -> dict[str, Any]:
    evidence = await _current_evidence(db, owner, evidence_id)
    expected = _expected_amount(evidence)
    selected_bank = (
        evidence.get("bank_selected_from_order")
        or bank_selected_from_order(evidence.get("payment_method_raw"))
    )
    bank = await _resolve_order_bank(db, owner, selected_bank)
    if bank["state"] != "resolved":
        raise BankTransferError("order_bank_not_configured")

    if len(content) <= 0 or len(content) > MAX_RECEIPT_BYTES:
        raise BankTransferError("receipt_file_size_invalid")
    ext = "." + filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext not in ALLOWED_EXTENSIONS:
        raise BankTransferError("receipt_file_type_invalid")
    if content_type and content_type not in ALLOWED_CONTENT_TYPES:
        raise BankTransferError("receipt_file_type_invalid")

    review_id = _hash(owner, "bank_transfer_receipt", evidence["order_number"])
    file_sha256 = hashlib.sha256(content).hexdigest()
    await _receipt_duplicate_guard(
        db,
        owner=owner,
        review_id=review_id,
        file_sha256=file_sha256,
        bank_reference="",
    )

    current = await db.mz2_bank_transfer_receipts.find_one(
        {"_id": review_id, "user_id": owner}
    )
    if current and current.get("status") not in {"pending_approval", "needs_review"}:
        return _public(current)

    file_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    await db.mz2_bank_transfer_receipt_files.insert_one({
        "_id": _hash(owner, file_id),
        "id": file_id,
        "user_id": owner,
        "review_id": review_id,
        "order_number": evidence["order_number"],
        "filename": filename,
        "content_type": content_type or "application/octet-stream",
        "size": len(content),
        "sha256": file_sha256,
        "content": content,
        "uploaded_by": actor["id"],
        "uploaded_at": now,
    })

    draft = {
        "_id": review_id,
        "id": review_id,
        "user_id": owner,
        "order_evidence_id": evidence["id"],
        "order_economic_hash": evidence.get("economic_hash"),
        "order_number": evidence["order_number"],
        "selected_bank_from_order": selected_bank,
        "bank_account_id": bank["bank_account_id"],
        "bank_account_name": bank["bank_account_name"],
        "bank_resolution": bank["resolution"],
        "expected_amount": format(expected, ".2f"),
        "receipt_file_id": file_id,
        "receipt_file_sha256": file_sha256,
        "receipt_filename": filename,
        "status": "pending_approval",
        "review_reasons": [],
        "notes": _clean(notes)[:1000],
        "created_by": current.get("created_by") if current else actor["id"],
        "created_at": current.get("created_at") if current else now,
        "updated_by": actor["id"],
        "updated_at": now,
    }
    if current:
        await db.mz2_bank_transfer_receipts.replace_one(
            {"_id": review_id, "user_id": owner},
            draft,
        )
    else:
        await db.mz2_bank_transfer_receipts.insert_one(draft)

    await db.mz2_bank_transfer_receipt_audit.insert_one({
        "user_id": owner,
        "review_id": review_id,
        "order_number": evidence["order_number"],
        "action": "receipt_uploaded",
        "actor_id": actor["id"],
        "at": now,
        "file_sha256": file_sha256,
        "expected_amount": draft["expected_amount"],
        "selected_bank_from_order": selected_bank,
        "bank_account_id": bank["bank_account_id"],
    })
    await db.mz2_salla_order_evidence.update_one(
        {"user_id": owner, "id": evidence["id"]},
        {"$set": {
            "bank_selected_from_order": selected_bank,
            "bank_transfer_receipt_review_id": review_id,
            "bank_transfer_receipt_status": draft["status"],
        }},
    )
    return _public(draft)


def _tax_event(evidence: dict[str, Any], amount: Decimal, recognized_at: datetime) -> dict[str, Any]:
    return {
        "kind": "sale",
        "provider": "bank_transfer",
        "provider_payment_id": evidence.get("bank_transfer_receipt_review_id") or evidence["id"],
        "canonical_event_id": evidence["id"],
        "order_number": evidence["order_number"],
        "amount": format(amount, ".2f"),
        "sale_amount": format(amount, ".2f"),
        "currency": "SAR",
        "recognized_at": recognized_at.isoformat(),
    }


async def _post_sale_from_advance(
    db,
    *,
    owner: str,
    actor: dict[str, Any],
    evidence: dict[str, Any],
    review: dict[str, Any],
) -> dict[str, Any]:
    if review.get("sale_txn_group_id"):
        return {
            "state": "already_posted",
            "txn_group_id": review["sale_txn_group_id"],
        }
    delivered = _delivery_instant(evidence.get("delivery_source_text"))
    if not delivered or evidence.get("order_status") != "تم التوصيل":
        raise BankTransferError("bank_transfer_order_not_delivered")
    received_at = _day_instant(review["transfer_date"])
    if delivered < received_at:
        raise BankTransferError("bank_transfer_delivery_precedes_confirmed_receipt")
    cut = await _cutover(db, owner)
    if delivered < cut:
        raise BankTransferError("pre_cutover_recognition")
    if delivered > datetime.now(timezone.utc):
        raise BankTransferError("future_delivery_event")

    amount = Decimal(review["received_amount"])
    event = _tax_event(evidence, amount, delivered)
    tax = sale_snapshot(
        await read_policy(db, owner),
        event,
        {"tax_amount": evidence.get("source_tax_sar")},
    )
    advance_id = review["advance_id"]
    existing = await db.general_ledger.find_one({
        "user_id": owner,
        "status": {"$in": ["posted", "reversed"]},
        "$or": [
            {"metadata.order_reference_id": evidence["order_number"], "entry_type": {"$in": ["bnpl_sale", "cod_sale", "bank_transfer_sale"]}},
            {"metadata.bank_transfer_review_id": review["id"], "entry_type": "bank_transfer_sale"},
        ],
    })
    if existing:
        raise BankTransferError("existing_journal_requires_review")

    result = await post_txn_group(
        db,
        user_id=owner,
        actor_id=actor["id"],
        actor_name=actor.get("name") or actor.get("email") or actor["id"],
        txn_type="mz2_bank_transfer_sale",
        notes=f"بيع حوالة بنكية — {evidence['order_number']}",
        metadata={
            "operation_id": OPERATION_ID,
            "source": SOURCE,
            "bank_transfer_review_id": review["id"],
            "bank_transfer_event_kind": "sale_from_advance",
            "accounting_at": delivered.isoformat(),
            "order_reference_id": evidence["order_number"],
            "advance_id": advance_id,
            "bank_account_id": review["bank_account_id"],
            "selected_bank_from_order": review["selected_bank_from_order"],
            "sales_tax": tax,
        },
        entries=[
            {
                "entity_type": "liability",
                "entity_id": advance_id,
                "sub_account": "customer_advance",
                "side": "debit",
                "amount": tax["gross"],
                "entry_type": "bank_transfer_sale",
            },
            {
                "entity_type": "revenue",
                "entity_id": "bnpl_sales",
                "side": "credit",
                "amount": tax["net"],
                "entry_type": "bank_transfer_sale",
            },
            *([{
                "entity_type": "tax",
                "entity_id": "sales_vat_payable",
                "side": "credit",
                "amount": tax["tax"],
                "entry_type": "bank_transfer_sale",
            }] if Decimal(tax["tax"]) > 0 else []),
        ],
    )
    return {"state": "posted", "txn_group_id": result["txn_group_id"], "tax": tax}


async def approve_receipt(
    db,
    *,
    owner: str,
    actor: dict[str, Any],
    review_id: str,
    movement_id: str,
) -> dict[str, Any]:
    async def commit(scoped):
        review = await scoped.mz2_bank_transfer_receipts.find_one(
            {"_id": review_id, "user_id": owner}
        )
        if not review:
            raise BankTransferError("bank_transfer_review_missing")
        if review.get("status") in {"confirmed_waiting_delivery", "recognized"}:
            if review.get("bank_movement_id") != movement_id:
                raise BankTransferError("bank_transfer_already_approved_with_other_movement")
            return _public(review)
        if review.get("status") != "pending_approval":
            raise BankTransferError("bank_transfer_review_not_approvable")

        evidence = await _current_evidence(scoped, owner, review["order_evidence_id"])
        if evidence.get("economic_hash") != review.get("order_economic_hash"):
            raise BankTransferError("order_evidence_changed_review_again")
        selected_bank = (
            evidence.get("bank_selected_from_order")
            or bank_selected_from_order(evidence.get("payment_method_raw"))
        )
        if selected_bank != review.get("selected_bank_from_order"):
            raise BankTransferError("selected_bank_changed_review_again")
        resolved = await _resolve_order_bank(scoped, owner, selected_bank)
        if (
            resolved.get("state") != "resolved"
            or resolved.get("bank_account_id") != review.get("bank_account_id")
        ):
            raise BankTransferError("order_bank_mapping_changed_review_again")

        movement = await scoped.mz2_daily_movements.find_one({
            "user_id": owner,
            "id": movement_id,
        })
        if not movement:
            raise BankTransferError("bank_movement_missing")
        if movement.get("direction") != "in":
            raise BankTransferError("bank_movement_must_be_incoming")
        if movement.get("bank_account_id") != review.get("bank_account_id"):
            raise BankTransferError("bank_movement_wrong_bank")
        if movement.get("status") != "unclassified":
            raise BankTransferError("bank_movement_already_classified")
        if (
            movement.get("receipt_id")
            or movement.get("accounting_event_id")
            or movement.get("explicit_provider")
            or movement.get("confirmed_provider")
            or movement.get("suggested_provider")
        ):
            raise BankTransferError("bank_movement_reserved_for_other_workflow")

        received = Decimal(str(movement.get("amount") or "0")).quantize(MONEY)
        expected = _expected_amount(evidence)
        if received != expected:
            raise BankTransferError("bank_movement_amount_mismatch")

        transfer_date = _source_day(movement.get("movement_date"))
        received_at = _day_instant(transfer_date)
        cut = await _cutover(scoped, owner)
        if received_at < cut or received_at > datetime.now(timezone.utc):
            raise BankTransferError("bank_transfer_date_outside_cutover_or_future")
        bank_reference = _clean(movement.get("reference") or "")

        duplicate_movement = await scoped.mz2_bank_transfer_receipts.find_one({
            "user_id": owner,
            "id": {"$ne": review_id},
            "bank_movement_id": movement_id,
            "status": {"$in": ["confirmed_waiting_delivery", "recognized"]},
        })
        if duplicate_movement:
            raise BankTransferError("bank_movement_already_used_for_another_order")

        await _receipt_duplicate_guard(
            scoped,
            owner=owner,
            review_id=review_id,
            file_sha256=review["receipt_file_sha256"],
            bank_reference=bank_reference,
        )
        await read_mz2_write_balances(
            scoped,
            owner=owner,
            required_accounts=[
                ("bank", review["bank_account_id"], "main"),
            ],
        )

        advance_id = _hash(owner, "bank_transfer_advance", evidence["order_number"])
        receipt_event_id = _hash(owner, "bank_transfer_receipt", review_id)
        prior = await scoped.mz2_bank_transfer_receipt_events.find_one(
            {"_id": receipt_event_id, "user_id": owner}
        )
        if prior and prior.get("status") == "posted":
            if prior.get("facts", {}).get("bank_movement_id") != movement_id:
                raise BankTransferError("bank_transfer_receipt_event_conflict")
            receipt_group_id = prior["txn_group_id"]
        elif prior:
            raise BankTransferError("bank_transfer_receipt_event_requires_recovery")
        else:
            facts = {
                "review_id": review_id,
                "order_number": evidence["order_number"],
                "bank_account_id": review["bank_account_id"],
                "amount": format(received, ".2f"),
                "transfer_date": transfer_date,
                "bank_reference": bank_reference or None,
                "bank_movement_id": movement_id,
                "bank_movement_file_id": movement.get("file_id"),
                "receipt_file_sha256": review["receipt_file_sha256"],
            }
            await scoped.mz2_bank_transfer_receipt_events.insert_one({
                "_id": receipt_event_id,
                "id": receipt_event_id,
                "user_id": owner,
                "status": "posting",
                "facts": facts,
                "economic_hash": _hash(str(sorted(facts.items()))),
                "created_by": actor["id"],
                "created_at": datetime.now(timezone.utc).isoformat(),
            })
            receipt_group = await post_txn_group(
                scoped,
                user_id=owner,
                actor_id=actor["id"],
                actor_name=actor.get("name") or actor.get("email") or actor["id"],
                txn_type="mz2_bank_transfer_advance",
                notes=f"تحويل بنكي من عميل — {evidence['order_number']}",
                metadata={
                    "operation_id": OPERATION_ID,
                    "source": SOURCE,
                    "bank_transfer_event_id": receipt_event_id,
                    "bank_transfer_event_kind": "customer_receipt",
                    "bank_transfer_review_id": review_id,
                    "accounting_at": received_at.isoformat(),
                    "order_reference_id": evidence["order_number"],
                    "bank_account_id": review["bank_account_id"],
                    "bank_reference": bank_reference or None,
                    "bank_movement_id": movement_id,
                    "bank_movement_file_id": movement.get("file_id"),
                    "selected_bank_from_order": selected_bank,
                    "receipt_file_sha256": review["receipt_file_sha256"],
                    "customer_advance_id": advance_id,
                },
                entries=[
                    {
                        "entity_type": "bank",
                        "entity_id": review["bank_account_id"],
                        "sub_account": "main",
                        "side": "debit",
                        "amount": format(received, ".2f"),
                        "entry_type": "bank_transfer_advance",
                    },
                    {
                        "entity_type": "liability",
                        "entity_id": advance_id,
                        "sub_account": "customer_advance",
                        "side": "credit",
                        "amount": format(received, ".2f"),
                        "entry_type": "bank_transfer_advance",
                    },
                ],
            )
            receipt_group_id = receipt_group["txn_group_id"]
            await scoped.mz2_bank_transfer_receipt_events.update_one(
                {"_id": receipt_event_id, "user_id": owner, "status": "posting"},
                {"$set": {
                    "status": "posted",
                    "txn_group_id": receipt_group_id,
                    "posted_at": datetime.now(timezone.utc).isoformat(),
                }},
            )

        next_status = "confirmed_waiting_delivery"
        sale_group_id = None
        tax = None
        effective_review = {
            **review,
            "advance_id": advance_id,
            "received_amount": format(received, ".2f"),
            "transfer_date": transfer_date,
            "bank_reference": bank_reference or None,
            "bank_movement_id": movement_id,
        }
        if (
            evidence.get("order_status") == "تم التوصيل"
            and evidence.get("delivery_source_text")
        ):
            sale = await _post_sale_from_advance(
                scoped,
                owner=owner,
                actor=actor,
                evidence=evidence,
                review=effective_review,
            )
            sale_group_id = sale["txn_group_id"]
            tax = sale.get("tax")
            next_status = "recognized"

        now = datetime.now(timezone.utc).isoformat()
        changes = {
            "status": next_status,
            "approved_by": actor["id"],
            "approved_at": now,
            "advance_id": advance_id,
            "received_amount": format(received, ".2f"),
            "transfer_date": transfer_date,
            "bank_reference": bank_reference or None,
            "bank_reference_key": bank_reference.upper() if bank_reference else None,
            "bank_movement_id": movement_id,
            "bank_movement_file_id": movement.get("file_id"),
            "receipt_txn_group_id": receipt_group_id,
            "sale_txn_group_id": sale_group_id,
            "sales_tax": tax,
        }
        await scoped.mz2_bank_transfer_receipts.update_one(
            {"_id": review_id, "user_id": owner, "status": "pending_approval"},
            {"$set": changes},
        )
        consumed = await scoped.mz2_daily_movements.update_one(
            {
                "_id": movement["_id"],
                "user_id": owner,
                "status": "unclassified",
                "accounting_event_id": {"$in": [None, ""]},
            },
            {"$set": {
                "status": "accounting_posted",
                "accounting_action": "bank_transfer_customer_receipt",
                "accounting_event_id": receipt_event_id,
                "accounting_txn_group_id": receipt_group_id,
                "accounting_order_number": evidence["order_number"],
                "accounting_review_id": review_id,
                "consumed_at": now,
                "consumed_by": actor["id"],
            }},
        )
        if consumed.modified_count != 1:
            raise BankTransferError("bank_movement_concurrent_consumption")

        await scoped.mz2_salla_order_evidence.update_one(
            {"user_id": owner, "id": evidence["id"]},
            {"$set": {
                "bank_selected_from_order": selected_bank,
                "bank_transfer_receipt_review_id": review_id,
                "bank_transfer_receipt_status": next_status,
                "bank_transfer_advance_id": advance_id,
                "bank_transfer_receipt_txn_group_id": receipt_group_id,
                "bank_transfer_sale_txn_group_id": sale_group_id,
                "status": (
                    "recognized_bank_transfer"
                    if next_status == "recognized"
                    else "bank_transfer_confirmed_waiting_delivery"
                ),
            }},
        )
        await scoped.mz2_bank_transfer_receipt_audit.insert_one({
            "user_id": owner,
            "review_id": review_id,
            "order_number": evidence["order_number"],
            "action": "receipt_and_bank_movement_approved",
            "actor_id": actor["id"],
            "at": now,
            "bank_movement_id": movement_id,
            "receipt_txn_group_id": receipt_group_id,
            "sale_txn_group_id": sale_group_id,
        })
        return _public({**review, **changes})

    return await atomic_owner(db, owner, commit)


async def convert_confirmed_deliveries(
    db,
    *,
    owner: str,
    actor: dict[str, Any],
    file_id: str | None = None,
    limit: int = 300,
    dry_run: bool = True,
) -> dict[str, Any]:
    query: dict[str, Any] = {
        "user_id": owner,
        "accounting_provider": "bank_transfer",
        "bank_transfer_receipt_status": "confirmed_waiting_delivery",
        "delivery_source_text": {"$nin": [None, ""]},
        "order_status": "تم التوصيل",
    }
    if file_id:
        query["latest_file_id"] = file_id
    rows = await db.mz2_salla_order_evidence.find(
        query, {"_id": 0}
    ).sort("updated_source_text", 1).limit(min(max(limit, 1), 500)).to_list(500)

    items = []
    for evidence in rows:
        review = await db.mz2_bank_transfer_receipts.find_one(
            {
                "user_id": owner,
                "id": evidence.get("bank_transfer_receipt_review_id"),
                "status": "confirmed_waiting_delivery",
            },
            {"_id": 0},
        )
        if not review:
            items.append({
                "order_number": evidence["order_number"],
                "state": "waiting",
                "reason": "approved_receipt_missing",
            })
            continue
        try:
            delivered = _delivery_instant(evidence.get("delivery_source_text"))
            received = _day_instant(review["transfer_date"])
            if not delivered or delivered < received:
                raise BankTransferError("bank_transfer_delivery_precedes_confirmed_receipt")
            if dry_run:
                items.append({
                    "order_number": evidence["order_number"],
                    "state": "eligible",
                    "review_id": review["id"],
                })
                continue

            async def commit(scoped):
                latest_evidence = await _current_evidence(scoped, owner, evidence["id"])
                latest_review = await scoped.mz2_bank_transfer_receipts.find_one(
                    {
                        "user_id": owner,
                        "id": review["id"],
                        "status": "confirmed_waiting_delivery",
                    }
                )
                if not latest_review:
                    raise BankTransferError("approved_receipt_missing")
                sale = await _post_sale_from_advance(
                    scoped,
                    owner=owner,
                    actor=actor,
                    evidence=latest_evidence,
                    review=latest_review,
                )
                now = datetime.now(timezone.utc).isoformat()
                await scoped.mz2_bank_transfer_receipts.update_one(
                    {"_id": review["id"], "user_id": owner},
                    {"$set": {
                        "status": "recognized",
                        "sale_txn_group_id": sale["txn_group_id"],
                        "sales_tax": sale.get("tax"),
                        "recognized_at": now,
                    }},
                )
                await scoped.mz2_salla_order_evidence.update_one(
                    {"user_id": owner, "id": evidence["id"]},
                    {"$set": {
                        "status": "recognized_bank_transfer",
                        "bank_transfer_receipt_status": "recognized",
                        "bank_transfer_sale_txn_group_id": sale["txn_group_id"],
                    }},
                )
                return sale

            sale = await atomic_owner(db, owner, commit)
            items.append({
                "order_number": evidence["order_number"],
                "state": sale["state"],
                "txn_group_id": sale.get("txn_group_id"),
            })
        except (BankTransferError, HTTPException) as exc:
            items.append({
                "order_number": evidence["order_number"],
                "state": "blocked",
                "reason": exc.detail if isinstance(exc, HTTPException) else str(exc),
            })
    return {
        "dry_run": dry_run,
        "file_id": file_id,
        "candidate_count": len(rows),
        "posted_count": sum(1 for item in items if item["state"] == "posted"),
        "eligible_count": sum(1 for item in items if item["state"] == "eligible"),
        "blocked_count": sum(1 for item in items if item["state"] in {"blocked", "waiting"}),
        "items": items,
    }


class ApproveIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    movement_id: str = Field(min_length=1, max_length=200)
    confirmation: Literal["CONFIRM_BANK_TRANSFER_RECEIPT"]

    @field_validator("movement_id")
    @classmethod
    def strip_movement_id(cls, value: str) -> str:
        return value.strip()


class ConvertBatchIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    file_id: str | None = Field(default=None, max_length=200)
    limit: int = Field(default=300, ge=1, le=500)
    dry_run: bool = True

    @field_validator("file_id")
    @classmethod
    def strip_file_id(cls, value):
        return value.strip() if isinstance(value, str) else value


def install_bank_transfer_receipt_routes(router, db, current_user) -> None:
    base = "/accounting-module/bank-transfer-receipts"

    async def scope(user: dict[str, Any], permission: str):
        actor = await fresh_accounting_user(db, user)
        require_accounting_permission(actor, permission)
        owner = accounting_owner_id(actor)
        if not owner:
            raise HTTPException(403, "accounting_owner_scope_missing")
        return actor, owner

    @router.get(base)
    async def queue(limit: int = 300, user: dict = Depends(current_user)):
        _, owner = await scope(user, "accounting.movements.view")
        return await bank_transfer_queue(db, owner=owner, limit=limit)

    @router.post(base + "/{evidence_id}/receipt")
    async def upload_receipt(
        evidence_id: str,
        file: UploadFile = File(...),
        notes: str = Form(""),
        user: dict = Depends(current_user),
    ):
        actor, owner = await scope(user, "accounting.movements.import")
        content = await file.read(MAX_RECEIPT_BYTES + 1)
        try:
            async def commit(scoped):
                return await save_receipt_draft(
                    scoped,
                    owner=owner,
                    actor=actor,
                    evidence_id=evidence_id,
                    filename=file.filename or "receipt",
                    content_type=file.content_type or "",
                    content=content,
                    notes=notes,
                )
            return await atomic_owner(db, owner, commit)
        except BankTransferError as exc:
            raise HTTPException(409, detail={"code": str(exc), "message": str(exc)}) from None

    @router.get(base + "/{review_id}/bank-candidates")
    async def candidates(
        review_id: str,
        limit: int = 30,
        user: dict = Depends(current_user),
    ):
        _, owner = await scope(user, "accounting.movements.view")
        try:
            return await bank_transfer_candidates(
                db,
                owner=owner,
                review_id=review_id,
                limit=limit,
            )
        except BankTransferError as exc:
            raise HTTPException(409, detail={"code": str(exc), "message": str(exc)}) from None

    @router.get(base + "/{review_id}/receipt")
    async def receipt_file(
        review_id: str,
        user: dict = Depends(current_user),
    ):
        _, owner = await scope(user, "accounting.movements.view")
        review = await db.mz2_bank_transfer_receipts.find_one(
            {"user_id": owner, "id": review_id},
            {"_id": 0, "receipt_file_id": 1},
        )
        if not review or not review.get("receipt_file_id"):
            raise HTTPException(404, "bank_transfer_receipt_file_missing")
        blob = await db.mz2_bank_transfer_receipt_files.find_one(
            {"user_id": owner, "id": review["receipt_file_id"]},
            {"_id": 0},
        )
        if not blob:
            raise HTTPException(404, "bank_transfer_receipt_file_missing")
        filename = _clean(blob.get("filename") or "receipt").replace("\\", "/").split("/")[-1]
        return Response(
            bytes(blob["content"]),
            media_type=blob.get("content_type") or "application/octet-stream",
            headers={
                "Content-Disposition": "inline; filename*=UTF-8''" + quote(filename, safe=""),
                "Cache-Control": "private, no-store",
                "X-Content-Type-Options": "nosniff",
                "X-Content-SHA256": blob.get("sha256") or "",
            },
        )

    @router.post(base + "/{review_id}/approve")
    async def approve(
        review_id: str,
        payload: ApproveIn,
        user: dict = Depends(current_user),
    ):
        actor, owner = await scope(user, "accounting.receivables.post")
        try:
            return await approve_receipt(
                db,
                owner=owner,
                actor=actor,
                review_id=review_id,
                movement_id=payload.movement_id,
            )
        except BankTransferError as exc:
            raise HTTPException(409, detail={"code": str(exc), "message": str(exc)}) from None

    @router.post(base + "/convert-delivered")
    async def convert_delivered(
        payload: ConvertBatchIn,
        user: dict = Depends(current_user),
    ):
        actor, owner = await scope(
            user,
            "accounting.movements.view" if payload.dry_run else "accounting.receivables.post",
        )
        return await convert_confirmed_deliveries(
            db,
            owner=owner,
            actor=actor,
            file_id=payload.file_id or None,
            limit=payload.limit,
            dry_run=payload.dry_run,
        )
