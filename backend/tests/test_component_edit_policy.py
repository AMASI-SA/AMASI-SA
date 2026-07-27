from component_edit_policy import component_cost_metadata


def test_stock_component_starts_with_manual_initial_cost():
    result = component_cost_metadata(track_inventory=True, amount=12.5)
    assert result["unit_cost"] == 12.5
    assert result["initial_unit_cost"] == 12.5
    assert result["cost_source"] == "manual_initial"
    assert result["cost_authoritative"] is False
    assert result["purchase_cost_pending"] is True


def test_purchase_invoice_becomes_authoritative_for_stock_component():
    result = component_cost_metadata(track_inventory=True, amount=12.5, purchase_cost=10.75)
    assert result["unit_cost"] == 10.75
    assert result["initial_unit_cost"] == 12.5
    assert result["cost_source"] == "purchase_invoice"
    assert result["cost_authoritative"] is True
    assert result["purchase_cost_pending"] is False


def test_service_cost_remains_manually_maintained():
    result = component_cost_metadata(track_inventory=False, amount=8.0)
    assert result["unit_cost"] == 8.0
    assert result["initial_unit_cost"] is None
    assert result["cost_source"] == "manual_service"
    assert result["cost_authoritative"] is True
