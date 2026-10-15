from datetime import datetime, timedelta, timezone

from campaign_ai_customer_voice_evidence_v3 import aggregate_customer_voice_rows


def test_customer_voice_never_exposes_raw_pii_or_conversation_text():
    now = datetime(2026, 8, 19, 2, 0, tzinfo=timezone.utc)
    output = aggregate_customer_voice_rows([
        {
            "observed_at": now,
            "signal_type": "price_objection",
            "count": 3,
            "source_channel": "instagram",
            "product_id": "p-1",
            "customer_name": "SHOULD_NOT_SURVIVE",
            "phone": "SHOULD_NOT_SURVIVE",
            "email": "SHOULD_NOT_SURVIVE",
            "raw_message": "SHOULD_NOT_SURVIVE",
            "conversation_text": "SHOULD_NOT_SURVIVE",
        }
    ], now=now, relevant_product_ids={"p-1"})

    rendered = repr(output)
    assert "SHOULD_NOT_SURVIVE" not in rendered
    assert output["contracts"]["raw_conversations_included"] is False
    assert output["contracts"]["pii_included"] is False
    assert output["product_corroboration"]["p-1"]["signal_types"]["price_objection"] == 3


def test_product_feedback_does_not_become_campaign_attribution_without_verified_link():
    now = datetime(2026, 8, 19, 2, 0, tzinfo=timezone.utc)
    output = aggregate_customer_voice_rows([
        {
            "observed_at": now,
            "signal_type": "offer_confusion",
            "count": 5,
            "source_channel": "instagram",
            "product_id": "p-1",
            "campaign_id": "c-1",
            "attribution_status": "inferred_or_unknown",
        }
    ], now=now, relevant_product_ids={"p-1"}, relevant_campaign_ids={"c-1"})

    assert "c-1" not in output["verified_campaign_corroboration"]
    assert output["product_corroboration"]["p-1"]["signal_count"] == 5
    assert output["contracts"]["store_or_product_feedback_becomes_campaign_attribution"] is False


def test_only_explicit_verified_campaign_customer_voice_is_campaign_corroboration():
    now = datetime(2026, 8, 19, 2, 0, tzinfo=timezone.utc)
    output = aggregate_customer_voice_rows([
        {
            "observed_at": now,
            "signal_type": "creative_expectation_mismatch",
            "count": 4,
            "source_channel": "instagram",
            "product_id": "p-1",
            "campaign_id": "c-1",
            "attribution_status": "verified_explicit_campaign",
        }
    ], now=now, relevant_product_ids={"p-1"}, relevant_campaign_ids={"c-1"})

    assert output["verified_campaign_corroboration"]["c-1"]["signal_count"] == 4
    assert output["verified_campaign_corroboration"]["c-1"]["signal_types"]["creative_expectation_mismatch"] == 4


def test_old_customer_voice_is_not_used_for_current_campaign_judgment():
    now = datetime(2026, 8, 19, 2, 0, tzinfo=timezone.utc)
    output = aggregate_customer_voice_rows([
        {
            "observed_at": now - timedelta(days=45),
            "signal_type": "shipping_cost_objection",
            "count": 20,
            "source_channel": "whatsapp",
        }
    ], now=now)

    assert output["available"] is False
    assert output["windows"]["last_30d"]["signal_count"] == 0
    assert "customer_voice_not_connected_or_no_recent_normalized_signals" in output["limitations"]
