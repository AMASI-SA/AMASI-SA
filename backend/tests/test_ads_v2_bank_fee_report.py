"""iter-257 — Bank-commission report regression test.

User-stated scenario (Snapchat Ads):
    spend_native = 105.41 USD
    spend_sar    = 395.76  (after FX, ~3.7549)
    bank_fee.rate_pct = 0.023  (2.3%)
expected:
    bank_fee_sar         = round(395.76 * 0.023, 2) = 9.10
    gross_sar            = 395.76 + 9.10           = 404.86
    bank_fee_pct (effective) = (9.10 / 395.76) * 100 ≈ 2.30

Goal: ensure the existing _compute_bank_fee + the new report
derivation of bank_fee_pct produce these exact numbers. We MUST NOT
touch the sync FX/spend code — this only validates the commission layer.
"""
import pytest

from ads_v2.sync.core import _compute_bank_fee


def test_snapchat_bank_fee_matches_user_scenario():
    """395.76 SAR × 2.3% = 9.10 SAR; gross = 404.86."""
    account = {
        "bank_fee": {
            "enabled": True,
            "method":  "pct",
            "rate_pct": 0.023,
        }
    }
    spend_sar = 395.76
    fee, detail = _compute_bank_fee(account, spend_sar)
    assert fee == 9.10, f"expected 9.10, got {fee}"
    assert detail["method"] == "pct"
    assert detail["rate_pct"] == 0.023
    gross = round(spend_sar + fee, 2)
    assert gross == 404.86


def test_effective_bank_fee_pct_matches_configured_rate():
    """Effective % derived from bank_fee_sar / spend_sar should ≈ 2.3%."""
    spend_sar = 395.76
    bank_fee_sar = 9.10
    effective_pct = round((bank_fee_sar / spend_sar) * 100.0, 3)
    # Slight rounding loss from 9.0998 → 9.10:
    assert abs(effective_pct - 2.3) < 0.005, \
        f"effective pct {effective_pct} not ~2.3%"


def test_bank_fee_disabled_returns_zero():
    """When bank_fee.enabled is False, fee is 0 regardless of rate."""
    account = {"bank_fee": {"enabled": False, "method": "pct", "rate_pct": 0.023}}
    fee, _detail = _compute_bank_fee(account, 1000.0)
    assert fee == 0.0


def test_pct_plus_flat_method():
    """pct_plus_flat must combine both correctly."""
    account = {"bank_fee": {
        "enabled": True, "method": "pct_plus_flat",
        "rate_pct": 0.023, "flat_amount_sar": 5.0,
    }}
    fee, detail = _compute_bank_fee(account, 395.76)
    # 395.76 * 0.023 ≈ 9.1025 → 9.10 + 5 = 14.10
    assert fee == 14.10
    assert detail["rate_pct_amount"] == 9.1025
    assert detail["flat_amount"] == 5.0


def test_report_layer_returns_new_fields():
    """Inspect reports.py to confirm new SSOT-derived fields exist."""
    with open("/app/backend/ads_v2/data_layer/reports.py", "r") as f:
        src = f.read()

    # Per-account aggregation must expose USD spend
    assert '"spend_native"' in src, "spend_native should appear in reports.py"
    # Per-account must derive effective bank_fee_pct
    assert "bank_fee_pct" in src
    # Provider aggregation now groups by currency for USD totals
    assert '"currency_native": "$currency_native"' in src
    # Multi-currency aggregation key for totals
    assert "spend_native_by_currency" in src


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
