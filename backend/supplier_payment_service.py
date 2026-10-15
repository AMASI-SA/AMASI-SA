"""V2 supplier settlement; all financial effects and projections share one session.

Invoice payments are allocated to the verified purchase journal. Supplier-level
payments may settle only the remaining unallocated payable (including opening).
The operation record is recovery evidence, never an independent balance.
"""
from datetime import date, datetime, time, timezone
from decimal import Decimal
import uuid

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from pymongo.errors import PyMongoError

from accounting_atomic import SessionDatabase, atomic_owner
from accounting_financial_identity import find_financial_account, list_financial_accounts
from accounting_ledger_v2 import AccountingLedgerV2Error, post_journal_v2, verify_active_opening_v2
from accounting_mz2_balances import read_mz2_write_balances
from accounting_mz2_reports import read_mz2_ledger
from accounting_module_readiness import build_accounting_module_status
from accounting_periods import assert_open_journal_periods
from accounting_report_dates import RIYADH, accounting_instant, report_cutoff
from purchase_receiving_service import (SCHEMA, actor_scope, assert_opening_inventory_initialized,
    fail, now, number, stable_id, stored_number)
from supplier_identity_service import require_supplier_v2

OPERATIONS = "mz2_supplier_payment_operations"
REVISIONS = "mz2_supplier_payment_revisions"
PERMISSION = "accounting.movements.import"


class SupplierPaymentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    operation_id: str = Field(min_length=1, max_length=150)
    expected_payment_revision: int = Field(ge=0, strict=True)
    amount: Decimal = Field(gt=0, max_digits=14, decimal_places=2)
    paid_from_account_id: str = Field(min_length=1, max_length=200)
    payment_date: date
    notes: str = Field("", max_length=2000)


def money(value):
    value = number(value)
    if value != value.quantize(Decimal("0.01")):
        fail("supplier_payment_cent_precision_required", 422)
    return value


async def _guard(db, owner):
    control = await db.mz2_atomic_owners.find_one({"_id": owner}) or {}
    if control.get("ledger_backend_state") != "v2_active":
        fail("supplier_payment_requires_v2_active")
    if control.get("writes_paused") is not False:
        fail("accounting_writes_paused", 423)
    settings = await db.settings.find_one({"user_id": owner}) or {}
    cutover = settings.get("mezan2_financial_cutover") or {}
    scoped = isinstance(db, SessionDatabase)
    verified = await verify_active_opening_v2(db._db if scoped else db,
        user_id=owner, cutover=cutover, mongo_session=db._session if scoped else None)
    if not build_accounting_module_status(cutover, opening_posted_verified=verified)["cutover"]["safe_active"]:
        fail("supplier_payment_accounting_not_safe_active")
    await assert_opening_inventory_initialized(db, owner, cutover)
    return cutover


async def _entries(db, owner, supplier_id):
    scope = await _eligible(db, owner, [("supplier", supplier_id, "payable")])
    return [row for row in scope if (row["entity_type"], row["entity_id"], row.get("sub_account")) == ("supplier", supplier_id, "payable")]


async def _eligible(db, owner, required_accounts=()):
    scope = await read_mz2_ledger(db, owner=owner, required_accounts=required_accounts)
    if scope["status"] != "available" or scope.get("ledger_backend") != "v2":
        fail("supplier_payment_ledger_not_ready", reason=scope["reason"], missing_accounts=scope["missing_accounts"])
    return scope["items"]


def _net(rows):
    return sum((money(row["amount"]) * (1 if row["side"] == "debit" else -1) for row in rows), Decimal(0))


async def _bank(db, owner, bank_id):
    # Reuse the native owner/type/status/currency contract in the same session.
    row = await find_financial_account(db, owner, bank_id, currency="SAR")
    if row is None:
        fail("supplier_payment_bank_unavailable")
    return row


