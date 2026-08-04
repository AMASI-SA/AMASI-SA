from integrations_control_center.snapchat_adsquad_parent_delivery import (
    ad_squad_effective_delivery_state,
)


def test_active_adsquad_is_operationally_paused_when_parent_budget_is_exhausted():
    result = ad_squad_effective_delivery_state(
        "ACTIVE",
        ["DELIVERING"],
        parent_campaign_status="ACTIVE",
        parent_campaign_delivery={
            "state": "NOT_DELIVERING",
            "code": "CAMPAIGN_DAILY_BUDGET_EXHAUSTED",
            "label": "لا تسليم — خارج الميزانية اليومية",
            "detail": "بلغت الحملة حد الإنفاق اليومي في Snapchat.",
        },
    )

    assert result["configured_status"] == "ACTIVE"
    assert result["effective_status"] == "PAUSED"
    assert result["state"] == "NOT_DELIVERING"
    assert result["inherited_from_campaign"] is True
    assert result["code"] == "PARENT_CAMPAIGN_DAILY_BUDGET_EXHAUSTED"
    assert result["label"] == "لا تسليم — الحملة خارج الميزانية اليومية"


def test_active_adsquad_is_operationally_paused_when_parent_campaign_is_paused():
    result = ad_squad_effective_delivery_state(
        "ACTIVE",
        [],
        parent_campaign_status="PAUSED",
        parent_campaign_delivery={
            "state": "NOT_DELIVERING",
            "code": "CAMPAIGN_NOT_ACTIVE",
            "label": "غير نشط",
            "detail": "الحملة متوقفة من مفتاح الحالة داخل Snapchat.",
        },
    )

    assert result["configured_status"] == "ACTIVE"
    assert result["effective_status"] == "PAUSED"
    assert result["inherited_from_campaign"] is True
    assert "الحملة" in result["label"]


def test_delivering_parent_preserves_active_adsquad():
    result = ad_squad_effective_delivery_state(
        "ACTIVE",
        ["DELIVERING"],
        parent_campaign_status="ACTIVE",
        parent_campaign_delivery={
            "state": "DELIVERING",
            "code": "DELIVERING",
            "label": "يتم التسليم",
            "detail": None,
        },
    )

    assert result["configured_status"] == "ACTIVE"
    assert result["effective_status"] == "ACTIVE"
    assert result["state"] == "DELIVERING"
    assert result["deliverable"] is True
    assert result["inherited_from_campaign"] is False


def test_paused_adsquad_remains_its_own_stop_reason():
    result = ad_squad_effective_delivery_state(
        "PAUSED",
        [],
        parent_campaign_status="ACTIVE",
        parent_campaign_delivery={
            "state": "DELIVERING",
            "code": "DELIVERING",
            "label": "يتم التسليم",
            "detail": None,
        },
    )

    assert result["configured_status"] == "PAUSED"
    assert result["effective_status"] == "PAUSED"
    assert result["code"] == "AD_SQUAD_NOT_ACTIVE"
    assert result["inherited_from_campaign"] is False
