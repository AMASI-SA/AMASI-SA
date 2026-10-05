"""Temporary operational balances: baseline and evidenced money movements.

This module intentionally imports no accounting service. Confirmed obligations
are not bank cash. Bank statements are optional later matching evidence.
"""
from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from uuid import uuid4

from operational_balance_store import audit, digest, fail, mutate, now, read, remember, replay, RECEIPTS
from operational_balance_engine import reconcile_credits

KINDS = {"employee", "provider", "courier", "store_driver", "ad_account", "supplier",
         "external_person", "bank", "cash", "employee_custody", "operating_expense", "owner_withdrawal"}


def money(value, *, zero=False):
    try:
        amount = Decimal(str(value))
        if (not amount.is_finite() or amount < 0 or (not zero and amount == 0)
                or amount != amount.quantize(Decimal(".01")) or amount > Decimal("999999999999.99")):
            raise ValueError()
        return amount
    except (ValueError, InvalidOperation, TypeError):
        fail("operational_amount_invalid", "أدخل مبلغًا صحيحًا بمنزلتين عشريتين", 422)


def fmt(value):
    return format(Decimal(str(value)), ".2f")


def party_key(kind, identity, currency):
    return digest([kind, identity, currency])


async def entity(db, owner, kind, identity, currency):
    from operational_balance_sources import entities
    if kind not in KINDS:
        fail("operational_party_type_invalid", "نوع الجهة غير صحيح", 422)
    try:
        rows = [r for r in await entities(db, owner, kind) if r["id"] == identity]
    except ValueError:
        fail("operational_entity_setup_incomplete", "إعداد الجهات في ميزان 2 غير مكتمل أو ملتبس")
    if len(rows) != 1:
        fail("operational_mz2_identity_required", "الجهة غير متاحة في ميزان 2")
    row = rows[0]
    if row.get("ready") is False or row.get("settings_complete") is False or row.get("currency") != currency:
        fail("operational_entity_setup_incomplete", "إعداد الجهة أو عملتها غير مكتمل")
    return row


async def active_gate(db, owner, state):
    if state["status"] != "active":
        fail("operational_not_active", "النظام غير نشط أو أُغلق للقراءة")
    await accounting_inactive(db, owner)


async def accounting_inactive(db, owner):
    # Read control metadata only. Never initialize, change or invoke its writer.
    marker = await db["mz2_atomic_owners"].find_one({"_id": owner}) or {}
    settings = await db["settings"].find_one({"user_id": owner}, {"mezan2_financial_cutover": 1}) or {}
    if (marker.get("ledger_backend_state") == "v2_active"
            or (settings.get("mezan2_financial_cutover") or {}).get("status") == "active"):
        fail("operational_accounting_active", "المحاسبة مفعلة؛ الحركات التشغيلية الجديدة متوقفة")


async def save_opening(db, owner, actor, payload, *, finish=False, clock=None):
    stamp = clock or now()
    action = "finish" if finish else "opening"

    async def apply(state):
        prior = replay(state, action, payload)
        if prior is not None:
            return prior
        if state["status"] != "draft":
            fail("operational_opening_closed", "الأرصدة محفوظة؛ التصحيح بحركة موثقة")
        await accounting_inactive(db, owner)
        opening = payload.get("opening") if finish else payload
        if opening:
            amount = money(opening["amount"], zero=True)
            row = await entity(db, owner, opening["party_type"], opening["party_id"], opening["currency"])
            key = party_key(opening["party_type"], opening["party_id"], opening["currency"])
            hybrid = opening["party_type"] == "ad_account" and row.get("funding_mode") == "hybrid"
            matches = [saved for saved in state["openings"].values() if saved["party_type"] == opening["party_type"]
                       and saved["party_id"] == opening["party_id"] and saved["currency"] == opening["currency"]]
            if matches and (not hybrid or any(saved["direction"] == opening["direction"] for saved in matches)):
                fail("operational_opening_duplicate", "سبق حفظ رصيد هذه الجهة؛ التصحيح بحركة موثقة")
            if opening["direction"] not in {"for_party", "for_us"}:
                fail("operational_direction_invalid", "حدد له أو عليه", 422)
            if hybrid:
                key = digest([key, opening["direction"]])
            item = {"id": key, "party_type": opening["party_type"], "party_id": opening["party_id"],
                    "name": row["name"], "currency": opening["currency"], "amount": fmt(amount),
                    "direction": opening["direction"], "created_at": stamp, "approved_at": stamp,
                    "actor_id": actor, "baseline": True, "funding_mode": row.get("funding_mode")}
            state["openings"][key] = item
            audit(state, actor, "baseline_saved", None, item, "رصيد ابتدائي معتمد", "opening", stamp)
        if finish:
            if not state["openings"]:
                fail("operational_opening_required", "أدخل الأرصدة قبل الإنهاء")
            from operational_balance_sources import start_baselines
            try:
                state["source_baselines"] = await start_baselines(db, owner, stamp)
            except ValueError:
                fail("operational_source_setup_incomplete", "تعذر تثبيت مصادر بداية النظام؛ أكمل إعداد ميزان 2")
            state.update(status="active", started_at=stamp)
            audit(state, actor, "system_started", "draft", "active", "حفظ وإنهاء", "opening", stamp)
        result = {"status": state["status"], "opening_count": len(state["openings"]),
                  "started_at": state["started_at"]}
        remember(state, action, payload, result)
        return result
    return await mutate(db, owner, apply)


