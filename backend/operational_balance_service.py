"""Temporary operational balances: baseline and evidenced money movements.

This module intentionally imports no accounting service. Confirmed obligations
are not bank cash. Bank statements are optional later matching evidence.
"""
import asyncio
import logging
from copy import deepcopy
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from uuid import uuid4
from fastapi import HTTPException

from operational_balance_store import audit, digest, fail, mutate, now, read, remember, replay, RECEIPTS, STATES, claim_movement
from operational_party_projection_state import party_projection_state
from operational_balance_engine import allocation_totals, reconcile_credits
from operational_financial_activity_runtime import prepare_in_thread
from operational_provider_report_coverage import provider_customer_payments
from operational_external_advances import ACTIONS as EXTERNAL_ACTIONS, CREATION as EXTERNAL_CREATION, REPAYMENT as EXTERNAL_REPAYMENT, advance_id, advance_obligations, balance_snapshot
from operational_ad_wallet_pool import enabled as wallet_pool_enabled, project_pool

_LOG = logging.getLogger(__name__)

DEFINITIVE_MOVEMENT_REJECTIONS = {
    "operational_workspace_source_insufficient", "operational_prepaid_source_invalid", "operational_pos_bank_confirmation_required",
    "operational_transfer_reference_required", "operational_pos_insufficient",
    "operational_workspace_owner_return_limit",
    "operational_salary_adjustment_required",
    "operational_future_business_date", "operational_business_date_invalid",
    "operational_supplier_payment_exceeds_payable",
    "operational_ad_debt_priority_required",
    "operational_wallet_movement_unsupported", "operational_wallet_pool_allocation_unsupported",
    "operational_wallet_balance_invalid",
    "operational_ad_fx_invalid", "operational_ad_fx_fields_incomplete",
    "operational_ad_fx_reference_required", "operational_ad_fx_rate_invalid",
    "operational_custody_insufficient", "operational_bank_required",
    "operational_bank_setup_incomplete", "operational_custody_source_invalid",
    "operational_custody_movement_invalid", "operational_expense_direction_invalid",
    "operational_correction_reason_required", "operational_correction_scope",
    "operational_wallet_funding_invalid", "operational_transfer_invalid",
    "operational_receipt_missing", "operational_receipt_consumed",
    "operational_order_receipt_required", "operational_order_ineligible",
    "operational_reference_duplicate", "operational_actual_fee_evidence_required",
    "operational_duplicate_allocation", "operational_allocation_scope",
    "operational_over_settlement", "operational_allocation_exceeds_movement",
    "operational_employee_net_over_settlement", "operational_party_over_settlement",
    "operational_settlement_allocation_required", "operational_wallet_spend_not_payable",
}

