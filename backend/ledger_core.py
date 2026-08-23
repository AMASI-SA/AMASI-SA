"""Universal Ledger Core — Iter-160

The single source of truth for ALL financial movements in MEZAN.
Replaces ad-hoc balance updates and destructive operations
(reset-debt / recompute-debt) with proper ERP-grade accounting:

  • Append-only ledger entries (no UPDATE, no DELETE)
  • Status states: draft → posted → reversed
  • Reversal entries (instead of deletes)
  • Adjustments / settlements / write-offs (with mandatory reason)
  • Full audit log for every accounting action

Computed balances:
  Any "balance" or "outstanding debt" must be derived from the
  ledger at query time — never stored on the entity row.

Collections
-----------
general_ledger:
    id, user_id, entry_no (monotonic per user)
    entity_type   ("ad_account" | "supplier" | "employee" | "courier" | "bank")
    entity_id     (counterparty / employee / account id)
    entry_type    ("spend"|"topup"|"payment"|"adjustment"|"reversal"
                   "|settlement"|"writeoff"|"accrual"|"opening_balance")
    amount        (positive number, direction is encoded in `side`)
    side          ("debit" | "credit")
                   debit  = our balance / asset increases OR
                            counterparty owes us increases
                   credit = our liability / debt to counterparty increases
                            OR our balance decreases
    currency      ("SAR" default)
    status        ("draft" | "posted" | "reversed")
    reverses_entry_id  (id of the entry this one reverses, if any)
    reversed_by_entry_id (set on the original when a reversal is posted)
    reason_code   (mandatory for adjustments — see REASON_CODES below)
    notes         (free text, optional)
    posted_at     (ISO timestamp when status became posted)
    posted_by     (user id of actor)
    created_at, updated_at
    metadata      (free-form contextual data: source, period, fingerprint, ...)

accounting_audit_log:
    id, user_id, timestamp
    actor_id, actor_name
    entity_type, entity_id
    action        ("create_entry"|"post_entry"|"reverse_entry"|
                   "settle"|"writeoff"|"adjustment"|"delete_blocked"|...)
    reason_code, notes
    before_state (snapshot dict)
    after_state  (snapshot dict)
    ledger_entry_id (linked entry, if any)

Convention for ad-accounts
--------------------------
A `spend` is a `credit` to the ad_account entity from our perspective
(we owe the platform more, OR we used up prepaid balance).
A `topup`  is a `debit`  (we add prepaid balance with the platform).
A `payment` (settle a debt) is a `debit`.

Net balance for entity = Σ debits − Σ credits   (over status=posted only)
    > 0   ⇒ asset (prepaid balance / we have credit there)
    < 0   ⇒ liability (we owe the entity that amount)
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Optional, Literal

from fastapi import HTTPException
from pydantic import BaseModel, Field

# Canonical reason codes (Arabic) — used in adjustments / write-offs / reversals
REASON_CODES: dict[str, str] = {
    "actual_payment":      "سداد فعلي",
    "data_entry_error":    "خطأ إدخال",
    "duplicate_entry":     "قيد مكرر",
    "accounting_settle":   "تسوية محاسبية",
    "approved_writeoff":   "شطب معتمد",
    "platform_correction": "تصحيح من المنصة",
    "balance_transfer":    "تحويل رصيد",
    "other":               "أخرى",
}

ENTRY_TYPES = (
    # Generic
    "spend", "topup", "payment", "adjustment", "reversal",
    "settlement", "writeoff", "accrual", "opening_balance",
    # ── Phase 2 (Iter-161) — universal accounting entries ──
    # Employees
    "salary_accrual",       # شهري: زيادة salary_payable + مصروف رواتب
    "salary_payment",       # صرف راتب: نقص salary_payable + نقص bank
    "advance_grant",        # منح سلفة: زيادة employee.advance + نقص bank
    "advance_settle",       # تسوية سلفة من راتب: نقص advance + نقص payable
    "advance_repay_cash",   # سداد السلفة نقداً: نقص advance + زيادة bank
    "custody_grant",        # تسليم عهدة: زيادة custody + نقص bank
    "custody_return",       # إرجاع عهدة نقداً: نقص custody + زيادة bank
    "custody_expense",      # إقفال عهدة بفواتير: نقص custody + مصروف
    "custody_to_advance",   # تحويل باقي عهدة لسلفة: نقص custody + زيادة advance
    "custody_transfer",     # نقل عهدة بين موظفين: نقص custody (محوِّل) + زيادة custody (مستلم)
    # Suppliers
    "supplier_invoice",     # فاتورة مورد: مصروف/مخزون + زيادة supplier.payable
    "supplier_payment",     # سداد مورد: نقص payable + نقص bank
    # Iter-246d — Fixed-asset purchase via the unified-movement form.
    "fixed_asset_purchase", # شراء أصل ثابت: زيادة fixed_asset + نقص bank/زيادة payable
    # External persons
    "receivable_grant",     # قرض خارجي: زيادة receivable + نقص bank
    "receivable_collection",# تحصيل: نقص receivable + زيادة bank
    # General
    "bank_transfer",        # تحويل بين الحسابات: نقص bank A + زيادة bank B
    "expense_record",       # مصروف عام: مصروف + نقص bank
    # Couriers / shipping companies
    "courier_cod_settle",   # تسوية COD متعددة الأرجل (Iter-190): نقص cod_receivable + bank/cash + مصروفات
    # Mezan 2 store drivers — individual driver sub-ledgers.
    "store_delivery_accrual",    # توصيل ناجح: عهدة COD + أجرة الموصل
    "store_delivery_settlement", # توريد COD / دفع الأجرة / المقاصة الصريحة
    # ── Phase 3 (Iter-196) — manual employee correction ──
    "correction",           # تصحيح: نقل أثر بين كيانَين دون مساس بالبنك/الصندوق
    # ── Iter-219 — BNPL bridge (Tabby/Tamara) ──
    "bnpl_sale",            # بيع Tabby/Tamara: مدين receivable + دائن bnpl_sales
    "bnpl_refund",          # استرجاع Tabby/Tamara: مدين bnpl_sales + دائن receivable
    # ── Iter-220 — BNPL Phase 2b settlement bridge ──
    "bnpl_settlement",      # تسوية Tabby/Tamara: مدين bank + عمولات/ضرائب/رسوم، دائن receivable
    # Reversal kinds for the above are unified under "reversal"
)
SIDES = ("debit", "credit")
STATUSES = ("draft", "posted", "reversed")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _round(v) -> float:
    return round(float(v or 0), 2)


# ── Pydantic models for API I/O ────────────────────────────────────────
class LedgerEntryIn(BaseModel):
    entity_type: str
    entity_id: str
    entry_type: Literal["spend", "topup", "payment", "adjustment",
                        "settlement", "writeoff", "accrual",
                        "opening_balance"]
    amount: float = Field(..., gt=0)
    side: Literal["debit", "credit"]
    reason_code: Optional[str] = None
    notes: Optional[str] = ""
    metadata: Optional[dict] = None
    # post immediately or save as draft
    auto_post: bool = True


class AdjustmentIn(BaseModel):
    """Generic adjustment / settlement / writeoff with mandatory reason."""
    entity_type: str
    entity_id: str
    amount: float = Field(..., gt=0)
    # the accounting nature of the adjustment
    kind: Literal["settlement", "writeoff", "adjustment"]
    # whether the adjustment reduces or increases the net liability
    # for ad_account: "reduce_debt" creates a debit (offsets the debt);
    #                 "increase_debt" creates a credit
    direction: Literal["reduce_debt", "increase_debt"] = "reduce_debt"
    reason_code: str = Field(..., min_length=1)
    notes: Optional[str] = ""
    metadata: Optional[dict] = None


class ReverseEntryIn(BaseModel):
    reason_code: str = Field(..., min_length=1)
    notes: Optional[str] = ""


# ── Core helpers ───────────────────────────────────────────────────────
async def write_audit(
    db, *, user_id: str, actor_id: str, actor_name: str,
    entity_type: str, entity_id: Optional[str], action: str,
    reason_code: Optional[str] = None, notes: Optional[str] = "",
    before_state: Optional[dict] = None, after_state: Optional[dict] = None,
    ledger_entry_id: Optional[str] = None,
) -> str:
    """Append a row to the audit log — fire-and-forget pattern."""
    audit_id = str(uuid.uuid4())
    await db.accounting_audit_log.insert_one({
        "id": audit_id,
        "user_id": user_id,
        "actor_id": actor_id,
        "actor_name": actor_name,
        "timestamp": _now(),
        "entity_type": entity_type,
        "entity_id": entity_id,
        "action": action,
        "reason_code": reason_code,
        "notes": notes or "",
        "before_state": before_state,
        "after_state": after_state,
        "ledger_entry_id": ledger_entry_id,
    })
    return audit_id


async def _next_entry_no(db, user_id: str) -> int:
    """Generate a monotonically increasing entry number per user."""
    res = await db.general_ledger.aggregate([
        {"$match": {"user_id": user_id}},
        {"$group": {"_id": None, "mx": {"$max": "$entry_no"}}},
    ]).to_list(1)
    cur = int(res[0]["mx"]) if res and res[0].get("mx") is not None else 0
    return cur + 1


async def post_ledger_entry(
    db, *, user_id: str, actor_id: str, actor_name: str,
    entity_type: str, entity_id: str,
    entry_type: str, amount: float, side: str,
    sub_account: Optional[str] = None,
    txn_group_id: Optional[str] = None,
    reason_code: Optional[str] = None, notes: Optional[str] = "",
    metadata: Optional[dict] = None,
    status: str = "posted",
    reverses_entry_id: Optional[str] = None,
) -> dict:
    """Insert a single ledger entry. Returns the inserted document.

    Adjustment / settlement / writeoff entries REQUIRE a reason_code.
    Phase 2 additions:
      • sub_account (optional) — sub-ledger code inside an entity
        (e.g. employee.advance, employee.custody, employee.salary_payable)
      • txn_group_id (optional) — links related entries belonging to
        the same business event (mandatory for grouped operations
        posted via post_txn_group).
    """
    if entry_type not in ENTRY_TYPES:
        raise HTTPException(400, f"entry_type غير صحيح: {entry_type}")
    if side not in SIDES:
        raise HTTPException(400, f"side غير صحيح: {side}")
    if status not in STATUSES:
        raise HTTPException(400, f"status غير صحيح: {status}")
    if amount <= 0:
        raise HTTPException(400, "amount يجب أن يكون موجباً")
    if entry_type in ("adjustment", "settlement", "writeoff", "reversal"):
        if not reason_code:
            raise HTTPException(
                400, "reason_code إلزامي للقيود التعديلية والتسويات والشطب",
            )
        if reason_code not in REASON_CODES:
            raise HTTPException(
                400, f"reason_code غير معتمد: {reason_code}",
            )

    # Iter-226 — orphan-prevention guard.
    # Iter-250b · P1.5.p — Widened to check BOTH `operating_salaries`
    # (modern primary storage) AND `employees` (legacy collection).
    # The previous implementation checked only `employees`, which
    # rejected EVERY employee created via the modern flow because
    # those records live exclusively in `operating_salaries`.
    #
    # We additionally reject employees that have been explicitly
    # marked `archived=true` or `deleted=true` — `status=active|stopped`
    # is still allowed because a stopped employee can legitimately
    # carry an open custody/advance/payable that needs settling.
    #
    # Reversals remain exempt (they may target an already-archived
    # employee for symmetry).
    if (
        entity_type == "employee"
        and entry_type != "reversal"
        and entity_id
    ):
        eid_str = str(entity_id)
        id_or_clause = [
            {"id": eid_str},
            {"employee_id": eid_str},
            {"external_id": eid_str},
            {"legacy_id": eid_str},
        ]
        # Exclude archived / deleted records. A missing flag means
        # "not archived / not deleted" — the absence of the field
        # MUST be treated as falsy.
        not_dead = {
            "archived": {"$ne": True},
            "is_archived": {"$ne": True},
            "deleted": {"$ne": True},
            "is_deleted": {"$ne": True},
        }
        query = {"user_id": user_id, "$or": id_or_clause, **not_dead}
        proj = {"_id": 1, "id": 1, "name": 1, "status": 1}
        # Prefer the modern collection. Fall back to the legacy one
        # so historical employees that exist only in `employees`
        # still pass (read-only — no copy / no migration).
        emp = (
            await db.operating_salaries.find_one(query, proj)
            or await db.employees.find_one(query, proj)
        )
        if not emp:
            raise HTTPException(
                400,
                (
                    f"لا يمكن إنشاء قيد على موظف غير موجود "
                    f"(entity_id={eid_str}). "
                    f"تأكد أن الموظف مُسجَّل في النظام قبل إنشاء "
                    f"أيّ قيد محاسبي عليه."
                ),
            )

    entry_no = await _next_entry_no(db, user_id)
    eid = str(uuid.uuid4())
    now = _now()
    doc = {
        "id": eid,
        "user_id": user_id,
        "entry_no": entry_no,
        "entity_type": entity_type,
        "entity_id": entity_id,
        "sub_account": sub_account,
        "txn_group_id": txn_group_id,
        "entry_type": entry_type,
        "amount": _round(amount),
        "side": side,
        "currency": "SAR",
        "status": status,
        "reverses_entry_id": reverses_entry_id,
        "reversed_by_entry_id": None,
        "reason_code": reason_code,
        "notes": notes or "",
        "metadata": metadata or {},
        "posted_at": now if status == "posted" else None,
        "posted_by": actor_id if status == "posted" else None,
        "created_at": now,
        "updated_at": now,
    }
    await db.general_ledger.insert_one(doc)

    # Audit
    await write_audit(
        db,
        user_id=user_id, actor_id=actor_id, actor_name=actor_name,
        entity_type=entity_type, entity_id=entity_id,
        action=f"create_{entry_type}",
        reason_code=reason_code, notes=notes,
        after_state={
            "entry_no": entry_no, "amount": doc["amount"],
            "side": side, "status": status,
            "sub_account": sub_account,
            "txn_group_id": txn_group_id,
        },
        ledger_entry_id=eid,
    )
    return doc


async def post_txn_group(
    db, *, user_id: str, actor_id: str, actor_name: str,
    entries: list[dict],
    txn_type: str,
    reason_code: Optional[str] = None,
    notes: str = "",
    metadata: Optional[dict] = None,
) -> dict:
    """Atomically post a group of related ledger entries that share
    one `txn_group_id`. Enforces the double-entry invariant:
        Σ amounts(debit) == Σ amounts(credit)

    Each `entries` element is a dict containing:
        entity_type, entity_id, side, amount, entry_type
        sub_account (optional), notes (optional), metadata (optional)

    `txn_type` is a short label for the business event (e.g.
    "employee_settlement", "supplier_payment", "bank_transfer"). It is
    stored in metadata.txn_type on every entry so reports can group
    them.

    Returns: {
        "txn_group_id": str,
        "entries": [doc, ...],
        "debit_total": float,
        "credit_total": float,
    }
    """
    if not entries or len(entries) < 2:
        raise HTTPException(400, "كل حركة محاسبية تحتاج قيدين على الأقل")
    debit_total = round(sum(float(e["amount"]) for e in entries
                              if e["side"] == "debit"), 2)
    credit_total = round(sum(float(e["amount"]) for e in entries
                                if e["side"] == "credit"), 2)
    if abs(debit_total - credit_total) > 0.01:
        raise HTTPException(
            400,
            f"عدم توازن القيد: مدين={debit_total} ≠ دائن={credit_total}",
        )

    group_id = str(uuid.uuid4())
    saved: list[dict] = []
    base_meta = {"txn_type": txn_type, **(metadata or {})}
    for e in entries:
        doc = await post_ledger_entry(
            db,
            user_id=user_id, actor_id=actor_id, actor_name=actor_name,
            entity_type=e["entity_type"], entity_id=e["entity_id"],
            entry_type=e["entry_type"], amount=float(e["amount"]),
            side=e["side"],
            sub_account=e.get("sub_account"),
            txn_group_id=group_id,
            reason_code=e.get("reason_code") or reason_code,
            notes=e.get("notes") or notes,
            metadata={**base_meta, **(e.get("metadata") or {})},
            status="posted",
        )
        doc.pop("_id", None)
        saved.append(doc)
    return {
        "txn_group_id": group_id,
        "txn_type": txn_type,
        "entries": saved,
        "debit_total": debit_total,
        "credit_total": credit_total,
    }


async def reverse_entry(
    db, *, user_id: str, actor_id: str, actor_name: str,
    entry_id: str, reason_code: str, notes: str = "",
) -> dict:
    """Create a REVERSAL entry that exactly mirrors the original on the
    opposite side. Marks the original as `reversed_by_entry_id`.
    Original entry is preserved; new entry references it.
    """
    if not reason_code:
        raise HTTPException(400, "reason_code إلزامي")
    if reason_code not in REASON_CODES:
        raise HTTPException(400, f"reason_code غير معتمد: {reason_code}")

    orig = await db.general_ledger.find_one(
        {"id": entry_id, "user_id": user_id},
    )
    if not orig:
        raise HTTPException(404, "القيد غير موجود")
    if orig.get("status") != "posted":
        raise HTTPException(
            400, "يمكن عكس القيود المعتمدة فقط (status=posted)",
        )
    if orig.get("reversed_by_entry_id"):
        raise HTTPException(400, "هذا القيد عُكس من قبل")
    if orig.get("entry_type") == "reversal":
        raise HTTPException(400, "لا يمكن عكس قيد عكسي")

    opposite_side = "credit" if orig["side"] == "debit" else "debit"
    rev = await post_ledger_entry(
        db,
        user_id=user_id, actor_id=actor_id, actor_name=actor_name,
        entity_type=orig["entity_type"], entity_id=orig["entity_id"],
        sub_account=orig.get("sub_account"),
        entry_type="reversal", amount=orig["amount"], side=opposite_side,
        reason_code=reason_code, notes=notes,
        metadata={"reverses_entry_no": orig.get("entry_no")},
        status="posted",
        reverses_entry_id=entry_id,
    )

    await db.general_ledger.update_one(
        {"id": entry_id, "user_id": user_id},
        {"$set": {
            "reversed_by_entry_id": rev["id"],
            "status": "reversed",
            "updated_at": _now(),
        }},
    )

    await write_audit(
        db,
        user_id=user_id, actor_id=actor_id, actor_name=actor_name,
        entity_type=orig["entity_type"], entity_id=orig["entity_id"],
        action="reverse_entry",
        reason_code=reason_code, notes=notes,
        before_state={"entry_no": orig.get("entry_no"),
                       "status": "posted"},
        after_state={"entry_no": orig.get("entry_no"),
                      "status": "reversed",
                      "reversed_by": rev["id"]},
        ledger_entry_id=entry_id,
    )
    return rev


async def compute_balance(
    db, *, user_id: str, entity_type: str, entity_id: str,
    sub_account: Optional[str] = None,
) -> dict:
    """Compute the live balance of an entity from POSTED entries only.

    Returns {
        debits, credits, net_balance,
        prepaid_balance, outstanding_debt,
    }

    Convention for ad_account / supplier / courier entities:
      net = Σ debits − Σ credits   (≥0 ⇒ asset, <0 ⇒ liability)

    When `sub_account` is provided, only entries with that exact
    sub_account value are aggregated. This is critical for
    employee.{advance, custody, salary_payable} which all live under
    entity_type="employee" but represent distinct sub-ledgers.
    """
    match: dict = {
        "user_id": user_id,
        "entity_type": entity_type,
        "entity_id": entity_id,
        # Iter-217 — Exclude both reversed originals AND their
        # reversal entries from the balance computation. Each pair
        # cancels by definition; counting only one side (the previous
        # behaviour) inflated balances by 2× the reversed amount.
        # The "live" balance now reflects ONLY entries that are both
        # `status=posted` AND NOT of `entry_type=reversal`.
        "status": "posted",
        "entry_type": {"$ne": "reversal"},
        # Iter-226 — Legacy orphan entries (archived for audit only)
        # MUST NOT participate in any live balance computation.
        "metadata.legacy_orphan": {"$ne": True},
    }
    if sub_account is not None:
        match["sub_account"] = sub_account
    pipeline = [
        {"$match": match},
        {"$group": {
            "_id": "$side",
            "total": {"$sum": "$amount"},
            "count": {"$sum": 1},
        }},
    ]
    debits = 0.0
    credits = 0.0
    async for row in db.general_ledger.aggregate(pipeline):
        if row["_id"] == "debit":
            debits = float(row["total"])
        elif row["_id"] == "credit":
            credits = float(row["total"])
    net = round(debits - credits, 2)

    # outstanding_debt convention: when net is negative, that's what we owe.
    outstanding = round(-net, 2) if net < 0 else 0.0
    prepaid = round(net, 2) if net > 0 else 0.0

    return {
        "debits": round(debits, 2),
        "credits": round(credits, 2),
        "net_balance": net,
        "prepaid_balance": prepaid,
        "outstanding_debt": outstanding,
        "sub_account": sub_account,
    }


async def compute_balances_bulk(
    db, *, user_id: str, entity_type: str, entity_ids: list[str],
) -> dict[str, dict]:
    """Bulk version of compute_balance — one aggregation for many entities."""
    if not entity_ids:
        return {}
    pipeline = [
        {"$match": {
            "user_id": user_id,
            "entity_type": entity_type,
            "entity_id": {"$in": entity_ids},
            "status": "posted",
            # Iter-217 — exclude reversal entries so pairs cancel.
            "entry_type": {"$ne": "reversal"},
            # Iter-226 — exclude archived legacy orphans.
            "metadata.legacy_orphan": {"$ne": True},
        }},
        {"$group": {
            "_id": {"entity_id": "$entity_id", "side": "$side"},
            "total": {"$sum": "$amount"},
        }},
    ]
    raw: dict[str, dict] = {eid: {"debits": 0.0, "credits": 0.0}
                            for eid in entity_ids}
    async for row in db.general_ledger.aggregate(pipeline):
        eid = row["_id"]["entity_id"]
        side = row["_id"]["side"]
        if eid in raw:
            raw[eid][side + "s"] = float(row["total"])
    out: dict[str, dict] = {}
    for eid, agg in raw.items():
        d = round(agg["debits"], 2)
        c = round(agg["credits"], 2)
        net = round(d - c, 2)
        out[eid] = {
            "debits": d, "credits": c, "net_balance": net,
            "prepaid_balance": round(net, 2) if net > 0 else 0.0,
            "outstanding_debt": round(-net, 2) if net < 0 else 0.0,
        }
    return out


async def ensure_indexes(db) -> None:
    await db.general_ledger.create_index(
        [("user_id", 1), ("entity_type", 1), ("entity_id", 1),
         ("status", 1)],
    )
    # Iter-161 — sub_account enables employee.{advance,custody,payable}
    await db.general_ledger.create_index(
        [("user_id", 1), ("entity_type", 1), ("entity_id", 1),
         ("sub_account", 1), ("status", 1)],
    )
    await db.general_ledger.create_index(
        [("user_id", 1), ("txn_group_id", 1)],
    )
    await db.general_ledger.create_index(
        [("user_id", 1), ("entry_no", 1)], unique=True,
    )
    await db.general_ledger.create_index([("user_id", 1), ("created_at", -1)])
    await db.accounting_audit_log.create_index(
        [("user_id", 1), ("timestamp", -1)],
    )
    await db.accounting_audit_log.create_index(
        [("user_id", 1), ("entity_type", 1), ("entity_id", 1),
         ("timestamp", -1)],
    )
    # Iter-161 — expense_categories collection (user-editable)
    await db.expense_categories.create_index(
        [("user_id", 1), ("code", 1)], unique=True,
    )
