"""Native v1 observations with in-memory streams; never credentials or Google."""
import asyncio
import base64
from copy import deepcopy
from dataclasses import FrozenInstanceError
from datetime import datetime, timedelta, timezone
import json

import httpx
import pytest

from integrations_control_center import google_merchant_product_observation as module
from integrations_control_center.google_merchant_product_observation import (
    MerchantObservationError, MerchantObservationScope,
    read_merchant_product_observation,
)

SCOPE = MerchantObservationScope("owner-1", "5052346701", "123")
STAMP = datetime(2026, 10, 29, 12, tzinfo=timezone.utc)


def product(offer="SKU1", *, language="en", feed="SA", local=False):
    native = ("local~" if local else "") + language + "~" + feed + "~" + offer
    return {"name": "accounts/123/products/" + native,
            "base64EncodedName": "accounts/123/products/" + base64.urlsafe_b64encode(native.encode()).decode().rstrip("="),
            "offerId": offer, "contentLanguage": language, "feedLabel": feed,
            "legacyLocal": local, "dataSource": "accounts/123/dataSources/456",
            "versionNumber": "100", "archived": False,
            "productAttributes": {"link": "https://store.example/product?variant=1#details",
                "canonicalLink": "https://store.example/product", "mobileLink": "https://m.store.example/product?variant=1",
                "adsRedirect": "https://tracking.example/click?dest=product1", "itemGroupId": "parent-1"}}


class Stream(httpx.AsyncByteStream):
    def __init__(self, body, *, chunk=17, fail=False, delay=0):
        self.body, self.chunk, self.fail, self.delay = body, chunk, fail, delay
        self.delivered = 0

    async def __aiter__(self):
        if self.delay:
            await asyncio.sleep(self.delay)
        for offset in range(0, len(self.body), self.chunk):
            part = self.body[offset:offset + self.chunk]
            self.delivered += len(part)
            yield part
        if self.fail:
            raise RuntimeError("PRIVATE_PROVIDER_SENTINEL")


async def observe(pages, *, scope=SCOPE, clock=None, status=200, headers=None, stream_factory=None):
    calls = []
    def respond(request):
        calls.append(request)
        index = len(calls) - 1
        assert index < len(pages), "unexpected request/retry"
        value = pages[index]
        body = value if isinstance(value, bytes) else json.dumps(value).encode()
        stream = stream_factory(body) if stream_factory else Stream(body)
        return httpx.Response(status, headers=headers or {"Content-Type": "application/json"}, stream=stream)
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond), follow_redirects=True) as transport:
        result = await read_merchant_product_observation(transport, scope=scope, clock=clock or (lambda: STAMP))
    return result, calls


@pytest.mark.asyncio
async def test_complete_short_and_empty_pages_preserve_independent_native_identities_and_links():
    rows = [product("sku/123"), product("sku/123", language="ar"), product("sku/123", feed="QA")]
    result, calls = await observe([
        {"products": rows[:1], "nextPageToken": "page-two"},
        {"products": [], "nextPageToken": "page-three"}, {"products": rows[1:]}])
    assert len(result.products) == 3 and result.pages_read == 3
    assert {(r.language_code, r.feed_label) for r in result.products} == {("en", "SA"), ("ar", "SA"), ("en", "QA")}
    for row in result.products:
        assert row.offer_id == "sku/123"
        assert row.link.endswith("?variant=1#details")
        assert row.canonical_link == "https://store.example/product"
        assert row.mobile_link == "https://m.store.example/product?variant=1"
        assert row.ads_redirect == "https://tracking.example/click?dest=product1"
        assert row.item_group_id == "parent-1"
    assert [str(c.url).split("?", 1)[0] for c in calls] == ["https://merchantapi.googleapis.com/products/v1/accounts/123/products"] * 3
    assert [c.method for c in calls] == ["GET"] * 3
    assert [c.content for c in calls] == [b""] * 3
    assert [dict(c.url.params) for c in calls] == [
        {"pageSize": "250"}, {"pageSize": "250", "pageToken": "page-two"}, {"pageSize": "250", "pageToken": "page-three"}]
    output = result.as_public_dict()
    assert output["complete"] is True and output["atomic_snapshot"] is False
    assert output["mapping_status"] == "unverified" and output["provider_writes"] == 0
    assert all(output[k] is False for k in ("store_product_mapping_verified", "google_ads_channel_verified", "destination_verified", "category_membership_verified", "attribution_verified", "execution_authorized"))
    assert "pageToken" not in json.dumps(output)
    with pytest.raises(FrozenInstanceError):
        result.products[0].link = "https://changed.example/"


