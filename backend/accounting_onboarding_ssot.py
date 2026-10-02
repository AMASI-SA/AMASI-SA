"""Native MZ2 setup contracts. Callers enforce fresh actor/owner permission.

These helpers never post, activate, update cutover or mutate operating invoices.
Fee schedules serialize through a single Mongo document CAS, including audit.
"""
from datetime import date, datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from hashlib import sha256
from typing import Literal
from uuid import uuid4

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, model_validator
from pymongo.errors import DuplicateKeyError

from accounting_settlement_service import PROVIDERS

POLICIES = "mz2_provider_fee_policies_v2"
PREPAIDS = "mz2_prepaid_selections_v2"
FACTS = "mz2_opening_facts_v2"
OBLIGATIONS = "operating_recurring_obligations_v2"
INVOICES = "operating_recurring_invoices_v2"
FACT_CONTRACTS = {
    "prepaid_expense": ("asset", "prepaid_expense", "debit"),
    "accrued_expense": ("liability", "accrued_expense", "credit"),
    "other_payable": ("liability", "other_payable", "credit"),
    "other_receivable": ("asset", "other_receivable", "debit"),
    "sales_vat_payable": ("tax", "sales_vat_payable", "credit"),
    "input_vat": ("tax", "input_vat", "debit"),
}


def _fail(code, **details):
    raise HTTPException(409, detail={"code": code, **details})


def _now():
    return datetime.now(timezone.utc).isoformat()


def _key(*parts):
    import json
    return sha256(json.dumps(parts).encode()).hexdigest()


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class FeePolicyCreate(Contract):
    provider: Literal["salla", "tamara", "tabby", "emkan"]
    percentage: Decimal = Field(ge=0, le=100, allow_inf_nan=False)
    fixed_amount: Decimal = Field(ge=0, allow_inf_nan=False)
    minimum: Decimal | None = Field(default=None, ge=0, allow_inf_nan=False)
    maximum: Decimal | None = Field(default=None, ge=0, allow_inf_nan=False)
    vat_treatment: Literal["inclusive", "exclusive", "exempt", "not_applicable"]
    effective_from: date
    effective_to: date | None = None
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    evidence: str = Field(min_length=3, max_length=1000)

    @model_validator(mode="after")
    def valid_range(self):
        if self.effective_to and self.effective_to < self.effective_from:
            raise ValueError("fee_policy_date_range_invalid")
        if self.minimum is not None and self.maximum is not None and self.maximum < self.minimum:
            raise ValueError("fee_policy_amount_range_invalid")
        return self


def _overlap(a, b):
    # Both effective boundaries are inclusive.
    return a["effective_from"] <= (b.get("effective_to") or "9999-12-31") and b["effective_from"] <= (a.get("effective_to") or "9999-12-31")


async def create_fee_policy(db, owner, actor_id, payload: FeePolicyCreate):
    key = _key(owner, payload.provider, payload.currency)
    candidate = payload.model_dump(mode="json")
    candidate.update(id=str(uuid4()), user_id=owner, status="active", version=1,
                     confirmed_by=actor_id, confirmed_at=_now())
    # Atomic append with a CAS revision prevents cross-worker overlap races.
    for _ in range(8):
        old = await db[POLICIES].find_one({"_id": key, "user_id": owner})
        policies = (old or {}).get("policies", [])
        if any(_overlap(row, candidate) for row in policies if row.get("status") == "active"):
            _fail("provider_fee_policy_overlap", provider=payload.provider)
        audit = {"action": "policy_confirmed", "actor_id": actor_id, "at": candidate["confirmed_at"], "policy_id": candidate["id"]}
        if old is None:
            try:
                await db[POLICIES].insert_one({"_id": key, "user_id": owner, "provider": payload.provider,
                    "currency": payload.currency, "version": 1, "policies": [candidate], "audit": [audit]})
                return candidate
            except DuplicateKeyError:
                continue
        result = await db[POLICIES].update_one({"_id": key, "user_id": owner, "version": old["version"]},
            {"$push": {"policies": candidate, "audit": audit}, "$inc": {"version": 1}})
        if result.modified_count == 1:
            return candidate
    _fail("provider_fee_policy_concurrent_update")


async def list_fee_policies(db, owner):
    rows = await db[POLICIES].find({"user_id": owner}, {"_id": 0}).to_list(1001)
    if len(rows) > 1000:
        _fail("provider_fee_policy_scope_too_large")
    return [policy for row in rows for policy in row.get("policies", [])]


