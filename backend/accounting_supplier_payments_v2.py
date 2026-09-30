"""MZ2 supplier settlement, separate payable/advance accounts, no legacy adapter.

Bank access is an injected Track A port. The default is deliberately unavailable.
Receiving history is never debt. The proposed invoice binding below must be
written by an independently reviewed native invoice producer, not this service.
"""
from datetime import date, datetime, time, timezone
from decimal import Decimal
import hashlib
import json
from typing import Literal, Protocol
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from accounting_atomic import SessionDatabase, atomic_owner
from accounting_ledger_v2 import (AccountingLedgerV2Error, post_journal_v2,
    read_reporting_entries_v2, verify_active_opening_v2)
from accounting_module_contract import accounting_owner_id, require_accounting_permission
from accounting_module_readiness import build_accounting_module_status
from accounting_writer_transition import transition_state
from accounting_periods import assert_open_journal_periods
from accounting_write_control import fresh_actor
from supplier_identity_service import require_supplier_v2

SUPPLIERS = "mezan_suppliers_v2"
INVOICES = "mezan_supplier_invoices_v2"
OPERATIONS = "mz2_supplier_settlements_v2"
INVOICE_CONTRACT = "mz2_supplier_invoice_v1"
PAYMENT_CONTRACT = "mz2_supplier_payment_v1"
BANK_GAP = "track_a_canonical_account_contract_required"
RIYADH = ZoneInfo("Asia/Riyadh")
LIMIT = 10000


class CanonicalFinancialAccountPort(Protocol):
    """Track A owns implementation and bank/cash leg identity, including currency.

    list_payment_accounts returns public active owner-scoped SAR account rows.
    require_payment_account runs in the owner transaction and returns exact
    id, entity_type, entity_id, sub_account, currency, status and account_type.
    No request may supply or override this port or its returned leg identity.
    """
    async def list_payment_accounts(self, db, owner): ...
    async def require_payment_account(self, db, owner, account_id): ...


def fail(code, status=409):
    raise HTTPException(status, detail={"code": code})


def minor(value):
    amount = Decimal(str(value))
    if not amount.is_finite() or amount != amount.quantize(Decimal("0.01")):
        fail("supplier_amount_invalid", 422)
    return int(amount * 100)


def money(value):
    return format(Decimal(value) / 100, ".2f")


def key(owner, operation):
    return hashlib.sha256(json.dumps([owner, operation]).encode()).hexdigest()


class PaymentIn(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False, str_strip_whitespace=True)
    operation_id: str = Field(min_length=1, max_length=120)
    amount: Decimal = Field(gt=0, max_digits=14, decimal_places=2)
    payment_date: date
    financial_account_id: str = Field(min_length=1, max_length=200)
    invoice_id: str | None = Field(default=None, min_length=1, max_length=200)
    unallocated_kind: Literal["payable", "advance"] = "advance"
    allow_advance: bool = False
    reference: str = Field(min_length=1, max_length=200)
    notes: str = Field(default="", max_length=2000)
    evidence_file_id: str | None = Field(default=None, min_length=1, max_length=200)


class AllocationIn(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False, str_strip_whitespace=True)
    operation_id: str = Field(min_length=1, max_length=120)
    payment_id: str = Field(min_length=1, max_length=200)
    invoice_id: str = Field(min_length=1, max_length=200)
    amount: Decimal = Field(gt=0, max_digits=14, decimal_places=2)
    payment_date: date
    reference: str = Field(min_length=1, max_length=200)
    notes: str = Field(default="", max_length=2000)
    evidence_file_id: str | None = Field(default=None, min_length=1, max_length=200)


async def actor_scope(db, user, *, write=False):
    actor = await fresh_actor(db, user)
    require_accounting_permission(actor, "accounting.movements.import" if write else "accounting.journals_reports.view")
    return actor, accounting_owner_id(actor)


