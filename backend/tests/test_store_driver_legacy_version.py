"""Regression coverage for legacy courier records created without version."""
from store_delivery_driver_routes import _driver_version_filter


def test_legacy_driver_accepts_missing_version_only_at_first_edit():
    assert _driver_version_filter(1) == {
        "$or": [{"version": 1}, {"version": {"$exists": False}}]
    }


def test_versioned_driver_stays_optimistically_locked():
    assert _driver_version_filter(2) == {"version": 2}
    assert _driver_version_filter(7) == {"version": 7}
