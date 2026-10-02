"""Native V2 shipping accounting, separate COD custody and fee liability.

All financial decisions share the existing owner transaction and sealed V2
ledger. P02 activation is never performed here. Setup lives in a different,
single-document capability that is safe while paused.
"""
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from decimal import Decimal

from accounting_atomic import atomic_owner
from accounting_ledger_v2 import post_journal_v2, read_verified_journal_metadata_v2
from accounting_module_contract import accounting_owner_id, require_accounting_permission
from accounting_mz2_reports import read_mz2_ledger
from accounting_order_cutover import require_order_created_on_or_after_cutover, OrderCutoverError
from accounting_periods import assert_open_journal_periods
from accounting_sales_tax_service import read_policy, sale_snapshot
from accounting_write_control import fresh_actor
from accounting_writer_transition import assert_writer_allowed
from store_delivery_accounting import require_p02_shipping_financial_writes
import accounting_shipping_bank_port as bank_port
from accounting_shipping_native_contract import (
    SETUP, EVIDENCE, EVENTS, SOURCE, digest, now, instant, money, amount, fail, subaccounts, select_rate, quote,
)
from accounting_shipping_native_setup import pin_setup, read_setup, require_party
from accounting_shipping_native_evidence import external_facts, driver_facts
from accounting_shipping_current_guard import CurrentShippingError, require_native_current_fee


async def _actor(db, owner, actor_id, permission):
    actor = await fresh_actor(db, {"id": actor_id})
    if accounting_owner_id(actor) != owner:
        fail("shipping_owner_scope_mismatch", 403)
    require_accounting_permission(actor, permission)
    return actor


async def _gate(db, owner, actor_id):
    actor = await _actor(db, owner, actor_id, "accounting.settlements.post")
    await assert_writer_allowed(db, owner, "v2")
    await require_p02_shipping_financial_writes(db, user_id=owner)
    return actor


async def _rows(db, owner, accounts=()):
    # Assert before invoking the shared reader: it can select Legacy for other
    # consumers, but that state is never permitted in this native path.
    await assert_writer_allowed(db, owner, "v2")
    scope = await read_mz2_ledger(db, owner=owner, required_accounts=accounts)
    if scope["status"] != "available":
        fail("shipping_v2_opening_required", reason=scope["reason"], missing_accounts=scope["missing_accounts"])
    metadata = {}
    for row in scope["items"]:
        group = row["txn_group_id"]
        if group not in metadata:
            metadata[group] = await read_verified_journal_metadata_v2(db, user_id=owner,
                txn_group_id=group, mongo_session=getattr(db, "_session", None))
        row["metadata"] = {**metadata[group], **(row.get("metadata") or {})}
    return scope["items"]


def _balance(rows, identity):
    return sum((Decimal(r["amount"]) * (1 if r["side"] == "debit" else -1) for r in rows
        if (r["entity_type"], r["entity_id"], r.get("sub_account") or "") == identity), Decimal(0))


def _leg(identity, side, value, key, entry_type):
    return {"entity_type": identity[0], "entity_id": identity[1], "sub_account": identity[2] or None,
            "side": side, "amount": amount(value), "leg_key": key, "entry_type": entry_type}


async def _post(db, owner, actor, key, kind, at, entries, metadata):
    await assert_open_journal_periods(db, owner, [{"metadata": {"accounting_at": at}}])
    journal = await post_journal_v2(db._db, user_id=owner, actor_id=actor["id"],
        actor_name=actor["id"], idempotency_key=key, txn_type=kind, source=SOURCE,
        effective_at=at, entries=entries, metadata=metadata, mongo_session=db._session)
    return {"txn_group_id": journal["group"]["txn_group_id"]}


def _economic(facts):
    return {key: facts[key] for key in ("order_id", "order_number", "order_created_at",
        "party_type", "party_id", "payment_method", "delivery_status", "cod_amount", "sale_total", "currency")}


