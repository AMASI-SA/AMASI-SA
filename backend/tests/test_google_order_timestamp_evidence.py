from datetime import date

import pytest

from integrations_control_center.google_order_timestamp_evidence import exact_riyadh_order_date


def test_explicit_instant_crosses_riyadh_midnight():
    assert exact_riyadh_order_date('2026-08-14T21:30:00Z') == date(2026, 8, 15)
    assert exact_riyadh_order_date('2026-08-15T00:30:00+03:00') == date(2026, 8, 15)


@pytest.mark.parametrize('value', [None, '', '2026-08-15', '2026-08-15T00:30:00',
    '2026-02-30T00:00:00Z', '2026-08-15T00:30:00+24:00', '2026-08-15T00:30:00+03:99',
    '2026-08-15T00:30:00-00:00', '2026-08-15 00:30:00Z', 123, True])
def test_inexact_or_invalid_source_is_unknown(value):
    assert exact_riyadh_order_date(value) is None


def test_date_boundaries_use_source_offset_not_normalized_day():
    assert exact_riyadh_order_date('2026-08-15T00:10:00+05:30') == date(2026, 8, 14)
    assert exact_riyadh_order_date('2026-08-14T23:00:00-04:00') == date(2026, 8, 15)


def test_fractional_seconds_and_extreme_dates():
    assert exact_riyadh_order_date('2026-08-14T21:00:00.123456Z') == date(2026, 8, 15)
    assert exact_riyadh_order_date('9999-12-31T23:59:59Z') is None
