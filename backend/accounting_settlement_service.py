"""P01 settlement accounting service for MZ2-FIN-CUTOVER-001.

Pure amount/reconciliation helpers plus the only ledger bridge used by the
unified accounting settlements page. Legacy balances are never read here.
"""
from __future__ import annotations

import hashlib
import re
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
from typing import Any

from fastapi import HTTPException

from accounting_module_contract import OPERATION_ID
from ledger_core import compute_balance, post_txn_group, write_audit

MONEY = Decimal("0.01")
PROVIDERS = ("salla", "tamara", "tabby", "emkan")
PROVIDER_ALIASES = {
    "salla": "salla",
    "tamara": "tamara",
    "tabby": "tabby",
    "emkan": "emkan",
    "imkan": "emkan",
}
PROVIDER_LABELS = {
    "salla": "سلة",
    "tamara": "تمارا",
    "tabby": "تابي",
    "emkan": "إمكان",
}
BLOCKING_REASON_CODES = frozenset({
    "missing_bank",
    "missing_statement_reference",
    "unmatched_rows",
    "statement_equation_difference",
    "statement_rows_difference",
    "source_requires_review",
    "negative_bank_net",
    "zero_receivable_close",
})


def _money(value: Any) -> float:
    return float(
        Decimal(str(value or 0)).quantize(MONEY, rounding=ROUND_HALF_UP)
    )


def canonical_provider(value: str | None) -> str:
    provider = PROVIDER_ALIASES.get(str(value or "").strip().lower())
    if not provider:
        raise ValueError("المزود غير مدعوم في مرحلة التسويات")
    return provider


def provider_label(provider: str) -> str:
    return PROVIDER_LABELS[canonical_provider(provider)]


def statement_reference_from_file(file_doc: dict[str, Any]) -> str:
    header = file_doc.get("header") or {}
    for key in (
        "invoice_number",
        "statement_id",
        "settlement_reference",
        "statement_number",
        "po_reference",
    ):
        value = str(header.get(key) or "").strip()
        if value:
            return value
    for key in ("invoice_number", "statement_reference"):
        value = str(file_doc.get(key) or "").strip()
        if value:
            return value
    return ""


def _iso_date(value: Any) -> str | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    match = re.search(r"\d{4}-\d{2}-\d{2}", raw)
    if match:
        return match.group(0)
    for pattern in (r"(\d{2})/(\d{2})/(\d{4})", r"(\d{2})-(\d{2})-(\d{4})"):
        match = re.search(pattern, raw)
        if match:
            day, month, year = match.groups()
            try:
                return datetime(int(year), int(month), int(day)).date().isoformat()
            except ValueError:
                return None
    return None


def period_from_file(file_doc: dict[str, Any]) -> tuple[str | None, str | None, str | None]:
    header = file_doc.get("header") or {}
    statement_date = (
        _iso_date(header.get("settlement_date"))
        or _iso_date(header.get("statement_date"))
        or _iso_date(header.get("statement_date_raw"))
        or _iso_date(file_doc.get("settlement_date"))
    )
    period_from = _iso_date(header.get("period_start"))
    period_to = _iso_date(header.get("period_end"))
    if not (period_from and period_to):
        raw_period = str(header.get("statement_period") or "").strip()
        found = re.findall(
            r"(?:\d{4}-\d{2}-\d{2}|\d{2}/\d{2}/\d{4}|\d{2}-\d{2}-\d{4})",
            raw_period,
        )
        if len(found) >= 2:
            period_from = period_from or _iso_date(found[0])
            period_to = period_to or _iso_date(found[1])
    return period_from, period_to, statement_date


def amounts_from_settlement_file(file_doc: dict[str, Any]) -> dict[str, float]:
    totals = file_doc.get("totals") or {}
    return normalize_amounts({
        "gross_sales": totals.get("gross"),
        "refund_full": totals.get("refund_full"),
        "refund_partial": totals.get("refund_partial"),
        "commission": totals.get("fees"),
        "commission_vat": totals.get("fees_vat"),
        "settlement_fee": totals.get("settlement_fee"),
        "settlement_fee_vat": totals.get("settlement_fee_vat"),
        "wallet_purchases": totals.get("salla_purchases_total"),
        "cancellation_amount": totals.get("canceled_amount"),
        "cancellation_fees": totals.get("canceled_fees"),
        "cancellation_fees_vat": totals.get("canceled_fees_vat"),
        "other_deductions": totals.get("other_deductions"),
        "rebates": totals.get("rebates"),
        "reported_net": totals.get("net"),
        "rounding_adjustment": totals.get("rounding_adjustment"),
        "statement_net_difference": totals.get("statement_net_difference"),
    })