async def _invoice_liability(db, owner, invoice_id):
    invoice = await db.purchase_invoices.find_one({"user_id": owner, "id": invoice_id})
    if not invoice or invoice.get("schema_version") != SCHEMA or invoice.get("state") != "approved":
        fail("supplier_payment_approved_purchase_required")
    liability = await db.liabilities.find_one({"user_id": owner, "id": invoice.get("liability_id")})
    if (not liability or liability.get("schema_version") != SCHEMA or liability.get("source") != "purchase_invoice" or
            liability.get("purchase_invoice_id") != invoice_id or liability.get("accounting_txn_group_id") != invoice.get("txn_group_id") or
            liability.get("supplier_entity_id") != invoice.get("supplier_entity_id") or
            liability.get("counterparty_id") != invoice.get("supplier_entity_id") or
            invoice.get("supplier_entity_id") != invoice.get("supplier_counterparty_id") or
            invoice.get("supplier_entity_id") != invoice.get("supplier_account_id")):
        fail("supplier_payment_invoice_identity_mismatch")
    return invoice, liability


def _allocated_balance(liability, entries):
    credits = [r for r in entries if r["txn_group_id"] == liability["accounting_txn_group_id"]]
    if len(credits) != 1 or credits[0]["side"] != "credit" or credits[0]["entry_type"] != "purchase_payable":
        fail("supplier_payment_purchase_journal_mismatch")
    expected = money(credits[0]["amount"])
    payments = [r for r in entries if (r.get("metadata") or {}).get("liability_id") == liability["id"]]
    if any(r["side"] != "debit" or r["entry_type"] != "supplier_payment" for r in payments):
        fail("supplier_payment_allocation_requires_reconciliation")
    paid = sum((money(r["amount"]) for r in payments), Decimal(0))
    if expected != money(liability.get("expected_amount")) or paid != money(liability.get("paid_amount")) or paid > expected:
        fail("supplier_payment_projection_requires_reconciliation")
    return expected, paid


async def _scope(db, owner, supplier_id, invoice_id=None):
    supplier = await require_supplier_v2(db, owner, supplier_id)
    entries = await _entries(db, owner, supplier_id)
    payable = max(-_net(entries), Decimal(0))
    revision = await db[REVISIONS].find_one({"_id": stable_id("supplier-payments", owner, supplier_id)}) or {}
    result = {"supplier": supplier, "supplier_entity_id": supplier_id, "supplier_payable": payable,
        "expected_payment_revision": revision.get("revision", 0), "source_kind": "invoice" if invoice_id else "unallocated"}
    if invoice_id:
        invoice, liability = await _invoice_liability(db, owner, invoice_id)
        if invoice["supplier_entity_id"] != supplier_id:
            fail("supplier_payment_invoice_identity_mismatch")
        expected, paid = _allocated_balance(liability, entries)
        result.update(liability=liability, invoice=invoice, remaining_amount=expected-paid, expected=expected, paid=paid)
    else:
        allocated = Decimal(0)
        async for liab in db.liabilities.find({"user_id": owner, "schema_version": SCHEMA,
                "source": "purchase_invoice", "$or": [{"supplier_entity_id": supplier_id}, {"counterparty_id": supplier_id}]}):
            await _invoice_liability(db, owner, liab["purchase_invoice_id"])
            expected, paid = _allocated_balance(liab, entries)
            allocated += expected-paid
        if allocated > payable:
            fail("supplier_payment_allocation_requires_reconciliation")
        result.update(remaining_amount=payable-allocated, unallocated_payable_amount=payable-allocated)
    return result


def ledger_number(value):
    return (-1 if value < 0 else 1) * stored_number(abs(value))


async def supplier_statement_balance(db, owner, supplier_id):
    state = await db.mz2_atomic_owners.find_one({"_id": owner}) or {}
    if state.get("ledger_backend_state") != "v2_active":
        fail("supplier_statement_requires_v2_active")
    scope = await _scope(db, owner, supplier_id)
    return {"payable": format(scope["supplier_payable"], ".2f"),
            "unallocated_payable_amount": format(scope["remaining_amount"], ".2f")}


