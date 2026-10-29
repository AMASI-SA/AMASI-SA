import pytest
from integrations_control_center.google_live_category_proof import checked_page, safe_customer_url, canonical_id


def page():
    return {"success": True, "status": 200, "data": [{"id": 1}], "pagination": {"currentPage": 1, "perPage": 60, "count": 1, "total": 1, "totalPages": 1}}


def test_actual_server_page_size_can_differ_from_requested_fifty():
    rows, marker = checked_page(page(), 1)
    assert rows == [{"id": 1}] and marker == (1, 60, 1)


@pytest.mark.parametrize("patch", [{"currentPage": 2}, {"totalPages": 2}, {"total": 2}, {"count": 0}, {"perPage": True}])
def test_invalid_pagination_not_complete(patch):
    response = page()
    response["pagination"].update(patch)
    with pytest.raises(ValueError):
        checked_page(response, 1)


@pytest.mark.parametrize("value", ["01", "0", "1/other", True, " 1", "1?x=2"])
def test_ids_cannot_change_fixed_path(value):
    with pytest.raises(ValueError):
        canonical_id(value)


@pytest.mark.parametrize("value", ["https://evil.test/category", "https://store.test.evil.test/a", "https://user@store.test/a", "https://store.test/a#x", "http://store.test/a"])
def test_customer_url_is_owned_https_only(value):
    with pytest.raises(ValueError):
        safe_customer_url(value, "https://store.test")


def test_url_is_validated_but_never_network_destination():
    assert safe_customer_url("https://store.test/a", "https://store.test") == "https://store.test/a"