def outstanding(state, obligation_id, *, include_expected=False):
    reconcile_credits(state)
    obligation = (state.get("engine", {}).get("obligations") or {}).get(obligation_id)
    if not obligation:
        # Baselines can be settled without replaying old orders.
        opening = state["openings"].get(obligation_id)
        if not opening or opening["party_type"] in {"bank", "cash", "employee_custody"}:
            fail("operational_obligation_missing", "الالتزام غير متاح")
        obligation = {**opening, "confirmed": opening["amount"],
                      "direction": "payable" if opening["direction"] == "for_party" else "receivable"}
    if obligation.get("funding_type") == "prepaid":
        fail("operational_wallet_spend_not_payable", "الصرف يستهلك المحفظة المدفوعة مسبقًا ولا يُسدّد مرة أخرى")
    used = sum((Decimal(a["amount"]) for m in state["movements"] for a in m.get("allocations", [])
                if a["obligation_id"] == obligation_id), Decimal(0))
    pending = Decimal(obligation.get("expected", "0")) if include_expected and obligation.get("kind") == "recurring" else Decimal(0)
    return obligation, max(Decimal(obligation["confirmed"]) + pending - used, Decimal(0))


async def create_movement(db, owner, actor, payload, *, source="mezan2", clock=None):
    stamp = clock or now()

    async def apply(state):
        prior = replay(state, "movement", payload)
        if prior is not None:
            return prior
        await active_gate(db, owner, state)
        amount = money(payload["amount"])
        currency = payload["currency"]
        row = await entity(db, owner, payload["party_type"], payload["party_id"], currency)
        bank = None
        account_type = payload.get("source_account_type", "bank_auto")
        if account_type not in {"bank_auto", "bank", "cash", "employee_custody"}:
            fail("operational_source_account_type_invalid", "نوع مصدر الحركة غير صحيح", 422)
        if payload.get("bank_id"):
            # Exact financial ID, only bank/cash. No fuzzy fallback or old accounts.
            from operational_balance_sources import entities
            try:
                account_kinds = ("bank", "cash") if account_type == "bank_auto" else (account_type,)
                banks = [r for k in account_kinds for r in await entities(db, owner, k)
                         if r["id"] == payload["bank_id"] and r.get("currency") == currency
                         and r.get("ready") is not False and r.get("settings_complete") is not False]
            except ValueError:
                fail("operational_bank_setup_incomplete", "إعداد البنوك والصناديق في ميزان 2 غير مكتمل")
            if len(banks) != 1:
                fail("operational_bank_required", "اختر بنكًا أو صندوقًا معتمدًا من ميزان 2")
            bank = banks[0]
        if bank and bank["kind"] == "employee_custody":
            if row["kind"] != "operating_expense" or payload["direction"] != "outgoing" or payload["kind"] != "payment":
                fail("operational_custody_source_invalid", "الصرف من العهدة مخصص للمصروف التشغيلي الصادر")
            balance = next((r for r in report(state)["parties"] if r["party_type"] == "employee_custody"
                            and r["party_id"] == bank["id"] and r["currency"] == currency), {})
            if amount > Decimal(balance.get("custody_remaining", "0")):
                fail("operational_custody_insufficient", "مبلغ الصرف يتجاوز المتبقي في العهدة")
        if row["kind"] == "employee_custody" and payload["kind"] != "correction":
            valid = ((payload["kind"] == "payment" and payload["direction"] == "outgoing")
                     or (payload["kind"] == "collection" and payload["direction"] == "incoming"))
            if not valid or not bank or bank["kind"] not in {"bank", "cash"} or payload.get("allocations"):
                fail("operational_custody_movement_invalid", "تمويل العهدة صادر من بنك أو صندوق وإرجاعها وارد إليه")
            if payload["direction"] == "incoming":
                balance = next((r for r in report(state)["parties"] if r["party_type"] == "employee_custody"
                                and r["party_id"] == row["id"] and r["currency"] == currency), {})
                if amount > Decimal(balance.get("custody_remaining", "0")):
                    fail("operational_custody_insufficient", "مبلغ الإرجاع يتجاوز المتبقي في العهدة")
        if row["kind"] in {"operating_expense", "owner_withdrawal"} and payload["kind"] != "correction":
            if payload["direction"] != "outgoing" or payload["kind"] not in {"payment", "settlement"}:
                fail("operational_expense_direction_invalid", "المصروف أو السحب حركة صادرة")
        if payload["kind"] != "correction" and bank is None:
            fail("operational_bank_required", "حدد البنك أو الصندوق الذي تحرك منه أو إليه المبلغ")
        if payload["kind"] == "correction" and not payload.get("note", "").strip():
            fail("operational_correction_reason_required", "التصحيح يتطلب سببًا موثقًا")
        if payload["kind"] == "correction" and (bank or payload.get("allocations")):
            fail("operational_correction_scope", "التصحيح يخص رصيد الجهة المختارة دون تخصيص أو حركة بنك أخرى")
        if payload["kind"] == "wallet_funding" and (
                row["kind"] != "ad_account" or row.get("funding_mode") not in {"prepaid", "hybrid"}
                or payload["direction"] != "outgoing" or payload.get("allocations")):
            fail("operational_wallet_funding_invalid", "تمويل المحفظة حركة صادرة لحساب إعلاني مسبق الدفع")
        if payload["kind"] == "transfer" and (row["kind"] not in {"bank", "cash"} or row["id"] == bank["id"]):
            fail("operational_transfer_invalid", "التحويل يحتاج حسابين مختلفين")
        receipt = None
        if payload.get("receipt_id"):
            receipt = await db[RECEIPTS].find_one({"_id": payload["receipt_id"], "owner_id": owner})
            if not receipt:
                fail("operational_receipt_missing", "الإيصال غير متاح")
            if any(m.get("receipt_id") == receipt["_id"] or m.get("receipt_hash") == receipt["sha256"] for m in state["movements"]):
                fail("operational_receipt_consumed", "سبق استخدام هذا الإيصال في حركة")
        if payload.get("order_number"):
            from operational_balance_sources import order_bank_eligible
            if not receipt:
                fail("operational_order_receipt_required", "أرفق إيصال الحركة المرتبطة بالطلب")
            try:
                await order_bank_eligible(db, owner, payload["order_number"], state["started_at"])
            except ValueError:
                fail("operational_order_ineligible", "الطلب أو حالته أو إثباته غير مؤهل لاحتساب الحركة")
        reference = payload.get("reference", "").strip()
        if reference and any(m.get("reference") == reference and m.get("bank_id") == payload.get("bank_id")
                             for m in state["movements"]):
            fail("operational_reference_duplicate", "مرجع الحركة مستخدم من قبل")
        fee = money(payload.get("actual_fee_amount", "0"), zero=True)
        if fee and (payload["kind"] != "settlement" or row["kind"] != "provider" or not receipt
                    or payload["direction"] != "incoming"):
            fail("operational_actual_fee_evidence_required", "رسوم التسوية تتطلب إيصال تسوية واردة من المنصة")
        requested = payload.get("allocations", [])
        party_available = None
        if row["kind"] not in {"bank", "cash", "employee_custody"}:
            balance = next((r for r in report(state)["parties"] if r["party_type"] == row["kind"]
                            and r["party_id"] == row["id"] and r["currency"] == currency), {})
            field = "outstanding_receivable" if payload["direction"] == "incoming" else "outstanding_payable"
            party_available = Decimal(balance.get(field, "0"))
        if not requested and payload["kind"] in {"payment", "collection", "refund"} and row["kind"] not in {"bank", "cash", "employee_custody"}:
            left, requested = min(amount, party_available) if party_available is not None else amount, []
            candidates = list(state["openings"]) + list(state.get("engine", {}).get("obligations", {}))
            for oid in candidates:
                try:
                    obligation, remaining = outstanding(state, oid)
                except Exception as exc:
                    from fastapi import HTTPException
                    if isinstance(exc, HTTPException):
                        continue
                    raise
                if obligation.get("kind") == "recurring" and Decimal(obligation.get("expected", "0")) > 0:
                    continue
                direction = "incoming" if obligation["direction"] == "receivable" else "outgoing"
                if (obligation["party_id"] == row["id"] and obligation["party_type"] == row["kind"]
                        and obligation["currency"] == currency and direction == payload["direction"] and remaining > 0 and left > 0):
                    take = min(left, remaining)
                    requested.append({"obligation_id": oid, "amount": fmt(take)})
                    left -= take
        allocations, total, seen = [], Decimal(0), set()
        promotions = {}
        for allocation in requested:
            identity = allocation["obligation_id"]
            if identity in seen:
                fail("operational_duplicate_allocation", "التزام مكرر في التسوية")
            seen.add(identity)
            obligation, remaining = outstanding(state, identity, include_expected=True)
            value = money(allocation["amount"])
            expected_direction = "incoming" if obligation["direction"] == "receivable" else "outgoing"
            if (obligation["party_id"] != row["id"] or obligation["party_type"] != row["kind"]
                    or obligation["currency"] != currency or payload["direction"] != expected_direction):
                fail("operational_allocation_scope", "التسوية لا تطابق الجهة أو الاتجاه أو العملة")
            if value > remaining:
                fail("operational_over_settlement", "المبلغ يتجاوز المتبقي")
            if obligation.get("kind") == "recurring":
                confirmed_available = outstanding(state, identity)[1]
                pending = max(value-confirmed_available, Decimal(0))
                if pending:
                    promotions[identity] = pending
            total += value
            allocations.append({"obligation_id": identity, "amount": fmt(value)})
        if total > amount + fee:
            fail("operational_allocation_exceeds_movement", "التخصيص يتجاوز مبلغ الحركة")
        if party_available is not None and total > party_available + sum(promotions.values(), Decimal(0)):
            fail("operational_employee_net_over_settlement" if row["kind"] == "employee" else "operational_party_over_settlement",
                 "التخصيص يتجاوز المتبقي المستحق بعد التصحيحات والمقاصة المعتمدة")
        if payload["kind"] == "settlement" and total != amount + fee:
            fail("operational_settlement_allocation_required", "خصص مبلغ التسوية للالتزامات المستحقة")
        item = {**payload, "id": digest([owner, "movement", payload["request_id"]]),
                "amount": fmt(amount), "actual_fee_amount": fmt(fee), "allocations": allocations, "actor_id": actor,
                "source": source, "occurred_at": stamp, "name": row["name"],
                "bank_kind": bank["kind"] if bank else None, "bank_name": bank["name"] if bank else None,
                "funding_mode": row.get("funding_mode"),
                "receipt_hash": receipt["sha256"] if receipt else None}
        for identity, value in promotions.items():
            obligation = state["engine"]["obligations"][identity]
            before_confirmation = deepcopy(obligation)
            obligation["expected"] = fmt(Decimal(obligation["expected"])-value)
            obligation["confirmed"] = fmt(Decimal(obligation["confirmed"])+value)
            state["engine"].setdefault("facts", {})["recurring_payment:" + item["id"] + ":" + identity] = {
                "obligation_id": identity, "amount": fmt(value), "movement_id": item["id"], "actor_id": actor,
                "occurred_at": stamp, "receipt_id": item.get("receipt_id")}
            audit(state, actor, "recurring_payment_confirmed", before_confirmation, deepcopy(obligation),
                  "تأكيد الجزء المدفوع من الالتزام الدوري", source, stamp)
        state["movements"].append(item)
        audit(state, actor, "movement_saved", None, item, payload.get("note") or "حركة معتمدة", source, stamp)
        remember(state, "movement", payload, item)
        return item
    return await mutate(db, owner, apply)