async def supplier_ledger_v2(db, *, user, supplier_id, from_date=None, to_date=None):
    _, owner = await actor_scope(db, user, "accounting.journals_reports.view")
    supplier = await require_supplier_v2(db, owner, supplier_id)
    all_rows = await _eligible(db, owner, [("supplier", supplier_id, "payable")])
    rows = [row for row in all_rows if (row["entity_type"], row["entity_id"], row.get("sub_account")) == ("supplier", supplier_id, "payable")]
    try:
        start = accounting_instant({"metadata": {"accounting_at": from_date}}) if from_date else None
        end = report_cutoff(to_date) if to_date else None
    except ValueError:
        fail("supplier_ledger_invalid_date", 422)
    if start and end and start >= end:
        fail("supplier_ledger_invalid_date", 422)
    opening, credits, debits = Decimal(0), Decimal(0), Decimal(0)
    timeline = []
    rows.sort(key=lambda r: (r["effective_at"], r["entry_no"]))
    for row in rows:
        instant = accounting_instant({"metadata": {"accounting_at": row["effective_at"]}})
        amount = money(row["amount"])
        signed = amount if row["side"] == "credit" else -amount
        if start and instant < start:
            opening += signed
            continue
        if end and instant >= end:
            continue
        credits += amount if row["side"] == "credit" else 0
        debits += amount if row["side"] == "debit" else 0
        timeline.append({"id": row["id"], "txn_group_id": row["txn_group_id"], "created_at": row["effective_at"],
            "side": row["side"], "amount": stored_number(amount), "entry_type": row["entry_type"],
            "running_balance": ledger_number(opening + credits - debits), "notes": (row.get("metadata") or {}).get("notes") or "",
            "source": "accounting_v2", "linked_movement": None})
    total = -_net(rows)
    invoices = []
    for entry in timeline:
        if entry["entry_type"] != "purchase_payable":
            continue
        source = next(row for row in rows if row["id"] == entry["id"])
        invoice_id = (source.get("metadata") or {}).get("invoice_id")
        invoice, liability = await _invoice_liability(db, owner, invoice_id)
        expected, paid = _allocated_balance(liability, rows)
        entry["linked_movement"] = {"movement_id": invoice_id, "doc_number": invoice.get("invoice_number"), "doc_date": invoice["invoice_date"]}
        invoices.append({"movement_id": invoice_id, "txn_group_id": invoice["txn_group_id"],
            "doc_number": invoice.get("invoice_number"), "doc_date": invoice["invoice_date"],
            "total_amount": stored_number(expected), "paid_amount": stored_number(paid), "remaining": stored_number(expected-paid),
            "status": "paid" if paid == expected else "partial" if paid else "unpaid", "payment_terms": "credit",
            "notes": invoice.get("notes") or "", "tax": invoice.get("tax_amount") or 0, "discount": 0,
            "line_items": [{"description": line.get("product_name") or line.get("resource_id") or line.get("product_id"),
                "quantity": line["quantity"], "unit_price": line["unit_cost"]} for line in invoice["lines"]],
            "gl_legs": [{key: row.get(key) for key in ("id", "txn_group_id", "entity_type", "entity_id", "sub_account", "side", "amount", "entry_type")}
                for row in all_rows if row["txn_group_id"] == invoice["txn_group_id"]],
            "payments_applied": [{"txn_group_id": row["txn_group_id"], "amount": row["amount"], "created_at": row["effective_at"]}
                for row in rows if (row.get("metadata") or {}).get("liability_id") == liability["id"]]})
    return {"ok": True, "ledger_backend": "v2", "supplier_entity_id": supplier_id, "supplier": supplier,
        "period": {"from": from_date, "to": to_date, "opening_balance": ledger_number(opening),
            "total_invoiced": stored_number(credits), "total_paid": stored_number(debits),
            "closing_balance": ledger_number(opening+credits-debits), "entries_count": len(timeline),
            "total_credit_purchases": stored_number(credits), "total_payments": stored_number(debits),
            "total_cash_purchases": 0, "cash_invoices_count": 0, "invoice_count_in_period": sum(r["entry_type"] == "purchase_payable" for r in timeline)},
        "timeline": timeline, "invoices": invoices, "manual_entries": [],
        "reconciliation": {"gl_balance_total": ledger_number(total), "derived_balance_period": ledger_number(opening+credits-debits),
            "balance_match": bool(start or end) or opening+credits-debits == total, "drift_detected": False,
            "gl_total_credits_period": stored_number(credits), "gl_total_debits_period": stored_number(debits),
            "gl_entries_in_period": len(timeline), "cash_invoices": [], "drift_credit": [], "ledger_failed": [], "movements_orphaned": []},
        "notes": ["Balances and timeline are derived exclusively from posted Accounting V2 journal legs."]}


