from product_category_publish_support import normalize_category_ids


def test_category_ids_are_published_as_integers():
    assert normalize_category_ids(["1682633260", "1285382580"]) == [1682633260, 1285382580]


def test_category_ids_accept_objects_and_remove_duplicates():
    assert normalize_category_ids([{"id": "1682633260"}, 1682633260, "1285382580"]) == [1682633260, 1285382580]


def test_category_ids_reject_non_numeric_values():
    try:
        normalize_category_ids(["not-a-category"])
    except ValueError as exc:
        assert "invalid category id" in str(exc)
    else:
        raise AssertionError("expected invalid category id")
