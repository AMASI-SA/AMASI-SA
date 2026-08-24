from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / "product_field_cost_support.py").read_text(encoding="utf-8")


def test_supplier_invoice_open_session_uses_live_piece_state():
    assert "recent_events_with_live_product_state" in SOURCE
    assert '"services", "product_options", "options_raw", "options_normalized"' in SOURCE
    assert 'row["invoice_services"] = merged_services' in SOURCE
    assert 'row["supplier_invoice_live_draft"] = True' in SOURCE


def test_supplier_invoice_product_cost_includes_components_and_customer_option_surcharge():
    assert "live_supplier_product_price" in SOURCE
    assert "PRODUCT_RESOURCE_BINDINGS" in SOURCE
    assert "binding_matches(binding, tokens)" in SOURCE
    assert '"reference_product_component_cost_halalas"' in SOURCE
    assert '"reference_product_option_cost_halalas"' in SOURCE
    assert 'if _text(resource.get("kind")).casefold() == "service"' in SOURCE


def test_supplier_invoice_services_stay_separate_and_follow_current_product_setup():
    assert "_live_service_row" in SOURCE
    assert '"live_invoice_services": live_services' in SOURCE
    assert 'option_selected=True' in SOURCE
    assert 'option_selected=False' in SOURCE
    assert '"customer_selected": option_selected' in SOURCE
    assert '"supplier_invoice_required": True' in SOURCE