async def payment_context(db, *, user, supplier_id=None, invoice_id=None):
    _, owner = await actor_scope(db, user, PERMISSION)
    await _guard(db, owner)
    if invoice_id:
        invoice, _ = await _invoice_liability(db, owner, invoice_id)
        supplier_id = invoice["supplier_entity_id"]
    scope = await _scope(db, owner, supplier_id, invoice_id)
    banks = {}
    for row in await list_financial_accounts(db, owner, currency="SAR"):
        eligible = await _eligible(db, owner, [("bank", row["id"], "main")])
        balance = _net([leg for leg in eligible if (leg["entity_type"], leg["entity_id"], leg.get("sub_account")) == ("bank", row["id"], "main")])
        banks[row["id"]] = {"id": row["id"], "name": row.get("name") or row["id"], "balance": format(balance, ".2f")}
    return {"operation_id": str(uuid.uuid4()), "expected_payment_revision": scope["expected_payment_revision"],
        "liability_id": (scope.get("liability") or {}).get("id"), "supplier_entity_id": supplier_id,
        "supplier_ledger_url": f"/api/accounting/suppliers/{supplier_id}/ledger-detail",
        "remaining_amount": format(scope["remaining_amount"], ".2f"), "supplier_payable": format(scope["supplier_payable"], ".2f"),
        "source_kind": scope["source_kind"], "banks": list(banks.values()), "permission": PERMISSION}


