import pytest

from integrations.qoyod_manual.send import (
    ManualSendRefused,
    _assert_sar_currency,
)


@pytest.mark.parametrize("value", ["SAR", "sar", None, ""])
def test_assert_sar_currency_accepts_sar_and_missing_default(value):
    canon = {} if value is None else {"currency": value}
    assert _assert_sar_currency(canon) == "SAR"


@pytest.mark.parametrize("value", ["AED", "QAR", {"code": "AED"}])
def test_assert_sar_currency_blocks_unverified_currency(value):
    with pytest.raises(ManualSendRefused) as exc_info:
        _assert_sar_currency({"currency": value})

    exc = exc_info.value
    assert exc.code == "unsupported_invoice_currency"
    assert exc.extra["qoyod_write_performed"] is False
    assert exc.extra["currency"] in {"AED", "QAR"}