_AMOUNT_KEYS = (
    "gross_sales",
    "refund_full",
    "refund_partial",
    "commission",
    "commission_vat",
    "settlement_fee",
    "settlement_fee_vat",
    "wallet_purchases",
    "cancellation_amount",
    "cancellation_fees",
    "cancellation_fees_vat",
    "other_deductions",
    "rebates",
    "reported_net",
    "rounding_adjustment",
    "statement_net_difference",
)


def normalize_amounts(values: dict[str, Any] | None) -> dict[str, float]:
    source = values or {}
    normalized = {key: _money(source.get(key)) for key in _AMOUNT_KEYS}
    nonnegative = set(_AMOUNT_KEYS) - {
        "reported_net",
        "rounding_adjustment",
        "statement_net_difference",
    }
    for key in nonnegative:
        if normalized[key] < 0:
            raise ValueError(f"{key} لا يقبل قيمة سالبة")
    return normalized


def calculate_settlement_totals(amounts: dict[str, Any]) -> dict[str, float]:
    a = normalize_amounts(amounts)
    refunds = _money(a["refund_full"] + a["refund_partial"])
    deductions = _money(
        refunds
        + a["commission"]
        + a["commission_vat"]
        + a["settlement_fee"]
        + a["settlement_fee_vat"]
        + a["wallet_purchases"]
        + a["other_deductions"]
    )
    calculated_net = _money(a["gross_sales"] - deductions + a["rebates"])
    difference = _money(a["reported_net"] - calculated_net)
    receivable_close = _money(
        a["reported_net"]
        + a["commission"]
        + a["commission_vat"]
        + a["settlement_fee"]
        + a["settlement_fee_vat"]
        + a["wallet_purchases"]
        + a["other_deductions"]
        - a["rebates"]
    )
    return {
        **a,
        "refunds_total": refunds,
        "deductions_total": deductions,
        "calculated_net": calculated_net,
        "equation_difference": difference,
        "provider_receivable_close": receivable_close,
    }


def build_review_reasons(
    *,
    file_doc: dict[str, Any],
    amounts: dict[str, Any],
    bank_account_id: str | None,
    source_review_count: int = 0,
) -> list[dict[str, str]]:
    calc = calculate_settlement_totals(amounts)
    reasons: list[dict[str, str]] = []
    if not str(bank_account_id or "").strip():
        reasons.append({"code": "missing_bank", "message": "لم يُحدد بنك التسوية"})
    if not statement_reference_from_file(file_doc):
        reasons.append({
            "code": "missing_statement_reference",
            "message": "مرجع كشف/فاتورة المزود مفقود",
        })
    unmatched = int(file_doc.get("unmatched") or 0)
    if unmatched:
        reasons.append({
            "code": "unmatched_rows",
            "message": f"{unmatched} صف غير مطابق لطلب معروف",
        })
    if abs(calc["equation_difference"]) > 0.01:
        reasons.append({
            "code": "statement_equation_difference",
            "message": (
                "فرق معادلة التسوية "
                f"{calc['equation_difference']:.2f} SAR"
            ),
        })
    if abs(calc.get("statement_net_difference") or 0) > 0.01:
        reasons.append({
            "code": "statement_rows_difference",
            "message": (
                "صافي رأس كشف المزود لا يطابق مجموع صفوفه "
                f"({calc['statement_net_difference']:.2f} SAR)"
            ),
        })
    if source_review_count:
        reasons.append({
            "code": "source_requires_review",
            "message": f"{source_review_count} بند في الملف يحتاج مراجعة صريحة",
        })
    if calc["reported_net"] < 0:
        reasons.append({
            "code": "negative_bank_net",
            "message": "صافي الكشف سالب؛ لا يمكن اعتباره تحويلًا واردًا للبنك",
        })
    if calc["provider_receivable_close"] <= 0:
        reasons.append({
            "code": "zero_receivable_close",
            "message": "لا يوجد مبلغ موجب لإقفال ذمة المزود",
        })
    unique: dict[str, dict[str, str]] = {}
    for reason in reasons:
        unique.setdefault(reason["code"], reason)
    return list(unique.values())