KINDS = {"employee", "provider", "courier", "store_driver", "ad_account", "supplier", "pos_receivable",
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


def prepaid_shipping_cash(state, identity):
    return any(rate.get("payment_mode") == "prepaid" and (rate.get("cash_account") or {}).get("id") == identity
               for rate in state.get("courier_tariffs", []))


async def movement_source(db, owner, payload, currency):
    bank = None
    account_type = payload.get("source_account_type", "bank_auto")
    if account_type not in {"bank_auto", "bank", "cash", "employee_custody"}:
        fail("operational_source_account_type_invalid", "نوع مصدر الحركة غير صحيح", 422)
    if payload.get("bank_id"):
        # Exact catalog ID, only bank/cash. No name matching or imported balances.
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
    return bank


async def entity(db, owner, kind, identity, currency):
    from operational_balance_sources import entities
    if kind not in KINDS:
        fail("operational_party_type_invalid", "نوع الجهة غير صحيح", 422)
    try:
        options = {}
        if kind == "ad_account" and identity.startswith("operational-ad:"):
            binding_state = await db[STATES].find_one({"_id": owner, "owner_id": owner},
                                                     {"owner_id": 1, "ad_funding": 1})
            options["operational_state"] = binding_state or {"owner_id": owner}
        rows = [r for r in await entities(db, owner, kind, **options) if r["id"] == identity]
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
            if opening['party_type'] == 'pos_receivable' and opening['direction'] != 'for_us':
                fail('operational_pos_receivable_direction', 'رصيد جهاز الشبكة مبلغ لنا بانتظار التسوية البنكية', 422)
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
                options = {"operational_state": state} if state.get("ad_funding") or state.get("native_purchase_links") else {}
                state["source_baselines"] = await start_baselines(db, owner, stamp, **options)
            except ValueError:
                fail("operational_source_setup_incomplete", "تعذر تثبيت مصادر بداية النظام؛ أكمل إعداد ميزان 2")
            state.update(status="active", started_at=stamp)
            audit(state, actor, "system_started", "draft", "active", "حفظ وإنهاء", "opening", stamp)
        result = {"status": state["status"], "opening_count": len(state["openings"]),
                  "started_at": state["started_at"]}
        remember(state, action, payload, result)
        return result
    return await mutate(db, owner, apply)


def outstanding(state, obligation_id, *, used, advances, include_expected=False):
    obligation = (state.get("engine", {}).get("obligations") or {}).get(obligation_id)
    if not obligation and obligation_id.startswith("external-advance:"):
        obligation = advances.get(obligation_id)
    if not obligation:
        # Baselines can be settled without replaying old orders.
        opening = state["openings"].get(obligation_id)
        if not opening or opening["party_type"] in {"bank", "cash", "employee_custody"}:
            fail("operational_obligation_missing", "الالتزام غير متاح")
        obligation = {**opening, "confirmed": opening["amount"],
                      "direction": "payable" if opening["direction"] == "for_party" else "receivable"}
    if obligation.get("funding_type") == "prepaid":
        fail("operational_wallet_spend_not_payable", "الصرف يستهلك المحفظة المدفوعة مسبقًا ولا يُسدّد مرة أخرى")
    pending = Decimal(obligation.get("expected", "0")) if include_expected and obligation.get("kind") == "recurring" else Decimal(0)
    return obligation, max(Decimal(obligation["confirmed"]) + pending - used.get(obligation_id, Decimal(0)), Decimal(0))


async def create_movement(db, owner, actor, payload, *, source="mezan2", clock=None, on_account=False):
    stamp = clock or now()
    operation_id = await claim_movement(db, owner, actor, payload)
    replay_action = "movement:" + actor

    async def validate_and_apply(state):
        before_report = None

        async def current_report():
            nonlocal before_report
            if before_report is None:
                if account_entry and row["kind"] not in {"bank", "cash", "employee_custody"}:
                    snapshot = await prepare_in_thread(party_projection_state, state, row["kind"], row["id"])
                else:
                    snapshot = {**state, "audit": [], "requests": {}, "engine": {**state.get("engine", {}), "audit": []}}
                before_report = await prepare_in_thread(report, snapshot)
            return before_report

        historical = next((m for m in state["movements"] if m.get("actor_id") == actor
                           and m.get("request_id") == payload["request_id"]), None)
        if historical is not None:
            # Stored payload amount is normalized; the original request hash is
            # authoritative when present, including pre-scope WIP requests.
            prior_request = replay(state, replay_action, payload)
            if prior_request is None:
                old_payload = {k: v for k, v in payload.items() if k != "expected_session_scope"}
                prior_request = replay(state, "movement", old_payload)
                if prior_request is None:
                    fail("operational_request_conflict", "تعذر التحقق من هوية الحركة السابقة")
            return prior_request
        prior = replay(state, replay_action, payload)
        if prior is not None:
            return prior
        await active_gate(db, owner, state)
        if payload.get("business_date") is not None:
            try:
                business_day = date.fromisoformat(payload["business_date"])
                if business_day.isoformat() != payload["business_date"]:
                    raise ValueError()
            except (TypeError, ValueError):
                fail("operational_business_date_invalid", "اختر تاريخًا صحيحًا للحركة", 422)
            today = datetime.fromisoformat(stamp.replace("Z", "+00:00")).astimezone(ZoneInfo("Asia/Riyadh")).date()
            if business_day > today:
                fail("operational_future_business_date", "لا يمكن تسجيل حركة بتاريخ مستقبلي", 422)
        amount = money(payload["amount"])
        currency = payload["currency"]
        row = await entity(db, owner, payload["party_type"], payload["party_id"], currency)
        external_operation = payload.get("external_operation")
        if external_operation is not None:
            if row["kind"] != "external_person" or currency != "SAR" or external_operation not in EXTERNAL_ACTIONS:
                fail("operational_external_operation_invalid", "اختر عملية صحيحة لجهة خارجية بالريال السعودي", 422)
            direction, kind, _ = EXTERNAL_ACTIONS[external_operation]
            if payload["direction"] != direction or payload["kind"] != kind:
                fail("operational_external_operation_direction", "اتجاه الحركة ونوعها لا يطابقان عملية السلفة أو السداد", 422)
            if external_operation in EXTERNAL_CREATION and payload.get("allocations"):
                fail("operational_external_creation_allocation", "إنشاء سلفة مستقلة لا يسدد دينًا سابقًا تلقائيًا", 422)
        # Cross-currency advertising requires two independently evidenced legs:
        # credited USD advertising amount, and total debited SAR including bank fee.
        fx_fields = ("bank_currency", "bank_amount", "bank_fee_amount")
        has_fx = any(payload.get(field) is not None for field in fx_fields)
        bank_gross = bank_fee = bank_net = fx_rate = None
        if has_fx:
            if any(payload.get(field) is None for field in fx_fields):
                fail("operational_ad_fx_fields_incomplete", "أدخل قيمة الإيداع بالدولار، وخصم البنك بالريال، والعمولة بالريال", 422)
            if (row["kind"] != "ad_account" or currency != "USD"
                    or payload["bank_currency"] != "SAR" or payload.get("source_account_type") != "bank"
                    or payload["direction"] != "outgoing"
                    or payload["kind"] not in {"payment", "wallet_funding"}):
                fail("operational_ad_fx_invalid", "تحويل الدولار متاح للدفع الإعلاني من بنك بالريال فقط", 422)
            bank_gross = money(payload["bank_amount"])
            bank_fee = money(payload["bank_fee_amount"], zero=True)
            if bank_fee >= bank_gross:
                fail("operational_ad_fx_invalid", "العمولة البنكية يجب أن تكون أقل من المبلغ المخصوم من البنك", 422)
            bank_net = bank_gross - bank_fee
            # This is an evidenced effective rate, not the daily advertising-spend FX rate.
            fx_rate = (bank_net / amount).quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP)
            if fx_rate < Decimal("2") or fx_rate > Decimal("6"):
                fail("operational_ad_fx_rate_invalid", "تأكد من المبلغ بالدولار والريال؛ سعر التحويل المحسوب غير معقول", 422)
            if len(str(payload.get("reference") or "").strip()) < 3:
                fail("operational_ad_fx_reference_required", "أدخل مرجع عملية البنك لتوثيق الخصم ومنع التكرار", 422)
        wallet_pool = row["kind"] == "ad_account" and wallet_pool_enabled(state, row["id"], currency)
        if wallet_pool:
            if payload["direction"] != "outgoing" or payload["kind"] not in {"payment", "wallet_funding"}:
                fail("operational_wallet_movement_unsupported", "حساب المحفظة يقبل دفعة صادرة تسدد الدين أولًا ثم تشحن الرصيد؛ التصحيح والاسترداد يحتاجان تسوية موثقة مستقلة")
            if payload.get("allocations"):
                fail("operational_wallet_pool_allocation_unsupported", "توزع دفعة المحفظة تلقائيًا على الدين ثم الرصيد؛ اترك تخصيص الالتزامات فارغًا")
        if row['kind'] == 'employee' and any(
                key.startswith('salary:' + row['id'] + ':')
                for key in state.get('engine', {}).get('salary_conflicts', {})):
            fail('operational_salary_adjustment_required', 'تغير مصدر استحقاق محفوظ؛ يلزم حل فرق الراتب بتسوية موثقة قبل تسجيل حركة جديدة')
        bank = await movement_source(db, owner, payload, "SAR" if has_fx else currency)
        account_entry = (on_account or bool(payload.get("workspace_intent_hash"))) and not payload.get("allocations")
        cash_balance = None
        if account_entry and bank and payload["direction"] == "outgoing" and (
                payload.get("workspace_operation") or bank["kind"] == "employee_custody"):
            from operational_financial_activity import cash_positions
            source_key = (bank["kind"], bank["id"], "SAR" if has_fx else currency)
            cash = await prepare_in_thread(cash_positions, state, {source_key})
            cash_balance = cash.get(source_key, {}).get("actual", Decimal(0))

        if bank and bank["kind"] == "employee_custody":
            custody_targets = {"operating_expense", "supplier"}
            if payload.get("workspace_operation") == "external.pay":
                custody_targets.add("external_person")
            if row["kind"] not in custody_targets or payload["direction"] != "outgoing" or payload["kind"] != "payment":
                fail("operational_custody_source_invalid", "العهدة تسمح بصرف تشغيلي أو سداد مورد فقط")
            if cash_balance is None:
                balance = next((r for r in (await current_report())["parties"] if r["party_type"] == "employee_custody"
                                and r["party_id"] == bank["id"] and r["currency"] == currency), {})
                cash_balance = Decimal(balance.get("custody_remaining", "0"))
            if amount > cash_balance:
                fail("operational_custody_insufficient", "رصيد العهدة لا يكفي. قلّل المبلغ أو اختر مصدرًا آخر.")
        if row["kind"] == "employee_custody" and payload["kind"] != "correction":
            valid = ((payload["kind"] == "payment" and payload["direction"] == "outgoing")
                     or (payload["kind"] == "collection" and payload["direction"] == "incoming"))
            if not valid or not bank or bank["kind"] not in {"bank", "cash"} or payload.get("allocations"):
                fail("operational_custody_movement_invalid", "تمويل العهدة صادر من بنك أو صندوق وإرجاعها وارد إليه")
            if payload["direction"] == "incoming":
                balance = next((r for r in (await current_report())["parties"] if r["party_type"] == "employee_custody"
                                and r["party_id"] == row["id"] and r["currency"] == currency), {})
                if amount > Decimal(balance.get("custody_remaining", "0")):
                    fail("operational_custody_insufficient", "مبلغ الإرجاع يتجاوز المتبقي في العهدة")
        if row["kind"] in {"operating_expense", "owner_withdrawal"} and payload["kind"] != "correction":
            owner_return = (row["kind"] == "owner_withdrawal" and payload.get("workspace_operation") == "owner.return"
                            and payload["direction"] == "incoming" and payload["kind"] == "collection")
            if not owner_return and (payload["direction"] != "outgoing" or payload["kind"] not in {"payment", "settlement"}):
                fail("operational_expense_direction_invalid", "المصروف أو السحب حركة صادرة")
            if owner_return:
                owner_balance = next((r for r in (await current_report())["parties"]
                    if r["party_type"] == "owner_withdrawal" and r["party_id"] == row["id"] and r["currency"] == currency), {})
                if amount > Decimal(owner_balance.get("owner_withdrawals", "0")):
                    fail("operational_workspace_owner_return_limit", "المبلغ يتجاوز السحوبات القابلة للإعادة", 409)
        if payload["kind"] != "correction" and bank is None:
            fail("operational_bank_required", "حدد البنك أو الصندوق الذي تحرك منه أو إليه المبلغ")
        if payload.get("workspace_operation") and bank and bank["kind"] in {"bank", "cash"} and payload["direction"] == "outgoing":
            if bank["kind"] == "cash" and payload["kind"] == "payment" and prepaid_shipping_cash(state, bank["id"]):
                fail("operational_prepaid_source_invalid", "هذا الرصيد مخصص للشحن. اختر صندوقًا نقديًا أو بنكًا.", 409)
            if cash_balance is None:
                available_source = next((r for r in (await current_report())["parties"]
                    if r["party_type"] == bank["kind"] and r["party_id"] == bank["id"]
                    and r["currency"] == ("SAR" if has_fx else currency)), {})
                cash_balance = Decimal(available_source.get("actual", "0"))
            if (bank_gross if has_fx else amount) > cash_balance:
                fail("operational_workspace_source_insufficient", "رصيد مصدر الدفع لا يكفي. قلّل المبلغ أو اختر مصدرًا آخر.", 409)
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

            try:
                await order_bank_eligible(db, owner, payload["order_number"], state["started_at"], reject_automatic=payload["direction"] == "incoming")
            except ValueError:
                fail("operational_order_ineligible", "الطلب أو حالته أو إثباته غير مؤهل لاحتساب الحركة")
        reference = payload.get("reference", "").strip()
        paypal_payout = row["kind"] == "provider" and row["id"] == "paypal" and payload["kind"] != "correction"
        if paypal_payout and (not bank or bank["kind"] != "bank" or currency != "SAR"
                or payload["kind"] != "collection" or payload["direction"] != "incoming"
                or not payload.get("business_date") or not reference or payload.get("bank_arrival_confirmed") is not True
                or payload.get("allocations") or money(payload.get("actual_fee_amount", "0"), zero=True)):
            fail("operational_paypal_bank_confirmation_required", "حدد البنك وأكّد صافي الحوالة وتاريخ وصولها ومرجعها", 422)
        pos_settlement = row["kind"] == "pos_receivable" and payload["kind"] != "correction"
        if pos_settlement and (not bank or bank["kind"] != "bank" or payload["direction"] != "incoming"
                or payload["kind"] not in {"collection", "settlement"} or not payload.get("business_date")
                or payload.get("bank_arrival_confirmed") is not True or not reference):
            fail("operational_pos_bank_confirmation_required", "أكد مطابقة وصول الحوالة إلى البنك وأدخل مرجعها وتاريخها", 422)
        if (pos_settlement or paypal_payout) and date.fromisoformat(payload["business_date"]) < datetime.fromisoformat(
                state["started_at"].replace("Z", "+00:00")).astimezone(ZoneInfo("Asia/Riyadh")).date():
            fail("operational_business_date_invalid", "تاريخ وصول الحوالة يسبق بداية الأرصدة التشغيلية", 422)
        if payload["kind"] == "transfer" and bank and bank["kind"] == "bank" and row["kind"] == "cash" and not reference:
            if prepaid_shipping_cash(state, row["id"]):
                fail("operational_transfer_reference_required", "أدخل مرجع التحويل إلى صندوق الشحن", 422)
        if reference and any(r.get("status") in {"refunded", "salla_refund_confirmed"} and r.get("refund_source_type") == "bank"
                             and r.get("refund_source_id") == payload.get("bank_id") and r.get("refund_reference") == reference
                             for r in state.get("customer_returns", [])):
            fail("operational_reference_duplicate", "مرجع الاسترداد مسجل بالفعل")
        if reference and any(m.get("reference") == reference and m.get("bank_id") == payload.get("bank_id")
                             for m in state["movements"]):
            fail("operational_reference_duplicate", "مرجع الحركة مستخدم من قبل")
        fee = money(payload.get("actual_fee_amount", "0"), zero=True)
        if pos_settlement and fee and not (payload.get("fee_reference") or "").strip():
            fail("operational_actual_fee_evidence_required", "أدخل مرجع مستند رسوم الشبكة الفعلية", 422)
        if fee and not pos_settlement and (payload["kind"] != "settlement" or row["kind"] != "provider"
                    or payload["direction"] != "incoming"):
            fail("operational_actual_fee_evidence_required", "رسوم التسوية تخص تسوية واردة من المنصة")
        requested = payload.get("allocations", [])
        party_available = None
        if paypal_payout:
            party_available = paypal_available(await prepare_in_thread(report, state, paypal_sar=True))
            if party_available is None or amount > party_available:
                fail("operational_paypal_insufficient", "تعذر مطابقة مبلغ الحوالة مع صافي رصيد باي بال المتاح", 409)
        elif wallet_pool:
            try:
                party_available = (await prepare_in_thread(project_pool, state, row["id"], currency))["payable"]
            except ValueError:
                fail("operational_wallet_balance_invalid", "تعذر اعتماد رصيد المحفظة؛ راجع مبالغ الحساب وأدلته")
        elif (payload["kind"] != "correction" and row["kind"] not in {"bank", "cash", "employee_custody"}
              and (not account_entry or pos_settlement
                   or (row["kind"] == "supplier" and payload["direction"] == "outgoing"))):
            balance = next((r for r in (await current_report())["parties"] if r["party_type"] == row["kind"]
                            and r["party_id"] == row["id"] and r["currency"] == currency), {})
            field = "outstanding_receivable" if payload["direction"] == "incoming" else "outstanding_payable"
            party_available = Decimal(balance.get(field, "0"))
        if pos_settlement and amount + fee > party_available:
            fail("operational_pos_insufficient", "صافي التحويل والرسوم يتجاوزان المتبقي المثبت لدى الشبكة")
        if (row["kind"] == "supplier" and payload["direction"] == "outgoing"
                and payload["kind"] in {"payment", "settlement"}
                and amount > max(party_available - Decimal(balance.get("outstanding_receivable", "0")), Decimal(0))):
            fail("operational_supplier_payment_exceeds_payable", "لا يمكن سداد المورد دون مستحق، أو بأكثر من المبلغ المستحق")
        used = {}
        advances = advance_obligations(state) if (external_operation in EXTERNAL_REPAYMENT or
            any(a["obligation_id"].startswith("external-advance:") for a in requested)) else {}
        if (not account_entry and not wallet_pool and not paypal_payout and not requested and external_operation not in EXTERNAL_CREATION
                and (payload["kind"] in {"payment", "collection", "refund"} or pos_settlement or external_operation in EXTERNAL_REPAYMENT)
                and row["kind"] not in {"bank", "cash", "employee_custody"}):
            left, requested = min(amount, party_available) if party_available is not None else amount, []
            if pos_settlement:
                left = amount + fee
            candidates = list(state["openings"]) + list(state.get("engine", {}).get("obligations", {}))
            if external_operation in EXTERNAL_REPAYMENT:
                candidates += list(advances)
            if candidates:
                used = allocation_totals(state)
                reconcile_credits(state, used=used)
            for oid in candidates:
                if left <= 0:
                    break
                try:
                    obligation, remaining = outstanding(state, oid, used=used, advances=advances)
                except Exception as exc:
                    from fastapi import HTTPException
                    if isinstance(exc, HTTPException):
                        continue
                    raise
                if obligation.get("kind") == "recurring" and Decimal(obligation.get("expected", "0")) > 0:
                    continue
                direction = "incoming" if obligation["direction"] == "receivable" else "outgoing"
                if (obligation["party_id"] == row["id"] and obligation["party_type"] == row["kind"]
                        and obligation["currency"] == currency and direction == payload["direction"] and remaining > 0):
                    take = min(left, remaining)
                    requested.append({"obligation_id": oid, "amount": fmt(take)})
                    left -= take
        elif requested:
            used = allocation_totals(state)
            reconcile_credits(state, used=used)
        allocations, total, seen = [], Decimal(0), set()
        promotions = {}
        for allocation in requested:
            identity = allocation["obligation_id"]
            if identity in seen:
                fail("operational_duplicate_allocation", "التزام مكرر في التسوية")
            seen.add(identity)
            obligation, remaining = outstanding(state, identity, used=used, advances=advances, include_expected=True)
            value = money(allocation["amount"])
            expected_direction = "incoming" if obligation["direction"] == "receivable" else "outgoing"
            if (obligation["party_id"] != row["id"] or obligation["party_type"] != row["kind"]
                    or obligation["currency"] != currency or payload["direction"] != expected_direction):
                fail("operational_allocation_scope", "التسوية لا تطابق الجهة أو الاتجاه أو العملة")
            if value > remaining:
                fail("operational_over_settlement", "المبلغ يتجاوز المتبقي")
            if obligation.get("kind") == "recurring":
                confirmed_available = outstanding(state, identity, used=used, advances=advances)[1]
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
        if not account_entry and (payload["kind"] == "settlement" or pos_settlement) and total != amount + fee:
            fail("operational_settlement_allocation_required", "خصص مبلغ التسوية للالتزامات المستحقة")
        if (not account_entry and not wallet_pool and row["kind"] == "ad_account" and row.get("funding_mode") == "hybrid"
                and payload["direction"] == "outgoing" and payload["kind"] == "payment"
                and total != min(amount, party_available)):
            fail("operational_ad_debt_priority_required",
                 "الدفعة تسدد المستحق الإعلاني أولًا، ثم يضاف الزائد إلى المحفظة. أكمل تخصيص المستحق أو اترك التخصيص تلقائيًا.")
        recorded_actor = await db["users"].find_one({"id": actor}, {"_id": 0, "id": 1, "name": 1})
        item = {**payload, "id": operation_id,
                "amount": fmt(amount), "actual_fee_amount": fmt(fee), "allocations": allocations, "actor_id": actor,
                "recorded_by": {"id": actor, "name": str(recorded_actor.get("name") or "غير موثق")} if recorded_actor else None,
                "source": source, "occurred_at": stamp, "name": row["name"],
                "bank_kind": bank["kind"] if bank else None, "bank_name": bank["name"] if bank else None,
                "funding_mode": row.get("funding_mode"),
                "receipt_hash": receipt["sha256"] if receipt else None}
        if (account_entry and not wallet_pool and not paypal_payout and payload["kind"] in {"payment", "collection", "refund"}
                and row["kind"] not in {"bank", "cash", "employee_custody", "operating_expense", "owner_withdrawal"}):
            item["settlement_scope"] = "account"
        if has_fx:
            item.update(bank_currency="SAR", bank_amount=fmt(bank_gross),
                        bank_fee_amount=fmt(bank_fee), bank_net_amount=fmt(bank_net),
                        effective_fx_rate_to_sar=f"{fx_rate:.6f}")
        if wallet_pool:
            item["funding_policy"] = "wallet_first"
            item["wallet_first_allocation"] = {
                "debt_settled": fmt(min(amount, party_available)),
                "wallet_funded": fmt(max(amount-party_available, Decimal(0))),
                "basis": "confirmed_balance_at_recording",
            }
            try:
                project_pool({**state, "movements": [*state["movements"], item]}, row["id"], currency)
            except ValueError:
                fail("operational_wallet_balance_invalid", "تعذر اعتماد رصيد المحفظة؛ راجع مبالغ الحساب وأدلته")
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
        if external_operation:
            before_balances = balance_snapshot(report(state), party_id=row["id"], currency=currency,
                                               bank_id=bank["id"], bank_kind=bank["kind"])
        state["movements"].append(item)
        if external_operation:
            item["external_balance_snapshot"] = {
                "operation": external_operation, "currency": currency,
                "account_id": bank["id"], "account_kind": bank["kind"],
                "account_label": "الاستلام في" if payload["direction"] == "incoming" else "الدفع من",
                "before": before_balances,
                "after": balance_snapshot(report(state), party_id=row["id"], currency=currency,
                                          bank_id=bank["id"], bank_kind=bank["kind"]),
            }
        await prepare_in_thread(audit, state, actor, "movement_saved", None, item,
                                payload.get("note") or "حركة معتمدة", source, stamp)
        remember(state, replay_action, payload, item)
        return item
    async def apply(state):
        try:
            return await validate_and_apply(state)
        except HTTPException as exc:
            if not isinstance(exc.detail, dict) or exc.detail.get("code") not in DEFINITIVE_MOVEMENT_REJECTIONS:
                raise
            # A terminal rejection is a CAS decision, just like acceptance.
            # Concurrent copies must replay it even if funds arrive later.
            rejection = {"_operational_rejected": True, "status": exc.status_code,
                         "detail": {**exc.detail, "not_applied": True}}
            remember(state, replay_action, payload, rejection)
            return rejection
    # A committed receipt and its posting share one revision CAS. A claim alone
    # is never a confirmation. Older receipts still take the full replay path.
    from operational_balance_store import check_authorization, request_key
    key = request_key(replay_action, payload["request_id"])
    stored = await db[STATES].find_one({"_id": owner, "owner_id": owner, f"requests.{key}": {"$exists": True}},
                                       {"_id": 0, f"requests.{key}": 1})
    result = replay(stored, replay_action, payload) if stored else None
    if result is None:
        result = await mutate(db, owner, apply, threaded_preparation=True)
    else:
        await check_authorization()
    if result.get("_operational_rejected"):
        raise HTTPException(result["status"], detail=result["detail"])
    return result


