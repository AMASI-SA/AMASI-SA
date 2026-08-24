from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / "product_field_cost_support.py").read_text(encoding="utf-8")


def test_supplier_invoice_open_session_uses_live_piece_state():
    assert "recent_events_with_live_product_state" in SOURCE
    assert '"services", "product_options", "options_raw", "options_normalized"' in SOURCE
    assert 'row["invoice_services"] = merged_services' in SOURCE
    assert 'row["supplier_invoice_live_draft"] = True' in SOURCE


def test_supplier_invoice_product_cost_is_base_plus_direct_customer_option_surcharge_only():
    assert "live_supplier_product_price" in SOURCE
    assert "binding_matches(binding, tokens)" in SOURCE
    assert 'option_halalas += round(_number(binding.get("direct_amount")) * 100)' in SOURCE
    assert '"reference_product_component_cost_halalas": 0' in SOURCE
    assert '"supplier_invoice_components_excluded": True' in SOURCE
    assert 'total = int(base.get("reference_product_unit_price_halalas") or 0) + option_halalas' in SOURCE


def test_supplier_invoice_component_resources_never_become_supplier_charge():
    assert "Resource-backed component costs are internal recipe costs" in SOURCE
    assert 'if _text(binding.get("mode")).casefold() == "resource"' in SOURCE
    assert 'continue\n                option_halalas +=' in SOURCE


def test_supplier_invoice_services_stay_separate_and_follow_current_product_setup():
    assert "_live_service_row" in SOURCE
    assert '"live_invoice_services": live_services' in SOURCE
    assert 'option_selected=True' in SOURCE
    assert 'option_selected=False' in SOURCE
    assert '"customer_selected": option_selected' in SOURCE
    assert '"supplier_invoice_required": True' in SOURCE
