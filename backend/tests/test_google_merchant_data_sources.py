"""Safe native data-source projection: synthetic official-v1 response shapes."""
import pytest

from integrations_control_center.google_merchant_data_sources_proof import (
    DataSourceProofError, DATA_SOURCE_FIELDS, parse_data_source_page,
)


def source(kind="primaryProductDataSource", *, source_id="18", input_type="FILE"):
    return {"name": f"accounts/123/dataSources/{source_id}", "dataSourceId": source_id,
        "input": input_type, kind: {"feedLabel": "SA", "contentLanguage": "ar"},
        **({"fileInput": {"fileInputType": "FETCH"}} if input_type == "FILE" else {})}


def test_primary_file_source_has_native_identity_without_publisher_or_product_inference():
    rows, token = parse_data_source_page({"dataSources": [source()]}, "123")
    assert token is None
    assert rows == [{"resource_name": "accounts/123/dataSources/18", "merchant_id": "123",
        "data_source_id": "18", "source_type": "primary_product", "input_type": "FILE",
        "file_input_type": "FETCH", "feed_label": "SA", "language_code": "ar"}]
    assert not any(word in DATA_SOURCE_FIELDS for word in ("fetchSettings", "fetchUri", "password", "username", "displayName", "fileName"))


def test_api_source_without_feed_restriction_remains_unspecified():
    item = source(input_type="API"); item["primaryProductDataSource"] = {}
    rows, _ = parse_data_source_page({"dataSources": [item]}, "123")
    assert rows[0]["feed_label"] is None and rows[0]["language_code"] is None
    assert rows[0]["file_input_type"] is None


@pytest.mark.parametrize("kind,expected", [
    ("supplementalProductDataSource", "supplemental_product"),
    ("localInventoryDataSource", "local_inventory"),
    ("regionalInventoryDataSource", "regional_inventory"),
    ("promotionDataSource", "promotion"),
    ("productReviewDataSource", "product_review"),
    ("merchantReviewDataSource", "merchant_review"),
])
def test_all_documented_source_kinds_preserved_without_loading_whole_configuration(kind, expected):
    item = source(kind)
    if kind == "promotionDataSource": item[kind] = {"contentLanguage": "ar"}
    elif kind.endswith("ReviewDataSource"): item[kind] = {}
    rows, _ = parse_data_source_page({"dataSources": [item]}, "123")
    assert rows[0]["source_type"] == expected


@pytest.mark.parametrize("field,value", [
    ("name", "accounts/999/dataSources/18"), ("name", "accounts/123/dataSources/018"),
    ("dataSourceId", "19"), ("dataSourceId", 18), ("input", "NEW_KIND"),
    ("input", None), ("input", []), ("input", {}),
    ("fileInput", {"fileInputType": []}), ("fileInput", {"fileInputType": "FILE_INPUT_TYPE_UNSPECIFIED"}),
    ("fileInput", {"fileInputType": "FETCH", "fetchSettings": {"password": "PRIVATE"}}),
    ("displayName", "PRIVATE"), ("primaryProductDataSource", {"feedLabel": "SA"}),
    ("primaryProductDataSource", {"feedLabel": "SA", "contentLanguage": "ar", "defaultRule": {}}),
    ("primaryProductDataSource", {"feedLabel": "SA_TEST", "contentLanguage": "ar"}),
])
def test_foreign_malformed_and_unrequested_configuration_is_refused_without_echo(field, value):
    item = source(); item[field] = value
    with pytest.raises(DataSourceProofError) as error:
        parse_data_source_page({"dataSources": [item]}, "123")
    assert "PRIVATE" not in str(error.value)


def test_duplicate_identity_and_multiple_native_source_kinds_fail_closed():
    with pytest.raises(DataSourceProofError): parse_data_source_page({"dataSources": [source(), source()]}, "123")
    item = source(); item["merchantReviewDataSource"] = {}
    with pytest.raises(DataSourceProofError): parse_data_source_page({"dataSources": [item]}, "123")


@pytest.mark.parametrize("payload", [None, [], {"dataSources": None}, {"dataSources": [], "unexpected": "PRIVATE"},
    {"dataSources": [], "nextPageToken": True}, {"nextPageToken": "bad\nvalue"}, {"nextPageToken": "x" * 4097}])
def test_invalid_page_shape_and_tokens_are_not_treated_as_complete(payload):
    with pytest.raises(DataSourceProofError): parse_data_source_page(payload, "123")


def test_empty_proto_list_and_final_empty_token_are_complete():
    assert parse_data_source_page({}, "123") == ([], None)
    assert parse_data_source_page({"dataSources": [], "nextPageToken": ""}, "123") == ([], None)


@pytest.mark.parametrize("merchant", ["0", "01", "123/other", "123?fields=*", "9223372036854775808", 123, True])
def test_canonical_account_only(merchant):
    with pytest.raises(DataSourceProofError): parse_data_source_page({}, merchant)


def test_non_file_supplemental_scope_must_be_unset_per_native_contract():
    item = source("supplementalProductDataSource", input_type="API")
    with pytest.raises(DataSourceProofError): parse_data_source_page({"dataSources": [item]}, "123")
    item["supplementalProductDataSource"] = {}
    assert parse_data_source_page({"dataSources": [item]}, "123")[0][0]["feed_label"] is None
