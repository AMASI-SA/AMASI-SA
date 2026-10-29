"""Bounded Merchant v1 observations, never store identity or spend authority.

The injected streaming transport must come from the server's separately proved
bound credential session. This module neither acquires credentials nor proves
the caller's authorization. It builds a fixed Google GET URL and returns no
partial observations. See GOOGLE-MERCHANT-OBSERVATION-20261029/CONTRACT.md.
"""
from __future__ import annotations

import asyncio
import base64
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import json
import re
from urllib.parse import urlsplit

PAGE_SIZE = 250
MAX_PAGES = 10
MAX_PRODUCTS = 2000
MAX_PAGE_BYTES = 2 * 1024 * 1024
MAX_TOTAL_BYTES = 8 * 1024 * 1024
MAX_PAGE_SECONDS = 12
MAX_TOTAL_SECONDS = 45
MAX_TOKEN_LENGTH = 4096
PRODUCTS_ORIGIN = "https://merchantapi.googleapis.com"
_ID = re.compile(r"[1-9][0-9]{0,29}\Z")
_LANGUAGE = re.compile(r"[a-z]{2}\Z")
_FEED = re.compile(r"[A-Z0-9_-]{1,20}\Z")


class MerchantObservationError(Exception):
    """Only a fixed code crosses the transport/credential boundary."""

    def __init__(self, code):
        self.code = code
        super().__init__(code)


def _fail(code="google_merchant_observation_unproved"):
    raise MerchantObservationError(code)


def _text(value, maximum, *, empty=False):
    if (not isinstance(value, str) or len(value) > maximum
        or (not value and not empty) or value != value.strip()
        or any(ord(c) < 32 or 127 <= ord(c) <= 159 or 0xD800 <= ord(c) <= 0xDFFF for c in value)):
        _fail()
    return value


def _id(value):
    if not isinstance(value, str) or not _ID.fullmatch(value):
        _fail("google_merchant_scope_unproved")
    return value


@dataclass(frozen=True)
class MerchantObservationScope:
    """Server request binding only; construction is NOT authorization proof."""

    owner_id: str
    ads_customer_id: str
    merchant_id: str

    def __post_init__(self):
        _text(self.owner_id, 128)
        _id(self.ads_customer_id)
        _id(self.merchant_id)


@dataclass(frozen=True)
class NativeMerchantProduct:
    resource_name: str
    base64_encoded_name: str | None
    merchant_id: str
    legacy_local: bool
    language_code: str
    feed_label: str
    offer_id: str
    data_source: str
    archived: bool
    version_number: str | None
    link: str | None
    canonical_link: str | None
    mobile_link: str | None
    ads_redirect: str | None
    item_group_id: str | None

    @property
    def identity(self):
        # Merchant v1's local/standard key is retained. It is not an assertion
        # of Google Ads channel eligibility or a variant/store identity join.
        return (self.merchant_id, self.legacy_local, self.language_code,
                self.feed_label, self.offer_id)


@dataclass(frozen=True)
class MerchantProductObservation:
    scope: MerchantObservationScope
    products: tuple[NativeMerchantProduct, ...]
    started_at: str
    observed_at: str
    semantic_digest: str
    pages_read: int

    def as_public_dict(self):
        return {**asdict(self), "products": [asdict(row) for row in self.products],
                "source": "merchant_processed_products_v1", "complete": True,
                "atomic_snapshot": False, "mapping_status": "unverified",
                "store_product_mapping_verified": False,
                "google_ads_channel_verified": False, "destination_verified": False,
                "category_membership_verified": False, "attribution_verified": False,
                "execution_authorized": False, "provider_writes": 0}


def _optional_link(attributes, key):
    if key not in attributes:
        return None
    value = _text(attributes[key], 8192)
    try:
        url = urlsplit(value)
        if (url.scheme not in {"http", "https"} or not url.hostname
            or url.username is not None or url.password is not None
            or "\\" in value or any(c.isspace() for c in value)):
            _fail()
        # Force malformed ports to be rejected, without changing URLs or
        # stripping query/fragment values that may distinguish variants.
        _ = url.port
    except ValueError:
        _fail()
    return value