async def eligible(db, owner, required=()):
    scoped = isinstance(db, SessionDatabase)
    raw, session = (db._db, db._session) if scoped else (db, None)
    state = await transition_state(raw, owner, mongo_session=session)
    if state["state"] != "v2_active":
        fail("supplier_requires_v2_active")
    settings = await db.settings.find_one({"user_id": owner}) or {}
    cutover = settings.get("mezan2_financial_cutover") or {}
    verified = await verify_active_opening_v2(raw, user_id=owner, cutover=cutover, mongo_session=session)
    readiness = build_accounting_module_status(cutover, opening_posted_verified=verified)
    if not readiness["cutover"]["safe_active"]:
        fail("supplier_opening_or_ledger_not_ready")
    cut_at = datetime.fromisoformat(cutover["cutover_at"].replace("Z", "+00:00"))
    rows = await read_reporting_entries_v2(raw, user_id=owner,
        effective_before=datetime.now(timezone.utc).isoformat(), mongo_session=session)
    rows = [r for r in rows if datetime.fromisoformat(r["effective_at"].replace("Z", "+00:00")) >= cut_at]
    active = cutover["opening_active_txn_group_id"]
    covered = {(r["entity_type"], r["entity_id"], r.get("sub_account") or "") for r in rows
               if r["txn_group_id"] == active and r["entry_type"] in {"opening_balance", "opening_replacement"}}
    for zero in cutover.get("opening_balance_zero_accounts") or []:
        if (zero.get("opening_balance_txn_group_id") != active or not zero.get("evidence_ref")
                or datetime.fromisoformat(zero["accounting_at"].replace("Z", "+00:00")) != cut_at):
            fail("supplier_opening_zero_evidence_invalid")
        covered.add((zero["entity_type"], zero["entity_id"], zero.get("sub_account") or ""))
    if set(required) - covered:
        fail("supplier_accounts_require_documented_opening")
    return {"items": rows, "cutover_at": cutover["cutover_at"], "ledger_backend": "v2", "status": "available"}


async def bounded(collection, query):
    rows = await collection.find(query, {"_id": 0}).to_list(LIMIT + 1)
    if len(rows) > LIMIT:
        fail("supplier_scope_too_large")
    return rows


def reversed_groups(entries):
    groups = {r["id"]: r["txn_group_id"] for r in entries}
    return {groups.get((r.get("metadata") or {}).get("reverses_entry_id"))
            for r in entries if r.get("entry_type") == "reversal"}


def invoice_projection(invoice, entries, allocations):
    """Only a verified native journal binding can authorize invoice allocation.

    Cached legacy paid/status fields are ignored. Opening payables remain a
    separately documented cutover fact, never a replay of historical invoices.
    """
    sid, iid = invoice.get("supplier_id"), invoice.get("id")
    total = invoice.get("total_halalas")
    base = {"id": iid, "supplier_id": sid, "invoice_number": invoice.get("invoice_number"),
            "total_halalas": total, "paid_halalas": None, "outstanding_halalas": None,
            "payment_status": "historical_unverified", "financial_eligible": False,
            "exclusion_reason": "native_invoice_provenance_contract_required",
            "lines": [], "approved_at": invoice.get("approved_at"),
            "experiment_mode": bool(invoice.get("experiment_mode"))}
    if (invoice.get("mz2_financial_contract") != INVOICE_CONTRACT
            or invoice.get("experiment_mode") is not False
            or invoice.get("currency") != "SAR" or type(total) is not int or total <= 0):
        return base
    matched = [r for r in entries if r.get("txn_group_id") == invoice.get("mz2_txn_group_id")
               and r.get("entity_type") == "supplier" and r.get("entity_id") == sid
               and r.get("sub_account") == "payable"]
    if len(matched) != 1:
        return base
    credit = matched[0]
    meta = credit.get("metadata") or {}
    if (credit.get("side") != "credit" or credit.get("entry_type") != "supplier_invoice"
            or minor(credit["amount"]) != total or meta.get("supplier_invoice_id") != iid
            or meta.get("supplier_source") != SUPPLIERS or meta.get("invoice_contract") != INVOICE_CONTRACT):
        return base
    # A reversed invoice cannot accept new payments until its allocation is reconciled.
    if credit["txn_group_id"] in reversed_groups(entries):
        return {**base, "exclusion_reason": "invoice_reversed_requires_reconciliation"}
    paid = sum(minor(r["amount"]) for r in entries if r.get("entity_type") == "supplier"
               and r.get("entity_id") == sid and r.get("sub_account") == "payable"
               and r.get("side") == "debit" and r.get("entry_type") in {"supplier_payment", "supplier_advance_allocation"}
               and (r.get("metadata") or {}).get("supplier_invoice_id") == iid
               and (r.get("metadata") or {}).get("payment_contract") == PAYMENT_CONTRACT)
    paid += sum(a["amount_halalas"] for a in allocations if a.get("invoice_id") == iid and a.get("kind") == "payable_allocation")
    if paid < 0 or paid > total:
        fail("supplier_invoice_allocation_requires_reconciliation")
    return {**base, "financial_eligible": True, "exclusion_reason": None, "paid_halalas": paid,
            "outstanding_halalas": total - paid,
            "payment_status": "paid" if paid == total else "partial" if paid else "unpaid"}


