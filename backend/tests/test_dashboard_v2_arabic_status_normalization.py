from dashboard_v2_routes import _matches_any, _normalize_match_text


def test_arabic_status_hamza_variants_match():
    allowed = ["بإنتظار المراجعة"]
    assert _matches_any("بانتظار المراجعة", allowed) is True
    assert _matches_any("بأنتظار المراجعة", allowed) is True
    assert _matches_any("بإنتظار المراجعة", allowed) is True


def test_arabic_status_diacritics_and_whitespace_match():
    assert _matches_any("  بِانتظار   المراجعة  " , ["بانتظار المراجعة"]) is True


def test_status_policy_is_not_broadened_to_different_status():
    allowed = ["بإنتظار المراجعة"]
    assert _matches_any("تم التوصيل", allowed) is False
    assert _matches_any("ملغي", allowed) is False


def test_normalizer_preserves_words_while_normalizing_alef():
    assert _normalize_match_text("بإنتظار المراجعة") == "بانتظار المراجعة"
