"""Minimal Merchant DataSources v1 projection; no URLs, credentials or mapping.

Contract: developers.google.com/merchant/api/reference/rest/datasources_v1/
accounts.dataSources and accounts.dataSources/list. Only explicitly requested
fields are accepted. Source ownership never establishes publisher/product ID.
"""
from __future__ import annotations

import re

API_ROOT = "https://merchantapi.googleapis.com/datasources/v1/accounts/"
PAGE_SIZE = 100
MAX_PAGES = 5
MAX_SOURCES = 500
MAX_PAGE_BYTES = 256 * 1024
MAX_TOTAL_BYTES = 1024 * 1024
READ_SECONDS = 45
PAGE_SECONDS = 10
SOURCE_KINDS = {
    "primaryProductDataSource": "primary_product",
    "supplementalProductDataSource": "supplemental_product",
    "localInventoryDataSource": "local_inventory",
    "regionalInventoryDataSource": "regional_inventory",
    "promotionDataSource": "promotion",
    "productReviewDataSource": "product_review",
    "merchantReviewDataSource": "merchant_review",
}
# Do not request free-form labels, filenames, fetchSettings or source rules.
# The two review-source messages are documented empty messages; future fields
# are rejected by the decoder instead of being exposed as configuration.
DATA_SOURCE_FIELDS = (
    "dataSources(name,dataSourceId,input,"
    "primaryProductDataSource(feedLabel,contentLanguage),"
    "supplementalProductDataSource(feedLabel,contentLanguage),"
    "localInventoryDataSource(feedLabel,contentLanguage),"
    "regionalInventoryDataSource(feedLabel,contentLanguage),"
    "promotionDataSource(contentLanguage),productReviewDataSource,"
    "merchantReviewDataSource,fileInput(fileInputType)),nextPageToken"
)


class DataSourceProofError(RuntimeError):
    def __init__(self, code="google_merchant_data_sources_unproved"):
        self.code = code
        super().__init__(code)


def canonical_id(value):
    if (type(value) is not str or not re.fullmatch(r"[1-9][0-9]{0,18}", value)
        or int(value) > 9223372036854775807):
        raise DataSourceProofError("google_merchant_data_sources_identity_invalid")
    return value


def page_token(value):
    if value is None or value == "":
        return None
    if (type(value) is not str or len(value) > 4096
        or any(ord(char) < 32 or ord(char) == 127 for char in value)):
        raise DataSourceProofError("google_merchant_data_sources_pagination_unproved")
    return value


def data_source_request(merchant_id, token=None):
    """No caller URL, method, fields mask, account path or page-size override."""
    merchant_id = canonical_id(merchant_id)
    params = {"pageSize": PAGE_SIZE, "fields": DATA_SOURCE_FIELDS}
    token = page_token(token)
    if token is not None:
        params["pageToken"] = token
    return API_ROOT + merchant_id + "/dataSources", params


def _source(raw, merchant_id):
    def refuse():
        raise DataSourceProofError()
    allowed = {"name", "dataSourceId", "input", "fileInput", *SOURCE_KINDS}
    if not isinstance(raw, dict) or set(raw) - allowed:
        refuse()
    identity = canonical_id(raw.get("dataSourceId"))
    resource = f"accounts/{merchant_id}/dataSources/{identity}"
    if (raw.get("name") != resource or type(raw.get("input")) is not str
        or raw["input"] not in {"API", "FILE", "UI", "AUTOFEED"}):
        refuse()
    kinds = set(raw) & SOURCE_KINDS.keys()
    if len(kinds) != 1:
        refuse()
    kind = next(iter(kinds))
    detail = raw[kind]
    detail_keys = ({"contentLanguage"} if kind == "promotionDataSource" else set()
        if kind in {"productReviewDataSource", "merchantReviewDataSource"}
        else {"feedLabel", "contentLanguage"})
    if not isinstance(detail, dict) or set(detail) - detail_keys:
        refuse()
    feed, language = detail.get("feedLabel"), detail.get("contentLanguage")
    if "feedLabel" in detail and (type(feed) is not str or not re.fullmatch(r"[A-Z0-9_-]{1,20}", feed)):
        refuse()
    if "contentLanguage" in detail and (type(language) is not str or not re.fullmatch(r"[a-z]{2}", language)):
        refuse()
    if kind in {"primaryProductDataSource", "supplementalProductDataSource", "localInventoryDataSource", "regionalInventoryDataSource"}:
        if (feed is None) != (language is None):
            refuse()
        if (raw["input"] == "FILE" or kind in {"localInventoryDataSource", "regionalInventoryDataSource"}) and feed is None:
            refuse()
    if kind == "promotionDataSource" and language is None:
        refuse()
    if kind == "localInventoryDataSource" and raw["input"] != "FILE":
        refuse()
    file_kind = None
    if "fileInput" in raw:
        file_input = raw["fileInput"]
        if not isinstance(file_input, dict) or set(file_input) - {"fileInputType"}:
            refuse()
        if "fileInputType" in file_input:
            file_kind = file_input["fileInputType"]
            if (raw["input"] != "FILE" or type(file_kind) is not str
                or file_kind not in {"FETCH", "UPLOAD", "GOOGLE_SHEETS"}):
                refuse()
    return {"resource_name": resource, "merchant_id": merchant_id, "data_source_id": identity,
        "source_type": SOURCE_KINDS[kind], "input_type": raw["input"],
        "file_input_type": file_kind, "feed_label": feed, "language_code": language}


def parse_data_source_page(payload, merchant_id):
    merchant_id = canonical_id(merchant_id)
    if not isinstance(payload, dict) or set(payload) - {"dataSources", "nextPageToken"}:
        raise DataSourceProofError()
    values = payload.get("dataSources", [])
    if not isinstance(values, list) or len(values) > PAGE_SIZE:
        raise DataSourceProofError("google_merchant_data_sources_bound_exceeded")
    rows = [_source(value, merchant_id) for value in values]
    if len({row["resource_name"] for row in rows}) != len(rows):
        raise DataSourceProofError("google_merchant_data_sources_duplicate_identity")
    return rows, page_token(payload.get("nextPageToken"))