def has_blocking_reasons(reasons: list[dict[str, Any]] | None) -> bool:
    return any(
        str(item.get("code") or "") in BLOCKING_REASON_CODES
        for item in (reasons or [])
    )


def settlement_idempotency_key(
    *, user_id: str, provider: str, statement_reference: str, source_hash: str = ""
) -> str:
    reference = str(statement_reference or "").strip().lower()
    file_hash = str(source_hash or "").strip().lower()
    identity = f"reference:{reference}" if reference else f"hash:{file_hash or 'missing'}"
    raw = "|".join((
        OPERATION_ID,
        str(user_id or "").strip(),
        canonical_provider(provider),
        identity,
    ))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def build_journal_preview(
    *,
    provider: str,
    bank_account_id: str,
    bank_account_name: str,
    amounts: dict[str, Any],
) -> dict[str, Any]:
    provider = canonical_provider(provider)
    calc = calculate_settlement_totals(amounts)
    if not str(bank_account_id or "").strip():
        raise ValueError("بنك التسوية مطلوب")
    if calc["reported_net"] < 0:
        raise ValueError("صافي التحويل البنكي لا يقبل قيمة سالبة")
    if calc["provider_receivable_close"] <= 0:
        raise ValueError("مبلغ إقفال ذمة المزود يجب أن يكون موجبًا")

    entries: list[dict[str, Any]] = []
    if calc["reported_net"] > 0:
        entries.append({
            "role": "bank_net",
            "label": f"المبلغ الواصل إلى {bank_account_name}",
            "entity_type": "bank",
            "entity_id": bank_account_id,
            "sub_account": "main",
            "side": "debit",
            "amount": calc["reported_net"],
            "entry_type": "settlement",
        })

    expense_legs = (
        ("commission", "provider_commission", "عمولة المزود"),
        ("commission_vat", "provider_commission_vat", "ضريبة عمولة المزود"),
        ("settlement_fee", "provider_settlement_fee", "رسم التسوية"),
        ("settlement_fee_vat", "provider_settlement_fee_vat", "ضريبة رسم التسوية"),
        ("wallet_purchases", "salla_wallet_purchases", "مشتريات محفظة سلة"),
        ("other_deductions", "provider_other_deductions", "خصومات أخرى موثقة"),
    )
    for key, entity_id, label in expense_legs:
        amount = calc[key]
        if amount <= 0:
            continue
        entries.append({
            "role": key,
            "label": label,
            "entity_type": "expense",
            "entity_id": entity_id,
            "sub_account": provider,
            "side": "debit",
            "amount": amount,
            "entry_type": "settlement",
        })

    entries.append({
        "role": "provider_receivable",
        "label": f"إقفال ذمة {provider_label(provider)}",
        "entity_type": "payment_gateway",
        "entity_id": provider,
        "sub_account": "receivable",
        "side": "credit",
        "amount": calc["provider_receivable_close"],
        "entry_type": "settlement",
    })
    if calc["rebates"] > 0:
        entries.append({
            "role": "rebates",
            "label": "رد/خصم رسوم لصالح المتجر",
            "entity_type": "expense",
            "entity_id": "provider_fee_rebates",
            "sub_account": provider,
            "side": "credit",
            "amount": calc["rebates"],
            "entry_type": "settlement",
        })

    debit_total = _money(sum(row["amount"] for row in entries if row["side"] == "debit"))
    credit_total = _money(sum(row["amount"] for row in entries if row["side"] == "credit"))
    return {
        "entries": entries,
        "debit_total": debit_total,
        "credit_total": credit_total,
        "balanced": abs(debit_total - credit_total) <= 0.01,
        "amounts": calc,
    }