async def resolve_fee_policy(db, owner, provider, transaction_date, currency="SAR"):
    if provider not in PROVIDERS:
        _fail("provider_identifier_not_canonical")
    day = date.fromisoformat(str(transaction_date)).isoformat()
    matches = [row for row in await list_fee_policies(db, owner)
               if row.get("provider") == provider and row.get("currency") == currency
               and row.get("status") == "active" and row["effective_from"] <= day
               and day <= (row.get("effective_to") or "9999-12-31")]
    if len(matches) != 1:
        _fail("provider_fee_policy_missing" if not matches else "provider_fee_policy_ambiguous", provider=provider, date=day)
    return matches[0]


def prepaid_calculation(invoice, cutover):
    """Native invoices specify inclusive period_end; cutover starts the new era."""
    cutover = date.fromisoformat(str(cutover))
    if invoice.get("payment_status") != "paid":
        return {"eligible": False, "reason": "invoice_unpaid"}
    try:
        coverage_end = date.fromisoformat(invoice["period_end"])
    except (ValueError, KeyError, TypeError):
        return {"eligible": False, "reason": "invoice_contract_invalid"}
    if coverage_end < cutover:
        # Native historical benchmark invoices often lack paid_date. Expired
        # coverage cannot become a prepaid and need not invent payment proof.
        return {"eligible": False, "reason": "coverage_ended_before_cutover"}
    if not invoice.get("paid_date"):
        return {"eligible": False, "reason": "payment_date_missing"}
    try:
        paid = date.fromisoformat(invoice["paid_date"])
        start, end = (date.fromisoformat(invoice[key]) for key in ("period_start", "period_end"))
        amount = Decimal(str(invoice["amount"]))
    except (ValueError, KeyError, TypeError, ArithmeticError):
        return {"eligible": False, "reason": "invoice_contract_invalid"}
    if not amount.is_finite() or amount <= 0 or end < start:
        return {"eligible": False, "reason": "invoice_contract_invalid"}
    if paid >= cutover:
        return {"eligible": False, "reason": "not_paid_before_cutover"}
    days = (end - start).days + 1
    consumed_days = min(max((cutover - start).days, 0), days)
    consumed = (amount * Decimal(consumed_days) / days).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    remaining = amount - consumed
    return {"eligible": remaining > 0, "reason": None if remaining > 0 else "coverage_ended_before_cutover",
            "total_days": days, "consumed_days": consumed_days, "remaining_days": days - consumed_days,
            "consumed_before_cutover": str(consumed), "remaining_prepaid_after_cutover": str(remaining),
            "cutover_date": cutover.isoformat(), "period_end_inclusive": True}


class PrepaidSelection(Contract):
    obligation_id: str = Field(min_length=1, max_length=120)
    invoice_id: str = Field(min_length=1, max_length=120)
    cutover_date: date
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    evidence: str = Field(min_length=3, max_length=1000)


async def _native_pair(db, owner, obligation_id, invoice_id):
    obligations = await db[OBLIGATIONS].find({"user_id": owner, "id": obligation_id}, {"_id": 0}).to_list(2)
    invoices = await db[INVOICES].find({"user_id": owner, "id": invoice_id, "obligation_id": obligation_id}, {"_id": 0}).to_list(2)
    if len(obligations) != 1 or len(invoices) != 1 or obligations[0].get("status") not in {"active", "stopped"}:
        _fail("recurring_identity_unresolved")
    return obligations[0], invoices[0]


async def save_prepaid_selection(db, owner, actor_id, payload: PrepaidSelection):
    obligation, invoice = await _native_pair(db, owner, payload.obligation_id, payload.invoice_id)
    calculation = prepaid_calculation(invoice, payload.cutover_date)
    if not calculation["eligible"]:
        _fail(calculation["reason"])
    if invoice.get("currency") and invoice["currency"] != payload.currency:
        _fail("prepaid_currency_conflict")
    identity = _key(owner, payload.invoice_id, payload.cutover_date.isoformat())
    row = {**payload.model_dump(mode="json"), "id": identity, "user_id": owner,
           "entity_type": "asset", "entity_id": identity, "category": "prepaid_expense",
           "sub_account": "prepaid_expense", "version": 1, "status": "active",
           "source": INVOICES, "confirmed_by": actor_id, "confirmed_at": _now(),
           "source_snapshot": {"obligation": {k: v for k, v in obligation.items() if k != "_id"},
                               "invoice": {k: v for k, v in invoice.items() if k != "_id"}},
           "calculation": calculation}
    try:
        await db[PREPAIDS].insert_one({"_id": identity, **row})
    except DuplicateKeyError:
        old = await db[PREPAIDS].find_one({"_id": identity, "user_id": owner}, {"_id": 0})
        if all(old.get(k) == row.get(k) for k in (*payload.model_dump(), "source_snapshot", "calculation")):
            return old
        _fail("prepaid_selection_conflict")
    return row