def _order_rows(rows, facts):
    return [r for r in rows if any(str((r.get("metadata") or {}).get(key) or "") == str(value)
        for key, value in (("order_number", facts["order_number"]),
                           ("order_reference_id", facts["order_number"]), ("order_id", facts["order_id"])))]


def recognition_legs(rows, facts, tax):
    """First sale OR an exact custody reclassification, never a second revenue."""
    target = (facts["party_type"], facts["party_id"], "cod_receivable")
    gross = money(facts["cod_amount"], positive=True)
    related = _order_rows(rows, facts)
    sale_groups = {r["txn_group_id"] for r in related if r["entity_type"] == "revenue" and r["side"] == "credit"}
    if len(sale_groups) > 1:
        fail("shipping_duplicate_sale_requires_reconciliation")
    if not sale_groups:
        if related:
            fail("shipping_existing_order_journal_requires_reconciliation")
        if facts["cod_amount"] != facts["sale_total"]:
            fail("shipping_partial_sale_allocation_required")
        entries = [_leg(target, "debit", gross, "cod", "cod_sale"),
                   _leg(("revenue", "bnpl_sales", ""), "credit", Decimal(tax["net"]), "sale", "cod_sale")]
        if Decimal(tax["tax"]) > 0:
            entries.append(_leg(("tax", "sales_vat_payable", ""), "credit", Decimal(tax["tax"]), "vat", "cod_sale"))
        return entries, "first_sale"
    group = next(iter(sale_groups))
    receivables = [r for r in rows if r["txn_group_id"] == group and r["side"] == "debit"
                   and r.get("sub_account") in {"receivable", "cod_receivable"}]
    if len(receivables) != 1 or Decimal(receivables[0]["amount"]) < gross:
        fail("shipping_prior_sale_receivable_allocation_required")
    source = receivables[0]
    if instant(source["effective_at"]) > instant(facts["delivery_event_at"]):
        fail("shipping_prior_sale_after_delivery_requires_reconciliation")
    identity = (source["entity_type"], source["entity_id"], source.get("sub_account") or "")
    # Use exact order allocation, never infer custody from a pooled balance.
    for row in rows:
        if row["side"] == "credit" and (row["entity_type"], row["entity_id"], row.get("sub_account") or "") == identity:
            meta = row.get("metadata") or {}
            if not any(meta.get(key) for key in ("order_number", "order_reference_id", "order_id")):
                fail("shipping_prior_receivable_allocation_ambiguous")
    allocated = [r for r in related if instant(r["effective_at"]) <= instant(facts["delivery_event_at"])]
    if _balance(allocated, identity) != gross or _balance(rows, identity) < gross:
        fail("shipping_prior_sale_receivable_allocation_required")
    if identity == target:
        return [], "already_on_courier"
    return [_leg(target, "debit", gross, "custody-in", "shipping_cod_reclass"),
            _leg(identity, "credit", gross, "custody-out", "shipping_cod_reclass")], "reclassify"


async def recognize_cod(db, *, owner, actor_id, order_number=None, assignment_id=None):
    return await _seal_delivery(db, owner=owner, actor_id=actor_id, order_number=order_number,
                                assignment_id=assignment_id, require_cod=True)


async def recognize_fee_delivery(db, *, owner, actor_id, order_number=None, assignment_id=None):
    """Seal delivered fee input, including prepaid orders; COD still recognizes first."""
    result = await _seal_delivery(db, owner=owner, actor_id=actor_id, order_number=order_number,
                                  assignment_id=assignment_id, require_cod=False)
    fee = await accrue_fee(db, owner=owner, actor_id=actor_id, evidence_id=result["evidence_id"])
    return {**result, "fee": fee}