async def post_reviewed_settlement(
    db,
    *,
    owner_id: str,
    actor: dict[str, Any],
    draft: dict[str, Any],
) -> dict[str, Any]:
    if draft.get("status") != "reviewed":
        raise HTTPException(409, "يجب مراجعة المسودة قبل الترحيل")
    if has_blocking_reasons(draft.get("review_reasons")):
        raise HTTPException(409, "لا يمكن ترحيل مسودة تحتوي أسباب مراجعة مفتوحة")

    provider = canonical_provider(draft.get("provider"))
    bank_id = str(draft.get("bank_account_id") or "").strip()
    bank = await db.accounts.find_one(
        {
            "user_id": owner_id,
            "id": bank_id,
            "account_type": {"$in": ["bank", "cash"]},
        },
        {"_id": 0, "id": 1, "name": 1, "account_type": 1},
    )
    if not bank:
        raise HTTPException(400, "الحساب البنكي غير موجود أو لا يتبع المتجر")

    idempotency_key = str(draft.get("idempotency_key") or "").strip()
    existing = await db.general_ledger.find_one(
        {
            "user_id": owner_id,
            "metadata.operation_id": OPERATION_ID,
            "metadata.idempotency_key": idempotency_key,
            "status": "posted",
        },
        {"_id": 0, "txn_group_id": 1},
    )
    if existing:
        raise HTTPException(
            409,
            f"التسوية مرحّلة مسبقًا ضمن القيد {existing.get('txn_group_id')}",
        )

    preview = build_journal_preview(
        provider=provider,
        bank_account_id=bank["id"],
        bank_account_name=bank.get("name") or "",
        amounts=draft.get("amounts") or {},
    )
    if not preview["balanced"]:
        raise HTTPException(400, "معاينة القيد غير متوازنة")

    balance = await compute_balance(
        db,
        user_id=owner_id,
        entity_type="payment_gateway",
        entity_id=provider,
        sub_account="receivable",
    )
    available = _money(max(float(balance.get("net_balance") or 0), 0))
    required = preview["amounts"]["provider_receivable_close"]
    if required > available + 0.01:
        raise HTTPException(
            409,
            (
                f"ذمة {provider_label(provider)} غير كافية: "
                f"المتاح {available:.2f} SAR والمطلوب {required:.2f} SAR"
            ),
        )

    statement_reference = str(draft.get("statement_reference") or "").strip()
    bank_snapshot = {
        "id": bank["id"],
        "name": bank.get("name") or "",
        "account_type": bank.get("account_type"),
    }
    metadata = {
        "operation_id": OPERATION_ID,
        "source": "accounting_settlement_p01",
        "settlement_draft_id": draft.get("id"),
        "idempotency_key": idempotency_key,
        "provider": provider,
        "provider_label": provider_label(provider),
        "statement_reference": statement_reference,
        "period_from": draft.get("period_from"),
        "period_to": draft.get("period_to"),
        "statement_date": draft.get("statement_date"),
        "source_file_id": draft.get("source_file_id"),
        "source_file_hash": draft.get("source_file_hash"),
        "bank_snapshot": bank_snapshot,
        "amounts": preview["amounts"],
        "refunds_are_statement_evidence_only": True,
    }
    entries = [
        {
            "entity_type": row["entity_type"],
            "entity_id": row["entity_id"],
            "sub_account": row.get("sub_account"),
            "side": row["side"],
            "amount": row["amount"],
            "entry_type": row["entry_type"],
            "metadata": {"role": row["role"], "label": row["label"]},
        }
        for row in preview["entries"]
    ]
    result = await post_txn_group(
        db,
        user_id=owner_id,
        actor_id=str(actor.get("id") or ""),
        actor_name=actor.get("name") or actor.get("email") or "",
        entries=entries,
        txn_type="provider_settlement_v2",
        reason_code="accounting_settle",
        notes=(
            f"تسوية {provider_label(provider)} — {statement_reference}"
        )[:500],
        metadata=metadata,
    )
    await write_audit(
        db,
        user_id=owner_id,
        actor_id=str(actor.get("id") or ""),
        actor_name=actor.get("name") or actor.get("email") or "",
        entity_type="payment_gateway",
        entity_id=provider,
        action="post_accounting_settlement",
        reason_code="accounting_settle",
        notes=statement_reference,
        after_state={
            "draft_id": draft.get("id"),
            "txn_group_id": result["txn_group_id"],
            "bank_snapshot": bank_snapshot,
            "amounts": preview["amounts"],
        },
        ledger_entry_id=result["entries"][0]["id"] if result.get("entries") else None,
    )
    return {
        **result,
        "bank_snapshot": bank_snapshot,
        "preview": preview,
    }


__all__ = [
    "PROVIDERS",
    "PROVIDER_LABELS",
    "BLOCKING_REASON_CODES",
    "amounts_from_settlement_file",
    "build_journal_preview",
    "build_review_reasons",
    "calculate_settlement_totals",
    "canonical_provider",
    "has_blocking_reasons",
    "period_from_file",
    "post_reviewed_settlement",
    "provider_label",
    "settlement_idempotency_key",
    "statement_reference_from_file",
]
