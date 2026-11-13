"""Bounded native source enumeration through an already bound read session."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import hashlib
import json

from .google_merchant_data_sources_proof import (
    DataSourceProofError, MAX_PAGES, MAX_SOURCES, MAX_PAGE_BYTES, MAX_TOTAL_BYTES,
    READ_SECONDS, PAGE_SECONDS, canonical_id, parse_data_source_page,
)


def _json_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise DataSourceProofError()
        result[key] = value
    return result


def _invalid_constant(value):
    raise DataSourceProofError()


async def read_merchant_data_sources(bound, *, merchant_id, clock=None):
    """No unbound transport, arbitrary URL, token export or partial-success data.

    Pages are streamed with identity encoding. Accepted bytes are capped before
    append; JSON materialization has bounded input, not an exact peak RAM claim.
    Enumeration completeness never establishes an atomic provider snapshot.
    """
    merchant_id = canonical_id(merchant_id)
    clock = clock or (lambda: datetime.now(timezone.utc))

    def now():
        value = clock()
        if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
            raise DataSourceProofError()
        return value.astimezone(timezone.utc)

    started = now()
    token, total = None, 0
    seen_tokens, identities, sources, digests = set(), set(), [], []
    try:
        async with asyncio.timeout(READ_SECONDS):
            for _ in range(MAX_PAGES):
                async with asyncio.timeout(PAGE_SECONDS):
                    async with bound.stream_merchant_data_sources(page_token=token) as response:
                        content_type = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
                        encoding = response.headers.get("content-encoding", "identity").strip().lower()
                        if response.status_code != 200 or content_type != "application/json" or encoding != "identity":
                            raise DataSourceProofError("google_merchant_data_sources_response_unproved")
                        raw = bytearray()
                        async for chunk in response.aiter_bytes(chunk_size=8192):
                            if not isinstance(chunk, bytes):
                                raise DataSourceProofError()
                            if len(raw) + len(chunk) > MAX_PAGE_BYTES or total + len(chunk) > MAX_TOTAL_BYTES:
                                raise DataSourceProofError("google_merchant_data_sources_bound_exceeded")
                            raw.extend(chunk)
                            total += len(chunk)
                        payload = json.loads(raw.decode("utf-8"), object_pairs_hook=_json_object,
                            parse_constant=_invalid_constant)
                        page, next_token = parse_data_source_page(payload, merchant_id)
                        for source in page:
                            if source["resource_name"] in identities:
                                raise DataSourceProofError("google_merchant_data_sources_duplicate_identity")
                            identities.add(source["resource_name"])
                        if len(sources) + len(page) > MAX_SOURCES:
                            raise DataSourceProofError("google_merchant_data_sources_bound_exceeded")
                        sources.extend(page)
                        digests.append(hashlib.sha256(raw).hexdigest())
                if next_token is None:
                    finished = now()
                    if finished < started:
                        raise DataSourceProofError()
                    return {
                        "status": "native_data_source_observations_only", "merchant_id": merchant_id,
                        "api_version": "datasources/v1", "sources": sources,
                        "pages_read": len(digests), "sources_observed": len(sources),
                        "pagination_traversal_complete": True, "atomic_snapshot": False,
                        "observed_started_at": started.isoformat(), "observed_finished_at": finished.isoformat(),
                        "page_digests": digests, "publisher_identity_verified": False,
                        "store_product_mapping_verified": False, "variant_mapping_verified": False,
                        "ad_eligibility_verified": False, "execution_authorized": False, "provider_writes": 0,
                        "limitations": ["source_configuration_is_not_publisher_identity",
                            "source_identity_does_not_map_store_products_or_variants",
                            "file_input_can_also_receive_api_products", "bracketing_reads_are_not_atomic"],
                    }
                if next_token in seen_tokens:
                    raise DataSourceProofError("google_merchant_data_sources_pagination_unproved")
                seen_tokens.add(next_token)
                token = next_token
            raise DataSourceProofError("google_merchant_data_sources_bound_exceeded")
    except DataSourceProofError:
        raise
    except TimeoutError:
        raise DataSourceProofError("google_merchant_data_sources_timeout") from None
    except Exception:
        raise DataSourceProofError() from None