async def list_prepaid_candidates(db, owner, cutover):
    obligations = await db[OBLIGATIONS].find({"user_id": owner}, {"_id": 0}).to_list(5001)
    invoices = await db[INVOICES].find({"user_id": owner}, {"_id": 0}).to_list(20001)
    selections = await db[PREPAIDS].find({"user_id": owner, "cutover_date": str(cutover)}, {"_id": 0}).to_list(20001)
    if len(obligations) > 5000 or len(invoices) > 20000 or len(selections) > 20000:
        _fail("recurring_scope_too_large")
    by_id = {row["id"]: row for row in obligations}
    if len(by_id) != len(obligations) or len({row["id"] for row in invoices}) != len(invoices):
        _fail("recurring_identity_ambiguous")
    selected = {row["invoice_id"]: row for row in selections}
    items, blockers = [], []
    for invoice in invoices:
        obligation = by_id.get(invoice.get("obligation_id"))
        if not obligation or obligation.get("status") not in {"active", "stopped"}:
            blockers.append({"code": "recurring_identity_unresolved", "invoice_id": invoice.get("id")})
            continue
        calculation = prepaid_calculation(invoice, cutover)
        saved = selected.get(invoice["id"])
        stale = bool(saved and (saved["source_snapshot"]["invoice"] != invoice or saved["source_snapshot"]["obligation"] != obligation))
        if stale:
            blockers.append({"code": "prepaid_source_changed", "invoice_id": invoice["id"]})
        items.append({"obligation_id": obligation["id"], "invoice_id": invoice["id"],
            "title": obligation.get("title"), "type": obligation.get("expense_type"),
            "entity_type": obligation.get("entity_type"), "entity_id": obligation.get("entity_id"),
            "entity_name": obligation.get("entity_name"), "coverage_start": invoice.get("period_start"),
            "coverage_end": invoice.get("period_end"), "payment_date": invoice.get("paid_date"),
            "payment_amount": invoice.get("amount") if invoice.get("payment_status") == "paid" else None,
            "currency": (saved or {}).get("currency", invoice.get("currency")),
            "auto_renew": obligation.get("auto_renew"), "evidence": (saved or {}).get("evidence"),
            "selection": saved, "source_stale": stale, "calculation": calculation})
    return {"items": items, "blockers": blockers, "source": OBLIGATIONS,
            "identity_only": False, "accounting_identity_requires_selection": True}


class TypedFactCreate(Contract):
    category: Literal["prepaid_expense", "accrued_expense", "other_payable", "other_receivable", "sales_vat_payable", "input_vat"]
    display_name: str = Field(min_length=1, max_length=160)
    reference: str = Field(min_length=1, max_length=160)
    amount: Decimal = Field(ge=0, allow_inf_nan=False, decimal_places=2)
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    cutover_date: date
    evidence: str = Field(min_length=3, max_length=1000)
    source: Literal["documented_opening_fact"] = "documented_opening_fact"
    manual_contract: str | None = Field(default=None, min_length=3, max_length=1000)

    @model_validator(mode="after")
    def exceptional_contract(self):
        if self.category == "prepaid_expense" and not self.manual_contract:
            raise ValueError("exceptional_prepaid_contract_required")
        return self


async def create_typed_fact(db, owner, actor_id, payload: TypedFactCreate):
    entity_type, sub_account, side = FACT_CONTRACTS[payload.category]
    identity = _key(owner, payload.category, payload.reference, str(payload.cutover_date))
    row = {**payload.model_dump(mode="json"), "id": identity, "entity_id": identity,
           "user_id": owner, "entity_type": entity_type, "sub_account": sub_account,
           "side": side, "version": 1, "status": "active", "created_at": _now(), "created_by": actor_id}
    try:
        await db[FACTS].insert_one({"_id": identity, **row})
    except DuplicateKeyError:
        old = await db[FACTS].find_one({"_id": identity, "user_id": owner}, {"_id": 0})
        if all(old.get(k) == row[k] for k in payload.model_dump()):
            return old
        _fail("opening_fact_reference_conflict")
    return row


async def list_typed_facts(db, owner):
    rows = await db[FACTS].find({"user_id": owner}, {"_id": 0}).to_list(10001)
    if len(rows) > 10000:
        _fail("opening_fact_scope_too_large")
    return rows