def _bank_credit_actions(orders, recorded):
    """Prepare bank claims without I/O or changes to recorded money.

    Positions preserve the original OR match: a movement found by both keys is
    one hit, while two equal entries remain a conflict. Prospective new rows
    are indexed immediately so later candidates see the same batch history.
    """
    from operational_demand_refresh import calculation_checkpoint

    candidates = []
    for order in orders:
        calculation_checkpoint()
        credit = order.get("bank_credit")
        if credit:
            candidates.append((order, credit))
    if not candidates:
        return []

    by_request, by_order = {}, {}
    rows = list(recorded)

    def add_position(lookup, key, position):
        # A legacy list/dict cannot match a normalized candidate identity.
        # Ignore only that key; the same row's other identity may still match.
        try:
            hash(key)
        except TypeError:
            return
        lookup.setdefault(key, set()).add(position)

    def index(movement, position):
        add_position(by_request, movement.get("request_id"), position)
        if movement.get("direction") == "incoming":
            add_position(by_order, movement.get("order_number"), position)

    for position, movement in enumerate(rows):
        calculation_checkpoint()
        index(movement, position)

    actions = []
    for order, credit in candidates:
        calculation_checkpoint()
        identity = "order-bank:" + str(order["id"])
        hits = by_request.get(identity, set()) | by_order.get(order["order_number"], set())
        if hits:
            existing = rows[next(iter(hits))]
            if (len(hits) != 1 or existing.get("bank_id") != credit["bank_id"]
                    or Decimal(existing["amount"]) != Decimal(credit["amount"])):
                actions.append(("conflict", order, None))
            continue
        payload = {"request_id": identity, "order_number": order["order_number"], "order_id": order["id"],
                   "bank_id": credit["bank_id"], "amount": fmt(credit["amount"]), "currency": order["currency"]}
        actions.append(("claim", order, payload))
        prospective = {**payload, "direction": "incoming"}
        index(prospective, len(rows))
        rows.append(prospective)
    return actions