@pytest.mark.asyncio
async def test_semantic_digest_excludes_time_page_order_and_partition_but_binds_scope_and_destination():
    a, b = product("A"), product("B")
    first, _ = await observe([{"products": [a, b]}])
    later, _ = await observe([{"products": [b], "nextPageToken": "next"}, {"products": [a]}], clock=lambda: STAMP + timedelta(hours=1))
    assert first.semantic_digest == later.semantic_digest
    assert first.observed_at != later.observed_at
    a["productAttributes"]["link"] += "-changed"
    changed, _ = await observe([{"products": [a, b]}])
    assert changed.semantic_digest != first.semantic_digest
    changed_scope, _ = await observe([{"products": [a, b]}], scope=MerchantObservationScope("owner-2", "5052346701", "123"))
    assert changed_scope.semantic_digest != changed.semantic_digest


@pytest.mark.asyncio
@pytest.mark.parametrize("offer", ["sku/123", "sku~123", "sku%123", "منتج-أزرق", "SKU A", "SKUa"])
async def test_native_special_offers_are_compared_from_explicit_fields_without_sku_inference(offer):
    row = product(offer)
    result, _ = await observe([{"products": [row]}])
    assert result.products[0].offer_id == offer
    assert result.products[0].resource_name == row["name"]
    assert result.products[0].base64_encoded_name == row["base64EncodedName"]


@pytest.mark.asyncio
async def test_local_and_standard_are_distinct_without_claiming_ads_online_channel():
    standard, local = product(), product(local=True)
    del standard["legacyLocal"]
    del standard["archived"]
    del standard["base64EncodedName"]
    result, _ = await observe([{"products": [standard, local]}])
    assert {p.legacy_local for p in result.products} == {False, True}
    assert result.as_public_dict()["google_ads_channel_verified"] is False


