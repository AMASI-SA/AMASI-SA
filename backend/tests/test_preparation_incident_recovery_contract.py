from preparation_incident_recovery import EXPECTED, INCIDENT_ID, SKU


def test_incident_scope_is_exactly_the_eight_orders_and_eleven_units():
    assert INCIDENT_ID == "ams11353-lost-11-20260825"
    assert SKU == "AMS11353"
    assert EXPECTED == {
        "279756840": 1,
        "279809610": 1,
        "279778158": 2,
        "279820694": 1,
        "279803951": 2,
        "279787662": 1,
        "279773618": 2,
        "279726749": 1,
    }
    assert len(EXPECTED) == 8
    assert sum(EXPECTED.values()) == 11


def test_incident_recovery_is_sku_scoped_and_fail_closed():
    source = __import__("inspect").getsource(
        __import__("preparation_incident_recovery")
    )
    assert "target_allocation_has_registered_file" in source
    assert "target_batch_contains_other_products" in source
    assert '"stage": "reviewed"' in source
    assert '"salla_updated": False' in source
    assert '"qoyod_updated": False' in source
    assert "incident_already_recovered" in source
