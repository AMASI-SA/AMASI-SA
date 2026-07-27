from types import SimpleNamespace

from product_field_cost_support import (
    _field_value_is_filled,
    normalize_custom_fields,
    readable_variant_label,
)


def test_custom_text_field_gets_cost_identity_and_type():
    fields = normalize_custom_fields([
        {"id": 77, "name": "الاسم", "type": "text", "required": True},
        {"id": 78, "name": "ملاحظات", "type": "textarea"},
    ])
    assert fields[0]["cost_subject_id"] == "field:77"
    assert fields[0]["cost_value_id"] == "filled"
    assert fields[0]["type"] == "text"
    assert fields[0]["required"] is True
    assert fields[1]["type"] == "textarea"


def test_variant_label_uses_option_value_names_not_numeric_id():
    options = [
        {
            "id": "10",
            "name": "لون السلسال",
            "values": [
                {"id": "100", "name": "اسود داكن"},
                {"id": "101", "name": "فضي - ذهبي"},
            ],
        }
    ]
    label, selections = readable_variant_label(
        {"id": "1373493425", "name": "1373493425", "selections": [{"option_id": "10", "value_id": "101"}]},
        options,
    )
    assert label == "فضي - ذهبي"
    assert selections == [{"option_name": "لون السلسال", "value_name": "فضي - ذهبي"}]


def test_text_field_cost_applies_only_for_non_empty_submission():
    assert _field_value_is_filled("عرفات") is True
    assert _field_value_is_filled("   ") is False
    assert _field_value_is_filled(None) is False
    assert _field_value_is_filled(["اختيار"]) is True