@pytest.mark.asyncio
async def test_absent_optional_destinations_do_not_become_mapping_or_eligibility_proof():
    row = product(); del row["productAttributes"]
    result, _ = await observe([{"products": [row]}])
    assert result.products[0].link is None and result.products[0].item_group_id is None
    assert result.as_public_dict()["store_product_mapping_verified"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize("version", ["-9223372036854775808", "-1", "0", "1", "9223372036854775807"])
async def test_native_signed_int64_boundaries_remain_observed_metadata(version):
    row = product(); row["versionNumber"] = version
    result, _ = await observe([{"products": [row]}])
    assert result.products[0].version_number == version
    assert result.observed_at == STAMP.isoformat()
    assert result.as_public_dict()["execution_authorized"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize("version", ["-9223372036854775809", "9223372036854775808", "99999999999999999999", "01", "+1", "-0", "00", "-01", "1e3", "1.0"])
async def test_invalid_int64_range_or_noncanonical_representation_refuses_complete_observation(version):
    row = product(); row["versionNumber"] = version
    with pytest.raises(MerchantObservationError):
        await observe([{"products": [product("good"), row]}])


@pytest.mark.asyncio
@pytest.mark.parametrize("patch", [
    {"name": "accounts/999/products/en~SA~SKU1"},
    {"name": "accounts/123/products/online~en~SA~SKU1"},
    {"name": "accounts/123/products/local~en~SA~SKU1"},
    {"base64EncodedName": "accounts/123/products/ZW5-VVN-c2t1LzEyMw"},
    {"base64EncodedName": "accounts/999/products/ZW5-U0F-U0tVMQ"},
    {"dataSource": "accounts/999/dataSources/456"}, {"dataSource": "accounts/123/dataSources/0"},
    {"offerId": "sku1"}, {"offerId": ""}, {"offerId": None}, {"offerId": "SKU1\n"}, {"offerId": "\ud800"},
    {"contentLanguage": "EN"}, {"contentLanguage": "en-US"}, {"feedLabel": "sa"}, {"feedLabel": "SA/QA"},
    {"legacyLocal": "false"}, {"legacyLocal": 0}, {"archived": 1},
    {"productAttributes": []}, {"productAttributes": {"itemGroupId": ["parent"]}}, {"versionNumber": 3},
])
async def test_invalid_or_conflicting_native_rows_reject_whole_observation(patch):
    row = product(); row.update(patch)
    with pytest.raises(MerchantObservationError):
        await observe([{"products": [product("good"), row]}])


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", [{}, None, [], "anonymous", 12])
async def test_anonymous_row_never_disappears_from_complete_inventory(bad):
    with pytest.raises(MerchantObservationError):
        await observe([{"products": [product(), bad]}])


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["link", "canonicalLink", "mobileLink", "adsRedirect"])
@pytest.mark.parametrize("bad", ["javascript:alert(1)", "/relative", "https://user:pass@store.example/item", "https://store.example:bad/item", "https://store.example/item\n", "https://store.example\\@other.example/item"])
async def test_invalid_destinations_are_not_coerced_or_fetched(field, bad):
    row = product(); row["productAttributes"][field] = bad
    with pytest.raises(MerchantObservationError):
        await observe([{"products": [row]}])


@pytest.mark.asyncio
async def test_duplicate_identity_across_pages_cannot_be_deduplicated_even_if_identical():
    with pytest.raises(MerchantObservationError, match="duplicate_native_identity"):
        await observe([{"products": [product()], "nextPageToken": "again"}, {"products": [product()]}])


@pytest.mark.asyncio
@pytest.mark.parametrize("pages", [
    [{"nextPageToken": "A"}, {"nextPageToken": "A"}],
    [{"nextPageToken": "A"}, {"nextPageToken": "B"}, {"nextPageToken": "A"}],
    [{"nextPageToken": ""}], [{"nextPageToken": None}], [{"nextPageToken": []}],
    [{"nextPageToken": "x" * 4097}], [{"products": {"SKU1": product()}}],
])
async def test_invalid_pagination_never_reports_partial_success(pages):
    with pytest.raises(MerchantObservationError):
        await observe(pages)


@pytest.mark.asyncio
async def test_page_and_total_row_caps_are_hard_limits(monkeypatch):
    monkeypatch.setattr(module, "MAX_PAGES", 2)
    with pytest.raises(MerchantObservationError, match="observation_limit"):
        await observe([{"nextPageToken": "A"}, {"nextPageToken": "B"}])
    monkeypatch.setattr(module, "MAX_PRODUCTS", 1)
    with pytest.raises(MerchantObservationError, match="observation_limit"):
        await observe([{"products": [product("A")], "nextPageToken": "next"}, {"products": [product("B")]}])
    monkeypatch.setattr(module, "PAGE_SIZE", 1)
    with pytest.raises(MerchantObservationError, match="observation_limit"):
        await observe([{"products": [product("A"), product("B")]}])


@pytest.mark.asyncio
@pytest.mark.parametrize("body", [b"[]", b"null", b'{"products":[],"products":[]}', b'{"products":[{"offerId":"a","offerId":"b"}]}', b'{"products":NaN}', b'{"products":Infinity}', b'{"products":[]', b'\xff', b'{"unexpected":"shape"}', b'{"error":{"message":"PRIVATE_PROVIDER_SENTINEL"}}'])
async def test_invalid_json_has_only_safe_error_code(body):
    with pytest.raises(MerchantObservationError) as caught:
        await observe([body])
    assert caught.value.code == "google_merchant_invalid_json"
    assert "PRIVATE" not in str(caught.value)


@pytest.mark.asyncio
async def test_byte_limit_stops_stream_before_full_response_is_materialized(monkeypatch):
    monkeypatch.setattr(module, "MAX_PAGE_BYTES", 32)
    streams = []
    def factory(body):
        stream = Stream(body, chunk=8192); streams.append(stream); return stream
    with pytest.raises(MerchantObservationError, match="observation_limit"):
        await observe([b" " * 100000], stream_factory=factory)
    assert streams[0].delivered < 100000


@pytest.mark.asyncio
async def test_total_byte_limit_applies_across_individually_valid_pages(monkeypatch):
    first = json.dumps({"nextPageToken": "A"}).encode()
    monkeypatch.setattr(module, "MAX_TOTAL_BYTES", len(first) + 1)
    with pytest.raises(MerchantObservationError, match="observation_limit"):
        await observe([first, b"{}"])


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [204, 301, 302, 307, 401, 403, 429, 500])
async def test_http_errors_and_redirects_are_never_followed_or_leaked(status):
    with pytest.raises(MerchantObservationError) as caught:
        await observe([b"PRIVATE_PROVIDER_SENTINEL"], status=status, headers={"Content-Type": "application/json", "Location": "https://other.example/secret"})
    assert caught.value.code == "google_merchant_read_rejected"
    assert "PRIVATE" not in str(caught.value) and "other.example" not in str(caught.value)