def balances(entries, sid):
    nets = {"payable": 0, "advance": 0}
    for row in entries:
        if row.get("entity_type") == "supplier" and row.get("entity_id") == sid and row.get("sub_account") in nets:
            nets[row["sub_account"]] += minor(row["amount"]) * (1 if row["side"] == "debit" else -1)
    if nets["payable"] > 0 or nets["advance"] < 0:
        fail("supplier_balance_requires_reconciliation")
    return -nets["payable"], nets["advance"]


async def snapshot(db, owner, supplier_ids, scope):
    entries = scope["items"]
    invoices = await bounded(db[INVOICES], {"user_id": owner, "supplier_id": {"$in": supplier_ids}})
    operations = await bounded(db[OPERATIONS], {"user_id": owner, "supplier_id": {"$in": supplier_ids}})
    allocations = [r for r in operations if r["kind"] in {"advance_allocation", "payable_allocation"}]
    if any(a.get("txn_group_id") in reversed_groups(entries) for a in allocations if a.get("txn_group_id")):
        fail("supplier_allocation_reversal_requires_reconciliation")
    projected = [invoice_projection(r, entries, allocations) for r in invoices]
    payments = []
    for op in operations:
        if op["kind"] != "payment":
            continue
        linked = [r for r in entries if r.get("txn_group_id") == op["txn_group_id"]]
        if not linked or op["txn_group_id"] in reversed_groups(entries):
            fail("supplier_payment_reversal_requires_reconciliation")
        applied = [a for a in allocations if a["payment_id"] == op["id"]]
        unallocated = op["unallocated_payable_halalas"] - sum(a["amount_halalas"] for a in applied if a["kind"] == "payable_allocation")
        advance = op["advance_halalas"] - sum(a["amount_halalas"] for a in applied if a["kind"] == "advance_allocation")
        if min(unallocated, advance) < 0:
            fail("supplier_payment_allocation_requires_reconciliation")
        payments.append({"id": op["id"], "supplier_id": op["supplier_id"],
                         "unallocated_payable_halalas": unallocated, "advance_available_halalas": advance,
                         "payment_date": op["request"]["payment_date"], "reference": op["request"]["reference"]})
    return projected, payments, operations