async def _reconcile_with_profit_snapshot(db, owner, state, stamp):
    """Join independent visible-request preparations before any financial save.

    Profit reads depend only on the owner and observation period. They do not
    read collected sources or the prepared ledger. Wait for the primary source
    batch to finish before sharing read capacity with the remaining collection
    and reconciliation. Both retain the same request deadline and must finish
    cleanup before the caller can admit any financial write.
    """
    from operational_balance_engine import reconcile
    from operational_balance_sources import collect_sources
    from operational_demand_refresh import checkpoint, demand_context, refresh_stage, run_calculation
    from operational_financial_activity_runtime import join_task
    from operational_profit_sources import load_profit_snapshot
    from operational_read_runtime import _snapshot_session

    async def calculate(on_primary_reads_complete=None):
        try:
            from operational_balance_inventory import assert_no_source_collision
            with refresh_stage("source_collection"):
                with refresh_stage("source_collision"):
                    await assert_no_source_collision(db, owner, state)
                readiness = ({"on_primary_reads_complete": on_primary_reads_complete}
                             if on_primary_reads_complete is not None else {})
                sources = await collect_sources(db, owner, state["started_at"], stamp,
                                                baselines=state.get("source_baselines"),
                                                operational_state=state, **readiness)
            await checkpoint()
        except ValueError as exc:
            from operational_read_runtime import SourceReadError
            if isinstance(exc, SourceReadError):
                from operational_source_diagnostics import source_failure_detail
                detail = source_failure_detail(exc.code, exc.collection)
                _LOG.warning("operational_refresh_source_failure stage=source_collection outcome=failed "
                             "cause=%s source_collection=%s", detail["cause"], detail["source_collection"])
                raise HTTPException(409, detail=detail) from None
            fail("operational_source_setup_incomplete", "مصادر ميزان 2 غير مكتملة أو تجاوزت حدود القراءة")
        sources.setdefault("supplier_returns", []).extend(state.get("supplier_returns", []))
        from operational_customer_returns import attach_returns
        attach_returns(state, sources)
        with refresh_stage("reconciliation"):
            result = await run_calculation(reconcile, state, sources, stamp)
        return result, sources

    async def profit(period, primary_ready=None):
        if primary_ready is not None:
            await primary_ready.wait()
        with refresh_stage("profit_snapshot"):
            return await load_profit_snapshot(db, owner, period, as_of=stamp)

    # Ordinary callers do not own cancellable CPU threads. Snapshot-session
    # readers also retain their serial contract, including authorization reads.
    if demand_context() is None or _snapshot_session(db):
        result, sources = await calculate()
        await checkpoint()
        result["engine"]["profit_snapshot"] = await profit(result)
        return result, sources

    period = {"owner_id": state["owner_id"], "started_at": state["started_at"]}
    primary_ready = asyncio.Event()
    tasks = [asyncio.create_task(calculate(primary_ready.set)),
             asyncio.create_task(profit(period, primary_ready))]
    group = asyncio.gather(*tasks)
    try:
        (result, sources), snapshot = await asyncio.shield(group)
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
        # A second cancellation must not detach a source cursor or CPU thread.
        # Cleanup exceptions cannot replace the first preparation outcome.
        await join_task(asyncio.gather(group, *tasks, return_exceptions=True))
    result["engine"]["profit_snapshot"] = snapshot
    return result, sources


