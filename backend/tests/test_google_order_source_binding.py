from integrations_control_center.google_order_source_binding import bound_salla_order_date


def row():
    return {"user_id": "owner", "order_id": "42", "order_number": "77", "raw_by_source": {"salla_direct": {
        "id": 42, "reference_id": 77, "date": {"date": "2026-08-14T21:30:00Z"},
        "created_at": "2026-08-15T00:30:00+03:00"}}}


def test_exact_owner_ids_and_agreeing_source_instants():
    result = bound_salla_order_date(row(), owner_id="owner")
    assert result == {"status": "observed_bound_source_instant", "business_date_riyadh": "2026-08-15",
        "coverage_complete": False, "source_snapshot_immutable": False}


def test_cross_owner_and_mismatched_id_are_never_bound():
    r = row(); assert bound_salla_order_date(r, owner_id="other")["status"] == "identity_unverified"
    r["raw_by_source"]["salla_direct"]["id"] = 43
    assert bound_salla_order_date(r, owner_id="owner")["status"] == "identity_unverified"
    r = row(); r["raw_by_source"]["salla_direct"]["reference_id"] = 78
    assert bound_salla_order_date(r, owner_id="owner")["status"] == "identity_unverified"


def test_missing_identity_bool_or_fallback_not_counted():
    for change in (lambda r: r.pop("order_id"), lambda r: r["raw_by_source"]["salla_direct"].pop("reference_id"),
                   lambda r: r["raw_by_source"]["salla_direct"].update(id=True)):
        r = row(); change(r)
        assert bound_salla_order_date(r, owner_id="owner")["status"] == "identity_unverified"


def test_conflict_or_unsupported_date_does_not_fall_back():
    for value in ("2026-08-15T00:35:00+03:00", "2026-08-15", "2026-08-15T00:30:00"):
        r = row(); r["raw_by_source"]["salla_direct"]["created_at"] = value
        assert bound_salla_order_date(r, owner_id="owner")["status"] == "timestamp_unverified"


def test_single_explicit_instant_is_observation_not_coverage_proof():
    r = row(); r["raw_by_source"]["salla_direct"].pop("created_at")
    result = bound_salla_order_date(r, owner_id="owner")
    assert result["business_date_riyadh"] == "2026-08-15" and result["coverage_complete"] is False


def test_date_object_with_unverified_timezone_metadata_is_not_silently_ignored():
    r = row(); r["raw_by_source"]["salla_direct"]["date"]["timezone"] = "Asia/Riyadh"
    assert bound_salla_order_date(r, owner_id="owner")["status"] == "timestamp_unverified"


def test_zero_identity_rejected_and_leading_zeros_not_equated():
    for zero in (0, '0', '000'):
        r = row(); r['order_id'] = zero; r['raw_by_source']['salla_direct']['id'] = zero
        assert bound_salla_order_date(r, owner_id='owner')['status'] == 'identity_unverified'
    r = row(); r['order_id'] = '042'
    assert bound_salla_order_date(r, owner_id='owner')['status'] == 'identity_unverified'
