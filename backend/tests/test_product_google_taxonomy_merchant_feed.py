from product_google_taxonomy_merchant_feed import (
    match_supplemental_feed_rows,
    supplemental_feed_csv,
)


def test_feed_matches_exact_merchant_offer_id_by_sku_or_salla_id():
    approved = [
        {
            "mezan_product_id": "mpv2_1",
            "salla_product_id": "111",
            "sku": "AMASI-ONE",
            "google_category_id": "188",
        },
        {
            "mezan_product_id": "mpv2_2",
            "salla_product_id": "222",
            "sku": "",
            "google_category": "201",
        },
    ]
    merchant = [
        {"offerId": "AMASI-ONE"},
        {"offerId": "222"},
        {"offerId": "not-approved"},
    ]

    result = match_supplemental_feed_rows(approved, merchant)

    assert result["matched"] == [
        {"id": "222", "google_product_category": "201"},
        {"id": "AMASI-ONE", "google_product_category": "188"},
    ]
    assert result["unmatched"] == []
    assert result["merchant_products"] == 3


def test_feed_never_guesses_unmatched_or_duplicates_offer_ids():
    approved = [
        {
            "mezan_product_id": "mpv2_1",
            "salla_product_id": "111",
            "sku": "missing",
            "google_category_id": "188",
        },
        {
            "mezan_product_id": "mpv2_2",
            "salla_product_id": "222",
            "sku": "same",
            "google_category_id": "201",
        },
        {
            "mezan_product_id": "mpv2_3",
            "salla_product_id": "333",
            "sku": "same",
            "google_category_id": "202",
        },
    ]

    result = match_supplemental_feed_rows(approved, [{"offerId": "same"}])

    assert result["matched"] == [
        {"id": "same", "google_product_category": "201"},
    ]
    assert len(result["unmatched"]) == 2


def test_csv_is_utf8_bom_and_has_only_supplemental_columns():
    value = supplemental_feed_csv([
        {"id": "sku-1", "google_product_category": "188"},
    ])

    assert value == "\ufeffid,google_product_category\nsku-1,188\n"