def _apply_refreshed_party_names(result, names):
    """Keep exact diagnostics while avoiding a whole-list scan per obligation."""
    from operational_demand_refresh import calculation_checkpoint
    from operational_issue_index import append_unique_issue, enable_issue_index

    issue_scope = None
    for obligation in result["engine"].get("obligations", {}).values():
        calculation_checkpoint()
        kind, identity = obligation["party_type"], obligation["party_id"]
        if kind == "supplier" and not identity:
            obligation["name"] = "تكلفة منتجات لم تُستلم من المورد"
        else:
            obligation["name"] = names.get(kind, {}).get(identity, "جهة غير مكتملة الإعداد")
            if identity not in names.get(kind, {}):
                if issue_scope is None:
                    issue_scope = {"issues": result["engine"]["issues"]}
                    enable_issue_index(issue_scope)
                append_unique_issue(issue_scope, {
                    "code": "operational_obligation_entity_incomplete", "source_id": obligation["id"]})


async def refresh(db, owner, *, clock=None):
    from operational_demand_refresh import calculation_checkpoint, checkpoint, refresh_stage, run_calculation
    stamp = clock or now()

    async def apply(state):
        if state["status"] != "active":
            return state
        previous_as_of = state.get("engine", {}).get("as_of")
        if previous_as_of and datetime.fromisoformat(stamp.replace("Z", "+00:00")) < datetime.fromisoformat(previous_as_of.replace("Z", "+00:00")):
            return state
        await active_gate(db, owner, state)
        await checkpoint()
        result, sources = await _reconcile_with_profit_snapshot(db, owner, state, stamp)
        await checkpoint()
        with refresh_stage("bank_claims"):
            bank_actions = await run_calculation(_bank_credit_actions, sources.get("orders", []), result["movements"])
            for action, order, payload in bank_actions:
                calculation_checkpoint()
                if action == "conflict":
                    result["engine"]["issues"].append({"code": "operational_order_bank_credit_conflict", "source_id": order["id"]})
                    continue
                # Preparation cannot authorize a financial intent. Every new
                # claim retains fresh request/lease admission and store arbitration.
                await checkpoint()
                credit = order["bank_credit"]
                operation = await claim_movement(db, owner, 'operational_order_worker', payload)
                item = {**payload, 'id': operation, 'party_type': 'bank', 'party_id': credit['bank_id'],
                        'bank_kind': 'bank', 'bank_name': credit['bank_name'], 'name': credit['bank_name'],
                        'direction': 'incoming', 'kind': 'collection', 'allocations': [], 'actual_fee_amount': '0.00',
                        'actor_id': 'operational_order_worker', 'source': 'mezan2', 'occurred_at': stamp,
                        'receipt_id': None, 'reference': 'طلب ' + order['order_number'], 'automatic_order_bank': True}
                result['movements'].append(item)
                audit(result, 'operational_order_worker', 'order_bank_credited', None, item,
                      'احتساب تحويل بنكي من طلب جديد مؤهل', 'mz2', stamp)
        from operational_balance_sources import entities
        names = {}
        with refresh_stage("party_catalogs"):
            for kind in {r["party_type"] for r in result["engine"].get("obligations", {}).values()}:
                await checkpoint()
                try:
                    options = {"operational_state": state} if kind == "ad_account" and state.get("ad_funding") else {}
                    names[kind] = {r["id"]: r["name"] for r in await entities(db, owner, kind, **options)}
                except ValueError:
                    names[kind] = {}
            await run_calculation(_apply_refreshed_party_names, result, names)
        state.update(result)
        state["engine"]["as_of"] = stamp
        await checkpoint()
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


