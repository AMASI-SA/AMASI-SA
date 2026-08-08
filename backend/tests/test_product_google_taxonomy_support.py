from product_google_taxonomy_support import (
    extract_google_taxonomy,
    google_taxonomy_matches,
    taxonomy_candidates,
    taxonomy_sync_state,
)


def test_extracts_live_salla_google_taxonomy_field():
    payload = {"status": 200, "data": {"id": 10, "google_taxonomy": "Apparel & Accessories > Jewelry > Necklaces"}}
    assert extract_google_taxonomy(payload) == "Apparel & Accessories > Jewelry > Necklaces"


def test_taxonomy_candidates_tolerate_future_object_shape():
    value = {"id": 188, "path": "Apparel & Accessories > Jewelry > Necklaces"}
    assert taxonomy_candidates(value) == {
        "188",
        "apparel & accessories > jewelry > necklaces",
    }


def test_readback_verification_matches_normalized_path():
    expected = " Apparel & Accessories  >  Jewelry > Necklaces "
    payload = {"data": {"google_taxonomy": "apparel & accessories > jewelry > necklaces"}}
    assert google_taxonomy_matches(expected, payload) is True


def test_readback_verification_fails_closed_on_missing_or_different_value():
    expected = "Apparel & Accessories > Jewelry > Necklaces"
    assert google_taxonomy_matches(expected, {"data": {"google_taxonomy": None}}) is False
    assert google_taxonomy_matches(expected, {"data": {"google_taxonomy": "Apparel & Accessories > Jewelry > Rings"}}) is False


def test_sync_state_never_reports_synced_without_matching_readback():
    state = taxonomy_sync_state(
        expected="Apparel & Accessories > Jewelry > Necklaces",
        salla_product_payload={"data": {"google_taxonomy": None}},
        attempted_write=True,
    )
    assert state == {
        "salla_sync_status": "failed",
        "expected_google_taxonomy": "Apparel & Accessories > Jewelry > Necklaces",
        "actual_google_taxonomy": None,
        "attempted_write": True,
        "verified": False,
    }
