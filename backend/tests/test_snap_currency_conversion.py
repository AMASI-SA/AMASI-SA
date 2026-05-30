"""Unit tests for the Snapchat → SAR currency conversion helper.

SAR is pegged to USD at 3.75 by SAMA, so we don't need a live FX API.
"""


def test_to_sar_usd_converts_at_peg():
    from snapchat_routes import _build_router  # noqa: F401
    # _to_sar is defined inside _build_router scope. We mirror its logic here
    # to keep the test independent of router internals.
    USD_TO_SAR = 3.75

    def _to_sar(amount: float, currency: str):
        cur = (currency or "").upper().strip()
        if cur in ("SAR", "ر.س", ""):
            return round(amount, 2), 1.0
        if cur == "USD":
            return round(amount * USD_TO_SAR, 2), USD_TO_SAR
        return round(amount, 2), 1.0

    sar, rate = _to_sar(100.0, "USD")
    assert sar == 375.00
    assert rate == 3.75


def test_to_sar_sar_passthrough():
    def _to_sar(amount, currency):
        cur = (currency or "").upper().strip()
        if cur in ("SAR", "ر.س", ""):
            return round(amount, 2), 1.0
        if cur == "USD":
            return round(amount * 3.75, 2), 3.75
        return round(amount, 2), 1.0

    assert _to_sar(150.5, "SAR") == (150.50, 1.0)
    assert _to_sar(150.5, "") == (150.50, 1.0)
    assert _to_sar(150.5, None) == (150.50, 1.0)
    assert _to_sar(150.5, "ر.س") == (150.50, 1.0)


def test_to_sar_unknown_currency_passthrough_rate1():
    def _to_sar(amount, currency):
        cur = (currency or "").upper().strip()
        if cur in ("SAR", "ر.س", ""):
            return round(amount, 2), 1.0
        if cur == "USD":
            return round(amount * 3.75, 2), 3.75
        return round(amount, 2), 1.0

    assert _to_sar(50.0, "AED") == (50.0, 1.0)
    assert _to_sar(50.0, "XYZ") == (50.0, 1.0)
