from integrations.qoyod_manual.send import _resolve_current_payment_method


def test_prefers_canonical_method_over_stale_waiting_state():
    canon = {"payment_method": "tabby_installment"}
    facts = {"payment_method": "waiting"}

    assert _resolve_current_payment_method(canon, facts) == "tabby_installment"


def test_keeps_real_order_engine_payment_method():
    canon = {"payment_method": "mada"}
    facts = {"payment_method": "tamara_installment"}

    assert _resolve_current_payment_method(canon, facts) == "tamara_installment"


def test_uses_canonical_method_when_engine_method_is_missing():
    canon = {"payment_method": "tabby_installment"}
    facts = {"payment_method": None}

    assert _resolve_current_payment_method(canon, facts) == "tabby_installment"