async def _seal_delivery(db, *, owner, actor_id, order_number=None, assignment_id=None, require_cod=True):
    if bool(order_number) == bool(assignment_id):
        fail("shipping_one_source_required", 422)
    async def commit(scoped):
        actor = await _gate(scoped, owner, actor_id)
        setup = await pin_setup(scoped, owner)
        facts = await driver_facts(scoped, owner, assignment_id, require_cod=require_cod) if assignment_id else await external_facts(scoped, owner, order_number, setup, require_cod=require_cod)
        await require_party(scoped, owner, setup, facts["party_type"], facts["party_id"])
        state = await scoped.settings.find_one({"user_id": owner}, {"mezan2_financial_cutover": 1}) or {}
        try:
            require_order_created_on_or_after_cutover(facts["order_created_at"],
                (state.get("mezan2_financial_cutover") or {}).get("cutover_at"))
        except OrderCutoverError as exc:
            fail(exc.code)
        key = digest([owner, "delivery", facts["order_id"]])
        fingerprint = digest(_economic(facts))
        prior = await scoped[EVIDENCE].find_one({"user_id": owner, "$or": [
            {"_id": key}, {"order_number": facts["order_number"]}]})
        if prior:
            if prior.get("seal") != digest({k: v for k, v in prior.items() if k not in {"_id", "seal"}}):
                fail("shipping_sealed_delivery_evidence_required")
            if (prior.get("late_delivery_proof") or facts.get("late_delivery_proof")) and (
                    prior.get("late_delivery_proof") != facts.get("late_delivery_proof")
                    or prior.get("delivery_proof_reference") != facts.get("delivery_proof_reference")):
                fail("late_delivery_already_recognized")
            if prior["economic_hash"] != fingerprint:
                fail("shipping_recognition_source_changed")
            return {"state": "already_posted", "evidence_id": key, "txn_group_id": prior["txn_group_id"]}
        is_cod = money(facts["cod_amount"]) > 0 and (facts["party_type"] == "store_driver" or facts["payment_method"] == "COD")
        accounts = [(facts["party_type"], facts["party_id"], "cod_receivable" if is_cod else subaccounts(facts["party_type"])[1])]
        rows = await _rows(scoped, owner, accounts)
        existing_sale = any(r["entity_type"] == "revenue" and r["side"] == "credit" for r in _order_rows(rows, facts))
        tax = None if existing_sale or not is_cod else sale_snapshot(await read_policy(scoped, owner),
            {"recognized_at": facts["delivery_event_at"], "amount": facts["cod_amount"]}, {})
        legs, mode = recognition_legs(rows, facts, tax) if is_cod else ([], "non_cod_delivery")
        metadata = {**{k: v for k, v in _economic(facts).items() if k != "currency"},
                    "evidence_id": key, "recognition_mode": mode}
        if facts["party_type"] == "store_driver":
            metadata["driver_collection_method"] = facts.get("collection_method")
            if facts.get("late_delivery_proof"):
                metadata["late_delivery_proof"] = facts["late_delivery_proof"]
        if tax:
            metadata["sales_tax"] = tax
        result = await _post(scoped, owner, actor, key, "shipping_cod_recognition",
            facts["delivery_event_at"], legs, metadata) if legs else {"txn_group_id": next((r["txn_group_id"] for r in _order_rows(rows, facts) if r["entity_type"] == "revenue"), None)}
        record = {"_id": key, "id": key, **facts, "schema": "mz2_courier_delivery_evidence_v1",
            "economic_hash": fingerprint, "status": "sealed", "created_at": now(),
            "recognized_by": actor_id, "recognition_mode": mode, "txn_group_id": result["txn_group_id"]}
        record["seal"] = digest({k: v for k, v in record.items() if k != "_id"})
        await scoped[EVIDENCE].insert_one(record)
        return {"state": "posted", "evidence_id": key, "txn_group_id": result["txn_group_id"], "mode": mode}
    return await atomic_owner(db, owner, commit)


