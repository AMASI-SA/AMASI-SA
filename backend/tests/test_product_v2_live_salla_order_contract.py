from product_v2_creation_order_routes import _salla_rows


def test_salla_rows_preserve_remote_order():
    response = {"data": [{"id": 30}, {"id": 20}, {"id": 10}]}
    assert [row["id"] for row in _salla_rows(response)] == [30, 20, 10]


def test_salla_rows_ignore_malformed_entries_without_reordering():
    response = {"data": [{"id": 30}, None, {"id": 10}]}
    assert [row["id"] for row in _salla_rows(response)] == [30, 10]
