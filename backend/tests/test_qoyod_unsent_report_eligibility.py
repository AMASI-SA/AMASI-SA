from integrations.qoyod.unsent_orders import (
    FAILED,
    SENT,
    UNSENT,
    _include_in_daily_report,
)


def _entry(status, salla_status):
    return {
        "status": status,
        "salla_status": salla_status,
        "salla_status_slug": None,
    }


def test_non_billable_salla_statuses_are_not_reported_as_unsent():
    for status in (
        "بانتظار المراجعة",
        "بانتظار الدفع",
        "ملغي",
        "محذوف",
        "مسند إلى مندوب التوصيل",
    ):
        assert _include_in_daily_report(_entry(UNSENT, status)) is False


def test_billable_salla_statuses_remain_actionable_when_unsent():
    for status in ("تم التنفيذ", "جاري التوصيل", "تم التوصيل"):
        assert _include_in_daily_report(_entry(UNSENT, status)) is True


def test_sent_history_remains_visible_but_failed_retry_tracks_current_eligibility():
    assert _include_in_daily_report(_entry(SENT, "بانتظار المراجعة")) is True
    # A failed local attempt is not actionable after Salla marks it cancelled.
    assert _include_in_daily_report(_entry(FAILED, "ملغي")) is False


def test_unknown_legacy_status_is_not_silently_hidden():
    assert _include_in_daily_report(_entry(UNSENT, None)) is True
