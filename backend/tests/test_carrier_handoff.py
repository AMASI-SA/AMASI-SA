from copy import deepcopy

import pytest
from fastapi import HTTPException
from carrier_handoff import (
    CarrierHandoffError,
    advance_carrier_handoff_from_salla_status,
    confirm_carrier_label_print,
    normalize_shipping_barcode,
    receive_carrier_shipment,
    workflow_stage_for_salla_status,
)


def _matches(document, query):
    for key, expected in query.items():
        if key == "$or":
            if not any(_matches(document, row) for row in expected):
                return False
            continue
        actual = document.get(key)
        if isinstance(expected, dict):
            if "$exists" in expected:
                if (key in document) is not bool(expected["$exists"]):
                    return False
            elif "$in" in expected:
                if actual not in expected["$in"]:
                    return False
            else:
                raise AssertionError(f"unsupported query operator: {expected}")
        elif isinstance(actual, list):
            if expected not in actual:
                return False
        elif actual != expected:
            return False
    return True


class _Result:
    def __init__(self, modified_count=0):
        self.modified_count = modified_count


class _Cursor:
    def __init__(self, rows=None):
        self.rows = [deepcopy(row) for row in rows or []]

    def sort(self, *_args, **_kwargs):
        return self

    async def to_list(self, length):
        return self.rows[:length]


class _Collection:
    def __init__(self, rows=None):
        self.rows = [deepcopy(row) for row in rows or []]

    async def find_one(self, query, _projection=None):
        return next(
            (deepcopy(row) for row in self.rows if _matches(row, query)),
            None,
        )

    def find(self, query, _projection=None):
        return _Cursor(row for row in self.rows if _matches(row, query))

    async def update_one(self, query, update):
        for row in self.rows:
            if not _matches(row, query):
                continue
            row.update(deepcopy(update.get("$set") or {}))
            return _Result(1)
        return _Result(0)

    async def insert_one(self, row):
        self.rows.append(deepcopy(row))
        return _Result(1)


class _DB:
    def __init__(self, workflows, instructions=None):
        self.collections = {
            "order_review_workflows": _Collection(workflows),
            "mezan_fulfillment_events_v2": _Collection(),
            "mezan_order_tracking_instructions_v1": _Collection(instructions),
        }

    def __getitem__(self, name):
        return self.collections[name]


def _workflow(**overrides):
    return {
        "user_id": "owner-1",
        "order_number": "276628330",
        "stage": "completed",
        "assembly_status": "completed",
        "carrier_label_ready": True,
        "carrier_label_type": "carrier",
        "carrier_name": "iMile",
        "carrier_tracking_number": "6081326581116",
        **overrides,
    }


def test_barcode_and_salla_status_normalization_are_exact():
    assert normalize_shipping_barcode(" 6081-3265 81116 ") == "6081326581116"
    assert workflow_stage_for_salla_status("delivering") == "delivering"
    assert workflow_stage_for_salla_status("delivered") == "delivered"
    assert workflow_stage_for_salla_status("completed") is None


@pytest.mark.asyncio
async def test_labeling_employee_must_scan_the_matching_imile_awb():
    db = _DB([_workflow()])

    with pytest.raises(CarrierHandoffError) as mismatch:
        await confirm_carrier_label_print(
            db,
            user_id="owner-1",
            order_number="276628330",
            scanned_barcode="WRONG-1116",
            actor_id="labeler-1",
            actor_name="موظف العنونة",
        )

    assert mismatch.value.code == "carrier_label_barcode_mismatch"
    result = await confirm_carrier_label_print(
        db,
        user_id="owner-1",
        order_number="276628330",
        scanned_barcode="6081326581116",
        actor_id="labeler-1",
        actor_name="موظف العنونة",
    )
    assert result["carrier_label_print_confirmed"] is True
    saved = db["order_review_workflows"].rows[0]
    assert saved["carrier_handoff_state"] == "awaiting_carrier_handoff"
    assert saved["carrier_label_barcode"] == "6081326581116"