def paypal_available(computed):
    groups = computed["details"]["provider_balances"]
    if any(row["provider"] == "paypal" for unit, rows in groups.items() if unit != "SAR" for row in rows):
        return None
    summary = next((r for r in groups.get("SAR", []) if r["provider"] == "paypal"), None)
    if summary is None:
        coverage = computed["details"].get("customer_tender_coverage", {})
        return Decimal(0) if coverage.get("paypal_provider_recognized") and not coverage.get("uninspected_source_orders") else None
    return Decimal(summary["net"]) if summary.get("complete") and summary.get("net") is not None else None


def report(state, *, as_of=None, provider_balances_sar=False, ad_balances_sar=False, provider_balances_net=False, paypal_sar=False, workspace_provider=None):
    from operational_financial_activity_runtime import preparation_checkpoint
    preparation_checkpoint()
    state = reconcile_credits(deepcopy(state))
    from operational_provider_balance_sar import convert_claim, convert_projection, unavailable, usable_bases
    valuations = usable_bases(state) if provider_balances_sar or paypal_sar else {}
    if paypal_sar and not provider_balances_sar:
        valuations = {key: value for key, value in valuations.items() if value["provider"] == "paypal"}
    valuation_issues = []
    allocated = {}
    for movement in state["movements"]:
        preparation_checkpoint()
        for entry in movement.get("allocations", []):
            preparation_checkpoint()
            key = entry["obligation_id"]
            allocated[key] = allocated.get(key, Decimal(0)) + Decimal(entry["amount"])
    parties = {}
    def get(kind, identity, currency, name=None):
        key = party_key(kind, identity, currency)
        if key not in parties:
            parties[key] = {"party_type": kind, "party_id": identity, "currency": currency,
                            "name": name or identity, **{f: Decimal(0) for f in (
                "opening", "expected_receivable", "expected_payable", "confirmed_receivable",
                "confirmed_payable", "settled", "actual", "outstanding_receivable", "outstanding_payable",
                "custody_funded", "custody_spent", "custody_returned", "custody_remaining", "custody_adjustments",
                "expense_paid", "owner_withdrawals", "ad_wallet_spent", "ad_wallet_balance", "ad_payable",
                "customer_refunds_manual_debited")}}
        return parties[key]
    obligations = deepcopy([row for row in (state.get("engine", {}).get("obligations") or {}).values() if not row.get("superseded")])
    for opening in state["openings"].values():
        preparation_checkpoint()
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
        preparation_checkpoint()
        used = allocated.get(obligation["id"], Decimal(0))
        if ((provider_balances_sar or paypal_sar and obligation["party_id"] == "paypal")
                and obligation["party_type"] == "provider" and obligation["currency"] != "SAR"):
            original = obligation.get("original_obligation_id", obligation["id"])
            basis = valuations.get(original, {})
            if basis and (basis["provider"], basis["currency"]) == (obligation["party_id"], obligation["currency"]):
                source_claim = state["engine"]["obligations"][original]
                used = convert_claim(obligation, used, basis, source_claim["confirmed"], allocated.get(original, Decimal(0)))
            else:
                unavailable(obligation, valuation_issues)
        row = get(obligation["party_type"], obligation["party_id"], obligation["currency"], obligation.get("name"))
        if obligation["party_type"] == "ad_account" and wallet_pool_enabled(state, obligation["party_id"], obligation["currency"]):
            # These rows retain the source amount and day, but payment belongs
            # to the revolving account balance, never to a fabricated day split.
            obligation.update(settled="0.00", outstanding="0.00", available_to_pay="0.00",
                              pending_confirmation=False, balance_role="wallet_pool_evidence")
            continue
        if obligation.get("funding_type"):
            row["funding_mode"] = obligation.get("funding_mode", obligation["funding_type"])
        direction = obligation["direction"]
        row["expected_" + direction] += Decimal(obligation.get("expected", "0"))
        row["confirmed_" + direction] += Decimal(obligation.get("confirmed", "0"))
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
    external_claims = advance_obligations(state)
    external_settled = {}
    for movement in state["movements"]:
        preparation_checkpoint()
        for allocation in movement.get("allocations", []):
            preparation_checkpoint()
            key = allocation["obligation_id"]
            if key in external_claims:
                external_settled[key] = external_settled.get(key, Decimal(0)) + Decimal(allocation["amount"])
    for movement in state["movements"]:
        preparation_checkpoint()
        amount = Decimal(movement["amount"])
        signed = amount if movement["direction"] == "incoming" else -amount
        if movement.get("bank_id"):
            bank_amount = Decimal(movement.get("bank_amount") or movement["amount"])
            bank_currency = movement.get("bank_currency") or movement["currency"]
            row = get(movement["bank_kind"], movement["bank_id"], bank_currency, movement.get("bank_name"))
            row["actual"] += bank_amount if signed > 0 else -bank_amount
            if movement["bank_kind"] == "employee_custody":
                row["custody_spent"] += amount
        if (movement.get("party_type") == "pos_receivable" and movement.get("bank_arrival_confirmed") is True
                and movement.get("fee_reference") and Decimal(movement.get("actual_fee_amount", "0")) > 0):
            fee_account = get("operating_expense", "pos_settlement_fee", "SAR", "رسوم تسوية الشبكة")
            fee_account["actual"] += Decimal(movement["actual_fee_amount"])
            fee_account["expense_paid"] += Decimal(movement["actual_fee_amount"])
        if movement.get("bank_fee_amount") is not None:
            bank_fee = Decimal(movement["bank_fee_amount"])
            if bank_fee > 0:
                # Evidence-backed separate fee; cash was debited gross once above.
                fee_account = get("operating_expense", "advertising_bank_fx_fee", "SAR",
                                  "عمولات البنك لتمويل الإعلانات")
                fee_account["actual"] += bank_fee
                fee_account["expense_paid"] += bank_fee
        party = get(movement["party_type"], movement["party_id"], movement["currency"], movement.get("name"))
        if movement["party_type"] == "ad_account" and wallet_pool_enabled(state, movement["party_id"], movement["currency"]):
            party["actual"] -= signed
            continue
        if movement.get("funding_mode"):
            party["funding_mode"] = movement["funding_mode"]
        if movement["direction"] == "outgoing" and movement["party_type"] not in {"operating_expense", "owner_withdrawal"}:
            party["expense_paid"] += sum((Decimal(a["amount"]) for a in movement.get("allocations", [])
                if (state.get("engine", {}).get("obligations", {}).get(a["obligation_id"], {}).get("kind") == "recurring"
                    or movement.get('automatic_prepaid_shipping') is True
                    and state.get('engine', {}).get('obligations', {}).get(a['obligation_id'], {}).get('recognition_event') in {'order_completed', 'aramex_status_eligible'}
                    and state.get('engine', {}).get('obligations', {}).get(a['obligation_id'], {}).get('payment_mode') == 'prepaid')), Decimal(0))
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
            effect = -amount if (movement["party_type"] == "owner_withdrawal" and movement.get("workspace_operation") == "owner.return"
                                 and movement["direction"] == "incoming") else amount
            party["actual"] += effect
            party["expense_paid" if movement["party_type"] == "operating_expense" else "owner_withdrawals"] += effect
        elif movement["party_type"] not in {"bank", "cash"}:
            party["actual"] -= signed
            if movement.get("settlement_scope") == "account":
                # Apply the cash to the account. The original invoices retain their own evidence.
                paying = signed < 0
                due = "outstanding_payable" if paying else "outstanding_receivable"
                opposite = "outstanding_receivable" if paying else "outstanding_payable"
                gross = amount + Decimal(movement.get("actual_fee_amount", "0"))
                covered = min(gross, party[due])
                party[due] -= covered
                party[opposite] += gross - covered
                party["settled"] += covered
                continue
            unallocated = amount - sum((Decimal(a["amount"]) for a in movement.get("allocations", [])), Decimal(0))
            if unallocated > 0:
                # Unallocated cash remains an explicit advance/overcollection,
                # never disappears or silently marks an invoice paid.
                field = "outstanding_receivable" if signed < 0 else "outstanding_payable"
                if movement["party_type"] == "external_person":
                    identity = advance_id(movement)
                    settled = external_settled.get(identity, Decimal(0))
                    if settled > unallocated:
                        raise ValueError("external_advance_settlement_exceeds_balance")
                    claim = deepcopy(external_claims[identity])
                    claim.update(settled=fmt(settled), outstanding=fmt(unallocated-settled),
                                 available_to_pay=fmt(unallocated-settled), pending_confirmation=False)
                    obligations.append(claim)
                    party["settled"] += settled
                    if movement.get("external_operation") in EXTERNAL_CREATION:
                        party["confirmed_receivable" if signed < 0 else "confirmed_payable"] += unallocated
                    unallocated -= settled
                party[field] += unallocated
    # Salary accrued against a starting amount owed by an employee offsets that
    # amount first; the gross history remains available in obligations.
    for row in parties.values():
        preparation_checkpoint()
        if row["party_type"] == "employee_custody":
            row["custody_remaining"] = row["actual"]
        if row["party_type"] == "ad_account":
            if wallet_pool_enabled(state, row["party_id"], row["currency"]):
                pool = project_pool(state, row["party_id"], row["currency"])
                row.update(funding_mode="hybrid", funding_policy="wallet_first",
                           ad_wallet_spent_basis="cumulative_funded_spend",
                           ad_wallet_balance=pool["wallet_balance"], ad_payable=pool["payable"],
                           outstanding_receivable=pool["wallet_balance"], outstanding_payable=pool["payable"],
                           ad_wallet_spent=pool["wallet_spent"], expected_payable=pool["expected_spend"],
                           confirmed_payable=pool["opening_debt"]+pool["spend"], settled=pool["debt_settled"])
                obligations.append({"id": "operational-ad-balance:" + party_key("ad_account", row["party_id"], row["currency"]),
                    "party_id": row["party_id"], "party_type": "ad_account", "currency": row["currency"],
                    "name": row["name"], "kind": "advertising_balance", "direction": "payable",
                    "expected": "0.00", "confirmed": fmt(pool["gross_debt"]),
                    "settled": fmt(pool["debt_settled"]), "outstanding": fmt(pool["payable"]),
                    "available_to_pay": fmt(pool["payable"]), "funding_policy": "wallet_first",
                    "balance_role": "wallet_pool_balance", "allocation_mode": "automatic_account_balance",
                    "business_date": None, "pending_confirmation": False})
            else:
                row["ad_wallet_balance"] = row["outstanding_receivable"] - row["ad_wallet_spent"]
                row["outstanding_receivable"] = max(row["ad_wallet_balance"], Decimal(0))
                row["ad_payable"] = row["outstanding_payable"]
        if row["party_type"] == "employee":
            offset = min(row["outstanding_receivable"], row["outstanding_payable"])
            row["outstanding_receivable"] -= offset
            row["outstanding_payable"] -= offset
    for case in state.get("customer_returns", []):
        preparation_checkpoint()
        status = case.get("status")
        kind = case.get("refund_source_type")
        if status == "refunded" and kind == "bank":
            bank = get("bank", case["refund_source_id"], case["currency"], case["refund_source_name"])
            bank["actual"] -= Decimal(case["amount"])
        elif status == "salla_refund_confirmed" and kind in {"bank", "provider"}:
            from operational_provider_statement_reconciliation import fact_backed_employee_refund
            if fact_backed_employee_refund(state, case):
                # The statement approval already proved and recorded this exact
                # provider refund fact. Do not also apply the older cash debit.
                continue
            # This independent operational cash-source debit is authorized by
            # the user selecting where Salla's existing refund was executed.
            # Never subtract from provider receivables or order sales here:
            # the Salla order may already contain the updated NET sale total.
            account = get(kind, case["refund_source_id"], case["currency"],
                          case["refund_source_name"])
            account["actual"] -= Decimal(case["amount"])
            account["customer_refunds_manual_debited"] += Decimal(case["amount"])
    if ad_balances_sar:
        from operational_ad_balance_sar import project_parties
        project_parties(parties, state.get("engine", {}).get("ad_balance_sar_basis", {}), valuation_issues)
    details = {k: v for k, v in state.get("engine", {}).items() if k not in {"audit", "obligations", "provider_fee_inputs"}}
    # The evidence refresh queue belongs to the worker, not the public report.
    details.get("customer_tender_coverage", {}).get("sar", {}).pop("proof_lookups", None)
    details["employee_custody"] = [{"party_id": row["party_id"], "name": row["name"], "currency": row["currency"],
        **{key: fmt(row[key]) for key in ("opening", "custody_funded", "custody_spent", "custody_returned", "custody_remaining", "custody_adjustments")},
        "receipts": [{key: movement.get(key) for key in ("id", "receipt_id", "receipt_hash", "amount", "direction", "source", "actor_id", "occurred_at", "note")}
                     for movement in state["movements"] if movement["currency"] == row["currency"] and (
                         (movement["party_type"] == "employee_custody" and movement["party_id"] == row["party_id"])
                         or (movement.get("bank_kind") == "employee_custody" and movement.get("bank_id") == row["party_id"]))]}
        for row in parties.values() if row["party_type"] == "employee_custody"]
    details["manual_customer_refunds"] = [{
        "id":case["id"], "order_number":case["order_number"], "amount":case["amount"],
        "currency":case["currency"], "status":case["status"],
        "source_type":case.get("refund_source_type"),
        "source_id":case.get("refund_source_id"),
        "source_name":case.get("refund_source_name"),
        "reference":case.get("refund_reference"), "reason":case.get("note")
        } for case in state.get("customer_returns", [])
        if case.get("case_type") == "order_compensation"]
    provider_metrics = deepcopy(details.get("provider_reports", {}))
    settlement_parts = {}
    for movement in state["movements"]:
        preparation_checkpoint()
        gross = Decimal(movement["amount"]) + Decimal(movement.get("actual_fee_amount", "0"))
        cash = Decimal(movement["amount"])
        cumulative, previous_cash = Decimal(0), Decimal(0)
        for allocation in movement.get("allocations", []):
            preparation_checkpoint()
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
        preparation_checkpoint()
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
    for identity in provider_metrics.keys() & valuations.keys():
        preparation_checkpoint()
        provider_metrics[identity] = convert_projection(provider_metrics[identity], valuations[identity])
    details["provider_reports"] = provider_metrics
    cod_metrics = deepcopy(details.get("cod_reports", {}))
    # Preserve incoming-only settlement arithmetic and allocation order, but
    # traverse history once rather than once for every COD claim.
    cod_settled = {}
    if cod_metrics:
        for movement in state["movements"]:
            preparation_checkpoint()
            if movement["direction"] != "incoming":
                continue
            for allocation in movement.get("allocations", []):
                preparation_checkpoint()
                identity = allocation["obligation_id"]
                if identity in cod_metrics:
                    cod_settled[identity] = cod_settled.get(identity, Decimal(0)) + Decimal(allocation["amount"])
    for identity, projection in cod_metrics.items():
        preparation_checkpoint()
        obligation = state.get("engine", {}).get("obligations", {}).get(identity, {})
        confirmed = Decimal(obligation.get("confirmed", "0"))
        settled = cod_settled.get(identity, Decimal(0))
        projection.update(party_type=obligation.get("party_type", projection.get("party_type")),
                          confirmed_custody=fmt(confirmed), settled=fmt(settled),
                          outstanding=fmt(max(confirmed-settled, Decimal(0))))
    details["cod_reports"] = cod_metrics
    issues = deepcopy(state.get("engine", {}).get("issues", [])) + valuation_issues
    details["provider_order_payments"], details["provider_paid_totals"] = provider_customer_payments({
        "details": details, "issues": issues, "obligations": obligations,
    })
    from operational_provider_summary import provider_balances
    order_details = None
    if workspace_provider is not None:
        from operational_workspace_provider_orders import ProviderOrders
        order_details = ProviderOrders(workspace_provider, state)
    details["provider_balances"], resolved_fees = provider_balances(
        state, details, obligations, parties if provider_balances_net else deepcopy(parties), valuations,
        sar_view=provider_balances_sar, paypal_sar=paypal_sar, order_details=order_details)
    if order_details is not None:
        details["workspace_provider_orders"] = list(order_details.rows.values())
    issues = [entry for entry in issues if not (
        entry.get("code") == "provider_fee_estimate_unavailable" and entry.get("source_id") in resolved_fees)]
    for currency, providers in details["provider_balances"].items():
        preparation_checkpoint()
        for provider in providers:
            preparation_checkpoint()
            if not provider["complete"]:
                issues.append({"code": "provider_net_balance_incomplete", "provider": provider["provider"],
                               "currency": currency, "source_id": "provider:" + provider["provider"],
                               "message": "صافي المنصة يحتاج إلى استكمال العمولة أو مطابقة المدفوع مع التسويات."})
    from operational_customer_wallet import project as customer_wallet_balances
    details["customer_wallet_balances"], wallet_issues = customer_wallet_balances(
        state, cutoff=as_of or state.get("engine", {}).get("as_of"), require_cutoff=True)
    issues.extend(wallet_issues)
    for currency, balance in details["customer_wallet_balances"].items():
        preparation_checkpoint()
        if balance["remaining"] is None:
            continue
        row = get("customer_wallet", "salla:" + balance["merchant_id"], currency, balance["name"])
        row.update(confirmed_payable=Decimal(balance["remaining"]),
                   outstanding_payable=Decimal(balance["remaining"]),
                   read_only=True, balance_role="customer_wallet_liability", source_as_of=balance["as_of"])
    summaries = {}
    for row in parties.values():
        preparation_checkpoint()
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
    for row in parties.values():
        preparation_checkpoint()
        if row["party_type"] == "ad_account" and row["ad_wallet_balance"] < 0:
            issues.append({"code": "advertising_prepaid_wallet_negative", "source_id": row["party_id"],
                           "currency": row["currency"], "amount": fmt(row["ad_wallet_balance"])})
    preparation_checkpoint()
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


