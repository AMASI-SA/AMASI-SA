from qoyod_auto_unified.installer import _consume_historical_total_override


def test_automatic_override_is_consumed_before_forwarding() -> None:
    kwargs = {
        "allow_historical_positive_total": True,
        "sentinel": "kept",
    }

    result = _consume_historical_total_override(kwargs, automatic=True)

    assert result is True
    assert kwargs == {"sentinel": "kept"}


def test_manual_override_is_consumed_and_preserved() -> None:
    kwargs = {
        "allow_historical_positive_total": False,
        "sentinel": "kept",
    }

    result = _consume_historical_total_override(kwargs, automatic=False)

    assert result is False