async def payment_workspace(db, user, supplier_id=None, *, bank_port=None):
    _, owner = await actor_scope(db, user)
    query = {"user_id": owner}
    if supplier_id:
        query["id"] = supplier_id
    suppliers = await bounded(db[SUPPLIERS], query)
    if supplier_id and not suppliers:
        fail("supplier_v2_not_found", 404)
    ids = [r["id"] for r in suppliers]
    try:
        scope = await eligible(db, owner, [("supplier", sid, sub) for sid in ids for sub in ("payable", "advance")])
    except HTTPException as exc:
        return {"ok": True, "financial_status": "not_ready", "reason": exc.detail,
                "suppliers": [{"id": r["id"], "company_name": r.get("company_name"), "financial": None} for r in suppliers],
                "invoices": [], "timeline": [], "payments": [], "summary": {}, "payment_accounts": [],
                "payment_available": False, "payment_unavailable_reason": "supplier_opening_or_ledger_not_ready"}
    invoices, payments, _ = await snapshot(db, owner, ids, scope)
    summary = {k: 0 for k in ("outstanding_halalas", "advance_halalas", "invoiced_halalas", "paid_halalas", "real_invoice_count", "experiment_invoice_count")}
    public = []
    timeline = []
    for supplier in suppliers:
        sid = supplier["id"]
        payable, advance = balances(scope["items"], sid)
        eligible_invoices = [r for r in invoices if r["supplier_id"] == sid and r["financial_eligible"]]
        rows = [r for r in scope["items"] if r.get("entity_type") == "supplier" and r.get("entity_id") == sid and r.get("sub_account") in {"payable", "advance"}]
        financial = dict(outstanding_halalas=payable, advance_halalas=advance,
            invoiced_halalas=sum(r["total_halalas"] for r in eligible_invoices),
            paid_halalas=sum(minor(r["amount"]) for r in rows if r["side"] == "debit" and r["entry_type"] == "supplier_payment"),
            real_invoice_count=len(eligible_invoices), experiment_invoice_count=0)
        for k in summary:
            summary[k] += financial[k]
        public.append({"id": sid, "company_name": supplier.get("company_name"), "financial": financial})
        timeline += [{"id": r["id"], "supplier_id": sid, "kind": r["entry_type"], "sub_account": r["sub_account"],
                      "side": r["side"], "amount_halalas": minor(r["amount"]), "created_at": r["effective_at"],
                      "notes": (r.get("metadata") or {}).get("notes", "")} for r in rows]
    accounts = await bank_port.list_payment_accounts(db, owner) if bank_port else []
    return {"ok": True, "financial_status": "available", "suppliers": public, "invoices": invoices,
            "timeline": timeline, "payments": payments, "summary": summary,
            "payment_accounts": accounts, "payment_available": bool(bank_port),
            "payment_unavailable_reason": None if bank_port else BANK_GAP,
            "rules": {"supplier_source": SUPPLIERS, "ledger_source": "accounting_v2",
                      "legacy_invoices_create_payable": False, "silent_netting": False,
                      "invoice_producer_gap": "native_invoice_provenance_contract_required"}}


def split_payment(amount, *, remaining, available_payable, invoice, kind, allow_advance):
    if not invoice and kind == "advance":
        return 0, amount
    payable = min(amount, remaining if invoice else available_payable, available_payable)
    advance = amount - payable
    if advance and not allow_advance:
        fail("supplier_overpayment_requires_explicit_advance")
    return payable, advance


async def evidence(db, owner, evidence_id):
    if evidence_id:
        row = await db.accounting_source_files.find_one({"user_id": owner, "file_id": evidence_id})
        if not row or not row.get("content") or hashlib.sha256(bytes(row["content"])).hexdigest() != row.get("sha256"):
            fail("supplier_payment_evidence_unavailable")


def effective_at(payload, scope, relevant):
    if payload.payment_date > datetime.now(RIYADH).date():
        fail("supplier_payment_future_date", 422)
    instant = datetime.combine(payload.payment_date, time.min, RIYADH)
    cut = datetime.fromisoformat(scope["cutover_at"].replace("Z", "+00:00"))
    # Date-only UI uses the end of the selected cutover day only if the actual
    # cutover instant is on that day. Never backdate before any relevant event.
    if instant.date() == cut.astimezone(RIYADH).date():
        instant = max(instant, cut)
    if instant < cut:
        fail("supplier_payment_before_cutover")
    for row in relevant:
        at = datetime.fromisoformat(row["effective_at"].replace("Z", "+00:00"))
        if at.astimezone(RIYADH).date() > payload.payment_date:
            fail("supplier_payment_backdating_requires_reconciliation")
        instant = max(instant, at)
    return instant.astimezone(timezone.utc).isoformat()


