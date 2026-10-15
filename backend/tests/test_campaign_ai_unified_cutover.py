from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_active_snapchat_ai_evidence_consumers_use_unified_gateway():
    for name in (
        "campaign_ai_funnel_evidence_v3.py",
        "campaign_ai_temporal_evidence_v3.py",
        "campaign_ai_product_intelligence_v3.py",
    ):
        source = (ROOT / name).read_text()
        assert "unified_marketing.gateway" in source
        assert "snapchat_account_timezone_manager" not in source
        assert "snapchat_native_data_common" not in source


def test_unified_ai_cutover_keeps_named_v1_rollback_readers():
    source = (ROOT / "campaign_ai_policy_v2.py").read_text()
    assert "async def _snapchat_v1_campaign_entities(" in source
    assert "async def _snapchat_v1_child_entities(" in source
    assert "async def _snapchat_campaign_entities(" in source
    assert "async def _snapchat_child_entities(" in source
    assert "load_snapchat_unified_ai_entities" in source


def test_unified_shadow_compares_named_rollback_not_active_source():
    source = (ROOT / "campaign_ai_unified_shadow.py").read_text()
    assert "_snapchat_v1_campaign_entities" in source
    assert "_snapchat_v1_child_entities" in source
    assert "cutover_active\": True" in source