async def pay_supplier(db, *, user, payload, supplier_id=None, invoice_id=None):
    actor, owner = await actor_scope(db, user, PERMISSION)
    try:
        request = SupplierPaymentRequest.model_validate(payload).model_dump(mode="json")
    except ValidationError:
        fail("supplier_payment_invalid_request", 422)
    amount = money(request["amount"])
    request["amount"] = format(amount, ".2f")
    request.update(invoice_id=invoice_id, supplier_id=supplier_id)
    key = stable_id("supplier-payment", owner, request["operation_id"])
    request_hash = stable_id("supplier-payment-request", request)

    async def fresh(scoped):
        active, active_owner = await actor_scope(scoped, user, PERMISSION)
        if active_owner != owner:
            fail("supplier_payment_actor_scope_changed", 403)
        return active

    async def prepare(scoped):
        await fresh(scoped)
        prior = await scoped[OPERATIONS].find_one({"_id": key, "user_id": owner})
        if prior:
            if prior["request_hash"] != request_hash:
                fail("supplier_payment_payload_conflict")
            return prior
        await _guard(scoped, owner)
        resolved = supplier_id
        if invoice_id:
            invoice, _ = await _invoice_liability(scoped, owner, invoice_id)
            resolved = invoice["supplier_entity_id"]
        await require_supplier_v2(scoped, owner, resolved)
        op = {"_id": key, "id": key, "user_id": owner, "supplier_entity_id": resolved,
            "request_hash": request_hash, "request": request, "status": "pending", "created_at": now(), "created_by": actor["id"]}
        await scoped[OPERATIONS].insert_one(op)
        return op

    await atomic_owner(db, owner, prepare)

    async def commit(scoped):
        active = await fresh(scoped)
        op = await scoped[OPERATIONS].find_one({"_id": key, "user_id": owner})
        if op["status"] == "succeeded":
            return op["result"]
        cutover = await _guard(scoped, owner)
        scope = await _scope(scoped, owner, op["supplier_entity_id"], invoice_id)
        if scope["expected_payment_revision"] != request["expected_payment_revision"]:
            fail("supplier_payment_revision_conflict")
        if amount > scope["remaining_amount"] or amount > scope["supplier_payable"]:
            fail("supplier_payment_exceeds_payable")
        bank = await _bank(scoped, owner, request["paid_from_account_id"])
        balances = await read_mz2_write_balances(scoped, owner=owner,
            required_accounts=[("bank", bank["id"], "main"), ("supplier", op["supplier_entity_id"], "payable")])
        if amount > balances.net_balance(entity_type="bank", entity_id=bank["id"], sub_account="main"):
            fail("insufficient_v2_bank_balance")
        if amount > -balances.net_balance(entity_type="supplier", entity_id=op["supplier_entity_id"], sub_account="payable"):
            fail("supplier_payment_exceeds_payable")
        if date.fromisoformat(request["payment_date"]) > datetime.now(RIYADH).date():
            fail("supplier_payment_future_date_not_allowed", 422)
        effective = datetime.combine(date.fromisoformat(request["payment_date"]), time.min, RIYADH).astimezone(timezone.utc)
        if effective < accounting_instant({"metadata": {"accounting_at": cutover["cutover_at"]}}):
            fail("supplier_payment_before_cutover")
        timestamp = effective.isoformat()
        metadata = {"accounting_at": timestamp, "payment_operation_id": key, "supplier_entity_id": op["supplier_entity_id"], "notes": request["notes"]}
        if invoice_id:
            metadata.update(invoice_id=invoice_id, liability_id=scope["liability"]["id"])
        entries = [{"leg_key": "supplier", "entity_type": "supplier", "entity_id": op["supplier_entity_id"],
            "sub_account": "payable", "entry_type": "supplier_payment", "side": "debit", "amount": request["amount"], "metadata": metadata},
            {"leg_key": "bank", "entity_type": "bank", "entity_id": bank["id"], "sub_account": "main",
             "entry_type": "supplier_payment", "side": "credit", "amount": request["amount"], "metadata": metadata}]
        await assert_open_journal_periods(scoped, owner, entries)
        journal = await post_journal_v2(scoped._db, user_id=owner, actor_id=active["id"], actor_name=active["id"],
            idempotency_key=key, txn_type="supplier_payment", source="supplier_payment", effective_at=timestamp,
            entries=entries, metadata=metadata, mongo_session=scoped._session)
        group = journal["group"]["txn_group_id"]
        liability_result = None
        if invoice_id:
            liab = scope["liability"]
            paid = scope["paid"] + amount
            update = {"paid_amount": stored_number(paid), "status": "paid" if paid == scope["expected"] else "partial", "updated_at": now()}
            await scoped.liabilities.update_one({"user_id": owner, "id": liab["id"]}, {"$set": update})
            liability_result = {k: v for k, v in {**liab, **update}.items() if k != "_id"}
            liability_result["remaining_amount"] = format(scope["expected"]-paid, ".2f")
        await scoped[REVISIONS].update_one({"_id": stable_id("supplier-payments", owner, op["supplier_entity_id"])},
            {"$set": {"user_id": owner, "supplier_entity_id": op["supplier_entity_id"], "revision": scope["expected_payment_revision"]+1}}, upsert=True)
        result = {"ok": True, "liability": liability_result, "txn_group_id": group, "supplier_entity_id": op["supplier_entity_id"],
            "operation": {"id": key, "operation_id": request["operation_id"], "status": "succeeded", "request": request}, "ledger_backend": "v2"}
        await scoped[OPERATIONS].update_one({"_id": key}, {"$set": {"status": "succeeded", "result": result, "txn_group_id": group, "completed_at": now()}, "$unset": {"error_code": ""}})
        return result

    try:
        return await atomic_owner(db, owner, commit)
    except (HTTPException, AccountingLedgerV2Error) as exc:
        code = (exc.detail.get("code") if isinstance(exc.detail, dict) else str(exc.detail)) if isinstance(exc, HTTPException) else exc.code
        await db[OPERATIONS].update_one({"_id": key, "user_id": owner, "status": {"$ne": "succeeded"}}, {"$set": {"status": "failed", "error_code": code}})
        if isinstance(exc, HTTPException):
            raise
        fail(code)
    except PyMongoError:
        await db[OPERATIONS].update_one({"_id": key, "user_id": owner, "status": {"$ne": "succeeded"}}, {"$set": {"status": "recovery_required", "error_code": "supplier_payment_retry_required"}})
        fail("supplier_payment_retry_required", 503)