def account_statement(state, account):
    """Project the same account legs as report(), without creating any movement."""
    from operational_financial_activity_runtime import checked_rows, preparation_checkpoint
    preparation_checkpoint()
    kind, identity, currency = account['kind'], account['id'], account['currency']
    balance = next((r for r in report(state)['parties'] if r['party_type']==kind
                    and r['party_id']==identity and r['currency']==currency), {})
    items = []
    for movement in checked_rows(state['movements']):
        bank_leg = (movement.get('bank_kind') == kind and movement.get('bank_id') == identity
                    and (movement.get('bank_currency') or movement['currency']) == currency)
        party_leg = (movement['party_type'] == kind and movement['party_id'] == identity
                     and movement['currency'] == currency)
        if not bank_leg and not party_leg:
            continue
        direction = 1 if movement['direction']=='incoming' else -1
        effect = Decimal(0)
        if bank_leg:
            # A USD ad funding payment may debit a SAR bank including its fee.
            effect += Decimal(movement.get('bank_amount') or movement['amount']) * direction
        if party_leg:
            signed = Decimal(movement['amount']) * direction
            if movement['kind']=='transfer':
                effect -= signed
            elif movement['kind']=='correction' and not movement.get('bank_id'):
                effect += signed
        if not effect:
            continue
        stamp = movement.get('occurred_at','')
        business_day = movement.get('business_date') or (
            datetime.fromisoformat(stamp.replace('Z','+00:00')).astimezone(ZoneInfo('Asia/Riyadh')).date().isoformat() if stamp else '')
        items.append({'id':movement['id'], 'name':movement.get('name') or account['name'],
            'amount':fmt(abs(effect)), 'currency':currency, 'direction':'incoming' if effect>0 else 'outgoing',
            'business_date':business_day, 'occurred_at':stamp, 'reference':movement.get('reference',''),
            'note':movement.get('note',''), 'kind':movement['kind'],
            **({'ad_amount': movement['amount'], 'ad_currency': movement['currency'],
                'bank_fee_amount': movement['bank_fee_amount'],
                'effective_fx_rate_to_sar': movement['effective_fx_rate_to_sar']}
               if movement.get('bank_currency') else {})})
    if kind=='bank':
        for case in checked_rows(state.get('customer_returns',[])):
            if (case['status'] not in {'refunded', 'salla_refund_confirmed'}
                    or case['refund_source_type']!='bank'
                    or case['refund_source_id']!=identity or case['currency']!=currency):
                continue
            stamp = case.get('refunded_at') or case.get('updated_at') or case.get('created_at','')
            business_day = datetime.fromisoformat(stamp.replace('Z','+00:00')).astimezone(ZoneInfo('Asia/Riyadh')).date().isoformat() if stamp else ''
            items.append({'id':'customer_return:'+case['id'],
                'name':'تعويض جزئي مسترد سابقًا في سلة' if case.get('case_type')=='order_compensation'
                       else 'استرداد عميل',
                'amount':case['amount'], 'currency':currency, 'direction':'outgoing',
                'business_date':business_day, 'occurred_at':stamp, 'reference':case.get('refund_reference',''),
                'note':case.get('note',''), 'kind':'customer_refund'})
    items.sort(key=lambda r:(r['business_date'],r['occurred_at'],r['id']),reverse=True)
    preparation_checkpoint()
    return {'account':{'name':account['name'],'party_type':kind,'party_id':identity,'currency':currency,
                       'actual':balance.get('actual','0.00'),'opening':balance.get('opening','0.00')},'items':items}