async def refresh(db, owner, *, clock=None):
    from operational_balance_sources import collect_sources
    from operational_balance_engine import reconcile
    stamp = clock or now()

    async def apply(state):
        if state["status"] != "active":
            return state
        previous_as_of = state.get("engine", {}).get("as_of")
        if previous_as_of and datetime.fromisoformat(stamp.replace("Z", "+00:00")) < datetime.fromisoformat(previous_as_of.replace("Z", "+00:00")):
            return state
        await active_gate(db, owner, state)
        try:
            sources = await collect_sources(db, owner, state["started_at"], stamp, baselines=state.get("source_baselines"))
        except ValueError:
            fail("operational_source_setup_incomplete", "مصادر ميزان 2 غير مكتملة أو تجاوزت حدود القراءة")
        sources.setdefault("supplier_returns", []).extend(state.get("supplier_returns", []))
        result = reconcile(state, sources, stamp)
        from operational_balance_sources import entities
        names = {}
        for kind in {r["party_type"] for r in result["engine"].get("obligations", {}).values()}:
            try:
                names[kind] = {r["id"]: r["name"] for r in await entities(db, owner, kind)}
            except ValueError:
                names[kind] = {}
        for obligation in result["engine"].get("obligations", {}).values():
            kind, identity = obligation["party_type"], obligation["party_id"]
            if kind == "supplier" and not identity:
                obligation["name"] = "تكلفة منتجات غير مسندة"
            else:
                obligation["name"] = names.get(kind, {}).get(identity, "جهة غير مكتملة الإعداد")
                if identity not in names.get(kind, {}):
                    issue = {"code": "operational_obligation_entity_incomplete", "source_id": obligation["id"]}
                    if issue not in result["engine"]["issues"]:
                        result["engine"]["issues"].append(issue)
        state.update(result)
        state["engine"]["as_of"] = stamp
        return state
    return await mutate(db, owner, apply)