async def verify_selected_contracts(db, owner, cutover, fee_policy_ids, prepaid_selection_ids,
                                    typed_fact_ids, opening_lines):
    """Read fresh setup/source facts for preview, review and handoff hashing."""
    from zoneinfo import ZoneInfo
    if "T" in str(cutover):
        day = datetime.fromisoformat(str(cutover)).astimezone(ZoneInfo("Asia/Riyadh")).date().isoformat()
    else:
        day = date.fromisoformat(str(cutover)).isoformat()
    blockers, snapshots = [], {"fee_policies": [], "prepaids": [], "typed_facts": []}
    policies = await list_fee_policies(db, owner)
    facts = await list_typed_facts(db, owner)
    candidates = await list_prepaid_candidates(db, owner, day)
    blockers.extend(candidates["blockers"])
    # Only selected sources may affect this session; stale selected sources block.
    prepaids = [item["selection"] for item in candidates["items"] if item.get("selection")]
    stale = {item["selection"]["id"] for item in candidates["items"] if item.get("selection") and item["source_stale"]}
    for item in candidates["items"]:
        if item["calculation"]["eligible"]:
            if not item.get("currency"):
                blockers.append({"code": "prepaid_currency_missing", "invoice_id": item["invoice_id"]})
            if not item.get("evidence"):
                blockers.append({"code": "prepaid_evidence_missing", "invoice_id": item["invoice_id"]})
            if not item.get("selection") or item["selection"]["id"] not in prepaid_selection_ids:
                blockers.append({"code": "prepaid_candidate_not_selected", "invoice_id": item["invoice_id"]})
        elif item["calculation"].get("reason") in {"payment_date_missing", "invoice_contract_invalid"}:
            blockers.append({"code": item["calculation"]["reason"], "invoice_id": item["invoice_id"]})
    for ids, rows, key in ((fee_policy_ids, policies, "fee_policies"),
                           (prepaid_selection_ids, prepaids, "prepaids"),
                           (typed_fact_ids, facts, "typed_facts")):
        if len(ids) != len(set(ids)):
            blockers.append({"code": "onboarding_duplicate_contract_selection", "source": key})
        for identity in set(ids):
            matches = [row for row in rows if row.get("id") == identity and row.get("status") == "active"]
            if len(matches) != 1:
                blockers.append({"code": "onboarding_contract_unresolved", "source": key, "id": identity})
                continue
            row = matches[0]
            if key != "fee_policies" and row.get("cutover_date") != day:
                blockers.append({"code": "onboarding_contract_cutover_mismatch", "id": identity})
            if identity in stale:
                blockers.append({"code": "prepaid_source_changed", "id": identity})
            snapshots[key].append(row)
    selected_facts = snapshots["prepaids"] + snapshots["typed_facts"]
    used = set()
    for line in opening_lines:
        category = line.get("category")
        if category == "provider_receivable":
            try:
                policy = await resolve_fee_policy(db, owner, line.get("entity_id"), day, line.get("original_currency"))
                if policy["id"] not in fee_policy_ids:
                    blockers.append({"code": "provider_fee_policy_not_selected", "provider": line.get("entity_id")})
            except HTTPException as exc:
                blockers.append(exc.detail)
        if category not in FACT_CONTRACTS:
            continue
        matches = [fact for fact in selected_facts if fact["entity_id"] == line.get("entity_id") and fact["category"] == category]
        if len(matches) != 1:
            blockers.append({"code": "opening_line_typed_fact_required", "category": category, "entity_id": line.get("entity_id")})
            continue
        fact = matches[0]
        used.add(fact["id"])
        amount = fact.get("amount") or fact.get("calculation", {}).get("remaining_prepaid_after_cutover")
        if (Decimal(str(line.get("original_amount"))) != Decimal(str(amount))
                or line.get("original_currency") != fact["currency"]
                or line.get("evidence_file_id") != fact["evidence"]
                or (Decimal(str(amount)) == 0) != (line.get("meaning") == "zero")):
            blockers.append({"code": "opening_line_typed_fact_mismatch", "id": fact["id"]})
    for fact in selected_facts:
        if fact["id"] not in used:
            blockers.append({"code": "selected_opening_fact_line_missing", "id": fact["id"]})
    for fact in facts:
        if (fact.get("status") == "active" and fact.get("cutover_date") == day
                and fact["id"] not in typed_fact_ids):
            blockers.append({"code": "opening_fact_not_selected", "id": fact["id"]})
    for rows in snapshots.values():
        rows.sort(key=lambda row: row["id"])
    return {"snapshots": snapshots, "blockers": blockers}