def _product(raw, merchant_id):
    if not isinstance(raw, dict):
        _fail()
    offer = _text(raw.get("offerId"), 256)
    language = _text(raw.get("contentLanguage"), 2)
    feed = _text(raw.get("feedLabel"), 20)
    if not _LANGUAGE.fullmatch(language) or not _FEED.fullmatch(feed):
        _fail()
    # Protobuf JSON may omit a false scalar. Validate the default against the
    # independently returned v1 resource name; never accept truthy coercion.
    local = raw.get("legacyLocal", False)
    archived = raw.get("archived", False)
    if type(local) is not bool or type(archived) is not bool:
        _fail()
    native_id = ("local~" if local else "") + language + "~" + feed + "~" + offer
    prefix = "accounts/" + merchant_id + "/products/"
    resource = _text(raw.get("name"), 1024)
    if resource != prefix + native_id:
        _fail("google_merchant_native_identity_unproved")
    encoded = raw.get("base64EncodedName")
    if encoded is not None:
        expected = prefix + base64.urlsafe_b64encode(native_id.encode()).decode().rstrip("=")
        if _text(encoded, 2048) != expected:
            _fail("google_merchant_native_identity_unproved")
    data_source = _text(raw.get("dataSource"), 128)
    if not re.fullmatch(re.escape("accounts/" + merchant_id + "/dataSources/") + r"[1-9][0-9]{0,29}", data_source):
        _fail("google_merchant_data_source_unproved")
    version = raw.get("versionNumber")
    if version is not None:
        # The native field is signed int64. Retain canonical decimal metadata;
        # it is not an observation timestamp or an independent freshness proof.
        if (not isinstance(version, str) or not re.fullmatch(r"(?:0|-?[1-9][0-9]{0,18})", version)
            or not -(2**63) <= int(version) <= 2**63 - 1):
            _fail()
    attributes = raw.get("productAttributes", {})
    if not isinstance(attributes, dict):
        _fail()
    group = _text(attributes["itemGroupId"], 256) if "itemGroupId" in attributes else None
    return NativeMerchantProduct(resource, encoded, merchant_id, local, language,
        feed, offer, data_source, archived, version,
        _optional_link(attributes, "link"), _optional_link(attributes, "canonicalLink"),
        _optional_link(attributes, "mobileLink"), _optional_link(attributes, "adsRedirect"), group)


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            _fail("google_merchant_invalid_json")
        result[key] = value
    return result


def _invalid_constant(_value):
    _fail("google_merchant_invalid_json")


async def _page(transport, url, params, remaining_bytes):
    raw = bytearray()
    async with asyncio.timeout(MAX_PAGE_SECONDS):
        async with transport.stream("GET", url, params=params,
                headers={"Accept-Encoding": "identity"}, follow_redirects=False) as response:
            if response.status_code != 200:
                _fail("google_merchant_read_rejected")
            if response.headers.get("content-type", "").split(";", 1)[0].lower().strip() != "application/json":
                _fail("google_merchant_invalid_json")
            if response.headers.get("content-encoding", "identity").lower().strip() != "identity":
                _fail("google_merchant_encoding_unproved")
            async for chunk in response.aiter_bytes(chunk_size=8192):
                if len(raw) + len(chunk) > min(MAX_PAGE_BYTES, remaining_bytes):
                    _fail("google_merchant_observation_limit")
                raw.extend(chunk)
    try:
        result = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_object,
                            parse_constant=_invalid_constant)
    except (ValueError, UnicodeError, RecursionError):
        _fail("google_merchant_invalid_json")
    if not isinstance(result, dict) or set(result) - {"products", "nextPageToken"}:
        _fail("google_merchant_invalid_json")
    return result, len(raw)


def _stamp(clock):
    stamp = clock()
    if not isinstance(stamp, datetime) or stamp.tzinfo is None or stamp.utcoffset() is None:
        _fail("google_merchant_observation_time_unproved")
    return stamp.astimezone(timezone.utc)


async def read_merchant_product_observation(transport, *, scope, clock=None):
    """Read only with a separately authorized opaque streaming transport.

    The bound-session owner must check credential/tenant membership before and
    after this call, before exposing its result. No route is attached here.
    """
    if not isinstance(scope, MerchantObservationScope):
        _fail("google_merchant_scope_unproved")
    clock = clock or (lambda: datetime.now(timezone.utc))
    started = _stamp(clock)
    url = PRODUCTS_ORIGIN + "/products/v1/accounts/" + scope.merchant_id + "/products"
    rows, identities, tokens = [], set(), set()
    token, total_bytes = None, 0
    try:
        async with asyncio.timeout(MAX_TOTAL_SECONDS):
            for page_number in range(1, MAX_PAGES + 1):
                params = {"pageSize": PAGE_SIZE}
                if token is not None:
                    params["pageToken"] = token
                page, used = await _page(transport, url, params, MAX_TOTAL_BYTES - total_bytes)
                total_bytes += used
                products = page.get("products", [])
                if not isinstance(products, list) or len(products) > PAGE_SIZE or len(rows) + len(products) > MAX_PRODUCTS:
                    _fail("google_merchant_observation_limit")
                for raw in products:
                    row = _product(raw, scope.merchant_id)
                    if row.identity in identities:
                        _fail("google_merchant_duplicate_native_identity")
                    identities.add(row.identity)
                    rows.append(row)
                if "nextPageToken" not in page:
                    break
                token = _text(page["nextPageToken"], MAX_TOKEN_LENGTH)
                if token in tokens:
                    _fail("google_merchant_pagination_unproved")
                tokens.add(token)
            else:
                _fail("google_merchant_observation_limit")
    except MerchantObservationError:
        raise
    except TimeoutError:
        raise MerchantObservationError("google_merchant_observation_timeout") from None
    except Exception:
        # Transport exceptions can embed URLs, headers or credential contents.
        raise MerchantObservationError("google_merchant_observation_unavailable") from None
    finished = _stamp(clock)
    if finished < started:
        _fail("google_merchant_observation_time_unproved")
    ordered = tuple(sorted(rows, key=lambda row: row.identity))
    payload = {"scope": asdict(scope), "products": [asdict(row) for row in ordered]}
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"),
                                      ensure_ascii=False, allow_nan=False).encode()).hexdigest()
    return MerchantProductObservation(scope, ordered, started.isoformat(), finished.isoformat(), digest, page_number)