async def accrue_fee(db, *, owner, actor_id, evidence_id):
    async def commit(scoped):
        actor = await _gate(scoped, owner, actor_id)
        evidence = await scoped[EVIDENCE].find_one({"_id": evidence_id, "user_id": owner, "status": "sealed"})
        if not evidence or evidence.get("seal") != digest({k: v for k, v in evidence.items() if k not in {"_id", "seal"}}):
            fail("shipping_sealed_delivery_evidence_required")
        key = digest([owner, "fee", evidence_id])
        prior = await scoped[EVENTS].find_one({"_id": key, "user_id": owner})
        if prior:
            return {"state": "already_posted", "txn_group_id": prior["txn_group_id"]}
        setup = await pin_setup(scoped, owner)
        kind, party = evidence["party_type"], evidence["party_id"]
        if kind == "courier":
            try:
                await require_native_current_fee(scoped, owner=owner, evidence=evidence, setup=setup)
            except CurrentShippingError as exc:
                fail(str(exc))
        await require_party(scoped, owner, setup, kind, party)
        rate = select_rate(setup, kind, party, evidence["context"], evidence["delivery_event_at"])
        rich = rate.get("kind") == "rich"
        if rich:
            from accounting_shipping_contracts import require_shipping_contract_charges, ShippingContractError
            from accounting_shipping_evidence import pin_bundle
            from accounting_shipping_native_contract_evidence import NativeEvidenceAuthority
            from accounting_shipping_native_rich_contracts import require_current_rich_contract
            version = await require_current_rich_contract(scoped, owner, rate, evidence["delivery_event_at"])
            try:
                proposal = require_shipping_contract_charges(version, owner=owner,
                    courier_id=party, accounting_at=evidence["delivery_event_at"],
                    cod_amount=Decimal(evidence["cod_amount"]) if evidence["payment_method"] == "COD" else None)
            except (ShippingContractError, ValueError, KeyError) as exc:
                fail(str(exc) if isinstance(exc, ShippingContractError) else "shipping_contract_record_invalid")
            bundle = rate.get("evidence_snapshot")
            calculation = proposal["calculation"]
            costs = {k: amount(calculation[k]) for k in
                     ("shipping_net", "shipping_vat", "cod_commission", "cod_commission_vat", "payable_total")}
            costs.update(gross=costs["payable_total"],
                expense=amount(calculation["shipping_net"] + calculation["cod_commission"]),
                input_vat=amount(calculation["shipping_vat"] + calculation["cod_commission_vat"]))
        else:
            costs = quote(rate, Decimal(evidence["cod_amount"]))
        payable = (kind, party, subaccounts(kind)[1])
        await _rows(scoped, owner, [payable])
        legs = []
        expense_legs = (("shipping_net", ("expense", "shipping", "")),
                        ("shipping_vat", ("tax", "input_vat", "")),
                        ("cod_commission", ("expense", "courier_cod_commission", "")),
                        ("cod_commission_vat", ("tax", "input_vat", ""))) if rich else (
                        ("expense", ("expense", "shipping" if kind == "courier" else "store_delivery", "")),
                        ("input_vat", ("tax", "input_vat", "")))
        for name, identity in expense_legs:
            if Decimal(costs[name]) > 0:
                legs.append(_leg(identity, "debit", Decimal(costs[name]), name, "shipping_fee_accrual"))
        if Decimal(costs["gross"]) > 0:
            legs.append(_leg(payable, "credit", Decimal(costs["gross"]), "payable", "shipping_fee_accrual"))
        metadata = {"party_type": kind, "party_id": party, "order_number": evidence["order_number"],
                    "evidence_id": evidence_id, "rate": rate, "costs": costs}
        result = await _post(scoped, owner, actor, key, "shipping_fee_accrual", evidence["delivery_event_at"], legs, metadata) if legs else {"txn_group_id": None}
        if rich:
            # Lock actual approval and retained bytes in this same transaction.
            # A failed pin rolls the journal back. A zero-cost event has no
            # journal, so retain the contract link without inventing one.
            await pin_bundle(scoped, owner=owner, courier_id=party, bundle=bundle,
                link_kind="journal" if result["txn_group_id"] else "contract",
                link_id=result["txn_group_id"] or rate["id"], authority=NativeEvidenceAuthority())
        await scoped[EVENTS].insert_one({"_id": key, "user_id": owner, "kind": "fee", **metadata,
                                        "txn_group_id": result["txn_group_id"], "created_at": now()})
        return {"state": "posted", "txn_group_id": result["txn_group_id"], "costs": costs}
    return await atomic_owner(db, owner, commit)


