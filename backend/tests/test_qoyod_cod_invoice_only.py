from pathlib import Path

from integrations.qoyod.payment_methods import is_cod_family


def test_cod_aliases_are_detected():
    assert is_cod_family("cod")
    assert is_cod_family("cash_on_delivery")
    assert is_cod_family("الدفع عند الاستلام")
    assert is_cod_family("نقد عند الاستلام")


def test_cod_branch_is_before_payment_post():
    source = Path(
        "integrations/qoyod_manual/send.py"
    ).read_text(encoding="utf-8")

    cod_branch = source.index(
        "# ── COD: invoice only, no payment"
    )
    payment_post = source.index(
        "# ── 5) POST invoice payment"
    )

    assert cod_branch < payment_post
    assert '"invoice_only": True' in source
    assert '"payment_amount": 0.0' in source


def test_cod_does_not_require_payment_account():
    source = Path(
        "integrations/qoyod_manual/send.py"
    ).read_text(encoding="utf-8")

    assert "if not is_cod:" in source
    assert "and not is_cod" in source
    assert 'unpaid_status="unpaid"' in source
