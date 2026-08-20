from preparation_pdf_card_readability_overlay import _wrap_value


def test_long_customer_option_wraps_to_second_visual_row():
    rows = _wrap_value("هل تريد تطريز الاسم على الدقلة نعم اضافة شماغ مجاني مع الدقلة")
    assert len(rows) == 2
    assert "..." not in " ".join(rows)
    assert "شماغ" in rows[1]


def test_short_customer_option_stays_one_row():
    assert _wrap_value("12 سنه") == ["12 سنه"]


def test_empty_value_is_not_rendered():
    assert _wrap_value("   ") == []