async def supplier_return(db, owner, actor, payload, *, clock=None):
    """Owner-documented supplier acceptance, separate from customer returns."""
    stamp = clock or now()
    async def apply(state):
        prior = replay(state, "supplier_return", payload)
        if prior is not None:
            return prior
        await active_gate(db, owner, state)
        receipt_key = "receipt:" + payload["receipt_id"]
        fact = state.get("engine", {}).get("facts", {}).get(receipt_key)
        if not fact:
            fail("operational_supplier_receipt_missing", "اختر استلام مورد مثبتًا")
        evidence = await db[RECEIPTS].find_one({"_id": payload["evidence_receipt_id"], "owner_id": owner})
        if not evidence:
            fail("operational_return_evidence_required", "أرفق إثبات قبول المورد للمرتجع")
        value = money(payload["amount"])
        returns = state.setdefault("supplier_returns", [])
        returned = sum((money(r["amount"]) for r in returns if r["receipt_id"] == payload["receipt_id"]), Decimal(0))
        if returned + value > money(fact["amount"]):
            fail("operational_return_exceeds_receipt", "المرتجع يتجاوز قيمة الاستلام")
        if any(r["evidence_receipt_id"] == payload["evidence_receipt_id"] for r in returns):
            fail("operational_return_evidence_duplicate", "إثبات المرتجع مستخدم من قبل")
        obligation_ids = [o["id"] for o in state["engine"].get("obligations", {}).values()
                          if receipt_key in o.get("evidence_ids", []) and not o.get("derived_credit")]
        if len(obligation_ids) != 1:
            fail("operational_supplier_receipt_obligation_missing", "التزام الاستلام غير مكتمل أو ملتبس")
        item = {**payload, "id": digest([owner, "supplier_return", payload["request_id"]]),
                "amount": fmt(value), "accepted": True, "accepted_at": stamp, "actor_id": actor}
        returns.append(item)
        # Update the same confirmed fact atomically: a second return or payment
        # must see the reduced remainder before a scheduled refresh runs.
        obligation = state["engine"]["obligations"][obligation_ids[0]]
        obligation["confirmed"] = fmt(Decimal(obligation["confirmed"]) - value)
        state["engine"]["facts"]["return:" + item["id"]] = {"receipt_id": receipt_key, "amount": fmt(value)}
        reconcile_credits(state)
        audit(state, actor, "supplier_return_accepted", None, item, payload["note"], "supplier_acceptance", stamp)
        remember(state, "supplier_return", payload, item)
        return item
    return await mutate(db, owner, apply)