async def settle(db, *, owner, actor_id, payload):
    async def commit(scoped):
        actor = await _gate(scoped, owner, actor_id)
        fingerprint = digest(payload.model_dump(mode="json"))
        key = digest([owner, "settlement", payload.request_id])
        prior = await scoped[EVENTS].find_one({"_id": key, "user_id": owner})
        if prior:
            if prior["request_hash"] != fingerprint:
                fail("shipping_settlement_idempotency_conflict")
            return {"state": "already_posted", "txn_group_id": prior["txn_group_id"]}
        setup = await pin_setup(scoped, owner)
        await require_party(scoped, owner, setup, payload.party_type, payload.party_id)
        candidates = await scoped.mz2_daily_movements.find({"user_id": owner, "id": payload.movement_id}).limit(2).to_list(2)
        movement = candidates[0] if len(candidates) == 1 else None
        if not movement or movement.get("status") != "unclassified" or movement.get("accounting_event_id"):
            fail("shipping_movement_unavailable")
        if any(movement.get(k) for k in ("receipt_id", "confirmed_provider", "explicit_provider", "suggested_provider")):
            fail("shipping_movement_already_assigned")
        if movement.get("currency") != "SAR":
            fail("shipping_currency_unsupported")
        action = payload.action
        if movement.get("direction") != ("in" if action == "receive_cod" else "out"):
            fail("shipping_movement_direction_invalid")
        bank_id = movement.get("bank_account_id")
        bindings = [r for r in setup["bindings"] if (r["party_type"], r["party_id"]) == (payload.party_type, payload.party_id)]
        if len(bindings) != 1 or not bank_id or bindings[0]["financial_account_id"] != bank_id:
            fail("shipping_confirmed_bank_binding_required")
        bank = await bank_port.require_shipping_bank_identity(scoped, owner, bank_id)
        bank_key = (bank["entity_type"], bank["entity_id"], bank["sub_account"])
        party_key = (payload.party_type, payload.party_id, subaccounts(payload.party_type)[0 if action == "receive_cod" else 1])
        rows = await _rows(scoped, owner, [bank_key, party_key])
        value = money(movement.get("amount"), positive=True)
        try:
            day = datetime.strptime(str(movement.get("movement_date")), "%Y-%m-%d")
        except ValueError:
            fail("shipping_movement_date_invalid")
        at = day.replace(tzinfo=ZoneInfo("Asia/Riyadh")).astimezone(timezone.utc).isoformat()
        cutoff = instant(at)
        if cutoff > datetime.now(timezone.utc):
            fail("shipping_settlement_in_future")
        balance = _balance(rows, party_key) * (1 if action == "receive_cod" else -1)
        if payload.party_type == "store_driver" and action == "receive_cod":
            # Generic merchant receipts settle CASH handovers only. Non-cash
            # responsibility is discharged exclusively by approved review.
            cash_evidence = await scoped[EVIDENCE].find({"user_id": owner, "party_type": "store_driver",
                "party_id": payload.party_id, "collection_method": "cash", "status": "sealed"}).limit(10001).to_list(10001)
            if len(cash_evidence) > 10000 or any(e.get("seal") != digest({k: v for k, v in e.items()
                    if k not in {"_id", "seal"}}) for e in cash_evidence):
                fail("driver_cash_responsibility_evidence_required")
            cash_rows = {r["id"]: r for evidence in cash_evidence for r in _order_rows(rows, evidence)}
            cash_debits = _balance(cash_rows.values(), party_key)
            cash_receipts = [r for r in rows if
                (r["entity_type"], r["entity_id"], r.get("sub_account")) == party_key and r["side"] == "credit"
                and (r.get("metadata") or {}).get("action") == "receive_cod"
                and (r.get("metadata") or {}).get("settlement_origin") != "driver_payment_review"]
            cash_credits = sum((Decimal(r["amount"]) for r in cash_receipts), Decimal(0))
            if value > cash_debits - cash_credits:
                fail("driver_non_cash_approved_review_required")
            # Earlier non-cash/opening COD cannot fund a receipt before cash
            # collection, even when the driver's total dated liability is enough.
            dated_cash_rows = {r["id"]: r for evidence in cash_evidence
                if instant(evidence.get("delivery_event_at")) <= cutoff
                for r in _order_rows(rows, evidence) if instant(r["effective_at"]) <= cutoff}
            dated_cash_credits = sum((Decimal(r["amount"]) for r in cash_receipts
                if instant(r["effective_at"]) <= cutoff), Decimal(0))
            if value > _balance(dated_cash_rows.values(), party_key) - dated_cash_credits:
                fail("shipping_settlement_before_liability")
        if value > balance:
            fail("shipping_cod_over_receive" if action == "receive_cod" else "shipping_fee_over_payment")
        if action == "pay_fee" and value > _balance(rows, bank_key):
            fail("shipping_insufficient_bank_balance")
        # A later recognized balance must not authorize a backdated settlement.
        dated_rows = [r for r in rows if instant(r["effective_at"]) <= cutoff]
        dated_balance = _balance(dated_rows, party_key) * (1 if action == "receive_cod" else -1)
        if value > dated_balance:
            fail("shipping_settlement_before_liability")
        legs = [_leg(bank_key, "debit" if action == "receive_cod" else "credit", value, "bank", "shipping_settlement"),
                _leg(party_key, "credit" if action == "receive_cod" else "debit", value, "party", "shipping_settlement")]
        metadata = {"party_type": payload.party_type, "party_id": payload.party_id, "action": action,
                    "movement_id": payload.movement_id, "binding": bindings[0], "reason": payload.reason}
        result = await _post(scoped, owner, actor, key, "shipping_" + action, at, legs, metadata)
        changed = await scoped.mz2_daily_movements.update_one({"_id": movement["_id"], "user_id": owner,
            "status": "unclassified", "accounting_event_id": {"$in": [None, ""]}},
            {"$set": {"status": "accounting_posted", "accounting_event_id": key,
                      "accounting_action": action, "accounting_txn_group_id": result["txn_group_id"]}})
        if changed.matched_count != 1:
            fail("shipping_movement_changed")
        await scoped[EVENTS].insert_one({"_id": key, "user_id": owner, "kind": "settlement", **metadata,
            "request_hash": fingerprint, "txn_group_id": result["txn_group_id"], "created_at": now()})
        return {"state": "posted", "txn_group_id": result["txn_group_id"]}
    return await atomic_owner(db, owner, commit)


