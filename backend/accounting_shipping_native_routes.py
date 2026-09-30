"""MZ2-only setup, recognition, independent receive/pay and statement routes."""
from fastapi import Depends, HTTPException
from pydantic import Field

from accounting_ledger_v2 import AccountingLedgerV2Error
from accounting_sales_tax import TaxError
from accounting_shipping_native_contract import (
    Input, CourierInput, RateInput, BindingInput, RecognitionInput,
    DriverRecognitionInput, SettlementInput, subaccounts, select_rate, now,
)
from accounting_shipping_native_setup import save_setup, read_setup
from accounting_shipping_native import _actor, _rows, recognize_cod, recognize_fee_delivery, accrue_fee, settle, statement
from accounting_module_contract import accounting_owner_id
from accounting_write_control import fresh_actor

BASE = "/accounting-module/shipping-v2"


class FeeInput(Input):
    evidence_id: str = Field(min_length=1, max_length=160)


async def readiness(db, owner):
    setup = await read_setup(db, owner)
    drivers = await db.store_drivers.find({"user_id": owner, "status": {"$nin": ["inactive", "archived", "deleted"]},
        "archived": {"$ne": True}, "deleted": {"$ne": True}, "is_archived": {"$ne": True}, "is_deleted": {"$ne": True}},
        {"_id": 0, "id": 1, "name": 1}).to_list(1001)
    stages = {"7": [], "8": [], "9": []}
    if len(drivers) > 1000:
        stages["9"].append({"code": "shipping_driver_scope_too_large"})
        drivers = []
    couriers = [c for c in setup["couriers"] if c["status"] == "active" and c.get("confirmed_by") and c.get("confirmed_at")]
    parties = [("courier", c["courier_key"]) for c in couriers] + [("store_driver", d.get("id")) for d in drivers]
    if not couriers:
        stages["7"].append({"code": "shipping_canonical_identity_required"})
        stages["8"].append({"code": "shipping_opening_scope_required"})
    for kind, identity in parties:
        stage = "7" if kind == "courier" else "9"
        try:
            select_rate(setup, kind, identity, "delivery", now())
        except HTTPException as exc:
            stages[stage].append({"party_type": kind, "party_id": identity, **exc.detail})
        try:
            await _rows(db, owner, [(kind, identity, sub) for sub in subaccounts(kind)])
        except HTTPException as exc:
            stages["8" if kind == "courier" else "9"].append({"party_type": kind, "party_id": identity,
                "code": exc.detail.get("code") if isinstance(exc.detail, dict) else exc.detail})
    if not drivers:
        stages["9"].append({"code": "shipping_driver_scope_or_not_applicable_required"})
    return {"stages": {stage: {"ready": not reasons, "reasons": reasons} for stage, reasons in stages.items()},
            "couriers": couriers, "store_drivers": drivers, "setup_version": setup["version"],
            "p02": "LOCKED_BY_EXISTING_ACTIVATION_GATE", "activation_performed": False,
            "bank_port": {"ready": False, "code": "mz2_shipping_bank_port_not_integrated"},
            "driver_payment_destination": {"ready": False, "code": "mz2_driver_payment_destination_not_integrated",
                "card_terminal_destination": "pos_receivable", "direct_pos_to_bank_on_accept": False},
            "delivery_policy": "canonical_salla_delivered_no_upload", "legacy_evidence_used": False}


def install_shipping_native_routes(router, db, current_user):
    async def scope(user, permission="accounting.shipping.view"):
        actor = await fresh_actor(db, user)
        owner = accounting_owner_id(actor)
        await _actor(db, owner, actor["id"], permission)
        return owner, actor["id"]

    async def invoke(awaitable):
        try:
            return await awaitable
        except AccountingLedgerV2Error as exc:
            raise HTTPException(409, detail={"code": exc.code}) from exc
        except TaxError as exc:
            raise HTTPException(409, detail={"code": str(exc)}) from exc

    @router.get(BASE + "/context")
    async def context(user=Depends(current_user)):
        owner, _ = await scope(user)
        return await invoke(readiness(db, owner))

    @router.post(BASE + "/couriers")
    async def courier(payload: CourierInput, user=Depends(current_user)):
        owner, actor = await scope(user, "accounting.rules.manage")
        return await save_setup(db, owner, actor, payload)

    @router.post(BASE + "/rates")
    async def rates(payload: RateInput, user=Depends(current_user)):
        owner, actor = await scope(user, "accounting.rules.manage")
        return await save_setup(db, owner, actor, payload)

    @router.post(BASE + "/bindings")
    async def bindings(payload: BindingInput, user=Depends(current_user)):
        owner, actor = await scope(user, "accounting.rules.manage")
        return await save_setup(db, owner, actor, payload)

    @router.post(BASE + "/recognize-courier")
    async def recognize_courier(payload: RecognitionInput, user=Depends(current_user)):
        owner, actor = await scope(user, "accounting.settlements.post")
        return await invoke(recognize_cod(db, owner=owner, actor_id=actor, order_number=payload.order_number))

    @router.post(BASE + "/recognize-driver")
    async def recognize_driver(payload: DriverRecognitionInput, user=Depends(current_user)):
        owner, actor = await scope(user, "accounting.settlements.post")
        return await invoke(recognize_cod(db, owner=owner, actor_id=actor, assignment_id=payload.assignment_id))

    @router.post(BASE + "/accrue-fee")
    async def accrue(payload: FeeInput, user=Depends(current_user)):
        owner, actor = await scope(user, "accounting.settlements.post")
        return await invoke(accrue_fee(db, owner=owner, actor_id=actor, evidence_id=payload.evidence_id))

    @router.post(BASE + "/courier-delivery-fee")
    async def courier_fee(payload: RecognitionInput, user=Depends(current_user)):
        owner, actor = await scope(user, "accounting.settlements.post")
        return await invoke(recognize_fee_delivery(db, owner=owner, actor_id=actor, order_number=payload.order_number))

    @router.post(BASE + "/driver-delivery-fee")
    async def driver_fee(payload: DriverRecognitionInput, user=Depends(current_user)):
        owner, actor = await scope(user, "accounting.settlements.post")
        return await invoke(recognize_fee_delivery(db, owner=owner, actor_id=actor, assignment_id=payload.assignment_id))

    @router.post(BASE + "/settlements")
    async def settlements(payload: SettlementInput, user=Depends(current_user)):
        owner, actor = await scope(user, "accounting.settlements.post")
        return await invoke(settle(db, owner=owner, actor_id=actor, payload=payload))

    @router.get(BASE + "/statements/{kind}/{identity}")
    async def statements(kind: str, identity: str, user=Depends(current_user)):
        owner, actor = await scope(user)
        return await invoke(statement(db, owner=owner, actor_id=actor, kind=kind, identity=identity))

    @router.post(BASE + "/retry/{order_number}")
    async def retry(order_number: str, user=Depends(current_user)):
        owner, actor = await scope(user, "accounting.settlements.post")
        from accounting_shipping_native_observer import observe_delivery
        return await observe_delivery(db, owner=owner, order_number=order_number, actor_id=actor)

    @router.post(BASE + "/retry-driver/{assignment_id}")
    async def retry_driver(assignment_id: str, user=Depends(current_user)):
        owner, actor = await scope(user, "accounting.settlements.post")
        from accounting_shipping_native_observer import observe_driver_delivery
        return await observe_driver_delivery(db, owner=owner, assignment_id=assignment_id, actor_id=actor)
