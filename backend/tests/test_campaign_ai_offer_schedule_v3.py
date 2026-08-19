from datetime import datetime, timedelta, timezone

from campaign_ai_decision_prompts_v3 import (
    FIRST_PASS_INSTRUCTIONS,
    SECOND_PASS_INSTRUCTIONS,
)
from campaign_ai_decision_schema_v3 import v3_json_schema
from campaign_ai_offer_schedule_v3 import build_offer_schedule_evidence


NOW = datetime(2026, 8, 19, 5, 0, tzinfo=timezone.utc)


def product(**overrides):
    row = {
        "name": "منتج",
        "description": "خصم خاص لفترة محدودة",
        "short_description": "",
        "price": 200,
        "sale_price": 150,
        "sale_starts_at": (NOW - timedelta(days=2)).isoformat(),
        "sale_ends_at": (NOW + timedelta(hours=6)).isoformat(),
    }
    row.update(overrides)
    return row


def test_offer_evidence_marks_expiring_sale_and_promotion_copy():
    evidence = build_offer_schedule_evidence(product(), now=NOW)
    assert evidence["state"] == "expiring"
    assert evidence["discounted_price_verified"] is True
    assert evidence["hours_to_end"] == 6.0
    assert evidence["promotion_copy"]["detected"] is True


def test_offer_evidence_marks_expired_sale_even_if_sale_price_still_present():
    evidence = build_offer_schedule_evidence(
        product(sale_ends_at=(NOW - timedelta(minutes=15)).isoformat()),
        now=NOW,
    )
    assert evidence["state"] == "expired"
    assert evidence["discounted_price_verified"] is True
    assert evidence["hours_to_end"] == -0.25


def test_promotion_copy_without_schedule_is_evidence_not_proof_of_active_sale():
    evidence = build_offer_schedule_evidence(
        product(
            sale_price=None,
            sale_starts_at=None,
            sale_ends_at=None,
            description="وفر الآن مع خصم مميز",
        ),
        now=NOW,
    )
    assert evidence["state"] == "no_schedule"
    assert evidence["discounted_price_verified"] is False
    assert evidence["promotion_copy"]["detected"] is True


def test_v3_prompt_requires_offer_expiry_reconciliation_and_owner_approval():
    assert "sale_starts_at/sale_ends_at" in FIRST_PASS_INSTRUCTIONS
    assert "EXTEND_PROMOTION" in FIRST_PASS_INSTRUCTIONS
    assert "لا تمدد التخفيض تلقائيًا" in FIRST_PASS_INSTRUCTIONS
    assert "SALE_EXPIRING/SALE_EXPIRED/EXPIRED_PROMOTION_COPY" in SECOND_PASS_INSTRUCTIONS
    assert "owner approval" in SECOND_PASS_INSTRUCTIONS


def test_v3_schema_supports_extend_promotion_as_business_recommendation():
    rendered = str(v3_json_schema())
    assert "EXTEND_PROMOTION" in rendered
    assert "product_change" in rendered