@pytest.mark.asyncio
@pytest.mark.parametrize("headers", [{"Content-Type": "text/html"}, {"Content-Type": "application/json", "Content-Encoding": "gzip"}])
async def test_response_type_and_compression_are_checked_before_decoding(headers):
    with pytest.raises(MerchantObservationError):
        await observe([b"PRIVATE_PROVIDER_SENTINEL"], headers=headers)


@pytest.mark.asyncio
async def test_stream_failure_and_deadlines_are_safe_and_do_not_return_previous_rows(monkeypatch):
    with pytest.raises(MerchantObservationError, match="observation_unavailable") as caught:
        await observe([{"products": [product()]}], stream_factory=lambda body: Stream(body, fail=True))
    assert "PRIVATE" not in str(caught.value)
    monkeypatch.setattr(module, "MAX_PAGE_SECONDS", 0.01)
    with pytest.raises(MerchantObservationError, match="observation_timeout"):
        await observe([{}], stream_factory=lambda body: Stream(body, delay=0.1))
    monkeypatch.setattr(module, "MAX_PAGE_SECONDS", 1)
    monkeypatch.setattr(module, "MAX_TOTAL_SECONDS", 0.01)
    with pytest.raises(MerchantObservationError, match="observation_timeout"):
        await observe([{}], stream_factory=lambda body: Stream(body, delay=0.1))


@pytest.mark.asyncio
async def test_empty_native_response_is_an_empty_observation_not_owner_or_mapping_proof():
    result, _ = await observe([{}])
    assert result.products == ()
    assert result.as_public_dict()["execution_authorized"] is False
    assert "owner_verified" not in result.as_public_dict()


@pytest.mark.asyncio
async def test_bad_clock_or_scope_is_not_coerced_into_fresh_verified_evidence():
    with pytest.raises(MerchantObservationError, match="scope_unproved"):
        await observe([{}], scope={"verified": True, "merchant_id": "123"})
    with pytest.raises(MerchantObservationError, match="time_unproved"):
        await observe([{}], clock=lambda: STAMP.replace(tzinfo=None))
    stamps = iter([STAMP, STAMP - timedelta(seconds=1)])
    with pytest.raises(MerchantObservationError, match="time_unproved"):
        await observe([{}], clock=lambda: next(stamps))


@pytest.mark.parametrize("owner,ads,merchant", [("owner", "1/../2", "123"), ("owner", "5052346701", "https://other.example"), ("owner", "5052346701", "0"), ("", "5052346701", "123")])
def test_scope_cannot_construct_arbitrary_host_path_or_default_account(owner, ads, merchant):
    with pytest.raises(MerchantObservationError):
        MerchantObservationScope(owner, ads, merchant)