def report(state, *, as_of=None):
    state = reconcile_credits(deepcopy(state))
    parties = {}
    def get(kind, identity, currency, name=None):
        key = party_key(kind, identity, currency)
        if key not in parties:
            parties[key] = {"party_type": kind, "party_id": identity, "currency": currency,
                            "name": name or identity, **{f: Decimal(0) for f in (
                "opening", "expected_receivable", "expected_payable", "confirmed_receivable",
                "confirmed_payable", "settled", "actual", "outstanding_receivable", "outstanding_payable",
                "custody_funded", "custody_spent", "custody_returned", "custody_remaining", "custody_adjustments",
                "expense_paid", "owner_withdrawals", "ad_wallet_spent", "ad_wallet_balance", "ad_payable")}}
        return parties[key]
    obligations = deepcopy([row for row in (state.get("engine", {}).get("obligations") or {}).values() if not row.get("superseded")])
    for opening in state["openings"].values():
        row = get(opening["party_type"], opening["party_id"], opening["currency"], opening["name"])
        value = Decimal(opening["amount"])
        signed = value if opening["direction"] == "for_us" else -value
        row["opening"] += signed
        if opening.get("funding_mode"):
            row["funding_mode"] = opening["funding_mode"]
        if opening["party_type"] in {"bank", "cash", "employee_custody"}:
            row["actual"] += signed
        else:
            obligations.append({**opening, "kind": "baseline", "expected": "0.00", "confirmed": opening["amount"],
                                "direction": "receivable" if signed >= 0 else "payable"})
    for obligation in obligations:
        row = get(obligation["party_type"], obligation["party_id"], obligation["currency"], obligation.get("name"))
        if obligation.get("funding_type"):
            row["funding_mode"] = obligation.get("funding_mode", obligation["funding_type"])
        direction = obligation["direction"]
        row["expected_" + direction] += Decimal(obligation.get("expected", "0"))
        row["confirmed_" + direction] += Decimal(obligation.get("confirmed", "0"))
        used = sum((Decimal(a["amount"]) for m in state["movements"] for a in m.get("allocations", [])
                    if a["obligation_id"] == obligation["id"]), Decimal(0))
        remaining = Decimal(obligation.get("confirmed", "0")) - used
        if obligation.get("funding_type") == "prepaid":
            row["ad_wallet_spent"] += remaining
        elif remaining < 0 and "credit:" + obligation["id"] not in state.get("engine", {}).get("obligations", {}):
            opposite = "payable" if direction == "receivable" else "receivable"
            row["outstanding_" + opposite] -= remaining
        elif remaining >= 0:
            row["outstanding_" + direction] += remaining
        row["settled"] += used
        obligation.update(settled=fmt(used), outstanding=fmt(max(remaining, Decimal(0))))
        obligation["available_to_pay"] = fmt(max(remaining + (Decimal(obligation.get("expected", "0")) if obligation.get("kind") == "recurring" else Decimal(0)), Decimal(0)))
        obligation["pending_confirmation"] = obligation.get("kind") == "recurring" and Decimal(obligation.get("expected", "0")) > 0
    for movement in state["movements"]:
        amount = Decimal(movement["amount"])
        signed = amount if movement["direction"] == "incoming" else -amount
        if movement.get("bank_id"):
            row = get(movement["bank_kind"], movement["bank_id"], movement["currency"], movement.get("bank_name"))
            row["actual"] += signed
            if movement["bank_kind"] == "employee_custody":
                row["custody_spent"] += amount
        party = get(movement["party_type"], movement["party_id"], movement["currency"], movement.get("name"))
        if movement.get("funding_mode"):
            party["funding_mode"] = movement["funding_mode"]
        if movement["direction"] == "outgoing" and movement["party_type"] not in {"operating_expense", "owner_withdrawal"}:
            party["expense_paid"] += sum((Decimal(a["amount"]) for a in movement.get("allocations", [])
                if state.get("engine", {}).get("obligations", {}).get(a["obligation_id"], {}).get("kind") == "recurring"), Decimal(0))
        if movement["party_type"] == "employee_custody" and movement["kind"] != "correction":
            party["actual"] -= signed
            party["custody_funded" if signed < 0 else "custody_returned"] += amount
        elif movement["kind"] == "transfer":
            party["actual"] -= signed
        elif movement["kind"] == "correction":
            if movement["party_type"] in {"bank", "cash", "employee_custody"}:
                if not movement.get("bank_id"):
                    party["actual"] += signed
                    if movement["party_type"] == "employee_custody":
                        party["custody_adjustments"] += signed
            else:
                field = "outstanding_receivable" if signed > 0 else "outstanding_payable"
                opposite = "outstanding_payable" if signed > 0 else "outstanding_receivable"
                reduced = min(party[opposite], amount)
                party[opposite] -= reduced
                party[field] += amount - reduced
        elif movement["party_type"] in {"operating_expense", "owner_withdrawal"}:
            party["actual"] += amount
            party["expense_paid" if movement["party_type"] == "operating_expense" else "owner_withdrawals"] += amount
        elif movement["party_type"] not in {"bank", "cash"}:
            party["actual"] -= signed
            unallocated = amount - sum((Decimal(a["amount"]) for a in movement.get("allocations", [])), Decimal(0))
            if unallocated > 0:
                # Unallocated cash remains an explicit advance/overcollection,
                # never disappears or silently marks an invoice paid.
                field = "outstanding_receivable" if signed < 0 else "outstanding_payable"
                party[field] += unallocated
    # Salary accrued against a starting amount owed by an employee offsets that
    # amount first; the gross history remains available in obligations.
    for row in parties.values():
        if row["party_type"] == "employee_custody":
            row["custody_remaining"] = row["actual"]
        if row["party_type"] == "ad_account":
            row["ad_wallet_balance"] = row["outstanding_receivable"] - row["ad_wallet_spent"]
            row["outstanding_receivable"] = max(row["ad_wallet_balance"], Decimal(0))
            row["ad_payable"] = row["outstanding_payable"]
        if row["party_type"] == "employee":
            offset = min(row["outstanding_receivable"], row["outstanding_payable"])
            row["outstanding_receivable"] -= offset
            row["outstanding_payable"] -= offset
    summaries = {}
    for row in parties.values():
        summary = summaries.setdefault(row["currency"], {key: Decimal(0) for key in (
            "actual_liquidity", "receivable", "payable", "expected_receivable", "expected_payable", "confirmed", "settled", "outstanding",
            "custody_remaining", "operating_expenses_paid", "owner_withdrawals")})
        if row["party_type"] in {"bank", "cash"}:
            summary["actual_liquidity"] += row["actual"]
        summary["receivable"] += row["outstanding_receivable"]
        summary["payable"] += row["outstanding_payable"]
        summary["expected_receivable"] += row["expected_receivable"]
        summary["expected_payable"] += row["expected_payable"]
        summary["confirmed"] += row["confirmed_receivable"] + row["confirmed_payable"]
        summary["settled"] += row["settled"]
        summary["outstanding"] += row["outstanding_receivable"] + row["outstanding_payable"]
        summary["custody_remaining"] += row["custody_remaining"]
        summary["operating_expenses_paid"] += row["expense_paid"]
        summary["owner_withdrawals"] += row["owner_withdrawals"]
    summaries = {c: {k: fmt(v) for k, v in s.items()} for c, s in summaries.items()}
    details = {k: v for k, v in state.get("engine", {}).items() if k not in {"audit", "obligations"}}
    details["employee_custody"] = [{"party_id": row["party_id"], "name": row["name"], "currency": row["currency"],
        **{key: fmt(row[key]) for key in ("opening", "custody_funded", "custody_spent", "custody_returned", "custody_remaining", "custody_adjustments")},
        "receipts": [{key: movement.get(key) for key in ("id", "receipt_id", "receipt_hash", "amount", "direction", "source", "actor_id", "occurred_at", "note")}
                     for movement in state["movements"] if movement["currency"] == row["currency"] and (
                         (movement["party_type"] == "employee_custody" and movement["party_id"] == row["party_id"])
                         or (movement.get("bank_kind") == "employee_custody" and movement.get("bank_id") == row["party_id"]))]}
        for row in parties.values() if row["party_type"] == "employee_custody"]
    provider_metrics = deepcopy(details.get("provider_reports", {}))
    settlement_parts = {}
    for movement in state["movements"]:
        gross = Decimal(movement["amount"]) + Decimal(movement.get("actual_fee_amount", "0"))
        cash = Decimal(movement["amount"])
        cumulative, previous_cash = Decimal(0), Decimal(0)
        for allocation in movement.get("allocations", []):
            value = Decimal(allocation["amount"])
            cumulative += value
            assigned_cash = (cash * cumulative / gross).quantize(Decimal(".01"), rounding=ROUND_HALF_UP)
            net = assigned_cash - previous_cash
            previous_cash = assigned_cash
            original = allocation["obligation_id"]
            obligation = state.get("engine", {}).get("obligations", {}).get(original, {})
            is_credit = obligation.get("derived_credit") is True
            if is_credit:
                original = obligation["original_obligation_id"]
            if original not in provider_metrics:
                continue
            metrics = settlement_parts.setdefault(original, {"gross": Decimal(0), "cash": Decimal(0), "fees": Decimal(0)})
            if is_credit and movement["direction"] == "outgoing":
                metrics["cash"] -= net
            elif not is_credit and movement["direction"] == "incoming":
                metrics["gross"] += value
                metrics["cash"] += net
                metrics["fees"] += value - net
    for identity, projection in provider_metrics.items():
        totals = settlement_parts.get(identity, {"gross": Decimal(0), "cash": Decimal(0), "fees": Decimal(0)})
        net = Decimal(projection["net"])
        estimated = Decimal(projection["estimated_fees"])
        covered_fee = ((estimated * min(totals["gross"], net) / net).quantize(Decimal(".01"), rounding=ROUND_HALF_UP)
                       if net else estimated)
        # Covered estimated fees are replaced by evidenced actual fees. The
        # unsettled share remains estimated; neither value creates bank money.
        expected = net - (estimated - covered_fee) - totals["fees"]
        projection.update(estimated_fees_original=projection["estimated_fees"],
                          estimated_fees_remaining=fmt(estimated-covered_fee), actual_fees=fmt(totals["fees"]),
                          expected_receivable=fmt(expected), settled=fmt(totals["cash"]),
                          outstanding=fmt(expected-totals["cash"]))
    details["provider_reports"] = provider_metrics
    issues = deepcopy(state.get("engine", {}).get("issues", []))
    for row in parties.values():
        if row["party_type"] == "ad_account" and row["ad_wallet_balance"] < 0:
            issues.append({"code": "advertising_prepaid_wallet_negative", "source_id": row["party_id"],
                           "currency": row["currency"], "amount": fmt(row["ad_wallet_balance"])})
    return {"status": state["status"], "as_of": as_of or state.get("engine", {}).get("as_of"),
            "started_at": state["started_at"], "summaries": summaries,
            "summary": summaries.get("SAR", {k: "0.00" for k in ("actual_liquidity", "receivable", "payable", "expected_receivable", "expected_payable", "confirmed", "settled", "outstanding", "custody_remaining", "operating_expenses_paid", "owner_withdrawals")}),
            "parties": [{k: fmt(v) if isinstance(v, Decimal) else v for k, v in r.items()} for r in parties.values()],
            "obligations": obligations, "issues": issues,
            "details": details}


async def freeze(db, owner, actor, payload, *, clock=None):
    stamp = clock or now()
    async def apply(state):
        prior = replay(state, "freeze", payload)
        if prior is not None:
            return prior
        if state["status"] != "active":
            fail("operational_not_active", "النظام غير نشط")
        snapshot = report(state)
        state.update(status="frozen", cutoff_at=stamp,
                     snapshot={"report": snapshot, "sha256": digest(snapshot), "created_at": stamp})
        audit(state, actor, "system_frozen", "active", "frozen", payload["reason"], "owner", stamp)
        result = {"status": "frozen", "cutoff_at": stamp, "snapshot_hash": state["snapshot"]["sha256"]}
        remember(state, "freeze", payload, result)
        return result
    return await mutate(db, owner, apply)