async def settle(db, user, supplier_id, payload, *, bank_port=None):
    _, owner = await actor_scope(db, user, write=True)
    is_allocation = isinstance(payload, AllocationIn)
    request = payload.model_dump(mode="json")
    fingerprint = {"supplier_id": supplier_id, "allocation": is_allocation, "request": request}
    operation_key = key(owner, payload.operation_id)

    async def commit(scoped):
        actor, fresh_owner = await actor_scope(scoped, user, write=True)
        if fresh_owner != owner:
            fail("supplier_actor_scope_changed", 403)
        await require_supplier_v2(scoped, owner, supplier_id)
        prior = await scoped[OPERATIONS].find_one({"_id": operation_key, "user_id": owner})
        if prior:
            if prior["fingerprint"] != fingerprint:
                fail("supplier_operation_payload_conflict")
            return {"ok": True, "replayed": True, **prior["result"]}
        required = [("supplier", supplier_id, sub) for sub in ("payable", "advance")]
        scope = await eligible(scoped, owner, required)
        invoices, payments, operations = await snapshot(scoped, owner, [supplier_id], scope)
        payable_balance, advance_balance = balances(scope["items"], supplier_id)
        invoice = None
        if payload.invoice_id:
            invoice = next((r for r in invoices if r["id"] == payload.invoice_id and r["financial_eligible"]), None)
            if not invoice:
                fail("supplier_native_financial_invoice_required")
        amount = minor(payload.amount)
        await evidence(scoped, owner, payload.evidence_file_id)
        supplier_rows = [r for r in scope["items"] if r.get("entity_type") == "supplier" and r.get("entity_id") == supplier_id]
        meta = {"payment_contract": PAYMENT_CONTRACT, "supplier_source": SUPPLIERS, "supplier_id": supplier_id,
                "payment_id": operation_key, "reference": payload.reference, "notes": payload.notes,
                "evidence_file_id": payload.evidence_file_id}
        if invoice:
            meta["supplier_invoice_id"] = invoice["id"]
        entries = []
        record = {"id": operation_key, "user_id": owner, "supplier_id": supplier_id, "request": request, "fingerprint": fingerprint}
        if is_allocation:
            payment = next((r for r in payments if r["id"] == payload.payment_id), None)
            if not payment or amount > invoice["outstanding_halalas"]:
                fail("supplier_allocation_exceeds_available")
            # Each payment may have both parts (overpayment). Consume payable
            # first via explicit allocation only; advance is a separate action.
            if payment["unallocated_payable_halalas"]:
                if amount > payment["unallocated_payable_halalas"]:
                    fail("supplier_allocation_split_required")
                kind = "payable_allocation"
            else:
                if amount > min(payment["advance_available_halalas"], advance_balance, payable_balance):
                    fail("supplier_allocation_exceeds_available")
                kind = "advance_allocation"
                entries = [dict(leg_key="payable", entity_type="supplier", entity_id=supplier_id, sub_account="payable", side="debit", entry_type="supplier_advance_allocation", amount=money(amount)),
                           dict(leg_key="advance", entity_type="supplier", entity_id=supplier_id, sub_account="advance", side="credit", entry_type="supplier_advance_allocation", amount=money(amount))]
            record.update(kind=kind, payment_id=payment["id"], invoice_id=invoice["id"], amount_halalas=amount)
            # Allocation date may not precede original payment or another allocation.
            dates = [r["request"]["payment_date"] for r in operations]
            if dates and payload.payment_date.isoformat() < max(dates):
                fail("supplier_allocation_backdating_requires_reconciliation")
        else:
            if bank_port is None:
                fail(BANK_GAP, 503)
            account = await bank_port.require_payment_account(scoped, owner, payload.financial_account_id)
            if (not account or account.get("id") != payload.financial_account_id or account.get("currency") != "SAR"
                    or account.get("status") != "active" or account.get("account_type") not in {"bank", "cash"}
                    or not all(account.get(k) for k in ("entity_type", "entity_id", "sub_account"))):
                fail("canonical_payment_account_contract_invalid")
            account_key = tuple(account[k] for k in ("entity_type", "entity_id", "sub_account"))
            scope = await eligible(scoped, owner, required + [account_key])
            bank_rows = [r for r in scope["items"] if tuple(r.get(k) for k in ("entity_type", "entity_id", "sub_account")) == account_key]
            funds = sum(minor(r["amount"]) * (1 if r["side"] == "debit" else -1) for r in bank_rows)
            if amount > funds:
                fail("supplier_insufficient_canonical_funds")
            supplier_rows += bank_rows
            paid, advance = split_payment(amount, remaining=invoice["outstanding_halalas"] if invoice else 0,
                available_payable=payable_balance, invoice=bool(invoice), kind=payload.unallocated_kind, allow_advance=payload.allow_advance)
            # Preserve invoice-specific remainder: unallocated settlement may
            # reduce total payable but cannot silently mark an invoice paid.
            for sub, value in (("payable", paid), ("advance", advance)):
                if value:
                    entries.append(dict(leg_key=sub, entity_type="supplier", entity_id=supplier_id, sub_account=sub,
                                        side="debit", entry_type="supplier_payment", amount=money(value)))
            entries.append(dict(leg_key="funding", entity_type=account_key[0], entity_id=account_key[1], sub_account=account_key[2],
                                side="credit", entry_type="supplier_payment", amount=money(amount)))
            record.update(kind="payment", payable_halalas=paid, advance_halalas=advance,
                          unallocated_payable_halalas=paid if not invoice else 0)
        at = effective_at(payload, scope, supplier_rows)
        meta["accounting_at"] = at
        group_id = None
        if entries:
            for leg in entries:
                leg["metadata"] = meta
            await assert_open_journal_periods(scoped, owner, entries)
            journal = await post_journal_v2(scoped._db, user_id=owner, actor_id=actor["id"], actor_name=actor["id"],
                idempotency_key="supplier-settlement:" + operation_key, txn_type=record["kind"],
                source=PAYMENT_CONTRACT, effective_at=at, entries=entries, metadata=meta, mongo_session=scoped._session)
            group_id = journal["group"]["txn_group_id"]
        else:
            # Metadata-only invoice allocation is still period-gated.
            await assert_open_journal_periods(scoped, owner, [{"metadata": {"accounting_at": at}}])
        result = {"id": operation_key, "kind": record["kind"], "txn_group_id": group_id,
                  "amount_halalas": amount, "payable_halalas": record.get("payable_halalas", 0),
                  "advance_halalas": record.get("advance_halalas", 0)}
        await scoped[OPERATIONS].insert_one({"_id": operation_key, **record, "txn_group_id": group_id,
                                           "effective_at": at, "result": result})
        return {"ok": True, "replayed": False, **result}
    try:
        return await atomic_owner(db, owner, commit)
    except AccountingLedgerV2Error as exc:
        fail(exc.code)


def make_supplier_payment_v2_router(db, current_user, *, bank_port=None):
    router = APIRouter()

    @router.get("/payment-workspace")
    async def workspace(supplier_id: str | None = None, user: dict = Depends(current_user)):
        return await payment_workspace(db, user, supplier_id, bank_port=bank_port)

    @router.post("/{supplier_id}/payments")
    async def payment(supplier_id: str, payload: PaymentIn, user: dict = Depends(current_user)):
        return await settle(db, user, supplier_id, payload, bank_port=bank_port)

    @router.post("/{supplier_id}/allocations")
    async def allocation(supplier_id: str, payload: AllocationIn, user: dict = Depends(current_user)):
        return await settle(db, user, supplier_id, payload, bank_port=bank_port)

    return router