async def statement(db, *, owner, actor_id, kind, identity):
    await _actor(db, owner, actor_id, "accounting.shipping.view")
    setup = await read_setup(db, owner)
    await require_party(db, owner, setup, kind, identity)
    cod, payable = subaccounts(kind)
    rows = await _rows(db, owner, [(kind, identity, cod), (kind, identity, payable)])
    selected = [r for r in rows if (r["entity_type"], r["entity_id"]) == (kind, identity)]
    total = lambda action, side: amount(sum((Decimal(r["amount"]) for r in selected if
        (r.get("metadata") or {}).get("action") == action and r["side"] == side), Decimal(0)))
    fees = sum((Decimal(r["amount"]) for r in selected if r["entry_type"] == "shipping_fee_accrual" and r["side"] == "credit"), Decimal(0))
    return {"ledger_source": "accounting_v2", "party_type": kind, "party_id": identity,
            "cod_receivable": amount(_balance(rows, (kind, identity, cod))),
            "payable": amount(-_balance(rows, (kind, identity, payable))),
            "collections": total("receive_cod", "credit"), "payments": total("pay_fee", "debit"),
            "recognized_fees": amount(fees), "entries": selected,
            "unreconciled_items": [r for r in selected if r.get("sub_account") not in {cod, payable}]}
