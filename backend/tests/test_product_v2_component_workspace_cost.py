from component_workspace_cost_compat_routes import _current_cost


def test_current_cost_prefers_active_unit_cost():
    assert _current_cost({"unit_cost": 10, "initial_unit_cost": 8}) == 10


def test_current_cost_reads_legacy_initial_cost():
    assert _current_cost({"unit_cost": None, "initial_unit_cost": 8}) == 8


def test_current_cost_reads_reference_contract():
    assert _current_cost({"reference_cost": {"amount": 7.5}}) == 7.5


def test_zero_cost_is_not_treated_as_missing():
    assert _current_cost({"unit_cost": 0}) == 0
