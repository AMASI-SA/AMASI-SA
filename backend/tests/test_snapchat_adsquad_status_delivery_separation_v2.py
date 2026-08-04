from integrations_control_center.snapchat_adsquad_status_delivery_separation import (
    separate_adsquad_status_and_delivery,
)


def test_active_adsquad_remains_active_when_account_debt_blocks_delivery():
    row = separate_adsquad_status_and_delivery({
        "ad_squad_id": "squad-1",
        "configured_status": "ACTIVE",
        "effective_status": "PAUSED",
        "status": "PAUSED",
        "delivery_state": "NOT_DELIVERING",
        "delivery_status": "لا تسليم — الحملة الحساب موقوف بسبب الدفع أو الرصيد",
        "status_inherited_from_campaign": True,
        "account_delivery_block": {
            "code": "ACCOUNT_PAYMENT_BLOCKED",
            "delivery_label": "لا تسليم — الحساب موقوف بسبب الدفع أو الرصيد",
            "detail": "رصيد الحساب أو وسيلة الدفع لا تسمح بالتسليم.",
        },
    })

    assert row["status"] == "ACTIVE"
    assert row["configured_status"] == "ACTIVE"
    assert row["effective_status"] == "ACTIVE"
    assert row["previous_operational_status"] == "PAUSED"
    assert row["delivery_state"] == "NOT_DELIVERING"
    assert row["delivery_reason_code"] == "ACCOUNT_PAYMENT_BLOCKED"
    assert row["delivery_status"] == (
        "لا تسليم — الحساب موقوف بسبب الدفع أو الرصيد"
    )
    assert row["delivery_inherited_from_account"] is True
    assert row["delivery_inherited_from_campaign"] is False
    assert row["status_inherited_from_campaign"] is False


def test_active_adsquad_keeps_switch_active_when_parent_budget_blocks_delivery():
    row = separate_adsquad_status_and_delivery({
        "ad_squad_id": "squad-2",
        "configured_status": "ACTIVE",
        "effective_status": "PAUSED",
        "status": "PAUSED",
        "delivery_state": "NOT_DELIVERING",
        "delivery_reason_code": "PARENT_CAMPAIGN_DAILY_BUDGET_EXHAUSTED",
        "delivery_status": "لا تسليم — الحملة خارج الميزانية اليومية",
        "status_inherited_from_campaign": True,
    })

    assert row["status"] == "ACTIVE"
    assert row["effective_status"] == "ACTIVE"
    assert row["delivery_state"] == "NOT_DELIVERING"
    assert row["delivery_status"] == (
        "لا تسليم — الحملة خارج الميزانية اليومية"
    )
    assert row["delivery_inherited_from_campaign"] is True
    assert row["delivery_inherited_from_account"] is False


def test_paused_adsquad_stays_paused_when_it_is_configured_paused():
    row = separate_adsquad_status_and_delivery({
        "ad_squad_id": "squad-3",
        "configured_status": "PAUSED",
        "effective_status": "PAUSED",
        "status": "PAUSED",
        "delivery_state": "NOT_DELIVERING",
        "delivery_status": "غير نشط",
    })

    assert row["status"] == "PAUSED"
    assert row["configured_status"] == "PAUSED"
    assert row["effective_status"] == "PAUSED"
    assert row["delivery_status"] == "غير نشط"


def test_delivering_active_adsquad_remains_active_and_delivering():
    row = separate_adsquad_status_and_delivery({
        "ad_squad_id": "squad-4",
        "configured_status": "ACTIVE",
        "effective_status": "ACTIVE",
        "status": "ACTIVE",
        "delivery_state": "DELIVERING",
        "delivery_status": "يتم التسليم",
        "deliverable": True,
    })

    assert row["status"] == "ACTIVE"
    assert row["effective_status"] == "ACTIVE"
    assert row["delivery_state"] == "DELIVERING"
    assert row["delivery_status"] == "يتم التسليم"
    assert row["deliverable"] is True
