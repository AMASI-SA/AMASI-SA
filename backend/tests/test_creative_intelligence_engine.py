from creative_intelligence_engine import CreativeIntelligenceEngine, CreativeSignal


def test_creative_engine_recommends_without_writes():
    result = CreativeIntelligenceEngine().analyze(
        [CreativeSignal(source="snap", metric="low_video_completion", value=0.2)]
    )
    assert result[0].area == "video"


def test_conversion_issue_recommendation():
    result = CreativeIntelligenceEngine().analyze(
        [CreativeSignal(source="meta", metric="high_click_low_conversion", value=1)]
    )
    assert result[0].area == "landing_page"