@pytest.mark.asyncio
async def test_customer_service_gate_blocks_carrier_label_confirmation():
    db = _DB(
        [_workflow()],
        instructions=[
            {
                "id": "instruction-1",
                "user_id": "owner-1",
                "order_number": "276628330",
                "status": "active",
                "scope": "order",
                "target_stages": ["carrier_handoff"],
                "enforcement": "completion_required",
            }
        ],
    )

    with pytest.raises(HTTPException) as blocked:
        await confirm_carrier_label_print(
            db,
            user_id="owner-1",
            order_number="276628330",
            scanned_barcode="6081326581116",
            actor_id="labeler-1",
            actor_name="موظف العنونة",
        )

    assert blocked.value.status_code == 409
    assert blocked.value.detail["code"] == (
        "customer_service_instruction_action_required"
    )


@pytest.mark.asyncio
async def test_handoff_scan_claims_once_and_rejects_every_duplicate():
    db = _DB(
        [
            _workflow(
                carrier_label_print_confirmed=True,
                carrier_label_barcode="6081326581116",
                carrier_handoff_state="awaiting_carrier_handoff",
            )
        ]
    )

    result = await receive_carrier_shipment(
        db,
        user_id="owner-1",
        scanned_barcode="6081326581116",
        actor_id="handoff-1",
        actor_name="موظف تسليم الشحن",
    )
    assert result["carrier_handoff_employee_id"] == "handoff-1"
    assert result["carrier_handoff_custody_active"] is True

    with pytest.raises(CarrierHandoffError) as duplicate:
        await receive_carrier_shipment(
            db,
            user_id="owner-1",
            scanned_barcode="6081326581116",
            actor_id="handoff-2",
            actor_name="موظف آخر",
        )
    assert duplicate.value.code == "carrier_shipment_already_received"
    assert duplicate.value.details["employee_name"] == "موظف تسليم الشحن"


@pytest.mark.asyncio
async def test_mezan_orders_status_sync_releases_employee_custody():
    db = _DB(
        [
            _workflow(
                carrier_label_print_confirmed=True,
                carrier_label_barcode="6081326581116",
                carrier_handoff_state="with_handoff_employee",
                carrier_handoff_employee_id="handoff-1",
                carrier_handoff_employee_name="موظف تسليم الشحن",
            )
        ]
    )

    result = await advance_carrier_handoff_from_salla_status(
        db,
        user_id="owner-1",
        order_number="276628330",
        status_slug="delivering",
        status_name="جاري التوصيل",
        source="mezan_orders_page_status_sync",
    )

    assert result == {"advanced": True, "stage": "delivering"}
    saved = db["order_review_workflows"].rows[0]
    assert saved["stage"] == "delivering"
    assert saved["carrier_handoff_state"] == "carrier_in_delivery"
    assert saved["carrier_handoff_custody_active"] is False
    assert saved["carrier_handoff_release_source"] == "mezan_orders_page_status_sync"


@pytest.mark.asyncio
async def test_delivered_status_also_releases_handoff_employee_custody():
    db = _DB(
        [
            _workflow(
                carrier_label_print_confirmed=True,
                carrier_label_barcode="6081326581116",
                carrier_handoff_state="with_handoff_employee",
                carrier_handoff_employee_id="handoff-1",
                carrier_handoff_employee_name="موظف تسليم الشحن",
                carrier_handoff_custody_active=True,
            )
        ]
    )

    result = await advance_carrier_handoff_from_salla_status(
        db,
        user_id="owner-1",
        order_number="276628330",
        status_slug="delivered",
        status_name="تم التوصيل",
    )

    assert result == {"advanced": True, "stage": "delivered"}
    saved = db["order_review_workflows"].rows[0]
    assert saved["stage"] == "delivered"
    assert saved["carrier_handoff_state"] == "delivered"
    assert saved["carrier_handoff_custody_active"] is False
    assert saved["carrier_handoff_employee_id"] == "handoff-1"


@pytest.mark.asyncio
async def test_store_courier_never_enters_external_carrier_handoff_flow():
    db = _DB([_workflow(carrier_label_type="store_courier")])

    with pytest.raises(CarrierHandoffError) as blocked:
        await confirm_carrier_label_print(
            db,
            user_id="owner-1",
            order_number="276628330",
            scanned_barcode="6081326581116",
            actor_id="labeler-1",
            actor_name="موظف العنونة",
        )

    assert blocked.value.code == "store_courier_separate_flow"
    result = await advance_carrier_handoff_from_salla_status(
        db,
        user_id="owner-1",
        order_number="276628330",
        status_slug="delivering",
        status_name="جاري التوصيل",
    )
    assert result == {"advanced": False, "reason": "workflow_not_waiting"}
    assert db["order_review_workflows"].rows[0]["stage"] == "completed"
